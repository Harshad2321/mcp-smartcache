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

### 6. Verification & Operational Testing

#### Running the Full Test Suite
```bash
docker compose exec gateway pytest -v
```

#### End-to-End Gateway API Verification via cURL

1. **Register Cache Policy for a Tool:**
```bash
curl -X POST http://localhost:8000/cache/policy \
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

2. **Execute First Tool Call (Cache Miss & Upstream Execution):**
```bash
curl -X POST http://localhost:8000/call \
  -H "Content-Type: application/json" \
  -d '{
    "tool": "repo_metadata__get_repo_info",
    "arguments": {"owner": "acme", "repo": "core-engine"}
  }'
```

3. **Execute Second Tool Call (Instant Cache Hit):**
```bash
curl -X POST http://localhost:8000/call \
  -H "Content-Type: application/json" \
  -d '{
    "tool": "repo_metadata__get_repo_info",
    "arguments": {"owner": "acme", "repo": "core-engine"}
  }'
```

4. **Verify Telemetry & Cache Metrics:**
```bash
curl -s http://localhost:8000/metrics | grep mcp_smartcache_
```

5. **Invalidate Tool Cache:**
```bash
curl -X DELETE http://localhost:8000/cache/invalidate/tool/repo_metadata__get_repo_info
```

### 7. Known Limitations
- **In-process singleflight**: Coalescing is within a single gateway process. Multi-replica distributed singleflight requires distributed locking (planned for future phases).
- **Authorization Scope**: Current implementation uses `scope="none"`. Full multi-tenant isolation will be wired to real auth identity tokens in Phase 4.
