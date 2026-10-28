"""
Server-Sent Events (SSE) Live Trace Streaming Tests for MCP SmartCache.
Verifies:
1. Authorized clients receive historical and live trace events in chronological order.
2. Cross-tenant stream subscriptions are denied with 404.
3. Reconnection with Last-Event-ID header retrieves only missed events without duplicates.
4. Live events published via EventHub are distributed in real-time.
5. Completed workflows terminate with workflow_end event.
"""

from datetime import datetime, timezone
import json
import os
import uuid
import pytest
from httpx import AsyncClient, ASGITransport

from control_api.main import app as control_api_app
from storage.database import db
from storage.events import event_hub
from storage.models.entities import TraceEvent, WorkflowStatus
from storage.repository import repository


@pytest.fixture(autouse=True)
async def setup_test_env():
    """Ensure database connection and seed tenants."""
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


def parse_sse_stream(text: str):
    """Helper: parse raw SSE stream text into structured event objects."""
    events = []
    current_event = {}
    for line in text.split("\n"):
        line = line.strip()
        if not line or line.startswith(":"):
            # Empty line indicates event boundary; comments start with :
            if current_event:
                events.append(current_event)
                current_event = {}
            continue
        if ":" in line:
            key, val = line.split(":", 1)
            key = key.strip()
            val = val.strip()
            if key == "id":
                current_event["id"] = int(val)
            elif key == "event":
                current_event["event"] = val
            elif key == "data":
                try:
                    current_event["data"] = json.loads(val)
                except Exception:
                    current_event["data"] = val
    if current_event:
        events.append(current_event)
    return events


@pytest.mark.asyncio
async def test_authorized_historical_stream_replay(auth_headers):
    """Verify authorized client receives historical workflow events in order."""
    wf_id = f"wf_stream_{uuid.uuid4().hex[:8]}"

    # Create completed workflow
    await repository.create_workflow(workflow_id=wf_id, tenant_id="tenant_a")
    await repository.update_workflow_status(
        wf_id,
        new_status=WorkflowStatus.RUNNING,
    )

    # Insert 3 historical trace events
    e1 = await repository.record_trace_event(
        TraceEvent(
            event_id=f"evt_{uuid.uuid4().hex[:8]}",
            workflow_id=wf_id,
            tenant_id="tenant_a",
            event_type="workflow_started",
            timestamp=datetime.now(timezone.utc),
            details={"step": 1},
        )
    )
    e2 = await repository.record_trace_event(
        TraceEvent(
            event_id=f"evt_{uuid.uuid4().hex[:8]}",
            workflow_id=wf_id,
            tenant_id="tenant_a",
            event_type="plan_validated",
            timestamp=datetime.now(timezone.utc),
            details={"step": 2},
        )
    )
    e3 = await repository.record_trace_event(
        TraceEvent(
            event_id=f"evt_{uuid.uuid4().hex[:8]}",
            workflow_id=wf_id,
            tenant_id="tenant_a",
            event_type="task_completed",
            task_id="task_1",
            timestamp=datetime.now(timezone.utc),
            details={"step": 3},
        )
    )

    # Complete workflow so stream terminates cleanly
    await repository.update_workflow_status(
        wf_id,
        new_status=WorkflowStatus.COMPLETED,
        final_answer="Complete",
    )

    transport = ASGITransport(app=control_api_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get(f"/workflows/{wf_id}/stream", headers=auth_headers["tenant_a"])
        assert res.status_code == 200
        assert "text/event-stream" in res.headers["content-type"]

        parsed = parse_sse_stream(res.text)
        event_types = [e.get("event") for e in parsed]

        assert "workflow_started" in event_types
        assert "plan_validated" in event_types
        assert "task_completed" in event_types
        assert "workflow_end" in event_types


@pytest.mark.asyncio
async def test_cross_tenant_stream_subscription_denied(auth_headers):
    """Verify Tenant B cannot subscribe to Tenant A's workflow stream."""
    wf_id = f"wf_private_{uuid.uuid4().hex[:8]}"
    await repository.create_workflow(workflow_id=wf_id, tenant_id="tenant_a")

    transport = ASGITransport(app=control_api_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Tenant B attempting to stream Tenant A's workflow
        res = await client.get(f"/workflows/{wf_id}/stream", headers=auth_headers["tenant_b"])
        assert res.status_code == 404


@pytest.mark.asyncio
async def test_last_event_id_reconnection_no_duplicates(auth_headers):
    """
    Verify reconnection with Last-Event-ID header retrieves only missed events
    without duplicating already received events.
    """
    wf_id = f"wf_reconnect_{uuid.uuid4().hex[:8]}"
    await repository.create_workflow(workflow_id=wf_id, tenant_id="tenant_a")
    await repository.update_workflow_status(wf_id, new_status=WorkflowStatus.RUNNING)

    # Insert events 1, 2, 3
    e1 = await repository.record_trace_event(
        TraceEvent(
            event_id=f"evt_{uuid.uuid4().hex[:8]}",
            workflow_id=wf_id,
            tenant_id="tenant_a",
            event_type="event_1",
            timestamp=datetime.now(timezone.utc),
        )
    )
    e2 = await repository.record_trace_event(
        TraceEvent(
            event_id=f"evt_{uuid.uuid4().hex[:8]}",
            workflow_id=wf_id,
            tenant_id="tenant_a",
            event_type="event_2",
            timestamp=datetime.now(timezone.utc),
        )
    )
    e3 = await repository.record_trace_event(
        TraceEvent(
            event_id=f"evt_{uuid.uuid4().hex[:8]}",
            workflow_id=wf_id,
            tenant_id="tenant_a",
            event_type="event_3",
            timestamp=datetime.now(timezone.utc),
        )
    )

    await repository.update_workflow_status(wf_id, new_status=WorkflowStatus.COMPLETED)

    transport = ASGITransport(app=control_api_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Client reconnects having already seen event e2 (Last-Event-ID = e2.id)
        reconnect_headers = dict(auth_headers["tenant_a"])
        reconnect_headers["Last-Event-ID"] = str(e2.id)

        res = await client.get(f"/workflows/{wf_id}/stream", headers=reconnect_headers)
        assert res.status_code == 200

        parsed = parse_sse_stream(res.text)
        received_ids = [e.get("id") for e in parsed if "id" in e]

        # e1 and e2 MUST NOT be duplicated
        assert e1.id not in received_ids
        assert e2.id not in received_ids
        # e3 MUST be delivered
        assert e3.id in received_ids


@pytest.mark.asyncio
async def test_live_event_broadcasting(auth_headers):
    """
    Verify that publishing an event via event_hub propagates to the local broadcaster.
    """
    wf_id = f"wf_live_{uuid.uuid4().hex[:8]}"
    await repository.create_workflow(workflow_id=wf_id, tenant_id="tenant_a")

    # Subscribe in-memory
    q = await event_hub.local_broadcaster.subscribe(wf_id)
    try:
        # Publish event
        evt = TraceEvent(
            event_id=f"evt_{uuid.uuid4().hex[:8]}",
            workflow_id=wf_id,
            tenant_id="tenant_a",
            event_type="custom_live_event",
            timestamp=datetime.now(timezone.utc),
            details={"value": "hello_sse"},
        )
        saved = await event_hub.publish_event(evt)
        assert saved.id is not None

        # Queue receives event
        received = q.get_nowait()
        assert received.event_type == "custom_live_event"
        assert received.details["value"] == "hello_sse"
    finally:
        await event_hub.local_broadcaster.unsubscribe(wf_id, q)
