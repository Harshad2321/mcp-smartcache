"""
PostgreSQL Storage Repository for MCP SmartCache.
Provides transactional CRUD operations, tenant isolation, trace event streaming persistence,
audit logging, upstream server registry, and durable workflow checkpoints.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from storage.database import DatabaseManager, db
from storage.models.entities import (
    ALLOWED_WORKFLOW_TRANSITIONS,
    AuditEvent,
    CacheRules,
    ExecutionOutcome,
    ReconciliationStatus,
    ResourceLimits,
    ResumeAttempt,
    TaskStatus,
    Tenant,
    TenantPolicy,
    ToolRules,
    TraceEvent,
    UpstreamServer,
    WorkflowCheckpoint,
    WorkflowReconciliation,
    WorkflowRun,
    WorkflowStatus,
    WorkflowTask,
)
from storage.sanitize import bound_json_payload

logger = logging.getLogger(__name__)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class StorageRepository:
    """PostgreSQL data access layer for MCP SmartCache."""

    def __init__(self, database: Optional[DatabaseManager] = None):
        self.db = database or db

    # ── Tenant Management ──────────────────────────────────────────────────────

    async def get_tenant(self, tenant_id: str) -> Optional[Tenant]:
        """Fetch tenant by ID."""
        row = await self.db.fetchrow(
            """
            SELECT tenant_id, name, is_active, config, created_at, updated_at
            FROM tenants
            WHERE tenant_id = $1
            """,
            tenant_id,
        )
        if not row:
            return None
        return Tenant(
            tenant_id=row["tenant_id"],
            name=row["name"],
            is_active=row["is_active"],
            config=row["config"] if isinstance(row["config"], dict) else {},
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    async def list_tenants(self, limit: int = 50, offset: int = 0) -> List[Tenant]:
        """List all tenants with pagination."""
        rows = await self.db.fetch(
            """
            SELECT tenant_id, name, is_active, config, created_at, updated_at
            FROM tenants
            ORDER BY created_at ASC
            LIMIT $1 OFFSET $2
            """,
            limit,
            offset,
        )
        return [
            Tenant(
                tenant_id=r["tenant_id"],
                name=r["name"],
                is_active=r["is_active"],
                config=r["config"] if isinstance(r["config"], dict) else {},
                created_at=r["created_at"],
                updated_at=r["updated_at"],
            )
            for r in rows
        ]

    async def create_tenant(
        self,
        tenant_id: str,
        name: str,
        is_active: bool = True,
        config: Optional[Dict[str, Any]] = None,
    ) -> Tenant:
        """Create or update a tenant record."""
        now = utc_now()
        cfg = bound_json_payload(config or {})
        row = await self.db.fetchrow(
            """
            INSERT INTO tenants (tenant_id, name, is_active, config, created_at, updated_at)
            VALUES ($1, $2, $3, $4, $5, $5)
            ON CONFLICT (tenant_id) DO UPDATE
            SET name = EXCLUDED.name,
                is_active = EXCLUDED.is_active,
                config = EXCLUDED.config,
                updated_at = EXCLUDED.updated_at
            RETURNING tenant_id, name, is_active, config, created_at, updated_at
            """,
            tenant_id,
            name,
            is_active,
            cfg,
            now,
        )
        return Tenant(
            tenant_id=row["tenant_id"],
            name=row["name"],
            is_active=row["is_active"],
            config=row["config"] if isinstance(row["config"], dict) else {},
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    async def update_tenant_config(
        self,
        tenant_id: str,
        config: Dict[str, Any],
    ) -> Optional[Tenant]:
        """Update a tenant's configuration."""
        now = utc_now()
        cfg = bound_json_payload(config)
        row = await self.db.fetchrow(
            """
            UPDATE tenants
            SET config = $2, updated_at = $3
            WHERE tenant_id = $1
            RETURNING tenant_id, name, is_active, config, created_at, updated_at
            """,
            tenant_id,
            cfg,
            now,
        )
        if not row:
            return None
        return Tenant(
            tenant_id=row["tenant_id"],
            name=row["name"],
            is_active=row["is_active"],
            config=row["config"] if isinstance(row["config"], dict) else {},
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    async def set_tenant_active(
        self,
        tenant_id: str,
        is_active: bool,
    ) -> Optional[Tenant]:
        """Enable or disable a tenant."""
        now = utc_now()
        row = await self.db.fetchrow(
            """
            UPDATE tenants
            SET is_active = $2, updated_at = $3
            WHERE tenant_id = $1
            RETURNING tenant_id, name, is_active, config, created_at, updated_at
            """,
            tenant_id,
            is_active,
            now,
        )
        if not row:
            return None
        return Tenant(
            tenant_id=row["tenant_id"],
            name=row["name"],
            is_active=row["is_active"],
            config=row["config"] if isinstance(row["config"], dict) else {},
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # ── Workflow Management ────────────────────────────────────────────────────

    async def create_workflow(
        self,
        workflow_id: str,
        tenant_id: str,
        correlation_id: Optional[str] = None,
        workflow_type: str = "repository_intelligence",
        request_prompt: Optional[str] = None,
        plan: Optional[Dict[str, Any]] = None,
    ) -> WorkflowRun:
        """Create a durable workflow run record in pending state."""
        now = utc_now()
        corr_id = correlation_id or f"req_{uuid.uuid4().hex[:12]}"
        cleaned_plan = bound_json_payload(plan or {})
        cleaned_prompt = request_prompt or ""

        row = await self.db.fetchrow(
            """
            INSERT INTO workflow_runs (
                workflow_id, tenant_id, correlation_id, workflow_type,
                original_request, request_prompt, status, plan, created_at, updated_at
            )
            VALUES ($1, $2, $3, $4, $5, $5, $6, $7, $8, $8)
            RETURNING *
            """,
            workflow_id,
            tenant_id,
            corr_id,
            workflow_type,
            cleaned_prompt,
            WorkflowStatus.PENDING.value,
            cleaned_plan,
            now,
        )
        return self._row_to_workflow(row)

    async def get_workflow(
        self,
        workflow_id: str,
        tenant_id: Optional[str] = None,
    ) -> Optional[WorkflowRun]:
        """
        Retrieve a workflow run by ID.
        If tenant_id is provided, enforces tenant isolation.
        """
        if tenant_id:
            row = await self.db.fetchrow(
                """
                SELECT * FROM workflow_runs
                WHERE workflow_id = $1 AND tenant_id = $2
                """,
                workflow_id,
                tenant_id,
            )
        else:
            row = await self.db.fetchrow(
                "SELECT * FROM workflow_runs WHERE workflow_id = $1",
                workflow_id,
            )
        if not row:
            return None
        return self._row_to_workflow(row)

    async def list_workflows(
        self,
        tenant_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[WorkflowRun]:
        """List workflows with filtering and pagination."""
        query = "SELECT * FROM workflow_runs WHERE 1=1"
        args = []

        if tenant_id:
            args.append(tenant_id)
            query += f" AND tenant_id = ${len(args)}"

        if status:
            args.append(status)
            query += f" AND status = ${len(args)}"

        query += " ORDER BY created_at DESC"
        args.append(limit)
        query += f" LIMIT ${len(args)}"
        args.append(offset)
        query += f" OFFSET ${len(args)}"

        rows = await self.db.fetch(query, *args)
        return [self._row_to_workflow(r) for r in rows]

    async def count_workflows(
        self,
        tenant_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> int:
        """Count total workflows matching filter criteria."""
        query = "SELECT COUNT(*) FROM workflow_runs WHERE 1=1"
        args = []

        if tenant_id:
            args.append(tenant_id)
            query += f" AND tenant_id = ${len(args)}"

        if status:
            args.append(status)
            query += f" AND status = ${len(args)}"

        val = await self.db.fetchval(query, *args)
        return int(val or 0)

    async def update_workflow_status(
        self,
        workflow_id: str,
        new_status: WorkflowStatus,
        started_at: Optional[datetime] = None,
        completed_at: Optional[datetime] = None,
        error: Optional[str] = None,
        final_answer: Optional[str] = None,
        summary: Optional[Dict[str, Any]] = None,
        duration_ms: Optional[int] = None,
        tenant_id: Optional[str] = None,
        expected_status: Optional[Any] = None,
    ) -> Optional[WorkflowRun]:
        """
        Transition workflow to new status with atomic database row-level locking (FOR UPDATE)
        and validation against ALLOWED_WORKFLOW_TRANSITIONS. Prevents race conditions.
        If expected_status is provided, verifies that current status matches before transitioning.
        """
        async with self.db.transaction() as conn:
            if tenant_id:
                curr_row = await conn.fetchrow(
                    "SELECT * FROM workflow_runs WHERE workflow_id = $1 AND tenant_id = $2 FOR UPDATE",
                    workflow_id,
                    tenant_id,
                )
            else:
                curr_row = await conn.fetchrow(
                    "SELECT * FROM workflow_runs WHERE workflow_id = $1 FOR UPDATE",
                    workflow_id,
                )
            if not curr_row:
                return None
            current = self._row_to_workflow(curr_row)

            if expected_status is not None:
                expected_set = (
                    expected_status
                    if isinstance(expected_status, (set, list, tuple, frozenset))
                    else {expected_status}
                )
                if current.status not in expected_set:
                    raise ValueError(
                        f"Workflow '{workflow_id}' status is '{current.status.value}', expected one of {[s.value if hasattr(s, 'value') else str(s) for s in expected_set]}"
                    )

            allowed = ALLOWED_WORKFLOW_TRANSITIONS.get(current.status, set())
            if new_status not in allowed and new_status != current.status:
                raise ValueError(
                    f"Invalid workflow status transition: '{current.status.value}' -> '{new_status.value if isinstance(new_status, WorkflowStatus) else new_status}'"
                )

            now = utc_now()
            effective_started_at = started_at or current.started_at
            if new_status == WorkflowStatus.RUNNING and not effective_started_at:
                effective_started_at = now

            effective_completed_at = completed_at or current.completed_at
            if new_status in {WorkflowStatus.COMPLETED, WorkflowStatus.FAILED, WorkflowStatus.CANCELLED, WorkflowStatus.INTERRUPTED} and not effective_completed_at:
                effective_completed_at = now

            cleaned_summary = bound_json_payload(summary) if summary else current.summary
            cleaned_error = error or current.error
            cleaned_answer = final_answer or current.final_answer

            row = await conn.fetchrow(
                """
                UPDATE workflow_runs
                SET status = $2,
                    started_at = $3,
                    completed_at = $4,
                    duration_ms = $5,
                    error = $6,
                    final_answer = $7,
                    summary = $8,
                    updated_at = $9
                WHERE workflow_id = $1
                RETURNING *
                """,
                workflow_id,
                new_status.value if isinstance(new_status, WorkflowStatus) else str(new_status),
                effective_started_at,
                effective_completed_at,
                duration_ms or current.duration_ms,
                cleaned_error,
                cleaned_answer,
                cleaned_summary,
                now,
            )
            if not row:
                return None
            return self._row_to_workflow(row)

    async def cancel_workflow(
        self,
        workflow_id: str,
        tenant_id: Optional[str] = None,
    ) -> Optional[WorkflowRun]:
        """Cancel a pending or running workflow."""
        return await self.update_workflow_status(
            workflow_id=workflow_id,
            new_status=WorkflowStatus.CANCELLED,
            error="Workflow cancelled by client",
            tenant_id=tenant_id,
        )

    async def recover_interrupted_workflows(self) -> List[str]:
        """
        Startup crash recovery:
        Identifies workflows stranded in 'running' state and safely transitions
        them to 'interrupted' without automatically replaying side-effecting tools.
        """
        now = utc_now()
        stranded_rows = await self.db.fetch(
            "SELECT workflow_id, tenant_id, correlation_id, started_at FROM workflow_runs WHERE status = 'running'"
        )
        recovered_ids = []

        for r in stranded_rows:
            wf_id = r["workflow_id"]
            t_id = r["tenant_id"]
            corr_id = r["correlation_id"]

            await self.db.execute(
                """
                UPDATE workflow_runs
                SET status = 'interrupted',
                    completed_at = $2,
                    updated_at = $2,
                    error = 'Process restarted during execution'
                WHERE workflow_id = $1
                """,
                wf_id,
                now,
            )

            await self.db.execute(
                """
                UPDATE workflow_tasks
                SET status = 'failed',
                    completed_at = $2,
                    error_message = 'Process terminated before task completion'
                WHERE workflow_id = $1 AND status = 'running'
                """,
                wf_id,
                now,
            )

            await self.record_trace_event(
                TraceEvent(
                    event_id=f"evt_{uuid.uuid4().hex[:12]}",
                    workflow_id=wf_id,
                    tenant_id=t_id,
                    correlation_id=corr_id,
                    event_type="workflow.recovery.interrupted",
                    timestamp=now,
                    details={"reason": "Detected stranded running workflow on process startup"},
                )
            )

            await self.record_audit_event(
                AuditEvent(
                    event_id=f"aud_{uuid.uuid4().hex[:12]}",
                    tenant_id=t_id,
                    actor="system_recovery",
                    action="workflow.recovered",
                    resource_type="workflow",
                    resource_id=wf_id,
                    timestamp=now,
                    outcome="interrupted",
                    metadata={"details": "Crash recovery marked workflow as interrupted"},
                )
            )

            recovered_ids.append(wf_id)
            logger.warning("Recovered stranded workflow %s for tenant %s", wf_id, t_id)

        return recovered_ids

    # ── Task Persistence ───────────────────────────────────────────────────────

    async def save_tasks(self, tasks: List[WorkflowTask]) -> None:
        """Batch insert or update workflow tasks."""
        for t in tasks:
            deps = t.dependencies or []
            res = bound_json_payload(t.result) if t.result else None
            err = t.error_message
            await self.db.execute(
                """
                INSERT INTO workflow_tasks (
                    task_id, workflow_id, tenant_id, tool_name,
                    status, dependencies, attempt_count, result,
                    error_message, started_at, completed_at, duration_ms
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                ON CONFLICT (workflow_id, task_id) DO UPDATE
                SET status = EXCLUDED.status,
                    attempt_count = EXCLUDED.attempt_count,
                    result = EXCLUDED.result,
                    error_message = EXCLUDED.error_message,
                    started_at = EXCLUDED.started_at,
                    completed_at = EXCLUDED.completed_at,
                    duration_ms = EXCLUDED.duration_ms
                """,
                t.task_id,
                t.workflow_id,
                t.tenant_id,
                t.tool_name,
                t.status.value if isinstance(t.status, TaskStatus) else str(t.status),
                deps,
                t.attempt_count,
                res,
                err,
                t.started_at,
                t.completed_at,
                t.duration_ms,
            )

    async def update_task_status(
        self,
        workflow_id: str,
        task_id: str,
        status: TaskStatus,
        started_at: Optional[datetime] = None,
        completed_at: Optional[datetime] = None,
        duration_ms: Optional[int] = None,
        result: Optional[Dict[str, Any]] = None,
        error_message: Optional[str] = None,
        attempt_count: Optional[int] = None,
    ) -> Optional[WorkflowTask]:
        """Update individual task status and result."""
        res = bound_json_payload(result) if result else None
        err = error_message
        row = await self.db.fetchrow(
            """
            UPDATE workflow_tasks
            SET status = $3,
                started_at = COALESCE($4, started_at),
                completed_at = COALESCE($5, completed_at),
                duration_ms = COALESCE($6, duration_ms),
                result = COALESCE($7, result),
                error_message = COALESCE($8, error_message),
                attempt_count = COALESCE($9, attempt_count)
            WHERE workflow_id = $1 AND task_id = $2
            RETURNING *
            """,
            workflow_id,
            task_id,
            status.value if isinstance(status, TaskStatus) else str(status),
            started_at,
            completed_at,
            duration_ms,
            res,
            err,
            attempt_count,
        )
        if not row:
            return None
        return self._row_to_task(row)

    async def claim_task(
        self,
        workflow_id: str,
        task_id: str,
        worker_id: Optional[str] = None,
    ) -> Optional[WorkflowTask]:
        """
        Atomically claim a pending task for exclusive execution by a worker.
        Uses conditional UPDATE on status='pending'. If two workers race to claim
        the same task, exactly one worker receives the updated record; the other gets None.
        """
        now = utc_now()
        row = await self.db.fetchrow(
            """
            UPDATE workflow_tasks
            SET status = 'running',
                started_at = $3,
                attempt_count = attempt_count + 1
            WHERE workflow_id = $1 AND task_id = $2 AND status = 'pending'
            RETURNING *
            """,
            workflow_id,
            task_id,
            now,
        )
        if not row:
            return None
        return self._row_to_task(row)

    async def get_workflow_tasks(
        self,
        workflow_id: str,
        tenant_id: Optional[str] = None,
    ) -> List[WorkflowTask]:
        """
        List all tasks for a given workflow in insertion order.
        Enforces tenant isolation if tenant_id is specified.
        """
        if tenant_id:
            rows = await self.db.fetch(
                """
                SELECT * FROM workflow_tasks
                WHERE workflow_id = $1 AND tenant_id = $2
                ORDER BY id ASC
                """,
                workflow_id,
                tenant_id,
            )
        else:
            rows = await self.db.fetch(
                """
                SELECT * FROM workflow_tasks
                WHERE workflow_id = $1
                ORDER BY id ASC
                """,
                workflow_id,
            )
        return [self._row_to_task(r) for r in rows]

    # ── Trace & Audit Persistence ──────────────────────────────────────────────

    async def record_trace_event(self, event: TraceEvent) -> int:
        """Persist a live execution trace event."""
        details = bound_json_payload(event.details or {})
        corr_id = event.correlation_id or f"req_{uuid.uuid4().hex[:12]}"
        ts_val = event.timestamp
        if isinstance(ts_val, datetime):
            ts_val = ts_val.timestamp()
        elif ts_val is None:
            ts_val = time.time()
        row = await self.db.fetchrow(
            """
            INSERT INTO trace_events (
                event_id, workflow_id, tenant_id, correlation_id,
                event_type, task_id, timestamp, details
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            RETURNING id
            """,
            event.event_id,
            event.workflow_id,
            event.tenant_id,
            corr_id,
            event.event_type,
            event.task_id,
            ts_val,
            details,
        )
        return TraceEvent(
            id=int(row["id"]) if row else None,
            event_id=event.event_id,
            workflow_id=event.workflow_id,
            tenant_id=event.tenant_id,
            correlation_id=corr_id,
            event_type=event.event_type,
            task_id=event.task_id,
            timestamp=ts_val,
            details=event.details,
        )

    async def list_trace_events(
        self,
        workflow_id: str,
        tenant_id: Optional[str] = None,
        since_id: Optional[int] = None,
        limit: int = 100,
    ) -> List[TraceEvent]:
        """
        List trace events for a workflow.
        Supports last-event reconnection via since_id.
        Enforces tenant isolation if tenant_id is provided.
        """
        query = "SELECT * FROM trace_events WHERE workflow_id = $1"
        args = [workflow_id]

        if tenant_id:
            args.append(tenant_id)
            query += f" AND tenant_id = ${len(args)}"

        if since_id:
            args.append(since_id)
            query += f" AND id > ${len(args)}"

        query += " ORDER BY id ASC"
        args.append(limit)
        query += f" LIMIT ${len(args)}"

        rows = await self.db.fetch(query, *args)
        return [
            TraceEvent(
                id=r["id"],
                event_id=r["event_id"],
                workflow_id=r["workflow_id"],
                tenant_id=r["tenant_id"],
                correlation_id=r["correlation_id"],
                event_type=r["event_type"],
                task_id=r["task_id"],
                timestamp=r["timestamp"],
                details=r["details"] if isinstance(r["details"], dict) else {},
            )
            for r in rows
        ]

    async def record_audit_event(self, event: AuditEvent) -> AuditEvent:
        """Persist an audit log entry for security and management tracking."""
        meta = bound_json_payload(event.metadata or {})
        now = event.timestamp or utc_now()
        row = await self.db.fetchrow(
            """
            INSERT INTO audit_events (
                event_id, tenant_id, actor, action,
                resource_type, resource_id, timestamp, outcome, metadata
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            RETURNING id
            """,
            event.event_id,
            event.tenant_id,
            event.actor,
            event.action,
            event.resource_type,
            event.resource_id,
            now,
            event.outcome,
            meta,
        )
        return AuditEvent(
            id=int(row["id"]) if row else None,
            event_id=event.event_id,
            tenant_id=event.tenant_id,
            actor=event.actor,
            action=event.action,
            resource_type=event.resource_type,
            resource_id=event.resource_id,
            timestamp=now,
            outcome=event.outcome,
            metadata=event.metadata,
        )

    async def list_audit_events(
        self,
        tenant_id: Optional[str] = None,
        action: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[AuditEvent]:
        """List audit events with tenant scoping and pagination."""
        query = "SELECT * FROM audit_events WHERE 1=1"
        args = []

        if tenant_id:
            args.append(tenant_id)
            query += f" AND tenant_id = ${len(args)}"

        if action:
            args.append(action)
            query += f" AND action = ${len(args)}"

        query += " ORDER BY id DESC"
        args.append(limit)
        query += f" LIMIT ${len(args)}"
        args.append(offset)
        query += f" OFFSET ${len(args)}"

        rows = await self.db.fetch(query, *args)
        return [
            AuditEvent(
                id=r["id"],
                event_id=r["event_id"],
                tenant_id=r["tenant_id"],
                actor=r["actor"],
                action=r["action"],
                resource_type=r["resource_type"],
                resource_id=r["resource_id"],
                timestamp=r["timestamp"],
                outcome=r["outcome"],
                metadata=r["metadata"] if isinstance(r["metadata"], dict) else {},
            )
            for r in rows
        ]

    async def count_audit_events(
        self,
        tenant_id: Optional[str] = None,
        action: Optional[str] = None,
    ) -> int:
        """Count audit records for pagination."""
        query = "SELECT COUNT(*) FROM audit_events WHERE 1=1"
        args = []

        if tenant_id:
            args.append(tenant_id)
            query += f" AND tenant_id = ${len(args)}"

        if action:
            args.append(action)
            query += f" AND action = ${len(args)}"

        val = await self.db.fetchval(query, *args)
        return int(val or 0)

    # ── Feature A: Upstream Servers ────────────────────────────────────────────

    async def create_or_update_server(self, server: UpstreamServer) -> UpstreamServer:
        """Create or update an upstream MCP server record."""
        now = utc_now()
        config_clean = bound_json_payload(server.connection_config)
        row = await self.db.fetchrow(
            """
            INSERT INTO upstream_servers (
                server_id, name, transport, connection_config, is_enabled,
                status, config_version, last_health_check, last_error, tags,
                created_at, updated_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
            ON CONFLICT (server_id) DO UPDATE SET
                name = EXCLUDED.name,
                transport = EXCLUDED.transport,
                connection_config = EXCLUDED.connection_config,
                is_enabled = EXCLUDED.is_enabled,
                status = EXCLUDED.status,
                config_version = upstream_servers.config_version + 1,
                last_health_check = COALESCE(EXCLUDED.last_health_check, upstream_servers.last_health_check),
                last_error = EXCLUDED.last_error,
                tags = EXCLUDED.tags,
                updated_at = EXCLUDED.updated_at
            RETURNING *;
            """,
            server.server_id,
            server.name,
            server.transport,
            config_clean,
            server.is_enabled,
            server.status,
            server.config_version,
            server.last_health_check,
            server.last_error,
            server.tags or [],
            server.created_at or now,
            server.updated_at or now,
        )
        return self._row_to_server(row)

    async def get_server(self, server_id: str) -> Optional[UpstreamServer]:
        """Fetch upstream server by ID."""
        row = await self.db.fetchrow(
            "SELECT * FROM upstream_servers WHERE server_id = $1;",
            server_id,
        )
        if not row:
            return None
        return self._row_to_server(row)

    async def list_servers(self, limit: int = 50, offset: int = 0) -> List[UpstreamServer]:
        """List registered upstream servers ordered by created_at DESC (newest first)."""
        rows = await self.db.fetch(
            "SELECT * FROM upstream_servers ORDER BY created_at DESC LIMIT $1 OFFSET $2;",
            limit,
            offset,
        )
        return [self._row_to_server(r) for r in rows]

    async def update_server_status(
        self,
        server_id: str,
        status: str,
        last_error: Optional[str] = None,
        last_health_check: Optional[datetime] = None,
    ) -> bool:
        """Update operational status and health check timestamp for a server."""
        now = utc_now()
        res = await self.db.execute(
            """
            UPDATE upstream_servers
            SET status = $2,
                last_error = $3,
                last_health_check = COALESCE($4, last_health_check),
                updated_at = $5
            WHERE server_id = $1;
            """,
            server_id,
            status,
            last_error,
            last_health_check,
            now,
        )
        return res == "UPDATE 1"

    async def delete_server(self, server_id: str) -> bool:
        """Delete an upstream server record."""
        res = await self.db.execute(
            "DELETE FROM upstream_servers WHERE server_id = $1;",
            server_id,
        )
        return res == "DELETE 1"

    # ── Feature C: Checkpoints & Resumption ────────────────────────────────────

    async def save_checkpoint(self, checkpoint: WorkflowCheckpoint) -> WorkflowCheckpoint:
        """Persist a durable workflow execution checkpoint."""
        now = utc_now()
        row = await self.db.fetchrow(
            """
            INSERT INTO workflow_checkpoints (
                checkpoint_id, workflow_id, tenant_id, checkpoint_version, graph_version,
                state_summary, completed_tasks, task_results, pending_tasks,
                is_resumable, non_resumable_reason, created_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
            ON CONFLICT (checkpoint_id) DO UPDATE SET
                state_summary = EXCLUDED.state_summary,
                completed_tasks = EXCLUDED.completed_tasks,
                task_results = EXCLUDED.task_results,
                pending_tasks = EXCLUDED.pending_tasks,
                is_resumable = EXCLUDED.is_resumable,
                non_resumable_reason = EXCLUDED.non_resumable_reason
            RETURNING *;
            """,
            checkpoint.checkpoint_id,
            checkpoint.workflow_id,
            checkpoint.tenant_id,
            checkpoint.checkpoint_version,
            checkpoint.graph_version,
            bound_json_payload(checkpoint.state_summary or {}),
            bound_json_payload(checkpoint.completed_tasks or {}),
            bound_json_payload(checkpoint.task_results or {}),
            checkpoint.pending_tasks or [],
            checkpoint.is_resumable,
            checkpoint.non_resumable_reason,
            checkpoint.created_at or now,
        )
        return self._row_to_checkpoint(row)

    async def get_latest_checkpoint(
        self,
        workflow_id: str,
        tenant_id: Optional[str] = None,
    ) -> Optional[WorkflowCheckpoint]:
        """Fetch the most recent checkpoint for a workflow scoped to tenant."""
        if tenant_id:
            row = await self.db.fetchrow(
                """
                SELECT * FROM workflow_checkpoints
                WHERE workflow_id = $1 AND tenant_id = $2
                ORDER BY id DESC
                LIMIT 1;
                """,
                workflow_id,
                tenant_id,
            )
        else:
            row = await self.db.fetchrow(
                """
                SELECT * FROM workflow_checkpoints
                WHERE workflow_id = $1
                ORDER BY id DESC
                LIMIT 1;
                """,
                workflow_id,
            )
        if not row:
            return None
        return self._row_to_checkpoint(row)

    async def list_checkpoints(
        self,
        workflow_id: str,
        tenant_id: Optional[str] = None,
        limit: int = 20,
    ) -> List[WorkflowCheckpoint]:
        """List checkpoints for a workflow in reverse chronological order."""
        if tenant_id:
            rows = await self.db.fetch(
                """
                SELECT * FROM workflow_checkpoints
                WHERE workflow_id = $1 AND tenant_id = $2
                ORDER BY id DESC
                LIMIT $3;
                """,
                workflow_id,
                tenant_id,
                limit,
            )
        else:
            rows = await self.db.fetch(
                """
                SELECT * FROM workflow_checkpoints
                WHERE workflow_id = $1
                ORDER BY id DESC
                LIMIT $2;
                """,
                workflow_id,
                limit,
            )
        return [self._row_to_checkpoint(r) for r in rows]

    async def record_resume_attempt(self, attempt: ResumeAttempt) -> ResumeAttempt:
        """Record an auditable resume attempt for a workflow."""
        now = utc_now()
        row = await self.db.fetchrow(
            """
            INSERT INTO workflow_resume_attempts (
                attempt_id, workflow_id, tenant_id, checkpoint_id,
                actor, status, reason, timestamp
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            RETURNING *;
            """,
            attempt.attempt_id,
            attempt.workflow_id,
            attempt.tenant_id,
            attempt.checkpoint_id,
            attempt.actor,
            attempt.status,
            attempt.reason,
            attempt.timestamp or now,
        )
        return self._row_to_resume_attempt(row)

    async def list_resume_attempts(
        self,
        workflow_id: str,
        tenant_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[ResumeAttempt]:
        """List all resume attempts for a workflow scoped to tenant."""
        if tenant_id:
            rows = await self.db.fetch(
                """
                SELECT * FROM workflow_resume_attempts
                WHERE workflow_id = $1 AND tenant_id = $2
                ORDER BY timestamp DESC
                LIMIT $3;
                """,
                workflow_id,
                tenant_id,
                limit,
            )
        else:
            rows = await self.db.fetch(
                """
                SELECT * FROM workflow_resume_attempts
                WHERE workflow_id = $1
                ORDER BY timestamp DESC
                LIMIT $2;
                """,
                workflow_id,
                limit,
            )
        return [self._row_to_resume_attempt(r) for r in rows]

    # ── Row Mapping Helpers ────────────────────────────────────────────────────

    def _row_to_workflow(self, row) -> WorkflowRun:
        keys = row.keys()
        prompt = row["request_prompt"] if "request_prompt" in keys and row["request_prompt"] is not None else row.get("original_request")
        return WorkflowRun(
            workflow_id=row["workflow_id"],
            tenant_id=row["tenant_id"],
            correlation_id=row["correlation_id"],
            workflow_type=row.get("workflow_type", "repository_intelligence"),
            request_prompt=prompt,
            status=WorkflowStatus(row["status"]),
            plan=row["plan"] if isinstance(row.get("plan"), dict) else {},
            summary=row["summary"] if isinstance(row.get("summary"), dict) else {},
            final_answer=row.get("final_answer"),
            error=row.get("error"),
            created_at=row["created_at"],
            started_at=row.get("started_at"),
            completed_at=row.get("completed_at"),
            updated_at=row["updated_at"],
            duration_ms=row.get("duration_ms"),
        )

    def _row_to_task(self, row) -> WorkflowTask:
        return WorkflowTask(
            task_id=row["task_id"],
            workflow_id=row["workflow_id"],
            tenant_id=row["tenant_id"],
            tool_name=row["tool_name"],
            status=TaskStatus(row["status"]),
            dependencies=row["dependencies"] if isinstance(row.get("dependencies"), list) else [],
            attempt_count=row.get("attempt_count", 0),
            result=row["result"] if isinstance(row.get("result"), dict) else None,
            error_message=row.get("error_message"),
            started_at=row.get("started_at"),
            completed_at=row.get("completed_at"),
            duration_ms=row.get("duration_ms"),
        )

    def _row_to_server(self, row) -> UpstreamServer:
        return UpstreamServer(
            server_id=row["server_id"],
            name=row["name"],
            transport=row["transport"],
            connection_config=row["connection_config"] if isinstance(row["connection_config"], dict) else {},
            is_enabled=row["is_enabled"],
            status=row["status"],
            config_version=row["config_version"],
            last_health_check=row.get("last_health_check"),
            last_error=row.get("last_error"),
            tags=row["tags"] if isinstance(row.get("tags"), list) else [],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _row_to_checkpoint(self, row) -> WorkflowCheckpoint:
        return WorkflowCheckpoint(
            id=row["id"],
            checkpoint_id=row["checkpoint_id"],
            workflow_id=row["workflow_id"],
            tenant_id=row["tenant_id"],
            checkpoint_version=row["checkpoint_version"],
            graph_version=row["graph_version"],
            state_summary=row["state_summary"] if isinstance(row.get("state_summary"), dict) else {},
            completed_tasks=row["completed_tasks"] if isinstance(row.get("completed_tasks"), dict) else {},
            task_results=row["task_results"] if isinstance(row.get("task_results"), dict) else {},
            pending_tasks=row["pending_tasks"] if isinstance(row.get("pending_tasks"), list) else [],
            is_resumable=row["is_resumable"],
            non_resumable_reason=row.get("non_resumable_reason"),
            created_at=row["created_at"],
        )

    def _row_to_resume_attempt(self, row) -> ResumeAttempt:
        return ResumeAttempt(
            id=row["id"],
            attempt_id=row["attempt_id"],
            workflow_id=row["workflow_id"],
            tenant_id=row["tenant_id"],
            checkpoint_id=row.get("checkpoint_id"),
            actor=row["actor"],
            status=row["status"],
            reason=row.get("reason"),
            timestamp=row["timestamp"],
        )

    # ── Phase 6: Tenant Policy Governance ──────────────────────────────────────

    def _row_to_policy(self, row) -> TenantPolicy:
        tool_rules = row["tool_rules"] if isinstance(row.get("tool_rules"), dict) else {}
        cache_rules = row["cache_rules"] if isinstance(row.get("cache_rules"), dict) else {}
        resource_limits = row["resource_limits"] if isinstance(row.get("resource_limits"), dict) else {}
        return TenantPolicy(
            policy_id=row["policy_id"],
            tenant_id=row["tenant_id"],
            policy_version=row["policy_version"],
            is_active=row["is_active"],
            description=row.get("description"),
            tool_rules=ToolRules(**tool_rules),
            cache_rules=CacheRules(**cache_rules),
            resource_limits=ResourceLimits(**resource_limits),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            created_by=row.get("created_by", "system"),
        )

    async def get_active_policy(self, tenant_id: str) -> Optional[TenantPolicy]:
        """Fetch the current active policy for a tenant."""
        row = await self.db.fetchrow(
            """
            SELECT policy_id, tenant_id, policy_version, is_active, description,
                   tool_rules, cache_rules, resource_limits, created_at, updated_at, created_by
            FROM tenant_policies
            WHERE tenant_id = $1 AND is_active = TRUE
            ORDER BY policy_version DESC
            LIMIT 1
            """,
            tenant_id,
        )
        if not row:
            return None
        return self._row_to_policy(row)

    async def get_policy(self, policy_id: str, tenant_id: Optional[str] = None) -> Optional[TenantPolicy]:
        """Fetch a specific policy by policy_id, optionally scoped to tenant_id."""
        query = """
            SELECT policy_id, tenant_id, policy_version, is_active, description,
                   tool_rules, cache_rules, resource_limits, created_at, updated_at, created_by
            FROM tenant_policies
            WHERE policy_id = $1
        """
        args = [policy_id]
        if tenant_id:
            query += " AND tenant_id = $2"
            args.append(tenant_id)
        row = await self.db.fetchrow(query, *args)
        if not row:
            return None
        return self._row_to_policy(row)

    async def list_policies(
        self, tenant_id: Optional[str] = None, limit: int = 50, offset: int = 0
    ) -> List[TenantPolicy]:
        """List policies with optional tenant filter and pagination."""
        if tenant_id:
            rows = await self.db.fetch(
                """
                SELECT policy_id, tenant_id, policy_version, is_active, description,
                       tool_rules, cache_rules, resource_limits, created_at, updated_at, created_by
                FROM tenant_policies
                WHERE tenant_id = $1
                ORDER BY policy_version DESC, created_at DESC
                LIMIT $2 OFFSET $3
                """,
                tenant_id,
                limit,
                offset,
            )
        else:
            rows = await self.db.fetch(
                """
                SELECT policy_id, tenant_id, policy_version, is_active, description,
                       tool_rules, cache_rules, resource_limits, created_at, updated_at, created_by
                FROM tenant_policies
                ORDER BY tenant_id ASC, policy_version DESC
                LIMIT $1 OFFSET $2
                """,
                limit,
                offset,
            )
        return [self._row_to_policy(r) for r in rows]

    async def list_policy_versions(self, tenant_id: str) -> List[TenantPolicy]:
        """List all version records for a given tenant."""
        rows = await self.db.fetch(
            """
            SELECT policy_id, tenant_id, policy_version, is_active, description,
                   tool_rules, cache_rules, resource_limits, created_at, updated_at, created_by
            FROM tenant_policies
            WHERE tenant_id = $1
            ORDER BY policy_version DESC
            """,
            tenant_id,
        )
        return [self._row_to_policy(r) for r in rows]

    async def create_or_update_policy(self, policy: TenantPolicy) -> TenantPolicy:
        """Create or update a policy definition."""
        now = utc_now()
        tool_rules_data = policy.tool_rules.model_dump()
        cache_rules_data = policy.cache_rules.model_dump()
        resource_limits_data = policy.resource_limits.model_dump()

        row = await self.db.fetchrow(
            """
            INSERT INTO tenant_policies (
                policy_id, tenant_id, policy_version, is_active, description,
                tool_rules, cache_rules, resource_limits, created_at, updated_at, created_by
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $9, $10)
            ON CONFLICT (policy_id) DO UPDATE
            SET description = EXCLUDED.description,
                tool_rules = EXCLUDED.tool_rules,
                cache_rules = EXCLUDED.cache_rules,
                resource_limits = EXCLUDED.resource_limits,
                updated_at = EXCLUDED.updated_at
            RETURNING policy_id, tenant_id, policy_version, is_active, description,
                      tool_rules, cache_rules, resource_limits, created_at, updated_at, created_by
            """,
            policy.policy_id,
            policy.tenant_id,
            policy.policy_version,
            policy.is_active,
            policy.description,
            tool_rules_data,
            cache_rules_data,
            resource_limits_data,
            now,
            policy.created_by,
        )
        return self._row_to_policy(row)

    async def activate_policy(self, policy_id: str, tenant_id: str) -> bool:
        """Atomically activate a policy version for a tenant, deactivating all others."""
        async with self.db.transaction() as conn:
            target = await conn.fetchrow(
                "SELECT policy_id FROM tenant_policies WHERE policy_id = $1 AND tenant_id = $2",
                policy_id,
                tenant_id,
            )
            if not target:
                return False
            await conn.execute(
                "UPDATE tenant_policies SET is_active = FALSE, updated_at = NOW() WHERE tenant_id = $1",
                tenant_id,
            )
            await conn.execute(
                "UPDATE tenant_policies SET is_active = TRUE, updated_at = NOW() WHERE policy_id = $1",
                policy_id,
            )
            return True

    # ── Phase 6: Workflow Reconciliation ──────────────────────────────────────

    def _row_to_reconciliation(self, row) -> WorkflowReconciliation:
        return WorkflowReconciliation(
            reconciliation_id=row["reconciliation_id"],
            workflow_id=row["workflow_id"],
            task_id=row["task_id"],
            tenant_id=row["tenant_id"],
            checkpoint_id=row.get("checkpoint_id"),
            tool_name=row["tool_name"],
            reason=row["reason"],
            status=ReconciliationStatus(row["status"]),
            known_outcome=ExecutionOutcome(row["known_outcome"]),
            reviewer=row.get("reviewer"),
            decision_note=row.get("decision_note"),
            idempotency_key=row.get("idempotency_key"),
            risk_acknowledged=row.get("risk_acknowledged", False),
            decision_timestamp=row.get("decision_timestamp"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    async def create_reconciliation(self, rec: WorkflowReconciliation) -> WorkflowReconciliation:
        """Create a new reconciliation record or update if conflict on (workflow_id, task_id)."""
        now = utc_now()
        row = await self.db.fetchrow(
            """
            INSERT INTO workflow_reconciliations (
                reconciliation_id, workflow_id, task_id, tenant_id, checkpoint_id,
                tool_name, reason, status, known_outcome, reviewer, decision_note,
                idempotency_key, risk_acknowledged, decision_timestamp, created_at, updated_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $15)
            ON CONFLICT (workflow_id, task_id) DO UPDATE
            SET reason = EXCLUDED.reason,
                known_outcome = EXCLUDED.known_outcome,
                checkpoint_id = EXCLUDED.checkpoint_id,
                updated_at = EXCLUDED.updated_at
            RETURNING reconciliation_id, workflow_id, task_id, tenant_id, checkpoint_id,
                      tool_name, reason, status, known_outcome, reviewer, decision_note,
                      idempotency_key, risk_acknowledged, decision_timestamp, created_at, updated_at
            """,
            rec.reconciliation_id,
            rec.workflow_id,
            rec.task_id,
            rec.tenant_id,
            rec.checkpoint_id,
            rec.tool_name,
            rec.reason,
            rec.status.value,
            rec.known_outcome.value,
            rec.reviewer,
            rec.decision_note,
            rec.idempotency_key,
            rec.risk_acknowledged,
            rec.decision_timestamp,
            now,
        )
        return self._row_to_reconciliation(row)

    async def get_reconciliation(
        self, reconciliation_id: str, tenant_id: Optional[str] = None
    ) -> Optional[WorkflowReconciliation]:
        """Fetch reconciliation by ID with optional tenant isolation."""
        query = """
            SELECT reconciliation_id, workflow_id, task_id, tenant_id, checkpoint_id,
                   tool_name, reason, status, known_outcome, reviewer, decision_note,
                   idempotency_key, risk_acknowledged, decision_timestamp, created_at, updated_at
            FROM workflow_reconciliations
            WHERE reconciliation_id = $1
        """
        args = [reconciliation_id]
        if tenant_id:
            query += " AND tenant_id = $2"
            args.append(tenant_id)
        row = await self.db.fetchrow(query, *args)
        if not row:
            return None
        return self._row_to_reconciliation(row)

    async def get_reconciliation_by_task(
        self, workflow_id: str, task_id: str
    ) -> Optional[WorkflowReconciliation]:
        """Fetch reconciliation by workflow_id and task_id."""
        row = await self.db.fetchrow(
            """
            SELECT reconciliation_id, workflow_id, task_id, tenant_id, checkpoint_id,
                   tool_name, reason, status, known_outcome, reviewer, decision_note,
                   idempotency_key, risk_acknowledged, decision_timestamp, created_at, updated_at
            FROM workflow_reconciliations
            WHERE workflow_id = $1 AND task_id = $2
            """,
            workflow_id,
            task_id,
        )
        if not row:
            return None
        return self._row_to_reconciliation(row)

    async def list_reconciliations(
        self,
        tenant_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[WorkflowReconciliation]:
        """List reconciliations with optional tenant and status filters."""
        conditions = []
        args = []
        idx = 1
        if tenant_id:
            conditions.append(f"tenant_id = ${idx}")
            args.append(tenant_id)
            idx += 1
        if status:
            conditions.append(f"status = ${idx}")
            args.append(status)
            idx += 1
        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        query = f"""
            SELECT reconciliation_id, workflow_id, task_id, tenant_id, checkpoint_id,
                   tool_name, reason, status, known_outcome, reviewer, decision_note,
                   idempotency_key, risk_acknowledged, decision_timestamp, created_at, updated_at
            FROM workflow_reconciliations
            {where_clause}
            ORDER BY created_at DESC
            LIMIT ${idx} OFFSET ${idx + 1}
        """
        args.extend([limit, offset])
        rows = await self.db.fetch(query, *args)
        return [self._row_to_reconciliation(r) for r in rows]

    async def update_reconciliation_decision(
        self,
        reconciliation_id: str,
        status: ReconciliationStatus,
        reviewer: str,
        decision_note: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        risk_acknowledged: bool = False,
        tenant_id: Optional[str] = None,
    ) -> Optional[WorkflowReconciliation]:
        """Apply an operator decision to a reconciliation case with concurrency and state validation."""
        now = utc_now()
        async with self.db.transaction() as conn:
            # Row lock for concurrency safety
            query = """
                SELECT reconciliation_id, status, tenant_id
                FROM workflow_reconciliations
                WHERE reconciliation_id = $1
                FOR UPDATE
            """
            rec_row = await conn.fetchrow(query, reconciliation_id)
            if not rec_row:
                return None
            if tenant_id and rec_row["tenant_id"] != tenant_id:
                return None

            current_status = rec_row["status"]
            if current_status not in (ReconciliationStatus.PENDING.value, ReconciliationStatus.APPROVED_RETRY.value):
                if current_status == status.value:
                    row = await conn.fetchrow(
                        """
                        SELECT reconciliation_id, workflow_id, task_id, tenant_id, checkpoint_id,
                               tool_name, reason, status, known_outcome, reviewer, decision_note,
                               idempotency_key, risk_acknowledged, decision_timestamp, created_at, updated_at
                        FROM workflow_reconciliations
                        WHERE reconciliation_id = $1
                        """,
                        reconciliation_id,
                    )
                    return self._row_to_reconciliation(row)
                raise ValueError(f"Cannot transition reconciliation from finalized state '{current_status}' to '{status.value}'")

            row = await conn.fetchrow(
                """
                UPDATE workflow_reconciliations
                SET status = $2,
                    reviewer = $3,
                    decision_note = $4,
                    idempotency_key = $5,
                    risk_acknowledged = $6,
                    decision_timestamp = $7,
                    updated_at = $7
                WHERE reconciliation_id = $1
                RETURNING reconciliation_id, workflow_id, task_id, tenant_id, checkpoint_id,
                          tool_name, reason, status, known_outcome, reviewer, decision_note,
                          idempotency_key, risk_acknowledged, decision_timestamp, created_at, updated_at
                """,
                reconciliation_id,
                status.value,
                reviewer,
                decision_note,
                idempotency_key,
                risk_acknowledged,
                now,
            )
            return self._row_to_reconciliation(row)


# Global singleton instance for repository access
repository = StorageRepository(database=db)
