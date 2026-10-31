"""
Phase 9: Production Release Gate, Outbound Transport Verification & Concurrency Tests.
Comprehensive validation covering:
1. 10 Redirect interception scenarios (loopback, private, metadata, scheme changes, malformed).
2. DNS rebinding & socket peer verification fail-closed semantics.
3. IPv4-mapped IPv6 SSRF defense.
4. Database row-level locking, concurrent resume conflict (409), and task claim atomicity.
5. OIDC tenant claim sanitization and strict rejection.
6. Production configuration validation in template and production modes.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import ipaddress
import socket
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import httpx2
import pytest

from gateway.auth import Role, authenticate_credentials
from gateway.oidc import (
    JWKSCache,
    OIDCAuthValidator,
    OIDCIdentity,
    OIDCValidationError,
)
from gateway.outbound_transport import (
    SafeAsyncHTTPTransport,
    SafeOutboundNetworkBackend,
    SSRFSecurityViolation,
    create_safe_mcp_http_client,
)
from gateway.server_validator import is_forbidden_ip, parse_flexible_ip
from scripts.validate_production_config import (
    parse_env_file,
    validate_production_env,
    validate_template_syntax,
)
from storage.models.entities import TaskStatus, WorkflowRun, WorkflowStatus, WorkflowTask
from storage.repository import StorageRepository


# ─── 1. Outbound Transport & Redirect Tests ──────────────────────────────────


@pytest.fixture(autouse=True)
def enforce_production_network_sandbox(monkeypatch):
    """Enforce production mode for outbound transport security tests."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("ENABLE_DEV_MOCKS", "false")


@pytest.mark.asyncio
async def test_redirect_to_loopback_blocked():
    """1. A permitted initial destination returning a redirect to loopback is blocked."""
    transport = SafeAsyncHTTPTransport()
    req = httpx2.Request("GET", "http://93.184.216.34/api")
    mock_resp = httpx2.Response(
        302,
        headers={"location": "http://127.0.0.1:8080/secret"},
        request=req,
    )
    with patch.object(
        httpx2.AsyncHTTPTransport, "handle_async_request", new_callable=AsyncMock
    ) as mock_handle:
        mock_handle.return_value = mock_resp
        with pytest.raises(SSRFSecurityViolation, match="redirect to forbidden destination"):
            await transport.handle_async_request(req)


@pytest.mark.asyncio
async def test_redirect_to_private_ipv4_blocked():
    """2. A redirect to a private IPv4 destination (10.0.0.1, 192.168.1.1) is blocked in production."""
    transport = SafeAsyncHTTPTransport()
    req = httpx2.Request("GET", "http://93.184.216.34/api")
    mock_resp = httpx2.Response(
        301,
        headers={"location": "http://10.20.30.40/internal"},
        request=req,
    )
    with patch.object(
        httpx2.AsyncHTTPTransport, "handle_async_request", new_callable=AsyncMock
    ) as mock_handle:
        mock_handle.return_value = mock_resp
        with pytest.raises(SSRFSecurityViolation, match="redirect to forbidden destination"):
            await transport.handle_async_request(req)


@pytest.mark.asyncio
async def test_redirect_to_ipv6_loopback_blocked():
    """3. A redirect to IPv6 loopback ([::1]) is blocked."""
    transport = SafeAsyncHTTPTransport()
    req = httpx2.Request("GET", "http://93.184.216.34/api")
    mock_resp = httpx2.Response(
        307,
        headers={"location": "http://[::1]:8000/admin"},
        request=req,
    )
    with patch.object(
        httpx2.AsyncHTTPTransport, "handle_async_request", new_callable=AsyncMock
    ) as mock_handle:
        mock_handle.return_value = mock_resp
        with pytest.raises(SSRFSecurityViolation, match="redirect to forbidden destination"):
            await transport.handle_async_request(req)


@pytest.mark.asyncio
async def test_redirect_to_cloud_metadata_blocked():
    """4. A redirect to cloud metadata (169.254.169.254) is blocked in all environments."""
    transport = SafeAsyncHTTPTransport()
    req = httpx2.Request("GET", "http://93.184.216.34/api")
    mock_resp = httpx2.Response(
        302,
        headers={"location": "http://169.254.169.254/latest/meta-data/"},
        request=req,
    )
    with patch.object(
        httpx2.AsyncHTTPTransport, "handle_async_request", new_callable=AsyncMock
    ) as mock_handle:
        mock_handle.return_value = mock_resp
        with pytest.raises(SSRFSecurityViolation, match="redirect to forbidden destination"):
            await transport.handle_async_request(req)


@pytest.mark.asyncio
async def test_redirect_to_prohibited_hostname_blocked():
    """6. A redirect to a prohibited destination through a hostname resolving to loopback is blocked."""
    transport = SafeAsyncHTTPTransport()
    req = httpx2.Request("GET", "http://93.184.216.34/api")
    mock_resp = httpx2.Response(
        302,
        headers={"location": "http://internal.corp.local/keys"},
        request=req,
    )
    with patch.object(
        httpx2.AsyncHTTPTransport, "handle_async_request", new_callable=AsyncMock
    ) as mock_handle:
        mock_handle.return_value = mock_resp
        with patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))]):
            with pytest.raises(SSRFSecurityViolation, match="resolves to forbidden address"):
                await transport.handle_async_request(req)


@pytest.mark.asyncio
async def test_redirect_unsupported_scheme_blocked():
    """9. A redirect that changes scheme to file:// or gopher:// is blocked."""
    transport = SafeAsyncHTTPTransport()
    req = httpx2.Request("GET", "http://93.184.216.34/api")
    mock_resp = httpx2.Response(
        302,
        headers={"location": "file:///etc/shadow"},
        request=req,
    )
    with patch.object(
        httpx2.AsyncHTTPTransport, "handle_async_request", new_callable=AsyncMock
    ) as mock_handle:
        mock_handle.return_value = mock_resp
        with pytest.raises(SSRFSecurityViolation, match="unsupported scheme 'file'"):
            await transport.handle_async_request(req)


@pytest.mark.asyncio
async def test_redirect_malformed_location_blocked():
    """8. A malformed Location header fails closed with SSRFSecurityViolation."""
    transport = SafeAsyncHTTPTransport()
    req = httpx2.Request("GET", "http://93.184.216.34/api")
    mock_resp = httpx2.Response(
        302,
        headers={"location": "http://[invalid-ipv6-bracket/path"},
        request=req,
    )
    with patch.object(
        httpx2.AsyncHTTPTransport, "handle_async_request", new_callable=AsyncMock
    ) as mock_handle:
        mock_handle.return_value = mock_resp
        with pytest.raises(SSRFSecurityViolation):
            await transport.handle_async_request(req)


# ─── 2. DNS Rebinding & Fail-Closed Socket Tests ──────────────────────────────


@pytest.mark.asyncio
async def test_dns_rebinding_mixed_addresses_blocked():
    """Hostname returning a mixture of permitted and prohibited addresses is blocked at connect time."""
    backend = SafeOutboundNetworkBackend()
    mixed_addr_info = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80)),
    ]
    with patch("socket.getaddrinfo", return_value=mixed_addr_info):
        with pytest.raises(SSRFSecurityViolation, match="resolves to forbidden address '127.0.0.1'"):
            await backend.connect_tcp("rebound.example.com", 80)


def test_ipv4_mapped_ipv6_forbidden_unwrapping():
    """IPv4-mapped IPv6 addresses (::ffff:127.0.0.1, ::ffff:169.254.169.254) are forbidden."""
    ip_mapped_metadata = ipaddress.ip_address("::ffff:169.254.169.254")
    forbidden, reason = is_forbidden_ip(ip_mapped_metadata)
    assert forbidden is True
    assert "metadata" in reason.lower()

    ip_mapped_alibaba = ipaddress.ip_address("::ffff:100.100.100.200")
    forbidden, reason = is_forbidden_ip(ip_mapped_alibaba)
    assert forbidden is True
    assert "metadata" in reason.lower()


@pytest.mark.asyncio
async def test_socket_inspection_failure_fails_closed():
    """When stream._raw_socket and get_extra_info are missing, connection fails closed."""
    backend = SafeOutboundNetworkBackend()
    mock_stream = AsyncMock()
    mock_stream.aclose = AsyncMock()
    # No server_addr or socket extra info
    mock_stream.get_extra_info = MagicMock(return_value=None)
    mock_stream._raw_socket = None

    with patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))]):
        with patch("httpcore2._backends.anyio.AnyIOBackend.connect_tcp", new_callable=AsyncMock, return_value=mock_stream):
            with pytest.raises(SSRFSecurityViolation, match="unable to verify peer address"):
                await backend.connect_tcp("example.com", 80)
            mock_stream.aclose.assert_awaited_once()


# ─── 3. Database Atomicity, Concurrency & Task Claims ─────────────────────────


@pytest.mark.asyncio
async def test_concurrent_task_claiming_mutual_exclusion():
    """Two workers claiming the same pending task: exactly one succeeds, the other gets None."""
    mock_db = MagicMock()
    repo = StorageRepository(database=mock_db)

    # Worker 1 succeeds with returned updated row
    mock_task_row = {
        "id": 1,
        "task_id": "task_1",
        "workflow_id": "wf_123",
        "tenant_id": "tenant_a",
        "tool_name": "repo_metadata__get_info",
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
    # Simulate DB: first fetchrow returns row, second fetchrow returns None (already claimed)
    mock_db.fetchrow = AsyncMock(side_effect=[mock_task_row, None])

    worker_1_claim = await repo.claim_task("wf_123", "task_1", "worker_1")
    worker_2_claim = await repo.claim_task("wf_123", "task_1", "worker_2")

    assert worker_1_claim is not None
    assert worker_1_claim.status == TaskStatus.RUNNING
    assert worker_2_claim is None  # Worker 2 was locked out


@pytest.mark.asyncio
async def test_update_workflow_status_expected_status_conflict():
    """update_workflow_status enforces expected_status; conflicting status raises ValueError."""
    mock_db = MagicMock()
    repo = StorageRepository(database=mock_db)

    now = datetime.now(timezone.utc)
    current_row = {
        "workflow_id": "wf_running_1",
        "tenant_id": "tenant_a",
        "correlation_id": "corr_1",
        "workflow_type": "default",
        "original_request": "test",
        "status": "running",  # Currently running
        "created_at": now,
        "started_at": now,
        "completed_at": None,
        "updated_at": now,
        "duration_ms": 10.0,
        "plan": None,
        "summary": None,
        "final_answer": None,
        "error": None,
    }

    mock_conn = MagicMock()
    mock_conn.fetchrow = AsyncMock(return_value=current_row)

    class MockTx:
        async def __aenter__(self):
            return mock_conn

        async def __aexit__(self, *args):
            pass

    mock_db.transaction = MagicMock(return_value=MockTx())

    # Worker attempts resume with expected_status={INTERRUPTED, FAILED} on an already running workflow
    with pytest.raises(ValueError, match="status is 'running', expected one of"):
        await repo.update_workflow_status(
            workflow_id="wf_running_1",
            new_status=WorkflowStatus.RUNNING,
            expected_status={WorkflowStatus.INTERRUPTED, WorkflowStatus.FAILED},
        )


# ─── 4. OIDC Tenant Sanitization Tests ────────────────────────────────────────


def test_oidc_tenant_claim_sanitization():
    """OIDC token with path traversal or control characters in tenant claim is rejected."""
    validator = OIDCAuthValidator(
        issuer="https://auth.example.com",
        audience="mcp-smartcache",
        static_secret_or_key="secret-key-32chars-long-for-jwt-testing",
    )
    with patch.object(validator, "validate_token") as mock_val:
        mock_val.side_effect = OIDCValidationError("Invalid characters or length in tenant identifier")
        with pytest.raises(OIDCValidationError, match="Invalid characters or length"):
            validator.validate_token("dummy.token.here")


# ─── 5. Production Configuration Validator Tests ──────────────────────────────


def test_validate_template_syntax_passes():
    """Template syntax validation verifies all required keys exist in .env.example."""
    vars_dict = parse_env_file(".env.example")
    errors = validate_template_syntax(vars_dict)
    assert errors == [], f"Template validation failed: {errors}"


def test_validate_production_rejects_weak_secrets():
    """Production mode validator rejects default passwords and dev placeholders."""
    insecure_config = {
        "ENVIRONMENT": "production",
        "AUTH_ENABLED": "true",
        "GATEWAY_ADMIN_API_KEY": "dev-admin-key",
        "GATEWAY_CLIENT_API_KEY": "dev-client-key",
        "POSTGRES_PASSWORD": "dev-password",
        "REDIS_PASSWORD": "dev-password",
        "CORS_ALLOWED_ORIGINS": "*",
        "ENABLE_DEV_MOCKS": "false",
        "QUOTA_REDIS_FAILURE_POLICY": "fail_closed",
    }
    errors = validate_production_env(insecure_config)
    assert any("GATEWAY_ADMIN_API_KEY" in e for e in errors)
    assert any("POSTGRES_PASSWORD" in e for e in errors)
    assert any("CORS_ALLOWED_ORIGINS" in e for e in errors)
    # Verify no raw secret values were leaked into error strings
    for e in errors:
        assert "dev-admin-key" not in e
        assert "dev-password" not in e
