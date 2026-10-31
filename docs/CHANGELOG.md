# MCP SmartCache — Development History & Changelog

This document preserves the chronological engineering history, architectural milestones, and phase-by-phase implementation records of the **MCP SmartCache** platform.

---

## Phase 2: Production-Grade Caching & Singleflight Deduplication

### 1. Cache Policy Model
MCP SmartCache follows a **strict default-deny policy**:
- Every tool is uncacheable by default.
- A tool is eligible for caching **only** when an explicit `CachePolicy` is registered with:
  - `enabled = True`
  - `read_only = True` (mutations and side-effecting tools are strictly prohibited from caching)
  - `ttl_seconds > 0` (default: 300s)
  - `policy_version` (default: `"1"`)
  - `max_response_bytes` (default: 512 KB)
- The policy enforcement is deterministic application code in `GatewayRouter` — an LLM never decides cache eligibility.
- Ineligible tools, unknown tools, upstream errors (`is_error=True`), schema validation failures, and malformed responses are **never** cached.

### 2. Cache Key Construction & Scope Isolation
Cache keys are canonical, deterministic, and collision-resistant:
```
mcp-sc:v1:<POLICY_SCHEMA_VERSION>:<SCOPE>:<SHA256(canonical_payload)>
```
- **Namespace & Version**: `mcp-sc:v1`
- **Canonicalization**: Dictionary keys are sorted recursively (`sort_keys=True`). List item orders are preserved as semantically significant. Non-serializable inputs raise `TypeError`.
- **Numeric Handling**: Numeric `1` and `1.0` serialise as `"1"` and `"1.0"`, producing distinct keys (explicitly tested).
- **Security & Privacy**: Arguments are digested via SHA-256; secrets or query arguments are never exposed in plaintext in Redis keys.
- **Scope Isolation**: Uses an opaque scope identifier (`scope="none"` in single-tenant mode; hashed tenant IDs in multi-tenant mode). Distinct scopes produce distinct keys and never share entries.

### 3. Redis-Backed Storage & Invalidation
- **Storage**: Serialised `CallToolResult` JSON payload with Redis `EX` TTL.
- **Fail-Open Graceful Degradation**: If Redis is unreachable or returns an error, the gateway logs a warning, increments error metrics, and fails open to upstream live execution without interrupting the caller or fabricating output.
- **Zero KEYS * Scans**: Invalidation uses secondary Redis Set indexes:
  - `mcp-sc:idx:<tool>`: Set of keys belonging to a tool.
  - `mcp-sc:tag:<tag>`: Set of keys belonging to a tag group.
  - Invalidation runs via atomic pipeline deletes on indexed keys only.

### 4. In-Process Singleflight Request Coalescing
When multiple identical eligible requests arrive concurrently:
- Only **one** upstream execution is in-flight for that cache key.
- The first request becomes the **leader**; concurrent callers register as **waiters** using `asyncio.Event`.
- Cache is re-checked inside the singleflight coordination block to avoid upstream execution if a concurrent leader just populated the cache.
- Cancellation of one waiter does **not** cancel the leader or other waiters.
- Waiters receive the same structured result or exception as the leader.
- In-flight entries are always cleaned up in a `finally` block.
- Ineligible requests bypass singleflight deduplication.
- **Boundary Honest Documentation**: Singleflight in Phase 2 operated in-process before being extended to distributed Redis leases in Phase 5.

### 5. Structured Metrics & Telemetry
Prometheus metrics exposed at `GET /metrics` with bounded-cardinality labels (`service`, `tool_category`):
- `mcp_smartcache_cache_hits_total`
- `mcp_smartcache_cache_misses_total`
- `mcp_smartcache_cache_writes_total`
- `mcp_smartcache_cache_write_skipped_total`
- `mcp_smartcache_cache_read_errors_total`
- `mcp_smartcache_cache_write_errors_total`
- `mcp_smartcache_cache_invalidations_total`
- `mcp_smartcache_singleflight_leaders_total`
- `mcp_smartcache_singleflight_waiters_total`
- `mcp_smartcache_upstream_calls_avoided_total`
- `mcp_smartcache_upstream_execution_failures_total`
- `mcp_smartcache_upstream_duration_seconds` (Histogram)
- `mcp_smartcache_cache_lookup_duration_seconds` (Histogram)

---

## Phase 3: LangGraph Agent Runtime & Dependency-Aware Orchestration

Phase 3 introduced a testable LangGraph agent runtime that accepts high-level user tasks, deterministically constructs a structured task plan, discovers tools and validates schemas via the gateway MCP interface, schedules independent subtasks concurrently while respecting prerequisites, and produces evidence-grounded answers.

### 1. Architectural Boundaries
- **Gateway (`gateway/`)**: Upstream MCP registry, tool namespace routing, JSON-Schema argument validation, Redis-backed response caching, and singleflight deduplication. Exposes MCP protocol over SSE (`/mcp/sse`) and Stdio (`python -m gateway.main --stdio`).
- **Runtime (`runtime/`)**: Task interpretation, plan validation, dependency analysis, workflow execution, scheduling, evidence synthesis, and execution tracing.
- **Mandatory Invariant**: The runtime never connects directly to upstream processes. Every tool execution passes through the gateway's MCP interface.

### 2. Deterministic Plan Validation
Before any tools execute, the plan is validated deterministically in `runtime/plan_validator.py`:
- **Unknown Tools**: Rejected if a tool is not present in the gateway's discovered catalog.
- **Schema Validation**: Task arguments are validated against the tool's JSON Schema.
- **Duplicate IDs & Self-Dependencies**: Rejected immediately.
- **Dependency Cycles**: Rejected via DFS cycle detection with the exact cycle path returned.
- **Invalid Concurrency**: Rejected if `max_concurrency < 1`.

### 3. Dependency-Aware Bounded Concurrency Scheduling
The `DependencyAwareScheduler` (`runtime/scheduler.py`):
- Executes independent tasks concurrently up to a configurable ceiling (`max_concurrency`).
- Dependent tasks wait until all prerequisite tasks finish successfully.
- Prerequisite failures block dependent tasks while independent branches continue to completion.
- Tracks peak concurrency and captures millisecond-precision task durations.

---

## Phase 4: Durable Control Plane, Tenancy & Live Tracing

Phase 4 elevated MCP SmartCache into a durable, multi-tenant, observable platform with PostgreSQL 16 persistence and live Server-Sent Events.

### 1. PostgreSQL Relational Persistence
- `tenants`: Multi-tenant organization boundaries and configuration ceilings.
- `workflow_runs`: Persistent workflow execution states and correlation IDs.
- `workflow_tasks`: Step-level task records, execution latencies, and sanitized arguments/results.
- `trace_events`: Monotonically sequenced telemetry events with `Last-Event-ID` replay.
- `audit_events`: Tamper-evident audit trail capturing security operations and policy changes.

### 2. Durable Workflow Lifecycle & Crash Recovery
Workflows follow a deterministic state machine:
`pending` $\to$ `running` $\to$ `completed` | `failed` | `cancelled` | `interrupted`.
Terminal states are strictly immutable. On startup, `recover_interrupted_workflows()` marks stale active workflows as `interrupted` without unsafe automatic replay.

### 3. Dual-Layer Event Distribution
- **Layer 1 (PostgreSQL Catch-Up)**: On connection, `GET /workflows/{id}/stream` streams historical events (`id > Last-Event-ID`).
- **Layer 2 (Redis Pub/Sub)**: Real-time broadcast channel (`mcp-sc:events:{workflow_id}`) delivers live execution events to connected SSE clients.

---

## Phase 5: Dynamic Discovery, Distributed Coordination & Safe Workflow Resumption

Phase 5 extended MCP SmartCache with three distributed infrastructure capabilities:

### 1. Dynamic Upstream Server Discovery & Hot Reload
- Database-backed persistent registry in `upstream_servers`.
- Supported transports: `stdio` (isolated subprocesses) and `sse` (remote HTTP streams).
- Staged atomic activation: valid tool schemas are discovered and verified before atomically swapping the active routing catalog.
- Redis Pub/Sub cluster propagation (`mcp-sc:registry:events`) triggers zero-downtime hot-reloads across gateway replicas.

### 2. Distributed Singleflight & Cache Coordination
- Multi-replica request coalescing using atomic Redis leases (`SET NX PX 15000`) with unique UUID owner tokens.
- Safe release via Lua script (`RELEASE_LEASE_LUA`), preventing stale leaders from releasing valid leases.
- Pub/Sub waiter notifications over `mcp-sc:chan:flight:{scope}:{cache_key}` with bounded 6.0-second timeout.
- Invalidation race protection: stale populated entries from expired leases are discarded if an invalidation occurred during execution.

### 3. Durable Workflow Checkpoints & Safe Resumption
- Snapshots persisted to `workflow_checkpoints` at task transition boundaries.
- Conservative safe-resume policy: completed tasks are reused; pure read-only tasks are re-executed; interrupted mutating tasks are quarantined for manual review.

---

## Phase 6: Dynamic Tenant Policy Governance, Distributed Quotas & Human-in-the-Loop Operations

Phase 6 introduced production-grade governance and operational resiliency:

### 1. Dynamic Tenant Policy Governance
- PostgreSQL versioned policy schema (`tenant_policies`) with optimistic locking.
- Tool rules (allowlists, denylists, verified read-only status).
- Rate limits (`RPM`, burst limits) and resource constraints.
- Hot-reload via Redis Pub/Sub (`mcp-sc:policy:events`).
- Transactional rollback to any prior policy version.

### 2. Distributed Quotas & Backpressure
- Shared rate limiting enforced across gateway replicas via Redis minute-epoch keys.
- Distributed concurrency leases using Redis Sorted Sets and Lua scripts (`ACQUIRE_CONCURRENCY_LUA` / `RELEASE_CONCURRENCY_LUA`).
- Auto-expiring stale permits (default: 60s) preventing permanent quota leaks.

### 3. Human-in-the-Loop Workflow Reconciliation
- Quarantines workflows that crash or time out during non-idempotent external mutations into `workflow_reconciliations`.
- Authoritative decision gate: `approve_retry` (requires idempotency key or explicit risk acknowledgment), `skip_task`, or `abort_workflow`.

---

## Phase 7: Production Hardening & Security Verification

Phase 7 hardened security boundaries and transport filtering:

### 1. SSRF & Outbound Transport Hardening
- Flexible IP parser decoding decimal, hexadecimal, dotted octal, shortened, and IPv4-mapped IPv6 addresses.
- Cloud metadata blocklist: AWS/GCP/Azure (`169.254.169.254`), Alibaba Cloud (`100.100.100.200`), Azure WireServer (`168.63.129.16`), Oracle Cloud (`192.0.0.192`), and GCP (`metadata.google.internal`).
- DNS rebinding pre-resolution check during server registration.
- Stdio transport shell interpreter disallowlist and metacharacter filtering.

### 2. Enterprise Authentication & OIDC Integration
- Cryptographic JWT signature verification against public keys or dynamic JWKS (`gateway/oidc.py`).
- Strict algorithm whitelist (`RS256`, `ES256`, `HS256`). Rejects `none` algorithm.
- Constant-time `secrets.compare_digest` across all API secret evaluations.
- Payload size enforcement via `ContentLengthLimitMiddleware`.

---

## Phase 8: Independent Security Verification & Reliability Engineering

### 1. Connection-Time Kernel Socket Peer Inspection
- Subclassed `AnyIOBackend.connect_tcp()` in `SafeOutboundNetworkBackend`: inspects connected socket peer IP (`stream._raw_socket.getpeername()`) immediately upon connection to eliminate TOCTOU DNS rebinding windows.
- Redirect interception: inspects HTTP 3xx `Location` targets before following redirects.

### 2. JWT Algorithm Confusion Attack Defense (CVE-2015-9235)
- Key-type separation: strictly rejects HMAC (`HS256`) tokens when the configured verification key is an asymmetric RSA or EC public key.

### 3. Non-Disclosing Authorization Boundary
- Standardized cross-tenant unauthorized resource requests to return HTTP 404 Not Found to prevent resource existence enumeration.

### 4. Database-Level Atomic Workflow Status Locking
- Enhanced status updates with `SELECT ... FOR UPDATE` row locks to ensure serializability across concurrent cancel, resume, and worker completion requests.

---

## Phase 9 & 10: Production Release Gate & Operational Acceptance

- Live deployment rehearsal across full Docker Compose stack.
- Multi-worker task mutual exclusion verified under concurrency.
- Operational runbooks published in [`docs/runbooks/production-operations.md`](docs/runbooks/production-operations.md).
- Acceptance report published in [`docs/release/phase10-operational-acceptance.md`](docs/release/phase10-operational-acceptance.md).

