# Workspace Rules: MCP SmartCache

Project: MCP SmartCache — an MCP gateway + LangGraph agent runtime for agentic
tool orchestration (caching, dedup, dependency-aware parallel execution,
observability). Not a tutorial project — treat it as real infra.

Non-negotiable rules for every mission in this project:

1. Research before implementing. If a task touches the MCP protocol, the
   official Python SDK, or LangGraph APIs, verify current behavior against
   official docs/source before writing code — do not guess method names or
   assume SDK behavior from memory.
2. Keep the gateway and the agent runtime as separate, independently testable
   modules. The agent must call tools ONLY through the gateway's MCP
   interface. Never give the agent a private/direct path to an upstream
   server, and never let it implement its own cache.
3. Execution policy (caching eligibility, concurrency limits, auth checks,
   write-op approval) is enforced in deterministic runtime code, never left
   to "the model should know not to." If a policy can't be enforced in code
   yet, it doesn't exist yet.
4. Caching: only explicit allowlisted read operations are cacheable. Cache
   keys must include tool identity, canonical-serialized arguments, tool
   schema/version, and tenant/auth scope. Never cache or replay
   side-effecting operations (writes). Never let one tenant's cached result
   be served to another tenant.
5. No fabricated evidence. If you can't verify something (API behavior,
   benchmark numbers), say so explicitly instead of inventing it.
6. Every phase must end in something runnable via `docker compose up` plus a
   documented test/benchmark command — not just design docs.
7. Don't introduce infra beyond what's specified for the current phase
   (no Kubernetes, no vector DB, no extra orchestration frameworks, no extra
   model providers) unless a later phase explicitly calls for it.
8. Write tests for what you build in the same phase you build it, not later.
9. Before proposing a plan, inspect the current repo state (files, configs,
   existing code) rather than assuming it's empty or assuming your last run
   succeeded.
10. Stop and ask me before: deleting/rewriting a previous phase's working
    code, changing the chosen tech stack, or changing module boundaries
    defined in Phase 0.

Tech stack (fixed unless a phase says otherwise):
Python 3.12, official MCP Python SDK, LangGraph, FastAPI (control API),
Redis (cache + execution coordination), PostgreSQL (config/run
metadata/audit), React + TypeScript + Vite (dashboard), OpenTelemetry,
Prometheus, Docker Compose, Pytest.
