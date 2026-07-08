# Contributing to MemMCP

First off, thank you for considering contributing to MemMCP! 

MemMCP is engineered for distributed multi-agent swarms, which means we hold our architectural standards to extreme, deterministic benchmarks. We value contributions that improve latency, security, and idempotency.

## 🧠 Architectural Philosophy

Before submitting a Pull Request, please ensure your code aligns with our core tenets:
1. **Idempotency is Mandatory:** All memory storage actions must utilize the Idempotency Key architecture.
2. **Concurrency Safety:** Do not introduce blocking SQLite transactions. All state mutations must rely on the existing Write-Ahead Log (WAL) and `lock_manager.py` RWLock backoff mechanics.
3. **Hardware Budgets:** 
   - FAISS Vector Search: `< 50ms` / query.
   - SQLite WAL Commit: `< 15ms` / 100 entries.

## 🚀 Development Workflow

1. **Fork the Repository:** Create your own fork and clone it locally.
2. **Environment Setup:** We mandate the use of `uv` and our `Makefile`.
   ```bash
   make build
   ```
3. **Branch Isolation:** Create a strictly isolated branch for your feature or fix.

## 🧪 Testing Constraints

We do not accept PRs without accompanying tests. 
- All modifications to the MCP tool interfaces must be verified in the `e2e_test.py` pipeline.
- Run tests before committing:
  ```bash
  make test
  ```

Thank you for helping us build the future of deterministic agent memory!
