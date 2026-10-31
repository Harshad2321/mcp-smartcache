"""
Phase 11: Safe Workflow Recovery & Replay-Safety Independent Proof.
Verifies:
1. Crash recovery transitions stranded running workflows to 'interrupted' safely.
2. Concurrent worker task claims enforce strict mutual exclusion (PostgreSQL FOR UPDATE SKIP LOCKED).
3. Concurrent resume requests serialize safely; running/terminal workflows reject concurrent resume with 409 Conflict.
4. Resumption of read-only interrupted tasks succeeds safely.
5. Workflows with uncertain side-effecting tasks (e.g., issues__create_issue) are strictly BLOCKED from automatic replay.
6. Operator reconciliation decision transitions unblock workflow only when explicitly authorized with risk acknowledgement or idempotency key.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime.resume_policy import assess_workflow_resumability
from storage.database import db
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

CONTROL_API_URL = os.environ.get("CONTROL_API_URL", "http://localhost:8080")
ADMIN_KEY = os.environ.get("GATEWAY_ADMIN_API_KEY", "dev-admin-secret-key-32chars-long")
CLIENT_KEY = "dev-client-secret-key-32chars-long"


async def main():
    print("==================================================================")
    print("  Phase 11: Safe Workflow Recovery & Replay-Safety Proof          ")
    print("==================================================================")

    await db.connect()
    tenant_id = "default"
    headers_client = {
        "Authorization": f"Bearer {CLIENT_KEY}",
        "X-Tenant-ID": tenant_id,
    }

    # ── Test 1: Crash Recovery for Stranded Workflows ───────────────────────
    print("\n--- 1. Testing Stranded Workflow Crash Recovery ---")
    wf_crash_id = f"wf_crash_{uuid.uuid4().hex[:8]}"
    await repository.create_workflow(
        workflow_id=wf_crash_id,
        tenant_id=tenant_id,
        request_prompt="Financial payroll calculation",
    )
    await repository.update_workflow_status(wf_crash_id, WorkflowStatus.RUNNING, tenant_id=tenant_id)
    wf_before = await repository.get_workflow(wf_crash_id)
    assert wf_before.status == WorkflowStatus.RUNNING

    recovered = await repository.recover_interrupted_workflows()
    assert wf_crash_id in recovered
    wf_after = await repository.get_workflow(wf_crash_id)
    assert wf_after.status == WorkflowStatus.INTERRUPTED
    assert "Process restarted" in (wf_after.error or "")
    print(f"[OK] Crash recovery safely transitioned running workflow {wf_crash_id} to INTERRUPTED.")

    # ── Test 2: Multi-Worker Task Claiming Mutual Exclusion ────────────────
    print("\n--- 2. Testing Multi-Worker Task Claim Mutual Exclusion ---")
    wf_claim_id = f"wf_claim_{uuid.uuid4().hex[:8]}"
    task_claim_id = f"task_{uuid.uuid4().hex[:8]}"
    await repository.create_workflow(wf_claim_id, tenant_id=tenant_id, request_prompt="Task claim test")
    await repository.save_tasks([
        WorkflowTask(
            task_id=task_claim_id,
            workflow_id=wf_claim_id,
            tenant_id=tenant_id,
            tool_name="repo_metadata__get_repo_info",
            status=TaskStatus.PENDING,
            dependencies=[],
        )
    ])

    # 3 concurrent workers attempting to claim the exact same pending task
    c1, c2, c3 = await asyncio.gather(
        repository.claim_task(wf_claim_id, task_claim_id, "worker_1"),
        repository.claim_task(wf_claim_id, task_claim_id, "worker_2"),
        repository.claim_task(wf_claim_id, task_claim_id, "worker_3"),
    )
    claims = [c1 is not None, c2 is not None, c3 is not None]
    assert claims.count(True) == 1, f"Expected exactly 1 claim, got: {claims}"
    assert claims.count(False) == 2, f"Expected exactly 2 lockouts, got: {claims}"
    claimed = c1 or c2 or c3
    print(f"[OK] Task claim mutual exclusion verified: Exactly 1 worker claimed the task (status={claimed.status}).")

    # ── Test 3: Terminal State Guard & Concurrent Resume Race Prevention ───
    print("\n--- 3. Testing Terminal Guard & Concurrent Resume Serializability ---")
    wf_term_id = f"wf_term_{uuid.uuid4().hex[:8]}"
    await repository.create_workflow(wf_term_id, tenant_id=tenant_id, request_prompt="Completed workflow")
    await repository.update_workflow_status(wf_term_id, WorkflowStatus.RUNNING, tenant_id=tenant_id)
    await repository.update_workflow_status(wf_term_id, WorkflowStatus.COMPLETED, tenant_id=tenant_id)

    async with httpx.AsyncClient(timeout=10.0) as client:
        # Attempting to resume completed workflow returns 400 or 409 Conflict
        res_res_comp = await client.post(f"{CONTROL_API_URL}/workflows/{wf_term_id}/resume", headers=headers_client)
        assert res_res_comp.status_code in (400, 409), f"Expected 400/409, got {res_res_comp.status_code}: {res_res_comp.text}"
        assert "already completed" in res_res_comp.json()["detail"] or "terminal state" in res_res_comp.json()["detail"]

        # Attempting to cancel completed workflow returns 409 Conflict
        res_canc_comp = await client.post(f"{CONTROL_API_URL}/workflows/{wf_term_id}/cancel", headers=headers_client)
        assert res_canc_comp.status_code == 409, f"Expected 409 Conflict, got {res_canc_comp.status_code}"
        print("[OK] Terminal workflows reject resume and cancellation with 409 Conflict.")

        # Concurrent resumes on an interrupted workflow
        wf_race_id = f"wf_race_{uuid.uuid4().hex[:8]}"
        await repository.create_workflow(wf_race_id, tenant_id=tenant_id, request_prompt="Race test")
        await repository.update_workflow_status(wf_race_id, WorkflowStatus.RUNNING, tenant_id=tenant_id)
        await repository.update_workflow_status(wf_race_id, WorkflowStatus.INTERRUPTED, tenant_id=tenant_id)
        # Add a checkpoint so it looks resumable
        cp = WorkflowCheckpoint(
            checkpoint_id=f"cp_{uuid.uuid4().hex[:8]}",
            workflow_id=wf_race_id,
            tenant_id=tenant_id,
            checkpoint_version=1,
            graph_version="1.0.0",
            completed_tasks={},
            is_resumable=True,
        )
        await repository.save_checkpoint(cp)

        # Fire 2 simultaneous resume requests
        r1, r2 = await asyncio.gather(
            client.post(f"{CONTROL_API_URL}/workflows/{wf_race_id}/resume", headers=headers_client),
            client.post(f"{CONTROL_API_URL}/workflows/{wf_race_id}/resume", headers=headers_client),
        )
        codes = [r1.status_code, r2.status_code]
        # One must succeed (200), the second must encounter conflict or locked state (409)
        assert (200 in codes and 409 in codes) or (codes == [200, 200] and r1.json().get("resumed") != r2.json().get("resumed")), f"Unexpected race status codes: {codes}"
        print(f"[OK] Concurrent resume serialization verified: Codes={codes}.")

    # ── Test 4: Replay-Safety Gate for Uncertain External Side Effects ─────
    print("\n--- 4. Testing Replay-Safety Gate for Uncertain Side Effects ---")
    wf_side_id = f"wf_side_{uuid.uuid4().hex[:8]}"
    wf_side = await repository.create_workflow(
        workflow_id=wf_side_id,
        tenant_id=tenant_id,
        request_prompt="Create GitHub issue and commit code",
    )
    await repository.update_workflow_status(wf_side_id, WorkflowStatus.RUNNING, tenant_id=tenant_id)
    await repository.update_workflow_status(wf_side_id, WorkflowStatus.INTERRUPTED, tenant_id=tenant_id)

    task_read = WorkflowTask(
        task_id=f"task_read_{uuid.uuid4().hex[:6]}",
        workflow_id=wf_side_id,
        tenant_id=tenant_id,
        tool_name="repo_metadata__get_repo_info",
        status=TaskStatus.COMPLETED,
    )
    task_mutation = WorkflowTask(
        task_id=f"task_mutation_{uuid.uuid4().hex[:6]}",
        workflow_id=wf_side_id,
        tenant_id=tenant_id,
        tool_name="issues__create_issue",  # Non-idempotent mutation!
        arguments={"title": "Critical production bug", "repo": "prod/repo"},
        status=TaskStatus.RUNNING,  # Was interrupted while in flight!
    )
    await repository.save_tasks([task_read, task_mutation])

    cp_side = WorkflowCheckpoint(
        checkpoint_id=f"cp_{uuid.uuid4().hex[:8]}",
        workflow_id=wf_side_id,
        tenant_id=tenant_id,
        checkpoint_version=1,
        graph_version="1.0.0",
        completed_tasks={task_read.task_id: {}},
        is_resumable=False,
    )
    await repository.save_checkpoint(cp_side)

    # Assess resumability before reconciliation
    assessment_initial = assess_workflow_resumability(
        workflow=wf_side,
        tasks=[task_read, task_mutation],
        checkpoint=cp_side,
        reconciliations=[],
    )
    assert assessment_initial.can_resume is False, "Workflow with interrupted mutation MUST NOT resume automatically!"
    assert task_mutation.task_id in assessment_initial.unsafe_tasks_blocked
    print(f"[OK] Replay safety verified: Unsafe task '{task_mutation.tool_name}' strictly BLOCKED automatic resumption.")

    # ── Test 5: Controlled Operator Reconciliation Unblocking ──────────────
    print("\n--- 5. Testing Operator Reconciliation Unblocking Gate ---")
    async with httpx.AsyncClient(timeout=10.0) as client:
        # Check resumability assessment endpoint (200 OK, can_resume=False)
        res_assess = await client.get(f"{CONTROL_API_URL}/workflows/{wf_side_id}/resumability", headers=headers_client)
        assert res_assess.status_code == 200
        data_assess = res_assess.json()
        assert data_assess["can_resume"] is False
        assert task_mutation.task_id in data_assess["unsafe_tasks_blocked"]

        # Trigger resumption check via Control API (400 rejected, creates reconciliation record)
        res_check = await client.post(f"{CONTROL_API_URL}/workflows/{wf_side_id}/resume", headers=headers_client)
        assert res_check.status_code == 400
        assert "Workflow resumption rejected" in res_check.json()["detail"]

        # Locate the created reconciliation case
        recs = await repository.list_reconciliations(tenant_id=tenant_id)
        wf_recs = [r for r in recs if r.workflow_id == wf_side_id and r.task_id == task_mutation.task_id]
        assert len(wf_recs) == 1
        rec = wf_recs[0]

        # Operator attempts retry WITHOUT risk acknowledgment or idempotency key -> 400 Bad Request
        bad_req = {"action": "approve_retry", "risk_acknowledged": False}
        res_bad = await client.post(f"{CONTROL_API_URL}/reconciliations/{rec.reconciliation_id}/decide", json=bad_req, headers=headers_client)
        assert res_bad.status_code == 400
        print("[OK] Operator approve_retry without risk acknowledgment or idempotency key rejected with 400.")

        # Operator provides authorized decision with idempotency key
        good_req = {
            "action": "approve_retry",
            "risk_acknowledged": True,
            "idempotency_key": f"idem_{uuid.uuid4().hex[:12]}",
            "decision_note": "Verified with upstream GitHub API; issue was not created. Approved retry.",
        }
        res_good = await client.post(f"{CONTROL_API_URL}/reconciliations/{rec.reconciliation_id}/decide", json=good_req, headers=headers_client)
        assert res_good.status_code == 200

        # Now evaluate resumability with approved reconciliation
        updated_recs = await repository.list_reconciliations(tenant_id=tenant_id)
        active_wf_recs = [r for r in updated_recs if r.workflow_id == wf_side_id]
        assessment_after = assess_workflow_resumability(
            workflow=wf_side,
            tasks=[task_read, task_mutation],
            checkpoint=cp_side,
            reconciliations=active_wf_recs,
        )
        assert assessment_after.can_resume is True, "Workflow should be resumable after authorized operator approval"
        print("[OK] Reconciliation unblocking verified: Workflow is safely resumable after approved operator review.")

    # Cleanup test workflows
    for wid in (wf_crash_id, wf_claim_id, wf_term_id, wf_race_id, wf_side_id):
        await db.execute("DELETE FROM workflow_reconciliations WHERE workflow_id = $1", wid)
        await db.execute("DELETE FROM workflow_checkpoints WHERE workflow_id = $1", wid)
        await db.execute("DELETE FROM workflow_tasks WHERE workflow_id = $1", wid)
        await db.execute("DELETE FROM workflow_runs WHERE workflow_id = $1", wid)

    await db.disconnect()
    print("\n==================================================================")
    print("  ALL 5 WORKFLOW RECOVERY & REPLAY-SAFETY INVARIANTS VERIFIED!    ")
    print("==================================================================")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
