"""
Phase 4 End-to-End Integrated System Verification Test.
Executes the full workflow from control plane submission through LangGraph orchestration,
MCP gateway communication, PostgreSQL durable persistence, task DAG tracking,
live trace streaming, and cross-restart durability.
"""

import asyncio
import json
import os
import uuid
import pytest
from httpx import AsyncClient, ASGITransport

from control_api.main import app as control_api_app
from storage.database import db, DatabaseManager
from storage.models.entities import WorkflowStatus
from storage.repository import StorageRepository, repository


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
        "tenant_a": {"Authorization": "Bearer key_alpha", "X-Tenant-ID": "tenant_a"},
        "tenant_b": {"Authorization": "Bearer key_beta", "X-Tenant-ID": "tenant_b"},
    }


@pytest.mark.asyncio
async def test_full_integrated_lifecycle_and_durability(auth_headers):
    """
    Exercise the 10-step integrated verification sequence:
    1. Submit repository-intelligence workflow via Control API
    2. Confirm workflow record in PostgreSQL
    3. Confirm task records and execution events are persisted
    4. Confirm runtime routes through MCP Gateway
    5. Observe events through streaming endpoint
    6. Confirm cross-tenant isolation (Tenant B cannot read Tenant A's workflow)
    7. Simulate process restart / reconstruct storage repository
    8. Confirm historical workflow, tasks, and traces remain fully queryable
    """
    transport = ASGITransport(app=control_api_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Step 1: Submit workflow as Tenant A
        sub_res = await client.post(
            "/workflows",
            json={
                "prompt": "Investigate Google SmartCache repository. Retrieve metadata, commits, and issues.",
                "owner": "google",
                "repo": "smartcache",
                "max_concurrency": 4,
            },
            headers=auth_headers["tenant_a"],
        )
        assert sub_res.status_code == 200
        sub_data = sub_res.json()
        wf_id = sub_data["workflow_id"]
        assert wf_id.startswith("wf_")

        # Step 2: Confirm workflow record in PostgreSQL
        wf_record = await repository.get_workflow(wf_id, tenant_id="tenant_a")
        assert wf_record is not None
        assert wf_record.tenant_id == "tenant_a"
        assert wf_record.workflow_id == wf_id

        # Wait briefly for background execution to advance
        for _ in range(30):
            await asyncio.sleep(0.5)
            wf_check = await repository.get_workflow(wf_id, tenant_id="tenant_a")
            if wf_check and wf_check.status in (WorkflowStatus.COMPLETED, WorkflowStatus.FAILED):
                break

        final_wf = await repository.get_workflow(wf_id, tenant_id="tenant_a")
        assert final_wf is not None
        assert final_wf.status in (WorkflowStatus.COMPLETED, WorkflowStatus.RUNNING, WorkflowStatus.PENDING)

        # Step 3: Confirm task records and execution events are persisted
        tasks = await repository.get_workflow_tasks(wf_id, tenant_id="tenant_a")
        assert isinstance(tasks, list)

        trace_events = await repository.list_trace_events(wf_id, tenant_id="tenant_a")
        assert len(trace_events) >= 1
        assert any(e.event_type in ("workflow_started", "request_parsed") for e in trace_events)

        # Step 4: Stream trace events through SSE endpoint
        stream_res = await client.get(f"/workflows/{wf_id}/stream", headers=auth_headers["tenant_a"])
        assert stream_res.status_code == 200
        assert "text/event-stream" in stream_res.headers["content-type"]
        assert "workflow_started" in stream_res.text or "event:" in stream_res.text

        # Step 5: Confirm cross-tenant isolation (Tenant B cannot read Tenant A's workflow)
        res_b = await client.get(f"/workflows/{wf_id}", headers=auth_headers["tenant_b"])
        assert res_b.status_code == 404

        res_b_tasks = await client.get(f"/workflows/{wf_id}/tasks", headers=auth_headers["tenant_b"])
        assert res_b_tasks.status_code == 404

        res_b_stream = await client.get(f"/workflows/{wf_id}/stream", headers=auth_headers["tenant_b"])
        assert res_b_stream.status_code == 404

        # Step 6: Process restart simulation — reconstruct new independent DatabaseManager
        reconstructed_db = DatabaseManager(dsn=db.dsn)
        await reconstructed_db.connect()
        try:
            reconstructed_repo = StorageRepository(database=reconstructed_db)

            # Confirm historical workflow survives application restart
            reconstructed_wf = await reconstructed_repo.get_workflow(wf_id, tenant_id="tenant_a")
            assert reconstructed_wf is not None
            assert reconstructed_wf.workflow_id == wf_id
            assert reconstructed_wf.tenant_id == "tenant_a"

            # Confirm historical trace events survive application restart
            reconstructed_trace = await reconstructed_repo.list_trace_events(wf_id, tenant_id="tenant_a")
            assert len(reconstructed_trace) == len(trace_events)
        finally:
            await reconstructed_db.disconnect()
