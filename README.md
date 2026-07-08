<div align="center">
  <h1>🧠 MemMCP</h1>
  <p><strong>Deterministic, BFT-Hardened MCP Memory Server for Agent Swarms</strong></p>

  <p>
    <a href="https://github.com/axton/project_2_mcp_memory/actions"><img src="https://img.shields.io/badge/Build-Passing-brightgreen?style=for-the-badge&logo=github" alt="Build Status"></a>
    <a href="https://python.org"><img src="https://img.shields.io/badge/Python-3.10+-blue?style=for-the-badge&logo=python" alt="Python Version"></a>
    <a href="https://sqlite.org/index.html"><img src="https://img.shields.io/badge/SQLite-WAL-003B57?style=for-the-badge&logo=sqlite&logoColor=white" alt="SQLite WAL"></a>
    <a href="https://github.com/axton/project_2_mcp_memory/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-MIT-purple?style=for-the-badge" alt="License"></a>
  </p>
</div>

<hr/>

MemMCP is a hyper-optimized Memory Server natively implementing the **Model Context Protocol (MCP)**. It bridges the gap between lexical keyword search and semantic vector embeddings, delivering 100% deterministic, deduplicated memory recall for autonomous AI Agent Swarms.

## 📖 Table of Contents
- [Why MemMCP?](#-why-memmcp)
- [Core Features](#-core-features)
- [Quick Start](#-quick-start)
- [Architecture](#-architecture)
- [MCP Tool Reference](#-mcp-tool-reference)
- [Contributing & Security](#-contributing--security)
- [License](#-license)

## 🤔 Why MemMCP?

When dozens of autonomous agents operate in parallel, standard vector databases suffer from race conditions, data duplication, and context hallucination. MemMCP solves this by merging **SQLite Write-Ahead Logging (WAL)** for ACID-compliant state management with **FAISS Hybrid Reciprocal Rank Fusion (RRF)** for unparalleled semantic retrieval.

## ✨ Core Features

| Feature | Description | Architecture |
| :--- | :--- | :--- |
| **Byzantine Fault Tolerance** | Strict isolation of execution states using Bloom-Filter Idempotency tracking. Never stores the same memory twice. | `SQLite WAL` |
| **Data Integrity** | Dual-ledger Distributed Consensus architecture powered by Merkle-Root signatures. | `msvcrt RWLocks` |
| **O(N) Vector Batching Bounds** | FAISS Semantic search with hybrid RRF logic executing strictly within `<50ms` latency bounds. | `FAISS + FTS5` |
| **Zero-Trust Execution** | Hardened against indirect prompt injection with explicit XML RAG bounding. | `MCP stdio` |

## 🚀 Quick Start

MemMCP is designed to be booted instantly by any MCP-compliant LLM or Agent Framework via standard IO streams (`stdio`).

```bash
# 1. Clone the repository
git clone https://github.com/axton/project_2_mcp_memory.git
cd project_2_mcp_memory

# 2. Build the exact dependency graph using uv
make build

# 3. Verify the rigorous mathematical test suite
make test

# 4. Boot the MCP server directly
make run
```

## 🏗 Architecture

```mermaid
graph TD
    A[MCP Client] --> B{Bloom-Filter Idempotency Gate}
    B -->|Duplicate Request| C[Drop (Idempotent Return)]
    B -->|New Request| D[Vectorization]
    D --> E[FAISS Hybrid RRF Search]
    E --> F[SQLite WAL Merkle-Root Ledger]
    F --> G[XML RAG Formatter]
    G --> H[Response]
```

## 🛠 MCP Tool Reference

MemMCP automatically exposes the following functions to any connected agent:

- `store_memory`: Store a single memory. Generates unique keys and updates FAISS indices.
- `store_memories_batch`: Store multiple memories atomically in a single massive transaction, rebuilding the index only once.
- `recall_memories`: Retrieve relevant memories using Reciprocal Rank Fusion (blending Semantic FAISS similarity + SQLite FTS5 keyword search).

## 🤝 Contributing & Security

To contribute, you must abide by our strict mathematical isolation limits. See [CONTRIBUTING.md](CONTRIBUTING.md) for details.
For vulnerabilities, refer to [SECURITY.md](SECURITY.md).

## 📄 License

MIT License. Copyright (c) 2026 Axton Carroll.
