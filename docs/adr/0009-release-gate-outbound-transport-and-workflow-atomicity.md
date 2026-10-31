# ADR 0009: Release Gate, Outbound Transport Fail-Closed Verification, and Workflow Atomicity

## Status
Accepted

## Date
2026-10-29

## Context
Phase 8 introduced socket peer verification and row-level locking for PostgreSQL. Independent release gate audit during Phase 9 identified several subtle edge-case boundaries requiring formal architectural contracts:
1. **Outbound Network Transport Fail-Closed Invariant**: In `gateway/outbound_transport.py`, the network backend inspected the socket peer to defeat DNS rebinding. However, if socket inspection attributes were missing or raised an exception, the implementation previously logged a debug note and returned the stream (fail-open). Furthermore, IPv4-mapped IPv6 representations (e.g. `::ffff:127.0.0.1` and `::ffff:100.100.100.200`) could evade IPv4-only metadata/loopback checks when evaluated as IPv6 objects. Redirect locations required strict scheme checking to forbid non-HTTP schemes (`file://`, `gopher://`).
2. **Workflow Resume & Task Claim Concurrency**: While row-level locking (`SELECT ... FOR UPDATE`) was added in Phase 8, two concurrent resume requests could both attempt status updates if transitions between identical states (`running` -> `running`) were permitted. Additionally, concurrent task workers required an atomic claim mechanism (`claim_task`) to guarantee that only one worker can acquire execution rights on a pending task.
3. **Configuration Validation Invariants**: Deployments required clear separation between *template syntax validation* (ensuring `.env.example` declares all necessary configuration keys without demanding real secrets) and *production readiness validation* (enforcing secret entropy, non-default credentials, explicit CORS domains, and secret-safe error reporting).

## Decision

### 1. Fail-Closed Outbound Transport and IPv4-Mapped IPv6 Unwrapping
- `SafeOutboundNetworkBackend` queries `stream.get_extra_info("server_addr")` and `stream.get_extra_info("socket")`. If the connected peer IP cannot be conclusively inspected, it immediately invokes `await stream.aclose()` and raises `SSRFSecurityViolation`.
- `is_forbidden_ip()` unwraps `ip.ipv4_mapped` on any `IPv6Address` instance before applying metadata, CGNAT, loopback, and private network rules, eliminating bypasses via mapped representations.
- `SafeAsyncHTTPTransport` intercepts 3xx redirects to ensure:
  - Schemes are strictly restricted to `http` and `https`.
  - Redirect destinations resolving to loopback, private IPs, or cloud metadata fail closed immediately before issuing follow-up requests.
  - Malformed `Location` headers abort execution safely.

### 2. State-Gated Status Transitions and Atomic Task Claiming
- `StorageRepository.update_workflow_status()` accepts an optional `expected_status` parameter (single value or set). Status transitions are verified inside the PostgreSQL row-lock transaction. If `current.status` does not match `expected_status`, a `ValueError` is raised, mapped to HTTP `409 Conflict` by API controllers.
- `StorageRepository.claim_task()` implements an atomic conditional query:
  ```sql
  UPDATE workflow_tasks
  SET status = 'running', started_at = $3, attempt_count = attempt_count + 1
  WHERE workflow_id = $1 AND task_id = $2 AND status = 'pending'
  RETURNING *;
  ```
  This guarantees mutual exclusion across distributed workers without external distributed lock dependencies.

### 3. Multi-Mode Configuration Validation
- `scripts/validate_production_config.py` provides distinct operational modes:
  - `--mode template`: Validates that template files (`.env.example`) declare all required environment variables and placeholder definitions.
  - `--mode production`: Validates environment files or production configurations against strict entropy, non-default passwords, non-wildcard CORS, and complete OIDC parameters. Never prints raw secrets in logs or error messages.
  - `--mode runtime`: Validates active process environment `os.environ` on startup.

## Consequences
- Defeats DNS rebinding, redirect exploitation, and IPv4-mapped IPv6 evasion at the socket level.
- Eliminates race conditions in distributed workflow resumption and task dispatching.
- Provides automated release verification in CI and deployment pipelines.

