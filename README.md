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

### Production Readiness & Known Limitations

- **Identity Provider (IdP)**: Current tenant authentication uses constant-time API key verification and strict tenant binding (`CLIENT_TENANT_KEYS`). Production enterprise deployments should integrate an external OAuth2/OIDC identity provider issuing cryptographically signed JWTs with tenant claims.
- **SSRF Network Controls**: Application-level IP validation blocks private subnets and cloud metadata services. In high-security multi-tenant deployments, this should be paired with Kubernetes egress network policies or VPC security groups.
- **At-Least-Once Tool Execution**: For upstream MCP servers without native idempotency token support, tool execution semantics remain at-least-once. Side-effecting operations interrupted mid-flight must be reconciled by human operators.


