"""
MCP SmartCache Agent Runtime Service.
Provides REST API endpoints for submitting agent workflows,
executing LangGraph-based dependency-aware tasks, and returning evidence-backed responses.
Includes PostgreSQL durable workflow lifecycle tracking, crash recovery, and SSE live tracing.
"""

import asyncio
from datetime import datetime, timezone
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, Response, Query
from fastapi.responses import JSONResponse, StreamingResponse

from runtime.models import TaskRequest, TaskResponse, WorkflowStatus
from runtime.mcp_client import MCPGatewayClient
from runtime.graph import build_agent_graph
from storage.database import db
from storage.repository import repository
from storage.events import event_hub
from storage.models.entities import WorkflowStatus as StorageWorkflowStatus

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global runtime state populated during lifespan
mcp_client: Optional[MCPGatewayClient] = None
agent_app = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Runtime lifecycle manager: initialize DB, MCP gateway client, crash recovery, and compiled workflow."""
    global mcp_client, agent_app

    gateway_url = os.environ.get("GATEWAY_MCP_URL", "http://gateway:8000")
    api_key = os.environ.get("GATEWAY_API_KEY")

    # Connect to PostgreSQL and run startup crash recovery
    try:
        await db.connect()
        recovered = await repository.recover_interrupted_workflows()
        if recovered:
            logger.warning("Startup crash recovery updated %d stranded workflows to interrupted: %s", len(recovered), recovered)
    except Exception as err:
        logger.warning("Database startup connection/recovery failed: %s", err)

    logger.info("Initializing Agent Runtime connecting to Gateway at %s", gateway_url)
    mcp_client = MCPGatewayClient(
        gateway_url=gateway_url,
        api_key=api_key,
    )
    agent_app = build_agent_graph(mcp_client)
    yield
    # Cleanup
    try:
        await db.disconnect()
    except Exception:
        pass
    mcp_client = None
    agent_app = None


app = FastAPI(
    title="MCP SmartCache Agent Runtime",
    description="LangGraph-based Dependency-Aware Agent Runtime communicating exclusively via MCP Gateway",
    version="0.3.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def correlation_id_middleware(request: Request, call_next):
    """Propagate or generate X-Request-ID correlation header."""
    correlation_id = request.headers.get("X-Request-ID") or f"req_{uuid.uuid4().hex[:12]}"
    request.state.correlation_id = correlation_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = correlation_id
    return response


@app.get("/health")
async def health_check():
    """Liveness probe."""
    gateway_url = os.environ.get("GATEWAY_MCP_URL", "http://gateway:8000")
    return {
        "status": "healthy",
        "service": "runtime",
        "version": "0.3.0",
        "gateway_url": gateway_url,
    }


def get_mcp_client() -> MCPGatewayClient:
    """Lazy initializer for MCPGatewayClient."""
    global mcp_client
    if mcp_client is None:
        gateway_url = os.environ.get("GATEWAY_MCP_URL", "http://gateway:8000")
        api_key = os.environ.get("GATEWAY_API_KEY")
        mcp_client = MCPGatewayClient(
            gateway_url=gateway_url,
            api_key=api_key,
        )
    return mcp_client


def get_agent_app():
    """Lazy initializer for compiled LangGraph agent workflow."""
    global agent_app
    if agent_app is None:
        client = get_mcp_client()
        agent_app = build_agent_graph(client)
    return agent_app


@app.get("/health/ready")
async def readiness_check():
    """Readiness probe: verifies gateway MCP and PostgreSQL reachability."""
    client = get_mcp_client()
    db_ready = await db.check_health()
    try:
        tools = await client.list_tools()
        return {
            "status": "ready" if db_ready else "degraded",
            "service": "runtime",
            "database_ready": db_ready,
            "gateway_tools_available": len(tools),
        }
    except Exception as err:
        return JSONResponse(
            status_code=503,
            content={
                "status": "not_ready",
                "service": "runtime",
                "database_ready": db_ready,
                "gateway_error": str(err),
            },
        )


@app.post("/tasks", response_model=TaskResponse)
@app.post("/agent/run", response_model=TaskResponse)
async def submit_task(request: TaskRequest, req: Request):
    """
    Submit a task to the LangGraph Agent Runtime.
    Validates tenant context and limits, creates durable workflow record,
    executes the DAG plan concurrently through the Gateway, and produces an
    evidence-backed response.
    """
    workflow_app = get_agent_app()
    correlation_id = getattr(req.state, "correlation_id", f"wf_{uuid.uuid4().hex[:12]}")
    tenant_id = req.headers.get("X-Tenant-ID", "default").strip()

    # Enforce tenant active status & server-side concurrency limits
    try:
        tenant = await repository.get_tenant(tenant_id)
        if tenant and not tenant.is_active:
            raise HTTPException(status_code=403, detail=f"Tenant '{tenant_id}' is disabled.")
        max_allowed_concurrency = tenant.config.get("max_concurrency", 5) if (tenant and tenant.config) else 5
    except HTTPException:
        raise
    except Exception as exc:
        logger.debug("Tenant lookup skipped: %s", exc)
        max_allowed_concurrency = 5

    effective_concurrency = min(request.max_concurrency or 5, max_allowed_concurrency)

    # Durable pre-execution record
    workflow_id = correlation_id
    try:
        await repository.create_workflow(
            workflow_id=workflow_id,
            tenant_id=tenant_id,
            correlation_id=correlation_id,
            request_prompt=request.prompt,
        )
    except Exception as err:
        logger.debug("Durable workflow creation skipped/failed: %s", err)

    initial_state = {
        "workflow_id": workflow_id,
        "tenant_id": tenant_id,
        "correlation_id": correlation_id,
        "original_request": request.prompt,
        "owner": request.owner or "google",
        "repo": request.repo or "smartcache",
        "max_concurrency": effective_concurrency,
        "workflow_timeout_seconds": request.workflow_timeout_seconds or 60.0,
        "custom_plan": request.custom_plan,
        "execution_trace": [],
    }

    try:
        final_state = await workflow_app.ainvoke(initial_state)

        # Build response
        status = final_state.get("workflow_status", WorkflowStatus.FAILED.value)
        return TaskResponse(
            workflow_id=final_state.get("workflow_id", workflow_id),
            status=WorkflowStatus(status) if status in [s.value for s in WorkflowStatus] else WorkflowStatus.FAILED,
            original_request=final_state.get("original_request", request.prompt),
            plan=final_state.get("plan"),
            task_results=final_state.get("task_results", {}),
            evidence=final_state.get("evidence", {}),
            final_answer=final_state.get("final_answer"),
            execution_trace=final_state.get("execution_trace", []),
            timing=final_state.get("timing", {}),
            error=final_state.get("error"),
        )
    except Exception as exc:
        logger.error("Workflow execution error: %s", exc, exc_info=True)
        return TaskResponse(
            workflow_id=workflow_id,
            status=WorkflowStatus.FAILED,
            original_request=request.prompt,
            error=f"Internal workflow execution error: {str(exc)}",
            timing={"duration_ms": 0.0},
        )


@app.get("/workflows/{workflow_id}")
async def get_workflow_details(workflow_id: str, req: Request):
    """Retrieve durable workflow execution record with tenant isolation."""
    tenant_id = req.headers.get("X-Tenant-ID", "default").strip()
    wf = await repository.get_workflow(workflow_id, tenant_id=tenant_id)
    if not wf:
        raise HTTPException(status_code=404, detail="Workflow not found or access denied")
    return wf.model_dump()


@app.get("/workflows/{workflow_id}/stream")
async def stream_workflow_trace(
    workflow_id: str,
    req: Request,
    last_event_id: Optional[int] = Query(None, alias="since_id"),
):
    """
    Live Server-Sent Events (SSE) trace streaming endpoint.
    Supports reconnection via Last-Event-ID header or since_id query parameter.
    Enforces tenant authorization.
    """
    tenant_id = req.headers.get("X-Tenant-ID", "default").strip()
    header_last_id = req.headers.get("Last-Event-ID")
    effective_last_id = last_event_id
    if effective_last_id is None and header_last_id and header_last_id.isdigit():
        effective_last_id = int(header_last_id)

    # Validate tenant access
    wf = await repository.get_workflow(workflow_id, tenant_id=tenant_id)
    if not wf:
        raise HTTPException(status_code=404, detail="Workflow not found or access denied")

    return StreamingResponse(
        event_hub.stream_events(
            workflow_id=workflow_id,
            tenant_id=tenant_id,
            last_event_id=effective_last_id,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("runtime.main:app", host="0.0.0.0", port=8002, reload=False)
