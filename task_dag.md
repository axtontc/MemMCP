# Topological Execution DAG (Phase 2)

## 1. Execution Nodes (Atomic Subtasks)

### Task_WAL
- **Description:** Implement Merkle Tree Root hashing and exhaustive telemetry logging for the `.agents/state/` Write-Ahead Log.
- **Dependencies:** `None`
- **Cognitive Load:** `[MODEL_TIER: 2]`
- **Status:** `PRIORITY: CRITICAL`

### Task_Auth
- **Description:** Implement cryptographic `action_id` signing functions for Swarm Machine Language (SML) payloads.
- **Dependencies:** `None`
- **Cognitive Load:** `[MODEL_TIER: 1]`
- **Status:** `READY`

### Task_Retrieval
- **Description:** Refactor `src/retrieval.py` to enforce O(N log N) vector-batch retrieval, eradicating the N+1 query problem for FAISS semantic fusion.
- **Dependencies:** `None`
- **Cognitive Load:** `[MODEL_TIER: 3]`
- **Status:** `READY`

### Task_Lock
- **Description:** Refactor `src/database.py` queue logic to explicitly integrate `lock_manager.py` RWLock mechanics (exponential backoff & jitter) while maintaining WAL telemetry state.
- **Dependencies:** `Task_WAL`
- **Cognitive Load:** `[MODEL_TIER: 3]`
- **Status:** `PRIORITY: CRITICAL`

### Task_Server
- **Description:** Implement rigid Pydantic JSON-Schema validation boundaries and AetherDAG cycle breakers for the MCP interface in `src/server.py`. Must seamlessly integrate SML Auth, Retrieval outputs, and DB lock queues.
- **Dependencies:** `Task_Auth`, `Task_Retrieval`, `Task_Lock`
- **Cognitive Load:** `[MODEL_TIER: 3]`
- **Status:** `PRIORITY: CRITICAL`

### Task_Repro
- **Description:** Update `repro.py` to mathematically and programmatically verify the newly enforced boundaries: FAISS `<50ms` latency bounds, WAL `<15ms` commit limits, and RWLock concurrency limits. This is the isolated verification gate before Phase 5.
- **Dependencies:** `Task_Server`
- **Cognitive Load:** `[MODEL_TIER: 2]`
- **Status:** `PRIORITY: CRITICAL`

## 2. Topological Sort (Execution Order)

1. **Layer 1 (Parallelizable):** `Task_WAL`, `Task_Auth`, `Task_Retrieval`
2. **Layer 2 (Parallelizable):** `Task_Lock`
3. **Layer 3 (Synchronous Integration):** `Task_Server`
4. **Layer 4 (Terminal Verification):** `Task_Repro`

## 3. Critical Path Method (CPM)
The bounding bottleneck for total execution time is defined by the following path:
**`Task_WAL` → `Task_Lock` → `Task_Server` → `Task_Repro`**

These nodes carry the `PRIORITY: CRITICAL` flag and must be provisioned compute resources immediately to prevent swarm stalling.
