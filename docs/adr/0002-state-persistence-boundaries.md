# ADR 0002: State Persistence Boundaries (PostgreSQL vs Redis vs LangGraph Checkpoints)

## Status
Accepted

## Context
MCP SmartCache handles transient request coalescing, cached tool responses, active workflow state, configuration, and audit records. To maintain system integrity and avoid split-brain consistency bugs, each piece of state must have a single authoritative source of truth with zero overlap.

## Decision
We define strict, non-overlapping ownership boundaries across storage backends:

1. **Redis (Ephemeral & Real-Time Coordination)**:
   - **Scope**: Tool response cache entries (keyed by tenant, server, tool, canonical args, schema hash), singleflight request coalescence locks/waiters, and runtime distributed concurrency limiters.
   - **Durability**: Ephemeral with TTL. A Redis crash degrades gracefully to cache misses (executing requests live without cache); it does not corrupt durable state.

2. **PostgreSQL (Durable Metadata, Configuration & Auditing)**:
   - **Scope**: Server registrations, tool access policies, tenant identities & auth credentials, workflow run summaries, task execution audit trails, and security logs.
   - **Durability**: ACID-compliant durable relational storage. It is the sole authority for system configuration and post-run audit records.

3. **LangGraph Checkpoints (In-Flight Agent State)**:
   - **Scope**: In-memory / thread-scoped checkpoint state of executing agent workflows (node inputs, intermediate tool outputs, plan revisions, step counters).
   - **Durability**: Scoped to the lifecycle of an active workflow execution for pause/resume and replay. Run metadata is synced to PostgreSQL only as immutable run summaries upon step/workflow completion.

## Rejected Alternatives & Rationale
- **Storing Cache Entries in PostgreSQL**:
  - *Why rejected*: Relational write overhead and vacuum churn for short-lived, high-frequency tool cache entries degrades audit and config query performance.
- **Using Redis as the Primary Configuration and Audit Store**:
  - *Why rejected*: Redis lacks robust relational querying, migrations, schema constraints, and audit-grade durability needed for enterprise compliance and policy enforcement.
- **Mirroring LangGraph State Simultaneously into PostgreSQL and Redis during Workflow Execution**:
  - *Why rejected*: Introduces dual-write hazards, synchronization latency, and split-brain states when failure occurs mid-transaction.
