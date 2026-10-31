# MCP SmartCache

MCP SmartCache is a specialized gateway and LangGraph agent runtime designed for robust Model Context Protocol (MCP) tool orchestration. It provides centralized upstream server routing, deterministic policy enforcement, exact-match response caching, concurrent request deduplication (singleflight), and execution observability across multi-step agent workflows.

## Getting Started

### Prerequisites
- Docker Engine (v24+) and Docker Compose (v2+)
- Python 3.12+ (for local host development)

### Running with Docker Compose

1. Clone or open the repository.
2. Validate the compose configuration:
   ```bash
   docker compose config
   ```
3. Start the infrastructure and stub services:
   ```bash
   docker compose up --build -d
   ```
4. Verify service health:
   ```bash
   docker compose ps
   ```
5. Stop services:
   ```bash
   docker compose down
   ```

### Exposed Services & Endpoints

| Service | Port | Description | Health Endpoint |
| :--- | :--- | :--- | :--- |
| `gateway` | `8000` | MCP gateway server & routing layer | `GET /health` |
| `control_api` | `8080` | FastAPI management & configuration API | `GET /health` |
| `runtime` | `8002` | LangGraph agent execution runtime | `GET /health` |
| `dashboard` | `5173` | React + TypeScript + Vite management UI | `GET /` |
| `redis` | `6379` | Cache store & execution coordination | `redis-cli ping` |
| `postgres` | `5432` | Relational metadata, config, and audit store | `pg_isready` |

## Repository Layout

- `gateway/`: MCP server handling upstream registry, request routing, caching, request deduplication, and policy enforcement.
- `runtime/`: LangGraph agent runtime orchestrating multi-step workflows exclusively via the gateway's MCP interface.
- `control_api/`: FastAPI service for server registration, policy configuration, and run query endpoints.
- `storage/`: PostgreSQL data models and schema migrations alongside the Redis access layer.
- `observability/`: OpenTelemetry trace propagators and Prometheus metrics collectors.
- `dashboard/`: React + TypeScript + Vite dashboard for workflow trace inspection.
- `mocks/`: Deterministic mock upstream MCP servers with configurable latency and error hooks.
- `benchmarks/`: Performance and caching benchmark harnesses.
- `tests/`: Automated unit and integration test suites mirroring the application layout.
- `docs/adr/`: Architecture Decision Records documenting system design rationale.

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
- **Boundary Honest Documentation**: Singleflight is currently **in-process**. It coalesces concurrent requests within a single gateway instance, but does not coordinate across separate gateway replicas.

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
- `mcp_smartcache_upstream_calls_avoided_total` (reasons: `cache_hit`, `singleflight`)
- `mcp_smartcache_upstream_execution_failures_total`
- `mcp_smartcache_upstream_duration_seconds` (Histogram)
- `mcp_smartcache_cache_lookup_duration_seconds` (Histogram)

### 6. Authentication, Authorization & Multi-Tenant Security Boundary

The Gateway enforces a strict production security boundary:
- **Role-Based Access Control**:
  - **ADMIN Role** (`GATEWAY_ADMIN_API_KEY`): Required for cache policy registration (`POST /cache/policy`), cache invalidation (`DELETE /cache/invalidate/...`), and Prometheus metrics scraping (`GET /metrics`). Admin keys can also invalidate cache globally or for a specific tenant.
  - **CLIENT Role** (`GATEWAY_CLIENT_API_KEY` or `CLIENT_TENANT_KEYS`): Allowed for tool discovery (`GET /tools`), status inspection (`GET /status`), and tool invocation (`POST /call`).
  - **Public**: Health liveness probe (`GET /health`) only.
- **Strict Tenant Authorization & Key Binding**:
  - Client API keys are cryptographically bound to authorized tenant IDs.
  - A client cannot choose arbitrary tenant IDs by manipulating the `X-Tenant-ID` header. Header tampering or requests for unauthorized tenants are immediately rejected with `403 Forbidden`.
  - When `X-Tenant-ID` is omitted, requests automatically and safely default to the client's bound tenant scope.
  - The cache key digest binds the tenant scope (`mcp-sc:v1:1:<tenant_id>:<sha256>`).
  - Identical calls from distinct tenants never share cache entries or bleed data across boundaries.
- **Tenant-Scoped Invalidation**:
  - Secondary Redis Set indexes include the tenant scope (`mcp-sc:idx:<tenant_id>:<tool>`).
  - Invalidation for Tenant A removes only Tenant A's cached entries and leaves Tenant B's cached entries intact.
- **Production Secret & Upstream Isolation**:
  - `ENVIRONMENT=production`: Mock servers are **strictly excluded**. Upstream MCP servers must be explicitly configured via `UPSTREAM_CONFIG_PATH`. The application refuses to start unauthenticated or with development secret placeholders (`dev-*`, `test-*`).
  - `ENVIRONMENT=development` (`ENABLE_DEV_MOCKS=true`): Safe development mock servers are loaded for automated test harnesses and local development.

### 7. Verification & Operational Testing

#### Running the Full Test Suite
```bash
docker compose exec gateway pytest -v
```

#### End-to-End Gateway API Verification via cURL

1. **Register Cache Policy for a Tool (Requires ADMIN Key):**
```bash
curl -X POST http://localhost:8000/cache/policy \
  -H "Authorization: Bearer dev-admin-secret-key-32chars-long" \
  -H "Content-Type: application/json" \
  -d '{
    "tool_name": "repo_metadata__get_repo_info",
    "enabled": true,
    "read_only": true,
    "ttl_seconds": 300,
    "policy_version": "1",
    "max_response_bytes": 524288,
    "invalidation_tags": ["repos"]
  }'
```

2. **Execute First Tool Call as Tenant A (Cache Miss & Upstream Execution):**
```bash
curl -X POST http://localhost:8000/call \
  -H "Authorization: Bearer dev-client-secret-key-32chars-long" \
  -H "X-Tenant-ID: default" \
  -H "Content-Type: application/json" \
  -d '{
    "tool": "repo_metadata__get_repo_info",
    "arguments": {"owner": "acme", "repo": "core-engine"}
  }'
```

3. **Execute Second Tool Call as Tenant A (Instant Cache Hit):**
```bash
curl -X POST http://localhost:8000/call \
  -H "Authorization: Bearer dev-client-secret-key-32chars-long" \
  -H "X-Tenant-ID: default" \
  -H "Content-Type: application/json" \
  -d '{
    "tool": "repo_metadata__get_repo_info",
    "arguments": {"owner": "acme", "repo": "core-engine"}
  }'
```

4. **Verify Unauthorized Tenant Request is Rejected (403 Forbidden):**
```bash
# Attempting to access an unauthorized tenant using a single-tenant or unassigned key returns 403:
curl -i -X POST http://localhost:8000/call \
  -H "Authorization: Bearer dev-client-secret-key-32chars-long" \
  -H "X-Tenant-ID: unauthorized-tenant" \
  -H "Content-Type: application/json" \
  -d '{
    "tool": "repo_metadata__get_repo_info",
    "arguments": {"owner": "acme", "repo": "core-engine"}
  }'
```

5. **Verify Telemetry & Cache Metrics (Requires ADMIN Key):**
```bash
curl -s -H "Authorization: Bearer dev-admin-secret-key-32chars-long" http://localhost:8000/metrics | grep mcp_smartcache_
```

6. **Invalidate Tool Cache (Admin Only, Supports Optional `tenant_id` Query Parameter):**
```bash
curl -X DELETE "http://localhost:8000/cache/invalidate/tool/repo_metadata__get_repo_info?tenant_id=default" \
  -H "Authorization: Bearer dev-admin-secret-key-32chars-long"
```

### 8. Production Readiness & Known Deployment Blockers

- **In-process singleflight**: Request coalescing operates in-process per gateway replica. Multi-instance horizontal scaling requires distributed lock coordination (e.g., Redis distributed lock) before high-traffic multi-pod deployment.
- **Dynamic Upstream Discovery**: In production, upstream MCP servers currently require static configuration via `UPSTREAM_CONFIG_PATH` or startup registration; dynamic runtime discovery via PostgreSQL configuration will be completed in Phase 4.
- **External Identity Provider**: Current authentication uses robust, constant-time API keys and validated tenant scopes. Full OAuth2/OIDC JWT token validation against an external enterprise IdP will be completed in the Phase 4 control plane.

## Phase 3: LangGraph Agent Runtime & Dependency-Aware Orchestration

Phase 3 introduces a real, testable LangGraph agent runtime that accepts high-level user tasks, deterministically constructs a structured task plan, discovers tools and validates schemas via the gateway MCP interface, schedules independent subtasks concurrently while respecting prerequisites, and produces evidence-grounded answers.

### 1. Architectural Boundaries

- **Gateway (`gateway/`)**: Remains responsible for upstream MCP registry, tool namespace routing, JSON-Schema argument validation, Redis-backed response caching, and in-process singleflight deduplication. Exposes the official MCP protocol interface over SSE (`/mcp/sse`) and Stdio (`python -m gateway.main --stdio`).
- **Runtime (`runtime/`)**: Responsible for task interpretation, plan validation, dependency analysis, workflow execution, scheduling, evidence synthesis, and execution tracing.
- **Mandatory Invariant**: The runtime **never** imports mock servers, never connects directly to upstream processes, and never implements its own cache. Every tool execution passes through the gateway's MCP interface.

### 2. LangGraph Workflow Structure

The runtime workflow is modeled as a compiled LangGraph `StateGraph` with explicit, typed state (`AgentState`):

```
       [START]
          │
          ▼
    parse_request
          │
          ▼
   discover_tools ──(queries gateway via MCP protocol)
          │
          ▼
     plan_tasks
          │
          ▼
   validate_plan ──[valid plan?]
         │                   │
       (Yes)                (No: bypass execution)
         │                   │
         ▼                   │
  execute_workflow           │
  (bounded parallel          │
   DAG scheduler)            │
         │                   │
         ▼                   │
evaluate_completeness        │
         │                   │
         ▼                   │
 synthesize_answer           │
         │                   │
         ▼                   │
   finalize_trace ◄──────────┘
          │
          ▼
        [END]
```

### 3. Deterministic Plan Validation

Before any tools execute, the plan is validated deterministically in `runtime/plan_validator.py`:
- **Unknown Tools**: Rejected if a tool is not present in the gateway's discovered catalog.
- **Schema Validation**: Task arguments are validated against the tool's JSON Schema using `jsonschema.validate()`.
- **Duplicate IDs**: Rejected if duplicate `task_id` values exist.
- **Self-Dependencies**: Rejected if a task depends on itself.
- **Missing Dependencies**: Rejected if a prerequisite ID does not exist in the plan.
- **Dependency Cycles**: Rejected via DFS cycle detection with the exact cycle path returned.
- **Invalid Concurrency**: Rejected if `max_concurrency < 1`.
- **Zero-Execution Invariant**: If validation fails, the workflow immediately routes to `finalize_trace` and exits without executing any planned tools.

### 4. Dependency-Aware Bounded Concurrency Scheduling

The `DependencyAwareScheduler` (`runtime/scheduler.py`):
- Executes independent tasks concurrently up to a configurable ceiling (`max_concurrency`).
- Dependent tasks wait until all prerequisite tasks finish successfully.
- Prerequisite failures block dependent tasks while independent branches continue to completion.
- Tracks peak concurrency and captures millisecond-precision task durations.

### 5. Official MCP SDK Integration

Runtime tool discovery and execution strictly use the official MCP Python SDK (`mcp` v2):
- **SSE Transport**: Connects over HTTP SSE to `http://gateway:8000/mcp/sse`.
- **Stdio Transport**: Connects via subprocess to `python -m gateway.main --stdio`.
- **Session Management**: Correctly initializes `ClientSession`, queries `list_tools()`, and invokes tools via `call_tool()`.

### 6. Grounded Evidence Citations

Answers are synthesized in `runtime/synthesis.py` without hallucination:
- Every claim derived from retrieved data cites an explicit evidence identifier, e.g.:
  `Repository metadata indicates 128 stars and 32 forks [evidence: evidence:task_repo_info].`
- Incomplete or failed subtasks are honestly flagged with reasons rather than inventing missing information.

### 7. Reproducing the Demonstration

1. **Submit a Repository Intelligence Task via HTTP:**
```bash
curl -X POST http://localhost:8002/tasks \
  -H "Content-Type: application/json" \
  -H "X-Request-ID: demo-req-001" \
  -d '{
    "prompt": "Investigate the Google SmartCache repository. Retrieve its repository metadata, recent commit history, and open issues. Summarize the repository status and identify the most important issues, using evidence from the retrieved data.",
    "owner": "google",
    "repo": "smartcache",
    "max_concurrency": 5
  }'
```

2. **Run All Automated Tests (143 Tests):**
```bash
docker exec mcp-smartcache-gateway pytest
```

3. **Run the Sequential vs. Parallel Benchmark:**
```bash
docker exec mcp-smartcache-gateway python -m benchmarks.benchmark_parallel_vs_sequential
```

**Measured Benchmark Results (5 iterations each, 200ms artificial delay):**
- **Sequential Baseline**: 609.09 ms mean
- **Parallel Runtime (Cold Cache)**: 206.96 ms mean (Peak Concurrency: 3) — **2.94x speedup**
- **Parallel Runtime (Warm Cache)**: 2.89 ms mean (0 upstream calls, 100% cache hits) — **210.57x speedup**

---

## Phase 4: Durable Control Plane, Tenancy & Live Tracing

Phase 4 elevates MCP SmartCache from an ephemeral orchestration engine into a durable, multi-tenant, observable platform. Agent executions survive process restarts, tenant boundaries are enforced across all layers, execution traces stream in real time, and operators monitor workflows via a React dashboard.

### 1. Architecture & Service Roles

```
┌────────────────────────────────────────────────────────────────────────┐
│                        React Dashboard (Vite:5173)                     │
│    [Tenant Switcher] [Workflow List] [Interactive DAG] [Live SSE]      │
└───────────────────▲───────────────────────────────▲────────────────────┘
                    │ REST                          │ SSE Stream
┌───────────────────┴───────────────────────────────┴────────────────────┐
│                      Control API (FastAPI:8080)                         │
│   • Tenant Auth & RBAC          • Durable Workflow & Task Query        │
│   • Config & Policy CRUD        • SSE Stream & Last-Event-ID Catch-up  │
│   • Audit Trail Query           • Server-Side Quota Enforcement        │
└─────────┬───────────────────────────────┬──────────────────────────────┘
          │ Internal REST                 │
┌─────────▼──────────────────┐            │
│  Agent Runtime (Port 8002) │            │
│  • LangGraph State Machine │            │
│  • DAG Scheduler & Hooks   │            │
│  • Startup Crash Recovery  │            │
└─────────┬──────────────────┘            │
          │ MCP over SSE                  │
┌─────────▼──────────────────┐            │
│  MCP Gateway (Port 8000)   │            │
│  • Tool Registry & Routing │            │
│  • Tenant Cache Isolation  │            │
│  • Tenant Singleflight     │            │
└─────────┬──────────────────┘            │
          │                               │
┌─────────▼───────────────────────────────▼──────────────────────────────┐
│                    Persistence & Infrastructure Layer                  │
│  • PostgreSQL (Port 5432): Durable metadata, tasks, traces, audit logs │
│  • Redis (Port 6379): Response cache, secondary indexes, pub/sub bus   │
└────────────────────────────────────────────────────────────────────────┘
```

- **MCP Gateway (`gateway/`)**: Remains the central enforcement point for upstream MCP tools, JSON-Schema argument validation, Redis-backed exact-match caching, and in-process singleflight deduplication. Enforces cryptographic client key tenant binding and tenant-scoped cache keys (`mcp-sc:v1:1:<tenant_id>:<hash>`).
- **Agent Runtime (`runtime/`)**: Orchestrates multi-step agent plans using LangGraph and the bounded-concurrency DAG scheduler. Integrates with the persistence layer via hooks to record task transitions, trace events, and final synthesized answers. Automatically runs crash recovery on startup.
- **Control API (`control_api/`)**: Provides REST management endpoints for tenants, workflows, tasks, traces, policies, and audit logs. Houses the Server-Sent Events (SSE) streaming engine with PostgreSQL historical replay and Redis pub/sub real-time event broadcasting.
- **Storage Layer (`storage/`)**: Houses the PostgreSQL connection pool (`asyncpg`), versioned database migration runner, multi-tenant repository layer, secret sanitization, and dual-layer event hub.
- **React Dashboard (`dashboard/`)**: Single-page application built with React 18, TypeScript, and Vite. Connects to the Control API to visualize workflow runs, inspect task DAGs, stream live trace timelines, and view audit events.

---

### 2. PostgreSQL Relational Persistence & Schema Migrations

The control plane uses PostgreSQL 16 for durable state. Database interactions use native asynchronous connection pooling via `asyncpg` with automatic JSON/JSONB codecs and ACID transaction contexts.

#### Database Tables

1. **`tenants`**:
   - `id VARCHAR(64) PRIMARY KEY`: Unique tenant slug (e.g., `default`, `tenant_a`, `tenant_b`).
   - `name VARCHAR(255) NOT NULL`: Human-readable tenant name.
   - `is_active BOOLEAN NOT NULL DEFAULT TRUE`: Active/disabled status.
   - `config JSONB NOT NULL DEFAULT '{}'`: Tenant runtime limits (e.g., `max_concurrency_limit`, `max_tasks_per_workflow`, `rate_limit_per_minute`).
   - `created_at`, `updated_at`: UTC timestamps.
2. **`workflow_runs`**:
   - `workflow_id VARCHAR(128) PRIMARY KEY`: Stable workflow identifier.
   - `tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(id)`: Owning tenant.
   - `correlation_id VARCHAR(128) NOT NULL`: End-to-end request tracing correlation ID (`X-Request-ID`).
   - `workflow_type VARCHAR(64) NOT NULL`: Type identifier (e.g., `repository_intelligence`).
   - `request_prompt TEXT NOT NULL`: Original user prompt.
   - `status VARCHAR(32) NOT NULL`: Workflow status (`pending`, `running`, `completed`, `failed`, `cancelled`, `interrupted`).
   - `plan_summary JSONB`: Validated task DAG metadata.
   - `final_answer TEXT`: Synthesized evidence-grounded result.
   - `error_message TEXT`: Sanitized error description on failure.
   - `created_at`, `started_at`, `completed_at`, `execution_duration_ms`.
3. **`workflow_tasks`**:
   - `task_id VARCHAR(128) PRIMARY KEY`: Structured task identifier (`{workflow_id}:{subtask_id}`).
   - `workflow_id VARCHAR(128) NOT NULL REFERENCES workflow_runs(workflow_id) ON DELETE CASCADE`.
   - `tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(id)`.
   - `tool_name VARCHAR(128) NOT NULL`: Gateway tool invoked.
   - `status VARCHAR(32) NOT NULL`: Task status (`pending`, `running`, `completed`, `failed`, `blocked`).
   - `dependencies JSONB NOT NULL DEFAULT '[]'`: Prerequisite task IDs.
   - `arguments JSONB NOT NULL DEFAULT '{}'`: Sanitized tool invocation arguments.
   - `result JSONB`: Sanitized tool execution result.
   - `error_message TEXT`: Sanitized error string if failed.
   - `execution_duration_ms`: Execution time in milliseconds.
4. **`trace_events`**:
   - `id BIGSERIAL PRIMARY KEY`: Monotonically increasing sequential event ID (used for `Last-Event-ID` ordering).
   - `workflow_id VARCHAR(128) NOT NULL REFERENCES workflow_runs(workflow_id) ON DELETE CASCADE`.
   - `tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(id)`.
   - `correlation_id VARCHAR(128) NOT NULL`.
   - `task_id VARCHAR(128)`: Associated task ID (nullable for workflow-level events).
   - `event_type VARCHAR(64) NOT NULL`: Event category (`workflow_started`, `task_started`, `task_completed`, `cache_hit`, `evidence_synthesized`, etc.).
   - `event_data JSONB NOT NULL DEFAULT '{}'`: Structured, redacted event payload.
   - `timestamp DOUBLE PRECISION NOT NULL`: Unix epoch timestamp.
5. **`audit_events`**:
   - `id VARCHAR(64) PRIMARY KEY`: Unique audit record UUID (`aud_...`).
   - `tenant_id VARCHAR(64) REFERENCES tenants(id)`: Associated tenant (nullable for system actions).
   - `actor_id VARCHAR(128) NOT NULL`: Service or user identity.
   - `action VARCHAR(128) NOT NULL`: Operation executed (`workflow_submitted`, `tenant_config_updated`, `cache_invalidated`, etc.).
   - `target_type VARCHAR(64) NOT NULL`: Target entity type (`workflow`, `tenant`, `cache`).
   - `target_id VARCHAR(128) NOT NULL`: Target entity ID.
   - `outcome VARCHAR(32) NOT NULL`: `success`, `failure`, `denied`.
   - `details JSONB NOT NULL DEFAULT '{}'`: Redacted contextual metadata.
   - `timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW()`.

#### Database Migration Engine

Migrations are managed deterministically via `storage/migrations/runner.py`:
- Tracks applied migrations in a dedicated `schema_migrations` table.
- Runs transactionally: each migration runs in its own transaction and rolls back if an error occurs.
- Idempotent: repeated runs safely detect already-applied migrations without modifying data.
- Built-in seed data initializes standard test tenants (`default`, `tenant_a`, `tenant_b`).

Run migrations manually:
```bash
docker exec mcp-smartcache-gateway python -m storage.migrations.runner
```

---

### 3. Durable Workflow Lifecycle & Crash Recovery

#### State Machine & Transition Rules

Workflows follow a deterministic state machine enforced by `storage/models/entities.py`:

```
   ┌───────────┐
   │  pending  │
   └─────┬─────┘
         │
         ▼
   ┌───────────┐         ┌───────────┐
   │  running  ├────────►│ cancelled │
   └──┬───┬───┬┘         └───────────┘
      │   │   │
      │   │   └──────────►┌─────────────┐
      │   │               │ interrupted │
      │   │               └─────────────┘
      │   ▼
      │ ┌───────────┐
      │ │  failed   │
      │ └───────────┘
      ▼
┌───────────┐
│ completed │
└───────────┘
```

- **Allowed Transitions**:
  - `pending` -> `running`, `cancelled`
  - `running` -> `completed`, `failed`, `cancelled`, `interrupted`
  - Terminal states (`completed`, `failed`, `cancelled`, `interrupted`) are immutable. Attempting to transition from a terminal state raises an `InvalidStateTransitionError` (`409 Conflict`).

#### Crash Recovery Without Unsafe Replay

When the runtime service restarts:
1. `recover_interrupted_workflows()` runs during application startup (`lifespan` in `runtime/main.py`).
2. Scans for workflows left in the non-terminal `running` state.
3. Transitions them to `interrupted` with an explicit reason: `"Execution interrupted by process crash or service restart"`.
4. Creates an associated `workflow_interrupted` trace event and records a security audit log.
5. **Safety Guarantee**: The recovery job **never automatically replays** tool calls or workflows upon restart. Arbitrary upstream tool re-execution could cause unintended duplicate side-effects (e.g., external API mutations). Workflows must be explicitly resubmitted by authorized clients.

---

### 4. Multi-Tenant Isolation & Security Boundary

Tenant isolation is enforced as a strict cryptographic and authorization boundary across all layers:

1. **Client Key Binding**:
   - Client API keys are mapped to authorized tenant IDs (`CLIENT_TENANT_KEYS`).
   - If a client supplies an `X-Tenant-ID` header for a tenant other than their bound tenant, the gateway and control API reject the request with `403 Forbidden` (`SECURITY ALERT: Tenant ID header does not match authenticated key`).
2. **Workflow & Task Data Isolation**:
   - All workflow, task, trace, and audit queries in `storage/repository.py` require an explicit `tenant_id` scope.
   - Cross-tenant requests return `404 Not Found` (or `403 Forbidden`), completely preventing Tenant A from probing or guessing Tenant B's workflow IDs.
3. **Cache & Singleflight Isolation**:
   - Cache keys incorporate the tenant ID in the hash digest: `mcp-sc:v1:1:<tenant_id>:<sha256>`.
   - Singleflight in-process deduplication incorporates the tenant ID into the flight key (`flight:<tenant_id>:<cache_key>`), ensuring concurrent calls from separate tenants are never coalesced.
4. **Tenant Limits & Quota Enforcement**:
   - Tenants have configurable runtime limits: `max_concurrency_limit` and `max_tasks_per_workflow`.
   - If an incoming workflow requests concurrency greater than the tenant's ceiling, it is clamped server-side to the allowed maximum.
5. **Disabled Tenant Protection**:
   - Inactive tenants (`is_active = False`) are blocked from submitting workflows (`403 Forbidden: Tenant is disabled`).

---

### 5. Live Trace Streaming (SSE) & Reconnection

The Control API exposes a real-time event streaming endpoint:
```
GET /workflows/{workflow_id}/stream
Headers: Authorization: Bearer <client_api_key>, Last-Event-ID: <optional_id>
```

#### Dual-Layer Event Distribution
- **Layer 1 (PostgreSQL Catch-Up)**: On connection, the endpoint queries PostgreSQL for all events matching `id > Last-Event-ID` and streams them immediately. This ensures clients that disconnect and reconnect never miss events.
- **Layer 2 (Redis Pub/Sub & Local Broadcaster)**: While connected, the client listens to live events published by the runtime to Redis channel `mcp-sc:events:{workflow_id}` and local async queues.
- **SSE Format**:
  ```http
  id: 42
  event: trace
  data: {"id": 42, "workflow_id": "wf_123", "event_type": "task_completed", "data": {...}}
  ```
- **Clean Disconnect**: Client disconnections automatically remove local queue listeners and close Redis subscriptions, preventing memory leaks and unbounded queue growth.

---

### 6. React Dashboard: Live Execution Visualizer

The dashboard (`dashboard/`) is a full-featured, dark-mode developer control interface built with React 18, TypeScript, and Vite.

#### Features
- **Tenant Context Switcher**: Switch active tenant (`default`, `tenant_a`, `tenant_b`) with dynamic quota and concurrency ceiling display.
- **Workflow Overview**: Real-time table of workflow runs displaying workflow ID, status badge, prompt, creation time, duration, and task counts.
- **Workflow Submission Modal**: Interactive form allowing operators to submit new prompts, target repositories, and concurrency limits.
- **Interactive Task DAG Visualizer**: Displays scheduled subtasks with visual dependency links, execution states (`completed`, `running`, `blocked`, `failed`), tool names, durations, and arguments/results inspector.
- **Live Event Stream Timeline**: Real-time SSE event timeline showing workflow lifecycle events, tool invocations, cache hits, and evidence synthesis with live connection badge and `Last-Event-ID` catch-up.
- **Audit Log Viewer**: Complete, filterable audit trail of tenant actions, workflow submissions, and policy modifications.

#### Running the Dashboard
The dashboard runs automatically in Docker on port `5173`. Access it at:
```
http://localhost:5173
```

To build the dashboard for production:
```bash
docker exec mcp-smartcache-dashboard npm run build
```

---

### 7. End-to-End Verification Recipes

#### 1. Submit a Workflow as Tenant A via Control API
```bash
curl -X POST http://localhost:8080/workflows \
  -H "Authorization: Bearer dev-client-secret-key-32chars-long" \
  -H "X-Tenant-ID: tenant_a" \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "Analyze repository health and commit activity for owner acme repo core-engine",
    "workflow_type": "repository_intelligence",
    "max_concurrency": 3
  }'
```

#### 2. Stream Live Execution Events via SSE
```bash
curl -N http://localhost:8080/workflows/<WORKFLOW_ID>/stream \
  -H "Authorization: Bearer dev-client-secret-key-32chars-long" \
  -H "X-Tenant-ID: tenant_a"
```

#### 3. Reconnect with Last-Event-ID Catch-Up
```bash
curl -N http://localhost:8080/workflows/<WORKFLOW_ID>/stream \
  -H "Authorization: Bearer dev-client-secret-key-32chars-long" \
  -H "X-Tenant-ID: tenant_a" \
  -H "Last-Event-ID: 5"
```

#### 4. Verify Cross-Tenant Access Denial (404 Not Found)
```bash
# Attempt to read Tenant A's workflow using Tenant B's credentials:
curl -i http://localhost:8080/workflows/<TENANT_A_WORKFLOW_ID> \
  -H "Authorization: Bearer dev-client-secret-key-tenant-b-32ch" \
  -H "X-Tenant-ID: tenant_b"
# Output: HTTP/1.1 404 Not Found
```

#### 5. Inspect Persisted Audit Events
```bash
curl -s http://localhost:8080/audit \
  -H "Authorization: Bearer dev-client-secret-key-32chars-long" \
  -H "X-Tenant-ID: tenant_a" | jq .
```

#### 6. Run the Complete Automated Test Suite (191 Tests)
```bash
docker exec mcp-smartcache-gateway pytest
```

---

## Phase 5: Dynamic Discovery, Distributed Coordination & Safe Workflow Resumption

Phase 5 extends MCP SmartCache with three production-grade distributed infrastructure capabilities:

### 1. Dynamic Upstream Server Discovery & Hot Reload (Feature A)
- **Database-Backed Persistent Registry**: Registered in the `upstream_servers` PostgreSQL table with schema validation, connection metadata, and version concurrency control (`config_version`).
- **Supported Transports**:
  - `stdio`: Spawns local subprocess MCP servers with arguments. Validated against dangerous shell metacharacters (`;&|$\` etc.).
  - `sse`: Connects to Server-Sent Events MCP endpoints. Validated with outbound SSRF protections: blocks private subnets, cloud metadata endpoints (`169.254.169.254`), and loopback addresses in production.
- **Staged Atomic Activation**: Upon registration or refresh, a candidate connection discovers and validates tool schemas. If discovery succeeds, the active routing catalog is atomically swapped. If connection or schema validation fails, the last known-good catalog is preserved.
- **Cluster Propagation**: Control plane updates publish notifications to Redis Pub/Sub (`mcp-sc:registry:events`), enabling all running gateway replicas to hot-reload without restarting processes.
- **Management Endpoints**:
  - `GET /servers`: List registered servers (admin only, credentials redacted).
  - `POST /servers`: Register new upstream server.
  - `GET /servers/{id}`: Inspect server metadata and runtime state.
  - `PUT /servers/{id}` / `PATCH /servers/{id}`: Update configuration, tags, or enable/disable.
  - `POST /servers/{id}/refresh`: Trigger discovery refresh without restart.
  - `GET /servers/{id}/tools`: Inspect discovered tool schemas.
  - `GET /servers/{id}/status`: Connection health check and sanitized errors.

### 2. Distributed Singleflight & Cache Coordination (Feature B)
- **Distributed Request Coalescing**: Multiple gateway replicas coordinate identical cacheable read requests using atomic Redis leases (`SET NX PX 15000`) with unique UUID owner tokens.
- **Ownership-Safe Atomic Release**: Leaders release leases via an atomic Lua script (`RELEASE_LEASE_LUA`), ensuring slow or expired leaders can never release a lease owned by a newer leader.
- **Pub/Sub Waiter Notifications**: Coalescing waiters await leader completion via Redis channels (`mcp-sc:chan:flight:{scope}:{cache_key}`) with a bounded 6.0-second timeout.
- **Invalidation Race Protection**: Tool invalidation stores epoch timestamps in `mcp-sc:inv_ts:{tool}:{scope}`. If an in-flight computation completes after an invalidation was triggered, the stale populated entry is immediately evicted.
- **Tenant Scope Isolation**: Grouping keys and pub/sub channels are strictly scoped by tenant (`flight:{scope}:{cache_key}`), preventing cross-tenant request coalescing.
- **Fail-Safe Local Fallback**: When Redis is unreachable or slow, callers gracefully fall back to process-local singleflight or independent execution.
- **Coordination Metrics**:
  - `GET /cache/coordination`: Exposes cluster coordination state, distributed leaders, waiters, lock contention failures, timeouts, and fallbacks.

### 3. Durable Workflow Checkpoints & Safe Resumption (Feature C)
- **Durable Checkpointing**: Snapshots are persisted to `workflow_checkpoints` at task transition boundaries, storing completed task outputs, pending task DAGs, and graph versions.
- **Conservative Safe-Resume Policy**:
  - **Completed Tasks**: Outputs are preserved and reused directly from the checkpoint; completed tasks are never re-executed.
  - **Read-Only Tasks**: Idempotent read operations (`repo_metadata__get_repo_info`, `issues__list_issues`, etc.) are scheduled for safe execution.
  - **Interrupted Side-Effecting Tasks**: Operations that mutate state or lack verifiable idempotency are strictly blocked from automatic replay and flagged for manual operator reconciliation.
- **Graph Version Compatibility**: Validates checkpoint schema compatibility against supported versions (`1.0.0`, `v1.0.0`). Incompatible checkpoints are safely rejected.
- **Authorized Resumption Endpoints**:
  - `GET /workflows/{id}/resumability`: Evaluates resumability, lists reusable tasks, and flags unsafe tasks.
  - `GET /workflows/{id}/checkpoints`: Retrieves durable checkpoint history.
  - `POST /workflows/{id}/resume`: Authorizes and resumes eligible interrupted workflows.

### 4. Interactive Dashboard Integration
- **Upstream Servers Tab**: Server table with enable/disable toggles, refresh buttons, discovered tool counts, and a modal to dynamically register new servers.
- **Distributed Cache & Coordination Tab**: Live metrics cards tracking distributed leaders, waiters, lock failures, timeouts, fallbacks, and lease expirations with 5-second auto-refresh.
- **Workflow Detail & Resumption View**: Checkpoint snapshot history, safety policy evaluation badge, and active `⚡ Resume Workflow from Checkpoint` button.

---

## Phase 6: Dynamic Tenant Policy Governance, Distributed Quotas & Human-in-the-Loop Operations

Phase 6 completes the production-grade governance and operational resiliency layer of MCP SmartCache:

### 1. Dynamic Tenant Policy Governance (Feature A)
- **Versioned Policy Schema**: Persisted in PostgreSQL (`tenant_policies`) with optimistic versioning (`policy_version`), tenant isolation foreign keys, and active state tracking (`is_active`).
- **Comprehensive Governance Model**:
  - **Tool Rules**: Configurable allowed and denied patterns (`fnmatch` syntax), verified read-only tool classifications, and optional default-deny mode. Substring heuristics (e.g. `get_` or `read_`) are strictly rejected as proof of read-only safety.
  - **Cache Rules**: Per-tenant caching enable/disable toggle, default TTL (seconds), maximum permitted TTL cap, per-tool custom TTL overrides, and response byte caps (`max_response_bytes`).
  - **Resource Limits**: Concurrency caps (`max_concurrent_upstream`, `max_concurrent_workflows`), task limits (`max_tasks_per_workflow`), execution timeout caps, payload bounds (`max_input_payload_bytes`, `max_output_payload_bytes`), request rate limits (`rate_limit_rpm`), and burst capacity (`burst_limit`).
- **Strict Validation Pipeline**: Candidate policies are pre-validated via `POST /policies/validate`. Inconsistent configurations (e.g., mutation keywords in read-only patterns, `default_ttl > max_ttl`, or unbounded payload sizes) return actionable validation errors and prevent activation.
- **Hot Reload via Redis Pub/Sub**: Activating a policy writes to PostgreSQL (source of truth) and publishes an invalidation event to `mcp-sc:policy:events`. Gateway replicas refresh their in-memory caches without process restarts or traffic interruption.
- **Control API Endpoints**:
  - `GET /policies`: List tenant policies (tenant-isolated).
  - `GET /policies/effective/{tenant_id}`: Inspect active effective policy.
  - `GET /policies/{id}`: Retrieve specific policy version.
  - `POST /policies/validate`: Dry-run validation of proposed policy rules.
  - `POST /policies`: Register or update policy version.
  - `POST /policies/{id}/activate`: Transactionally activate a validated policy version.
  - `POST /policies/{id}/rollback`: Roll back to a previous valid policy version.

### 2. Runtime Policy Enforcement (Feature B)
Every incoming request to the MCP Gateway traverses an 11-step enforcement pipeline:
1. **Authentication & Tenant Binding**: Extracts bearer token and validates against tenant key registry (`CLIENT_TENANT_KEYS`).
2. **Effective Policy Resolution**: Reads active policy from fast local cache or PostgreSQL with safe fallback defaults.
3. **Tool Access Evaluation**: Checks explicit denylists and default-deny allowlists. Denied tools are rejected immediately with structured `policy_denied` errors and never dispatched upstream.
4. **Input Payload Bound Check**: Validates serialized arguments against `max_input_payload_bytes`. Excess sizes return `payload_too_large`.
5. **Distributed Rate Limiting**: Evaluates rate against minute-window limits via Redis.
6. **Cache Eligibility Determination**: Validates tenant cache toggle, bypass flags, and explicit read-only status.
7. **Cache Lookup**: Queries tenant-scoped cache (`mcp-sc:v1:<version>:<tenant>:<digest>`).
8. **Concurrency Lease Acquisition**: Acquires atomic Redis permit before invoking upstream.
9. **Bounded Upstream Invocation**: Invokes tool within timeout limits; permit is guaranteed released in a `finally` block.
10. **Output Payload Bound & Upstream Result Check**: Ensures response does not exceed `max_output_payload_bytes`. Only clean read-only results are stored in cache.
11. **Audit & Telemetry**: Emits bounded-cardinality Prometheus metrics (`policy_eval_total`, `policy_denials_total`, `quota_rejections_total`, `payload_rejections_total`).

### 3. Distributed Quotas & Backpressure (Feature C)
- **Shared Rate Limiting**: Multi-replica rate limits are enforced via Redis minute-epoch keys (`mcp-sc:rate:{tenant_id}:{minute_epoch}`) using atomic pipelines. Over-quota callers receive HTTP 429 with standard `Retry-After` headers.
- **Atomic Concurrency Leases**: Concurrency is coordinated across gateway replicas using Redis Sorted Sets (`mcp-sc:concurrency:{tenant_id}`) and atomic Lua scripts:
  - `ACQUIRE_CONCURRENCY_LUA`: Purges expired leases (`now - lease_timeout`), tests capacity, and atomically adds lease.
  - `RELEASE_CONCURRENCY_LUA`: Removes lease using the unique permit ID, preventing stale processes from releasing valid permits.
- **Auto-Expiring Stale Leases**: Crashed or stalled processes automatically lose their leases after `lease_timeout` (default: 60s), preventing permanent quota leaks.
- **Degraded Local Fallback**: If Redis becomes unavailable, the system safely falls back to local in-process limits with structured error logging.

### 4. Human-in-the-Loop Workflow Reconciliation (Feature D)
- **Interrupted Mutation Detection**: When a workflow is interrupted mid-execution, `assess_workflow_resumability()` inspects uncompleted tasks:
  - Pure read-only tasks are safely re-scheduled.
  - Side-effecting tasks whose outcome is uncertain are **strictly blocked from automatic replay**.
  - A durable reconciliation record is created in `workflow_reconciliations`.
- **Durable Case Record**: Captures `reconciliation_id`, `workflow_id`, `task_id`, `tenant_id`, `checkpoint_id`, `tool_name`, `reason`, `known_outcome`, `status`, `decision_note`, `idempotency_key`, `risk_acknowledged`, `reviewer`, and `decision_timestamp`.
- **Operator Decision State Machine**: Authorized operators review cases via Control API or Dashboard:
  - `approve_retry`: Permits re-execution only when either a verified upstream `idempotency_key` is supplied or the operator explicitly checks `risk_acknowledged = True`.
  - `reject_retry`: Rejects retry; workflow task will not be re-executed.
  - `acknowledge_success`: Marks task as verified successful externally, allowing dependent tasks to resume.
  - `acknowledge_failure`: Finalizes task as confirmed failure without re-running.
  - `external_investigation`: Marks case for external review.
  - `resolve`: Closes finalized case.
- **Transactional Consistency & Auditing**: Conflicting concurrent operator decisions are rejected via database transactions and status checks. Every decision generates an immutable PostgreSQL audit event.

### 5. Interactive Dashboard Integration (Feature E)
- **Tenant Policies Tab**:
  - Live display of effective active policy, tool rules, caching limits, and resource quotas.
  - Historical policy version table with rollback actions.
  - Policy editor modal with live JSON validation against platform invariants and one-click atomic activation.
- **Human-in-the-Loop Reconciliations Tab**:
  - Operational metrics cards: Total Cases, Pending Review, Approved Retries, and Resolved.
  - Case table detailing workflow ID, task ID, tool, known outcome, and status.
  - Interactive "Review & Decide" modal with high-risk warning banners, idempotency key input, and mandatory risk-acknowledgment checkbox for unsafe retries.

### 6. Security Boundaries & Operational Invariants (Feature F)
- **Tenant Scope Isolation**: All cache keys, singleflight locks, pub/sub channels, policies, and reconciliations are partitioned by authenticated tenant ID.
- **SSRF & Dangerous Subprocess Protection**: Strict IP parsing blocks private IP blocks and cloud metadata endpoints (`169.254.169.254`). Stdio transports reject shell injection metacharacters.
- **Bounded Telemetry Cardinality**: Prometheus metrics avoid raw tenant IDs, user keys, or tool arguments in labels, preventing memory leaks in metric scrapers.

---

### Production Readiness & Known Limitations

- **Identity Provider (IdP)**: Current tenant authentication supports both constant-time API key verification (`CLIENT_TENANT_KEYS`) and enterprise OAuth2/OIDC JWT validation (`OIDCAuthValidator` supporting RS256/ES256/HS256 with key rotation and strict algorithm whitelist).
- **SSRF Network Controls**: Multi-tier application-level IP validation blocks private subnets, decimal/hex/octal/shortened alternate IP encodings, IPv4-mapped IPv6 addresses, DNS rebinding, and cloud metadata services. In high-security multi-tenant deployments, this is paired with Docker internal network tiers or Kubernetes network policies.
- **At-Least-Once Tool Execution**: For upstream MCP servers without native idempotency token support, tool execution semantics remain at-least-once. Side-effecting operations interrupted mid-flight must be reconciled by human operators via the reconciliation state machine.

---

## Phase 7: Production Hardening, Security Verification & Release Readiness

Phase 7 completes the production hardening of MCP SmartCache across security boundaries, transport filtering, authentication federation, failure resilience, and deployment architecture.

### 1. SSRF & Outbound Transport Hardening
- **Flexible IP Address Parser (`parse_flexible_ip`)**: Transparently decodes alternate representations before evaluation:
  - Decimal integers (e.g. `http://2130706433/` -> `127.0.0.1`)
  - Hexadecimal IPs (e.g. `http://0x7f000001/` -> `127.0.0.1`)
  - Dotted octal IPs (e.g. `http://0177.0.0.1/` -> `127.0.0.1`)
  - Shortened IP formats (e.g. `http://127.1/` -> `127.0.0.1`)
  - IPv4-mapped IPv6 addresses (e.g. `http://[::ffff:127.0.0.1]/` and `http://[::ffff:169.254.169.254]/`)
- **Comprehensive Cloud Metadata Blocklist**:
  - Unconditionally blocks cloud metadata endpoints across all environments: AWS/GCP/Azure (`169.254.169.254`), Alibaba Cloud (`100.100.100.200`), Azure WireServer (`168.63.129.16`), Oracle Cloud (`192.0.0.192`), and GCP metadata (`metadata.google.internal`).
  - Production blocks loopback, RFC 1918 private subnets, CGNAT (`100.64.0.0/10`), link-local, multicast, reserved, and unspecified addresses.
- **DNS Rebinding Protection**:
  - Resolves domain names via `socket.getaddrinfo()` during registration; if any returned address resolves to a forbidden or private address, registration is rejected.
- **Stdio Transport Subprocess Lockdown**:
  - Disallows shell interpreters (`sh`, `bash`, `zsh`, `csh`, `cmd.exe`, `powershell.exe`, `pwsh`).
  - Metacharacter filter strictly rejects `[;&|`$<>()\n\r"'\\]`.

### 2. Enterprise Authentication & OIDC Integration
- **OAuth2 / OIDC Cryptographic JWT Provider (`gateway/oidc.py`)**:
  - Supports standard OIDC Bearer tokens with signature verification against configured public keys or dynamic JWKS (`JWKSCache`).
  - Strict algorithm whitelist (`RS256`, `ES256`, `HS256`). Rejects unsigned tokens or tokens specifying algorithm `none`.
  - Enforces `iss`, `aud`, `exp`, `nbf`, and `iat` time claims.
  - Zero-trust tenant claim extraction (`tenant_id`, `tid`, `org_id`) and role mapping (`roles`, `groups`).
- **Control API & Gateway Hardening**:
  - All API key and secret checks use constant-time `secrets.compare_digest`.
  - Restricted CORS origin filtering (`CORS_ALLOWED_ORIGINS`).
  - Lifespan startup validation (`validate_production_security_config`) enforcing minimum key lengths and rejecting dev placeholders in production.
  - DoS mitigation via `ContentLengthLimitMiddleware` enforcing strict payload size bounds.

### 3. Workflow Concurrency, Replay & Cancellation Safety
- **Tenant-Isolated Cancellation**:
  - Control API exposes `POST /workflows/{id}/cancel` enforcing tenant ownership, rejecting cross-tenant cancellation with HTTP 404, transitioning state to `CANCELLED`, and publishing audit/trace telemetry.
- **Resumption Race Condition Elimination**:
  - `POST /workflows/{id}/resume` verifies active workflow status, rejecting resume requests on currently `RUNNING` workflows with HTTP 409 Conflict.
- **Reconciliation Decision Hardening**:
  - Strips whitespace on `idempotency_key` and strictly rejects whitespace-only keys when `risk_acknowledged=False`.
  - Prevents conflicting decisions on finalized reconciliation cases (`RESOLVED` or `REJECTED`) with HTTP 409 Conflict.

### 4. Distributed Quota Fail-Closed Resilience
- **Configurable Outage Policy**:
  - Supports `QUOTA_REDIS_FAILURE_POLICY=fail_closed` to strictly reject traffic with HTTP 429 when Redis is unreachable, protecting upstream services against quota overages.
- **Permit Namespace Separation**:
  - Distinguishes permits (`prm_redis_` vs `prm_local_`), guaranteeing that releasing a Redis permit during transient connection issues never corrupts local concurrency counts.

### 5. Sensitive Data Redaction & Sanitization
- **Regex Redaction in String Fields**:
  - Automatically redacts authorization headers (`Bearer [REDACTED]`) and AWS keys (`[REDACTED_AWS_KEY]`) within log messages, prompt inputs, and trace event details.
- **Sensitive Key Expansion**:
  - Deep dictionary recursion redacts `password`, `token`, `secret`, `api_key`, `client_secret`, `admin_key`, `id_token`, `refresh_token`, `cookie`, `session`, `x-api-key`, and private keys.

### 6. Production Deployment Stack (`docker-compose.prod.yml`)
- **Internal Network Isolation**:
  - PostgreSQL and Redis containers do not publish host ports; they reside strictly within an isolated `internal-tier` Docker network.
- **Unprivileged Containers**:
  - Application services enforce non-root execution (`user: "1000:1000"`) and `no-new-privileges:true`.
- **Resource Constraints & Log Rotation**:
  - CPU and memory bounds defined for all containers with JSON-file log rotation.

---

## Phase 8: Independent Security Verification, Reliability Engineering & Production Deployment

Phase 8 independently verifies and hardens the entire MCP SmartCache platform against adversarial threat vectors, concurrency race conditions, and production failure modes.

### 1. Connection-Time SSRF & Kernel Socket Peer Inspection (`gateway/outbound_transport.py`)
- **Anti-DNS Rebinding (TOCTOU Defense)**:
  - Validating hostnames at registration time alone is insufficient against DNS rebinding, where a domain returns a public IP during registration and `127.0.0.1` at connection time.
  - Subclassed `AnyIOBackend.connect_tcp()` in `SafeOutboundNetworkBackend`:
    - Validates all resolved addresses before dialing.
    - Inspects the actual connected TCP peer socket (`stream._raw_socket.getpeername()`). If the connected peer IP is a loopback, private, link-local, or cloud metadata address, the connection is instantly aborted with `SSRFSecurityViolation`.
- **Redirect Interception (`SafeAsyncHTTPTransport`)**:
  - Automatically intercepts HTTP 3xx responses and validates the target `Location` header before allowing the client to follow redirects.
- **MCP Client Integration**:
  - `create_safe_mcp_http_client` is supplied as `httpx_client_factory` across `sse_client` tool discovery and runtime tool invocation.

### 2. JWT Algorithm Confusion Attack Defense (CVE-2015-9235)
- **Key-Type Separation in `gateway/oidc.py`**:
  - Prevents an attacker from signing a forged JWT using HMAC (`HS256`) with the server's public RSA/EC key as the HMAC secret.
  - The validator strictly rejects `HS256` tokens if the configured verification key is an asymmetric public key (PEM string or `PublicKey` instance).
  - Enforces mandatory `issuer` and `audience` parameters on every validation attempt.

### 3. Non-Disclosing Authorization Boundary
- Standardized all tenant-facing endpoints (`/workflows/{id}`, `/policies/{id}`, `/policies/{id}/activate`, `/policies/{id}/rollback`, `/reconciliations/{id}`) to return HTTP 404 Not Found when a caller attempts to access another tenant's resource, eliminating resource existence disclosure side-channels.

### 4. Database-Level Atomic Workflow Status Locking
- Enhanced `StorageRepository.update_workflow_status()` with PostgreSQL row-level locks:
  ```sql
  SELECT * FROM workflow_runs WHERE workflow_id = $1 FOR UPDATE
  ```
- Guarantees strict serializability during concurrent workflow resumption, cancellation, and worker execution. Prevents parallel requests from overwriting terminal states (`COMPLETED`, `CANCELLED`).

### 5. Multi-Pattern Sensitive Data Redaction (`storage/sanitize.py`)
- Automatically detects and redacts:
  - Database connection strings (`postgresql://user:[REDACTED]@host:port/db`, `redis://:[REDACTED]@host:port/db`).
  - Multiline PEM private keys (`[REDACTED_PRIVATE_KEY]`).
  - HTTP Basic authorization headers (`Basic [REDACTED]`).
  - Session and JWT cookies (`session=[REDACTED]`, `jwt=[REDACTED]`).

### 6. Automated Production Verification & CI Pipeline
- **Continuous Integration (`.github/workflows/ci.yml`)**:
  - Spins up PostgreSQL 16 and Redis 7 service containers.
  - Executes database migrations, backend unit tests, and adversarial security suites.
  - Performs TypeScript type validation (`tsc --noEmit`) and Vite production bundling.
  - Validates `docker-compose.prod.yml` configuration syntax.
- **Production Config Validator (`scripts/validate_production_config.py`)**:
  - Enforces minimum secret lengths, verifies lack of development placeholders, checks CORS origins, and validates Redis failure policies prior to deployment.
- **Sanitized Environment Template (`.env.example`)**:
  - Provides a safe template of all required environment variables with non-secret examples.

---

## Phase 9 & 10: Production Release Gate & Deployment Rehearsal

Phase 10 independently verified the entire stack through a live deployment rehearsal, confirming fail-closed outbound SSRF transport, multi-mode configuration validation, multi-worker task claim mutual exclusion, and durability across container restarts.

### Operational Deliverables & Verification
- **Operational Acceptance Report**: [`docs/release/phase10-operational-acceptance.md`](docs/release/phase10-operational-acceptance.md) (Full test evidence matrix, 258 passing tests, live deployment metrics).
- **Production Operations Runbook**: [`docs/runbooks/production-operations.md`](docs/runbooks/production-operations.md) (Procedures for deployment, health checks, backup/restore, secret rotation, and incident recovery).
- **Architectural Decision Record**: [`docs/adr/0010-production-deployment-rehearsal-and-operational-acceptance.md`](docs/adr/0010-production-deployment-rehearsal-and-operational-acceptance.md).






