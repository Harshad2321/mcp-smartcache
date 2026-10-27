"""
Security Boundary & Production Isolation Test Suite.

Validates:
1. Authentication & Role-Based Access Control (Admin vs Client vs Public).
2. Multi-Tenant Cache Isolation via X-Tenant-ID header.
3. Production Upstream Isolation: exclusion of mock servers in production.
4. Production Security Validation: refusing to start unauthenticated in production.
5. Request Size Limiting (DoS mitigation).
6. Input Validation (path traversal protection, regex validation, TTL caps).
"""

import json
import os
from unittest.mock import patch
import pytest
from httpx import AsyncClient, ASGITransport

from gateway.main import app
from gateway.auth import (
    validate_production_security_config,
    authenticate_token,
    Role,
)
from gateway.server import GatewayService
from gateway.registry import ServerConfig
from gateway.cache_policy import CachePolicy
from gateway.cache_store import RedisCacheStore
from mocks.repo_metadata import MockRepoMetadataServer


ADMIN_KEY = "test-admin-secret-key-32chars-long"
CLIENT_KEY = "test-client-secret-key-32chars-long"


@pytest.fixture
def auth_env():
    """Configure test authentication environment."""
    with patch.dict(
        os.environ,
        {
            "AUTH_ENABLED": "true",
            "ENVIRONMENT": "development",
            "ENABLE_DEV_MOCKS": "true",
            "GATEWAY_ADMIN_API_KEY": ADMIN_KEY,
            "GATEWAY_CLIENT_API_KEY": CLIENT_KEY,
        },
    ):
        yield


@pytest.fixture
async def secure_client(auth_env):
    """Async test client with in-process mock server configured."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Pre-register in-process mock server for testing
        import gateway.main as main_mod
        if main_mod.gateway_service is None:
            main_mod.gateway_service = GatewayService()
        mock_server = MockRepoMetadataServer()
        await main_mod.gateway_service.registry.register_server(
            ServerConfig(name="repo_metadata", transport="in-process", server_instance=mock_server)
        )
        yield client, mock_server


# ── 1. Authentication & RBAC Tests ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_health_endpoint_public_unauthenticated(secure_client):
    """Health check must be public without requiring authentication."""
    client, _ = secure_client
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"


@pytest.mark.asyncio
async def test_call_without_token_rejected_401(secure_client):
    """Unauthenticated call to POST /call must be rejected with 401."""
    client, _ = secure_client
    resp = await client.post(
        "/call",
        json={"tool": "repo_metadata__get_repo_info", "arguments": {"owner": "a", "repo": "b"}},
    )
    assert resp.status_code == 401
    assert "WWW-Authenticate" in resp.headers


@pytest.mark.asyncio
async def test_call_with_invalid_token_rejected_401(secure_client):
    """Call with invalid credentials must be rejected with 401."""
    client, _ = secure_client
    resp = await client.post(
        "/call",
        json={"tool": "repo_metadata__get_repo_info", "arguments": {"owner": "a", "repo": "b"}},
        headers={"Authorization": "Bearer completely-wrong-token"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_call_with_valid_client_token_accepted(secure_client):
    """Client with valid client token can invoke tools."""
    client, mock_server = secure_client
    resp = await client.post(
        "/call",
        json={"tool": "repo_metadata__get_repo_info", "arguments": {"owner": "alice", "repo": "proj"}},
        headers={"Authorization": f"Bearer {CLIENT_KEY}"},
    )
    assert resp.status_code == 200
    assert not resp.json()["is_error"]


@pytest.mark.asyncio
async def test_policy_endpoint_requires_admin_role(secure_client):
    """Client role cannot configure cache policies (403); Admin role can (200)."""
    client, _ = secure_client
    policy_body = {
        "tool_name": "repo_metadata__get_repo_info",
        "enabled": True,
        "read_only": True,
        "ttl_seconds": 120,
    }

    # Client role: rejected with 403 Forbidden
    resp_client = await client.post(
        "/cache/policy",
        json=policy_body,
        headers={"Authorization": f"Bearer {CLIENT_KEY}"},
    )
    assert resp_client.status_code == 403

    # Admin role: accepted with 200 OK
    resp_admin = await client.post(
        "/cache/policy",
        json=policy_body,
        headers={"Authorization": f"Bearer {ADMIN_KEY}"},
    )
    assert resp_admin.status_code == 200
    assert resp_admin.json()["status"] == "registered"


@pytest.mark.asyncio
async def test_metrics_requires_admin_role(secure_client):
    """Operational metrics must be protected and restricted to ADMIN."""
    client, _ = secure_client

    # Unauthenticated -> 401
    resp_anon = await client.get("/metrics")
    assert resp_anon.status_code == 401

    # Client token -> 403
    resp_client = await client.get("/metrics", headers={"Authorization": f"Bearer {CLIENT_KEY}"})
    assert resp_client.status_code == 403

    # Admin token -> 200
    resp_admin = await client.get("/metrics", headers={"Authorization": f"Bearer {ADMIN_KEY}"})
    assert resp_admin.status_code == 200
    assert "mcp_smartcache_" in resp_admin.text


@pytest.mark.asyncio
async def test_cache_invalidation_requires_admin_role(secure_client):
    """Invalidating cache requires ADMIN privileges."""
    client, _ = secure_client

    # Client role: 403 Forbidden
    resp_client = await client.delete(
        "/cache/invalidate/tool/repo_metadata__get_repo_info",
        headers={"Authorization": f"Bearer {CLIENT_KEY}"},
    )
    assert resp_client.status_code == 403

    # Admin role: accepted
    resp_admin = await client.delete(
        "/cache/invalidate/tool/repo_metadata__get_repo_info",
        headers={"Authorization": f"Bearer {ADMIN_KEY}"},
    )
    # 200 OK (or 503 if redis offline in unit test)
    assert resp_admin.status_code in (200, 503)


# ── 2. Multi-Tenant Cache Isolation ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_multi_tenant_cache_isolation_via_api(secure_client):
    """
    Tenant A and Tenant B issuing identical calls must NOT share cache entries.
    Tenant A's result must be isolated from Tenant B.
    """
    client, mock_server = secure_client
    import gateway.main as main_mod

    # Register caching policy
    main_mod.gateway_service.policy_registry.register(
        CachePolicy(
            tool_name="repo_metadata__get_repo_info",
            enabled=True,
            read_only=True,
            ttl_seconds=300,
        )
    )

    args = {"owner": "alice", "repo": "proj"}
    initial_calls = mock_server.call_counters["get_repo_info"]

    # 1. Tenant Alpha executes call (miss -> upstream called)
    resp_alpha1 = await client.post(
        "/call",
        json={"tool": "repo_metadata__get_repo_info", "arguments": args},
        headers={"Authorization": f"Bearer {CLIENT_KEY}", "X-Tenant-ID": "tenant-alpha"},
    )
    assert resp_alpha1.status_code == 200
    calls_after_alpha = mock_server.call_counters["get_repo_info"]
    assert calls_after_alpha == initial_calls + 1

    # 2. Tenant Beta executes identical call with different X-Tenant-ID
    # MUST NOT reuse Tenant Alpha's entry (must execute upstream!)
    resp_beta = await client.post(
        "/call",
        json={"tool": "repo_metadata__get_repo_info", "arguments": args},
        headers={"Authorization": f"Bearer {CLIENT_KEY}", "X-Tenant-ID": "tenant-beta"},
    )
    assert resp_beta.status_code == 200
    calls_after_beta = mock_server.call_counters["get_repo_info"]
    assert calls_after_beta == calls_after_alpha + 1, (
        "CRITICAL: Tenant Beta was served Tenant Alpha's cached data! Isolation breach."
    )


# ── 3. Production Upstream Isolation ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_production_environment_excludes_mock_servers():
    """
    When ENVIRONMENT=production and ENABLE_DEV_MOCKS is not set,
    mock servers must NEVER be registered automatically.
    """
    import gateway.main as main_mod

    with patch.dict(
        os.environ,
        {
            "ENVIRONMENT": "production",
            "ENABLE_DEV_MOCKS": "false",
            "GATEWAY_ADMIN_API_KEY": ADMIN_KEY,
            "GATEWAY_CLIENT_API_KEY": CLIENT_KEY,
        },
    ):
        # Create fresh service and run production upstream loader
        service = GatewayService()
        main_mod.gateway_service = service
        await main_mod.load_production_upstream_servers()

        registered_tools = service.registry.list_tools()
        registered_servers = service.registry.list_servers()

        # In production without config, no mock servers or tools are loaded
        assert len(registered_servers) == 0, "Mock servers were improperly loaded in production!"
        assert len(registered_tools) == 0


def test_production_fails_to_start_without_configured_keys():
    """
    In production mode, the application MUST fail to start if admin/client keys are missing.
    """
    # 1. Missing admin key
    with patch.dict(
        os.environ,
        {
            "ENVIRONMENT": "production",
            "AUTH_ENABLED": "true",
            "GATEWAY_ADMIN_API_KEY": "",
            "GATEWAY_CLIENT_API_KEY": "client-key-long-enough-32-chars",
        },
    ):
        with pytest.raises(RuntimeError, match="GATEWAY_ADMIN_API_KEY"):
            validate_production_security_config()

    # 2. Short / insecure client key
    with patch.dict(
        os.environ,
        {
            "ENVIRONMENT": "production",
            "AUTH_ENABLED": "true",
            "GATEWAY_ADMIN_API_KEY": "admin-key-long-enough-32-chars",
            "GATEWAY_CLIENT_API_KEY": "short",
        },
    ):
        with pytest.raises(RuntimeError, match="GATEWAY_CLIENT_API_KEY"):
            validate_production_security_config()

    # 3. AUTH_ENABLED=false in production
    with patch.dict(
        os.environ,
        {
            "ENVIRONMENT": "production",
            "AUTH_ENABLED": "false",
        },
    ):
        with pytest.raises(RuntimeError, match="AUTH_ENABLED cannot be false"):
            validate_production_security_config()


# ── 4. Input & Size Validation Tests ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_invalid_tool_name_rejected_422(secure_client):
    """Malformed tool name (e.g. path traversal or shell characters) must be rejected with 422."""
    client, _ = secure_client

    # Path traversal attempt
    resp1 = await client.post(
        "/call",
        json={"tool": "../../etc/passwd", "arguments": {}},
        headers={"Authorization": f"Bearer {CLIENT_KEY}"},
    )
    assert resp1.status_code == 422

    # Missing namespace separator
    resp2 = await client.post(
        "/call",
        json={"tool": "single_name_no_namespace", "arguments": {}},
        headers={"Authorization": f"Bearer {CLIENT_KEY}"},
    )
    assert resp2.status_code == 422


@pytest.mark.asyncio
async def test_invalid_ttl_rejected_422(secure_client):
    """Negative or excessively large TTL must be rejected with 422."""
    client, _ = secure_client

    # Negative TTL
    resp_neg = await client.post(
        "/cache/policy",
        json={"tool_name": "repo_metadata__get_repo_info", "ttl_seconds": -10},
        headers={"Authorization": f"Bearer {ADMIN_KEY}"},
    )
    assert resp_neg.status_code == 422

    # Excessively large TTL (> 30 days)
    resp_huge = await client.post(
        "/cache/policy",
        json={"tool_name": "repo_metadata__get_repo_info", "ttl_seconds": 99999999},
        headers={"Authorization": f"Bearer {ADMIN_KEY}"},
    )
    assert resp_huge.status_code == 422


@pytest.mark.asyncio
async def test_invalid_tenant_id_rejected_400(secure_client):
    """Tenant ID with invalid characters (e.g. injection attempt) must be rejected with 400."""
    client, _ = secure_client
    resp = await client.post(
        "/call",
        json={"tool": "repo_metadata__get_repo_info", "arguments": {"owner": "a", "repo": "b"}},
        headers={
            "Authorization": f"Bearer {CLIENT_KEY}",
            "X-Tenant-ID": "tenant/../../evil\x00",
        },
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_oversized_payload_rejected_413(secure_client):
    """Payloads exceeding MAX_REQUEST_BYTES must be rejected with 413."""
    client, _ = secure_client
    # Simulate oversized payload via Content-Length header
    resp = await client.post(
        "/call",
        content=b"{}",
        headers={
            "Authorization": f"Bearer {CLIENT_KEY}",
            "Content-Type": "application/json",
            "Content-Length": "2000000",  # 2MB > 1MB limit
        },
    )
    assert resp.status_code == 413
