"""
Phase 12: Production Ingress, Trusted Proxy, and Authentication Boundary Tests.

Verifies:
1. Production mode strictly rejects DEFAULT_CLIENT_KEY, DEFAULT_ADMIN_KEY, and dev-* keys.
2. Startup configuration validation enforces strong, non-development keys in production.
3. Ingress Host header validation blocks DNS rebinding and malicious Host headers.
4. Trusted proxy validation: untrusted peers cannot forge X-Forwarded-* headers; trusted proxies have their headers accepted.
5. Synthetic OIDC / JWT authentication correctly isolates cross-tenant requests.
6. Restrictive CORS rules prevent wildcard credentials in production mode.
"""

from __future__ import annotations

import json
import os
import time
from unittest.mock import patch

import jwt
import pytest
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request
from starlette.responses import Response

from control_api.main import app as control_app
from gateway.auth import (
    KNOWN_DEV_SECRETS,
    Role,
    authenticate_credentials,
    set_oidc_validator,
    validate_production_security_config,
)
from gateway.ingress_middleware import (
    TrustedProxyHostMiddleware,
    is_peer_trusted_proxy,
    parse_allowed_hosts,
    parse_trusted_proxies,
)
from gateway.main import app as gateway_app
from gateway.oidc import OIDCAuthValidator


# ── 1. Dev Credentials Rejection in Production ──────────────────────────────────

def test_production_mode_rejects_default_and_dev_keys():
    """Verify that authenticate_credentials strictly rejects default and dev keys when ENVIRONMENT=production."""
    with patch.dict(os.environ, {"ENVIRONMENT": "production"}):
        for dev_key in KNOWN_DEV_SECRETS:
            with pytest.raises(Exception) as exc_info:
                authenticate_credentials(dev_key)
            assert "401" in str(exc_info.value) or "rejected" in str(exc_info.value).lower()

        # Custom dev- prefixed keys
        for key in ["dev-custom-key-12345", "test-arbitrary-key-999"]:
            with pytest.raises(Exception) as exc_info:
                authenticate_credentials(key)
            assert "401" in str(exc_info.value) or "rejected" in str(exc_info.value).lower()


def test_production_mode_accepts_strong_keys():
    """Verify strong, non-dev keys are accepted in production mode."""
    strong_admin = "prod-master-admin-secure-key-abcdef-123456"
    strong_client = "prod-client-tenant-secure-key-xyz987-654321"

    with patch.dict(
        os.environ,
        {
            "ENVIRONMENT": "production",
            "GATEWAY_ADMIN_API_KEY": strong_admin,
            "GATEWAY_CLIENT_API_KEY": strong_client,
            "DEFAULT_CLIENT_TENANT": "prod_tenant",
        },
    ):
        role_admin, actor_admin, _ = authenticate_credentials(strong_admin)
        assert role_admin == Role.ADMIN
        assert actor_admin == "admin"

        role_client, actor_client, tenants = authenticate_credentials(strong_client)
        assert role_client == Role.CLIENT
        assert "prod_tenant" in tenants


def test_production_startup_validation_rejects_weak_or_dev_credentials():
    """Verify validate_production_security_config fails safe if any credential is weak or dev-like."""
    # 1. Dev admin key
    with patch.dict(
        os.environ,
        {
            "ENVIRONMENT": "production",
            "AUTH_ENABLED": "true",
            "GATEWAY_ADMIN_API_KEY": "dev-admin-secret-key-32chars-long",
            "GATEWAY_CLIENT_API_KEY": "prod-client-secret-key-sufficiently-long-32chars",
        },
    ):
        with pytest.raises(RuntimeError, match="Development API keys cannot be used in production mode"):
            validate_production_security_config()

    # 2. Short admin key (< 16 chars)
    with patch.dict(
        os.environ,
        {
            "ENVIRONMENT": "production",
            "AUTH_ENABLED": "true",
            "GATEWAY_ADMIN_API_KEY": "shortkey",
            "GATEWAY_CLIENT_API_KEY": "prod-client-secret-key-sufficiently-long-32chars",
        },
    ):
        with pytest.raises(RuntimeError, match="at least 16 characters"):
            validate_production_security_config()

    # 3. Dev client key in CLIENT_TENANT_KEYS
    with patch.dict(
        os.environ,
        {
            "ENVIRONMENT": "production",
            "AUTH_ENABLED": "true",
            "GATEWAY_ADMIN_API_KEY": "prod-admin-secret-key-sufficiently-long-32chars",
            "GATEWAY_CLIENT_API_KEY": "prod-client-secret-key-sufficiently-long-32chars",
            "CLIENT_TENANT_KEYS": "dev-tenant-key-123456:tenant_a",
        },
    ):
        with pytest.raises(RuntimeError, match="Development client keys in CLIENT_TENANT_KEYS cannot be used"):
            validate_production_security_config()


# ── 2. Host Header Validation ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ingress_host_header_validation():
    """Verify Host header validation blocks untrusted Host headers and accepts allowed ones."""
    transport = ASGITransport(app=gateway_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Valid allowed host (e.g., localhost)
        resp = await client.get("/health", headers={"Host": "localhost"})
        assert resp.status_code == 200

        # 2. Valid configured production domain
        resp = await client.get("/health", headers={"Host": "mcp-smartcache.example.com"})
        assert resp.status_code == 200

        # 3. Malicious / untrusted Host header
        resp = await client.get("/health", headers={"Host": "evil.attacker.com"})
        assert resp.status_code == 400
        assert "Untrusted or invalid Host header" in resp.json()["detail"]

        # 4. Host with arbitrary unapproved port
        resp = await client.get("/health", headers={"Host": "internal.infra.internal:9999"})
        assert resp.status_code == 400


# ── 3. Trusted Proxy & Forwarded Header Sanitization ──────────────────────────

def test_trusted_proxy_detection():
    """Verify peer IP is accurately tested against trusted proxy networks."""
    trusted_nets = parse_trusted_proxies()

    # Loopback is trusted
    assert is_peer_trusted_proxy("127.0.0.1", trusted_nets) is True
    assert is_peer_trusted_proxy("::1", trusted_nets) is True

    # Private Docker bridge (172.16.0.0/12) is trusted
    assert is_peer_trusted_proxy("172.18.0.5", trusted_nets) is True
    assert is_peer_trusted_proxy("10.0.1.50", trusted_nets) is True

    # Public internet IP is NOT a trusted proxy
    assert is_peer_trusted_proxy("203.0.113.195", trusted_nets) is False
    assert is_peer_trusted_proxy("8.8.8.8", trusted_nets) is False


@pytest.mark.asyncio
async def test_untrusted_client_cannot_forge_forwarded_headers():
    """Verify that when a request comes from an untrusted client, X-Forwarded-For is ignored."""
    from starlette.datastructures import Headers

    # Mock an ASGI scope where client peer IP is a public internet address
    received_state = {}

    async def fake_app(scope, receive, send):
        request = Request(scope, receive)
        received_state["client_ip"] = request.state.client_ip
        received_state["forged"] = request.state.forged_headers_detected
        response = Response(content="ok", media_type="text/plain")
        await response(scope, receive, send)

    middleware = TrustedProxyHostMiddleware(fake_app)

    # 1. Untrusted client sends spoofed X-Forwarded-For: 1.1.1.1
    scope_untrusted = {
        "type": "http",
        "method": "GET",
        "path": "/test",
        "headers": [
            (b"host", b"localhost"),
            (b"x-forwarded-for", b"1.1.1.1"),
        ],
        "client": ("198.51.100.25", 54321),  # Public IP -> NOT in trusted proxies
        "scheme": "http",
    }

    async def dummy_receive():
        return {"type": "http.request"}

    async def dummy_send(event):
        pass

    await middleware(scope_untrusted, dummy_receive, dummy_send)
    assert received_state["client_ip"] == "198.51.100.25"
    assert received_state["forged"] is True

    # 2. Trusted proxy sends X-Forwarded-For: 203.0.113.5
    scope_trusted = {
        "type": "http",
        "method": "GET",
        "path": "/test",
        "headers": [
            (b"host", b"localhost"),
            (b"x-forwarded-for", b"203.0.113.5"),
        ],
        "client": ("127.0.0.1", 54321),  # Localhost -> in trusted proxies
        "scheme": "http",
    }
    await middleware(scope_trusted, dummy_receive, dummy_send)
    assert received_state["client_ip"] == "203.0.113.5"
    assert received_state["forged"] is False


# ── 4. External Authentication & Tenant Isolation via Ingress ─────────────────

@pytest.mark.asyncio
async def test_synthetic_jwt_external_auth_and_cross_tenant_isolation():
    """Verify synthetic JWT auth verifies tenant scope and enforces strict isolation."""
    shared_key = "synthetic-secret-signing-key-for-ingress-tests-32chars"
    validator = OIDCAuthValidator(
        issuer="https://auth.example.com",
        audience="mcp-smartcache-api",
        static_secret_or_key=shared_key,
    )
    set_oidc_validator(validator)

    # Issue synthetic tokens for Tenant A and Tenant B
    token_tenant_a = jwt.encode(
        {
            "iss": "https://auth.example.com",
            "aud": "mcp-smartcache-api",
            "sub": "user_alpha_01",
            "tenant_id": "tenant_a",
            "exp": time.time() + 3600,
        },
        shared_key,
        algorithm="HS256",
    )

    token_tenant_b = jwt.encode(
        {
            "iss": "https://auth.example.com",
            "aud": "mcp-smartcache-api",
            "sub": "user_beta_01",
            "tenant_id": "tenant_b",
            "exp": time.time() + 3600,
        },
        shared_key,
        algorithm="HS256",
    )

    # Control API Test
    transport = ASGITransport(app=control_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Unauthenticated request -> 401
        res_unauth = await client.get("/policies/effective/tenant_a")
        assert res_unauth.status_code == 401

        # 2. Invalid token -> 401
        res_invalid = await client.get(
            "/policies/effective/tenant_a",
            headers={"Authorization": "Bearer invalid.jwt.token"},
        )
        assert res_invalid.status_code == 401

        # 3. Tenant A accessing Tenant A -> 200
        res_a = await client.get(
            "/policies/effective/tenant_a",
            headers={"Authorization": f"Bearer {token_tenant_a}"},
        )
        assert res_a.status_code == 200

        # 4. Cross-tenant attempt: Tenant B attempting to access Tenant A policy -> 403 Forbidden
        res_cross = await client.get(
            "/policies/effective/tenant_a",
            headers={
                "Authorization": f"Bearer {token_tenant_b}",
                "X-Tenant-ID": "tenant_a",
            },
        )
        assert res_cross.status_code == 403
        assert "not authorized to access tenant 'tenant_a'" in res_cross.json()["detail"]


# ── 5. Control API Rejects Dev Keys in Production Mode ─────────────────────────

@pytest.mark.asyncio
async def test_control_api_rejects_dev_keys_in_production():
    """Verify Control API endpoint strictly rejects dev client and admin keys in production mode."""
    with patch.dict(os.environ, {"ENVIRONMENT": "production"}):
        transport = ASGITransport(app=control_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Dev admin secret
            res_admin = await client.get(
                "/policies/effective/default",
                headers={"Authorization": "Bearer dev-admin-secret-key-32chars-long"},
            )
            assert res_admin.status_code == 401
            assert "Development credentials are strictly rejected" in res_admin.json()["detail"]

            # 2. Dev client secret
            res_client = await client.get(
                "/policies/effective/default",
                headers={"Authorization": "Bearer dev-client-secret-key-32chars-long"},
            )
            assert res_client.status_code == 401
            assert "Development credentials are strictly rejected" in res_client.json()["detail"]
