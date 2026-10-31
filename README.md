# MCP SmartCache

<p align="center">
  <strong>The Distributed Caching Gateway & Durable Orchestration Runtime for Model Context Protocol (MCP) Tools</strong>
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-Apache_2.0-blue.svg?style=flat-square" alt="License: Apache 2.0" /></a>
  <img src="https://img.shields.io/badge/Python-3.12%2B-3776AB.svg?style=flat-square&logo=python&logoColor=white" alt="Python 3.12+" />
  <img src="https://img.shields.io/badge/FastAPI-0.115%2B-009688.svg?style=flat-square&logo=fastapi&logoColor=white" alt="FastAPI" />
  <img src="https://img.shields.io/badge/LangGraph-1.2.18-7928CA.svg?style=flat-square&logo=python&logoColor=white" alt="LangGraph" />
  <img src="https://img.shields.io/badge/Redis-7.2_L2-DC382D.svg?style=flat-square&logo=redis&logoColor=white" alt="Redis 7.2" />
  <img src="https://img.shields.io/badge/PostgreSQL-16_ACID-336791.svg?style=flat-square&logo=postgresql&logoColor=white" alt="PostgreSQL 16" />
  <img src="https://img.shields.io/badge/React-18_TypeScript-61DAFB.svg?style=flat-square&logo=react&logoColor=black" alt="React 18" />
  <img src="https://img.shields.io/badge/Tailwind-v4-06B6D4.svg?style=flat-square&logo=tailwindcss&logoColor=white" alt="Tailwind CSS v4" />
</p>

<p align="center">
  <img src="assets/screenshots/architecture-hero.png" alt="MCP SmartCache Interactive System Architecture & Component Inspector" width="100%" />
</p>

<p align="center">
  MCP SmartCache sits between autonomous AI agents and upstream tools to eliminate redundant executions, cut tool-call latency by up to 99%, and protect downstream infrastructure from cache stampedes. Built on the Model Context Protocol (MCP), it combines deterministic SHA-256 caching, distributed singleflight deduplication, and transactional LangGraph checkpointing into a unified developer platform.
</p>

---

## Table of Contents

- [Why This Exists](#why-this-exists)
- [Key Features](#key-features)
- [System Architecture](#system-architecture)
- [Distributed Singleflight Protocol](#distributed-singleflight-protocol)
- [Product Tour & Screenshots](#product-tour--screenshots)
- [Quickstart](#quickstart)
- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [Security & Production Hardening](#security--production-hardening)
- [Roadmap & Known Limitations](#roadmap--known-limitations)
- [Contributing](#contributing)
- [License](#license)
- [Documentation & Resources](#documentation--resources)

---

## Why This Exists

Autonomous AI agents frequently execute repetitive, overlapping tool calls across parallel execution branches and multi-turn loops. Without an intermediary gateway, every single agent inquiry triggers an uncoordinated upstream roundtrip — causing severe API rate-limit exhaustion, unnecessary token spend, and service degradation. When hundreds of concurrent agents request the exact same external resource, uncoordinated systems suffer from catastrophic cache stampedes.

MCP SmartCache solves this by introducing a transparent, self-optimizing proxy layer between agents and MCP servers. It deterministically caches idempotent read queries, coalesces concurrent identical requests into a single execution leader, and provides durable state checkpointing so interrupted agent workflows resume seamlessly without re-running finished tasks.

---

## Key Features

- **Sub-5ms Distributed Caching**: Exact-match Redis L2 caching powered by deterministic SHA-256 canonical argument digests and strict tenant isolation.
- **Distributed Singleflight (Stampede Elimination)**: Coordinates concurrent identical requests via atomic Redis leases, coalescing $N$ callers into a single execution leader.
- **Durable LangGraph Workflows**: Compiles multi-step agent tasks into stateful DAG execution graphs backed by PostgreSQL checkpoints for zero-loss crash recovery.
- **Human-in-the-Loop (HITL) Safety Gates**: Automatically quarantines workflows that fail or crash during non-idempotent external mutations, requiring explicit operator risk sign-off.
- **Dynamic Upstream Server Discovery**: Auto-discovers and hot-reloads tool schemas across isolated `stdio` subprocesses and remote `SSE` transports with zero service downtime.
- **Multi-Tenant Policy Governance**: Enforces token bucket rate limits, concurrency semaphores, and tool ACL matrices (allowlist/denylist) with instant signed rollbacks.
- **Kernel Socket SSRF Hardening**: Subclasses network connection routines to inspect peer socket IPs at connection time, eliminating Time-of-Check to Time-of-Use (TOCTOU) DNS rebinding.
- **Developer Observability Console**: Linear-grade dark UI featuring real-time architecture topology, interactive task DAGs, millisecond SSE telemetry streams, and policy version logs.

---

## System Architecture

```mermaid
flowchart TD
    subgraph IngressTier ["1. Ingress Tier"]
        Agent["🤖 AI Agent / Client SDK"]
    end

    subgraph GatewayTier ["2. Gateway Tier (Port :8000)"]
        GW["MCP SmartCache Gateway\n(FastAPI / MCP SSE)"]
        PE["Policy & Quota Engine\n(Token Bucket / ACL)"]
        SF["Distributed Singleflight\n(Lua Lease Manager)"]
    end

    subgraph CoordinationTier ["3. Coordination & Cache Tier"]
        Redis[("Redis 7.2 L2 Cluster (:6379)\n• Cache Keys: mcp-sc:v1:*\n• Pub/Sub Waiter Bus\n• Concurrency Semaphores")]
    end

    subgraph ExecutionTier ["4. Execution Tier"]
        Runtime["LangGraph Agent Runtime (:8002)\n• Directed Acyclic Graphs (DAG)\n• Bounded Parallel Scheduler\n• Evidence Synthesis Engine"]
        Upstream["Upstream MCP Hosts\n• Isolated stdio subprocesses\n• Remote SSE Streams"]
    end

    subgraph PersistenceTier ["5. Persistence & Control Plane"]
        Ctrl["Control & Governance API (:8080)\n• REST & OpenAPI Engine\n• Real-Time SSE Event Hub"]
        PG[("PostgreSQL 16 ACID (:5432)\n• Durable Task Checkpoints\n• Tamper-Evident Audit Logs\n• Policy Version History")]
        Dashboard["React 18 Developer Console (:5173)\n• Interactive Architecture Canvas\n• Task DAG Visualizer\n• HITL Quarantine Gate"]
    end

    %% Ingress Flow
    Agent -->|"MCP Tool Call (Bearer Token / JWT)"| GW

    %% Gateway Internal Flow
    GW --> PE
    PE -->|"Check Quotas & Tool ACL"| Redis
    GW --> SF
    SF <-->|"Elect Leader / Register Waiters"| Redis

    %% Cache Hit Path
    Redis -.->|"Warm Hit (< 4ms)"| GW
    GW -.->|"Instant Cached Result"| Agent

    %% Cold Miss Execution Path
    SF -->|"Cold Miss (Leader Only)"| Upstream
    Upstream -->|"Tool Execution Output"| GW
    GW -->|"Store L2 Cache Entry"| Redis

    %% Orchestration Path
    Agent -->|"Dispatch Workflow Plan"| Runtime
    Runtime -->|"Execute Subtasks via MCP"| GW
    Runtime -->|"Transactional State Snapshot"| PG

    %% Control Plane & UI
    Ctrl <--> PG
    Ctrl <--> Redis
    Dashboard <-->|"REST / Live SSE Event Stream"| Ctrl
```

### Request Flow Walkthrough
Incoming tool calls from AI agents arrive at the **MCP Gateway**, where the **Policy Engine** evaluates tenant rate quotas and tool access controls against Redis. For cache-eligible read operations, the **Distributed Singleflight** manager checks the **Redis L2 Cache**; if a cache miss occurs, only the elected leader executes the tool against the **Upstream MCP Host**, while concurrent duplicate callers wait on Redis Pub/Sub channels to receive the cached result in sub-4ms.

---

## Distributed Singleflight Protocol

When multiple concurrent agent workers request the identical tool invocation during parallel workflows, MCP SmartCache coalesces them to prevent downstream cache stampedes:

```mermaid
sequenceDiagram
    autonumber
    participant AgentA as Agent Worker A (Leader)
    participant AgentB as Agent Worker B (Waiter)
    participant GW as MCP Gateway (:8000)
    participant Redis as Redis L2 (:6379)
    participant Tool as Upstream MCP Tool

    AgentA->>GW: POST /call (tool="get_repo_info", args={...})
    AgentB->>GW: POST /call (tool="get_repo_info", args={...})
    
    rect rgb(20, 25, 35)
        Note over GW,Redis: Distributed Singleflight Lease Coordination
        GW->>Redis: SET NX PX 15000 (flight:tenant_a:hash)
        Redis-->>GW: OK (Agent A elected Leader)
        GW->>Redis: SUBSCRIBE flight:tenant_a:hash (Agent B attaches as Waiter)
    end

    GW->>Tool: Execute Upstream Subprocess
    Tool-->>GW: Upstream Tool Result (642ms)
    
    rect rgb(16, 35, 25)
        Note over GW,Redis: Atomic Cache Populate & Fanout
        GW->>Redis: SETEX cache:tenant_a:hash 300 <payload>
        GW->>Redis: PUBLISH flight:tenant_a:hash <payload>
        GW->>Redis: EVAL Lua Release Lease
    end

    GW-->>AgentA: 200 OK (Cold Miss: 642ms)
    GW-->>AgentB: 200 OK (Singleflight Coalesced: 3.8ms)
```

---

## Product Tour & Screenshots

The platform includes a dedicated, dark-mode developer control console built with React 18, Tailwind CSS v4, and React Flow.

### 1. Interactive System Topology
Symmetrical 3-column architecture visualizer rendering live request flows across Gateway, Redis, LangGraph Runtime, and Upstream MCP Servers, complete with a slide-out Component Inspector.
<p align="center">
  <img src="assets/screenshots/architecture-hero.png" alt="Architecture Topology View" width="100%" />
</p>

### 2. Cache & Singleflight Visualizer
Real-time latency drop curves, hit-ratio counters, and an interactive packet-pulse simulator illustrating cold-miss vs warm-cache fanout.
<p align="center">
  <img src="assets/screenshots/cache-singleflight.png" alt="Cache & Singleflight View" width="100%" />
</p>

### 3. Task Dependency DAG Visualizer
Interactive graph of compiled LangGraph workflows showing parallel step execution, tool parameters, schema validation, and checkpoint metadata.
<p align="center">
  <img src="assets/screenshots/task-dag-visualizer.png" alt="Task Dependency DAG Visualizer" width="100%" />
</p>

### 4. Human-in-the-Loop (HITL) Safety Gate
Quarantine queue for workflows interrupted during non-idempotent mutations, featuring raw JSON payload inspection and mandatory operator risk acknowledgment before replay.
<p align="center">
  <img src="assets/screenshots/reconciliation-hitl.png" alt="Human-in-the-Loop Reconciliation View" width="100%" />
</p>

---

## Quickstart

### Prerequisites
- [Docker Engine](https://docs.docker.com/engine/install/) (v24.0+)
- [Docker Compose](https://docs.docker.com/compose/) (v2.20+)
- Python 3.12+ and Node.js 20+ (only if running locally without Docker)

### 1. Start Infrastructure Stack
Clone the repository and launch all 6 containerized services:

```bash
docker compose up -d --build
```

### 2. Verify Service Health
Ensure all containers are running and report healthy status:

```bash
docker compose ps
```

*Expected output: 6 services active (`dashboard`, `gateway`, `runtime`, `control_api`, `redis`, `postgres`).*

| Service | Address | Role | Health Endpoint |
| :--- | :--- | :--- | :--- |
| **Developer Console** | `http://localhost:5173` | React 18 / Vite Management UI | `GET /` |
| **MCP Gateway** | `http://localhost:8000` | MCP Ingress & Singleflight Core | `GET /health` |
| **Agent Runtime** | `http://localhost:8002` | LangGraph Workflow Engine | `GET /health` |
| **Control API** | `http://localhost:8080` | Control Plane & SSE Stream | `GET /health` |
| **Redis Cache L2** | `localhost:6379` | Distributed Cache & Pub/Sub | `redis-cli ping` |
| **PostgreSQL Datastore** | `localhost:5432` | Relational Checkpoints & Audit | `pg_isready` |

### 3. Execute a Tool Call
Invoke a namespaced tool through the Gateway to observe the automatic transition from Cold Miss to sub-4ms Warm Cache Hit:

```bash
# 1. First Call: Cold Miss (Executes upstream tool)
curl -X POST http://localhost:8000/call \
  -H "Authorization: Bearer dev-client-secret-key-32chars-long" \
  -H "X-Tenant-ID: tenant_a" \
  -H "Content-Type: application/json" \
  -d '{
    "tool": "repo_metadata__get_repo_info",
    "arguments": {"owner": "google", "repo": "smartcache"}
  }'

# 2. Second Call: Warm Cache Hit (< 4ms, served directly from Redis L2)
curl -X POST http://localhost:8000/call \
  -H "Authorization: Bearer dev-client-secret-key-32chars-long" \
  -H "X-Tenant-ID: tenant_a" \
  -H "Content-Type: application/json" \
  -d '{
    "tool": "repo_metadata__get_repo_info",
    "arguments": {"owner": "google", "repo": "smartcache"}
  }'
```

---

## Tech Stack

| Layer | Technology | Primary Purpose in Codebase |
| :--- | :--- | :--- |
| **Gateway Ingress** | [FastAPI](https://fastapi.tiangolo.com/) + [Uvicorn](https://www.uvicorn.org/) | High-throughput async REST & Server-Sent Events (SSE) ingress proxy. |
| **MCP Protocol** | [Model Context Protocol Python SDK](https://github.com/modelcontextprotocol/python-sdk) | Standardized tool discovery, schema representation, and invocation. |
| **Workflow Engine** | [LangGraph](https://github.com/langchain-ai/langgraph) | Stateful multi-step agent graphs with cyclical state transition safety. |
| **Cache & Coordination** | [Redis 7.2](https://redis.io/) via `redis-py` | Distributed L2 cache, singleflight pub/sub bus, and atomic token buckets. |
| **Persistence** | [PostgreSQL 16](https://www.postgresql.org/) via `asyncpg` | Transactional checkpoints, immutable audit trails, and policy versions. |
| **Frontend Framework** | [React 18](https://react.dev/) + [TypeScript](https://www.typescriptlang.org/) | Component-driven developer platform console with strict type-safety. |
| **Diagrams & Graphs** | [React Flow (xyflow)](https://reactflow.dev/) | Interactive system topology and task DAG visualizers with custom nodes. |
| **Styling & Motion** | [Tailwind CSS v4](https://tailwindcss.com/) + [Framer Motion](https://www.framer.com/motion/) | Linear-grade dark design system, micro-interactions, and page transitions. |

---

## Project Structure

<details>
<summary><strong>Click to expand the full repository directory tree</strong></summary>

```
.
├── gateway/                    # MCP Gateway, routing, caching, and singleflight core
│   ├── auth.py                 # Constant-time API key and tenant scope validation
│   ├── cache_key.py            # Canonical SHA-256 deterministic cache key builder
│   ├── cache_policy.py         # Default-deny cache policy models and registration
│   ├── cache_store.py          # Redis L2 cache operations and secondary Set indexes
│   ├── distributed_singleflight.py # Redis distributed locks & Lua lease management
│   ├── ingress_middleware.py   # Trusted proxy and content length boundary middlewares
│   ├── main.py                 # Gateway FastAPI entrypoint and SSE transport
│   ├── oidc.py                 # Enterprise OIDC JWT signature and algorithm validator
│   ├── outbound_transport.py   # Kernel socket peer IP inspection (Anti-SSRF / Anti-DNS rebinding)
│   ├── policy_engine.py        # Tool ACL allowlist/denylist evaluation engine
│   ├── quota_manager.py        # Distributed rate limiting & concurrency semaphores
│   └── router.py               # Central GatewayRouter: policy checks and upstream dispatch
├── runtime/                    # LangGraph agent workflow engine and task scheduler
│   ├── graph.py                # LangGraph StateGraph state machine lifecycle
│   ├── main.py                 # Runtime FastAPI service and crash recovery on startup
│   ├── mcp_client.py           # Official MCP Python SDK client over SSE and stdio
│   ├── models.py               # Pydantic data schemas for agent states and DAG nodes
│   ├── plan_validator.py       # Deterministic task DAG validator (cycles, schemas, tools)
│   ├── resume_policy.py        # Conservative safe resumption logic for interrupted workflows
│   ├── scheduler.py            # Bounded parallel dependency-aware DAG scheduler
│   └── synthesis.py            # Grounded evidence synthesis with citation links
├── control_api/                # Control plane management service
│   └── main.py                 # REST endpoints for servers, policies, workflows, and audit
├── storage/                    # Multi-tenant persistence layer
│   ├── database.py             # Asyncpg connection pooling and transaction contexts
│   ├── events.py               # Dual-layer event hub (PostgreSQL replay + Redis Pub/Sub)
│   ├── migrations/             # Transactional, version-controlled SQL migration runner
│   ├── models/entities.py      # Dataclasses and state transition rules for stored records
│   ├── repository.py           # Tenant-isolated transactional repository operations
│   └── sanitize.py             # Deep dictionary and regex sensitive secret redaction
├── dashboard/                  # React 18 + Vite developer platform frontend
│   ├── src/
│   │   ├── components/         # Shared UI components (Sidebar, Header, MetricCards, DAG)
│   │   ├── pages/              # 8 flagship platform views (Architecture, DAG, Cache, etc.)
│   │   ├── services/api.ts     # Control API client with fallback to structured workload data
│   │   └── types/              # TypeScript definitions for entities, DAGs, and traces
├── docs/                       # Architecture records, guides, and changelogs
│   ├── adr/                    # 12 Architecture Decision Records (ADRs)
│   ├── release/                # Release acceptance reports and screenshot guides
│   ├── runbooks/               # Production operations and runbook guides
│   └── CHANGELOG.md            # Detailed phase-by-phase development history
├── tests/                      # Automated unit, integration, and security test suites
├── docker-compose.yml          # Container configuration for complete local cluster
└── pyproject.toml              # Python project metadata, dependencies, and test config
```
</details>

---

## Security & Production Hardening

- **Connection-Time Kernel Socket Inspection**: Defeats Time-of-Check to Time-of-Use (TOCTOU) DNS rebinding by inspecting the actual connected TCP socket peer IP (`stream._raw_socket.getpeername()`) in [`SafeOutboundNetworkBackend`](gateway/outbound_transport.py) before transmitting outbound data.
- **Strict Cloud Metadata & Private Subnet Blocking**: Unconditionally drops connections to `169.254.169.254` (AWS/GCP/Azure), `100.100.100.200` (Alibaba), `168.63.129.16` (Azure WireServer), loopback, link-local, and RFC 1918 private subnets.
- **JWT Algorithm Confusion Defense (CVE-2015-9235)**: [`OIDCAuthValidator`](gateway/oidc.py) strictly rejects HMAC (`HS256`) tokens when the configured verification key is an asymmetric RSA or EC public key.
- **Multi-Tenant Cryptographic Isolation**: Cache keys, flight leases, pub/sub channels, policies, and queries are strictly bound to authenticated tenant IDs (`CLIENT_TENANT_KEYS`), rejecting unauthorized cross-tenant requests with `404 Not Found` to eliminate resource existence side-channels.
- **Multi-Pattern Secret Redaction**: Automatically scrubs database connection strings, multiline PEM private keys, bearer authorization tokens, and API credentials from logs and audit trails via [`storage/sanitize.py`](storage/sanitize.py).

---

## Roadmap & Known Limitations

- [ ] **External IdP Dynamic Discovery**: Automate periodic OIDC `.well-known/openid-configuration` fetching and dynamic JWKS key rotation across third-party identity providers.
- [ ] **Multi-Region Cache Replication**: Support geo-distributed Redis replication topologies with active-active conflict resolution.
- [ ] **OpenTelemetry OTLP Exporter**: Direct gRPC export of distributed spans to Grafana Tempo, Jaeger, and Datadog.
- [ ] **Streaming LLM Token Caching**: Chunked semantic cache evaluation for long-running streaming tool completions.

---

## Contributing

Contributions, bug reports, and feature proposals are welcome! Please follow these steps:

1. Fork the repository and create your feature branch: `git checkout -b feature/my-feature`.
2. Ensure all unit and integration tests pass: `pytest tests/`.
3. Verify the frontend builds cleanly without TypeScript errors: `npm --prefix dashboard run build`.
4. Open a Pull Request with a clear description of your changes and architectural impact.

---

## License

This project is licensed under the Apache 2.0 License. See the [LICENSE](LICENSE) file for details.

---

## Documentation & Resources

- **Phase-by-Phase Development History**: [`docs/CHANGELOG.md`](docs/CHANGELOG.md)
- **Production Operations Runbook**: [`docs/runbooks/production-operations.md`](docs/runbooks/production-operations.md)
- **LinkedIn Dashboard Screenshot Guide**: [`docs/release/linkedin-dashboard-screenshot-guide.md`](docs/release/linkedin-dashboard-screenshot-guide.md)
- **Architecture Decision Records**: [`docs/adr/`](docs/adr/)
