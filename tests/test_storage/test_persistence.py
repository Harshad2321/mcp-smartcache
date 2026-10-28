"""
PostgreSQL Persistence Tests for MCP SmartCache.
Tests database migrations, tenant storage, workflow lifecycle persistence,
state transition validation, task DAG storage, trace events, audit records,
and crash recovery.
"""

import uuid
from datetime import datetime, timezone
import pytest

from storage.database import db
from storage.migrations.runner import MigrationRunner
from storage.models.entities import (
    ALLOWED_WORKFLOW_TRANSITIONS,
    AuditEvent,
    TaskStatus,
    TraceEvent,
    WorkflowRun,
    WorkflowStatus,
    WorkflowTask,
)
from storage.repository import StorageRepository


@pytest.fixture(autouse=True)
async def setup_db():
    """Ensure clean database connection for each test function's event loop."""
    await db.connect()
    runner = MigrationRunner(db)
    await runner.run_migrations()
    yield
    await db.disconnect()


@pytest.fixture
def repo():
    return StorageRepository(database=db)


@pytest.mark.asyncio
async def test_migration_runner_idempotency():
    """Verify that migrations can be run repeatedly without errors or data loss."""
    runner = MigrationRunner(db)
    applied = await runner.run_migrations()
    assert isinstance(applied, list)
    # Subsequent run should apply 0 migrations
    applied_again = await runner.run_migrations()
    assert applied_again == []


@pytest.mark.asyncio
async def test_tenant_crud_operations(repo: StorageRepository):
    """Verify tenant creation, retrieval, listing, config update, and status toggling."""
    tenant_id = f"test_tenant_{uuid.uuid4().hex[:8]}"
    name = "Acme Corp"
    initial_config = {"max_concurrency": 8, "rate_limit_rpm": 120}

    # Create
    tenant = await repo.create_tenant(
        tenant_id=tenant_id,
        name=name,
        is_active=True,
        config=initial_config,
    )
    assert tenant.tenant_id == tenant_id
    assert tenant.name == name
    assert tenant.is_active is True
    assert tenant.config["max_concurrency"] == 8

    # Get
    fetched = await repo.get_tenant(tenant_id)
    assert fetched is not None
    assert fetched.tenant_id == tenant_id
    assert fetched.name == name

    # Update config
    updated = await repo.update_tenant_config(tenant_id, {"max_concurrency": 12, "rate_limit_rpm": 200})
    assert updated is not None
    assert updated.config["max_concurrency"] == 12

    # Disable tenant
    disabled = await repo.set_tenant_active(tenant_id, is_active=False)
    assert disabled is not None
    assert disabled.is_active is False

    # List tenants
    tenants = await repo.list_tenants(limit=100)
    assert any(t.tenant_id == tenant_id for t in tenants)


@pytest.mark.asyncio
async def test_workflow_lifecycle_and_state_transitions(repo: StorageRepository):
    """Verify workflow creation, valid state transitions, and invalid transition rejection."""
    wf_id = f"wf_{uuid.uuid4().hex[:10]}"
    t_id = "tenant_a"
    corr_id = f"req_{uuid.uuid4().hex[:8]}"

    # 1. Create in PENDING
    wf = await repo.create_workflow(
        workflow_id=wf_id,
        tenant_id=t_id,
        correlation_id=corr_id,
        request_prompt="Analyze smartcache repo",
    )
    assert wf.workflow_id == wf_id
    assert wf.status == WorkflowStatus.PENDING
    assert wf.tenant_id == t_id

    # 2. Transition PENDING -> RUNNING (Valid)
    running_wf = await repo.update_workflow_status(
        workflow_id=wf_id,
        new_status=WorkflowStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
    )
    assert running_wf is not None
    assert running_wf.status == WorkflowStatus.RUNNING
    assert running_wf.started_at is not None

    # 3. Transition RUNNING -> COMPLETED (Valid)
    completed_wf = await repo.update_workflow_status(
        workflow_id=wf_id,
        new_status=WorkflowStatus.COMPLETED,
        final_answer="Repository analysis complete: 128 stars, 32 forks.",
        duration_ms=1500,
    )
    assert completed_wf is not None
    assert completed_wf.status == WorkflowStatus.COMPLETED
    assert completed_wf.final_answer is not None
    assert completed_wf.duration_ms == 1500

    # 4. Attempt Invalid Transition: COMPLETED -> RUNNING (Must be rejected!)
    with pytest.raises(ValueError, match="Invalid workflow status transition"):
        await repo.update_workflow_status(
            workflow_id=wf_id,
            new_status=WorkflowStatus.RUNNING,
        )

    # 5. Attempt Invalid Transition: COMPLETED -> PENDING (Must be rejected!)
    with pytest.raises(ValueError, match="Invalid workflow status transition"):
        await repo.update_workflow_status(
            workflow_id=wf_id,
            new_status=WorkflowStatus.PENDING,
        )


@pytest.mark.asyncio
async def test_workflow_task_dag_persistence(repo: StorageRepository):
    """Verify task insertion, status transitions, and dependency tracking in PostgreSQL."""
    wf_id = f"wf_{uuid.uuid4().hex[:10]}"
    t_id = "tenant_a"

    # Create parent workflow
    await repo.create_workflow(workflow_id=wf_id, tenant_id=t_id)

    # Insert DAG tasks
    tasks = [
        WorkflowTask(
            task_id="task_meta",
            workflow_id=wf_id,
            tenant_id=t_id,
            tool_name="repo_metadata__get_info",
            status=TaskStatus.PENDING,
            dependencies=[],
        ),
        WorkflowTask(
            task_id="task_issues",
            workflow_id=wf_id,
            tenant_id=t_id,
            tool_name="issues__list_issues",
            status=TaskStatus.PENDING,
            dependencies=[],
        ),
        WorkflowTask(
            task_id="task_detail",
            workflow_id=wf_id,
            tenant_id=t_id,
            tool_name="issues__get_detail",
            status=TaskStatus.BLOCKED,
            dependencies=["task_issues"],
        ),
    ]

    await repo.save_tasks(tasks)

    # Retrieve tasks
    fetched_tasks = await repo.get_workflow_tasks(wf_id, tenant_id=t_id)
    assert len(fetched_tasks) == 3
    task_map = {t.task_id: t for t in fetched_tasks}

    assert task_map["task_meta"].status == TaskStatus.PENDING
    assert task_map["task_detail"].dependencies == ["task_issues"]

    # Update individual task status
    updated_meta = await repo.update_task_status(
        workflow_id=wf_id,
        task_id="task_meta",
        status=TaskStatus.COMPLETED,
        duration_ms=120,
        result={"stars": 128},
    )
    assert updated_meta is not None
    assert updated_meta.status == TaskStatus.COMPLETED
    assert updated_meta.result == {"stars": 128}


@pytest.mark.asyncio
async def test_trace_and_audit_event_persistence(repo: StorageRepository):
    """Verify trace and audit event durability, filtering, and last-event pagination."""
    wf_id = f"wf_{uuid.uuid4().hex[:10]}"
    t_id = "tenant_a"
    corr_id = f"req_{uuid.uuid4().hex[:8]}"

    # Create workflow
    await repo.create_workflow(workflow_id=wf_id, tenant_id=t_id, correlation_id=corr_id)

    # Record trace events
    evt1 = await repo.record_trace_event(
        TraceEvent(
            event_id=f"evt_{uuid.uuid4().hex[:8]}",
            workflow_id=wf_id,
            tenant_id=t_id,
            correlation_id=corr_id,
            event_type="workflow_started",
            timestamp=datetime.now(timezone.utc),
            details={"step": 1},
        )
    )
    assert evt1.id is not None

    evt2 = await repo.record_trace_event(
        TraceEvent(
            event_id=f"evt_{uuid.uuid4().hex[:8]}",
            workflow_id=wf_id,
            tenant_id=t_id,
            correlation_id=corr_id,
            event_type="task_completed",
            task_id="task_1",
            timestamp=datetime.now(timezone.utc),
            details={"step": 2},
        )
    )
    assert evt2.id is not None

    # Query all events
    events = await repo.list_trace_events(wf_id, tenant_id=t_id)
    assert len(events) == 2
    assert events[0].event_type == "workflow_started"
    assert events[1].event_type == "task_completed"

    # Query since_id (for SSE reconnection)
    since_events = await repo.list_trace_events(wf_id, tenant_id=t_id, since_id=evt1.id)
    assert len(since_events) == 1
    assert since_events[0].id == evt2.id

    # Record Audit Event
    aud_evt = await repo.record_audit_event(
        AuditEvent(
            event_id=f"aud_{uuid.uuid4().hex[:8]}",
            tenant_id=t_id,
            actor="admin_user",
            action="tenant.cache.invalidate",
            resource_type="cache",
            resource_id="tag:repo_info",
            timestamp=datetime.now(timezone.utc),
            outcome="success",
            metadata={"tag": "repo_info"},
        )
    )
    assert aud_evt.id is not None

    audit_logs = await repo.list_audit_events(tenant_id=t_id, action="tenant.cache.invalidate")
    assert len(audit_logs) >= 1
    assert audit_logs[0].actor == "admin_user"


@pytest.mark.asyncio
async def test_startup_crash_recovery(repo: StorageRepository):
    """
    Verify restart crash recovery:
    Workflows stranded in 'running' state are safely identified and transitioned
    to 'interrupted' without automatically replaying side-effecting tools.
    """
    stranded_id = f"wf_stranded_{uuid.uuid4().hex[:8]}"
    t_id = "tenant_b"

    # 1. Create a workflow and set to RUNNING
    await repo.create_workflow(workflow_id=stranded_id, tenant_id=t_id)
    await repo.update_workflow_status(stranded_id, WorkflowStatus.RUNNING)

    # 2. Add an in-progress task
    await repo.save_tasks(
        [
            WorkflowTask(
                task_id="task_in_flight",
                workflow_id=stranded_id,
                tenant_id=t_id,
                tool_name="repo_metadata__get_info",
                status=TaskStatus.RUNNING,
            )
        ]
    )

    # 3. Simulate runtime crash & restart recovery
    recovered_ids = await repo.recover_interrupted_workflows()
    assert stranded_id in recovered_ids

    # 4. Check workflow status is now INTERRUPTED (not completed, not replayed)
    recovered_wf = await repo.get_workflow(stranded_id)
    assert recovered_wf is not None
    assert recovered_wf.status == WorkflowStatus.INTERRUPTED
    assert "restarted" in recovered_wf.error.lower()

    # 5. Check in-flight task was marked failed
    tasks = await repo.get_workflow_tasks(stranded_id)
    assert tasks[0].status == TaskStatus.FAILED

    # 6. Verify subsequent recovery run finds 0 stranded workflows
    subsequent = await repo.recover_interrupted_workflows()
    assert stranded_id not in subsequent
