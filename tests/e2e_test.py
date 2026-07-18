"""
End-to-End integration test for the MemMCP server over STDIO.
This acts as a mock MCP Client, launching the server as a subprocess and interacting with it.
"""

import asyncio
import json
import logging
import sys
import os

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("e2e_test")


async def run_e2e_test():
    # Construct paths
    server_script = os.path.join(os.path.dirname(__file__), "..", "src", "server.py")

    server_params = StdioServerParameters(
        command=sys.executable,
        args=[server_script],
        env={
            "MEMMCP_DB_PATH": "test_e2e.db",
            "MEMMCP_LOG_PATH": "test_e2e_wal.log",
            **os.environ,
        },
    )

    logger.info("Starting MCP Client session over stdio...")
    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            logger.info("Session initialized successfully.")

            # Test 1: Store memory
            logger.info("Test 1: store_memory tool...")
            store_res = await session.call_tool(
                "store_memory",
                arguments={
                    "content": "E2E Test memory: The MCP stdio transport is functioning perfectly.",
                    "idempotency_key": "e2e-key-1",
                },
            )

            if store_res.isError:
                logger.error(f"Failed to store memory: {store_res.content}")
                sys.exit(1)

            mem_id = store_res.content[0].text
            logger.info(f"Memory stored with ID: {mem_id}")

            # Test 2: Recall memory
            logger.info("Test 2: recall_memories tool...")
            recall_res = await session.call_tool(
                "recall_memories", arguments={"query": "stdio transport", "limit": 5}
            )

            if recall_res.isError:
                logger.error(f"Failed to recall memories: {recall_res.content}")
                sys.exit(1)

            results = json.loads(recall_res.content[0].text)
            logger.info(f"Retrieved {len(results)} results.")

            found = any(r["id"] == mem_id for r in results)
            if found:
                logger.info("SUCCESS: E2E Pipeline verified.")
            else:
                logger.error(
                    "FAILURE: Memory was stored but not found in recall results."
                )
                sys.exit(1)


if __name__ == "__main__":
    try:
        asyncio.run(run_e2e_test())
    except Exception as e:
        logger.error(f"E2E test failed with exception: {e}")
        sys.exit(1)
    finally:
        # Cleanup
        for file in [
            "test_e2e.db",
            "test_e2e.db-shm",
            "test_e2e.db-wal",
            "test_e2e_wal.log",
        ]:
            if os.path.exists(file):
                try:
                    os.remove(file)
                except Exception:
                    pass
