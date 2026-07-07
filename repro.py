#!/usr/bin/env python3
"""
Reproduction and validation script for MemMCP.
Tests the following components:
1. Reciprocal Rank Fusion (RRF) with FAISS and SQLite FTS5.
2. SQLite write queue concurrency and WAL crash recovery.
3. KV-Cache prompt packing validator.
"""

import asyncio
import os
import shutil
import sqlite3
import sys
import logging
from typing import Any, Dict, List, Tuple, Union
import numpy as np
import faiss

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("repro")


# =====================================================================
# 1. RECIPROCAL RANK FUSION (RRF) TEST
# =====================================================================

def reciprocal_rank_fusion(
    results_lists: List[List[str]],
    k: int = 60
) -> List[Tuple[str, float]]:
    """
    Implements the Reciprocal Rank Fusion (RRF) formula directly:
    RRF_score(d) = sum_{m in M} 1 / (k + r_m(d))
    
    Args:
        results_lists: A list of lists, where each list contains ordered document IDs.
        k: Constant parameters to regulate the impact of high/low ranks.
        
    Returns:
        A sorted list of (document_id, score) tuples in descending order.
    """
    scores: Dict[str, float] = {}
    for results in results_lists:
        for rank, doc_id in enumerate(results, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


def test_rrf_vector_keyword() -> None:
    """
    Sets up FAISS for vector search and SQLite FTS5 for keyword search,
    performs queries on mock data, and fuses the results using RRF.
    """
    logger.info("Starting Reciprocal Rank Fusion (RRF) validation...")
    
    # Mock documents
    documents: Dict[str, str] = {
        "doc_a": "Artificial intelligence and machine learning algorithms are evolving fast.",
        "doc_b": "SQL databases like SQLite are reliable for storing structured memory records.",
        "doc_c": "Vector search similarity models like FAISS allow fast high-dimensional index querying.",
        "doc_d": "Context window limits in LLM prompt engineering require efficient KV-cache packing.",
        "doc_e": "Direct integration of FTS5 with SQLite facilitates full-text keyword search indexing."
    }
    
    # 1. Vector Search setup using FAISS
    vocab: List[str] = ["intelligence", "sqlite", "vector", "cache", "search", "fts5"]
    dim: int = len(vocab)
    
    def get_doc_vector(text: str) -> np.ndarray:
        text_lower = text.lower()
        vec = np.zeros(dim, dtype=np.float32)
        for idx, word in enumerate(vocab):
            vec[idx] = float(text_lower.count(word))
        norm = np.linalg.norm(vec)
        if norm > 0.0:
            vec = vec / norm
        return vec

    # Construct FAISS index
    index = faiss.IndexFlatIP(dim) # Inner product for cosine similarity of normalized vectors
    doc_keys = list(documents.keys())
    vectors = np.vstack([get_doc_vector(documents[key]) for key in doc_keys])
    index.add(vectors)
    
    # Perform Vector query
    query_text = "efficient vector search and intelligence query"
    query_vector = get_doc_vector(query_text).reshape(1, -1)
    
    # Search top 5
    distances, indices = index.search(query_vector, 5)
    
    vector_results: List[str] = []
    for idx_val in indices[0]:
        if idx_val >= 0 and idx_val < len(doc_keys):
            vector_results.append(doc_keys[idx_val])
            
    logger.info(f"Vector search rankings for '{query_text}': {vector_results}")
    
    # 2. SQLite FTS5 Full-Text Search setup
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE VIRTUAL TABLE mock_docs USING fts5(id, content);")
        for doc_id, text in documents.items():
            conn.execute("INSERT INTO mock_docs (id, content) VALUES (?, ?);", (doc_id, text))
        
        # SQLite FTS5 MATCH query
        fts_query = "search OR intelligence"
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM mock_docs WHERE content MATCH ? ORDER BY rank;", (fts_query,))
        keyword_results: List[str] = [row[0] for row in cursor.fetchall()]
    finally:
        conn.close()
        
    logger.info(f"Keyword search rankings for '{fts_query}': {keyword_results}")
    
    # 3. Fuse rankings using RRF
    k_param = 60
    fused_results = reciprocal_rank_fusion([vector_results, keyword_results], k=k_param)
    logger.info(f"RRF fused rankings: {fused_results}")
    
    # Verification
    # Ensure all unique results are present
    all_unique_docs = set(vector_results).union(set(keyword_results))
    fused_docs = [doc_id for doc_id, _ in fused_results]
    
    assert len(fused_docs) == len(all_unique_docs), "Fused results do not contain all unique document IDs."
    
    # Verify scores are strictly descending
    for i in range(len(fused_results) - 1):
        assert fused_results[i][1] >= fused_results[i+1][1], "Fused results are not sorted in descending order."
        
    logger.info("RRF Validation PASSED successfully.")


# =====================================================================
# 2. SQLITE WRITE QUEUE & WAL RECOVERY
# =====================================================================

WriteRequest = Tuple[str, List[Any], asyncio.Future[Any]]

class SQLiteWriteQueue:
    """
    Manages concurrent writes to an SQLite database by routing them through 
    an asyncio.Queue to a single writer task, preventing database locks.
    """
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.queue: asyncio.Queue[WriteRequest] = asyncio.Queue()
        self._worker_task: Union[asyncio.Task[None], None] = None
        self._running = False

    async def start(self) -> None:
        self._running = True
        self._worker_task = asyncio.create_task(self._worker())
        logger.info(f"SQLiteWriteQueue started for {self.db_path}")

    async def stop(self) -> None:
        self._running = False
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        logger.info("SQLiteWriteQueue stopped.")

    async def _worker(self) -> None:
        # Single database connection used sequentially by the worker
        conn = sqlite3.connect(self.db_path)
        try:
            # Enable WAL mode
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS concurrent_writes ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "writer_id TEXT,"
                "val INTEGER"
                ");"
            )
            conn.commit()
            
            while self._running:
                try:
                    query, params, future = await self.queue.get()
                except asyncio.CancelledError:
                    break
                
                try:
                    cursor = conn.cursor()
                    cursor.execute(query, params)
                    conn.commit()
                    future.set_result(cursor.lastrowid)
                except Exception as e:
                    logger.error(f"Writer worker SQL execution error: {e}")
                    future.set_exception(e)
                finally:
                    self.queue.task_done()
        except Exception as e:
            logger.error(f"Writer worker encountered an error: {e}")
        finally:
            conn.close()

    async def execute_write(self, query: str, params: List[Any]) -> Any:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        await self.queue.put((query, params, future))
        return await future


async def test_concurrent_writers(db_path: str) -> None:
    """
    Spawns multiple async tasks writing concurrently to test the write queue.
    """
    logger.info("Verifying SQLite Write Queue with concurrent tasks...")
    write_queue = SQLiteWriteQueue(db_path)
    await write_queue.start()

    num_writers = 8
    writes_per_writer = 50

    async def mock_writer(writer_id: str) -> None:
        for val in range(writes_per_writer):
            await write_queue.execute_write(
                "INSERT INTO concurrent_writes (writer_id, val) VALUES (?, ?);",
                [writer_id, val]
            )
            await asyncio.sleep(0.001)

    writers = [
        asyncio.create_task(mock_writer(f"writer_{i}"))
        for i in range(num_writers)
    ]
    
    await asyncio.gather(*writers)
    await write_queue.stop()

    # Verify write counts
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM concurrent_writes;")
        total_writes = cursor.fetchone()[0]
        logger.info(f"Total concurrent writes recorded: {total_writes}")
        assert total_writes == num_writers * writes_per_writer, (
            f"Expected {num_writers * writes_per_writer} writes, but got {total_writes}"
        )
    finally:
        conn.close()
    logger.info("SQLite Write Queue concurrency validation PASSED.")


def test_wal_recovery(db_path: str) -> None:
    """
    Simulates a database crash with outstanding transactions in the WAL file
    and verifies that opening the database recovers those transactions.
    """
    logger.info("Simulating SQLite crash and WAL recovery...")
    
    # 1. Set up source database
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("CREATE TABLE IF NOT EXISTS recovery_test (id INTEGER PRIMARY KEY, value TEXT);")
        
        # Write checkpointed data
        conn.execute("INSERT INTO recovery_test (value) VALUES ('checkpointed_1');")
        conn.commit()
        # Force a checkpoint to move data to the main db file
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        
        # Write uncheckpointed data (resides in WAL file only)
        conn.execute("INSERT INTO recovery_test (value) VALUES ('wal_only_2');")
        conn.commit()
        
        # At this point, 'wal_only_2' is committed but not checkpointed into the .db file.
        # We simulate a "crash" by copying the raw files while the database is still open 
        # and lock is active.
        
        db_wal_path = f"{db_path}-wal"
        
        dest_no_wal = "recovered_no_wal.db"
        dest_with_wal = "recovered_with_wal.db"
        dest_with_wal_log = f"{dest_with_wal}-wal"
        
        # Copy main DB only (simulates missing/lost WAL file or file system failure)
        shutil.copy2(db_path, dest_no_wal)
        
        # Copy both DB and WAL (simulates clean recovery from crashed state with WAL intact)
        shutil.copy2(db_path, dest_with_wal)
        shutil.copy2(db_wal_path, dest_with_wal_log)
        
    finally:
        # Cleanly close the connection (will checkpoint and clean up WAL for db_path)
        conn.close()

    # 2. Verify lost WAL file behavior (should not contain 'wal_only_2')
    conn_no_wal = sqlite3.connect(dest_no_wal)
    try:
        cursor = conn_no_wal.cursor()
        cursor.execute("SELECT value FROM recovery_test;")
        results = [row[0] for row in cursor.fetchall()]
        logger.info(f"Query results without WAL file: {results}")
        assert "checkpointed_1" in results, "Missing checkpointed data."
        assert "wal_only_2" not in results, "WAL-only data should not be present without the WAL file."
    finally:
        conn_no_wal.close()

    # 3. Verify WAL recovery behavior (should contain BOTH records)
    conn_with_wal = sqlite3.connect(dest_with_wal)
    try:
        cursor = conn_with_wal.cursor()
        # Query triggers SQLite's automatic recovery process from dest_with_wal-wal
        cursor.execute("SELECT value FROM recovery_test;")
        results = [row[0] for row in cursor.fetchall()]
        logger.info(f"Query results with WAL file (recovered): {results}")
        assert "checkpointed_1" in results, "Missing checkpointed data."
        assert "wal_only_2" in results, "WAL recovery failed: uncheckpointed WAL transaction was lost."
    finally:
        conn_with_wal.close()

    # Cleanup temporary recovery databases
    for temp_file in [dest_no_wal, dest_with_wal, dest_with_wal_log]:
        if os.path.exists(temp_file):
            os.remove(temp_file)
            
    logger.info("SQLite WAL Recovery validation PASSED.")


# =====================================================================
# 3. KV-CACHE PACKING VALIDATOR
# =====================================================================

class MockTokenizer:
    """
    Deterministic tokenizer representing words as stable token IDs.
    Provides identical mapping regardless of prompt placement.
    """
    def __init__(self) -> None:
        self.vocab: Dict[str, int] = {}
        self.counter = 1

    def encode(self, text: str) -> List[int]:
        tokens: List[int] = []
        words = text.strip().split()
        for word in words:
            word_clean = "".join(char for char in word if char.isalnum()).lower()
            if not word_clean:
                continue
            if word_clean not in self.vocab:
                self.vocab[word_clean] = self.counter
                self.counter += 1
            tokens.append(self.vocab[word_clean])
        return tokens


class KVCachePackingValidator:
    """
    Validates LLM prompt packing structure. Confirms that modifications 
    to dynamic suffixes do not invalidate the static cache prefix bounds.
    """
    def __init__(self, tokenizer: MockTokenizer):
        self.tokenizer = tokenizer

    def pack(
        self,
        static_prefix: str,
        semi_static_context: str,
        dynamic_suffix: str
    ) -> Dict[str, Any]:
        """
        Formats prompt parts and returns token offset metadata.
        """
        prefix_tokens = self.tokenizer.encode(static_prefix)
        context_tokens = self.tokenizer.encode(semi_static_context)
        suffix_tokens = self.tokenizer.encode(dynamic_suffix)
        
        full_tokens = prefix_tokens + context_tokens + suffix_tokens
        
        prefix_len = len(prefix_tokens)
        context_len = len(context_tokens)
        
        return {
            "tokens": full_tokens,
            "static_prefix_bounds": (0, prefix_len),
            "semi_static_context_bounds": (prefix_len, prefix_len + context_len),
            "dynamic_suffix_bounds": (prefix_len + context_len, len(full_tokens)),
            "prefix_len": prefix_len,
            "context_len": context_len,
            "suffix_len": len(suffix_tokens)
        }


def test_kv_cache_packing() -> None:
    """
    Tests that changes to dynamic prompt parts do not shift or invalidate
    the static prefix and semi-static context offsets or contents.
    """
    logger.info("Validating KV-Cache Packing alignment...")
    
    tokenizer = MockTokenizer()
    validator = KVCachePackingValidator(tokenizer)
    
    static_prefix = "SYSTEM: You are a secure memory manager agent."
    semi_static_context = "CONTEXT: User session active since 2026-07-05. Current timezone offset is UTC-8."
    
    dynamic_suffix_1 = "QUERY: Write a recovery schema description."
    dynamic_suffix_2 = "QUERY: What is the current timestamp?"
    
    packed_1 = validator.pack(static_prefix, semi_static_context, dynamic_suffix_1)
    packed_2 = validator.pack(static_prefix, semi_static_context, dynamic_suffix_2)
    
    # 1. Verify bounds match exactly
    assert packed_1["static_prefix_bounds"] == packed_2["static_prefix_bounds"], (
        "Static prefix bounds shifted when changing dynamic suffix."
    )
    assert packed_1["semi_static_context_bounds"] == packed_2["semi_static_context_bounds"], (
        "Semi-static context bounds shifted when changing dynamic suffix."
    )
    
    # 2. Verify token lengths match exactly
    assert packed_1["prefix_len"] == packed_2["prefix_len"], "Static prefix token length changed."
    assert packed_1["context_len"] == packed_2["context_len"], "Semi-static context token length changed."
    
    # 3. Verify actual token IDs of prefix and context remain identical
    p1_prefix_tokens = packed_1["tokens"][slice(*packed_1["static_prefix_bounds"])]
    p2_prefix_tokens = packed_2["tokens"][slice(*packed_2["static_prefix_bounds"])]
    assert p1_prefix_tokens == p2_prefix_tokens, "Static prefix token IDs changed."
    
    p1_context_tokens = packed_1["tokens"][slice(*packed_1["semi_static_context_bounds"])]
    p2_context_tokens = packed_2["tokens"][slice(*packed_2["semi_static_context_bounds"])]
    assert p1_context_tokens == p2_context_tokens, "Semi-static context token IDs changed."
    
    # 4. Verify suffix offsets are identical but token IDs differ
    assert packed_1["dynamic_suffix_bounds"][0] == packed_2["dynamic_suffix_bounds"][0], (
        "Dynamic suffix start offset changed."
    )
    p1_suffix_tokens = packed_1["tokens"][slice(*packed_1["dynamic_suffix_bounds"])]
    p2_suffix_tokens = packed_2["tokens"][slice(*packed_2["dynamic_suffix_bounds"])]
    assert p1_suffix_tokens != p2_suffix_tokens, "Dynamic suffixes are identical but should differ."
    
    logger.info("KV-Cache Packing validation PASSED.")


# =====================================================================
# MAIN RUNNER
# =====================================================================

async def main() -> None:
    logger.info("=== Starting MemMCP repro.py Validation Tests ===")
    
    # Setup temporary directory for db tests
    temp_dir = "temp_repro"
    os.makedirs(temp_dir, exist_ok=True)
    db_path = os.path.join(temp_dir, "test_write_queue.db")
    
    try:
        # Part A: RRF Fusion
        test_rrf_vector_keyword()
        
        # Part B: SQLite Write Queue & WAL Recovery
        if os.path.exists(db_path):
            os.remove(db_path)
            
        await test_concurrent_writers(db_path)
        
        # Run WAL Recovery tests
        test_wal_recovery(db_path)
        
        # Part C: KV-Cache Packing
        test_kv_cache_packing()
        
        logger.info("=== All Validation Tests Completed Successfully (Exit Code 0) ===")
        
    except Exception as e:
        logger.exception(f"Validation failed with exception: {e}")
        sys.exit(1)
    finally:
        # Cleanup temp directory
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)


if __name__ == "__main__":
    asyncio.run(main())
