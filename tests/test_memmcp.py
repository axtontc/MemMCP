# tests/test_memmcp.py
"""
Integration and unit tests for MemMCP DatabaseManager and HybridRetriever.
Verifies concurrent writes, transaction serialization, WAL audit logging, and RRF hybrid retrieval.
"""

import asyncio
import os
import shutil
import tempfile
import unittest
import sys

# Ensure the root project directory is on the path so 'src' can be imported
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.database import DatabaseManager
from src.retrieval import HybridRetriever
from src.pruner import ContextPruner
import src.server as server_mod


class TestDatabaseManager(unittest.IsolatedAsyncioTestCase):
    """Test suite for serialized SQLite WAL DatabaseManager."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")
        self.log_path = os.path.join(self.temp_dir, "memory_wal.log")
        self.db = DatabaseManager(self.db_path, self.log_path)

    async def asyncSetUp(self) -> None:
        await self.db.start()

    async def asyncTearDown(self) -> None:
        await self.db.close()
        # Clean up files
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    async def test_wal_mode_enabled(self) -> None:
        """Verifies that WAL mode and synchronous settings are applied."""
        rows = await self.db.execute_read("PRAGMA journal_mode;")
        self.assertEqual(rows[0][0].lower(), "wal")

        rows_sync = await self.db.execute_read("PRAGMA synchronous;")
        self.assertEqual(rows_sync[0][0], 1)  # NORMAL synchronous is 1

    async def test_table_schema_and_triggers(self) -> None:
        """Verifies tables are created and triggers sync to FTS5 table."""
        # Insert a memory directly
        memory_id = "mem-1"
        content = "Testing SQLite FTS5 search capabilities"
        idempotency_key = "key-1"
        metadata_str = '{"author": "test"}'

        await self.db.execute_write(
            "INSERT INTO memories (id, content, idempotency_key, metadata) VALUES (?, ?, ?, ?);",
            [memory_id, content, idempotency_key, metadata_str],
        )

        # Read back from memories table
        rows = await self.db.execute_read(
            "SELECT content FROM memories WHERE id = ?;", [memory_id]
        )
        self.assertEqual(rows[0]["content"], content)

        # Read back from FTS table (should be populated automatically by trigger)
        fts_rows = await self.db.execute_read(
            "SELECT content FROM memories_fts WHERE id = ?;", [memory_id]
        )
        self.assertEqual(fts_rows[0]["content"], content)

        # Update the memory
        new_content = "Updated content for SQLite FTS5"
        await self.db.execute_write(
            "UPDATE memories SET content = ? WHERE id = ?;", [new_content, memory_id]
        )

        # Verify FTS updated
        fts_rows_updated = await self.db.execute_read(
            "SELECT content FROM memories_fts WHERE id = ?;", [memory_id]
        )
        self.assertEqual(fts_rows_updated[0]["content"], new_content)

        # Delete the memory
        await self.db.execute_write("DELETE FROM memories WHERE id = ?;", [memory_id])

        # Verify FTS deleted
        fts_rows_deleted = await self.db.execute_read(
            "SELECT content FROM memories_fts WHERE id = ?;", [memory_id]
        )
        self.assertEqual(len(fts_rows_deleted), 0)

    async def test_concurrent_writes(self) -> None:
        """Spawns concurrent tasks to insert memories and checks for locking or errors."""
        num_tasks = 20

        async def insert_task(idx: int) -> None:
            await self.db.execute_write(
                "INSERT INTO memories (id, content, idempotency_key, metadata) VALUES (?, ?, ?, ?);",
                [
                    f"task-{idx}",
                    f"Content for task {idx}",
                    f"idem-{idx}",
                    '{"test": true}',
                ],
            )

        tasks = [asyncio.create_task(insert_task(i)) for i in range(num_tasks)]
        await asyncio.gather(*tasks)

        # Read and check count
        rows = await self.db.execute_read("SELECT COUNT(*) FROM memories;")
        self.assertEqual(rows[0][0], num_tasks)

    async def test_batch_write_transaction(self) -> None:
        """Verifies atomic execution of batch writes and rollback on failure."""
        queries = [
            (
                "INSERT INTO memories (id, content, idempotency_key, metadata) VALUES (?, ?, ?, ?);",
                ["batch-1", "Content 1", "key-b1", "{}"],
            ),
            (
                "INSERT INTO memories (id, content, idempotency_key, metadata) VALUES (?, ?, ?, ?);",
                ["batch-2", "Content 2", "key-b2", "{}"],
            ),
        ]
        await self.db.execute_batch_write(queries)

        # Check they exist
        rows = await self.db.execute_read(
            "SELECT COUNT(*) FROM memories WHERE id IN ('batch-1', 'batch-2');"
        )
        self.assertEqual(rows[0][0], 2)

        # Test rollback on duplicate key
        failed_queries = [
            (
                "INSERT INTO memories (id, content, idempotency_key, metadata) VALUES (?, ?, ?, ?);",
                ["batch-3", "Content 3", "key-b3", "{}"],
            ),
            (
                "INSERT INTO memories (id, content, idempotency_key, metadata) VALUES (?, ?, ?, ?);",
                ["batch-4", "Content 4", "key-b1", "{}"],
            ),  # Duplicate idempotency_key
        ]

        with self.assertRaises(Exception):
            await self.db.execute_batch_write(failed_queries)

        # Verify batch-3 was rolled back and not inserted
        rows_rollback = await self.db.execute_read(
            "SELECT COUNT(*) FROM memories WHERE id = 'batch-3';"
        )
        self.assertEqual(rows_rollback[0][0], 0)

    async def test_audit_logging(self) -> None:
        """Verifies that all successful writes are logged to the audit log."""
        await self.db.execute_write(
            "INSERT INTO memories (id, content, idempotency_key, metadata) VALUES (?, ?, ?, ?);",
            ["audit-1", "Audit logging content", "audit-key-1", '{"meta": 1}'],
        )

        self.assertTrue(os.path.exists(self.log_path))
        with open(self.log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        self.assertGreater(len(lines), 0)
        # Verify it contains JSON matching our insert
        import json

        last_log = json.loads(lines[-1])
        self.assertIn("queries", last_log)
        self.assertEqual(last_log["queries"][0][1][0], "audit-1")


class TestHybridRetriever(unittest.IsolatedAsyncioTestCase):
    """Test suite for HybridRetriever dense/sparse RRF fusion search."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")
        self.log_path = os.path.join(self.temp_dir, "memory_wal.log")
        self.db = DatabaseManager(self.db_path, self.log_path)
        self.retriever = HybridRetriever(self.db)

    async def asyncSetUp(self) -> None:
        await self.db.start()
        # Seed the database before initializing retriever
        memories = [
            (
                "doc_a",
                "Artificial intelligence and machine learning algorithms are evolving fast.",
                "key_a",
                '{"tag": "AI"}',
            ),
            (
                "doc_b",
                "SQL databases like SQLite are reliable for storing structured memory records.",
                "key_b",
                '{"tag": "SQL"}',
            ),
            (
                "doc_c",
                "Vector search similarity models like FAISS allow fast high-dimensional index querying.",
                "key_c",
                '{"tag": "Vector"}',
            ),
            (
                "doc_d",
                "Context window limits in LLM prompt engineering require efficient KV-cache packing.",
                "key_d",
                '{"tag": "LLM"}',
            ),
            (
                "doc_e",
                "Direct integration of FTS5 with SQLite facilitates full-text keyword search indexing.",
                "key_e",
                '{"tag": "FTS5"}',
            ),
        ]
        for m in memories:
            await self.db.execute_write(
                "INSERT INTO memories (id, content, idempotency_key, metadata) VALUES (?, ?, ?, ?);",
                list(m),
            )
        await self.retriever.initialize()

    async def asyncTearDown(self) -> None:
        await self.db.close()
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    async def test_dense_and_sparse_indexing(self) -> None:
        """Verifies FAISS index and SQLite databases are aligned."""
        self.assertEqual(len(self.retriever.index_to_id), 5)
        self.assertEqual(self.retriever.index.ntotal, 5)

    async def test_add_and_delete_memory(self) -> None:
        """Verifies dynamic indexing additions and deletions align properly."""
        # Insert to DB first, then retriever
        new_id = "doc_f"
        new_content = (
            "A new memory relating to cognitive architectures and neural networks."
        )
        await self.db.execute_write(
            "INSERT INTO memories (id, content, idempotency_key, metadata) VALUES (?, ?, ?, ?);",
            [new_id, new_content, "key_f", "{}"],
        )
        await self.retriever.add_memory(new_id, new_content)

        self.assertEqual(len(self.retriever.index_to_id), 6)
        self.assertEqual(self.retriever.index.ntotal, 6)

        # Search should find it
        results = await self.retriever.search("cognitive architectures")
        self.assertEqual(results[0]["id"], new_id)

        # Delete it from DB, then retriever
        await self.db.execute_write("DELETE FROM memories WHERE id = ?;", [new_id])
        await self.retriever.delete_memory(new_id)

        self.assertEqual(len(self.retriever.index_to_id), 5)
        self.assertEqual(self.retriever.index.ntotal, 5)

    async def test_rrf_rank_fusion(self) -> None:
        """Verifies RRF fusion yields correctly ordered and scored results."""
        # Run search
        query = "efficient vector search and intelligence query"
        results = await self.retriever.search(query, limit=5)

        self.assertGreater(len(results), 0)

        # Verify scores are sorted descending
        for i in range(len(results) - 1):
            self.assertGreaterEqual(results[i]["score"], results[i + 1]["score"])

        # Check structure of results
        for res in results:
            self.assertIn("id", res)
            self.assertIn("content", res)
            self.assertIn("metadata", res)
            self.assertIn("score", res)


class TestContextPruner(unittest.TestCase):
    """Test suite for ContextPruner."""

    def setUp(self) -> None:
        self.pruner = ContextPruner()

    def test_no_pruning_under_threshold(self) -> None:
        text = "This is a simple text that does not exceed the threshold."
        pruned = self.pruner.prune_context(text, max_tokens=10, threshold_tokens=100)
        self.assertEqual(text, pruned)

    def test_pruning_threshold_breach(self) -> None:
        text = (
            "SYSTEM: You are a helpful assistant.\n"
            "This line is about apples and bananas.\n"
            "This line is about SQLite databases and vector search indexes.\n"
            "This line is about irrelevant noise that we do not care about.\n"
            "USER: What is the weather today?"
        )
        pruned = self.pruner.prune_context(text, max_tokens=25, threshold_tokens=10)
        self.assertLess(
            self.pruner.estimate_tokens(pruned), self.pruner.estimate_tokens(text)
        )
        self.assertTrue(pruned.startswith("SYSTEM: You are a helpful assistant."))
        self.assertTrue(pruned.endswith("USER: What is the weather today?"))


class TestMCPServer(unittest.IsolatedAsyncioTestCase):
    """Test suite for MCP server tools."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_server.db")
        self.log_path = os.path.join(self.temp_dir, "test_server_wal.log")

        # Override the server's db_manager and retriever
        self.original_db_manager = server_mod.db_manager
        self.original_retriever = server_mod.retriever

        server_mod.db_manager = DatabaseManager(self.db_path, self.log_path)
        server_mod.retriever = HybridRetriever(server_mod.db_manager)

    async def asyncSetUp(self) -> None:
        await server_mod.db_manager.start()
        await server_mod.retriever.initialize()

    async def asyncTearDown(self) -> None:
        await server_mod.db_manager.close()
        server_mod.db_manager = self.original_db_manager
        server_mod.retriever = self.original_retriever

        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    async def test_list_tools(self) -> None:
        """Verifies that handle_list_tools registers and lists the correct tools."""
        tools = await server_mod.handle_list_tools()
        tool_names = [t.name for t in tools]
        self.assertIn("recall_memories", tool_names)
        self.assertIn("store_memory", tool_names)
        self.assertIn("store_memories_batch", tool_names)

    async def test_store_memory_tool(self) -> None:
        """Verifies that the store_memory tool functions correctly."""
        content = "My favorite language is Python."
        res = await server_mod.handle_call_tool(
            "store_memory",
            {
                "content": content,
                "idempotency_key": "idem-p1",
                "metadata": {"category": "coding"},
            },
        )
        self.assertFalse(res.isError)
        mem_id = res.content[0].text
        self.assertTrue(mem_id.startswith("mem-"))

        # Verify insertion in DB
        db_rows = await server_mod.db_manager.execute_read(
            "SELECT content, idempotency_key FROM memories WHERE id = ?;", [mem_id]
        )
        self.assertEqual(db_rows[0]["content"], content)
        self.assertEqual(db_rows[0]["idempotency_key"], "idem-p1")

        # Test duplicate storing of same key returns the same ID
        res_dup = await server_mod.handle_call_tool(
            "store_memory",
            {
                "content": "Different content but same key.",
                "idempotency_key": "idem-p1",
            },
        )
        self.assertFalse(res_dup.isError)
        self.assertEqual(res_dup.content[0].text, mem_id)

    async def test_store_memories_batch_tool(self) -> None:
        """Verifies that the store_memories_batch tool inserts batch elements atomicly."""
        memories = [
            {"content": "Batch memory one", "idempotency_key": "b-key-1"},
            {
                "content": "Batch memory two",
                "idempotency_key": "b-key-2",
                "metadata": {"importance": "high"},
            },
        ]
        res = await server_mod.handle_call_tool(
            "store_memories_batch", {"memories": memories}
        )
        self.assertFalse(res.isError)
        import json

        ids = json.loads(res.content[0].text)
        self.assertEqual(len(ids), 2)

        # Verify in DB
        rows = await server_mod.db_manager.execute_read(
            "SELECT COUNT(*) FROM memories WHERE idempotency_key IN ('b-key-1', 'b-key-2');"
        )
        self.assertEqual(rows[0][0], 2)

    async def test_recall_memories_tool(self) -> None:
        """Verifies that recall_memories retrieves relevant memories."""
        content = "Deep learning uses neural networks to learn representations."
        await server_mod.handle_call_tool(
            "store_memory", {"content": content, "idempotency_key": "idem-r1"}
        )

        res = await server_mod.handle_call_tool(
            "recall_memories", {"query": "neural networks", "limit": 2}
        )
        self.assertFalse(res.isError)
        import json

        results = json.loads(res.content[0].text)
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0]["content"], content)
        self.assertIn("score", results[0])


if __name__ == "__main__":
    unittest.main()
