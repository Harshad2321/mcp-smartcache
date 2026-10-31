# ADR 0007: Production Hardening, Security Verification, and Release Readiness

## Status
Accepted

## Context
Across Phases 0–6, MCP SmartCache evolved into a multi-tenant MCP tool gateway and workflow orchestration platform with Redis caching, singleflight coalescing, PostgreSQL persistence, dynamic policy governance, distributed quotas, and human-in-the-loop workflow reconciliation.

Before declaring production readiness, an exhaustive security audit and hardening review identified concrete vulnerabilities and boundaries requiring remediation:
1. **SSRF and Outbound Transport Bypasses**: The upstream server validation logic lacked DNS resolution checks, allowing domain names resolving to loopback or cloud metadata IPs (`127.0.0.1.nip.io`, `169.254.169.254.nip.io`) to bypass checks. Furthermore, alternate IP representations (decimal integers `2130706433`, hex `0x7f000001`, octal `0177.0.0.1`, shortened `127.1`, and IPv4-mapped IPv6 `::ffff:127.0.0.1`) bypassed standard IP address parser filters. Stdio transport allowed invoking shell interpreters (`bash`, `sh`, `powershell`, `cmd.exe`) and metacharacters.
2. **Timing Side-Channels and Wildcard CORS in Control API**: The Control API authenticated admin and tenant API tokens with standard Python string equality and dictionary lookups instead of constant-time comparisons (`secrets.compare_digest`), leaving credentials vulnerable to timing analysis. Wildcard CORS (`*`) with credentials enabled violated browser security policies.
3. **Control API Denial of Service & Missing Size Limits**: Unlike the Gateway, the Control API lacked request body size bounding middleware (`ContentLengthLimitMiddleware`), exposing endpoints to memory exhaustion attacks.
4. **Missing Workflow Cancellation & Resumption Race Conditions**: Workflow cancellation endpoint (`POST /workflows/{id}/cancel`) was absent from the Control API, and concurrent resume calls could simultaneously trigger executions on an already running workflow.
5. **Whitespace Idempotency Key Bypass**: In human-in-the-loop reconciliation decisions, non-empty whitespace strings in `idempotency_key` bypassed explicit risk acknowledgement requirements.
6. **Distributed Quota State Corruption on Outage**: Redis outages lacked configurable fail-closed policies, and concurrency permit releases lacked namespace separation, risking desynchronization between Redis and local in-memory permit counts.
7. **Identity Federation Readiness**: The platform lacked enterprise OIDC / OAuth2 token verification with cryptographic signature, algorithm, audience, issuer, expiration, and tenant claim enforcement.
8. **Container and Network Security**: Compose deployments published raw PostgreSQL and Redis ports directly to the host network and ran services as root without resource limits.

## Decisions

### 1. SSRF and Transport Security Hardening
- **Flexible IP Address Parser (`parse_flexible_ip`)**: Implemented parser handling standard IPv4/IPv6, integer, hexadecimal, octal, shortened, and IPv4-mapped IPv6 representations before checking security boundaries.
- **Strict Forbidden IP Classifications**: Cloud metadata IP addresses (`169.254.169.254`, `100.100.100.200`, `168.63.129.16`, `192.0.0.192`, `metadata.google.internal`) are unconditionally forbidden across all environments, including development. Carrier-grade NAT (CGNAT `100.64.0.0/10`), loopback, private, link-local, multicast, reserved, and unspecified addresses are strictly blocked in production.
- **DNS Resolution Boundary**: Hostnames that are domain names undergo DNS lookup via `socket.getaddrinfo()`. If any resolved address maps to a forbidden IP, registration is rejected with `ServerConfigValidationError`.
- **Stdio Shell Execution Restrictions**: Shell binaries (`sh`, `bash`, `zsh`, `csh`, `cmd.exe`, `powershell.exe`, `pwsh`) are strictly forbidden as stdio commands. The metacharacter filter was expanded to reject `[;&|`$<>()\n\r"'\\]`.

### 2. Authentication & Authorization Hardening
- **Constant-Time Secret Validation**: All API key comparisons across Gateway and Control API enforce `secrets.compare_digest`.
- **CORS Hardening**: Replaced wildcard CORS with explicit origins via `CORS_ALLOWED_ORIGINS`. In production mode, wildcard origins with credentials are explicitly prohibited.
- **Startup Security Validation**: Both Gateway and Control API run `validate_production_security_config()` during lifespan startup, refusing to boot in production with default, development, or weak credentials.

### 3. Production OAuth2 / OIDC Validator (`gateway/oidc.py`)
- **Cryptographic JWT Validation**: Validates RS256, ES256, and HS256 tokens using `PyJWT` and `cryptography`. Tokens with algorithm `none` or unsigned payloads are strictly rejected.
- **Standard Claim Enforcement**: Verifies `iss`, `aud`, `exp`, `nbf`, and `iat`.
- **Tenant Scope & Role Extraction**: Extracts caller tenant binding (`tenant_id`, `tid`, `org_id`) and maps administrative roles (`roles`, `groups`), guaranteeing zero-trust isolation without trusting arbitrary request parameters.
- **Key Rotation**: Built-in `JWKSCache` supporting dynamic key lookups via JWT `kid` headers.

### 4. Workflow Replay, Cancellation & Reconciliation Safety
- **Tenant-Isolated Workflow Cancellation**: Implemented `POST /workflows/{workflow_id}/cancel` with tenant ownership verification, state transition to `CANCELLED`, and audit/trace event publication.
- **Concurrency Guard on Resume**: Resuming a workflow in `RUNNING` status returns HTTP 409 Conflict. Resuming a `CANCELLED` workflow returns HTTP 400 Bad Request.
- **Reconciliation Safety**: Strips whitespace on `idempotency_key` and rejects whitespace-only keys when `risk_acknowledged=False`. Prevents conflicting decisions on terminal cases with HTTP 409 Conflict.

### 5. Distributed Quota Fail-Closed Resilience
- **Configurable Outage Policy**: Added `QUOTA_REDIS_FAILURE_POLICY` supporting `fail_closed` (strictly rejecting traffic with HTTP 429 when Redis is unreachable) or `degrade_local`.
- **Isolated Permit Namespaces**: Prefixes permits as `prm_redis_...` or `prm_local_...`. Releasing permits only modifies the designated store, preventing state desynchronization.

### 6. Data Protection & Payload Sanitization
- **Regex Redaction in String Fields**: Sanitizes bearer tokens (`Bearer [REDACTED]`) and AWS credentials (`[REDACTED_AWS_KEY]`) found in log traces, outputs, and checkpoints.
- **Sensitive Key Expansion**: Expanded redaction to include `client_secret`, `admin_key`, `id_token`, `refresh_token`, `cookie`, `session`, `x-api-key`, and private certificates.

### 7. Production Container Hardening (`docker-compose.prod.yml`)
- **Internal Tier Isolation**: PostgreSQL and Redis run on an isolated `internal-tier` network without published host ports.
- **Unprivileged Execution**: All application containers enforce non-root user (`user: "1000:1000"`) and `no-new-privileges:true`.
- **Resource Limits**: Configured CPU and memory limits on all services.
- **Credential Requirements**: Production compose configuration strictly requires real environment secrets (`POSTGRES_PASSWORD`, `REDIS_PASSWORD`, `GATEWAY_ADMIN_API_KEY`) with no fallback defaults.

## Verification
- **Automated Regression Suite**: 227 tests passing (205 baseline + 22 new Phase 7 tests).
- **Frontend Build**: Built successfully with TypeScript type checking in under 500ms.
- **Zero Regressions**: 100% of Phase 0–6 capabilities verified.

