# src/server.py
"""
MCP Server implementation for MemMCP.
Provides tools to store, search, and recall memories.
"""

import asyncio
import hashlib
import json
import logging
import os
import uuid
from typing import Any, Dict, List, Optional

import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from src.database import DatabaseManager, DatabaseError
from src.retrieval import HybridRetriever, RetrievalError

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("memmcp.server")

# Configuration via environment variables with defaults
DB_PATH = os.environ.get("MEMMCP_DB_PATH", "memmcp.db")
LOG_PATH = os.environ.get("MEMMCP_LOG_PATH", "memory_wal.log")

db_manager = DatabaseManager(DB_PATH, LOG_PATH)
retriever = HybridRetriever(db_manager)
server = Server(name="MemMCP")


async def store_memory_impl(
    content: str,
    idempotency_key: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None
) -> str:
    """
    Business logic for storing a single memory.
    """
    if not idempotency_key:
        idempotency_key = hashlib.sha256(content.encode("utf-8")).hexdigest()

    # Query first to achieve idempotency and prevent throwing duplicate error logs
    existing = await db_manager.execute_read(
        "SELECT id FROM memories WHERE idempotency_key = ?;",
        [idempotency_key]
    )
    if existing:
        return existing[0]["id"]

    memory_id = f"mem-{uuid.uuid4()}"
    metadata_str = json.dumps(metadata or {})

    # Perform insertion
    await db_manager.execute_write(
        "INSERT INTO memories (id, content, idempotency_key, metadata) VALUES (?, ?, ?, ?);",
        [memory_id, content, idempotency_key, metadata_str]
    )
    # Update dense index
    await retriever.add_memory(memory_id, content)
    return memory_id


async def store_memories_batch_impl(memories: List[Dict[str, Any]]) -> List[str]:
    """
    Business logic for storing a batch of memories atomically.
    """
    # 1. Resolve/generate idempotency keys for all memories
    keys: List[str] = []
    for m in memories:
        content = m["content"]
        k = m.get("idempotency_key")
        if not k:
            k = hashlib.sha256(content.encode("utf-8")).hexdigest()
        keys.append(k)

    # 2. Query database for any keys that already exist to reuse their IDs
    existing_map: Dict[str, str] = {}
    if keys:
        placeholders = ",".join("?" for _ in keys)
        existing_rows = await db_manager.execute_read(
            f"SELECT id, idempotency_key FROM memories WHERE idempotency_key IN ({placeholders});",
            keys
        )
        existing_map = {row["idempotency_key"]: row["id"] for row in existing_rows}

    queries: List[tuple[str, List[Any]]] = []
    result_ids: List[str] = []
    seen_keys: Dict[str, str] = {}

    # 3. Formulate the batch write queries
    for m in memories:
        content = m["content"]
        k = m.get("idempotency_key")
        if not k:
            k = hashlib.sha256(content.encode("utf-8")).hexdigest()

        # Deduplicate within the batch itself
        if k in seen_keys:
            result_ids.append(seen_keys[k])
            continue

        # If already exists in DB, reuse the ID
        if k in existing_map:
            result_ids.append(existing_map[k])
            seen_keys[k] = existing_map[k]
            continue

        memory_id = f"mem-{uuid.uuid4()}"
        seen_keys[k] = memory_id
        result_ids.append(memory_id)

        metadata_str = json.dumps(m.get("metadata") or {})
        queries.append((
            "INSERT INTO memories (id, content, idempotency_key, metadata) VALUES (?, ?, ?, ?);",
            [memory_id, content, k, metadata_str]
        ))

    # 4. Perform execution and rebuild index
    if queries:
        await db_manager.execute_batch_write(queries)
        await retriever.rebuild_index()

    return result_ids


@server.list_tools()
async def handle_list_tools() -> List[types.Tool]:
    """
    Lists the tools supported by the MemMCP server.
    """
    return [
        types.Tool(
            name="recall_memories",
            description="Recall relevant memories using hybrid search (semantic similarity + keyword search).",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query to match against memories."
                    },
                    "limit": {
                        "type": "integer",
                        "description": "The maximum number of memories to return.",
                        "default": 5
                    }
                },
                "required": ["query"]
            }
        ),
        types.Tool(
            name="store_memory",
            description="Store a single memory, automatically generating unique keys and updating indices.",
            inputSchema={
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "The text content of the memory to store."
                    },
                    "idempotency_key": {
                        "type": "string",
                        "description": "Optional unique key to prevent duplicate storage."
                    },
                    "metadata": {
                        "type": "object",
                        "description": "Optional structured metadata dictionary."
                    }
                },
                "required": ["content"]
            }
        ),
        types.Tool(
            name="store_memories_batch",
            description="Store multiple memories atomically in a single transaction, then rebuild the index once.",
            inputSchema={
                "type": "object",
                "properties": {
                    "memories": {
                        "type": "array",
                        "description": "A list of memories, each containing 'content', and optional 'idempotency_key' and 'metadata'.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "content": {
                                    "type": "string",
                                    "description": "The text content of the memory."
                                },
                                "idempotency_key": {
                                    "type": "string",
                                    "description": "Optional unique key."
                                },
                                "metadata": {
                                    "type": "object",
                                    "description": "Optional metadata dictionary."
                                }
                            },
                            "required": ["content"]
                        }
                    }
                },
                "required": ["memories"]
            }
        )
    ]


@server.call_tool()
async def handle_call_tool(
    name: str,
    arguments: Dict[str, Any]
) -> types.CallToolResult:
    """
    Handles tool call invocations.
    """
    try:
        if name == "recall_memories":
            query = arguments["query"]
            limit = arguments.get("limit", 5)
            results = await retriever.search(query=query, limit=limit)
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=json.dumps(results))]
            )

        elif name == "store_memory":
            content = arguments["content"]
            idempotency_key = arguments.get("idempotency_key")
            metadata = arguments.get("metadata")

            memory_id = await store_memory_impl(
                content=content,
                idempotency_key=idempotency_key,
                metadata=metadata
            )
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=memory_id)]
            )

        elif name == "store_memories_batch":
            memories_list = arguments["memories"]
            memory_ids = await store_memories_batch_impl(memories_list)
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=json.dumps(memory_ids))]
            )

        else:
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=f"Unknown tool: {name}")],
                isError=True
            )
    except Exception as e:
        logger.error(f"Error executing tool {name}: {e}")
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=str(e))],
            isError=True
        )


async def main() -> None:
    """
    Runs the MCP server using stdio transport.
    """
    await db_manager.start()
    await retriever.initialize()

    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options()
            )
    except Exception as e:
        logger.critical(f"Server execution failed: {e}")
    finally:
        await db_manager.close()


if __name__ == "__main__":
    asyncio.run(main())
