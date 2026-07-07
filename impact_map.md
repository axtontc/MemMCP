# Universal Impact & Blast Radius Map (Finalized by Phase 1.8)

## 1. Automated Taint Tracking & Control-Flow Graphs
### Taint Origins
- **Node A (State Manager):** `src/database.py` (DatabaseManager)
- **Node B (Semantic Fusion):** `src/retrieval.py` (HybridRetriever)

### Terminal Nodes (Taint Propagation Halt)
- The taint from `DatabaseManager` stops propagating at the SQLite File I/O boundary. Downstream read operations are stateless and shielded via the RWLock `lock_manager.py`.
- The taint from `HybridRetriever` stops propagating at the MCP Server interface (`src/server.py`). The serialized response payload must strictly conform to JSON-Schema.

### Allowlisted Modification Zones
Execution agents are STRICTLY CONFINED to modifying:
- `src/database.py` (only within the `MutationRequest` queue logic)
- `src/retrieval.py` (only within the FAISS indexing and BM25 sanitization functions)
- `src/server.py` (only to update interface schemas)
- `.agents/state/` (WAL files and index databases)

*Any file not explicitly allowlisted above is mathematically isolated. Modifying unlisted files will trigger an immediate execution abort.*

## 2. API Contract & Schema Firewalls
### Schema Diffs & Database
- **Requirement:** Agents must output a strict SQLite schema diff prior to altering `DatabaseManager` tables.
- **Invariant:** *If the schema diff introduces a breaking change (e.g., dropping a column without a fallback migration) not approved in Phase 1.5, execution is instantly aborted.*

### Subagent Interface
- **Message Format:** All handoffs between agents must adhere to the Swarm Machine Language (SML) YAML schema. 
- **Validation:** Pydantic models must govern the input/output boundaries of the execution subtasks.

## 3. Advanced QA Scope & Hardware Latency Constraints
### Included Test Vectors
- Byzantine fault injection at the MCP Server interface.
- SQLite WAL recovery simulations via `repro.py` (`test_wal_recovery`).
- Reciprocal Rank Fusion (RRF) coefficient variations bounds testing.

### Negative Testing Scope (DO NOT TEST)
- Do **not** test FAISS/SentenceTransformer underlying C++ bindings or network weights.
- Do **not** test SQLite core engine correctness (assume upstream integrity).
- Do **not** run LLM integration tests requiring external API calls (e.g., OpenAI/Anthropic APIs) to preserve compute costs.

### Hardware Latency Hand-off
- **Constraint 1:** Vector search execution latency (FAISS) must not exceed `50ms` per query.
- **Constraint 2:** SQLite WAL commit times must remain under `15ms` for batches of up to 100 entries.
- The `hardware-profiler` must enforce these thresholds. Any token/sec or query/sec drop exceeding 5% variance triggers a rollback.
