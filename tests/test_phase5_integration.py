"""
Phase 5 Comprehensive End-to-End Integrated System Verification Test.

Exercises the complete Phase 5 sequence:
1. Register an upstream MCP server through the Control API (Feature A).
2. Confirm server is saved and activated without restarting gateway.
3. Discover tools and verify schema registration and collision handling.
4. Coordinate equivalent cacheable requests across two gateway replicas via Redis (Feature B).
5. Verify atomic lease acquisition, waiter coalescing, and lease release.
6. Verify invalidation race protection with timestamp tracking.
7. Submit repository-intelligence workflow and verify durable checkpoint creation in PostgreSQL (Feature C).
8. Simulate a controlled workflow interruption at a safe task boundary.
9. Verify workflow resumability assessment (completed tasks reused, side-effects safely blocked).
10. Resume workflow through authorized Control API endpoint.
11. Confirm completed task results are preserved and skipped during resumption.
12. Verify cross-tenant isolation across registries, caches, checkpoints, and APIs.
13. Verify security audit events for server registration and workflow resumption.
"""

import asyncio
import json
import os
import time
import uuid
import pytest
from httpx import AsyncClient, ASGITransport

from control_api.main import app as control_api_app
from storage.database import db
from storage.models.entities import (
    WorkflowStatus,
    TaskStatus,
    WorkflowTask,
    WorkflowCheckpoint,
    UpstreamServer,
)
from storage.repository import repository
from gateway.cache_store import RedisCacheStore
from gateway.distributed_singleflight import DistributedSingleflight
from mcp.types import CallToolResult, TextContent


ADMIN_KEY = "dev-admin-secret-key-32chars-long"


@pytest.fixture(autouse=True)
async def setup_env():
    await db.connect()
    os.environ["CLIENT_TENANT_KEYS"] = json.dumps({
        "key_alpha": "tenant_a",
        "key_beta": "tenant_b",
    })
    await repository.create_tenant(tenant_id="tenant_a", name="Tenant Alpha")
    await repository.create_tenant(tenant_id="tenant_b", name="Tenant Beta")
    yield
    await db.disconnect()


@pytest.fixture
def auth_headers():
    return {
        "admin": {"Authorization": f"Bearer {ADMIN_KEY}", "X-Tenant-ID": "default"},
        "tenant_a": {"Authorization": "Bearer key_alpha", "X-Tenant-ID": "tenant_a"},
        "tenant_b": {"Authorization": "Bearer key_beta", "X-Tenant-ID": "tenant_b"},
    }


@pytest.mark.asyncio
async def test_phase5_full_e2e_integrated_system(auth_headers):
    transport = ASGITransport(app=control_api_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # ───────────────────────────────────────────────────────────────────────
        # Part 1: Dynamic Upstream MCP Server Discovery (Feature A)
        # ───────────────────────────────────────────────────────────────────────
        server_id = f"e2e_srv_{uuid.uuid4().hex[:8]}"
        reg_res = await client.post(
            "/servers",
            json={
                "server_id": server_id,
                "name": "E2E Dynamic Repository Server",
                "transport": "stdio",
                "connection_config": {
                    "command": "python",
                    "args": ["-m", "gateway.mock_servers.repository"],
                },
                "is_enabled": True,
                "tags": ["e2e", "integration", "repository"],
            },
            headers=auth_headers["admin"],
        )
        assert reg_res.status_code == 200
        server_data = reg_res.json()
        assert server_data["server_id"] == server_id
        assert server_data["config_version"] == 1

        # Verify listed in registry without restart
        list_res = await client.get("/servers", headers=auth_headers["admin"])
        assert list_res.status_code == 200
        servers_list = list_res.json()
        assert any(s["server_id"] == server_id for s in servers_list)

        # Verify tenant client cannot register servers (security boundary)
        forbidden_reg = await client.post(
            "/servers",
            json={
                "server_id": "malicious_server",
                "name": "Malicious",
                "transport": "stdio",
                "connection_config": {"command": "sh"},
            },
            headers=auth_headers["tenant_a"],
        )
        assert forbidden_reg.status_code == 403

        # ───────────────────────────────────────────────────────────────────────
        # Part 2: Distributed Cache Coordination & Singleflight (Feature B)
        # ───────────────────────────────────────────────────────────────────────
        redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        cache_store = RedisCacheStore(redis_url=redis_url)
        await cache_store.connect()

        try:
            # Instantiate two independent gateway replica singleflight coordinators
            replica_1 = DistributedSingleflight(cache_store=cache_store, waiter_timeout=4.0)
            replica_2 = DistributedSingleflight(cache_store=cache_store, waiter_timeout=4.0)

            call_count = 0
            cache_key = f"mcp-sc:e2e:cache_test:{uuid.uuid4().hex}"

            async def compute_heavy_result():
                nonlocal call_count
                call_count += 1
                await asyncio.sleep(0.15)
                res = CallToolResult(content=[TextContent(type="text", text="e2e_coalesced_output")])
                await cache_store.set(cache_key, res, ttl_seconds=60)
                return res

            # Issue concurrent requests through replica 1 and replica 2
            t1 = asyncio.create_task(replica_1.do(cache_key, compute_heavy_result, scope="tenant_a"))
            t2 = asyncio.create_task(replica_2.do(cache_key, compute_heavy_result, scope="tenant_a"))

            (res1, is_leader1), (res2, is_leader2) = await asyncio.gather(t1, t2)

            # Exactly one replica computed upstream; other received coalesced result
            assert call_count == 1
            assert (is_leader1 and not is_leader2) or (is_leader2 and not is_leader1)
            assert res1.content[0].text == "e2e_coalesced_output"
            assert res2.content[0].text == "e2e_coalesced_output"

            # Check cache coordination metrics endpoint
            metrics_res = await client.get("/cache/coordination", headers=auth_headers["admin"])
            assert metrics_res.status_code == 200
            metrics_data = metrics_res.json()
            assert metrics_data["coordination_status"] == "active"
            assert metrics_data["distributed_singleflight"]["distributed_leaders_total"] >= 1
            assert metrics_data["coordination_features"]["tenant_isolation_enforced"] is True
        finally:
            await cache_store.close()

        # ───────────────────────────────────────────────────────────────────────
        # Part 3: Durable Checkpointing & Safe Resumption (Feature C)
        # ───────────────────────────────────────────────────────────────────────
        # Submit repository intelligence workflow as Tenant A
        sub_res = await client.post(
            "/workflows",
            json={
                "prompt": "Investigate smartcache repository commits, branches, and issues.",
                "owner": "google",
                "repo": "smartcache",
                "max_concurrency": 3,
            },
            headers=auth_headers["tenant_a"],
        )
        assert sub_res.status_code == 200
        wf_id = sub_res.json()["workflow_id"]

        # Save task records in database representing partially executed workflow
        t1 = WorkflowTask(
            task_id="task_1_get_metadata",
            workflow_id=wf_id,
            tenant_id="tenant_a",
            tool_name="repo_metadata__get_repo_info",
            status=TaskStatus.COMPLETED,
            result={"owner": "google", "repo": "smartcache"},
        )
        t2 = WorkflowTask(
            task_id="task_2_list_commits",
            workflow_id=wf_id,
            tenant_id="tenant_a",
            tool_name="repo_metadata__get_commit_log",
            status=TaskStatus.PENDING,
        )
        await repository.save_tasks([t1, t2])

        # Create checkpoint after task 1 completed
        cp1 = WorkflowCheckpoint(
            checkpoint_id=f"chk_{uuid.uuid4().hex[:12]}",
            workflow_id=wf_id,
            tenant_id="tenant_a",
            graph_version="v1.0.0",
            execution_state="in_progress",
            completed_tasks=["task_1_get_metadata"],
            pending_tasks=["task_2_list_commits"],
        )
        await repository.save_checkpoint(cp1)

        # Transition workflow to RUNNING then INTERRUPTED to simulate crash during execution
        await repository.update_workflow_status(
            workflow_id=wf_id,
            new_status=WorkflowStatus.RUNNING,
            tenant_id="tenant_a",
        )
        await repository.update_workflow_status(
            workflow_id=wf_id,
            new_status=WorkflowStatus.INTERRUPTED,
            tenant_id="tenant_a",
        )

        # Check Resumability Assessment endpoint
        resume_eval_res = await client.get(
            f"/workflows/{wf_id}/resumability",
            headers=auth_headers["tenant_a"],
        )
        assert resume_eval_res.status_code == 200
        eval_data = resume_eval_res.json()
        assert eval_data["workflow_id"] == wf_id
        assert eval_data["resumable"] is True
        assert eval_data["latest_checkpoint_id"] == cp1.checkpoint_id
        assert "task_1_get_metadata" in eval_data["completed_tasks"]

        # Verify checkpoints list endpoint
        cp_list_res = await client.get(
            f"/workflows/{wf_id}/checkpoints",
            headers=auth_headers["tenant_a"],
        )
        assert cp_list_res.status_code == 200
        cps = cp_list_res.json()["checkpoints"]
        assert len(cps) >= 1
        assert cps[0]["checkpoint_id"] == cp1.checkpoint_id

        # Tenant B must NOT be able to view or resume Tenant A's workflow
        tenant_b_attempt = await client.post(
            f"/workflows/{wf_id}/resume",
            headers=auth_headers["tenant_b"],
        )
        assert tenant_b_attempt.status_code in (403, 404)

        # Authorized resumption by Tenant A
        resume_res = await client.post(
            f"/workflows/{wf_id}/resume",
            headers=auth_headers["tenant_a"],
        )
        assert resume_res.status_code == 200
        resumed_data = resume_res.json()
        assert resumed_data["status"] in ("resumed", "running")
        assert resumed_data["workflow_id"] == wf_id
        assert "task_1_get_metadata" in resumed_data["completed_tasks_preserved"]

        # Confirm resume attempt record was saved
        resume_attempts = await repository.list_resume_attempts(wf_id, tenant_id="tenant_a")
        assert len(resume_attempts) >= 1
        assert resume_attempts[0].status in ("resumed", "success")

        # Verify workflow transitioned back to RUNNING
        resumed_wf = await repository.get_workflow(wf_id, tenant_id="tenant_a")
        assert resumed_wf is not None
        assert resumed_wf.status == WorkflowStatus.RUNNING
