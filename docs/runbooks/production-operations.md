# MCP SmartCache — Production Operations Runbook

**Target Architecture:** MCP SmartCache Production Docker Compose Stack with TLS Ingress  
**Author:** Staff Platform, Security & Site Reliability Engineering  
**Version:** 1.2 (Phase 12 Release)

---

## 1. Starting and Stopping the Deployment

### Prerequisites
- Docker Engine >= 26.0 and Docker Compose >= 2.20
- Configured production environment file (`.env`) with non-default secrets validated via:
  ```bash
  python scripts/validate_production_config.py .env --mode production
  ```

### Starting the Stack
```bash
# 1. Validate configuration
python scripts/validate_production_config.py .env --mode production

# 2. Start all production services in detached mode (including TLS Ingress)
docker compose -f docker-compose.prod.yml --env-file .env up -d

# 3. Check service startup status
docker compose -f docker-compose.prod.yml ps
```

### Stopping the Stack
```bash
# Graceful shutdown (30-second timeout to allow active tasks to complete)
docker compose -f docker-compose.prod.yml down --timeout 30

# Emergency shutdown
docker compose -f docker-compose.prod.yml stop
```

---

## 2. Checking Service Readiness & Health Probes

MCP SmartCache exposes standard health probes on all application services:

| Service | Host Port / Interface | Internal Port | Endpoint | Expected Response |
| :--- | :--- | :--- | :--- | :--- |
| **Ingress (Public Edge)** | `443`, `80` (0.0.0.0) | `443`, `80` | `GET /health` | `{"status": "healthy", "service": "gateway"}` |
| **Gateway** | `127.0.0.1:8000` (Loopback only) | `8000` | `GET /health` | `{"status": "healthy", "service": "gateway"}` |
| **Control API** | `127.0.0.1:8080` (Loopback only) | `8080` | `GET /health` | `{"status": "healthy", "service": "control_api"}` |
| **Runtime** | *Not exposed to host* | `8002` (internal-tier) | `GET /health/ready` | `{"status": "ready", "database_ready": true}` |

### Automated Health Verification Probe
```bash
# Edge Ingress Probe (HTTPS)
curl -skf https://localhost/health || exit 1

# Local loopback probes (internal service verification)
curl -sf http://127.0.0.1:8000/health || exit 1
curl -sf http://127.0.0.1:8080/health || exit 1
```

---

## 3. Database Migration Procedures

Migrations are versioned, idempotent, and executed within an isolated PostgreSQL transaction.

### Pre-Deployment Migration
Execute migrations before routing live traffic to updated container images:
```bash
python -m storage.migrations.runner
```

### Verifying Applied Migrations
Connect to PostgreSQL and inspect `schema_migrations`:
```sql
SELECT version, name, applied_at FROM schema_migrations ORDER BY version ASC;
```

---

## 4. Secret Rotation Runbook

### Rotating Client API Keys & Tenant Mappings
1. Update `CLIENT_TENANT_KEYS` in `.env` with dual-key mappings (minimum 16 characters, no `dev-` or `test-` prefixes):
   ```env
   CLIENT_TENANT_KEYS={"prod-old-key-min-16-chars": "tenant_a", "prod-new-key-min-16-chars": "tenant_a"}
   ```
2. Reload Gateway without restarting the database:
   ```bash
   docker compose -f docker-compose.prod.yml up -d --no-deps gateway control_api
   ```
3. Update downstream client applications to send `prod-new-key-min-16-chars`.
4. Remove `prod-old-key-min-16-chars` from `CLIENT_TENANT_KEYS` and reload services.

### Rotating PostgreSQL Passwords
1. Issue `ALTER USER mcp_user WITH PASSWORD 'new-secure-password';` inside PostgreSQL.
2. Update `POSTGRES_PASSWORD` in `.env`.
3. Restart Gateway, Control API, and Runtime:
   ```bash
   docker compose -f docker-compose.prod.yml up -d --no-deps gateway control_api runtime
   ```

---

## 5. PostgreSQL Backup and Disaster Recovery

### Creating a Point-In-Time Backup
```bash
docker exec -t mcp-smartcache-postgres-prod pg_dump -U mcp_user -d mcp_smartcache -Fc > backup_$(date +%Y%m%d_%H%M%S).dump
```

### Restoring from Backup
```bash
# 1. Stop application services to prevent write conflicts
docker compose -f docker-compose.prod.yml stop gateway control_api runtime ingress

# 2. Restore database
cat backup_YYYYMMDD_HHMMSS.dump | docker exec -i mcp-smartcache-postgres-prod pg_restore -U mcp_user -d mcp_smartcache --clean --if-exists

# 3. Restart application services
docker compose -f docker-compose.prod.yml start gateway control_api runtime ingress
```

---

## 6. Recovering from Runtime & Worker Crashes

If the Agent Runtime crashes during workflow execution:
1. When Runtime starts, its lifespan context manager runs `repository.recover_interrupted_workflows()`.
2. Workflows stranded in `running` state are safely transitioned to `interrupted`.
3. Interrupted side-effecting tasks are **not** blindly replayed.
4. Operators inspect blocked tasks via Control API:
   ```bash
   curl -H "Authorization: Bearer <admin-key>" http://127.0.0.1:8080/reconciliations
   ```
5. Approve or skip tasks through:
   ```bash
   curl -X POST -H "Authorization: Bearer <admin-key>" \
     -H "Content-Type: application/json" \
     -d '{"action": "approve_retry", "risk_acknowledged": true, "idempotency_key": "<unique_key>", "decision_note": "Operator approved after manual review"}' \
     http://127.0.0.1:8080/reconciliations/<reconciliation_id>/decide
   ```

---

## 7. Recovering from Redis Outages

### Failure Modes:
- **`QUOTA_REDIS_FAILURE_POLICY=fail_closed` (Default):**
  If Redis is down, rate limiting, distributed leases, and caching fail closed, rejecting tool calls with HTTP 429/503 to prevent unconstrained upstream quota exhaustion.
- **`QUOTA_REDIS_FAILURE_POLICY=degrade_local`:**
  Gateway falls back to process-local sliding window limits and in-memory singleflight.

### Reconnection Procedure
Once Redis recovers, the `RedisCacheStore` and Pub/Sub listener reconnect automatically. Verify via:
```bash
curl -H "Authorization: Bearer <admin-key>" http://127.0.0.1:8000/metrics | grep mcp_smartcache_cache_read_errors
```

---

## 8. Rollback Procedures

If an application release must be rolled back:
```bash
# 1. Checkout previous stable release tag
git checkout <previous-release-tag>

# 2. Rebuild previous production images
docker compose -f docker-compose.prod.yml build

# 3. Restart stack with previous images
docker compose -f docker-compose.prod.yml up -d

# 4. Verify health probes
curl -skf https://localhost/health
curl -sf http://127.0.0.1:8000/health
curl -sf http://127.0.0.1:8080/health
```

---

## 9. Edge Ingress & Reverse Proxy Operations

### Network Isolation and Port Topology
Production deployment enforces a strict two-tier network architecture:
1. **`ingress-tier`**: Bridge network containing the public Nginx Ingress reverse proxy (`ports: 80, 443`), and internal proxies to Gateway and Control API.
2. **`internal-tier`**: Strict internal network (`internal: true`). Contains PostgreSQL, Redis, and LangGraph Runtime.
   - **Runtime (`runtime`)**: Has zero exposed host ports. Only accessible by Control API and Gateway over `internal-tier`.
   - **PostgreSQL & Redis**: Zero exposed host ports.
   - **Gateway & Control API**: Bound to loopback interface `127.0.0.1` for local administrator troubleshooting, but all external traffic must traverse Ingress.

### Trusted Proxy & Forwarded Header Hygiene
- Ingress strips client-supplied `X-Forwarded-For` and `X-Forwarded-Proto` headers, replacing them with `$remote_addr` and `$scheme`.
- Both Gateway and Control API run `TrustedProxyHostMiddleware`, which validates:
  1. The `Host` header against `ALLOWED_HOSTS` (dropping requests with 400 Bad Request if unauthorized).
  2. The immediate peer IP against `TRUSTED_PROXIES`. If an untrusted peer attempts to send spoofed `X-Forwarded-*` headers, the headers are flagged as forged and ignored.

### Certificate Renewal (Let's Encrypt / Enterprise PKI)
To replace or renew TLS certificates:
1. Place new PEM-formatted certificate and key in `deploy/nginx/certs/server.crt` and `deploy/nginx/certs/server.key`.
2. Reload Nginx without downtime:
   ```bash
   docker exec mcp-smartcache-ingress-prod nginx -s reload
   ```
3. Verify TLS expiration and cipher suite:
   ```bash
   openssl s_client -connect localhost:443 -servername mcp-smartcache.example.com
   ```

### 10-Point Ingress Integration Rehearsal
Verify edge ingress, authentication boundaries, and tenant isolation:
```bash
python scripts/test_ingress_integration.py
```
