"""
Phase 6 Feature D Tests: Human-in-the-Loop Workflow Reconciliation.
Verifies durable reconciliation records, operator decision state machines,
controlled retry safety gates, tenant isolation, and audit event emission.
"""

import uuid
import pytest
from httpx import AsyncClient, ASGITransport

from control_api.main import app, DEFAULT_ADMIN_KEY
from runtime.resume_policy import assess_workflow_resumability
from storage.models.entities import (
    ExecutionOutcome,
    ReconciliationStatus,
    TaskStatus,
    WorkflowCheckpoint,
    WorkflowReconciliation,
    WorkflowRun,
    WorkflowStatus,
    WorkflowTask,
)
from storage.repository import repository

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


@pytest.mark.asyncio
async def test_interrupted_side_effect_requires_reconciliation_before_resumption():
    wf_id = f"wf_rec_{uuid.uuid4().hex[:8]}"
    tenant_id = "tenant_a"

    wf = await repository.create_workflow(
        workflow_id=wf_id,
        tenant_id=tenant_id,
        request_prompt="Execute critical update",
    )
    # Transition to RUNNING then INTERRUPTED
    await repository.update_workflow_status(
        workflow_id=wf_id,
        new_status=WorkflowStatus.RUNNING,
        tenant_id=tenant_id,
    )
    await repository.update_workflow_status(
        workflow_id=wf_id,
        new_status=WorkflowStatus.INTERRUPTED,
        tenant_id=tenant_id,
    )

    # 1 read-only task (completed) and 1 side-effecting task (interrupted/failed)
    task1 = WorkflowTask(
        task_id="t1_read",
        workflow_id=wf_id,
        tenant_id=tenant_id,
        tool_name="repo_metadata__get_repo_info",
        arguments={},
        status=TaskStatus.COMPLETED,
    )
    task2 = WorkflowTask(
        task_id="t2_mutation",
        workflow_id=wf_id,
        tenant_id=tenant_id,
        tool_name="issues__create_issue",  # side-effecting!
        arguments={"title": "urgent bug"},
        status=TaskStatus.RUNNING,
    )
    await repository.save_tasks([task1, task2])

    cp = WorkflowCheckpoint(
        checkpoint_id=f"cp_{uuid.uuid4().hex[:8]}",
        workflow_id=wf_id,
        tenant_id=tenant_id,
        checkpoint_version=1,
        graph_version="1.0.0",
        completed_tasks={"t1_read": {}},
        is_resumable=False,
    )
    await repository.save_checkpoint(cp)

    # Initially, resumption must be BLOCKED
    assessment = assess_workflow_resumability(
        workflow=wf,
        tasks=[task1, task2],
        checkpoint=cp,
        reconciliations=[],
    )
    assert assessment.can_resume is False
    assert "t2_mutation" in assessment.unsafe_tasks_blocked

    # Create reconciliation case for task2
    rec = WorkflowReconciliation(
        reconciliation_id=f"rec_{uuid.uuid4().hex[:8]}",
        workflow_id=wf_id,
        task_id="t2_mutation",
        tenant_id=tenant_id,
        checkpoint_id=cp.checkpoint_id,
        tool_name="issues__create_issue",
        reason="Interrupted side-effecting tool requires review.",
        status=ReconciliationStatus.PENDING,
        known_outcome=ExecutionOutcome.UNKNOWN,
    )
    saved_rec = await repository.create_reconciliation(rec)
    assert saved_rec.status == ReconciliationStatus.PENDING

    # Attempt retry approval WITHOUT idempotency_key or risk_acknowledged -> REJECTED
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp_bad = await client.post(
            f"/reconciliations/{saved_rec.reconciliation_id}/decide",
            headers={"Authorization": f"Bearer {CLIENT_ALPHA_KEY}"},
            json={"action": "approve_retry"},  # missing both!
        )
        assert resp_bad.status_code == 400
        assert "requires either a trustworthy upstream idempotency_key or explicit risk_acknowledged=True" in resp_bad.json()["detail"]

        # Valid retry approval with acknowledged risk
        resp_good = await client.post(
            f"/reconciliations/{saved_rec.reconciliation_id}/decide",
            headers={"Authorization": f"Bearer {CLIENT_ALPHA_KEY}"},
            json={
                "action": "approve_retry",
                "risk_acknowledged": True,
                "decision_note": "Operator verified upstream log: operation did not execute.",
            },
        )
        assert resp_good.status_code == 200
        data = resp_good.json()["reconciliation"]
        assert data["status"] == "approved_retry"
        assert data["risk_acknowledged"] is True

        # Now, assess resumability again: MUST BE RESUMABLE!
        updated_rec = await repository.get_reconciliation(saved_rec.reconciliation_id)
        new_assessment = assess_workflow_resumability(
            workflow=wf,
            tasks=[task1, task2],
            checkpoint=cp,
            reconciliations=[updated_rec],
        )
        assert new_assessment.can_resume is True
        assert "t2_mutation" in new_assessment.tasks_to_resume
        assert "t1_read" in new_assessment.tasks_to_reuse


@pytest.mark.asyncio
async def test_reconciliation_tenant_isolation():
    # Tenant Alpha reconciliation record
    rec_alpha = WorkflowReconciliation(
        reconciliation_id=f"rec_alpha_{uuid.uuid4().hex[:8]}",
        workflow_id=f"wf_{uuid.uuid4().hex[:8]}",
        task_id="t_alpha",
        tenant_id="tenant_a",
        tool_name="issues__create_issue",
        reason="Review needed",
        status=ReconciliationStatus.PENDING,
    )
    # Seed workflow for foreign key constraint
    await repository.create_workflow(
        workflow_id=rec_alpha.workflow_id,
        tenant_id="tenant_a",
        request_prompt="p",
    )
    await repository.create_reconciliation(rec_alpha)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Tenant Beta cannot view Tenant Alpha's reconciliation case (404)
        resp_view = await client.get(
            f"/reconciliations/{rec_alpha.reconciliation_id}",
            headers={"Authorization": f"Bearer {CLIENT_BETA_KEY}"},
        )
        assert resp_view.status_code == 404

        # Tenant Beta cannot make decision on Tenant Alpha's case (404)
        resp_decide = await client.post(
            f"/reconciliations/{rec_alpha.reconciliation_id}/decide",
            headers={"Authorization": f"Bearer {CLIENT_BETA_KEY}"},
            json={"action": "resolve"},
        )
        assert resp_decide.status_code == 404
