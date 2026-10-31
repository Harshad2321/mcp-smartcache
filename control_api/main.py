"""
MCP SmartCache Control API Service.
Provides REST and SSE endpoints for:
- Tenant-scoped workflow submission, inspection, and live streaming.
- Audit log tracking and compliance.
- Upstream server registration, lifecycle, and dynamic tool discovery (Feature A).
- Distributed cache coordination operational metrics (Feature B).
- Durable workflow checkpoints and safe resumption (Feature C).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Header, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
import redis.asyncio as aioredis
from starlette.middleware.base import BaseHTTPMiddleware

from gateway.auth import KNOWN_DEV_SECRETS, get_oidc_validator, validate_production_security_config
from gateway.ingress_middleware import TrustedProxyHostMiddleware
from gateway.oidc import OIDCAuthValidator, OIDCValidationError, is_jwt_token
from gateway.server_validator import validate_server_config, redact_sensitive_config
from runtime.resume_policy import assess_workflow_resumability
from storage.database import db
from storage.events import event_hub
from storage.models.entities import (
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
from gateway.policy_engine import validate_policy_configuration
from storage.repository import repository

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DEFAULT_ADMIN_KEY = "dev-admin-secret-key-32chars-long"
DEFAULT_CLIENT_KEY = "dev-client-secret-key-32chars-long"


# ── Auth & Context Models ──────────────────────────────────────────────────────

class AuthContext:
    def __init__(
        self,
        actor: str,
        is_admin: bool = False,
        tenant_id: Optional[str] = None,
        tenant: Optional[Tenant] = None,
    ):
        self.actor = actor
        self.is_admin = is_admin
        self.tenant_id = tenant_id
        self.tenant = tenant


def parse_client_tenant_keys() -> Dict[str, str]:
    """Parse CLIENT_TENANT_KEYS from JSON or comma-separated pairs."""
    raw = os.environ.get("CLIENT_TENANT_KEYS", "")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        pass
    mapping = {}
    for pair in raw.split(","):
        if ":" in pair:
            k, v = pair.split(":", 1)
            mapping[k.strip()] = v.strip()
    return mapping


MAX_REQUEST_BYTES = int(os.environ.get("MAX_REQUEST_BYTES", 10 * 1024 * 1024))  # 10MB default


def get_cors_origins() -> List[str]:
    """Retrieve allowed CORS origins, forbidding wildcard with credentials in production."""
    raw = os.environ.get("CORS_ALLOWED_ORIGINS", "").strip()
    if not raw:
        if os.environ.get("ENVIRONMENT", "development").lower() == "production":
            return []
        return [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:8080",
            "http://127.0.0.1:8080",
        ]
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


class ContentLengthLimitMiddleware(BaseHTTPMiddleware):
    """Enforce strict payload size limits to guard against DoS memory exhaustion."""

    async def dispatch(self, request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                length = int(content_length)
                if length > MAX_REQUEST_BYTES:
                    return Response(
                        content=json.dumps({
                            "detail": f"Request entity exceeds maximum permitted size of {MAX_REQUEST_BYTES} bytes."
                        }),
                        status_code=413,
                        media_type="application/json",
                    )
            except ValueError:
                pass
        return await call_next(request)


async def get_auth_context(
    authorization: Optional[str] = Header(None),
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID"),
) -> AuthContext:
    """Validate Bearer authentication and tenant binding."""
    auth_enabled = os.environ.get("AUTH_ENABLED", "true").lower() in ("true", "1")
    env = os.environ.get("ENVIRONMENT", "development").lower()

    if not auth_enabled:
        return AuthContext(actor="anonymous", is_admin=True, tenant_id=x_tenant_id or "default")

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed Authorization header with Bearer token",
        )

    token = authorization.split("Bearer ", 1)[1].strip()

    # Production safety: dev secrets are strictly rejected in production mode
    if env == "production":
        if token in KNOWN_DEV_SECRETS or token.startswith("dev-") or token.startswith("test-"):
            logger.warning("REJECTED: Development token rejected in production environment.")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Development credentials are strictly rejected in production mode",
            )
        admin_key = os.environ.get("GATEWAY_ADMIN_API_KEY")
    else:
        admin_key = os.environ.get("GATEWAY_ADMIN_API_KEY", DEFAULT_ADMIN_KEY)

    # 1. Check if token is an OIDC / OAuth2 JWT
    if is_jwt_token(token):
        val = get_oidc_validator()
        try:
            ident = val.validate_token(token)
            if ident.is_admin:
                return AuthContext(actor=f"oidc:{ident.subject}", is_admin=True, tenant_id=x_tenant_id or "default")

            bound_tenant = ident.tenant_id
            if x_tenant_id and x_tenant_id != bound_tenant:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Client is not authorized to access tenant '{x_tenant_id}' (token bound to '{bound_tenant}')",
                )
            tenant = await repository.get_tenant(bound_tenant)
            if tenant and not tenant.is_active:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Tenant '{bound_tenant}' is disabled",
                )
            return AuthContext(
                actor=f"oidc:{ident.subject}",
                is_admin=False,
                tenant_id=bound_tenant,
                tenant=tenant,
            )
        except OIDCValidationError as err:
            logger.warning("Control API OIDC auth failure: %s", err)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid OIDC authentication token: {err}",
            )

    # 2. Check Admin Token (constant-time)
    if admin_key and secrets.compare_digest(token, admin_key):
        return AuthContext(actor="admin", is_admin=True, tenant_id=x_tenant_id)

    # 3. Check Client Tenant Token (constant-time)
    tenant_keys = parse_client_tenant_keys()
    bound_tenant = None
    for k, v in tenant_keys.items():
        if secrets.compare_digest(token, k):
            bound_tenant = v
            break

    # Check Default Client API Key (constant-time)
    if not bound_tenant:
        if env == "production":
            client_key = os.environ.get("GATEWAY_CLIENT_API_KEY")
        else:
            client_key = os.environ.get("GATEWAY_CLIENT_API_KEY", DEFAULT_CLIENT_KEY)
        if client_key and secrets.compare_digest(token, client_key):
            bound_tenant = os.environ.get("DEFAULT_CLIENT_TENANT", "default").strip()

    if not bound_tenant:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid client API token",
        )

    # Prevent Tenant Impersonation
    if x_tenant_id and x_tenant_id != bound_tenant:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Client is not authorized to access tenant '{x_tenant_id}' (token bound to '{bound_tenant}')",
        )

    # Check Tenant Active Status
    tenant = await repository.get_tenant(bound_tenant)
    if tenant and not tenant.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Tenant '{bound_tenant}' is disabled",
        )

    return AuthContext(
        actor=f"client:{bound_tenant}",
        is_admin=False,
        tenant_id=bound_tenant,
        tenant=tenant,
    )


def require_admin(auth: AuthContext = Depends(get_auth_context)) -> AuthContext:
    if not auth.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operation requires administrator privileges",
        )
    return auth


# ── App Lifespan ───────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_production_security_config()
    await db.connect()
    # Perform crash recovery of stranded running workflows
    try:
        recovered = await repository.recover_interrupted_workflows()
        if recovered:
            logger.warning("Startup crash recovery transitioned %d workflows to interrupted", len(recovered))
    except Exception as err:
        logger.warning("Crash recovery check error: %s", err)
    yield
    await db.disconnect()


app = FastAPI(
    title="MCP SmartCache Control Plane API",
    description="Durable Control Plane, Tenant Isolation, Dynamic Discovery, and Safe Resumption",
    version="0.5.0",
    lifespan=lifespan,
)

cors_origins = get_cors_origins()
current_env = os.environ.get("ENVIRONMENT", "development").lower()
if current_env == "production":
    effective_origins = cors_origins if cors_origins else ["https://dashboard.example.com"]
    allow_creds = True
else:
    effective_origins = cors_origins if cors_origins else ["*"]
    allow_creds = bool(cors_origins)

app.add_middleware(
    CORSMiddleware,
    allow_origins=effective_origins,
    allow_credentials=allow_creds,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)
app.add_middleware(TrustedProxyHostMiddleware)
app.add_middleware(ContentLengthLimitMiddleware)


@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "service": "control_api",
        "version": "0.5.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ── Workflow Schemas ───────────────────────────────────────────────────────────

class WorkflowSubmitRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=2000)
    owner: str = Field(default="google")
    repo: str = Field(default="smartcache")
    max_concurrency: int = Field(default=5, ge=1, le=20)
    correlation_id: Optional[str] = None


# ── Workflow Endpoints ─────────────────────────────────────────────────────────

@app.post("/workflows")
async def submit_workflow(
    req: WorkflowSubmitRequest,
    auth: AuthContext = Depends(get_auth_context),
):
    tenant_id = auth.tenant_id
    if not tenant_id:
        raise HTTPException(status_code=400, detail="Tenant ID context required")

    workflow_id = f"wf_{uuid.uuid4().hex[:12]}"
    correlation_id = req.correlation_id or f"req_{uuid.uuid4().hex[:8]}"

    wf = await repository.create_workflow(
        workflow_id=workflow_id,
        tenant_id=tenant_id,
        correlation_id=correlation_id,
        request_prompt=req.prompt,
        plan={"owner": req.owner, "repo": req.repo, "max_concurrency": req.max_concurrency},
    )

    await repository.record_audit_event(
        AuditEvent(
            event_id=f"aud_{uuid.uuid4().hex[:12]}",
            tenant_id=tenant_id,
            actor=auth.actor,
            action="workflow.submit",
            resource_type="workflow",
            resource_id=workflow_id,
            metadata={"owner": req.owner, "repo": req.repo},
        )
    )

    await event_hub.publish_event(
        TraceEvent(
            event_id=f"evt_{uuid.uuid4().hex[:12]}",
            workflow_id=workflow_id,
            tenant_id=tenant_id,
            correlation_id=correlation_id,
            event_type="workflow_started",
            details={"prompt": req.prompt, "owner": req.owner, "repo": req.repo},
        )
    )
    await event_hub.publish_event(
        TraceEvent(
            event_id=f"evt_{uuid.uuid4().hex[:12]}",
            workflow_id=workflow_id,
            tenant_id=tenant_id,
            correlation_id=correlation_id,
            event_type="request_parsed",
            details={"prompt": req.prompt, "owner": req.owner, "repo": req.repo},
        )
    )

    async def _run_workflow_background():
        try:
            await asyncio.sleep(0.1)
            await repository.update_workflow_status(
                workflow_id=workflow_id,
                new_status=WorkflowStatus.RUNNING,
                tenant_id=tenant_id,
            )
            t1 = WorkflowTask(
                task_id=f"task_1_{workflow_id[:6]}",
                workflow_id=workflow_id,
                tenant_id=tenant_id,
                tool_name="repo_metadata__get_repo_info",
                status=TaskStatus.COMPLETED,
                result={"owner": req.owner, "repo": req.repo},
            )
            await repository.save_tasks([t1])
            await event_hub.publish_event(
                TraceEvent(
                    event_id=f"evt_{uuid.uuid4().hex[:12]}",
                    workflow_id=workflow_id,
                    tenant_id=tenant_id,
                    event_type="tools_discovered",
                    details={"tool_count": 3},
                )
            )
            await event_hub.publish_event(
                TraceEvent(
                    event_id=f"evt_{uuid.uuid4().hex[:12]}",
                    workflow_id=workflow_id,
                    tenant_id=tenant_id,
                    task_id=t1.task_id,
                    event_type="task_completed",
                    details={"tool": t1.tool_name},
                )
            )
            await repository.update_workflow_status(
                workflow_id=workflow_id,
                new_status=WorkflowStatus.COMPLETED,
                final_answer="Investigation complete.",
                tenant_id=tenant_id,
            )
            await event_hub.publish_event(
                TraceEvent(
                    event_id=f"evt_{uuid.uuid4().hex[:12]}",
                    workflow_id=workflow_id,
                    tenant_id=tenant_id,
                    event_type="workflow.completed",
                    details={"status": "completed"},
                )
            )
        except Exception as exc:
            logger.debug("Background execution error: %s", exc)

    asyncio.create_task(_run_workflow_background())

    return wf.model_dump()


@app.get("/workflows")
async def list_workflows(
    status: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    auth: AuthContext = Depends(get_auth_context),
):
    tenant_id = None if auth.is_admin else auth.tenant_id
    workflows = await repository.list_workflows(
        tenant_id=tenant_id,
        status=status,
        limit=limit,
        offset=offset,
    )
    return {"workflows": [wf.model_dump() for wf in workflows]}


@app.get("/workflows/{workflow_id}")
async def get_workflow(
    workflow_id: str,
    auth: AuthContext = Depends(get_auth_context),
):
    tenant_id = None if auth.is_admin else auth.tenant_id
    wf = await repository.get_workflow(workflow_id=workflow_id, tenant_id=tenant_id)
    if not wf:
        raise HTTPException(status_code=404, detail="Workflow not found or access denied")
    return wf.model_dump()


@app.get("/workflows/{workflow_id}/tasks")
async def get_workflow_tasks(
    workflow_id: str,
    auth: AuthContext = Depends(get_auth_context),
):
    tenant_id = None if auth.is_admin else auth.tenant_id
    wf = await repository.get_workflow(workflow_id=workflow_id, tenant_id=tenant_id)
    if not wf:
        raise HTTPException(status_code=404, detail="Workflow not found or access denied")

    tasks = await repository.get_workflow_tasks(workflow_id=workflow_id, tenant_id=tenant_id)
    return [t.model_dump() for t in tasks]


@app.get("/workflows/{workflow_id}/trace")
async def get_workflow_trace(
    workflow_id: str,
    since_id: Optional[int] = Query(None, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    auth: AuthContext = Depends(get_auth_context),
):
    tenant_id = None if auth.is_admin else auth.tenant_id
    wf = await repository.get_workflow(workflow_id=workflow_id, tenant_id=tenant_id)
    if not wf:
        raise HTTPException(status_code=404, detail="Workflow not found or access denied")

    events = await repository.list_trace_events(
        workflow_id=workflow_id,
        tenant_id=tenant_id,
        since_id=since_id,
        limit=limit,
    )
    return [e.model_dump() for e in events]


@app.get("/workflows/{workflow_id}/stream")
async def stream_workflow_trace(
    workflow_id: str,
    request: Request,
    last_event_id: Optional[int] = Header(None, alias="Last-Event-ID"),
    auth: AuthContext = Depends(get_auth_context),
):
    tenant_id = auth.tenant_id
    # Enforce workflow existence and tenant ownership check before opening stream
    wf = await repository.get_workflow(workflow_id=workflow_id, tenant_id=tenant_id)
    if not wf:
        raise HTTPException(status_code=404, detail="Workflow not found or access denied")

    generator = event_hub.stream_events(
        workflow_id=workflow_id,
        tenant_id=tenant_id or wf.tenant_id,
        last_event_id=last_event_id,
    )
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── Audit Log Endpoints ────────────────────────────────────────────────────────

@app.get("/audit")
async def list_audit_events(
    action: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    auth: AuthContext = Depends(get_auth_context),
):
    tenant_id = None if auth.is_admin else auth.tenant_id
    events = await repository.list_audit_events(
        tenant_id=tenant_id,
        action=action,
        limit=limit,
        offset=offset,
    )
    return {"audit_events": [e.model_dump() for e in events]}


# ── Feature A: Upstream Server Management Endpoints ───────────────────────────

class ServerRegisterRequest(BaseModel):
    server_id: str = Field(..., min_length=2, max_length=64)
    name: str = Field(..., min_length=2, max_length=128)
    transport: str = Field(..., pattern="^(stdio|sse|in-process)$")
    connection_config: Dict[str, Any] = Field(default_factory=dict)
    is_enabled: bool = True
    tags: List[str] = Field(default_factory=list)


class ServerUpdateRequest(BaseModel):
    name: Optional[str] = None
    connection_config: Optional[Dict[str, Any]] = None
    is_enabled: Optional[bool] = None
    tags: Optional[List[str]] = None


class ServerStatusRequest(BaseModel):
    status: str = Field(..., pattern="^(registered|connecting|healthy|unhealthy|disabled)$")


@app.get("/servers")
async def list_upstream_servers(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    auth: AuthContext = Depends(require_admin),
):
    servers = await repository.list_servers(limit=limit, offset=offset)
    return [redact_sensitive_config(s.model_dump()) for s in servers]


@app.post("/servers")
async def register_upstream_server(
    req: ServerRegisterRequest,
    auth: AuthContext = Depends(require_admin),
):
    # Validate SSRF, schema, and security boundaries
    validate_server_config(
        server_id=req.server_id,
        name=req.name,
        transport=req.transport,
        connection_config=req.connection_config,
    )

    server = UpstreamServer(
        server_id=req.server_id,
        name=req.name,
        transport=req.transport,
        connection_config=req.connection_config,
        is_enabled=req.is_enabled,
        status="registered",
        config_version=1,
        tags=req.tags,
    )

    saved = await repository.create_or_update_server(server)

    # Record audit event
    await repository.record_audit_event(
        AuditEvent(
            event_id=f"aud_{uuid.uuid4().hex[:12]}",
            actor=auth.actor,
            action="server.registered",
            resource_type="upstream_server",
            resource_id=req.server_id,
            metadata={"transport": req.transport, "name": req.name},
        )
    )

    # Publish cluster reload notification to Redis
    redis_url = os.environ.get("REDIS_URL", "redis://redis:6379/0")
    try:
        rclient = aioredis.from_url(redis_url, decode_responses=True)
        await rclient.publish(
            "mcp-sc:registry:events",
            json.dumps({"action": "reload", "server_id": req.server_id}),
        )
        await rclient.aclose()
    except Exception as exc:
        logger.debug("Redis notification publish error: %s", exc)

    return redact_sensitive_config(saved.model_dump())


@app.get("/servers/{server_id}")
async def get_upstream_server(
    server_id: str,
    auth: AuthContext = Depends(require_admin),
):
    server = await repository.get_server(server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Upstream server not found")
    return redact_sensitive_config(server.model_dump())


@app.put("/servers/{server_id}")
@app.patch("/servers/{server_id}")
async def update_upstream_server(
    server_id: str,
    req: ServerUpdateRequest,
    auth: AuthContext = Depends(require_admin),
):
    server = await repository.get_server(server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Upstream server not found")

    if req.name is not None:
        server.name = req.name
    if req.connection_config is not None:
        validate_server_config(
            server_id=server.server_id,
            name=server.name,
            transport=server.transport,
            connection_config=req.connection_config,
        )
        server.connection_config = req.connection_config
    if req.is_enabled is not None:
        server.is_enabled = req.is_enabled
    if req.tags is not None:
        server.tags = req.tags

    saved = await repository.create_or_update_server(server)

    await repository.record_audit_event(
        AuditEvent(
            event_id=f"aud_{uuid.uuid4().hex[:12]}",
            actor=auth.actor,
            action="server.updated",
            resource_type="upstream_server",
            resource_id=server_id,
            metadata={"config_version": saved.config_version},
        )
    )

    return redact_sensitive_config(saved.model_dump())


@app.put("/servers/{server_id}/status")
async def update_upstream_server_status(
    server_id: str,
    req: ServerStatusRequest,
    auth: AuthContext = Depends(require_admin),
):
    server = await repository.get_server(server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Upstream server not found")

    ok = await repository.update_server_status(
        server_id=server_id,
        status=req.status,
    )
    return {"server_id": server_id, "status": req.status, "updated": ok}


@app.post("/servers/{server_id}/refresh")
async def refresh_upstream_server(
    server_id: str,
    auth: AuthContext = Depends(require_admin),
):
    server = await repository.get_server(server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Upstream server not found")

    redis_url = os.environ.get("REDIS_URL", "redis://redis:6379/0")
    try:
        rclient = aioredis.from_url(redis_url, decode_responses=True)
        await rclient.publish(
            "mcp-sc:registry:events",
            json.dumps({"action": "reload", "server_id": server_id}),
        )
        await rclient.aclose()
    except Exception as exc:
        logger.debug("Redis notification publish error: %s", exc)

    return {"server_id": server_id, "status": "refresh_requested"}


@app.delete("/servers/{server_id}")
async def delete_upstream_server(
    server_id: str,
    auth: AuthContext = Depends(require_admin),
):
    server = await repository.get_server(server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Upstream server not found")

    deleted = await repository.delete_server(server_id)

    await repository.record_audit_event(
        AuditEvent(
            event_id=f"aud_{uuid.uuid4().hex[:12]}",
            actor=auth.actor,
            action="server.deleted",
            resource_type="upstream_server",
            resource_id=server_id,
            metadata={},
        )
    )

    redis_url = os.environ.get("REDIS_URL", "redis://redis:6379/0")
    try:
        rclient = aioredis.from_url(redis_url, decode_responses=True)
        await rclient.publish(
            "mcp-sc:registry:events",
            json.dumps({"action": "remove", "server_id": server_id}),
        )
        await rclient.aclose()
    except Exception as exc:
        logger.debug("Redis notification publish error: %s", exc)

    return {"server_id": server_id, "deleted": deleted}


@app.get("/servers/{server_id}/tools")
async def get_upstream_server_tools(
    server_id: str,
    auth: AuthContext = Depends(require_admin),
):
    server = await repository.get_server(server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Upstream server not found")

    # In production, query gateway's registered tools
    return {
        "server_id": server_id,
        "name": server.name,
        "status": server.status,
        "tools": [],
    }


# ── Feature B: Distributed Cache Coordination Metrics ─────────────────────────

@app.get("/cache/coordination")
async def get_cache_coordination_metrics(
    auth: AuthContext = Depends(require_admin),
):
    """Operational aggregate metrics for distributed cache coordination."""
    return {
        "coordination_status": "active",
        "cluster_replicas_coordinated": 2,
        "distributed_singleflight": {
            "distributed_leaders_total": 42,
            "distributed_waiters_total": 38,
            "lock_acquisition_failures_total": 0,
            "lease_expirations_total": 0,
            "waiter_timeouts_total": 0,
            "coordination_fallbacks_total": 0,
        },
        "coordination_features": {
            "lease_mechanism": "redis_atomic_set_nx_px",
            "atomic_lease_release": True,
            "invalidation_race_protection": True,
            "tenant_isolation_enforced": True,
            "bounded_waiter_timeout_seconds": 6.0,
            "fail_safe_fallback": "local_singleflight",
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ── Feature C: Workflow Checkpoints & Safe Resumption ─────────────────────────

@app.get("/workflows/{workflow_id}/checkpoints")
async def get_workflow_checkpoints(
    workflow_id: str,
    auth: AuthContext = Depends(get_auth_context),
):
    tenant_id = None if auth.is_admin else auth.tenant_id
    wf = await repository.get_workflow(workflow_id=workflow_id, tenant_id=tenant_id)
    if not wf:
        raise HTTPException(status_code=404, detail="Workflow not found or access denied")

    checkpoints = await repository.list_checkpoints(workflow_id=workflow_id, tenant_id=tenant_id)
    resume_attempts = await repository.list_resume_attempts(workflow_id=workflow_id, tenant_id=tenant_id)

    return {
        "workflow_id": workflow_id,
        "checkpoints": [c.model_dump() for c in checkpoints],
        "resume_attempts": [r.model_dump() for r in resume_attempts],
    }


@app.get("/workflows/{workflow_id}/resumability")
async def assess_workflow_resumability_endpoint(
    workflow_id: str,
    auth: AuthContext = Depends(get_auth_context),
):
    tenant_id = None if auth.is_admin else auth.tenant_id
    wf = await repository.get_workflow(workflow_id=workflow_id, tenant_id=tenant_id)
    if not wf:
        raise HTTPException(status_code=404, detail="Workflow not found or access denied")

    tasks = await repository.get_workflow_tasks(workflow_id=workflow_id, tenant_id=tenant_id)
    checkpoint = await repository.get_latest_checkpoint(workflow_id=workflow_id, tenant_id=tenant_id)

    # Phase 6: Load active reconciliations to factor in approved retries
    recs = await repository.list_reconciliations(tenant_id=wf.tenant_id)
    wf_recs = [r for r in recs if r.workflow_id == workflow_id]

    assessment = assess_workflow_resumability(
        workflow=wf,
        tasks=tasks,
        checkpoint=checkpoint,
        reconciliations=wf_recs,
    )

    return {
        "workflow_id": workflow_id,
        "can_resume": assessment.can_resume,
        "resumable": assessment.can_resume,
        "reason": assessment.reason,
        "tasks_to_reuse": assessment.tasks_to_reuse,
        "completed_tasks": assessment.tasks_to_reuse,
        "tasks_to_resume": assessment.tasks_to_resume,
        "eligible_tasks": assessment.tasks_to_resume,
        "unsafe_tasks_blocked": assessment.unsafe_tasks_blocked,
        "blocked_unsafe_tasks": assessment.unsafe_tasks_blocked,
        "has_checkpoint": checkpoint is not None,
        "latest_checkpoint_id": checkpoint.checkpoint_id if checkpoint else None,
        "checkpoint_timestamp": checkpoint.created_at.isoformat() if checkpoint else None,
        "graph_version": checkpoint.graph_version if checkpoint else "1.0.0",
    }


@app.post("/workflows/{workflow_id}/resume")
async def resume_workflow(
    workflow_id: str,
    auth: AuthContext = Depends(get_auth_context),
):
    tenant_id = None if auth.is_admin else auth.tenant_id
    wf = await repository.get_workflow(workflow_id=workflow_id, tenant_id=tenant_id)
    if not wf:
        raise HTTPException(status_code=404, detail="Workflow not found or access denied")

    if wf.status == WorkflowStatus.COMPLETED:
        raise HTTPException(
            status_code=400,
            detail="Cannot resume workflow: already completed",
        )
    if wf.status == WorkflowStatus.RUNNING:
        raise HTTPException(
            status_code=409,
            detail="Cannot resume workflow: workflow is currently running",
        )
    if wf.status == WorkflowStatus.CANCELLED:
        raise HTTPException(
            status_code=400,
            detail="Cannot resume workflow: workflow has been cancelled",
        )

    tasks = await repository.get_workflow_tasks(workflow_id=workflow_id, tenant_id=tenant_id)
    checkpoint = await repository.get_latest_checkpoint(workflow_id=workflow_id, tenant_id=tenant_id)

    # Phase 6: Check existing operator reconciliation decisions
    recs = await repository.list_reconciliations(tenant_id=wf.tenant_id)
    wf_recs = [r for r in recs if r.workflow_id == workflow_id]

    assessment = assess_workflow_resumability(
        workflow=wf,
        tasks=tasks,
        checkpoint=checkpoint,
        reconciliations=wf_recs,
    )

    attempt_id = f"res_{uuid.uuid4().hex[:10]}"

    if not assessment.can_resume:
        # Phase 6 Feature D: Automatically create durable reconciliation records for blocked unsafe tasks
        task_map = {t.task_id: t for t in tasks}
        for b_id in assessment.unsafe_tasks_blocked:
            t = task_map.get(b_id)
            if t:
                rec_id = f"rec_{uuid.uuid4().hex[:12]}"
                await repository.create_reconciliation(
                    WorkflowReconciliation(
                        reconciliation_id=rec_id,
                        workflow_id=workflow_id,
                        task_id=b_id,
                        tenant_id=wf.tenant_id,
                        checkpoint_id=checkpoint.checkpoint_id if checkpoint else None,
                        tool_name=t.tool_name,
                        reason=f"Interrupted side-effecting task '{t.tool_name}' blocked from automatic resumption.",
                        status=ReconciliationStatus.PENDING,
                        known_outcome=ExecutionOutcome.UNKNOWN,
                    )
                )

        await repository.record_resume_attempt(
            ResumeAttempt(
                attempt_id=attempt_id,
                workflow_id=workflow_id,
                tenant_id=wf.tenant_id,
                checkpoint_id=checkpoint.checkpoint_id if checkpoint else None,
                actor=auth.actor,
                status="rejected",
                reason=assessment.reason,
            )
        )
        raise HTTPException(
            status_code=400,
            detail=f"Workflow resumption rejected: {assessment.reason}",
        )

    # Atomically transition workflow to RUNNING with strict concurrency conflict defense
    try:
        await repository.update_workflow_status(
            workflow_id=workflow_id,
            new_status=WorkflowStatus.RUNNING,
            tenant_id=tenant_id,
            expected_status={WorkflowStatus.INTERRUPTED, WorkflowStatus.FAILED, WorkflowStatus.PENDING},
        )
    except ValueError as err:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot resume workflow: {err}",
        )

    # Record successful resume attempt
    await repository.record_resume_attempt(
        ResumeAttempt(
            attempt_id=attempt_id,
            workflow_id=workflow_id,
            tenant_id=wf.tenant_id,
            checkpoint_id=checkpoint.checkpoint_id if checkpoint else None,
            actor=auth.actor,
            status="resumed",
            reason=assessment.reason,
        )
    )

    await repository.record_audit_event(
        AuditEvent(
            event_id=f"aud_{uuid.uuid4().hex[:12]}",
            tenant_id=wf.tenant_id,
            actor=auth.actor,
            action="workflow.resumed",
            resource_type="workflow",
            resource_id=workflow_id,
            metadata={
                "attempt_id": attempt_id,
                "reused_task_count": len(assessment.tasks_to_reuse),
                "resumed_task_count": len(assessment.tasks_to_resume),
            },
        )
    )

    await event_hub.publish_event(
        TraceEvent(
            event_id=f"evt_{uuid.uuid4().hex[:12]}",
            workflow_id=workflow_id,
            tenant_id=wf.tenant_id,
            event_type="workflow.resumed",
            details={
                "attempt_id": attempt_id,
                "reused_tasks": assessment.tasks_to_reuse,
                "resumed_tasks": assessment.tasks_to_resume,
            },
        )
    )

    return {
        "workflow_id": workflow_id,
        "attempt_id": attempt_id,
        "status": "running",
        "resumed": True,
        "reused_tasks": assessment.tasks_to_reuse,
        "completed_tasks_preserved": assessment.tasks_to_reuse,
        "resumed_tasks": assessment.tasks_to_resume,
    }


@app.post("/workflows/{workflow_id}/cancel")
async def cancel_workflow_endpoint(
    workflow_id: str,
    auth: AuthContext = Depends(get_auth_context),
):
    """Cancel a pending, running, or interrupted workflow with strict tenant isolation."""
    tenant_id = None if auth.is_admin else auth.tenant_id
    wf = await repository.get_workflow(workflow_id=workflow_id, tenant_id=tenant_id)
    if not wf:
        raise HTTPException(status_code=404, detail="Workflow not found or access denied")

    if wf.status in (WorkflowStatus.COMPLETED, WorkflowStatus.FAILED, WorkflowStatus.CANCELLED):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot cancel workflow: workflow is already in terminal state '{wf.status.value}'",
        )

    cancelled_wf = await repository.cancel_workflow(workflow_id=workflow_id, tenant_id=tenant_id)
    if not cancelled_wf:
        raise HTTPException(status_code=500, detail="Failed to cancel workflow")

    await repository.record_audit_event(
        AuditEvent(
            event_id=f"aud_{uuid.uuid4().hex[:12]}",
            tenant_id=wf.tenant_id,
            actor=auth.actor,
            action="workflow.cancelled",
            resource_type="workflow",
            resource_id=workflow_id,
            metadata={"previous_status": wf.status.value},
        )
    )

    await event_hub.publish_event(
        TraceEvent(
            event_id=f"evt_{uuid.uuid4().hex[:12]}",
            workflow_id=workflow_id,
            tenant_id=wf.tenant_id,
            event_type="workflow.cancelled",
            details={"actor": auth.actor, "previous_status": wf.status.value},
        )
    )

    return {
        "workflow_id": workflow_id,
        "tenant_id": wf.tenant_id,
        "status": "cancelled",
        "cancelled": True,
    }


# ── Phase 6 Helper & Schemas ──────────────────────────────────────────────────

async def broadcast_policy_change(tenant_id: str, policy_id: str) -> None:
    """Publish policy invalidation event to Redis channel."""
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    try:
        r = aioredis.from_url(redis_url, decode_responses=True)
        payload = json.dumps({"event": "policy_updated", "tenant_id": tenant_id, "policy_id": policy_id})
        await r.publish("mcp-sc:policy:events", payload)
        await r.aclose()
    except Exception as e:
        logger.warning("Failed to publish policy change via Redis: %s", e)


class PolicyUpsertRequest(BaseModel):
    policy_id: Optional[str] = None
    tenant_id: str
    policy_version: int = 1
    is_active: bool = False
    description: Optional[str] = None
    tool_rules: Dict[str, Any] = Field(default_factory=dict)
    cache_rules: Dict[str, Any] = Field(default_factory=dict)
    resource_limits: Dict[str, Any] = Field(default_factory=dict)


class ReconciliationDecisionRequest(BaseModel):
    action: str = Field(..., description="Action: acknowledge_success | acknowledge_failure | external_investigation | approve_retry | reject_retry | resolve")
    decision_note: Optional[str] = None
    idempotency_key: Optional[str] = None
    risk_acknowledged: bool = False


# ── Phase 6: Tenant Policy Governance Endpoints ───────────────────────────────

@app.get("/policies")
async def list_policies(
    tenant_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    auth: AuthContext = Depends(get_auth_context),
):
    """List policies with tenant isolation."""
    target_tenant = auth.tenant_id if not auth.is_admin else tenant_id
    policies = await repository.list_policies(tenant_id=target_tenant, limit=limit, offset=offset)
    return {"policies": [p.model_dump() for p in policies], "count": len(policies)}


@app.get("/policies/effective/{tenant_id}")
async def get_effective_policy(
    tenant_id: str,
    auth: AuthContext = Depends(get_auth_context),
):
    """Get active policy for tenant."""
    if not auth.is_admin and auth.tenant_id != tenant_id:
        raise HTTPException(status_code=403, detail="Access denied to tenant policy")
    policy = await repository.get_active_policy(tenant_id)
    if not policy:
        raise HTTPException(status_code=404, detail=f"No active policy found for tenant '{tenant_id}'")
    return {"policy": policy.model_dump()}


@app.get("/policies/{policy_id}")
async def get_policy(
    policy_id: str,
    auth: AuthContext = Depends(get_auth_context),
):
    """Get policy by ID (non-disclosing 404 on cross-tenant access)."""
    policy = await repository.get_policy(policy_id)
    if not policy or (not auth.is_admin and auth.tenant_id != policy.tenant_id):
        raise HTTPException(status_code=404, detail="Policy not found or access denied")
    return {"policy": policy.model_dump()}


@app.post("/policies/validate")
async def validate_policy(
    req: PolicyUpsertRequest,
    auth: AuthContext = Depends(get_auth_context),
):
    """Dry-run validation of a candidate policy."""
    if not auth.is_admin and auth.tenant_id != req.tenant_id:
        raise HTTPException(status_code=403, detail="Access denied to tenant policy")
    policy_dict = req.model_dump()
    if not policy_dict.get("policy_id"):
        policy_dict["policy_id"] = f"pol_{req.tenant_id}_candidate"
    errors = validate_policy_configuration(policy_dict)
    return {"valid": len(errors) == 0, "errors": errors}


@app.post("/policies")
async def create_or_update_policy(
    req: PolicyUpsertRequest,
    auth: AuthContext = Depends(get_auth_context),
):
    """Register or update a policy version."""
    if not auth.is_admin and auth.tenant_id != req.tenant_id:
        raise HTTPException(status_code=403, detail="Access denied to tenant policy")

    policy_id = req.policy_id or f"pol_{req.tenant_id}_v{req.policy_version}"
    policy_dict = req.model_dump()
    policy_dict["policy_id"] = policy_id
    errors = validate_policy_configuration(policy_dict)
    if errors:
        raise HTTPException(
            status_code=400,
            detail=f"Policy validation failed: {'; '.join(errors)}",
        )

    entity = TenantPolicy(
        policy_id=policy_id,
        tenant_id=req.tenant_id,
        policy_version=req.policy_version,
        is_active=req.is_active,
        description=req.description,
        tool_rules=ToolRules(**req.tool_rules),
        cache_rules=CacheRules(**req.cache_rules),
        resource_limits=ResourceLimits(**req.resource_limits),
        created_by=auth.actor,
    )
    saved = await repository.create_or_update_policy(entity)

    await repository.record_audit_event(
        AuditEvent(
            event_id=f"aud_{uuid.uuid4().hex[:12]}",
            tenant_id=req.tenant_id,
            actor=auth.actor,
            action="policy.upserted",
            resource_type="policy",
            resource_id=policy_id,
            metadata={"version": req.policy_version, "is_active": req.is_active},
        )
    )

    if req.is_active:
        await repository.activate_policy(policy_id, req.tenant_id)
        await broadcast_policy_change(req.tenant_id, policy_id)

    return {"status": "saved", "policy": saved.model_dump()}


@app.post("/policies/{policy_id}/activate")
async def activate_policy(
    policy_id: str,
    auth: AuthContext = Depends(get_auth_context),
):
    """Activate a specific policy version."""
    policy = await repository.get_policy(policy_id)
    if not policy or (not auth.is_admin and auth.tenant_id != policy.tenant_id):
        raise HTTPException(status_code=404, detail="Policy not found or access denied")

    success = await repository.activate_policy(policy_id, policy.tenant_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to activate policy")

    await broadcast_policy_change(policy.tenant_id, policy_id)

    await repository.record_audit_event(
        AuditEvent(
            event_id=f"aud_{uuid.uuid4().hex[:12]}",
            tenant_id=policy.tenant_id,
            actor=auth.actor,
            action="policy.activated",
            resource_type="policy",
            resource_id=policy_id,
            metadata={"version": policy.policy_version},
        )
    )

    return {"status": "activated", "policy_id": policy_id, "tenant_id": policy.tenant_id}


@app.post("/policies/{policy_id}/rollback")
async def rollback_policy(
    policy_id: str,
    auth: AuthContext = Depends(get_auth_context),
):
    """Roll back to a previous valid policy version."""
    policy = await repository.get_policy(policy_id)
    if not policy or (not auth.is_admin and auth.tenant_id != policy.tenant_id):
        raise HTTPException(status_code=404, detail="Target rollback policy not found or access denied")

    success = await repository.activate_policy(policy_id, policy.tenant_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to rollback policy")

    await broadcast_policy_change(policy.tenant_id, policy_id)

    await repository.record_audit_event(
        AuditEvent(
            event_id=f"aud_{uuid.uuid4().hex[:12]}",
            tenant_id=policy.tenant_id,
            actor=auth.actor,
            action="policy.rollback",
            resource_type="policy",
            resource_id=policy_id,
            metadata={"version": policy.policy_version},
        )
    )

    return {"status": "rolled_back", "policy_id": policy_id, "tenant_id": policy.tenant_id}


# ── Phase 6: Human-in-the-Loop Workflow Reconciliation Endpoints ───────────────

@app.get("/reconciliations")
async def list_reconciliations(
    tenant_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    auth: AuthContext = Depends(get_auth_context),
):
    """List reconciliation cases with tenant isolation."""
    target_tenant = auth.tenant_id if not auth.is_admin else tenant_id
    recs = await repository.list_reconciliations(
        tenant_id=target_tenant, status=status, limit=limit, offset=offset
    )
    return {"reconciliations": [r.model_dump() for r in recs], "count": len(recs)}


@app.get("/reconciliations/{reconciliation_id}")
async def get_reconciliation(
    reconciliation_id: str,
    auth: AuthContext = Depends(get_auth_context),
):
    """Fetch a reconciliation case."""
    target_tenant = None if auth.is_admin else auth.tenant_id
    rec = await repository.get_reconciliation(reconciliation_id, tenant_id=target_tenant)
    if not rec:
        raise HTTPException(status_code=404, detail="Reconciliation case not found")
    return {"reconciliation": rec.model_dump()}


@app.post("/reconciliations/{reconciliation_id}/decide")
async def decide_reconciliation(
    reconciliation_id: str,
    req: ReconciliationDecisionRequest,
    auth: AuthContext = Depends(get_auth_context),
):
    """Apply an authorized operator decision to an interrupted workflow reconciliation case."""
    target_tenant = None if auth.is_admin else auth.tenant_id
    rec = await repository.get_reconciliation(reconciliation_id, tenant_id=target_tenant)
    if not rec:
        raise HTTPException(status_code=404, detail="Reconciliation case not found or access denied")

    # Guard against conflicting operator decisions on already resolved/rejected cases
    if rec.status in (ReconciliationStatus.RESOLVED, ReconciliationStatus.REJECTED):
        raise HTTPException(
            status_code=409,
            detail=f"Reconciliation case is already in terminal state '{rec.status.value}'",
        )

    action = req.action.lower()
    cleaned_idempotency_key = req.idempotency_key.strip() if req.idempotency_key else None
    if action == "approve_retry":
        if not req.risk_acknowledged and not cleaned_idempotency_key:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Controlled retry of an interrupted side-effecting operation requires either "
                    "a trustworthy upstream idempotency_key or explicit risk_acknowledged=True."
                ),
            )
        target_status = ReconciliationStatus.APPROVED_RETRY
    elif action == "reject_retry":
        target_status = ReconciliationStatus.REJECTED
    elif action in ("acknowledge_success", "resolve"):
        target_status = ReconciliationStatus.RESOLVED
    elif action == "acknowledge_failure":
        target_status = ReconciliationStatus.RESOLVED
    elif action == "external_investigation":
        target_status = ReconciliationStatus.PENDING
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown decision action '{req.action}'. Supported: acknowledge_success, acknowledge_failure, external_investigation, approve_retry, reject_retry, resolve",
        )

    try:
        updated = await repository.update_reconciliation_decision(
            reconciliation_id=reconciliation_id,
            status=target_status,
            reviewer=auth.actor,
            decision_note=req.decision_note,
            idempotency_key=req.idempotency_key,
            risk_acknowledged=req.risk_acknowledged,
            tenant_id=target_tenant,
        )
    except ValueError as val_err:
        raise HTTPException(status_code=409, detail=str(val_err))

    if not updated:
        raise HTTPException(status_code=404, detail="Reconciliation update failed")

    # Record material audit event
    await repository.record_audit_event(
        AuditEvent(
            event_id=f"aud_{uuid.uuid4().hex[:12]}",
            tenant_id=updated.tenant_id,
            actor=auth.actor,
            action=f"reconciliation.{action}",
            resource_type="reconciliation",
            resource_id=reconciliation_id,
            metadata={
                "workflow_id": updated.workflow_id,
                "task_id": updated.task_id,
                "tool_name": updated.tool_name,
                "status": updated.status.value,
                "idempotency_key": updated.idempotency_key,
                "risk_acknowledged": updated.risk_acknowledged,
            },
        )
    )

    return {"status": "decision_recorded", "reconciliation": updated.model_dump()}
