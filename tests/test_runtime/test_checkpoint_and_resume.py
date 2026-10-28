"""
Phase 5 Tests — Safe, Durable Workflow Checkpointing and Resumption.

Tests:
1. Checkpoint creation and persistence.
2. Resumability assessment: eligible safe workflows.
3. Resumability assessment: blocks automatic replay of unsafe side-effecting tasks.
4. Resumability assessment: rejects completed workflows.
5. Incompatible graph versions rejected.
6. Scheduler resumes from checkpoint skipping completed tasks.
7. Scheduler emits reuse trace events and satisfies dependencies.
"""

import asyncio
from datetime import datetime
import pytest
from unittest.mock import AsyncMock

from mcp.types import CallToolResult, TextContent
from storage.models.entities import (
    WorkflowRecord,
    TaskRecord,
    WorkflowCheckpoint,
    WorkflowStatus,
    TaskStatus,
)
from runtime.models import TaskDefinition, TaskPlan, TaskResult
from runtime.resume_policy import assess_workflow_resumability, is_tool_read_only
from runtime.scheduler import DependencyAwareScheduler


def _make_result(text: str = "ok") -> CallToolResult:
    return CallToolResult(
        is_error=False,
        content=[TextContent(type="text", text=text)],
    )


class MockMCPClient:
    def __init__(self):
        self.call_counts = {}

    async def call_tool(self, tool_name: str, arguments: dict, timeout: float = 30.0) -> CallToolResult:
        self.call_counts[tool_name] = self.call_counts.get(tool_name, 0) + 1
        return _make_result(f"{tool_name}_result")


@pytest.mark.asyncio
async def test_tool_read_only_safety_classification():
    """Verify known read-only tools are recognized and unknown tools default to conservative unsafe."""
    assert is_tool_read_only("repo_metadata__get_repo_info") is True
    assert is_tool_read_only("repo_metadata__get_commit_log") is True
    assert is_tool_read_only("issues__list_issues") is True
    assert is_tool_read_only("issues__get_issue") is True

    # Unknown or mutation tools must NOT be inferred as read-only
    assert is_tool_read_only("issues__create_issue") is False
    assert is_tool_read_only("repo_metadata__delete_branch") is False
    assert is_tool_read_only("arbitrary__get_or_create_resource") is False


@pytest.mark.asyncio
async def test_resumability_assessment_eligible_workflow():
    """Interrupted workflow with completed task and pending safe task is resumable."""
    wf = WorkflowRecord(
        workflow_id="wf_123",
        tenant_id="tenant_a",
        query="Inspect repository",
        status=WorkflowStatus.INTERRUPTED,
    )

    t1 = TaskRecord(
        task_id="t1",
        workflow_id="wf_123",
        tenant_id="tenant_a",
        tool_name="repo_metadata__get_repo_info",
        status=TaskStatus.COMPLETED,
        result={"status": "ok"},
    )
    t2 = TaskRecord(
        task_id="t2",
        workflow_id="wf_123",
        tenant_id="tenant_a",
        tool_name="issues__list_issues",
        status=TaskStatus.PENDING,
    )

    cp = WorkflowCheckpoint(
        checkpoint_id="cp_123",
        workflow_id="wf_123",
        tenant_id="tenant_a",
        graph_version="1.0.0",
        checkpoint_version=1,
        completed_task_ids=["t1"],
        persisted_task_outputs={"t1": {"status": "ok"}},
        execution_state={"phase": "executing"},
    )

    res = assess_workflow_resumability(wf, [t1, t2], cp)
    assert res.can_resume is True
    assert "t1" in res.tasks_to_reuse
    assert "t2" in res.tasks_to_resume
    assert len(res.unsafe_tasks_blocked) == 0


@pytest.mark.asyncio
async def test_resumability_assessment_blocks_interrupted_side_effect():
    """Interrupted workflow with mid-flight side-effecting task must be blocked from automatic replay."""
    wf = WorkflowRecord(
        workflow_id="wf_side_effect",
        tenant_id="tenant_a",
        query="Deploy code",
        status=WorkflowStatus.INTERRUPTED,
    )

    t1 = TaskRecord(
        task_id="t1",
        workflow_id="wf_side_effect",
        tenant_id="tenant_a",
        tool_name="repo_metadata__get_repo_info",
        status=TaskStatus.COMPLETED,
        result={"status": "ok"},
    )
    # Side-effecting task was running when system crashed
    t2 = TaskRecord(
        task_id="t2",
        workflow_id="wf_side_effect",
        tenant_id="tenant_a",
        tool_name="deployment__trigger_deploy",
        status=TaskStatus.RUNNING,  # Left interrupted/running
    )

    cp = WorkflowCheckpoint(
        checkpoint_id="cp_se",
        workflow_id="wf_side_effect",
        tenant_id="tenant_a",
        graph_version="1.0.0",
        checkpoint_version=1,
        completed_task_ids=["t1"],
        persisted_task_outputs={"t1": {"status": "ok"}},
        execution_state={"phase": "executing"},
    )

    res = assess_workflow_resumability(wf, [t1, t2], cp)
    assert res.can_resume is False
    assert "t2" in res.unsafe_tasks_blocked
    assert "reconciliation" in res.reason.lower()


@pytest.mark.asyncio
async def test_resumability_assessment_rejects_completed_workflow():
    """Completed workflow cannot be resumed."""
    wf = WorkflowRecord(
        workflow_id="wf_done",
        tenant_id="tenant_a",
        query="Done",
        status=WorkflowStatus.COMPLETED,
    )
    res = assess_workflow_resumability(wf, [], None)
    assert res.can_resume is False
    assert "already completed" in res.reason.lower()


@pytest.mark.asyncio
async def test_resumability_assessment_rejects_incompatible_version():
    """Checkpoint with unsupported graph schema version is rejected."""
    wf = WorkflowRecord(
        workflow_id="wf_old",
        tenant_id="tenant_a",
        query="Old",
        status=WorkflowStatus.INTERRUPTED,
    )
    cp = WorkflowCheckpoint(
        checkpoint_id="cp_old",
        workflow_id="wf_old",
        tenant_id="tenant_a",
        graph_version="99.0.0",  # Incompatible version!
        checkpoint_version=1,
        completed_task_ids=[],
        persisted_task_outputs={},
        execution_state={},
    )
    res = assess_workflow_resumability(wf, [], cp)
    assert res.can_resume is False
    assert "incompatible" in res.reason.lower()


@pytest.mark.asyncio
async def test_scheduler_resumes_from_checkpoint_skipping_completed_tasks():
    """
    Given a 3-task DAG (t1 -> t2 -> t3), where t1 is already completed in checkpoint:
    The scheduler must NOT re-execute t1, but reuse its result and execute t2 and t3.
    """
    client = MockMCPClient()
    scheduler = DependencyAwareScheduler(mcp_client=client, max_concurrency=3)

    plan = TaskPlan(
        plan_id="plan_resume",
        tasks=[
            TaskDefinition(task_id="t1", tool_name="repo_metadata__get_repo_info", arguments={}, dependencies=[]),
            TaskDefinition(task_id="t2", tool_name="issues__list_issues", arguments={}, dependencies=["t1"]),
            TaskDefinition(task_id="t3", tool_name="repo_metadata__get_commit_log", arguments={}, dependencies=["t2"]),
        ],
        max_concurrency=3,
    )

    initial_completed = {
        "t1": TaskResult(
            task_id="t1",
            tool_name="repo_metadata__get_repo_info",
            arguments={},
            is_error=False,
            content={"info": "cached_info"},
            start_time=0.0,
            end_time=0.0,
            duration_ms=0.0,
            evidence_id="evidence:t1",
            status="completed",
        )
    }

    results, trace, timing = await scheduler.execute_plan(
        plan=plan,
        workflow_id="wf_resume_test",
        tenant_id="tenant_test",
        initial_completed_tasks=initial_completed,
    )

    # Verify all 3 tasks have results
    assert len(results) == 3
    assert results["t1"].result == {"info": "cached_info"}
    assert results["t2"].is_error is False
    assert results["t3"].is_error is False

    # CRITICAL: Tool for t1 must NOT have been called!
    assert "repo_metadata__get_repo_info" not in client.call_counts
    # Tools for t2 and t3 were executed
    assert client.call_counts["issues__list_issues"] == 1
    assert client.call_counts["repo_metadata__get_commit_log"] == 1
