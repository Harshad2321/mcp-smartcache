"""
Pydantic models and data structures for the MCP SmartCache Agent Runtime.
Represents task requests, task plans, tool results, execution traces, and responses.
"""

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class WorkflowStatus(str, Enum):
    """Lifecycle status of an agent workflow."""
    PENDING = "pending"
    PLANNING = "planning"
    VALIDATED = "validated"
    EXECUTING = "executing"
    COMPLETED = "completed"
    PARTIAL_SUCCESS = "partial_success"
    FAILED = "failed"


class TaskDefinition(BaseModel):
    """A single discrete task in the execution plan."""
    task_id: str = Field(..., description="Unique identifier for the task within the plan.")
    tool_name: str = Field(..., description="Namespaced tool identifier (e.g. server__tool).")
    arguments: Dict[str, Any] = Field(default_factory=dict, description="Arguments to pass to the tool.")
    dependencies: List[str] = Field(default_factory=list, description="IDs of prerequisite tasks that must complete first.")
    description: str = Field(default="", description="Human-readable description of what this task accomplishes.")
    timeout_seconds: float = Field(default=30.0, ge=0.1, le=300.0, description="Per-task timeout limit in seconds.")


class TaskPlan(BaseModel):
    """A directed acyclic graph (DAG) of tasks to achieve a user objective."""
    plan_id: str = Field(..., description="Unique plan identifier.")
    task_type: str = Field(default="repository_intelligence", description="Category or template of the plan.")
    tasks: List[TaskDefinition] = Field(..., description="List of tasks comprising the plan.")
    max_concurrency: int = Field(default=5, ge=1, le=20, description="Maximum number of tasks to execute concurrently.")
    description: Optional[str] = Field(default=None, description="Overview of the plan strategy.")


class TaskResult(BaseModel):
    """Execution outcome and retained evidence for a completed or failed task."""
    task_id: str
    tool_name: str
    arguments: Dict[str, Any]
    is_error: bool
    content: Any = None
    start_time: float
    end_time: float
    duration_ms: float
    evidence_id: str
    status: str = Field(..., description="completed | failed | blocked | timeout")
    error_message: Optional[str] = None

    @property
    def result(self) -> Any:
        return self.content


class TraceEvent(BaseModel):
    """A single observable timestamped event in the execution trace."""
    event: str
    timestamp: float
    task_id: Optional[str] = None
    details: Dict[str, Any] = Field(default_factory=dict)


class EvidenceItem(BaseModel):
    """Structured evidence citation extracted from tool execution."""
    evidence_id: str
    task_id: str
    tool_name: str
    source_data: Any
    summary: str


class TaskRequest(BaseModel):
    """API request payload to execute an agent task."""
    prompt: str = Field(..., description="Natural language task or investigation request.")
    owner: Optional[str] = Field(default="google", description="Repository owner/organization.")
    repo: Optional[str] = Field(default="smartcache", description="Repository name.")
    max_concurrency: Optional[int] = Field(default=5, ge=1, le=20, description="Max concurrent tool executions.")
    workflow_timeout_seconds: Optional[float] = Field(default=60.0, ge=1.0, le=600.0, description="Total workflow timeout.")
    custom_plan: Optional[TaskPlan] = Field(default=None, description="Optional custom plan to execute directly.")


class TaskResponse(BaseModel):
    """API response returning the workflow outcome, plan, evidence, trace, and final answer."""
    workflow_id: str
    status: WorkflowStatus
    original_request: str
    plan: Optional[TaskPlan] = None
    task_results: Dict[str, TaskResult] = Field(default_factory=dict)
    evidence: Dict[str, EvidenceItem] = Field(default_factory=dict)
    final_answer: Optional[str] = None
    execution_trace: List[TraceEvent] = Field(default_factory=list)
    timing: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None
