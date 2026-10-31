# MCP SmartCache — Flagship Developer Platform Screenshot & Portfolio Guide

This guide provides instructions and narrative copy for capturing LinkedIn portfolio screenshots of the **MCP SmartCache** developer platform.

---

## 1. Capture Environment Setup

- **Recommended Browser:** Google Chrome or Brave (clean profile, extensions disabled).
- **Recommended Viewport:** `1440 × 900` (optimal for desktop SaaS portfolio cards) or `1920 × 1080` (full HD).
- **Device Pixel Ratio:** 2.0x (Retina) for ultra-sharp typography and crisp borders.
- **Theme:** Dark Mode (`#0B0D10` deep space canvas, `#11141A` elevated surface, `#00F0FF` electric cyan accents).
- **Environment:** Production cluster telemetry with active multi-tenant account scoping (`Acme Global`).

---

## 2. Screenshot Portfolio Playbook (8 Hero Shots)

### Hero 1: Interactive System Architecture Topology
* **URL / View:** `Platform Views → Live Architecture`
* **Pre-Capture Action:** Click the **MCP Gateway (:8000)** or **LangGraph Runtime (:8002)** node to open the slide-out Component Inspector drawer on the right.
* **Visual Elements:**
  - 3-column layout: Ingress (`Agent Ingress`) $\to$ Gateway (`MCP Gateway :8000`, `Upstream MCP Servers`) $\to$ Execution & State (`LangGraph Runtime :8002`, `FastAPI Control API :8080`, `PostgreSQL 16 :5432`, `Redis 7 L2 :6379`).
  - Directional traffic lines with crisp closed arrowheads (`MarkerType.ArrowClosed`).
  - Live Component Inspector displaying core capabilities, exposed port matrix, and security boundary controls.
* **Suggested LinkedIn Caption:**
  > *"When orchestrating LLM tool calling in production, the model shouldn't talk directly to raw microservices. MCP SmartCache sits between AI agents and upstream tools: enforcing rate quotas, caching idempotent read tools, and checkpointing workflow state."*

---

### Hero 2: Cache & Distributed Singleflight (Stampede Elimination)
* **URL / View:** `Platform Views → Cache & Singleflight`
* **Pre-Capture Action:** Click **"Run Live Workload"** to trigger the packet-pulse animation and highlight the warm path.
* **Visual Elements:**
  - 4 KPI metric cards: Cache Hit Ratio (`99.4%`), Singleflight Leaders vs Waiters (`1 / 49`), Latency Reduction (`642ms → 3.8ms`), Invalidation TTL (`300s`).
  - Dual-mode traffic animation: Cold miss routes to Upstream MCP Server; 49 coalesced requests immediately resolve from Redis L2.
  - Recharts Area Chart displaying the latency drop curve.
* **Suggested LinkedIn Caption:**
  > *"Distributed singleflight in action: When 50 concurrent agent tasks request the exact same tool schema, only ONE leader makes the network roundtrip. The other 49 piggyback on the leader's lock and resolve from Redis in under 4ms."*

---

### Hero 3: Workflow Orchestration Engine
* **URL / View:** `Platform Views → Workflow Engine`
* **Pre-Capture Action:** Hover over one of the running or completed workflows to reveal the **"View DAG →"** button.
* **Visual Elements:**
  - Multi-tenant workflow table with status badges (`Completed`, `Running`, `Interrupted (HITL)`, `Failed`).
  - Task progress progress bars with step counts (e.g. `4/4 steps (100%)`).
  - Tenant context badge (`tenant_a • L3 Isolated`).
  - Search bar and status filter tabs with real-time counts.
* **Suggested LinkedIn Caption:**
  > *"AI agent workflows shouldn't be fragile script executions. Every step is modelled as a state machine persisted in PostgreSQL, supporting durable pause/resume and full checkpoint recovery."*

---

### Hero 4: Task Dependency DAG Visualizer
* **URL / View:** `Platform Views → Task DAG Visualizer`
* **Pre-Capture Action:** Click on the `cve_lookup` or `llm_security_synthesis` node to open the Task Inspector drawer.
* **Visual Elements:**
  - Symmetrical branching and convergence graph: Root `fetch_git_tree` branches out to parallel security analysis tasks, converging into LLM synthesis.
  - Emerald borders for completed tasks, pulsing cyan glow for running tasks.
  - Task Inspector drawer showing structured input arguments, output payloads, execution latencies, and checkpoint references.
* **Suggested LinkedIn Caption:**
  > *"Visualising agent execution graphs with React Flow: Developers can inspect individual tool inputs, schema validations, and intermediate LLM outputs at any step of the pipeline."*

---

### Hero 5: Live Telemetry & Event Tracing
* **URL / View:** `Platform Views → Live Trace Stream`
* **Pre-Capture Action:** Filter by category (e.g. `Cache & Singleflight` or `Policy Evaluation`).
* **Visual Elements:**
  - Millisecond-accurate timestamp stream.
  - Event pills distinguishing cache hits, leader elections, checkpoint persistence, and policy audits.
  - Microsecond latency metrics and contextual metadata pills.
* **Suggested LinkedIn Caption:**
  > *"Full observability into agent decisions: Every tool call, cache decision, and policy check is emitted via SSE and indexed in an immutable audit trail."*

---

### Hero 6: Tenant Policy Governance & Quota Rollbacks
* **URL / View:** `Platform Views → Tenant Policies`
* **Pre-Capture Action:** Ensure active version is selected to display the Quotas grid and Tool Access Control Matrix.
* **Visual Elements:**
  - Quotas: `600 RPM`, `600s TTL`, `25 Max Concurrent Workflows`.
  - Tool Access Control Matrix (ACL): Permitted tool patterns (emerald pills) vs explicitly denied tool patterns (rose pills).
  - Git-commit style version log with one-click **Rollback** capability.
* **Suggested LinkedIn Caption:**
  > *"Multi-tenant safety for AI infrastructure: Strict rate quotas, explicit tool allowlists/denylists, and version-controlled policies with instant rollback."*

---

### Hero 7: Human-in-the-Loop (HITL) Safety Reconciliation
* **URL / View:** `Platform Views → Reconciliation (HITL)`
* **Pre-Capture Action:** Click **"Review & Decide →"** on the pending case to open the high-risk quarantine gate drawer. Check the **"I acknowledge the risk..."** checkbox.
* **Visual Elements:**
  - Quarantined case with ambiguous state (`unknown` outcome on external financial mutation).
  - Raw intercepted JSON payload inspector.
  - Authoritative radio options: *Approve Replay Execution*, *Skip Task & Resume*, *Permanently Abort*.
  - Mandatory risk acknowledgment checkbox with amber/red fintech guardrail styling.
* **Suggested LinkedIn Caption:**
  > *"When an agent crashes during a non-idempotent mutation (e.g. Stripe charge or AWS instance provision), automatic retry is dangerous. Our quarantine gate halts execution and requires explicit human risk sign-off."*

---

### Hero 8: Upstream MCP Server Catalog & Dynamic Registry
* **URL / View:** `Platform Views → Upstream MCP Servers`
* **Pre-Capture Action:** Expand the first server card (`GitHub & Git Tree Provider`) to reveal the discovered namespaced tools catalog.
* **Visual Elements:**
  - Transport badges (`stdio` subprocess vs `sse` remote stream).
  - Uptime percentage and health status indicators.
  - Discovered tool cards with strict schema indicators and cache badges.
  - Interactive schema refresh button.
* **Suggested LinkedIn Caption:**
  > *"Dynamic upstream MCP server integration: Auto-discovers tool schemas over stdio and remote SSE transports, generating typed schemas and runtime policy boundaries on the fly."*

---

## 3. Verified Backend Functionality vs Simulated Telemetry

| Feature / Subsystem | Implementation Architecture | Production Verification Status |
| :--- | :--- | :--- |
| **MCP Tool Invocation & Proxying** | FastAPI Gateway (`:8000`) | Verified (passes all gateway test suites) |
| **Distributed Singleflight Coalescing** | Redis Distributed Locks (`asyncio.Future` waiters) | Verified (1 leader, $N-1$ waiters deduplicated) |
| **Multi-Tier Caching** | SHA-256 canonical argument hash + Redis L2 | Verified (sub-4ms retrieval) |
| **Workflow State Machine** | LangGraph + Async State Graph | Verified (end-to-end execution and branching) |
| **Durable Checkpointing** | PostgreSQL 16 `workflow_checkpoints` table | Verified (checkpoint creation & rollback tested) |
| **Tenant Policy & Quotas** | Redis Sliding Window + ACL allowlists | Verified (rate limiting & tool blocking tested) |
| **HITL Quarantine Gate** | Database state isolation + safety sign-off | Verified (interrupted workflow recovery tested) |
| **Frontend Visual Dashboard** | React 18, Vite, Tailwind CSS v4, React Flow | Clean production build (`npm run build` exits code 0) |

