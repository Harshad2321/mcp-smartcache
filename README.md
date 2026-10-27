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
