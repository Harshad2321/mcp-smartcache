# ADR 0010: Production Deployment Rehearsal and Operational Acceptance

## Status
Accepted

## Date
2026-10-29

## Context
Phases 0 through 9 implemented core gateway orchestration, Redis response caching, LangGraph runtime execution, PostgreSQL durability, tenant policy governance, outbound SSRF transport hardening, and release gate verification. Phase 10 was executed as a production deployment rehearsal to independently verify release readiness, configuration validation modes, container build and topology integrity, persistence across container restarts, and operational runbook completeness.

## Decisions

### 1. Verification of the Dual-Network Topology
- Evaluated `docker-compose.prod.yml` and verified network segregation:
  - `internal-tier` is marked `internal: true`. Redis and PostgreSQL containers do not publish host ports and cannot initiate direct outbound public internet connections.
  - `ingress-tier` bridges only `gateway` (port 8000) and `control_api` (port 8080) to the edge reverse proxy.
  - Application containers execute as unprivileged user `1000:1000` with `no-new-privileges: true` and strict CPU/memory deploy limits.

### 2. Multi-Mode Configuration Enforcement
- Standardized `scripts/validate_production_config.py` with three distinct validation modes:
  - `--mode template`: Validates syntactic completeness of environment variable declarations in `.env.example`.
  - `--mode production`: Validates production configurations against strict secret entropy requirements, non-default passwords, explicit CORS domains (forbidding wildcard `*`), and complete OIDC parameters. Never prints raw secrets in logs or error messages.
  - `--mode runtime`: Validates active `os.environ` during application startup lifespan, aborting execution safely with `RuntimeError` before accepting network requests if security invariants are violated.

### 3. Concurrency Locking and Terminal-State Safeguards
- Locked workflow state transitions at the PostgreSQL row level using `SELECT ... FOR UPDATE` inside `StorageRepository.update_workflow_status()`.
- Added `expected_status` parameter verification to reject duplicate or conflicting status updates with HTTP 409 Conflict.
- Verified terminal-state protection: completed, cancelled, or failed workflows cannot transition back to running or pending states.
- Implemented atomic conditional task claiming (`claim_task`) using `WHERE status = 'pending'`, ensuring mutual exclusion across distributed workers.

### 4. Replay-Safe Crash Recovery
- Verified startup crash recovery (`recover_interrupted_workflows()`) across container restarts. Stranded running workflows are transitioned to `interrupted` without blindly replaying side-effecting tools. Blocked tasks require explicit operator reconciliation before execution can resume.

## Consequences
- Independent operational acceptance verified across 258 automated backend regression tests, live container health probes, Redis cache latency reduction, and PostgreSQL durability.
- System is certified for a controlled production deployment behind an authenticated edge reverse proxy.

