# ADR 0012: Production Ingress, Reverse Proxy, and Authentication Boundary Hardening

## Status
Accepted

## Date
2026-10-31

## Context
Phase 11 completed with a **CONDITIONAL GO** determination. While internal durability, Redis caching, singleflight deduplication, and workflow safety invariants were established, the system lacked a verified production edge reverse proxy with TLS termination, private network port isolation, trusted proxy validation, and strict rejection of development secrets in production mode.

Specifically:
1. `DEFAULT_CLIENT_KEY` and `DEFAULT_ADMIN_KEY` were present as fallback credentials, presenting a residual risk that requests with development credentials might authenticate if production variables were omitted.
2. `docker-compose.prod.yml` published host ports for `runtime` (`8002:8002`), which bypassed network tier boundaries and exposed internal workflow execution machinery to the host.
3. The application lacked a reverse proxy layer with TLS termination (ports 80 and 443), client header sanitization (resetting `X-Forwarded-*` headers), and Host header validation to guard against DNS rebinding and HTTP host header injection attacks.

## Decisions

### 1. Strict Elimination of Development Secrets in Production Mode
- Hardened `gateway/auth.py` and `control_api/main.py`:
  - When `ENVIRONMENT=production`, any authentication attempt presenting a key in `KNOWN_DEV_SECRETS` or prefixed with `dev-` or `test-` is immediately rejected with HTTP 401 Unauthorized.
  - Startup lifespan validation (`validate_production_security_config()`) enforces that all keys in `GATEWAY_ADMIN_API_KEY`, `GATEWAY_CLIENT_API_KEY`, and `CLIENT_TENANT_KEYS` are at least 16 characters long and free of development placeholders. The services fail closed with `RuntimeError` before binding or accepting requests if this invariant is violated.
  - `control_api/main.py` no longer falls back to `DEFAULT_ADMIN_KEY` or `DEFAULT_CLIENT_KEY` when `ENVIRONMENT=production`.

### 2. Dual-Tier Private Network Isolation
- Hardened `docker-compose.prod.yml`:
  - **Runtime Isolation**: Removed published host ports (`8002:8002`) from `runtime`. The LangGraph runtime container now resides exclusively on `internal-tier` (`internal: true`) and can only be invoked by `control_api` and `gateway`.
  - **Database & Cache Isolation**: PostgreSQL and Redis containers reside exclusively on `internal-tier` with zero exposed host ports.
  - **Loopback Service Binding**: `gateway` and `control_api` host ports are bound strictly to `127.0.0.1` (`127.0.0.1:8000:8000` and `127.0.0.1:8080:8080`), preventing direct public exposure across external network interfaces.

### 3. Public Edge Reverse Proxy with TLS Termination
- Introduced an `ingress` service running `nginx:1.27-alpine` on `ingress-tier`:
  - Terminates TLS 1.2 and TLS 1.3 on port `443` with strict modern ciphers, session tickets disabled, and HTTP Strict Transport Security (`HSTS`).
  - Listens on port `80` with `/health` probe support and redirects all operational traffic to `https://$host$request_uri`.
  - Enforces default server block to drop any request with an unrecognized or malicious `Host` header with HTTP 400 Bad Request.
  - Routes external traffic securely: `/api/` to `control_api:8080`, `/call`, `/tools/`, and `/mcp/` to `gateway:8000`, and explicitly drops any external `/runtime/` requests with HTTP 403 Forbidden.

### 4. Trusted Proxy & Forwarded Header Hygiene
- Implemented `TrustedProxyHostMiddleware` in `gateway/ingress_middleware.py` and applied it across both Gateway and Control API services:
  - **Host Header Validation**: Rejects requests lacking a `Host` header or with hostnames not explicitly listed in `ALLOWED_HOSTS` with HTTP 400 Bad Request.
  - **Forwarded Header Sanitization**: Inspects immediate network peer IP against `TRUSTED_PROXIES`. Untrusted clients attempting to send spoofed `X-Forwarded-For` or `X-Forwarded-Proto` headers have their spoofed headers stripped and flagged as untrusted; only verified proxies on internal Docker bridges or loopback networks are permitted to set client IP and protocol metadata.
  - **CORS Hardening**: In production mode, Control API requires explicit allowed origins; wildcard origins (`*`) with credentials are strictly forbidden.

### 5. Automated 10-Point Ingress Integration Rehearsal
- Established `scripts/test_ingress_integration.py` covering:
  1. Valid authenticated requests (HTTP 200)
  2. Unauthenticated requests (HTTP 401)
  3. Tampered / invalid token rejection (HTTP 401)
  4. Cross-tenant isolation enforcement (HTTP 403)
  5. Direct backend port and development key rejection in production (HTTP 401)
  6. Forged forwarding header sanitization
  7. Unauthorized Host header rejection (HTTP 400)
  8. Internal-only endpoint restriction (HTTP 403 / 404)
  9. Public edge health probe (HTTP 200)
  10. End-to-end tool invocation and workflow submission

## Consequences
- The deployment boundary is sealed: only the TLS Ingress reverse proxy publishes public external ports (`80` and `443`).
- Development secrets can never authenticate requests in production environments.
- Private runtime, persistence, and caching layers are protected from external direct access.
- 266 automated backend tests pass, frontend builds cleanly, configuration validator succeeds, and all 10 ingress acceptance points are verified.

