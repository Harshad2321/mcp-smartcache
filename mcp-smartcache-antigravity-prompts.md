# MCP SmartCache — Antigravity Mission Prompts

**How to use this**

1. Open Antigravity, create/open the project folder (empty is fine).
2. Paste the **Workspace Rules** block below once, as a persistent rule (Antigravity lets you save workspace-level rules/memory — add it there so every phase inherits it).
3. Paste **Phase 0** into the Manager surface as a new mission. Review the plan the agent proposes before letting it execute, then review the diff/output before accepting.
4. Only move to the next phase after you've accepted the previous one. Each phase prompt assumes the previous phases are done and in the repo — don't skip ahead.
5. If an agent proposes something that violates a workspace rule (e.g. caching a write op, skipping tests), reject and point it back at the rule.

---

## Workspace Rules (paste once, persistent)

```
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
```

---

## Phase 0 — Repo scaffold + architecture lock-in

```
Mission: Phase 0 — Scaffold MCP SmartCache and lock in the architecture.
No business logic yet. Produce structure, not features.

First, inspect the current directory. If it's not empty, tell me what's
there before proposing changes — don't overwrite anything without asking.

Deliverables:

1. Repo layout (propose it as a tree, get my OK, then create it):
   - gateway/        (MCP-facing server: registry, routing, caching, dedup,
                       execution policy enforcement)
   - runtime/         (LangGraph agent orchestration — depends on gateway's
                       client interface only, never touches upstream
                       servers directly)
   - control_api/     (FastAPI management/config API)
   - storage/         (Postgres models + migrations, Redis access layer)
   - observability/   (OpenTelemetry + Prometheus wiring)
   - dashboard/        (React + TS + Vite, minimal for now)
   - mocks/           (deterministic mock MCP servers for dev + benchmark)
   - benchmarks/      (benchmark harness, added properly in a later phase)
   - tests/           (mirrors the module layout above)
   - docs/adr/        (architecture decision records)

2. Write ADRs (short, one per decision) for:
   - Why the gateway and runtime are separate processes/modules and the
     exact interface boundary between them.
   - What's persisted in Postgres vs Redis vs LangGraph checkpoints, and why
     (be explicit — no overlapping sources of truth for the same state).
   - The initial caching eligibility model (allowlist of read ops, not
     "all read-only tools").

3. docker-compose.yml with service stubs for: gateway, control_api, runtime,
   redis, postgres, dashboard — services can be no-ops/health-check-only
   for now, just prove the compose file is valid and `docker compose up`
   starts without crashing.

4. pyproject.toml / dependency manifests for the Python packages, with the
   fixed tech stack from the workspace rules. Pin versions you've actually
   verified exist (check PyPI), don't guess version numbers.

5. README.md: project description (one paragraph, no hype claims), how to
   run `docker compose up`, repo layout explanation.

6. A CONTRIBUTING-style doc or top-level NOTES.md listing the module
   boundary rules from the workspace rules, so they're visible in-repo too.

Acceptance criteria: `docker compose config` validates cleanly,
`docker compose up` starts all stub services without crash-looping, repo
tree matches what I approved, each ADR fits on one screen and states the
decision + the rejected alternative + why.

Do not write gateway logic, caching logic, or agent logic yet — that's
Phase 1+. If you're tempted to "just add" something small to make the
scaffold feel more real, don't — flag it to me instead and I'll tell you
which phase it belongs in.
```

---

## Phase 1 — MCP plumbing: registry, routing, mock servers

```
Mission: Phase 1 — Get real MCP traffic flowing end-to-end with zero caching.

Context: Phase 0 scaffold is in place (gateway/, runtime/, mocks/, etc.).
Inspect it before starting.

Before writing code: verify against the official MCP Python SDK docs
(current version) exactly how server registration, tool discovery, and
tool invocation work — don't assume method names. If the SDK's behavior
differs from what you expected, tell me.

Deliverables:

1. Two deterministic mock MCP servers under mocks/ — e.g. a mock "repo
   metadata" server and a mock "issues" server — each exposing a couple of
   read-only tools with fixed, predictable responses. Support configurable
   artificial delay and error injection (even if unused until Phase 6) —
   build the hooks now so you don't have to retrofit them later.

2. gateway/registry: a server registry that can register upstream MCP
   servers (name, transport, connection config, credentials placeholder),
   discover their tools, and namespace tool names to prevent collisions
   between servers. Store this in-memory for now unless Postgres wiring
   already makes more sense — tell me which you're choosing and why.

3. gateway/routing: a minimal gateway that itself speaks MCP (or exposes
   an MCP-compatible surface) and routes incoming tool-call requests to the
   correct upstream server/tool based on the registry, after schema
   validation of the arguments. No caching, no dedup — pure passthrough.

4. A small integration test (and/or CLI script) that: registers both mock
   servers, lists the unified/namespaced tool catalog, calls a tool on each
   server through the gateway, and asserts the responses match the mocks.

5. Basic connection health/lifecycle handling: what happens if a mock
   server is unreachable or returns malformed output — don't crash the
   gateway, return a structured error.

Acceptance criteria: the integration test passes, tool namespacing
demonstrably prevents a collision (prove it with a test, e.g. two mock
servers exposing a same-named tool), and an upstream failure produces a
clean error instead of a crash. Still zero caching/dedup — explicitly do
not add it even partially.
```

---

## Phase 2 — Caching and concurrent dedup

```
Mission: Phase 2 — Exact-match caching and singleflight-style request
deduplication, built on top of Phase 1's routing path.

Inspect gateway/routing from Phase 1 before starting; extend it, don't
replace it.

Deliverables:

1. Cache key design: canonical JSON serialization of arguments, server +
   tool identity, tool schema/config version, and tenant/auth scope (use a
   placeholder tenant id for now if auth isn't built yet, but the field
   must exist in the key from day one). Document the key scheme in a short
   doc under docs/.

2. An explicit per-tool caching allowlist/policy config (not "cache
   everything read-only"). Only tools on the allowlist are cacheable, with
   configurable TTL per tool.

3. Redis-backed cache store: get/set with TTL, explicit invalidate,
   explicit bypass/force-refresh on a request, and cleanup of expired
   entries. Cache-store-unavailable must degrade to "execute without cache"
   rather than failing the request — add a test for this.

4. Concurrent request coalescing (singleflight): if N concurrent callers
   request the same eligible cache key while no cached result exists yet,
   only one upstream call should fire; all callers get the result (or the
   error) when it resolves. Test: fire concurrent duplicate requests
   against a mock server with an artificial delay, assert the mock's
   call-counter is 1, not N. Also test what happens when the "leader"
   request times out — the waiters shouldn't hang forever.

5. Basic cache observability: hit/miss/bypass/expired counters exposed
   somewhere inspectable (even just a debug endpoint for now — real metrics
   wiring comes in Phase 5).

Acceptance criteria: a test suite that separately proves (a) a second
identical request hits the cache and does not call upstream, (b) concurrent
identical in-flight requests coalesce into one upstream call, (c) a
non-allowlisted tool is never cached even if arguments repeat exactly, and
(d) cache-store failure doesn't take down the gateway.
```

---

## Phase 3 — Agent runtime (LangGraph) and the Repo Intelligence Agent demo

```
Mission: Phase 3 — Build the LangGraph-based orchestration runtime and the
first real end-to-end demo: the Repo Intelligence Agent.

Verify current LangGraph APIs against official docs before coding —
don't assume prior-version method names.

The demo task: "Investigate this repository's technical health. Examine
its recent commits, open issues, configuration and documentation. Identify
notable risks, support your findings with evidence, and produce a concise
technical report." Use the two mock MCP servers from Phase 1/2 as the data
sources for now (real GitHub integration is optional/later, not required
for this phase).

Deliverables:

1. runtime/: a LangGraph workflow that (a) takes the task, (b) plans a set
   of tool calls with dependencies between them, (c) identifies which
   calls are independent and can run concurrently, (d) executes them —
   ALL tool calls go through the Phase 1/2 gateway's client interface, no
   exceptions, (e) collects results, (f) does a bounded replanning step if
   evidence is missing (cap the number of replanning rounds), (g) produces
   a final report citing which tool results back each claim.

2. Deterministic runtime enforcement (not model-trusted) for: max parallel
   tool calls, max total workflow steps, and an overall deadline/budget.
   If the model's plan would exceed these, the runtime truncates/rejects
   it — write a test that proves this with a plan that intentionally
   exceeds the limits.

3. Workflow state handling: use LangGraph checkpoints for in-flight
   workflow state. Don't duplicate that same state manually elsewhere yet
   (Postgres persistence of run metadata comes in Phase 4) — keep this
   phase's state story simple and say clearly what's ephemeral vs
   checkpointed.

4. A runnable CLI or script: `run_demo.py` (or similar) that executes the
   Repo Intelligence Agent against the mocks end-to-end and prints the
   report plus a simple execution trace (what was called, in what order,
   what was parallel, what came from cache if anything did).

Acceptance criteria: running the demo produces a coherent report with
evidence citations, the trace clearly shows at least one pair of calls that
ran concurrently because they were independent, and the step/deadline
limits are enforced by a passing test, not just a comment saying they are.
```

---

## Phase 4 — Security, authZ boundaries, persistence

```
Mission: Phase 4 — Auth boundaries, tenant isolation, Postgres persistence,
and write-operation approval gating.

Deliverables:

1. Caller authentication for the control API and gateway (pick the
   simplest thing that's actually correct — e.g. API keys/bearer tokens
   mapped to a tenant id — don't overbuild this).

2. Authorization: which tenant/caller may use which registered server/tool.
   Enforce this in the gateway before routing, not just in the control API.

3. Tenant isolation in the cache: extend Phase 2's cache key to use the
   real tenant id now, and write a test proving tenant A's cached result is
   never served to tenant B even with identical arguments.

4. Write-operation handling: any tool marked as a write/side-effecting
   operation defaults to no caching, no automatic retry, and requires
   explicit policy approval to execute at all — enforced by runtime code.
   Write a test with a mock "write" tool proving it's blocked without
   approval and never cached even if called with identical args twice.

5. storage/: Postgres models + migrations for server/tool config, cache
   policy config, workflow run metadata, task execution records, and audit
   records. Define a retention/redaction strategy for sensitive fields in
   stored payloads — don't just dump raw tool arguments/results into audit
   rows without thinking about what's sensitive.

6. Secret handling: upstream credentials and auth tokens must never appear
   in cache keys, cache values, or logs — write a test/log-scan that checks
   for this.

Acceptance criteria: isolation test passes, write-op gating test passes,
secret-leak test passes, and a fresh `docker compose up` applies Postgres
migrations cleanly.
```

---

## Phase 5 — Observability

```
Mission: Phase 5 — OpenTelemetry tracing, Prometheus metrics, and a minimal
dashboard showing a workflow run's execution trace.

Deliverables:

1. OpenTelemetry instrumentation across gateway and runtime: spans for
   tool calls, cache hit/miss/coalesce events, and workflow steps, with
   trace context propagated from the agent's plan through to the upstream
   mock call. Check the OpenTelemetry GenAI/MCP semantic conventions doc
   and follow it where it applies rather than inventing your own span
   naming scheme.

2. Prometheus metrics: at minimum, tool call counts (by server/tool/
   outcome), cache hit/miss/coalesce counts, workflow duration, and active
   concurrent tool calls.

3. dashboard/: a minimal React+TS+Vite page that lists recent workflow
   runs (from Postgres) and, for a selected run, shows the execution trace
   — which calls happened, parallel vs sequential, cache hits, errors.
   Keep it minimal — a usable table/timeline, not a polished product.

Acceptance criteria: running the Phase 3 demo produces traces/metrics
visible in whatever local viewer you wire up (e.g. a simple Jaeger/
Prometheus stack in docker-compose), and the dashboard shows that run's
trace correctly, including at least one cache hit and one parallel pair
if you re-run the demo task twice.
```

---

## Phase 6 — Benchmark harness

```
Mission: Phase 6 — Reproducible benchmark comparing four configurations.

Deliverables:

1. benchmarks/: a runnable, documented command that executes the Repo
   Intelligence Agent task against the mock servers under four configs:
   (1) baseline sequential agent, (2) parallel agent with no SmartCache,
   (3) parallel agent with caching + dedup, (4) full runtime with policies
   and observability on.

2. Use Phase 1's mock-server delay/error-injection hooks to create
   realistic, controlled workload conditions — identical inputs across all
   four configs.

3. Metrics captured per run: end-to-end duration, median/p95 latency,
   total upstream calls, cache hits/misses/bypasses/expirations, calls
   coalesced, calls avoided, error/recovery rate, output correctness check
   (the report must actually contain the expected evidence, not just
   "didn't crash"), and cache isolation violations (should be zero).

4. Machine-readable output (JSON or CSV) plus a short human-readable
   summary table.

5. A documented acceptance threshold you propose before running anything —
   e.g. "config 3 should reduce total upstream calls vs config 2 by at
   least X% given Y repeated/duplicate calls in this task" — then run it
   and report the actual numbers. Do not invent numbers; if a config
   doesn't show the expected improvement, say so and investigate why.

Acceptance criteria: the benchmark command runs clean end-to-end,
produces the machine-readable output, and the summary clearly separates
gains attributable to parallelization from gains attributable to
caching/dedup (configs 2 vs 3 isolate this).
```

---

## Phase 7 — Hardening, fault injection, docs

```
Mission: Phase 7 — Harden failure paths and finish documentation.

Deliverables:

1. Fault-injection tests using the mock servers' error/delay hooks for:
   upstream timeout, upstream error, Redis outage, Postgres outage,
   malformed tool response, cancellation mid-workflow, and partial
   workflow failure followed by recovery (don't blindly restart completed
   work — verify this with a test).

2. Review and tighten retry policy: confirm retries are idempotency-aware
   and that write operations are never silently retried.

3. Final README pass: accurate setup instructions, architecture diagram
   (Mermaid is fine), what's implemented vs explicitly deferred (semantic
   caching, multi-region, Kubernetes, etc. — list these as deferred, not
   missing), and a short "results" section summarizing the Phase 6
   benchmark findings without overclaiming.

4. A concise public-facing project description suitable for a GitHub
   README intro / LinkedIn post — no unmeasured performance claims, no
   "novel" claims without the competitor comparison backing it up.

Acceptance criteria: full test suite (unit + integration + fault-injection)
passes via a single documented command, `docker compose up` from a clean
clone works end-to-end including the dashboard, and the README is accurate
enough that a new developer could run the demo and the benchmark without
asking you anything.
```
