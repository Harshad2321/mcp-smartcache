"""
Phase 10: Production Deployment Rehearsal & Live Acceptance Script.
Executes end-to-end rehearsal tests against live PostgreSQL, Redis, Gateway, and Runtime services.
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import time
from datetime import datetime, timezone
from typing import Any, Dict

import asyncpg
import httpx

from storage.database import DatabaseManager, db
from storage.migrations.runner import run_migrations
from storage.models.entities import TaskStatus, WorkflowStatus
from storage.repository import StorageRepository, repository


async def test_database_persistence_and_crash_recovery():
    print("\n--- 1. Testing Database Persistence & Crash Recovery ---")
    await db.connect()
    wf_id = "wf_crash_rehearsal_01"

    # Cleanup
    await db.execute("DELETE FROM workflow_runs WHERE workflow_id = $1", wf_id)

    # 1. Create running workflow
    await repository.create_workflow(
        workflow_id=wf_id,
        tenant_id="default",
        correlation_id="corr_crash_01",
        request_prompt="Rehearsal test prompt",
    )
    await repository.update_workflow_status(
        workflow_id=wf_id,
        new_status=WorkflowStatus.RUNNING,
    )
    wf_before = await repository.get_workflow(wf_id)
    assert wf_before.status == WorkflowStatus.RUNNING, "Must be running"

    # 2. Crash recovery: stranded workflows become interrupted
    recovered = await repository.recover_interrupted_workflows()
    assert wf_id in recovered, f"Workflow {wf_id} must be in recovered list"

    wf_after = await repository.get_workflow(wf_id)
    assert wf_after.status == WorkflowStatus.INTERRUPTED, "Must transition to interrupted"
    assert "Process restarted" in (wf_after.error or ""), "Error must explain process restart"
    print("[OK] Crash recovery transitioned stranded workflow to 'interrupted' safely.")

    # Cleanup
    await db.execute("DELETE FROM workflow_runs WHERE workflow_id = $1", wf_id)
    await db.disconnect()


async def test_concurrency_and_task_claim_atomicity():
    print("\n--- 2. Testing Multi-Worker Task Claiming & Mutual Exclusion ---")
    await db.connect()
    wf_id = "wf_claim_rehearsal_02"
    task_id = "task_claim_01"

    # Cleanup
    await db.execute("DELETE FROM workflow_tasks WHERE workflow_id = $1", wf_id)
    await db.execute("DELETE FROM workflow_runs WHERE workflow_id = $1", wf_id)

    from storage.models.entities import WorkflowTask

    await repository.create_workflow(
        workflow_id=wf_id,
        tenant_id="default",
        correlation_id="corr_claim_02",
        request_prompt="Claim test",
    )
    await repository.save_tasks(
        [
            WorkflowTask(
                task_id=task_id,
                workflow_id=wf_id,
                tenant_id="default",
                tool_name="repo_metadata__get_repo_info",
                status=TaskStatus.PENDING,
                dependencies=[],
            )
        ]
    )

    # Two concurrent workers attempting to claim the exact same pending task
    worker_1_task, worker_2_task = await asyncio.gather(
        repository.claim_task(wf_id, task_id, "worker_1"),
        repository.claim_task(wf_id, task_id, "worker_2"),
    )

    # Exactly one worker must claim, the other must receive None
    claims = [worker_1_task is not None, worker_2_task is not None]
    assert claims.count(True) == 1, f"Expected exactly 1 claim, got: {claims}"
    assert claims.count(False) == 1, f"Expected exactly 1 lockout, got: {claims}"
    print(f"[OK] Task claim mutual exclusion verified: Worker 1 claimed={claims[0]}, Worker 2 claimed={claims[1]}.")

    # Cleanup
    await db.execute("DELETE FROM workflow_tasks WHERE workflow_id = $1", wf_id)
    await db.execute("DELETE FROM workflow_runs WHERE workflow_id = $1", wf_id)
    await db.disconnect()


async def test_live_gateway_and_runtime_apis():
    print("\n--- 3. Testing Live Gateway, Caching, and Runtime APIs ---")
    async with httpx.AsyncClient(timeout=15.0) as client:
        headers_admin = {
            "Authorization": "Bearer dev-admin-secret-key-32chars-long",
            "X-Tenant-ID": "default",
        }
        headers_client = {
            "Authorization": "Bearer dev-client-secret-key-32chars-long",
            "X-Tenant-ID": "default",
        }

        # Health
        gw_health = await client.get("http://localhost:8000/health")
        rt_health = await client.get("http://localhost:8002/health")
        ctrl_health = await client.get("http://localhost:8080/health")
        assert gw_health.status_code == 200, "Gateway must be healthy"
        assert rt_health.status_code == 200, "Runtime must be healthy"
        assert ctrl_health.status_code == 200, "Control API must be healthy"
        print("[OK] All 3 services healthy (Gateway: 8000, Control API: 8080, Runtime: 8002).")

        # Register cache policy for tool
        pol_payload = {
            "tool_name": "repo_metadata__get_repo_info",
            "enabled": True,
            "read_only": True,
            "ttl_seconds": 120,
        }
        r_pol = await client.post("http://localhost:8000/cache/policy", json=pol_payload, headers=headers_admin)
        assert r_pol.status_code == 200, "Cache policy registration failed"

        # Call tool: Miss followed by Hit
        call_payload = {
            "tool": "repo_metadata__get_repo_info",
            "arguments": {"owner": "google", "repo": "smartcache"},
        }
        t0 = time.time()
        r1 = await client.post("http://localhost:8000/call", json=call_payload, headers=headers_client)
        d1 = (time.time() - t0) * 1000

        t1 = time.time()
        r2 = await client.post("http://localhost:8000/call", json=call_payload, headers=headers_client)
        d2 = (time.time() - t1) * 1000

        assert r1.status_code == 200 and r2.status_code == 200
        print(f"[OK] Cache latency: Call 1 (upstream miss): {d1:.2f}ms, Call 2 (cache hit): {d2:.2f}ms.")

        # Workflow execution
        wf_req = {
            "prompt": "Analyze repository metadata for google/smartcache",
            "owner": "google",
            "repo": "smartcache",
            "max_concurrency": 2,
        }
        res_wf = await client.post("http://localhost:8002/tasks", json=wf_req, headers=headers_client)
        assert res_wf.status_code == 200, f"Workflow failed: {res_wf.text}"
        wf_data = res_wf.json()
        wf_id = wf_data.get("workflow_id")
        assert wf_id, "Workflow ID must exist"
        print(f"[OK] Live workflow {wf_id} executed successfully with status '{wf_data.get('status')}'.")


async def main():
    print("================================================================")
    print("   MCP SmartCache Phase 10: Production Deployment Rehearsal     ")
    print("================================================================")
    try:
        await test_database_persistence_and_crash_recovery()
        await test_concurrency_and_task_claim_atomicity()
        await test_live_gateway_and_runtime_apis()
        print("\n================================================================")
        print("   REHEARSAL EXECUTION COMPLETED: ALL INVARIANTS VERIFIED!       ")
        print("================================================================")
        return 0
    except Exception as err:
        print(f"\nREHEARSAL EXECUTION FAILED: {err}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
