# ADR 0008: Independent Production Verification, Reliability Engineering, and Release Readiness

## Status
Accepted

## Context
Following Phase 7, MCP SmartCache possessed robust application-level validation and policy governance. However, an independent security review identified key areas requiring hardening prior to an enterprise release:
1. **Time-of-Check / Time-of-Use (TOCTOU) DNS Rebinding Risk**: Upstream hostnames validated at registration could resolve to private network destinations or cloud metadata IP addresses at connection time.
2. **JWT Algorithm Confusion (CVE-2015-9235)**: If a server accepts both asymmetric (RS256) and symmetric (HS256) algorithms, an attacker could sign a forged token using HMAC with the server's public RSA key as the HMAC secret.
3. **Resource Disclosure Side-Channels**: Cross-tenant requests to certain policy endpoints returned HTTP 403 Forbidden instead of HTTP 404 Not Found, disclosing the existence of other tenants' private resources.
4. **Non-Atomic Status Transitions**: Workflow status transitions relied on an in-memory check followed by an update, which allowed concurrent worker processes or parallel resume API calls to race and potentially overwrite terminal states (`COMPLETED`, `CANCELLED`).
5. **Credential Leakage in Complex Formats**: Standard redaction did not cover database connection strings with embedded passwords (`postgresql://user:pass@host`), PEM-encoded private keys, HTTP Basic auth headers, or session cookies.
6. **Lack of Reproducible CI & Configuration Validation**: The repository lacked an automated continuous integration pipeline and a deterministic production configuration validation command.

## Decisions

### 1. Connection-Time SSRF & DNS Rebinding Elimination
- **Socket-Level Verification (`SafeOutboundNetworkBackend`)**: Subclassed `AnyIOBackend.connect_tcp()` to intercept all outbound TCP connections. 
  - Every resolved address is pre-validated with `is_forbidden_ip()`.
  - Once the TCP socket is connected, the kernel-level socket peer address is verified via `raw_socket.getpeername()`. If the actual connected IP belongs to loopback, private, link-local, or cloud metadata ranges, the socket is immediately terminated and raises `SSRFSecurityViolation`.
- **Redirect Interception (`SafeAsyncHTTPTransport`)**: Intercepts HTTP 3xx responses and re-validates the target `Location` header before allowing the HTTP client to follow redirects.
- **MCP Client Factory (`create_safe_mcp_http_client`)**: Configured as the standard `httpx_client_factory` across `sse_client` discovery and runtime router dispatch.

### 2. JWT Algorithm Confusion Attack Defense
- **Key-Type Separation**: Enforced strict validation preventing asymmetric public keys (PEM strings containing `BEGIN PUBLIC KEY` or cryptography `PublicKey` instances) from being used as HMAC secrets when verifying `HS256` tokens.
- **Fail-Safe Incomplete Configuration**: Enforces mandatory `issuer` and `audience` checks at the entry of token validation.

### 3. Non-Disclosing Authorization Boundary
- Standardized all tenant-scoped resource endpoints (`/workflows/{id}`, `/policies/{id}`, `/policies/{id}/activate`, `/policies/{id}/rollback`, `/reconciliations/{id}`) to return HTTP 404 Not Found when a caller attempts to access another tenant's resource, completely mitigating resource enumeration attacks.

### 4. Database-Level Atomic Workflow Status Locking
- Enhanced `StorageRepository.update_workflow_status()` to execute inside a PostgreSQL transaction with row-level locking:
  ```sql
  SELECT * FROM workflow_runs WHERE workflow_id = $1 FOR UPDATE
  ```
- Any concurrent worker or API call attempting to transition the workflow waits for the lock, reads the updated status, and is validated against `ALLOWED_WORKFLOW_TRANSITIONS`. Terminal states (`COMPLETED`, `CANCELLED`) can never be overwritten by concurrent workers.

### 5. Multi-Pattern Data Protection & Redaction
- Expanded `storage/sanitize.py` to recursively detect and redact:
  - Database connection strings (`postgresql://user:[REDACTED]@host`, `redis://:[REDACTED]@host`).
  - Multiline PEM private keys (`[REDACTED_PRIVATE_KEY]`).
  - HTTP Basic auth headers (`Basic [REDACTED]`).
  - Session and authentication cookies (`session=[REDACTED]`, `jwt=[REDACTED]`).

### 6. Reproducible CI and Configuration Validation
- Implemented `.github/workflows/ci.yml` running backend tests against live PostgreSQL and Redis services, executing adversarial security suites, validating migrations, and building the frontend.
- Created `scripts/validate_production_config.py` validating environment variables, minimum secret lengths, and CORS configurations.
- Created `.env.example` as a clean, sanitized environment configuration template.

## Verification
- **Automated Test Suite**: 235 passing tests (227 baseline + 8 Phase 8 adversarial security regression tests) with zero failures.
- **Frontend Validation**: TypeScript compilation (`tsc`) and Vite production bundle passed in ~500ms.
- **Migration & Compose Checks**: Migrations and `docker-compose.prod.yml` validated.

