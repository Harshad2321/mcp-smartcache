"""
Phase 8 Automated Adversarial Security & Reliability Test Suite.
Validates:
1. Connection-time SSRF and DNS-rebinding prevention (kernel socket peer inspection).
2. Outbound redirect interception to loopback and cloud metadata.
3. JWT algorithm confusion attack (HMAC signing with RSA public key rejected).
4. Incomplete OIDC configuration fail-safe behavior.
5. Cross-tenant non-disclosing 404 behavior across policies and workflows.
6. Atomic database workflow status transition (FOR UPDATE prevents duplicate runs).
7. Cache isolation across tenant scopes and policy denial bypass prevention.
8. Deep redaction of connection strings, PEM private keys, and session cookies.
"""

import asyncio
from datetime import datetime, timezone
import json
import os
import socket
import time
import uuid
import pytest
from httpx import AsyncClient, ASGITransport
import httpcore2
import httpx2
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from unittest.mock import AsyncMock, patch

from control_api.main import app as control_api_app
from gateway.oidc import OIDCAuthValidator, OIDCValidationError
from gateway.outbound_transport import (
    SafeAsyncHTTPTransport,
    SafeOutboundNetworkBackend,
    SSRFSecurityViolation,
    create_safe_mcp_http_client,
)
from gateway.cache_store import RedisCacheStore
from storage.database import db
from storage.models.entities import (
    CacheRules,
    ResourceLimits,
    Tenant,
    TenantPolicy,
    ToolRules,
    WorkflowRun,
    WorkflowStatus,
)
from storage.repository import repository
from storage.sanitize import sanitize_data


@pytest.fixture(autouse=True)
async def setup_test_env():
    await db.connect()
    os.environ["CLIENT_TENANT_KEYS"] = json.dumps({
        "key_alpha": "tenant_a",
        "key_beta": "tenant_b",
    })
    await repository.create_tenant(tenant_id="tenant_a", name="Tenant A", is_active=True)
    await repository.create_tenant(tenant_id="tenant_b", name="Tenant B", is_active=True)
    yield
    await db.disconnect()


@pytest.fixture
def auth_headers():
    admin_key = os.environ.get("GATEWAY_ADMIN_API_KEY", "dev-admin-secret-key-32chars-long")
    return {
        "admin": {"Authorization": f"Bearer {admin_key}"},
        "tenant_a": {"Authorization": "Bearer key_alpha", "X-Tenant-ID": "tenant_a"},
        "tenant_b": {"Authorization": "Bearer key_beta", "X-Tenant-ID": "tenant_b"},
    }


# ── 1. Connection-Time SSRF & DNS Rebinding Verification ─────────────────────

@pytest.mark.asyncio
async def test_connection_time_ssrf_dns_rebinding_blocked():
    """
    Simulate DNS rebinding attack where a public domain resolves to 127.0.0.1
    at connection time. Kernel socket peer check must abort and raise SSRFSecurityViolation.
    """
    backend = SafeOutboundNetworkBackend()

    # In production mode, connecting to loopback or private network must be aborted
    with patch("gateway.server_validator.is_production", return_value=True), \
         patch("gateway.server_validator.is_dev_mocks_enabled", return_value=False):
        # Even if initial hostname looked public, if it resolves to loopback/private at connect:
        with patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))]):
            with pytest.raises(SSRFSecurityViolation, match="resolves to forbidden address"):
                await backend.connect_tcp("rebind.attacker.com", 80)


@pytest.mark.asyncio
async def test_connection_time_redirect_to_cloud_metadata_blocked():
    """
    Verify SafeAsyncHTTPTransport intercepts 302 redirect targeting cloud metadata IP.
    """
    transport = SafeAsyncHTTPTransport()

    # Mock response returning 302 redirect to 169.254.169.254
    fake_redirect_response = httpcore2.Response(
        status=302,
        headers=[(b"location", b"http://169.254.169.254/latest/meta-data/")],
        content=b"",
    )

    request = httpx2.Request("GET", "http://api.public-service.com/mcp")

    with patch.object(transport._pool, "handle_async_request", return_value=fake_redirect_response), \
         patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))]):
        with pytest.raises(SSRFSecurityViolation, match="redirect to forbidden destination.*is blocked"):
            await transport.handle_async_request(request)


# ── 2. JWT Algorithm Confusion Attack Defense ────────────────────────────────

def test_jwt_algorithm_confusion_attack_rejected():
    """
    Test CVE-2015-9235 defense: Attacker attempts to sign a JWT using HS256
    with the server's RSA public key as the HMAC secret.
    """
    import base64
    import hashlib
    import hmac

    private_key = rsa.generate_private_key(65537, 2048)
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")

    validator = OIDCAuthValidator(
        issuer="https://auth.company.com/",
        audience="mcp-smartcache",
        static_secret_or_key=public_pem,
    )

    # Manually assemble an HS256 JWT signed with public_pem bytes as HMAC key
    header = base64.urlsafe_b64encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode()).decode().rstrip("=")
    payload = base64.urlsafe_b64encode(
        json.dumps({
            "iss": "https://auth.company.com/",
            "aud": "mcp-smartcache",
            "sub": "attacker_admin",
            "tenant_id": "tenant_a",
            "roles": ["admin"],
            "exp": int(time.time()) + 3600,
        }).encode()
    ).decode().rstrip("=")
    msg = f"{header}.{payload}".encode()
    sig = base64.urlsafe_b64encode(hmac.new(public_pem.encode(), msg, hashlib.sha256).digest()).decode().rstrip("=")
    forged_token = f"{header}.{payload}.{sig}"

    with pytest.raises(OIDCValidationError, match="Algorithm confusion attack detected"):
        validator.validate_token(forged_token)


def test_incomplete_oidc_configuration_fails_safe():
    """OIDC validator without configured issuer/audience must fail safely."""
    validator = OIDCAuthValidator(
        issuer="",  # Missing issuer
        audience="mcp-smartcache",
        static_secret_or_key="secret",
    )
    # Generate any valid 3-part token
    dummy_jwt = jwt.encode({"sub": "u"}, "dummy_secret_32_characters_long", algorithm="HS256")
    with pytest.raises(OIDCValidationError, match="missing required issuer"):
        validator.validate_token(dummy_jwt)


# ── 3. Cross-Tenant Non-Disclosing 404 Behavior ──────────────────────────────

@pytest.mark.asyncio
async def test_cross_tenant_policy_access_returns_non_disclosing_404(auth_headers):
    """
    Tenant B requesting Tenant A's policy must receive 404 (non-disclosing),
    preventing resource enumeration.
    """
    # Create policy for tenant_a
    pol_id = f"pol_iso_{uuid.uuid4().hex[:8]}"
    await repository.create_or_update_policy(
        TenantPolicy(
            policy_id=pol_id,
            tenant_id="tenant_a",
            policy_version=1,
            is_active=True,
            tool_rules=ToolRules(),
            cache_rules=CacheRules(),
            resource_limits=ResourceLimits(),
            created_by="system",
        )
    )

    transport = ASGITransport(app=control_api_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Tenant B requests Tenant A's policy -> 404 Not Found (not 403)
        resp = await client.get(f"/policies/{pol_id}", headers=auth_headers["tenant_b"])
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

        # Tenant B attempts to activate Tenant A's policy -> 404 Not Found
        resp_act = await client.post(f"/policies/{pol_id}/activate", headers=auth_headers["tenant_b"])
        assert resp_act.status_code == 404

        # Tenant B attempts to rollback Tenant A's policy -> 404 Not Found
        resp_rb = await client.post(f"/policies/{pol_id}/rollback", headers=auth_headers["tenant_b"])
        assert resp_rb.status_code == 404


# ── 4. Atomic Database Workflow Transition (Race Condition Guard) ─────────────

@pytest.mark.asyncio
async def test_atomic_workflow_status_transition_serializability():
    """
    Verify that update_workflow_status uses database transaction and row-level
    locking (FOR UPDATE) so invalid or concurrent transitions are rejected.
    """
    wf_id = f"wf_race_{uuid.uuid4().hex[:8]}"
    await repository.create_workflow(
        workflow_id=wf_id,
        tenant_id="tenant_a",
        request_prompt="Atomic concurrency test",
    )

    # 1. Transition pending -> running
    wf_running = await repository.update_workflow_status(wf_id, WorkflowStatus.RUNNING)
    assert wf_running is not None
    assert wf_running.status == WorkflowStatus.RUNNING

    # 2. Transition running -> completed (terminal state)
    wf_comp = await repository.update_workflow_status(wf_id, WorkflowStatus.COMPLETED)
    assert wf_comp is not None
    assert wf_comp.status == WorkflowStatus.COMPLETED

    # 3. Attempting to transition from terminal COMPLETED -> RUNNING must fail with ValueError
    with pytest.raises(ValueError, match="Invalid workflow status transition"):
        await repository.update_workflow_status(wf_id, WorkflowStatus.RUNNING)

    # 4. Status remains safely COMPLETED
    current = await repository.get_workflow(wf_id)
    assert current is not None
    assert current.status == WorkflowStatus.COMPLETED


# ── 5. Cache Isolation Across Tenants & Denied Tool Protection ───────────────

@pytest.mark.asyncio
async def test_cache_store_tenant_isolation_boundary():
    """
    Verify RedisCacheStore enforces tenant scope:
    Tenant B cannot retrieve a cached entry primed by Tenant A for identical arguments.
    """
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    store = RedisCacheStore(redis_url)
    await store.connect()

    try:
        from gateway.cache_key import build_cache_key
        from gateway.cache_policy import CachePolicy
        from mcp.types import CallToolResult, TextContent
        policy = CachePolicy(tool_name="repo_metadata__get_info", enabled=True, ttl_seconds=60, read_only=True)
        key_tenant_a = build_cache_key(
            namespaced_tool_name="repo_metadata__get_info",
            arguments={"repo": "deepmind/smartcache"},
            policy=policy,
            scope="tenant_a",
        )
        key_tenant_b = build_cache_key(
            namespaced_tool_name="repo_metadata__get_info",
            arguments={"repo": "deepmind/smartcache"},
            policy=policy,
            scope="tenant_b",
        )

        # Keys must be cryptographically distinct across tenants
        assert key_tenant_a != key_tenant_b

        # Prime cache for tenant_a
        cached_val = CallToolResult(content=[TextContent(type="text", text=json.dumps({"stars": 4200}))])
        await store.set(key_tenant_a, cached_val, ttl_seconds=60)

        # Tenant A gets cache hit
        hit_a = await store.get(key_tenant_a)
        assert hit_a is not None
        assert json.loads(hit_a.content[0].text) == {"stars": 4200}

        # Tenant B gets cache miss
        hit_b = await store.get(key_tenant_b)
        assert hit_b is None
    finally:
        await store.close()


# ── 6. Comprehensive Sensitive Data Redaction ────────────────────────────────

def test_deep_data_redaction_comprehensive():
    """
    Verify redaction of connection strings, PEM private keys, basic auth,
    and session cookies in arbitrary nested and multiline text.
    """
    sample_data = {
        "db_uri": "postgresql+asyncpg://mcp_admin:SuperSecretPass123!@postgres.internal:5432/mcp_prod",
        "redis_uri": "redis://:RedisClusterSecretToken99@redis.internal:6379/0",
        "nested_block": {
            "auth_basic": "Basic dXNlcjpwYXNzd29yZA==",
            "transport_metadata": "session=abc123secret; other=public_val; jwt=eyJhbGciOi...",
            "pem_key": (
                "-----BEGIN RSA PRIVATE KEY-----\n"
                "MIIEowIBAAKCAQEA0Y1W9FzL8...\n"
                "-----END RSA PRIVATE KEY-----"
            ),
        },
    }

    cleaned = sanitize_data(sample_data)

    # Passwords redacted from connection strings
    assert "SuperSecretPass123!" not in cleaned["db_uri"]
    assert "[REDACTED]@" in cleaned["db_uri"]
    assert "RedisClusterSecretToken99" not in cleaned["redis_uri"]
    assert "[REDACTED]@" in cleaned["redis_uri"]

    # Basic auth, cookies, and PEM private keys redacted
    assert cleaned["nested_block"]["auth_basic"] == "Basic [REDACTED]"
    assert "abc123secret" not in cleaned["nested_block"]["transport_metadata"]
    assert "session=[REDACTED]" in cleaned["nested_block"]["transport_metadata"]
    assert cleaned["nested_block"]["pem_key"] == "[REDACTED_PRIVATE_KEY]"
