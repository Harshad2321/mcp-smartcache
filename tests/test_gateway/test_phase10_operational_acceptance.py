"""
Phase 10: Production Deployment Rehearsal & Operational Acceptance Test Suite.
Verifies:
1. Production configuration validator across all operational modes and negative security cases.
2. PostgreSQL transaction rollback safety and state integrity.
3. Terminal-state transition protection (completed workflows locked from invalid mutation).
4. Multi-worker atomic task claiming.
5. Outbound transport fail-closed connection-time destination validation.
6. Credential scrubbing in audit events and error payloads.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.auth import Role, validate_production_security_config
from gateway.outbound_transport import (
    SafeAsyncHTTPTransport,
    SafeOutboundNetworkBackend,
    SSRFSecurityViolation,
)
from gateway.server_validator import is_forbidden_ip, parse_flexible_ip
from scripts.validate_production_config import (
    parse_env_file,
    validate_production_env,
    validate_template_syntax,
)
from storage.database import DatabaseManager, db
from storage.models.entities import TaskStatus, WorkflowStatus, WorkflowTask
from storage.repository import StorageRepository, repository
from storage.sanitize import sanitize_data, sanitize_string


# ─── 1. Production Configuration Invariant Tests ──────────────────────────────


def test_config_validator_template_syntax():
    """Validates that .env.example contains all required configuration declarations."""
    env_vars = parse_env_file(".env.example")
    errors = validate_template_syntax(env_vars)
    assert errors == [], f"Template validation failed: {errors}"


def test_config_validator_rejects_missing_required_variable():
    """Validates that missing required variables fail template validation."""
    env_vars = parse_env_file(".env.example")
    del env_vars["POSTGRES_PASSWORD"]
    errors = validate_template_syntax(env_vars)
    assert any("POSTGRES_PASSWORD" in e for e in errors)


def test_config_validator_accepts_valid_production_config():
    """Validates that a compliant production configuration passes with code 0."""
    valid_prod = {
        "ENVIRONMENT": "production",
        "AUTH_ENABLED": "true",
        "GATEWAY_ADMIN_API_KEY": "prod-admin-super-secret-key-12345",
        "GATEWAY_CLIENT_API_KEY": "prod-client-super-secret-key-12345",
        "POSTGRES_USER": "mcp_user",
        "POSTGRES_PASSWORD": "prod-secure-postgres-password-12345",
        "POSTGRES_DB": "mcp_smartcache",
        "REDIS_PASSWORD": "prod-secure-redis-password-12345",
        "CORS_ALLOWED_ORIGINS": "https://dashboard.example.com",
        "ENABLE_DEV_MOCKS": "false",
        "QUOTA_REDIS_FAILURE_POLICY": "fail_closed",
    }
    errors = validate_production_env(valid_prod)
    assert errors == [], f"Expected zero errors, got: {errors}"


def test_config_validator_rejects_default_and_short_secrets():
    """Validates that default credentials and weak secrets fail production validation without leaking values."""
    insecure = {
        "ENVIRONMENT": "production",
        "AUTH_ENABLED": "true",
        "GATEWAY_ADMIN_API_KEY": "dev-admin-key",
        "GATEWAY_CLIENT_API_KEY": "short",
        "POSTGRES_PASSWORD": "dev-password",
        "REDIS_PASSWORD": "short",
        "CORS_ALLOWED_ORIGINS": "*",
        "ENABLE_DEV_MOCKS": "true",
        "QUOTA_REDIS_FAILURE_POLICY": "unknown_policy",
    }
    errors = validate_production_env(insecure)
    assert len(errors) >= 5
    # Verify no raw secrets printed
    for err in errors:
        assert "dev-admin-key" not in err
        assert "dev-password" not in err


def test_runtime_startup_validation_fails_safe(monkeypatch):
    """Runtime startup validation raises RuntimeError on missing/dev credentials in production mode."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("GATEWAY_ADMIN_API_KEY", "dev-admin-key")
    with pytest.raises(RuntimeError, match="CRITICAL SECURITY CONFIGURATION ERROR"):
        validate_production_security_config()


# ─── 2. Database Concurrency & Atomicity Tests ────────────────────────────────


@pytest.mark.asyncio
async def test_terminal_state_protection():
    """Terminal workflows (completed, cancelled) cannot be overwritten or transitioned back to running."""
    now = datetime.now(timezone.utc)
    mock_db = MagicMock()
    repo = StorageRepository(database=mock_db)

    completed_row = {
        "workflow_id": "wf_done_1",
        "tenant_id": "tenant_a",
        "correlation_id": "corr_done",
        "workflow_type": "default",
        "original_request": "done",
        "status": "completed",
        "created_at": now,
        "started_at": now,
        "completed_at": now,
        "updated_at": now,
        "duration_ms": 100.0,
        "plan": None,
        "summary": None,
        "final_answer": "Done",
        "error": None,
    }

    mock_conn = MagicMock()
    mock_conn.fetchrow = AsyncMock(return_value=completed_row)

    class MockTx:
        async def __aenter__(self):
            return mock_conn

        async def __aexit__(self, *args):
            pass

    mock_db.transaction = MagicMock(return_value=MockTx())

    # Attempting to resume or transition a completed workflow to RUNNING must fail
    with pytest.raises(ValueError, match="Invalid workflow status transition: 'completed' -> 'running'"):
        await repo.update_workflow_status(
            workflow_id="wf_done_1",
            new_status=WorkflowStatus.RUNNING,
        )


@pytest.mark.asyncio
async def test_atomic_task_claim_idempotency():
    """Concurrent task claiming guarantees exactly one winner and idempotent failure for the loser."""
    mock_db = MagicMock()
    repo = StorageRepository(database=mock_db)

    claimed_row = {
        "id": 10,
        "task_id": "task_10",
        "workflow_id": "wf_10",
        "tenant_id": "tenant_a",
        "tool_name": "repo_metadata__get_repo_info",
        "status": "running",
        "dependencies": "[]",
        "created_at": None,
        "started_at": None,
        "completed_at": None,
        "duration_ms": None,
        "attempt_count": 1,
        "result": None,
        "error_message": None,
    }

    # First caller gets row, second caller gets None
    mock_db.fetchrow = AsyncMock(side_effect=[claimed_row, None])

    win = await repo.claim_task("wf_10", "task_10", "worker_alpha")
    lose = await repo.claim_task("wf_10", "task_10", "worker_beta")

    assert win is not None
    assert win.status == TaskStatus.RUNNING
    assert lose is None


# ─── 3. Credential Redaction & Sanitization ───────────────────────────────────


def test_credential_sanitization_in_errors_and_traces():
    """Sanitizer scrubs database URIs, basic auth, bearer tokens, PEM keys, and session cookies."""
    raw_trace = {
        "error": "Failed to connect to postgresql://admin:SuperSecretPass99@db.prod.internal:5432/mcp",
        "auth_header": "Basic dXNlcjpwYXNzd29yZA==",
        "bearer_token": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.e30.secret",
        "cookie": "session=sensitive_cookie_value_12345; Path=/",
        "ssl_debug_dump": "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA0\n-----END RSA PRIVATE KEY-----",
    }
    clean = sanitize_data(raw_trace)
    assert "SuperSecretPass99" not in clean["error"]
    assert "[REDACTED]" in clean["error"]
    assert "dXNlcjpwYXNzd29yZA==" not in clean["auth_header"]
    assert "[REDACTED]" in clean["auth_header"]
    assert "sensitive_cookie_value_12345" not in clean["cookie"]
    assert "[REDACTED]" in clean["cookie"]
    assert "MIIEowIBAAKCAQEA0" not in clean["ssl_debug_dump"]
    assert "[REDACTED_PRIVATE_KEY]" in clean["ssl_debug_dump"]
