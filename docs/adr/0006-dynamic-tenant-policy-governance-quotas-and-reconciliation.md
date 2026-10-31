# ADR 0006: Dynamic Tenant Policy Governance, Distributed Quotas, and Human-in-the-Loop Operations

## Status
Accepted

## Context
In Phases 0–5, MCP SmartCache established:
- A policy-controlled MCP tool gateway with explicit read-only response caching and Redis-backed storage.
- Distributed singleflight request coalescing across gateway replicas with atomic lease tokens and pub/sub notifications.
- Dynamic upstream MCP server discovery with atomic hot reloads and SSRF egress filtering.
- A LangGraph-based workflow runtime with PostgreSQL-backed checkpoints and conservative replay-safety policies.

However, key production governance and operational limitations remained:
1. **Coarse-Grained / Static Policy Configuration**: Tool access rules, caching parameters, and payload limits were applied globally or statically configured per tool, without versioned, tenant-isolated runtime policy definitions.
2. **Multi-Replica Resource Starvation**: While in-flight cacheable reads were coalesced, rate limits and concurrency limits were not coordinated across gateway replicas, allowing multiple instances to multiply effective tenant quotas.
3. **Dead-End Interrupted Mutations**: Side-effecting operations (such as creating issues, deploying releases, or modifying database records) that were interrupted mid-flight entered a safe `interrupted` state, but had no structured mechanism for human operators to inspect the execution outcome, verify external state, and authorize controlled, idempotent resumption.

## Decision

### 1. Versioned Dynamic Tenant Policy Governance (Feature A)
- **Data Model & Schema**: Policies are persisted in PostgreSQL in the `tenant_policies` table with foreign key reference to `tenants(tenant_id)`. Each policy specifies:
  - `tool_rules`: Allowlist and denylist patterns, explicit read-only classifications, and optional default-deny mode. A tool never becomes safe merely because its name contains substring patterns like `get` or `read`.
  - `cache_rules`: Tenant-level caching enable/disable toggle, default TTL, maximum permitted TTL, per-tool TTL overrides, and maximum cacheable response byte limits.
  - `resource_limits`: Max concurrent upstream calls, max concurrent workflows, max tasks per workflow, max workflow duration, max input/output payload sizes, request rate limits (RPM), and burst limits.
  - `is_active` & `policy_version`: Optimistic concurrency versioning ensuring transactional activation and rollback.
- **Validation Pipeline**: Candidate policies undergo deterministic validation via `validate_policy_configuration()` prior to activation. Contradictory rules (e.g. mutation keywords in read-only tools, `default_ttl > max_ttl`, or unbounded payload sizes) are strictly rejected.
- **Atomic Activation & Cluster Synchronization**: Activating a policy sets `is_active = TRUE` in PostgreSQL (source of truth) and broadcasts a notification over Redis Pub/Sub channel `mcp-sc:policy:events`. All gateway replicas receive the event and invalidate their local in-memory policy cache, fetching the new policy on subsequent requests without restarting processes.

### 2. Runtime Policy Enforcement Lifecycle (Feature B)
Gateway requests follow a deterministic 11-step execution pipeline:
1. **Authentication & Tenant Resolution**: Caller is authenticated via bearer token and mapped to an isolated tenant scope (`X-Tenant-ID` verified against authenticated tenant key).
2. **Effective Policy Resolution**: Gateway loads the active policy from in-memory cache or PostgreSQL with fail-safe defaults.
3. **Tool Access Evaluation**: `check_tool_access()` verifies tool name against explicit denylists, default-deny constraints, and allowlists. Denied requests never touch upstream servers.
4. **Input Payload Bound Validation**: Arguments payload size is checked against `max_input_payload_bytes`. Oversized inputs are rejected immediately.
5. **Distributed Rate Limiting**: Request count within the minute window is checked against `rate_limit_rpm + burst_limit` via Redis. Excess requests return HTTP 429 with `Retry-After`.
6. **Cache Eligibility Check**: Verified read-only classification, cache enablement, and tenant bypass rules determine eligibility.
7. **Cache Lookup**: If eligible, cache is queried using tenant-isolated key `mcp-sc:v1:<version>:<tenant>:<digest>`.
8. **Distributed Concurrency Lease Acquisition**: Concurrency permit is acquired atomically in Redis Sorted Set before upstream dispatch.
9. **Upstream Tool Invocation**: Invocation runs under strict timeout bounds. The permit is guaranteed released in a `finally` block upon completion, error, or timeout.
10. **Output Payload & Upstream Result Validation**: Upstream response size is evaluated against `max_output_payload_bytes`. Only non-error, read-only results within size bounds are stored in cache.
11. **Telemetry & Audit Logging**: Bounded-cardinality Prometheus metrics and PostgreSQL audit records are generated.

### 3. Distributed Quotas & Backpressure (Feature C)
- **Distributed Rate Limiting**: Coordinated across all gateway instances using Redis minute-epoch keys (`mcp-sc:rate:{tenant_id}:{minute_epoch}`) with atomic pipeline `INCR` and 120s TTL expiration.
- **Distributed Concurrency Leases**: Tracked in Redis Sorted Sets (`mcp-sc:concurrency:{tenant_id}`). Leases are acquired via atomic Lua script `ACQUIRE_CONCURRENCY_LUA` which prunes expired leases (`now - lease_timeout`), verifies active count against `max_concurrent_upstream`, and records `(score=now, member=permit_id)`.
- **Atomic Permit Release**: Released via atomic Lua script `RELEASE_CONCURRENCY_LUA` removing the caller's unique permit ID, preventing stale processes from releasing other processes' permits.
- **Failure Policy & Graceful Degradation**: If Redis becomes unreachable, the quota manager falls back to process-local tracking with warning logs and error metric increments.

### 4. Human-in-the-Loop Workflow Reconciliation (Feature D)
- **Durable Reconciliation Case Model**: When an interrupted workflow contains unsafe side-effecting operations (`is_tool_read_only == False`) whose outcome is unknown, automatic resumption is strictly blocked. A durable record is created in `workflow_reconciliations` tracking:
  - `reconciliation_id`, `workflow_id`, `task_id`, `tenant_id`, `checkpoint_id`, `tool_name`, `reason`, `status`, `known_outcome`, `decision_note`, `idempotency_key`, `risk_acknowledged`, `reviewer`, `decision_timestamp`.
- **Operator Decision State Machine**: Authorized operators review pending cases through Control API or Dashboard. Supported actions:
  - `approve_retry`: Requires either a trustworthy upstream `idempotency_key` or explicit `risk_acknowledged = True`.
  - `reject_retry`: Finalizes case as rejected; task will not be retried.
  - `acknowledge_success`: Resolves case based on external verification; output is marked reusable.
  - `acknowledge_failure`: Resolves case confirming failure without replay.
  - `external_investigation`: Flags case for offline remediation.
  - `resolve`: Closes finalized case.
- **Safe Resumption Integration**: `assess_workflow_resumability()` evaluates existing reconciliations. Tasks with `status == APPROVED_RETRY` are safely added to `tasks_to_resume`; tasks with `status == RESOLVED` are added to `tasks_to_reuse`. Workflows cannot be resumed unless all interrupted unsafe tasks have an approved reconciliation decision.

### 5. Multi-Tenant Authorization & Security
- Tenant data and reconciliation cases are strictly tenant-isolated. Tenant Alpha cannot inspect or decide on Tenant Beta's cases.
- Administrative endpoints require explicit `ADMIN` role (`GATEWAY_ADMIN_API_KEY`).
- Material policy modifications, activations, rollbacks, and reconciliation decisions generate immutable PostgreSQL audit events.
- Prometheus metric labels use strictly bounded cardinality (`service`, `action`, `outcome`, `error_type`), never embedding raw tenant IDs, user keys, or tool arguments.

## Consequences

### Positive
- **Deterministic Tenant Governance**: Platform administrators and tenants can dynamically manage policies, quotas, and cache rules without server downtime.
- **Production-Grade Multi-Replica Protection**: Shared Redis rate limits and concurrency leases prevent multi-instance quota multiplication and upstream server exhaustion.
- **Safe Human-in-the-Loop Operations**: Interrupted mutations cannot cause silent duplicate executions; operators have clear audit trails, idempotency controls, and risk-acknowledgment gates.
- **Strict Tenant Isolation**: Policies, caches, singleflight locks, and reconciliation cases are partitioned by tenant identity.

### Operational Requirements & Limitations
- **External Redis & PostgreSQL Dependency**: High availability of Redis and PostgreSQL is required for cluster-wide coordination and persistence. Redis fail-safe degrades to local process tracking.
- **Upstream Idempotency**: For upstream tools lacking native idempotency keys, operators must manually verify external state before approving retries with acknowledged risk.

