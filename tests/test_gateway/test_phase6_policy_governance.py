"""
Phase 6 Feature A Tests: Dynamic Tenant Policy Governance.
Verifies policy schema validation, persistence, versioning, atomic activation,
rollback, tenant isolation, and control API endpoints.
"""

import pytest
import uuid
from httpx import AsyncClient, ASGITransport

from storage.models.entities import (
    CacheRules,
    ResourceLimits,
    TenantPolicy,
    ToolRules,
)
from storage.repository import repository
from gateway.policy_engine import validate_policy_configuration, policy_engine
from control_api.main import app, DEFAULT_ADMIN_KEY

CLIENT_ALPHA_KEY = "alpha-test-token-secret-32chars"
CLIENT_BETA_KEY = "beta-test-token-secret-32chars"


@pytest.fixture(autouse=True)
def setup_auth_env(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("GATEWAY_ADMIN_API_KEY", DEFAULT_ADMIN_KEY)
    monkeypatch.setenv(
        "CLIENT_TENANT_KEYS",
        f'{{"{CLIENT_ALPHA_KEY}": "tenant_a", "{CLIENT_BETA_KEY}": "tenant_b"}}',
    )


# ── 1. Policy Schema & Validation Tests ────────────────────────────────────────

def test_policy_validation_valid():
    valid_payload = {
        "policy_id": "pol_valid_1",
        "tenant_id": "tenant_a",
        "policy_version": 1,
        "tool_rules": {
            "allowed_tools": ["repo_metadata__*", "issues__*"],
            "denied_tools": ["*_delete*"],
            "read_only_tools": ["repo_metadata__*", "issues__*"],
            "default_deny": True,
        },
        "cache_rules": {
            "enabled": True,
            "default_ttl_seconds": 300,
            "max_ttl_seconds": 3600,
            "tool_ttls": {"repo_metadata__get_repo_info": 600},
            "max_response_bytes": 524288,
        },
        "resource_limits": {
            "max_concurrent_upstream": 10,
            "rate_limit_rpm": 120,
            "burst_limit": 20,
            "max_input_payload_bytes": 1048576,
            "max_output_payload_bytes": 1048576,
        },
    }
    errors = validate_policy_configuration(valid_payload)
    assert errors == []


def test_policy_validation_rejects_unsafe_configurations():
    # 1. Invalid TTLs
    bad_ttl = {
        "policy_id": "pol_bad",
        "tenant_id": "tenant_a",
        "cache_rules": {
            "default_ttl_seconds": 5000,
            "max_ttl_seconds": 3600,  # max < default
        },
    }
    errors = validate_policy_configuration(bad_ttl)
    assert any("max_ttl_seconds must be an integer >= default_ttl_seconds" in e for e in errors)

    # 2. Tool TTL exceeds max_ttl
    exceed_ttl = {
        "policy_id": "pol_bad",
        "tenant_id": "tenant_a",
        "cache_rules": {
            "default_ttl_seconds": 300,
            "max_ttl_seconds": 3600,
            "tool_ttls": {"some_tool": 7200},
        },
    }
    errors = validate_policy_configuration(exceed_ttl)
    assert any("exceeds max_ttl_seconds" in e for e in errors)

    # 3. Mutation tool classified as read-only
    bad_tool = {
        "policy_id": "pol_bad",
        "tenant_id": "tenant_a",
        "tool_rules": {
            "read_only_tools": ["issues__delete_issue"],
        },
    }
    errors = validate_policy_configuration(bad_tool)
    assert any("contains mutation keyword and cannot be classified as read-only" in e for e in errors)

    # 4. Excessive resource limits
    excess_limits = {
        "policy_id": "pol_bad",
        "tenant_id": "tenant_a",
        "resource_limits": {
            "max_concurrent_upstream": 9999,  # platform bound is 500
        },
    }
    errors = validate_policy_configuration(excess_limits)
    assert any("max_concurrent_upstream must be between 1 and 500" in e for e in errors)


# ── 2. Persistence & Atomic Activation Tests ──────────────────────────────────

@pytest.mark.asyncio
async def test_policy_persistence_and_version_activation():
    tenant_id = f"ten_test_{uuid.uuid4().hex[:8]}"
    await repository.create_tenant(tenant_id=tenant_id, name="Test Tenant")

    p1 = TenantPolicy(
        policy_id=f"pol_{tenant_id}_v1",
        tenant_id=tenant_id,
        policy_version=1,
        is_active=True,
        description="Version 1",
        tool_rules=ToolRules(allowed_tools=["*"], default_deny=False),
        cache_rules=CacheRules(default_ttl_seconds=120),
        resource_limits=ResourceLimits(rate_limit_rpm=30),
    )
    p2 = TenantPolicy(
        policy_id=f"pol_{tenant_id}_v2",
        tenant_id=tenant_id,
        policy_version=2,
        is_active=False,
        description="Version 2",
        tool_rules=ToolRules(allowed_tools=["repo_metadata__*"], default_deny=True),
        cache_rules=CacheRules(default_ttl_seconds=240),
        resource_limits=ResourceLimits(rate_limit_rpm=60),
    )

    await repository.create_or_update_policy(p1)
    await repository.create_or_update_policy(p2)

    # Active policy should be v1
    active = await repository.get_active_policy(tenant_id)
    assert active is not None
    assert active.policy_version == 1
    assert active.cache_rules.default_ttl_seconds == 120

    # Atomically activate v2
    success = await repository.activate_policy(p2.policy_id, tenant_id)
    assert success is True

    # Now active policy must be v2 and v1 must be inactive
    active_new = await repository.get_active_policy(tenant_id)
    assert active_new is not None
    assert active_new.policy_version == 2
    assert active_new.cache_rules.default_ttl_seconds == 240

    old_v1 = await repository.get_policy(p1.policy_id, tenant_id)
    assert old_v1 is not None
    assert old_v1.is_active is False

    # Roll back to v1
    rollback_success = await repository.activate_policy(p1.policy_id, tenant_id)
    assert rollback_success is True
    rolled_back = await repository.get_active_policy(tenant_id)
    assert rolled_back.policy_version == 1


# ── 3. Control API Endpoints & Tenant Isolation Tests ─────────────────────────

@pytest.mark.asyncio
async def test_control_api_policy_crud_and_isolation():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Tenant Alpha reads their active policy
        resp = await client.get(
            "/policies/effective/tenant_a",
            headers={"Authorization": f"Bearer {CLIENT_ALPHA_KEY}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["policy"]["tenant_id"] == "tenant_a"

        # 2. Tenant Alpha attempting to access Tenant Beta policy is rejected (403)
        resp_forbidden = await client.get(
            "/policies/effective/tenant_b",
            headers={"Authorization": f"Bearer {CLIENT_ALPHA_KEY}"},
        )
        assert resp_forbidden.status_code == 403

        # 3. Dry-run validation endpoint
        resp_val = await client.post(
            "/policies/validate",
            headers={"Authorization": f"Bearer {CLIENT_ALPHA_KEY}"},
            json={
                "tenant_id": "tenant_a",
                "policy_version": 2,
                "tool_rules": {"allowed_tools": ["repo_metadata__*"]},
                "cache_rules": {"default_ttl_seconds": 600, "max_ttl_seconds": 1200},
                "resource_limits": {"rate_limit_rpm": 100},
            },
        )
        assert resp_val.status_code == 200
        assert resp_val.json()["valid"] is True
        assert resp_val.json()["errors"] == []

        # 4. Admin creates and activates a new policy version for Tenant Beta
        resp_create = await client.post(
            "/policies",
            headers={"Authorization": f"Bearer {DEFAULT_ADMIN_KEY}"},
            json={
                "policy_id": "pol_tenant_b_v2",
                "tenant_id": "tenant_b",
                "policy_version": 2,
                "is_active": True,
                "description": "Beta upgraded policy",
                "tool_rules": {"allowed_tools": ["repo_metadata__*", "issues__*"]},
                "cache_rules": {"default_ttl_seconds": 400, "max_ttl_seconds": 1800},
                "resource_limits": {"max_concurrent_upstream": 8, "rate_limit_rpm": 80},
            },
        )
        assert resp_create.status_code == 200
        assert resp_create.json()["status"] == "saved"

        # Verify active policy for Tenant Beta is now v2
        resp_beta_active = await client.get(
            "/policies/effective/tenant_b",
            headers={"Authorization": f"Bearer {CLIENT_BETA_KEY}"},
        )
        assert resp_beta_active.status_code == 200
        assert resp_beta_active.json()["policy"]["policy_version"] == 2
        assert resp_beta_active.json()["policy"]["resource_limits"]["max_concurrent_upstream"] == 8
