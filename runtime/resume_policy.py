"""
Phase 5: Workflow Checkpoint Resumption Safety Policy.
Distinguishes between read-only, idempotent, and unsafe side-effecting operations.
Ensures interrupted tasks are safely audited and prevents automatic replay of unsafe mutations.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from storage.models.entities import (
    WorkflowRun,
    WorkflowTask,
    WorkflowCheckpoint,
    WorkflowStatus,
    TaskStatus,
    WorkflowReconciliation,
    ReconciliationStatus,
)

# Verified read-only tools that are repeatable without state mutation.
# Per Section 4.3: Do NOT infer safety from tool names containing 'get' or 'read'.
READ_ONLY_TOOLS: Set[str] = {
    "repo_metadata__get_repo_info",
    "repo_metadata__get_commit_log",
    "repo_metadata__get_status",
    "issues__list_issues",
    "issues__get_issue",
    "issues__get_status",
}

SUPPORTED_GRAPH_VERSIONS: Set[str] = {"1.0.0", "v1.0.0"}


@dataclass
class ResumabilityAssessment:
    can_resume: bool
    reason: str
    tasks_to_reuse: List[str] = field(default_factory=list)
    tasks_to_resume: List[str] = field(default_factory=list)
    unsafe_tasks_blocked: List[str] = field(default_factory=list)


def is_tool_read_only(tool_name: str) -> bool:
    """
    Return True only if tool is explicitly verified safe and read-only.
    Never infer safety based on substring matches.
    """
    return tool_name in READ_ONLY_TOOLS


def assess_workflow_resumability(
    workflow: WorkflowRun,
    tasks: List[WorkflowTask],
    checkpoint: Optional[WorkflowCheckpoint],
    reconciliations: Optional[List[WorkflowReconciliation]] = None,
) -> ResumabilityAssessment:
    """
    Evaluate whether an interrupted or failed workflow can safely be resumed.
    Conservative safety rules:
    1. Completed workflows cannot be resumed.
    2. Checkpoint must exist and match a supported graph version.
    3. Completed tasks are safely reused without recomputation.
    4. Pending tasks with read-only tools can be resumed.
    5. Side-effecting tasks interrupted mid-execution require approved reconciliation
       before they can be retried.
    """
    if workflow.status == WorkflowStatus.COMPLETED:
        return ResumabilityAssessment(
            can_resume=False,
            reason="Workflow is already completed.",
        )

    if not checkpoint:
        return ResumabilityAssessment(
            can_resume=False,
            reason="No durable checkpoint found for workflow.",
        )

    if checkpoint.graph_version not in SUPPORTED_GRAPH_VERSIONS:
        return ResumabilityAssessment(
            can_resume=False,
            reason=f"Incompatible checkpoint graph version '{checkpoint.graph_version}'. Supported versions: {sorted(SUPPORTED_GRAPH_VERSIONS)}",
        )

    completed_task_ids = set(checkpoint.completed_task_ids)
    reconciliation_map: Dict[str, WorkflowReconciliation] = {
        r.task_id: r for r in (reconciliations or [])
    }
    tasks_to_reuse: List[str] = []
    tasks_to_resume: List[str] = []
    unsafe_tasks_blocked: List[str] = []

    for t in tasks:
        if t.task_id in completed_task_ids or t.status == TaskStatus.COMPLETED:
            tasks_to_reuse.append(t.task_id)
        else:
            rec = reconciliation_map.get(t.task_id)
            if rec and rec.status == ReconciliationStatus.RESOLVED:
                tasks_to_reuse.append(t.task_id)
            elif rec and rec.status == ReconciliationStatus.APPROVED_RETRY:
                tasks_to_resume.append(t.task_id)
            elif is_tool_read_only(t.tool_name):
                tasks_to_resume.append(t.task_id)
            else:
                unsafe_tasks_blocked.append(t.task_id)

    if unsafe_tasks_blocked:
        return ResumabilityAssessment(
            can_resume=False,
            reason=(
                f"Workflow contains {len(unsafe_tasks_blocked)} unsafe side-effecting task(s) "
                f"({', '.join(unsafe_tasks_blocked)}) that were interrupted. "
                "Automatic replay is blocked to prevent duplicate side effects; requires operator reconciliation."
            ),
            tasks_to_reuse=tasks_to_reuse,
            tasks_to_resume=tasks_to_resume,
            unsafe_tasks_blocked=unsafe_tasks_blocked,
        )

    return ResumabilityAssessment(
        can_resume=True,
        reason="Workflow is eligible for safe resumption from durable checkpoint.",
        tasks_to_reuse=tasks_to_reuse,
        tasks_to_resume=tasks_to_resume,
        unsafe_tasks_blocked=[],
    )
