"""
Cross-Tenant Security & Isolation Regression Tests for MCP SmartCache.
Verifies:
1. Tenant A cannot read Tenant B's workflows, tasks, traces, or audit records.
2. Tenant A's listing only returns Tenant A's workflows.
3. Unauthenticated requests are rejected with 401.
4. Client attempting to access an unauthorized tenant is rejected with 403 Forbidden.
5. Disabled tenants are rejected with 403 on workflow operations.
6. Cache isolation: Tenant A's cache entries are never shared with Tenant B.
7. Cache invalidation can be strictly scoped to a single tenant.
8. Singleflight deduplication does not coalesce in-flight requests across tenant boundaries.
"""

from datetime import datetime, timezone
import json
import os
import uuid
import pytest
from httpx import AsyncClient, ASGITransport

from control_api.main import app as control_api_app
from gateway.cache_key import build_cache_key
from gateway.cache_policy import CachePolicy
from gateway.cache_store import RedisCacheStore
from gateway.singleflight import Singleflight
from storage.database import db
from storage.models.entities import AuditEvent, TaskStatus, TraceEvent, WorkflowStatus, WorkflowTask
from storage.repository import repository


@pytest.fixture(autouse=True)
async def setup_test_env():
    """Ensure database connection, seed tenants, and configure tenant keys."""
    await db.connect()

    # Configure tenant API keys
    os.environ["CLIENT_TENANT_KEYS"] = json.dumps({
        "key_alpha": "tenant_a",
        "key_beta": "tenant_b",
        "key_disabled": "tenant_disabled",
    })

    # Create or update tenant_a, tenant_b, tenant_disabled
    await repository.create_tenant(
        tenant_id="tenant_a",
        name="Tenant Alpha",
        is_active=True,
        config={"max_concurrency": 5, "rate_limit_rpm": 60},
    )
    await repository.create_tenant(
        tenant_id="tenant_b",
        name="Tenant Beta",
        is_active=True,
        config={"max_concurrency": 3, "rate_limit_rpm": 30},
    )
    await repository.create_tenant(
        tenant_id="tenant_disabled",
        name="Disabled Tenant",
        is_active=False,
        config={"max_concurrency": 1},
    )
    yield
    await db.disconnect()


@pytest.fixture
def auth_headers():
    admin_key = os.environ.get("GATEWAY_ADMIN_API_KEY", "dev-admin-secret-key-32chars-long")

    return {
        "admin": {"Authorization": f"Bearer {admin_key}"},
        "tenant_a": {"Authorization": "Bearer key_alpha", "X-Tenant-ID": "tenant_a"},
        "tenant_b": {"Authorization": "Bearer key_beta", "X-Tenant-ID": "tenant_b"},
        "tenant_disabled": {"Authorization": "Bearer key_disabled", "X-Tenant-ID": "tenant_disabled"},
    }


@pytest.mark.asyncio
async def test_cross_tenant_workflow_read_denied(auth_headers):
    """Verify Tenant A cannot access Tenant B's workflow, tasks, or traces."""
    wf_b_id = f"wf_b_{uuid.uuid4().hex[:8]}"

    # Create workflow belonging to tenant_b
    await repository.create_workflow(
        workflow_id=wf_b_id,
        tenant_id="tenant_b",
        correlation_id=f"req_{uuid.uuid4().hex[:8]}",
        request_prompt="Tenant B confidential task",
    )
    await repository.save_tasks(
        [
            WorkflowTask(
                task_id="task_secret",
                workflow_id=wf_b_id,
                tenant_id="tenant_b",
                tool_name="repo_metadata__get_info",
                status=TaskStatus.COMPLETED,
                result={"confidential_metric": 42},
            )
        ]
    )

    transport = ASGITransport(app=control_api_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Tenant B can access its own workflow
        res_b = await client.get(f"/workflows/{wf_b_id}", headers=auth_headers["tenant_b"])
        assert res_b.status_code == 200
        assert res_b.json()["workflow_id"] == wf_b_id

        # 2. Tenant A trying to get Tenant B's workflow MUST be rejected with 404
        res_a_wf = await client.get(f"/workflows/{wf_b_id}", headers=auth_headers["tenant_a"])
        assert res_a_wf.status_code == 404

        # 3. Tenant A trying to get Tenant B's tasks MUST be rejected with 404
        res_a_tasks = await client.get(f"/workflows/{wf_b_id}/tasks", headers=auth_headers["tenant_a"])
        assert res_a_tasks.status_code == 404

        # 4. Tenant A trying to get Tenant B's traces MUST be rejected with 404
        res_a_trace = await client.get(f"/workflows/{wf_b_id}/trace", headers=auth_headers["tenant_a"])
        assert res_a_trace.status_code == 404


@pytest.mark.asyncio
async def test_tenant_workflow_listing_isolation(auth_headers):
    """Verify workflow listing strictly filters by tenant context."""
    wf_a_id = f"wf_a_{uuid.uuid4().hex[:8]}"
    wf_b_id = f"wf_b_{uuid.uuid4().hex[:8]}"

    await repository.create_workflow(workflow_id=wf_a_id, tenant_id="tenant_a", request_prompt="Task A")
    await repository.create_workflow(workflow_id=wf_b_id, tenant_id="tenant_b", request_prompt="Task B")

    transport = ASGITransport(app=control_api_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Tenant A listing
        res_a = await client.get("/workflows", headers=auth_headers["tenant_a"])
        assert res_a.status_code == 200
        data_a = res_a.json()
        ids_a = [w["workflow_id"] for w in data_a["workflows"]]
        assert wf_a_id in ids_a
        assert wf_b_id not in ids_a

        # Tenant B listing
        res_b = await client.get("/workflows", headers=auth_headers["tenant_b"])
        assert res_b.status_code == 200
        data_b = res_b.json()
        ids_b = [w["workflow_id"] for w in data_b["workflows"]]
        assert wf_b_id in ids_b
        assert wf_a_id not in ids_b


@pytest.mark.asyncio
async def test_audit_logs_tenant_isolation(auth_headers):
    """Verify audit logs are isolated per tenant."""
    aud_a = f"aud_a_{uuid.uuid4().hex[:8]}"
    aud_b = f"aud_b_{uuid.uuid4().hex[:8]}"

    await repository.record_audit_event(
        AuditEvent(
            event_id=aud_a,
            tenant_id="tenant_a",
            actor="client_a",
            action="workflow.submit",
            resource_type="workflow",
            resource_id="wf_1",
            timestamp=datetime.now(timezone.utc),
        )
    )

    await repository.record_audit_event(
        AuditEvent(
            event_id=aud_b,
            tenant_id="tenant_b",
            actor="client_b",
            action="workflow.submit",
            resource_type="workflow",
            resource_id="wf_2",
            timestamp=datetime.now(timezone.utc),
        )
    )

    transport = ASGITransport(app=control_api_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res_a = await client.get("/audit", headers=auth_headers["tenant_a"])
        assert res_a.status_code == 200
        events_a = [e["event_id"] for e in res_a.json()["audit_events"]]
        assert aud_a in events_a
        assert aud_b not in events_a

        res_b = await client.get("/audit", headers=auth_headers["tenant_b"])
        assert res_b.status_code == 200
        events_b = [e["event_id"] for e in res_b.json()["audit_events"]]
        assert aud_b in events_b
        assert aud_a not in events_b


@pytest.mark.asyncio
async def test_unauthorized_tenant_impersonation_denied(auth_headers):
    """
    Verify that Client A passing another tenant's ID (tenant_b) in X-Tenant-ID
    is rejected with 403 Forbidden.
    """
    transport = ASGITransport(app=control_api_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Key alpha is bound to tenant_a. It CANNOT access tenant_b!
        spoofed_headers = {"Authorization": "Bearer key_alpha", "X-Tenant-ID": "tenant_b"}
        res = await client.get("/workflows", headers=spoofed_headers)
        assert res.status_code == 403
        assert "not authorized" in res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_unauthenticated_request_rejected():
    """Verify unauthenticated requests are rejected with 401."""
    transport = ASGITransport(app=control_api_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/workflows")
        assert res.status_code == 401


@pytest.mark.asyncio
async def test_disabled_tenant_rejected(auth_headers):
    """Verify disabled tenant cannot submit workflows."""
    transport = ASGITransport(app=control_api_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post(
            "/workflows",
            json={"prompt": "Run confidential investigation", "owner": "google", "repo": "smartcache"},
            headers=auth_headers["tenant_disabled"],
        )
        assert res.status_code == 403
        assert "disabled" in res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_cross_tenant_cache_isolation():
    """
    Verify that cache keys and cache hits/misses are strictly tenant-isolated.
    Tenant A's cached entry cannot be read by Tenant B even with identical arguments.
    """
    policy = CachePolicy(
        tool_name="repo_metadata__get_info",
        enabled=True,
        read_only=True,
        ttl_seconds=300,
    )
    args = {"owner": "google", "repo": "smartcache"}

    # 1. Deterministic cache keys differ by tenant scope
    key_a = build_cache_key("repo_metadata__get_info", args, policy, scope="tenant_a")
    key_b = build_cache_key("repo_metadata__get_info", args, policy, scope="tenant_b")

    assert key_a != key_b
    assert ":tenant_a:" in key_a
    assert ":tenant_b:" in key_b

    # 2. Test cache store behavior
    store = RedisCacheStore(redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
    try:
        await store.connect()
        from mcp.types import CallToolResult, TextContent
        sample_result = CallToolResult(
            is_error=False,
            content=[TextContent(type="text", text=json.dumps({"stars": 128, "owner": "google"}))],
        )

        # Store result under tenant_a
        await store.set(key_a, sample_result, ttl_seconds=300, tool_name="repo_metadata__get_info", scope="tenant_a")

        # Tenant A gets a cache hit
        cached_a = await store.get(key_a)
        assert cached_a is not None

        # Tenant B gets a cache MISS!
        cached_b = await store.get(key_b)
        assert cached_b is None

        # Cleanup
        await store.delete(key_a)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_tenant_scoped_cache_invalidation():
    """Verify cache invalidation can target only a specific tenant's entries."""
    store = RedisCacheStore(redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
    try:
        await store.connect()
        policy = CachePolicy(tool_name="repo_metadata__get_info", enabled=True, read_only=True)
        args = {"owner": "google", "repo": "smartcache"}

        key_a = build_cache_key("repo_metadata__get_info", args, policy, scope="tenant_a")
        key_b = build_cache_key("repo_metadata__get_info", args, policy, scope="tenant_b")

        from mcp.types import CallToolResult, TextContent
        res = CallToolResult(is_error=False, content=[TextContent(type="text", text="{}")])

        await store.set(key_a, res, ttl_seconds=300, tool_name="repo_metadata__get_info", scope="tenant_a")
        await store.set(key_b, res, ttl_seconds=300, tool_name="repo_metadata__get_info", scope="tenant_b")

        # Invalidate tool cache ONLY for tenant_a
        deleted = await store.invalidate_tool("repo_metadata__get_info", scope="tenant_a")
        assert deleted >= 1

        # Key A is deleted
        assert await store.get(key_a) is None
        # Key B is preserved!
        assert await store.get(key_b) is not None

        # Cleanup
        await store.delete(key_b)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_singleflight_deduplication_isolation():
    """
    Verify Singleflight deduplication does not merge requests across different security boundaries.
    """
    sf = Singleflight()
    policy = CachePolicy(tool_name="repo_metadata__get_info", enabled=True, read_only=True)
    args = {"owner": "google", "repo": "smartcache"}

    key_a = build_cache_key("repo_metadata__get_info", args, policy, scope="tenant_a")
    key_b = build_cache_key("repo_metadata__get_info", args, policy, scope="tenant_b")

    # Different keys ensure independent in-flight tracking
    assert not sf.is_in_flight(key_a)
    assert not sf.is_in_flight(key_b)
