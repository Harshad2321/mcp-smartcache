"""
Phase 7 Automated Security Regression Test:
Workflow cancellation tenant isolation, race condition prevention on resume,
whitespace-only idempotency key rejection, and conflicting decision guards.
"""

import json
import os
import uuid
import pytest
from httpx import AsyncClient, ASGITransport

from control_api.main import app as control_api_app
from storage.database import db
from storage.models.entities import (
    ExecutionOutcome,
    ReconciliationStatus,
    Tenant,
    WorkflowReconciliation,
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
    # Ensure active test tenants exist in DB
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


@pytest.mark.asyncio
async def test_workflow_cancellation_tenant_isolation(auth_headers):
    """Tenant B cannot cancel a workflow owned by Tenant A."""
    wf_id = f"wf_cancel_iso_{uuid.uuid4().hex[:8]}"
    await repository.create_workflow(
        workflow_id=wf_id,
        tenant_id="tenant_a",
        request_prompt="Critical financial calculation",
    )
    await repository.update_workflow_status(
        workflow_id=wf_id,
        new_status=WorkflowStatus.RUNNING,
        tenant_id="tenant_a",
    )

    transport = ASGITransport(app=control_api_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Attacker (tenant_b) attempts to cancel tenant_a workflow -> 404 access denied
        resp_b = await client.post(f"/workflows/{wf_id}/cancel", headers=auth_headers["tenant_b"])
        assert resp_b.status_code == 404

        # 2. Legitimate owner (tenant_a) cancels workflow -> 200 OK
        resp_a = await client.post(f"/workflows/{wf_id}/cancel", headers=auth_headers["tenant_a"])
        assert resp_a.status_code == 200
        data = resp_a.json()
        assert data["cancelled"] is True
        assert data["status"] == "cancelled"

        # 3. Cancelling an already cancelled workflow returns 409 Conflict
        resp_again = await client.post(f"/workflows/{wf_id}/cancel", headers=auth_headers["tenant_a"])
        assert resp_again.status_code == 409
        assert "already in terminal state" in resp_again.json()["detail"]


@pytest.mark.asyncio
async def test_workflow_resume_prevents_concurrent_runs(auth_headers):
    """Resuming a workflow currently in RUNNING state returns 409 Conflict to prevent race conditions."""
    wf_id = f"wf_running_{uuid.uuid4().hex[:8]}"
    await repository.create_workflow(
        workflow_id=wf_id,
        tenant_id="tenant_a",
        request_prompt="Active workflow run",
    )
    await repository.update_workflow_status(
        workflow_id=wf_id,
        new_status=WorkflowStatus.RUNNING,
        tenant_id="tenant_a",
    )

    transport = ASGITransport(app=control_api_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(f"/workflows/{wf_id}/resume", headers=auth_headers["tenant_a"])
        assert resp.status_code == 409
        assert "currently running" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_reconciliation_whitespace_idempotency_key_rejected(auth_headers):
    """Whitespace-only idempotency key is rejected when risk_acknowledged=False."""
    wf_id = f"wf_rec_{uuid.uuid4().hex[:8]}"
    await repository.create_workflow(
        workflow_id=wf_id,
        tenant_id="tenant_a",
        request_prompt="Payment reconciliation workflow",
    )
    rec_id = f"rec_white_{uuid.uuid4().hex[:8]}"
    await repository.create_reconciliation(
        WorkflowReconciliation(
            reconciliation_id=rec_id,
            workflow_id=wf_id,
            task_id="tsk_charge_card",
            tenant_id="tenant_a",
            tool_name="stripe.charge",
            reason="Blocked side-effecting task",
            status=ReconciliationStatus.PENDING,
            known_outcome=ExecutionOutcome.UNKNOWN,
        )
    )

    transport = ASGITransport(app=control_api_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            f"/reconciliations/{rec_id}/decide",
            headers=auth_headers["tenant_a"],
            json={
                "action": "approve_retry",
                "idempotency_key": "    ",  # Whitespace bypass attempt
                "risk_acknowledged": False,
            },
        )
        assert resp.status_code == 400
        assert "requires either a trustworthy upstream idempotency_key" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_reconciliation_conflicting_decision_rejected(auth_headers):
    """Deciding an already resolved or rejected reconciliation case fails with 409 Conflict."""
    wf_id = f"wf_rec2_{uuid.uuid4().hex[:8]}"
    await repository.create_workflow(
        workflow_id=wf_id,
        tenant_id="tenant_a",
        request_prompt="Notification workflow",
    )
    rec_id = f"rec_term_{uuid.uuid4().hex[:8]}"
    await repository.create_reconciliation(
        WorkflowReconciliation(
            reconciliation_id=rec_id,
            workflow_id=wf_id,
            task_id="tsk_send_email",
            tenant_id="tenant_a",
            tool_name="sendgrid.mail",
            reason="Interrupted task",
            status=ReconciliationStatus.RESOLVED,
            known_outcome=ExecutionOutcome.SUCCESS,
        )
    )

    transport = ASGITransport(app=control_api_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            f"/reconciliations/{rec_id}/decide",
            headers=auth_headers["tenant_a"],
            json={"action": "approve_retry", "risk_acknowledged": True},
        )
        assert resp.status_code == 409
        assert "terminal state" in resp.json()["detail"]


def test_sensitive_data_sanitization_patterns():
    """Verify regex-based string masking for bearer tokens and AWS access keys."""
    raw_payload = {
        "regular_text": "Normal user query",
        "nested_data": {
            "auth_header": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.dummy_signature",
            "aws_creds": "Found AWS key AKIAIOSFODNN7EXAMPLE in log output",
            "client_secret": "super_secret_client_token",
            "refresh_token": "rt_9876543210fedcba",
        },
    }

    cleaned = sanitize_data(raw_payload)
    assert cleaned["nested_data"]["client_secret"] == "[REDACTED]"
    assert cleaned["nested_data"]["refresh_token"] == "[REDACTED]"
    assert "Bearer [REDACTED]" in cleaned["nested_data"]["auth_header"]
    assert "[REDACTED_AWS_KEY]" in cleaned["nested_data"]["aws_creds"]
    assert "AKIAIOSFODNN7EXAMPLE" not in cleaned["nested_data"]["aws_creds"]
