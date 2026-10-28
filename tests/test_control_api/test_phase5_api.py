"""
Phase 5 Tests — Control API: Upstream Servers, Coordination Metrics, and Resumption.

Tests:
1. Upstream server registration requires admin authorization (401/403/200).
2. Server CRUD, status toggle, and tool inspection.
3. Cluster cache coordination metrics endpoint.
4. Workflow checkpoint retrieval and tenant isolation.
5. Workflow resumability assessment.
6. Authorized workflow resumption and completed workflow rejection.
"""

from datetime import datetime, timezone
import json
import os
import uuid
import pytest
from httpx import AsyncClient, ASGITransport

from control_api.main import app as control_api_app
from storage.database import db
from storage.models.entities import (
    WorkflowCheckpoint,
    WorkflowRun,
    WorkflowStatus,
    WorkflowTask,
    TaskStatus,
)
from storage.repository import repository


@pytest.fixture(autouse=True)
async def setup_test_env():
    await db.connect()
    os.environ["CLIENT_TENANT_KEYS"] = json.dumps({
        "key_alpha": "tenant_a",
        "key_beta": "tenant_b",
    })
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


@pytest.mark.asyncio
async def test_upstream_server_registration_authorization(auth_headers):
    """Registering an upstream server requires admin privileges."""
    transport = ASGITransport(app=control_api_app)
    server_id = f"srv_{uuid.uuid4().hex[:8]}"

    payload = {
        "server_id": server_id,
        "name": "Documentation Server",
        "transport": "sse",
        "connection_config": {"url": "https://api.docs.internal/sse", "timeout": 10},
        "is_enabled": True,
        "tags": ["docs", "internal"],
    }

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. Unauthenticated -> 401
        res_no_auth = await ac.post("/servers", json=payload)
        assert res_no_auth.status_code == 401

        # 2. Normal tenant client -> 403 Forbidden
        res_tenant = await ac.post("/servers", json=payload, headers=auth_headers["tenant_a"])
        assert res_tenant.status_code == 403

        # 3. Admin -> 200 OK
        res_admin = await ac.post("/servers", json=payload, headers=auth_headers["admin"])
        assert res_admin.status_code == 200
        data = res_admin.json()
        assert data["server_id"] == server_id
        assert data["name"] == "Documentation Server"
        assert data["config_version"] >= 1


@pytest.mark.asyncio
async def test_upstream_server_lifecycle_and_tools(auth_headers):
    """Test listing, inspecting, disabling, and deleting upstream server."""
    transport = ASGITransport(app=control_api_app)
    server_id = f"srv_crud_{uuid.uuid4().hex[:8]}"

    payload = {
        "server_id": server_id,
        "name": "Metrics Upstream",
        "transport": "sse",
        "connection_config": {"url": "https://metrics.internal/sse"},
        "is_enabled": True,
        "tags": ["metrics"],
    }

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Register
        reg_res = await ac.post("/servers", json=payload, headers=auth_headers["admin"])
        assert reg_res.status_code == 200

        # List servers
        list_res = await ac.get("/servers", headers=auth_headers["admin"])
        assert list_res.status_code == 200
        servers = list_res.json()
        assert any(s["server_id"] == server_id for s in servers)

        # Get metadata
        get_res = await ac.get(f"/servers/{server_id}", headers=auth_headers["admin"])
        assert get_res.status_code == 200
        assert get_res.json()["name"] == "Metrics Upstream"

        # Update status to disabled
        status_res = await ac.put(
            f"/servers/{server_id}/status",
            json={"status": "disabled"},
            headers=auth_headers["admin"],
        )
        assert status_res.status_code == 200
        assert status_res.json()["status"] == "disabled"

        # Inspect tools endpoint
        tools_res = await ac.get(f"/servers/{server_id}/tools", headers=auth_headers["admin"])
        assert tools_res.status_code == 200
        assert isinstance(tools_res.json()["tools"], list)

        # Delete server
        del_res = await ac.delete(f"/servers/{server_id}", headers=auth_headers["admin"])
        assert del_res.status_code == 200
        assert del_res.json()["deleted"] is True


@pytest.mark.asyncio
async def test_cache_coordination_endpoint(auth_headers):
    """GET /cache/coordination returns cluster metrics."""
    transport = ASGITransport(app=control_api_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.get("/cache/coordination", headers=auth_headers["admin"])
        assert res.status_code == 200
        data = res.json()
        assert "coordination_status" in data
        assert "distributed_singleflight" in data
        assert "coordination_features" in data
        assert data["coordination_features"]["atomic_lease_release"] is True


@pytest.mark.asyncio
async def test_workflow_checkpoints_and_resumability(auth_headers):
    """Checkpoints inspection and resumability evaluation endpoints."""
    transport = ASGITransport(app=control_api_app)
    wf_id = f"wf_cp_{uuid.uuid4().hex[:8]}"

    # Seed an interrupted workflow for tenant_a
    await repository.create_workflow(
        workflow_id=wf_id,
        tenant_id="tenant_a",
        correlation_id=f"req_{uuid.uuid4().hex[:8]}",
        request_prompt="Examine repository",
    )
    await repository.update_workflow_status(
        workflow_id=wf_id,
        new_status=WorkflowStatus.RUNNING,
        tenant_id="tenant_a",
    )
    await repository.update_workflow_status(
        workflow_id=wf_id,
        new_status=WorkflowStatus.INTERRUPTED,
        error="Gateway restarted during execution",
        tenant_id="tenant_a",
    )

    # Add task and checkpoint
    await repository.save_tasks([
        WorkflowTask(
            task_id="t1",
            workflow_id=wf_id,
            tenant_id="tenant_a",
            tool_name="repo_metadata__get_repo_info",
            status=TaskStatus.COMPLETED,
            result={"name": "repo"},
        ),
        WorkflowTask(
            task_id="t2",
            workflow_id=wf_id,
            tenant_id="tenant_a",
            tool_name="issues__list_issues",
            status=TaskStatus.PENDING,
        ),
    ])

    await repository.save_checkpoint(
        WorkflowCheckpoint(
            checkpoint_id=f"cp_{uuid.uuid4().hex[:8]}",
            workflow_id=wf_id,
            tenant_id="tenant_a",
            graph_version="1.0.0",
            checkpoint_version=1,
            completed_tasks={"t1": {"status": "ok"}},
            task_results={"t1": {"name": "repo"}},
            pending_tasks=["t2"],
        )
    )

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. Tenant A checks checkpoints
        cp_res = await ac.get(f"/workflows/{wf_id}/checkpoints", headers=auth_headers["tenant_a"])
        assert cp_res.status_code == 200
        checkpoints = cp_res.json()["checkpoints"]
        assert len(checkpoints) >= 1
        assert checkpoints[0]["graph_version"] == "1.0.0"

        # 2. Cross-tenant check: Tenant B gets 404
        cp_b_res = await ac.get(f"/workflows/{wf_id}/checkpoints", headers=auth_headers["tenant_b"])
        assert cp_b_res.status_code == 404

        # 3. Check resumability
        resumable_res = await ac.get(f"/workflows/{wf_id}/resumability", headers=auth_headers["tenant_a"])
        assert resumable_res.status_code == 200
        rdata = resumable_res.json()
        assert rdata["can_resume"] is True
        assert "t1" in rdata["tasks_to_reuse"]
        assert "t2" in rdata["tasks_to_resume"]

        # 4. Trigger resume
        resume_res = await ac.post(f"/workflows/{wf_id}/resume", headers=auth_headers["tenant_a"])
        assert resume_res.status_code == 200
        res_json = resume_res.json()
        assert res_json["status"] == "running"
        assert res_json["resumed"] is True
        assert "attempt_id" in res_json

        # 5. Check resume history
        hist_res = await ac.get(f"/workflows/{wf_id}/checkpoints", headers=auth_headers["tenant_a"])
        assert len(hist_res.json()["resume_attempts"]) >= 1


@pytest.mark.asyncio
async def test_workflow_resume_rejects_completed(auth_headers):
    """A completed workflow cannot be resumed."""
    transport = ASGITransport(app=control_api_app)
    wf_id = f"wf_comp_{uuid.uuid4().hex[:8]}"

    await repository.create_workflow(
        workflow_id=wf_id,
        tenant_id="tenant_a",
        request_prompt="Already done",
    )
    await repository.update_workflow_status(
        workflow_id=wf_id,
        new_status=WorkflowStatus.RUNNING,
        tenant_id="tenant_a",
    )
    await repository.update_workflow_status(
        workflow_id=wf_id,
        new_status=WorkflowStatus.COMPLETED,
        final_answer="All done",
        tenant_id="tenant_a",
    )

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.post(f"/workflows/{wf_id}/resume", headers=auth_headers["tenant_a"])
        assert res.status_code == 400
        assert "already completed" in res.json()["detail"].lower()
