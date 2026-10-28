"""
PostgreSQL data models for server config, tool policies, run metadata, trace, audit, and checkpoints.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field


class WorkflowStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class TaskStatus(str, Enum):
    PENDING = "pending"
    BLOCKED = "blocked"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


ALLOWED_WORKFLOW_TRANSITIONS = {
    WorkflowStatus.PENDING: {WorkflowStatus.RUNNING, WorkflowStatus.CANCELLED},
    WorkflowStatus.RUNNING: {
        WorkflowStatus.COMPLETED,
        WorkflowStatus.FAILED,
        WorkflowStatus.CANCELLED,
        WorkflowStatus.INTERRUPTED,
    },
    WorkflowStatus.COMPLETED: set(),
    WorkflowStatus.FAILED: {WorkflowStatus.RUNNING},
    WorkflowStatus.CANCELLED: set(),
    WorkflowStatus.INTERRUPTED: {WorkflowStatus.RUNNING},
}


class TenantConfig(BaseModel):
    max_concurrency: int = 4
    max_tasks_per_workflow: int = 15
    rate_limit_rpm: int = 120
    cache_ttl_seconds: int = 300
    allowed_tools: List[str] = Field(default_factory=list)


class Tenant(BaseModel):
    tenant_id: str
    name: str
    is_active: bool = True
    config: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class WorkflowRun(BaseModel):
    workflow_id: str
    tenant_id: str
    correlation_id: Optional[str] = None
    workflow_type: str = "repository_intelligence"
    request_prompt: Optional[str] = None
    status: WorkflowStatus = WorkflowStatus.PENDING
    plan: Optional[Dict[str, Any]] = None
    summary: Optional[Dict[str, Any]] = None
    final_answer: Optional[str] = None
    error: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    duration_ms: Optional[int] = None

    # Convenience aliases for query/prompt consistency
    @property
    def query(self) -> Optional[str]:
        return self.request_prompt


# Alias WorkflowRecord to WorkflowRun
WorkflowRecord = WorkflowRun


class WorkflowTask(BaseModel):
    task_id: str
    workflow_id: str
    tenant_id: str
    tool_name: str
    status: TaskStatus = TaskStatus.PENDING
    dependencies: List[str] = Field(default_factory=list)
    attempt_count: int = 0
    result: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_ms: Optional[int] = None


# Alias TaskRecord to WorkflowTask
TaskRecord = WorkflowTask


class TraceEvent(BaseModel):
    id: Optional[int] = None
    event_id: str
    workflow_id: str
    tenant_id: str
    correlation_id: Optional[str] = None
    event_type: str
    task_id: Optional[str] = None
    timestamp: Union[float, datetime] = Field(default_factory=time.time)
    details: Dict[str, Any] = Field(default_factory=dict)


class AuditEvent(BaseModel):
    id: Optional[int] = None
    event_id: str
    tenant_id: Optional[str] = None
    actor: str
    action: str
    resource_type: str
    resource_id: Optional[str] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    outcome: str = "success"
    metadata: Dict[str, Any] = Field(default_factory=dict)


class UpstreamServer(BaseModel):
    server_id: str
    name: str
    transport: str
    connection_config: Dict[str, Any] = Field(default_factory=dict)
    is_enabled: bool = True
    status: str = "registered"
    config_version: int = 1
    last_health_check: Optional[datetime] = None
    last_error: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class WorkflowCheckpoint(BaseModel):
    id: Optional[int] = None
    checkpoint_id: str
    workflow_id: str
    tenant_id: str
    checkpoint_version: int = 1
    graph_version: str = "1.0.0"
    state_summary: Dict[str, Any] = Field(default_factory=dict)
    completed_tasks: Union[Dict[str, Any], List[str]] = Field(default_factory=dict)
    task_results: Dict[str, Any] = Field(default_factory=dict)
    pending_tasks: List[str] = Field(default_factory=list)
    is_resumable: bool = True
    non_resumable_reason: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Compatibility properties for list-of-ids vs dict
    @property
    def completed_task_ids(self) -> List[str]:
        if isinstance(self.completed_tasks, list):
            return self.completed_tasks
        return list(self.completed_tasks.keys())

    @property
    def persisted_task_outputs(self) -> Dict[str, Any]:
        return self.task_results or self.completed_tasks


class ResumeAttempt(BaseModel):
    id: Optional[int] = None
    attempt_id: str
    workflow_id: str
    tenant_id: str
    checkpoint_id: Optional[str] = None
    actor: str = "system"
    status: str = "initiated"
    reason: Optional[str] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ── Phase 6 Models: Policy Governance & Reconciliation ────────────────────────

class ToolRules(BaseModel):
    allowed_tools: List[str] = Field(default_factory=lambda: ["*"])
    denied_tools: List[str] = Field(default_factory=list)
    read_only_tools: List[str] = Field(default_factory=list)
    default_deny: bool = False
    allow_admin_override: bool = False


class CacheRules(BaseModel):
    enabled: bool = True
    default_ttl_seconds: int = 300
    max_ttl_seconds: int = 3600
    tool_ttls: Dict[str, int] = Field(default_factory=dict)
    max_response_bytes: int = 524288  # 512KB
    bypass_cache: bool = False


class ResourceLimits(BaseModel):
    max_concurrent_upstream: int = 5
    max_concurrent_workflows: int = 3
    max_tasks_per_workflow: int = 20
    max_workflow_duration_seconds: int = 300
    max_input_payload_bytes: int = 1048576   # 1MB
    max_output_payload_bytes: int = 1048576  # 1MB
    rate_limit_rpm: int = 60
    burst_limit: int = 10


class TenantPolicy(BaseModel):
    policy_id: str
    tenant_id: str
    policy_version: int = 1
    is_active: bool = True
    description: Optional[str] = None
    tool_rules: ToolRules = Field(default_factory=ToolRules)
    cache_rules: CacheRules = Field(default_factory=CacheRules)
    resource_limits: ResourceLimits = Field(default_factory=ResourceLimits)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    created_by: str = "system"


class ReconciliationStatus(str, Enum):
    PENDING = "pending"
    APPROVED_RETRY = "approved_retry"
    REJECTED = "rejected"
    RESOLVED = "resolved"


class ExecutionOutcome(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    UNKNOWN = "unknown"


class WorkflowReconciliation(BaseModel):
    reconciliation_id: str
    workflow_id: str
    task_id: str
    tenant_id: str
    checkpoint_id: Optional[str] = None
    tool_name: str
    reason: str
    status: ReconciliationStatus = ReconciliationStatus.PENDING
    known_outcome: ExecutionOutcome = ExecutionOutcome.UNKNOWN
    reviewer: Optional[str] = None
    decision_note: Optional[str] = None
    idempotency_key: Optional[str] = None
    risk_acknowledged: bool = False
    decision_timestamp: Optional[datetime] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

