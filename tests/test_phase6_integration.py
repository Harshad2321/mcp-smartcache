"""
Phase 6 End-to-End Integration Test Suite.
Verifies full lifecycle:
1. Dynamic policy upsert and activation on Control API.
2. Gateway tool invocation enforcing the activated policy.
3. Distributed quota enforcement returning HTTP 429.
4. Interrupted side-effecting workflow entering reconciliation.
5. Operator decision with acknowledged risk enabling safe resumption.
"""

import json
import uuid
import pytest
from httpx import AsyncClient, ASGITransport

from control_api.main import app as control_app, DEFAULT_ADMIN_KEY
from gateway.main import app as gateway_app
from storage.repository import repository
from storage.models.entities import (
    ExecutionOutcome,
    ReconciliationStatus,
    TaskStatus,
    WorkflowCheckpoint,
    WorkflowRun,
    WorkflowStatus,
    WorkflowTask,
)

from gateway.server import GatewayService
from gateway.registry import ServerConfig
from mocks.repo_metadata import MockRepoMetadataServer
from mocks.issues import MockIssuesServer
import gateway.main as gw_main

CLIENT_ALPHA_KEY = "alpha-test-token-secret-32chars"
CLIENT_BETA_KEY = "beta-test-token-secret-32chars"


@pytest.fixture(autouse=True)
def setup_auth_env(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("ENABLE_DEV_MOCKS", "true")
    monkeypatch.setenv("GATEWAY_ADMIN_API_KEY", DEFAULT_ADMIN_KEY)
    monkeypatch.setenv(
        "CLIENT_TENANT_KEYS",
        f'{{"{CLIENT_ALPHA_KEY}": "tenant_a", "{CLIENT_BETA_KEY}": "tenant_b"}}',
    )


@pytest.mark.asyncio
async def test_phase6_end_to_end_governance_quotas_and_reconciliation():
    if gw_main.gateway_service is None:
        gw_main.gateway_service = GatewayService()
        await gw_main.gateway_service.registry.register_server(
            ServerConfig(name="repo_metadata", transport="in-process", server_instance=MockRepoMetadataServer())
        )
        await gw_main.gateway_service.registry.register_server(
            ServerConfig(name="issues", transport="in-process", server_instance=MockIssuesServer())
        )

    control_transport = ASGITransport(app=control_app)
    gateway_transport = ASGITransport(app=gateway_app)

    async with AsyncClient(transport=control_transport, base_url="http://control-api") as ctrl_client, \
               AsyncClient(transport=gateway_transport, base_url="http://gateway") as gw_client:

        # ── 1. Create and Activate Strict Policy for Tenant Alpha ──────────────
        policy_id = f"pol_alpha_strict_{uuid.uuid4().hex[:6]}"
        resp_pol = await ctrl_client.post(
            "/policies",
            headers={"Authorization": f"Bearer {DEFAULT_ADMIN_KEY}"},
            json={
                "policy_id": policy_id,
                "tenant_id": "tenant_a",
                "policy_version": 3,
                "is_active": True,
                "description": "Strict E2E policy for Alpha",
                "tool_rules": {
                    "allowed_tools": ["repo_metadata__get_repo_info"],
                    "denied_tools": ["issues__*"],
                    "read_only_tools": ["repo_metadata__get_repo_info"],
                    "default_deny": True,
                },
                "cache_rules": {
                    "enabled": True,
                    "default_ttl_seconds": 300,
                    "max_ttl_seconds": 3600,
                },
                "resource_limits": {
                    "max_concurrent_upstream": 5,
                    "rate_limit_rpm": 2,
                    "burst_limit": 1,
                    "max_input_payload_bytes": 1048576,
                    "max_output_payload_bytes": 1048576,
                },
            },
        )
        assert resp_pol.status_code == 200
        assert resp_pol.json()["status"] == "saved"

        # ── 2. Gateway Tool Calls: Allowed vs Denied ───────────────────────────
        # Allowed tool: repo_metadata__get_repo_info
        resp_call1 = await gw_client.post(
            "/call",
            headers={"Authorization": f"Bearer {CLIENT_ALPHA_KEY}"},
            json={"tool": "repo_metadata__get_repo_info", "arguments": {"owner": "google", "repo": "smartcache"}},
        )
        assert resp_call1.status_code == 200
        assert resp_call1.json()["is_error"] is False

        # Denied tool: issues__get_issue (explicitly denied by policy)
        resp_call_denied = await gw_client.post(
            "/call",
            headers={"Authorization": f"Bearer {CLIENT_ALPHA_KEY}"},
            json={"tool": "issues__get_issue", "arguments": {"owner": "google", "repo": "smartcache", "issue_id": 1}},
        )
        assert resp_call_denied.status_code == 200
        data_denied = resp_call_denied.json()
        assert data_denied["is_error"] is True
        error_detail = json.loads(data_denied["content"][0]["text"])
        assert error_detail["error"] == "policy_denied"

        # ── 3. Distributed Rate Limit Rejection (HTTP 429) ─────────────────────
        # Call 2 allowed
        resp_call2 = await gw_client.post(
            "/call",
            headers={"Authorization": f"Bearer {CLIENT_ALPHA_KEY}"},
            json={"tool": "repo_metadata__get_repo_info", "arguments": {"owner": "google", "repo": "smartcache"}},
        )
        assert resp_call2.status_code == 200

        # Call 4 exceeds limit (max_allowed = rpm + burst = 3) -> HTTP 429 Too Many Requests
        resp_call3 = await gw_client.post(
            "/call",
            headers={"Authorization": f"Bearer {CLIENT_ALPHA_KEY}"},
            json={"tool": "repo_metadata__get_repo_info", "arguments": {"owner": "google", "repo": "smartcache"}},
        )
        assert resp_call3.status_code == 429
        assert "Rate limit exceeded" in resp_call3.json()["detail"]
        assert "Retry-After" in resp_call3.headers

        # ── 4. Workflow Interruption & Reconciliation Lifecycle ────────────────
        wf_id = f"wf_e2e_{uuid.uuid4().hex[:8]}"
        await repository.create_workflow(
            workflow_id=wf_id,
            tenant_id="tenant_a",
            request_prompt="Deploy database migration and update tags",
        )
        await repository.update_workflow_status(workflow_id=wf_id, new_status=WorkflowStatus.RUNNING, tenant_id="tenant_a")
        await repository.update_workflow_status(workflow_id=wf_id, new_status=WorkflowStatus.INTERRUPTED, tenant_id="tenant_a")

        task_mutation = WorkflowTask(
            task_id="t_e2e_deploy",
            workflow_id=wf_id,
            tenant_id="tenant_a",
            tool_name="repo_metadata__deploy_release",  # unsafe side effect
            arguments={"version": "1.0.0"},
            status=TaskStatus.RUNNING,
        )
        await repository.save_tasks([task_mutation])

        cp = WorkflowCheckpoint(
            checkpoint_id=f"cp_e2e_{uuid.uuid4().hex[:8]}",
            workflow_id=wf_id,
            tenant_id="tenant_a",
            checkpoint_version=1,
            graph_version="1.0.0",
            completed_tasks={},
            is_resumable=False,
        )
        await repository.save_checkpoint(cp)

        # Resumption attempt fails and creates a durable reconciliation case
        resp_res_init = await ctrl_client.post(
            f"/workflows/{wf_id}/resume",
            headers={"Authorization": f"Bearer {CLIENT_ALPHA_KEY}"},
        )
        assert resp_res_init.status_code == 400
        assert "Workflow contains 1 unsafe side-effecting task(s)" in resp_res_init.json()["detail"]

        # ── 5. Operator Inspects and Decides on Reconciliation ──────────────────
        resp_recs = await ctrl_client.get(
            "/reconciliations",
            headers={"Authorization": f"Bearer {CLIENT_ALPHA_KEY}"},
        )
        assert resp_recs.status_code == 200
        cases = [c for c in resp_recs.json()["reconciliations"] if c["workflow_id"] == wf_id]
        assert len(cases) == 1
        rec_case = cases[0]
        assert rec_case["status"] == "pending"
        assert rec_case["tool_name"] == "repo_metadata__deploy_release"

        # Operator approves retry with acknowledged risk
        resp_decide = await ctrl_client.post(
            f"/reconciliations/{rec_case['reconciliation_id']}/decide",
            headers={"Authorization": f"Bearer {CLIENT_ALPHA_KEY}"},
            json={
                "action": "approve_retry",
                "risk_acknowledged": True,
                "idempotency_key": "idemp_rel_1.0.0",
                "decision_note": "Verified release bundle integrity; safe to retry deploy.",
            },
        )
        assert resp_decide.status_code == 200
        assert resp_decide.json()["reconciliation"]["status"] == "approved_retry"

        # ── 6. Resumption now succeeds! ────────────────────────────────────────
        resp_res_final = await ctrl_client.post(
            f"/workflows/{wf_id}/resume",
            headers={"Authorization": f"Bearer {CLIENT_ALPHA_KEY}"},
        )
        assert resp_res_final.status_code == 200
        assert resp_res_final.json()["status"] == "running"
        assert resp_res_final.json()["resumed"] is True
        assert "t_e2e_deploy" in resp_res_final.json()["resumed_tasks"]
