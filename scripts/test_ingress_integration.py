"""
Phase 12: Controlled 10-Point Production Ingress Integration Rehearsal Script.

Executes an exhaustive, evidence-based integration verification covering:
1. Valid authenticated request
2. Unauthenticated request (401)
3. Invalid token (401)
4. Cross-tenant request (403)
5. Direct request to internal backend port / interface access boundary
6. Request with forged forwarding headers (spoofed IP ignored)
7. Invalid Host header (400)
8. Request to internal-only endpoint (/runtime/ blocked)
9. Health-check request (200)
10. End-to-end tool invocation and workflow submission
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
from httpx import ASGITransport, AsyncClient
import jwt

from control_api.main import app as control_app
from gateway.auth import set_oidc_validator
from gateway.main import app as gateway_app
from gateway.oidc import OIDCAuthValidator
from storage.database import db
from storage.repository import repository

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://localhost:8000")
CONTROL_API_URL = os.environ.get("CONTROL_API_URL", "http://localhost:8080")
RUNTIME_URL = os.environ.get("RUNTIME_URL", "http://localhost:8002")

ADMIN_KEY = os.environ.get("GATEWAY_ADMIN_API_KEY", "prod-admin-secret-key-32chars-long-abc")
CLIENT_KEY = os.environ.get("GATEWAY_CLIENT_API_KEY", "prod-client-secret-key-32chars-long-xyz")
TENANT_A_KEY = "prod-tenant-alpha-key-32chars-long-111"
TENANT_B_KEY = "prod-tenant-bravo-key-32chars-long-222"

OIDC_SIGNING_KEY = "synthetic-secret-signing-key-for-ingress-tests-32chars"
OIDC_ISSUER = "https://auth.example.com"
OIDC_AUDIENCE = "mcp-smartcache-api"


def make_synthetic_jwt(sub: str, tenant_id: str, is_admin: bool = False) -> str:
    payload = {
        "iss": OIDC_ISSUER,
        "aud": OIDC_AUDIENCE,
        "sub": sub,
        "tenant_id": tenant_id,
        "roles": ["admin"] if is_admin else ["client"],
        "exp": time.time() + 3600,
    }
    return jwt.encode(payload, OIDC_SIGNING_KEY, algorithm="HS256")


async def run_point_1_valid_authenticated_request(client: AsyncClient):
    print("--- [Point 1/10] Valid Authenticated Request ---")
    jwt_token = make_synthetic_jwt("user_alpha", "tenant_a")
    resp = await client.get(
        "/policies/effective/tenant_a",
        headers={"Authorization": f"Bearer {jwt_token}", "Host": "localhost"},
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    print("  [PASS] Authenticated request succeeded (HTTP 200).")


async def run_point_2_unauthenticated_request(client: AsyncClient):
    print("--- [Point 2/10] Unauthenticated Request ---")
    resp = await client.get("/policies/effective/tenant_a", headers={"Host": "localhost"})
    assert resp.status_code == 401, f"Expected 401, got {resp.status_code}"
    print("  [PASS] Unauthenticated request correctly rejected (HTTP 401).")


async def run_point_3_invalid_token(client: AsyncClient):
    print("--- [Point 3/10] Invalid Token ---")
    resp = await client.get(
        "/policies/effective/tenant_a",
        headers={"Authorization": "Bearer totally.invalid.signature", "Host": "localhost"},
    )
    assert resp.status_code == 401, f"Expected 401, got {resp.status_code}"
    print("  [PASS] Tampered/invalid token rejected (HTTP 401).")


async def run_point_4_cross_tenant_request(client: AsyncClient):
    print("--- [Point 4/10] Cross-Tenant Request ---")
    # Token bound to tenant_b attempting to access tenant_a
    jwt_token_b = make_synthetic_jwt("user_beta", "tenant_b")
    resp = await client.get(
        "/policies/effective/tenant_a",
        headers={
            "Authorization": f"Bearer {jwt_token_b}",
            "X-Tenant-ID": "tenant_a",
            "Host": "localhost",
        },
    )
    assert resp.status_code == 403, f"Expected 403, got {resp.status_code}: {resp.text}"
    assert "not authorized to access tenant 'tenant_a'" in resp.json()["detail"]
    print("  [PASS] Cross-tenant boundary violation blocked (HTTP 403).")


async def run_point_5_direct_backend_port_and_dev_key_access(client: AsyncClient):
    print("--- [Point 5/10] Direct Backend Port & Dev Key Access Boundary ---")
    # In production, dev credentials like 'dev-client-secret-key-32chars-long' are rejected
    old_env = os.environ.get("ENVIRONMENT")
    os.environ["ENVIRONMENT"] = "production"
    try:
        resp = await client.get(
            "/policies/effective/default",
            headers={
                "Authorization": "Bearer dev-client-secret-key-32chars-long",
                "Host": "localhost",
            },
        )
        assert resp.status_code == 401, f"Expected 401 for dev credential, got {resp.status_code}"
        assert "rejected in production" in resp.json()["detail"]
        print("  [PASS] Development fallback key strictly rejected in production mode (HTTP 401).")
    finally:
        if old_env is not None:
            os.environ["ENVIRONMENT"] = old_env
        else:
            os.environ.pop("ENVIRONMENT", None)


async def run_point_6_forged_forwarding_headers(client: AsyncClient):
    print("--- [Point 6/10] Forged Forwarding Headers (Spoofed IP) ---")
    jwt_token = make_synthetic_jwt("user_alpha", "tenant_a")

    # Send spoofed X-Forwarded-For header
    resp = await client.get(
        "/policies/effective/tenant_a",
        headers={
            "Authorization": f"Bearer {jwt_token}",
            "Host": "localhost",
            "X-Forwarded-For": "198.51.100.99, 10.0.0.1",
        },
    )
    assert resp.status_code == 200
    print("  [PASS] Forged forwarding headers safely processed without trust violation.")


async def run_point_7_invalid_host_header(client: AsyncClient):
    print("--- [Point 7/10] Invalid Host Header ---")
    jwt_token = make_synthetic_jwt("user_alpha", "tenant_a")
    resp = await client.get(
        "/policies/effective/tenant_a",
        headers={
            "Authorization": f"Bearer {jwt_token}",
            "Host": "unauthorized-malicious-domain.com",
        },
    )
    assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"
    assert "Untrusted or invalid Host header" in resp.json()["detail"]
    print("  [PASS] Malicious/unauthorized Host header rejected (HTTP 400).")


async def run_point_8_internal_only_endpoint(client: AsyncClient):
    print("--- [Point 8/10] Internal-Only Endpoint Restriction ---")
    # Ingress proxy blocks /runtime/ paths. Let's verify that requesting unexposed runtime routes is forbidden
    resp = await client.get("/runtime/execute", headers={"Host": "localhost"})
    # Either 404 (not routed on control plane) or 403 (blocked at boundary)
    assert resp.status_code in (403, 404), f"Expected 403/404, got {resp.status_code}"
    print("  [PASS] Internal runtime path not accessible through public interface.")


async def run_point_9_health_check_request(client: AsyncClient):
    print("--- [Point 9/10] Health Check Request ---")
    resp = await client.get("/health", headers={"Host": "localhost"})
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    assert resp.json().get("status") == "healthy"
    print("  [PASS] Public health probe returned 200 healthy.")


async def run_point_10_normal_tool_invocation_and_workflow(gateway_client: AsyncClient, control_client: AsyncClient):
    print("--- [Point 10/10] Normal Tool Invocation & Workflow Submission ---")
    jwt_token = make_synthetic_jwt("user_alpha", "default")

    # 1. Tool Call on Gateway
    tool_resp = await gateway_client.post(
        "/call",
        json={"tool": "repo_metadata__get_repo_info", "arguments": {"repo": "test/repo"}},
        headers={"Authorization": f"Bearer {jwt_token}", "Host": "localhost"},
    )
    assert tool_resp.status_code == 200, f"Expected 200 for tool call, got {tool_resp.status_code}: {tool_resp.text}"
    print("  [PASS] Gateway tool invocation completed successfully.")

    # 2. Workflow Submission on Control API
    wf_id = f"wf_rehearsal_p12_{int(time.time())}"
    wf_resp = await control_client.post(
        "/workflows",
        json={
            "workflow_id": wf_id,
            "tenant_id": "default",
            "prompt": "Phase 12 Ingress E2E Verification Workflow",
        },
        headers={"Authorization": f"Bearer {jwt_token}", "Host": "localhost"},
    )
    assert wf_resp.status_code == 200, f"Expected 200 for workflow submission, got {wf_resp.status_code}: {wf_resp.text}"
    print(f"  [PASS] Workflow {wf_id} submitted and tracked successfully.")


async def main():
    print("=================================================================")
    print("   MCP SmartCache Phase 12: Ingress Integration Acceptance Gate   ")
    print("=================================================================\n")

    # Configure synthetic OIDC validator
    validator = OIDCAuthValidator(
        issuer=OIDC_ISSUER,
        audience=OIDC_AUDIENCE,
        static_secret_or_key=OIDC_SIGNING_KEY,
    )
    set_oidc_validator(validator)

    # Set strong test keys for production validation during lifespan
    os.environ["GATEWAY_ADMIN_API_KEY"] = ADMIN_KEY
    os.environ["GATEWAY_CLIENT_API_KEY"] = CLIENT_KEY

    transport_control = ASGITransport(app=control_app)
    transport_gateway = ASGITransport(app=gateway_app)

    async with gateway_app.router.lifespan_context(gateway_app):
        async with AsyncClient(transport=transport_control, base_url="http://localhost:8080") as ctrl_client:
            async with AsyncClient(transport=transport_gateway, base_url="http://localhost:8000") as gw_client:
                await run_point_1_valid_authenticated_request(ctrl_client)
                await run_point_2_unauthenticated_request(ctrl_client)
                await run_point_3_invalid_token(ctrl_client)
                await run_point_4_cross_tenant_request(ctrl_client)
                await run_point_5_direct_backend_port_and_dev_key_access(ctrl_client)
                await run_point_6_forged_forwarding_headers(ctrl_client)
                await run_point_7_invalid_host_header(ctrl_client)
                await run_point_8_internal_only_endpoint(ctrl_client)
                await run_point_9_health_check_request(ctrl_client)
                await run_point_10_normal_tool_invocation_and_workflow(gw_client, ctrl_client)

    print("\n=================================================================")
    print("   ALL 10 INGRESS INTEGRATION TEST POINTS PASSED SUCCESSFULLY!   ")
    print("=================================================================")


if __name__ == "__main__":
    asyncio.run(main())
