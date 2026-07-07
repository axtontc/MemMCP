# src/retrieval.py
"""
Dual-index hybrid retrieval using SentenceTransformers + FAISS and SQLite FTS5 with Reciprocal Rank Fusion (RRF).
Handles dense semantic vector matching and sparse lexical matching, fused deterministically.
"""

from typing import Any, Dict, List, Tuple, Optional, Set
import asyncio
import logging
import json
import numpy as np
import faiss
from sentence_transformers import SentenceTransformer
from src.database import DatabaseManager

logger = logging.getLogger("memmcp.retrieval")

class RetrievalError(Exception):
    """Base exception for retrieval errors."""
    pass

class HybridRetriever:
    """
    Combines dense semantic vector search (FAISS) and sparse keyword search (SQLite FTS5)
    using Reciprocal Rank Fusion (RRF) for robust memory retrieval.
    """
    def __init__(
        self,
        db_manager: DatabaseManager,
        model_name: str = "sentence-transformers/all-MiniLM-L6-v2",
        k: int = 60
    ) -> None:
        """
        Initializes the HybridRetriever.
        
        Args:
            db_manager: The active DatabaseManager instance.
            model_name: The SentenceTransformer model to use for dense vector generation.
            k: The RRF constant parameter (default 60).
        """
        self.db_manager = db_manager
        self.model_name = model_name
        self.k = k
        self.model: Optional[SentenceTransformer] = None
        self.index: Optional[faiss.IndexFlatIP] = None
        self.index_to_id: List[str] = []
        self.id_to_index: Dict[str, int] = {}
        self.lock = asyncio.Lock()  # Synchronizes modifications and searches on FAISS / ID mapping

    async def initialize(self) -> None:
        """
        Initializes the retriever, lazy-loading the SentenceTransformer model and
        populating the FAISS index with all memories currently in the database.
        
        Raises:
            RetrievalError: If model initialization or database reading fails.
        """
        try:
            # Lazy load the SentenceTransformer model in a thread pool
            logger.info(f"Loading SentenceTransformer model '{self.model_name}'...")
            self.model = await asyncio.to_thread(SentenceTransformer, self.model_name)
            
            # Build the FAISS index from the database state
            await self.rebuild_index()
            logger.info("HybridRetriever initialized successfully.")
        except Exception as e:
            logger.error(f"Failed to initialize HybridRetriever: {e}")
            raise RetrievalError(f"HybridRetriever initialization failed: {e}") from e

    async def rebuild_index(self) -> None:
        """
        Rebuilds the FAISS index by querying all memories from the database,
        re-computing all embeddings, and updating ID maps.
        Thread-safe and synchronized with self.lock.
        """
        async with self.lock:
            await self._rebuild_index_unlocked()

    async def _rebuild_index_unlocked(self) -> None:
        """
        Internal implementation of index rebuilding. Assumes lock is already acquired.
        """
        try:
            # Fetch all active memories from the database
            rows = await self.db_manager.execute_read("SELECT id, content FROM memories;")
            
            # Initialize FAISS IndexFlatIP for cosine similarity (inner product on L2 normalized vectors)
            self.index = faiss.IndexFlatIP(384)
            self.index_to_id = []
            self.id_to_index = {}
            
            if not rows:
                logger.info("No memories found in the database. Created empty FAISS index.")
                return
                
            ids = [row["id"] for row in rows]
            contents = [row["content"] for row in rows]
            
            # Generate embeddings in thread pool
            if self.model is None:
                raise RetrievalError("SentenceTransformer model is not loaded.")
                
            embeddings_list = await asyncio.to_thread(
                self.model.encode, 
                contents, 
                show_progress_bar=False
            )
            
            embeddings = np.array(embeddings_list).astype("float32")
            faiss.normalize_L2(embeddings)
            
            self.index.add(embeddings)
            self.index_to_id = ids
            self.id_to_index = {id_str: idx for idx, id_str in enumerate(ids)}
            logger.info(f"FAISS index rebuilt with {len(ids)} memories.")
        except Exception as e:
            logger.error(f"Failed to rebuild FAISS index: {e}")
            raise RetrievalError(f"FAISS index rebuild failed: {e}") from e

    async def add_memory(self, memory_id: str, content: str) -> None:
        """
        Adds or updates a single memory in the FAISS index.
        Thread-safe and synchronized with self.lock.
        
        Args:
            memory_id: The unique identifier of the memory.
            content: The text content of the memory.
        """
        async with self.lock:
            if self.model is None or self.index is None:
                raise RetrievalError("HybridRetriever is not fully initialized. Call initialize() first.")
                
            # If the memory already exists, trigger an unlocked rebuild to handle the update/overwrite cleanly
            if memory_id in self.id_to_index:
                await self._rebuild_index_unlocked()
            else:
                try:
                    # Generate embedding in thread pool
                    embedding_list = await asyncio.to_thread(
                        self.model.encode, 
                        [content], 
                        show_progress_bar=False
                    )
                    embedding = np.array(embedding_list).astype("float32")
                    faiss.normalize_L2(embedding)
                    
                    self.index.add(embedding)
                    self.index_to_id.append(memory_id)
                    self.id_to_index[memory_id] = len(self.index_to_id) - 1
                except Exception as e:
                    logger.error(f"Failed to add memory {memory_id} to dense index: {e}")
                    raise RetrievalError(f"Add memory to dense index failed: {e}") from e

    async def delete_memory(self, memory_id: str) -> None:
        """
        Removes a memory from the dense index by rebuilding the index.
        Thread-safe and synchronized with self.lock.
        
        Args:
            memory_id: The unique identifier of the memory to remove.
        """
        async with self.lock:
            if memory_id in self.id_to_index:
                await self._rebuild_index_unlocked()

    def _sanitize_fts_query(self, query: str) -> str:
        """
        Sanitizes raw query strings to prevent FTS5 syntax errors,
        extracting alphanumeric words and combining them with OR.
        """
        words = [w.strip() for w in query.split() if w.strip()]
        cleaned_words = []
        for w in words:
            # Strip all punctuation except hyphens/underscores which are common in compound terms
            clean = "".join(c for c in w if c.isalnum() or c in ("-", "_"))
            if clean:
                cleaned_words.append(clean)
        return " OR ".join(cleaned_words)

    async def search(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """
        Performs hybrid search using FAISS (dense vector) and SQLite FTS5 (sparse keyword),
        combining the results via Reciprocal Rank Fusion (RRF).
        
        Args:
            query: The search query string.
            limit: The maximum number of results to return.
            
        Returns:
            A list of dictionary objects representing the fused memories (containing id, content, metadata, score)
            sorted by descending RRF score.
        """
        if self.model is None or self.index is None:
            raise RetrievalError("HybridRetriever is not fully initialized. Call initialize() first.")
            
        # 1. Sparse search query execution
        fts_results: List[str] = []
        sanitized = self._sanitize_fts_query(query)
        if sanitized:
            try:
                rows = await self.db_manager.execute_read(
                    "SELECT id FROM memories_fts WHERE content MATCH ? ORDER BY rank LIMIT 100;",
                    [sanitized]
                )
                fts_results = [row["id"] for row in rows]
            except Exception as fts_err:
                logger.warning(f"FTS5 match failed. Falling back to LIKE matching: {fts_err}")
                try:
                    # Fallback to standard SQL LIKE matching if FTS5 operational error occurs
                    rows = await self.db_manager.execute_read(
                        "SELECT id FROM memories WHERE content LIKE ? LIMIT 100;",
                        [f"%{query}%"]
                    )
                    fts_results = [row["id"] for row in rows]
                except Exception as fallback_err:
                    logger.error(f"Fallback LIKE query failed: {fallback_err}")
                    
        # 2. Dense search query execution
        dense_results: List[str] = []
        if self.index.ntotal > 0:
            try:
                q_emb = await asyncio.to_thread(
                    self.model.encode, 
                    [query], 
                    show_progress_bar=False
                )
                q_emb_arr = np.array(q_emb).astype("float32")
                faiss.normalize_L2(q_emb_arr)
                
                async with self.lock:
                    search_limit = min(100, self.index.ntotal)
                    distances, indices = self.index.search(q_emb_arr, search_limit)
                    
                    for idx_val in indices[0]:
                        if 0 <= idx_val < len(self.index_to_id):
                            dense_results.append(self.index_to_id[idx_val])
            except Exception as dense_err:
                logger.error(f"Dense vector search failed: {dense_err}")
                # We do not crash here, as we can still rely on sparse search results if they exist.

        # 3. Reciprocal Rank Fusion (RRF) Calculation
        scores: Dict[str, float] = {}
        
        # Dense rank fusion
        for rank, doc_id in enumerate(dense_results, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (self.k + rank)
            
        # Sparse rank fusion
        for rank, doc_id in enumerate(fts_results, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (self.k + rank)
            
        # Sort and limit fused list
        sorted_docs = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        top_docs = sorted_docs[:limit]
        
        if not top_docs:
            return []
            
        # 4. Fetch full memory database records for fused IDs to restore metadata and contents
        placeholders = ",".join("?" for _ in top_docs)
        doc_ids = [doc_id for doc_id, _ in top_docs]
        
        try:
            db_rows = await self.db_manager.execute_read(
                f"SELECT id, content, metadata FROM memories WHERE id IN ({placeholders});",
                doc_ids
            )
            db_memories = {row["id"]: row for row in db_rows}
        except Exception as read_err:
            logger.error(f"Failed to fetch memories metadata for search results: {read_err}")
            raise RetrievalError(f"Memories metadata lookup failed: {read_err}") from read_err
            
        final_results = []
        for doc_id, score in top_docs:
            if doc_id in db_memories:
                row = db_memories[doc_id]
                metadata_dict = {}
                if row["metadata"]:
                    try:
                        metadata_dict = json.loads(row["metadata"])
                    except Exception as json_err:
                        logger.error(f"Failed to parse metadata JSON for memory {doc_id}: {json_err}")
                
                final_results.append({
                    "id": row["id"],
                    "content": row["content"],
                    "metadata": metadata_dict,
                    "score": score
                })
                
        return final_results
