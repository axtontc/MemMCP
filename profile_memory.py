import asyncio
import time
import os
import shutil
import tempfile
import cProfile
import pstats
import io
from src.database import DatabaseManager
from src.retrieval import HybridRetriever

async def run_profile(db_path, log_path):
    db = DatabaseManager(db_path, log_path)
    await db.start()
    
    # Prefill database
    memories = [
        ("doc_a", "Artificial intelligence and machine learning algorithms are evolving fast.", "key_a", '{"tag": "AI"}'),
        ("doc_b", "SQL databases like SQLite are reliable for storing structured memory records.", "key_b", '{"tag": "SQL"}'),
        ("doc_c", "Vector search similarity models like FAISS allow fast high-dimensional index querying.", "key_c", '{"tag": "Vector"}'),
        ("doc_d", "Context window limits in LLM prompt engineering require efficient KV-cache packing.", "key_d", '{"tag": "LLM"}'),
        ("doc_e", "Direct integration of FTS5 with SQLite facilitates full-text keyword search indexing.", "key_e", '{"tag": "FTS5"}'),
    ]
    for m in memories:
        await db.execute_write(
            "INSERT INTO memories (id, content, idempotency_key, metadata) VALUES (?, ?, ?, ?);",
            list(m)
        )
        
    retriever = HybridRetriever(db)
    await retriever.initialize()
    
    # 1. Profile Concurrent Writes
    num_writes = 50
    start_write = time.perf_counter()
    
    async def write_task(idx):
        await db.execute_write(
            "INSERT OR IGNORE INTO memories (id, content, idempotency_key, metadata) VALUES (?, ?, ?, ?);",
            [f"profile-{idx}", f"This is dynamic memory content for profile step {idx}", f"key-profile-{idx}", "{}"]
        )
        
    tasks = [asyncio.create_task(write_task(i)) for i in range(num_writes)]
    await asyncio.gather(*tasks)
    end_write = time.perf_counter()
    avg_write_latency = (end_write - start_write) / num_writes
    print(f"Project 2 Database Concurrent Write: Total Writes={num_writes}, Total Time={end_write - start_write:.4f} s, Avg Latency={avg_write_latency * 1000:.4f} ms")
    
    # Update retriever
    await retriever.rebuild_index()
    
    # 2. Profile Search
    queries = [
        "vector search similarity models",
        "SQLite databases reliability",
        "artificial intelligence algorithms",
        "KV-cache packing",
        "FTS5 full-text integration"
    ]
    
    # Warmup search
    for q in queries:
        await retriever.search(q, limit=3)
        
    num_searches = 50
    start_search = time.perf_counter()
    for i in range(num_searches):
        q = queries[i % len(queries)]
        await retriever.search(q, limit=3)
    end_search = time.perf_counter()
    
    avg_search_latency = (end_search - start_search) / num_searches
    print(f"Project 2 Hybrid Search (RRF): Total Searches={num_searches}, Total Time={end_search - start_search:.4f} s, Avg Latency={avg_search_latency * 1000:.4f} ms")
    
    await db.close()

def main():
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "profile_test.db")
    log_path = os.path.join(temp_dir, "profile_memory_wal.log")
    
    pr = cProfile.Profile()
    pr.enable()
    asyncio.run(run_profile(db_path, log_path))
    pr.disable()
    
    s = io.StringIO()
    sortby = pstats.SortKey.CUMULATIVE
    ps = pstats.Stats(pr, stream=s).sort_stats(sortby)
    ps.print_stats(15)
    print("\n--- Profile Statistics ---")
    print(s.getvalue())
    
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir)

if __name__ == "__main__":
    main()
