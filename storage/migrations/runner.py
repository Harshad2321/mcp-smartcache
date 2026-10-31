"""
Versioned Database Migration Engine for MCP SmartCache.
Ensures ACID-compliant schema creation, indexes, foreign keys, and default seed data.
Safe for repeatable executions across restarts.
"""

import logging
from typing import List
from storage.database import DatabaseManager

logger = logging.getLogger(__name__)

MIGRATIONS = [
    (
        1,
        "0001_initial_control_plane_schema",
        """
        -- 1. Tenants Table
        CREATE TABLE IF NOT EXISTS tenants (
            tenant_id VARCHAR(64) PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            config JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        -- 2. Workflow Runs Table
        CREATE TABLE IF NOT EXISTS workflow_runs (
            workflow_id VARCHAR(64) PRIMARY KEY,
            tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(tenant_id) ON DELETE RESTRICT,
            correlation_id VARCHAR(64) NOT NULL,
            workflow_type VARCHAR(64) NOT NULL DEFAULT 'repository_intelligence',
            original_request TEXT NOT NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'pending',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            started_at TIMESTAMPTZ,
            completed_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            duration_ms DOUBLE PRECISION,
            plan JSONB,
            summary JSONB,
            final_answer TEXT,
            error TEXT
        );

        -- 3. Workflow Tasks Table
        CREATE TABLE IF NOT EXISTS workflow_tasks (
            id BIGSERIAL PRIMARY KEY,
            task_id VARCHAR(64) NOT NULL,
            workflow_id VARCHAR(64) NOT NULL REFERENCES workflow_runs(workflow_id) ON DELETE CASCADE,
            tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(tenant_id) ON DELETE RESTRICT,
            tool_name VARCHAR(128) NOT NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'pending',
            dependencies JSONB NOT NULL DEFAULT '[]'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            started_at TIMESTAMPTZ,
            completed_at TIMESTAMPTZ,
            duration_ms DOUBLE PRECISION,
            attempt_count INT NOT NULL DEFAULT 1,
            result JSONB,
            error_message TEXT,
            CONSTRAINT uq_workflow_task UNIQUE (workflow_id, task_id)
        );

        -- 4. Trace Events Table
        CREATE TABLE IF NOT EXISTS trace_events (
            id BIGSERIAL PRIMARY KEY,
            event_id VARCHAR(64) NOT NULL UNIQUE,
            workflow_id VARCHAR(64) NOT NULL REFERENCES workflow_runs(workflow_id) ON DELETE CASCADE,
            tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(tenant_id) ON DELETE RESTRICT,
            correlation_id VARCHAR(64) NOT NULL,
            event_type VARCHAR(64) NOT NULL,
            task_id VARCHAR(64),
            timestamp DOUBLE PRECISION NOT NULL,
            details JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        -- 5. Audit Events Table
        CREATE TABLE IF NOT EXISTS audit_events (
            id BIGSERIAL PRIMARY KEY,
            event_id VARCHAR(64) NOT NULL UNIQUE,
            tenant_id VARCHAR(64),
            actor VARCHAR(128) NOT NULL,
            action VARCHAR(128) NOT NULL,
            resource_type VARCHAR(64) NOT NULL,
            resource_id VARCHAR(128) NOT NULL,
            outcome VARCHAR(32) NOT NULL,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        -- 6. Performance Indexes
        CREATE INDEX IF NOT EXISTS idx_workflow_runs_tenant_created ON workflow_runs(tenant_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_workflow_runs_status ON workflow_runs(status);
        CREATE INDEX IF NOT EXISTS idx_workflow_runs_correlation ON workflow_runs(correlation_id);
        CREATE INDEX IF NOT EXISTS idx_workflow_tasks_workflow ON workflow_tasks(workflow_id);
        CREATE INDEX IF NOT EXISTS idx_workflow_tasks_tenant ON workflow_tasks(tenant_id);
        CREATE INDEX IF NOT EXISTS idx_trace_events_workflow_id ON trace_events(workflow_id, id ASC);
        CREATE INDEX IF NOT EXISTS idx_trace_events_tenant ON trace_events(tenant_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_audit_events_tenant_time ON audit_events(tenant_id, timestamp DESC);
        CREATE INDEX IF NOT EXISTS idx_audit_events_action ON audit_events(action);

        -- 7. Seed Standard Development Tenants
        INSERT INTO tenants (tenant_id, name, is_active, config)
        VALUES 
            ('default', 'Default Development Tenant', TRUE, '{"max_workflow_concurrency": 5, "max_tasks_per_workflow": 20}'::jsonb),
            ('tenant_a', 'Enterprise Tenant Alpha', TRUE, '{"max_workflow_concurrency": 5, "max_tasks_per_workflow": 20}'::jsonb),
            ('tenant_b', 'Enterprise Tenant Beta', TRUE, '{"max_workflow_concurrency": 3, "max_tasks_per_workflow": 10}'::jsonb)
        ON CONFLICT (tenant_id) DO NOTHING;
        """
    ),
    (
        2,
        "0002_align_schema_columns",
        """
        -- Ensure all columns and constraints match expected Phase 4 types
        ALTER TABLE workflow_runs ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW();
        ALTER TABLE workflow_runs ADD COLUMN IF NOT EXISTS request_prompt TEXT;
        """
    ),
    (
        3,
        "0003_upstream_servers_and_checkpoints",
        """
        -- Upstream MCP servers table
        CREATE TABLE IF NOT EXISTS upstream_servers (
            server_id VARCHAR(64) PRIMARY KEY,
            name VARCHAR(128) NOT NULL,
            transport VARCHAR(32) NOT NULL,
            connection_config JSONB NOT NULL DEFAULT '{}'::jsonb,
            is_enabled BOOLEAN NOT NULL DEFAULT TRUE,
            status VARCHAR(32) NOT NULL DEFAULT 'registered',
            config_version INTEGER NOT NULL DEFAULT 1,
            last_health_check TIMESTAMPTZ,
            last_error TEXT,
            tags JSONB NOT NULL DEFAULT '[]'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        -- Workflow execution checkpoints
        CREATE TABLE IF NOT EXISTS workflow_checkpoints (
            id SERIAL PRIMARY KEY,
            checkpoint_id VARCHAR(64) UNIQUE NOT NULL,
            workflow_id VARCHAR(64) NOT NULL REFERENCES workflow_runs(workflow_id) ON DELETE CASCADE,
            tenant_id VARCHAR(64) NOT NULL,
            checkpoint_version INTEGER NOT NULL DEFAULT 1,
            graph_version VARCHAR(32) NOT NULL DEFAULT '1.0.0',
            state_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
            completed_tasks JSONB NOT NULL DEFAULT '{}'::jsonb,
            task_results JSONB NOT NULL DEFAULT '{}'::jsonb,
            pending_tasks JSONB NOT NULL DEFAULT '[]'::jsonb,
            is_resumable BOOLEAN NOT NULL DEFAULT TRUE,
            non_resumable_reason TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        -- Workflow resume attempts historical audit
        CREATE TABLE IF NOT EXISTS workflow_resume_attempts (
            id SERIAL PRIMARY KEY,
            attempt_id VARCHAR(64) UNIQUE NOT NULL,
            workflow_id VARCHAR(64) NOT NULL REFERENCES workflow_runs(workflow_id) ON DELETE CASCADE,
            tenant_id VARCHAR(64) NOT NULL,
            checkpoint_id VARCHAR(64),
            actor VARCHAR(128) NOT NULL DEFAULT 'system',
            status VARCHAR(32) NOT NULL DEFAULT 'initiated',
            reason TEXT,
            timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE INDEX IF NOT EXISTS idx_workflow_checkpoints_lookup ON workflow_checkpoints(workflow_id, tenant_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_workflow_resume_attempts_lookup ON workflow_resume_attempts(workflow_id, tenant_id);
        """
    ),
    (
        4,
        "0004_tenant_policies_and_reconciliations",
        """
        -- 1. Tenant Policies Table (Phase 6 Feature A)
        CREATE TABLE IF NOT EXISTS tenant_policies (
            policy_id VARCHAR(64) PRIMARY KEY,
            tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
            policy_version INTEGER NOT NULL DEFAULT 1,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            description TEXT,
            tool_rules JSONB NOT NULL DEFAULT '{}'::jsonb,
            cache_rules JSONB NOT NULL DEFAULT '{}'::jsonb,
            resource_limits JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            created_by VARCHAR(128) NOT NULL DEFAULT 'system'
        );

        -- 2. Workflow Reconciliations Table (Phase 6 Feature D)
        CREATE TABLE IF NOT EXISTS workflow_reconciliations (
            reconciliation_id VARCHAR(64) PRIMARY KEY,
            workflow_id VARCHAR(64) NOT NULL REFERENCES workflow_runs(workflow_id) ON DELETE CASCADE,
            task_id VARCHAR(64) NOT NULL,
            tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(tenant_id) ON DELETE RESTRICT,
            checkpoint_id VARCHAR(64),
            tool_name VARCHAR(128) NOT NULL,
            reason TEXT NOT NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'pending',
            known_outcome VARCHAR(32) NOT NULL DEFAULT 'unknown',
            reviewer VARCHAR(128),
            decision_note TEXT,
            idempotency_key VARCHAR(128),
            risk_acknowledged BOOLEAN NOT NULL DEFAULT FALSE,
            decision_timestamp TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_reconciliation_wf_task UNIQUE (workflow_id, task_id)
        );

        -- 3. Indexes for Policy and Reconciliation lookups
        CREATE INDEX IF NOT EXISTS idx_tenant_policies_lookup ON tenant_policies(tenant_id, is_active);
        CREATE INDEX IF NOT EXISTS idx_tenant_policies_version ON tenant_policies(tenant_id, policy_version DESC);
        CREATE INDEX IF NOT EXISTS idx_workflow_reconciliations_tenant ON workflow_reconciliations(tenant_id, status);
        CREATE INDEX IF NOT EXISTS idx_workflow_reconciliations_wf ON workflow_reconciliations(workflow_id);

        -- 4. Seed Standard Default Policies for Tenants
        INSERT INTO tenant_policies (policy_id, tenant_id, policy_version, is_active, description, tool_rules, cache_rules, resource_limits, created_by)
        VALUES 
            ('pol_default_v1', 'default', 1, TRUE, 'Default standard policy',
             '{"allowed_tools": ["*"], "denied_tools": [], "read_only_tools": ["repo_metadata__*", "issues__*"], "default_deny": false}'::jsonb,
             '{"enabled": true, "default_ttl_seconds": 300, "max_ttl_seconds": 3600, "tool_ttls": {}, "max_response_bytes": 524288, "bypass_cache": false}'::jsonb,
             '{"max_concurrent_upstream": 5, "max_concurrent_workflows": 3, "max_tasks_per_workflow": 20, "max_workflow_duration_seconds": 300, "max_input_payload_bytes": 1048576, "max_output_payload_bytes": 1048576, "rate_limit_rpm": 60, "burst_limit": 10}'::jsonb,
             'system'),
            ('pol_tenant_a_v1', 'tenant_a', 1, TRUE, 'Enterprise policy for Tenant Alpha',
             '{"allowed_tools": ["*"], "denied_tools": [], "read_only_tools": ["repo_metadata__*", "issues__*"], "default_deny": false}'::jsonb,
             '{"enabled": true, "default_ttl_seconds": 300, "max_ttl_seconds": 7200, "tool_ttls": {}, "max_response_bytes": 1048576, "bypass_cache": false}'::jsonb,
             '{"max_concurrent_upstream": 10, "max_concurrent_workflows": 5, "max_tasks_per_workflow": 30, "max_workflow_duration_seconds": 600, "max_input_payload_bytes": 2097152, "max_output_payload_bytes": 2097152, "rate_limit_rpm": 120, "burst_limit": 20}'::jsonb,
             'system'),
            ('pol_tenant_b_v1', 'tenant_b', 1, TRUE, 'Standard policy for Tenant Beta',
             '{"allowed_tools": ["repo_metadata__*", "issues__*"], "denied_tools": ["*_delete*", "*_drop*"], "read_only_tools": ["repo_metadata__*", "issues__*"], "default_deny": false}'::jsonb,
             '{"enabled": true, "default_ttl_seconds": 180, "max_ttl_seconds": 1800, "tool_ttls": {}, "max_response_bytes": 262144, "bypass_cache": false}'::jsonb,
             '{"max_concurrent_upstream": 3, "max_concurrent_workflows": 2, "max_tasks_per_workflow": 10, "max_workflow_duration_seconds": 180, "max_input_payload_bytes": 524288, "max_output_payload_bytes": 524288, "rate_limit_rpm": 30, "burst_limit": 5}'::jsonb,
             'system')
        ON CONFLICT (policy_id) DO NOTHING;
        """
    )
]


async def run_migrations(db: DatabaseManager) -> List[str]:
    """
    Ensure schema_migrations table exists, check already applied migrations,
    and apply new ones in strict sequential order within an isolated transaction.
    """
    await db.connect()
    async with db.transaction() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INT PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)

        applied_rows = await conn.fetch("SELECT version FROM schema_migrations ORDER BY version ASC;")
        applied_versions = {r["version"] for r in applied_rows}

        executed = []
        for version, name, sql in MIGRATIONS:
            if version not in applied_versions:
                logger.info("Applying database migration %d: %s", version, name)
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO schema_migrations (version, name) VALUES ($1, $2);",
                    version,
                    name,
                )
                executed.append(name)
                logger.info("Migration %d (%s) applied successfully.", version, name)
            else:
                logger.debug("Migration %d (%s) already applied.", version, name)

        return executed


class MigrationRunner:
    """Wrapper class providing object-oriented migration interface for tests."""

    def __init__(self, db: DatabaseManager):
        self.db = db

    async def run_migrations(self) -> List[str]:
        return await run_migrations(self.db)


if __name__ == "__main__":
    import asyncio
    db_mgr = DatabaseManager()
    results = asyncio.run(run_migrations(db_mgr))
    print(f"Applied migrations: {results}")
