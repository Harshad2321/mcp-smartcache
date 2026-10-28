# ADR 0004: Durable Control Plane, Multi-Tenancy & Live SSE Tracing

## Status
Accepted

## Context
In Phases 0–3, MCP SmartCache established tool routing, explicit allowlist Redis caching, singleflight coalescing, and a LangGraph agent runtime. However, workflow execution state was ephemeral (in-memory per execution), client requests were single-tenant by default, and real-time observability was restricted to log output.

Phase 4 requirements mandate:
1. Durable workflow execution records surviving process restarts.
2. PostgreSQL-backed control plane managing tenants, workflow runs, tasks, traces, and security audit logs.
3. Multi-tenant security boundaries enforcing isolation across workflow queries, cache lookups, singleflight coalescing, and control APIs.
4. Live execution visibility streaming events in real time to a developer dashboard with historical replay upon reconnection.

## Decisions

### 1. Versioned Relational Storage Layer
- **PostgreSQL Persistence**: Managed by `DatabaseManager` using native asynchronous connection pooling (`asyncpg`) with explicit connection limits (`min_size=2`, `max_size=10`), health probes (`SELECT 1`), and automatic JSONB codecs.
- **Migration Engine**: Idempotent version-tracked migrations (`storage/migrations/runner.py`) using `schema_migrations` table.
- **Entities**:
  - `tenants`: Primary key `tenant_id`, active status flag, and JSONB config (concurrency and rate quotas).
  - `workflow_runs`: Execution metadata, correlation ID, state, timestamps, sanitized plan, summary, and grounded final answer.
  - `workflow_tasks`: DAG task records with unique `(workflow_id, task_id)`, status, dependencies, execution duration, and sanitized results.
  - `trace_events`: Chronological execution events indexed by `(workflow_id, id ASC)`.
  - `audit_events`: Durable records for security-relevant operations (workflow submission, cancellation, tenant config changes, cache invalidation).

### 2. Workflow Lifecycle State Machine
- Workflows strictly adhere to validated transitions defined in `ALLOWED_WORKFLOW_TRANSITIONS`:
  - `pending` -> `running`, `cancelled`
  - `running` -> `completed`, `failed`, `cancelled`, `interrupted`
  - Terminal states (`completed`, `failed`, `cancelled`, `interrupted`) are immutable; invalid state transitions raise `ValueError` and are rejected.

### 3. Startup Crash Recovery Policy
- When the runtime or control plane boots, `recover_interrupted_workflows()` scans for workflows stranded in the `running` state.
- Stranded workflows are transitioned to `interrupted` with an explicit reason (`Process restarted during execution`).
- In-progress tasks are transitioned to `failed`.
- **Zero Unsafe Replay**: Upstream tool calls are never automatically re-executed upon process restart, preventing accidental side effects against remote services.

### 4. Tenant Identity and Security Boundaries
- Tenant context is resolved from authenticated credentials (`AuthContext` via Bearer API keys or `CLIENT_TENANT_KEYS` mapping).
- Client credentials cannot access arbitrary tenant IDs; unauthorized impersonation is rejected with `403 Forbidden`.
- **Cache Isolation**: Redis cache keys include the authenticated tenant scope (`mcp-sc:v1:1:<tenant_id>:<sha256>`). Tenant B can never retrieve Tenant A's cached responses.
- **Singleflight Isolation**: Request coalescing keys are tenant-scoped, ensuring concurrent callers across different tenants never share execution leaders or in-flight data.
- **Cache Invalidation Scoping**: Tool and tag invalidation can be targeted to a single tenant without evicting other tenants' caches.

### 5. Live SSE Streaming with Historical Reconnection
- The `/workflows/{workflow_id}/stream` endpoint uses Server-Sent Events (SSE).
- Reconnection via `Last-Event-ID` header (or `since_id` parameter) queries PostgreSQL for historical events where `id > last_event_id` before streaming live events, preventing duplicate delivery.
- Cross-process distribution combines Redis Pub/Sub (`mcp-sc:events:{workflow_id}`) with process-local queues and periodic keepalive comments (`: keep-alive\n\n`).
- Disconnecting clients immediately release subscription resources.

## Consequences & Trade-offs
- **Guaranteed Durability**: Every workflow run, DAG task outcome, and audit log persists across service and container restarts.
- **Strict Authorization Overhead**: Every query and management API validates tenant scope, preventing IDOR or cross-tenant data leaks.
- **Fail-Open Resilience**: If PostgreSQL or Redis is temporarily unreachable during non-critical unit tests, components fail safely without corrupting memory or fabricating tool results.
