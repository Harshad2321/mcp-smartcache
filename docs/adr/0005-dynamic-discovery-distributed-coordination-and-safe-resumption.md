# ADR 0005: Dynamic Discovery, Distributed Coordination, and Safe Workflow Resumption

## Status
Accepted

## Context
In Phases 0–4, MCP SmartCache established:
- A policy-controlled MCP gateway with tool routing and explicit read-only caching.
- Process-local singleflight deduplication preventing duplicate concurrent in-flight requests within a single process.
- A LangGraph-based agent runtime executing repository intelligence workflows.
- A durable PostgreSQL control plane with multi-tenant authorization, audit logging, and live SSE trace streaming.

However, three key production limitations remained:
1. **Static Upstream Configuration**: Upstream MCP servers could only be registered via configuration files or dev mock discovery during process startup. Registering or updating an upstream server required restarting the gateway.
2. **Process-Local Request Coalescing**: When multiple gateway replicas ran behind a load balancer, identical cacheable requests hitting different replicas resulted in duplicate execution against upstream MCP servers.
3. **Interrupted Workflow Stagnation**: Workflows interrupted by gateway restart or worker failure were safely marked as `interrupted`, but lacked durable checkpoints and safe resumption mechanisms to resume eligible work without replaying side-effects.

## Decision

### 1. Dynamic Upstream Server Discovery and Atomic Hot Reload
- Upstream MCP servers are persisted in a PostgreSQL `upstream_servers` table with `config_version` optimistic locking and operational tags.
- Transports supported: `stdio` (subprocess execution) and `sse` (Server-Sent Events HTTP endpoints).
- **Security Boundaries**: Outbound URLs are strictly validated against SSRF attacks: private IP ranges, cloud metadata IPs (`169.254.169.254`), and loopback addresses are blocked in production. Subprocess commands are validated against shell metacharacters (`;&|$\` etc.). Credentials are never persisted in plaintext.
- **Staged Atomic Activation**: When a server is registered or refreshed, the gateway connects to a candidate instance, validates tool schemas, and builds a candidate registry snapshot. The catalog is atomically swapped only upon full validation success. If validation fails, the last known-good catalog is preserved.
- **Cluster Reload Propagation**: Administrative configuration updates publish reload events via Redis Pub/Sub (`mcp-sc:registry:events`), enabling all gateway replicas to synchronize without restarts.

### 2. Distributed Singleflight and Cross-Replica Coordination
- **Atomic Lease Acquisition**: Replicas coordinate on cacheable read operations using Redis atomic leases:
  `SET mcp-sc:lock:flight:{scope}:{cache_key} {owner_token} NX PX 15000`
- **Ownership-Safe Release**: Leaders release leases using a Lua compare-and-delete script:
  ```lua
  if redis.call("get", KEYS[1]) == ARGV[1] then
      return redis.call("del", KEYS[1])
  else
      return 0
  end
  ```
  This guarantees that a slow or expired leader can never accidentally release a lease acquired by a newer leader.
- **Pub/Sub Waiter Notification**: Coalescing waiters subscribe to channel `mcp-sc:chan:flight:{scope}:{cache_key}`. When the leader finishes, it publishes completion and waiters read the cached result.
- **Bounded Waiter Timeout & Graceful Degradation**: Waiters have a bounded 6.0-second timeout. If the leader crashes, hangs, or Redis becomes unreachable, callers fall back to process-local singleflight or independent execution.
- **Invalidation Race Condition Protection**: Tool invalidation writes epoch timestamps to `mcp-sc:inv_ts:{tool}:{scope}`. If an in-flight leader finishes computation after an invalidation occurred, the stale populated cache key is immediately evicted.
- **Tenant Scope Isolation**: All lock keys and pub/sub channels include the tenant security scope `flight:{scope}:{cache_key}`, preventing cross-tenant request coalescing or information leakage.

### 3. Durable Checkpointing and Safe Workflow Resumption
- **Durable Checkpoints**: State snapshots are persisted to `workflow_checkpoints` at task transition boundaries, storing completed task outputs, pending task lists, and graph schema versions.
- **Explicit Safe Resumption Policy**: Resumption eligibility distinguishes between pure computation, verified read-only tool calls, and side-effecting operations:
  - **Completed Tasks**: Output results are restored directly from the checkpoint; completed tasks are never re-executed.
  - **Read-Only Pending Tasks**: Repeatable read operations (`repo_metadata__get_repo_info`, `issues__list_issues`, etc.) are safely scheduled for execution.
  - **Interrupted Side-Effecting Tasks**: Operations that mutate state or lack verifiable idempotency (`repository__create_tag`, etc.) are **strictly blocked from automatic replay** and flagged with `can_resume = False` for manual operator reconciliation.
- **Compatibility Versioning**: Checkpoints enforce graph schema compatibility (`1.0.0`, `v1.0.0`). Incompatible graph versions are rejected with clear diagnostic reasons.
- **Audited Resumption**: Resumption attempts are authenticated, authorized against tenant boundaries, and permanently logged in `workflow_resume_attempts` and `audit_events`.

## Consequences
- Upstream MCP servers can be added, updated, enabled, disabled, and refreshed on running clusters without downtime.
- Cache stampedes and duplicate upstream calls across multiple gateway replicas are prevented by distributed leases.
- Interrupted workflows can be safely resumed from their latest durable checkpoint, reusing completed work without duplicating side effects.
- Security boundaries are strictly maintained: tenant isolation is preserved across distributed locks, tool schemas, and resumption endpoints.
