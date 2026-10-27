"""
MCP SmartCache Gateway Main Service.
Provides REST management, health endpoints, metrics, and MCP routing layer.

Production Boundaries:
- Role-based API authentication & authorization (ADMIN vs CLIENT roles).
- Tenant ID extraction and validation for multi-tenant cache isolation.
- Dev mock isolation: Mock servers are excluded in production unless explicitly enabled.
- Request payload size protection and strict schema/input validation.
- Hardened CORS configuration.
- Singleflight deduplication and Redis-backed response caching.
"""

import json
import logging
import os
import re
import sys
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, HTTPException, Request, Response, Security, Depends
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from pydantic import BaseModel, Field, field_validator
from mcp.types import TextContent
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

from gateway.server import GatewayService
from gateway.registry import ServerConfig
from gateway.cache_policy import CachePolicy, CachePolicyRegistry
from gateway.cache_store import RedisCacheStore, CacheError
from gateway.auth import (
    AuthContext,
    Role,
    get_environment,
    is_auth_enabled,
    require_admin,
    require_client,
    validate_production_security_config,
)
from mocks.repo_metadata import MockRepoMetadataServer
from mocks.issues import MockIssuesServer

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Request size limit: default 1 MB
MAX_REQUEST_BYTES = int(os.environ.get("MAX_REQUEST_BYTES", str(1024 * 1024)))

# Safe tool and tag regex
TOOL_NAME_PATTERN = r"^[a-zA-Z0-9_-]+__[a-zA-Z0-9_-]+$"
TOOL_NAME_REGEX = re.compile(TOOL_NAME_PATTERN)
TAG_REGEX = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

# Global gateway service reference populated during lifespan
gateway_service: GatewayService = None  # type: ignore[assignment]


# ── Middleware ────────────────────────────────────────────────────────────────

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


# ── Request / Response Schemas ────────────────────────────────────────────────

class ToolCallRequest(BaseModel):
    tool: str = Field(
        ...,
        pattern=TOOL_NAME_PATTERN,
        description="Namespaced tool identifier in 'server__tool' format.",
    )
    arguments: Optional[Dict[str, Any]] = Field(
        default_factory=dict,
        description="Arguments dictionary conforming to the tool's input schema.",
    )


class CachePolicyRequest(BaseModel):
    tool_name: str = Field(
        ...,
        pattern=TOOL_NAME_PATTERN,
        description="Namespaced tool name to configure.",
    )
    enabled: bool = False
    read_only: bool = False
    ttl_seconds: int = Field(
        default=300,
        ge=1,
        le=2592000,
        description="TTL in seconds (minimum 1s, maximum 30 days).",
    )
    policy_version: str = Field(
        default="1",
        min_length=1,
        max_length=32,
        description="Policy version string included in cache keys.",
    )
    max_response_bytes: int = Field(
        default=524288,
        ge=512,
        le=10485760,
        description="Max serialised response size in bytes (512B to 10MB).",
    )
    invalidation_tags: List[str] = Field(default_factory=list)

    @field_validator("invalidation_tags")
    @classmethod
    def validate_invalidation_tags(cls, tags: List[str]) -> List[str]:
        for tag in tags:
            if not TAG_REGEX.match(tag):
                raise ValueError(
                    f"Invalid tag '{tag}'. Tags must be 1-64 characters matching alphanumeric, hyphens, or underscores."
                )
        return tags


# ── Upstream Server Loading & Environment Isolation ───────────────────────────

async def initialize_default_mocks():
    """Register default mock servers (only permitted in dev/test environments)."""
    repo_script = "/app/mocks/repo_metadata.py"
    issues_script = "/app/mocks/issues.py"

    if os.path.exists(repo_script) and os.path.exists(issues_script):
        await gateway_service.registry.register_server(
            ServerConfig(
                name="repo_metadata",
                transport="stdio",
                connection_config={"command": sys.executable, "args": [repo_script]},
            )
        )
        await gateway_service.registry.register_server(
            ServerConfig(
                name="issues",
                transport="stdio",
                connection_config={"command": sys.executable, "args": [issues_script]},
            )
        )
    else:
        # Fallback to in-process mock instances
        await gateway_service.registry.register_server(
            ServerConfig(
                name="repo_metadata",
                transport="in-process",
                server_instance=MockRepoMetadataServer(),
            )
        )
        await gateway_service.registry.register_server(
            ServerConfig(
                name="issues",
                transport="in-process",
                server_instance=MockIssuesServer(),
            )
        )


async def load_production_upstream_servers():
    """
    Load production upstream servers from an explicit configuration file.
    In production, mock servers are strictly excluded.
    """
    config_path = os.environ.get("UPSTREAM_CONFIG_PATH")
    if config_path and os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                configs = json.load(f)
            for item in configs.get("servers", []):
                cfg = ServerConfig(
                    name=item["name"],
                    transport=item.get("transport", "stdio"),
                    connection_config=item.get("connection_config", {}),
                )
                await gateway_service.registry.register_server(cfg)
            logger.info("Loaded %d production upstream servers from %s", len(configs.get("servers", [])), config_path)
        except Exception as err:
            logger.error("Failed to load upstream servers from %s: %s", config_path, err)
            raise RuntimeError(f"Failed to load production upstream servers: {err}") from err
    else:
        logger.info(
            "Production mode: No UPSTREAM_CONFIG_PATH specified. Starting with empty catalog; "
            "upstream servers must be registered via admin control."
        )


# ── Application Lifespan ──────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle manager: security validation, Redis connection, and upstream registration."""
    global gateway_service

    # 1. Validate security configuration in production
    validate_production_security_config()

    # 2. Redis Cache Store initialization
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    store = RedisCacheStore(redis_url=redis_url)
    try:
        await store.connect()
        logger.info("Gateway lifespan: Redis connected at %s", redis_url)
    except CacheError as err:
        logger.warning("Gateway lifespan: Redis unavailable — caching disabled (fail-open mode): %s", err)
        store = None  # type: ignore[assignment]

    gateway_service = GatewayService(cache_store=store)

    # 3. Environment-aware upstream server isolation
    env = get_environment()
    enable_dev_mocks = os.environ.get("ENABLE_DEV_MOCKS", "false").lower() in ("true", "1", "yes")

    if enable_dev_mocks or env in ("development", "test"):
        logger.warning(
            "ENVIRONMENT '%s': Development mocks are ENABLED. Mock servers registered.", env
        )
        await initialize_default_mocks()
    else:
        logger.info(
            "ENVIRONMENT '%s': Production mode active. Mock servers are STRICTLY EXCLUDED.", env
        )
        await load_production_upstream_servers()

    yield

    # Cleanup: close Redis on shutdown
    if store is not None:
        await store.close()


# ── FastAPI App Setup ─────────────────────────────────────────────────────────

app = FastAPI(
    title="MCP SmartCache Gateway",
    description="Production-grade MCP Gateway with Policy Enforcement, Redis Caching, and Singleflight",
    version="0.2.0",
    lifespan=lifespan,
)

# Apply content length guard
app.add_middleware(ContentLengthLimitMiddleware)

# Apply CORS with explicit origins (default: restrictive)
cors_origins_str = os.environ.get("CORS_ALLOWED_ORIGINS", "")
allowed_origins = [o.strip() for o in cors_origins_str.split(",") if o.strip()]
if allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )


# ── Public Endpoints ──────────────────────────────────────────────────────────

@app.get("/health")
async def health_check():
    """Public liveness probe for orchestrators and container healthchecks."""
    return {
        "status": "healthy",
        "service": "gateway",
        "version": "0.2.0",
        "environment": get_environment(),
    }


# ── Protected Operational Endpoints (Admin Only) ──────────────────────────────

@app.get("/metrics")
async def get_metrics(auth: AuthContext = Depends(require_admin)):
    """Expose Prometheus metrics. Restricted to ADMIN role."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ── Client / Agent Protected Endpoints ────────────────────────────────────────

@app.get("/status")
async def status(auth: AuthContext = Depends(require_client)):
    """Inspect status of registered upstream servers and discovered tool count."""
    servers = gateway_service.registry.list_servers()
    tools = gateway_service.registry.list_tools()
    return {
        "status": "ready",
        "environment": get_environment(),
        "server_count": len(servers),
        "servers": [s.name for s in servers],
        "tool_count": len(tools),
        "tools": [t.namespaced_name for t in tools],
        "tenant_id": auth.tenant_id,
    }


@app.get("/tools")
async def list_tools(auth: AuthContext = Depends(require_client)):
    """Return unified catalog of namespaced tools available for invocation."""
    tools = await gateway_service.mcp_server.list_tools()
    return {
        "tools": [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
            }
            for t in tools
        ]
    }


@app.post("/call")
async def call_tool(
    req: ToolCallRequest,
    auth: AuthContext = Depends(require_client),
):
    """
    Invoke an upstream MCP tool through the gateway.
    Applies schema validation, explicit cache policy, singleflight deduplication,
    and multi-tenant scope isolation based on the authenticated tenant.
    """
    result = await gateway_service.mcp_server.call_tool(
        req.tool,
        req.arguments or {},
        scope=auth.tenant_id,
    )
    contents = []
    for c in result.content:
        if isinstance(c, TextContent):
            contents.append({"type": "text", "text": c.text})
        else:
            contents.append(getattr(c, "model_dump", lambda: str(c))())
    return {
        "is_error": result.is_error,
        "content": contents,
        "tenant_id": auth.tenant_id,
    }


# ── Admin-Only Cache Policy Management Endpoints ──────────────────────────────

@app.post("/cache/policy")
async def set_cache_policy(
    req: CachePolicyRequest,
    auth: AuthContext = Depends(require_admin),
):
    """Register or update an explicit cache policy for a tool (ADMIN only)."""
    policy = CachePolicy(
        tool_name=req.tool_name,
        enabled=req.enabled,
        read_only=req.read_only,
        ttl_seconds=req.ttl_seconds,
        policy_version=req.policy_version,
        max_response_bytes=req.max_response_bytes,
        invalidation_tags=frozenset(req.invalidation_tags),
    )
    gateway_service.policy_registry.register(policy)
    return {"status": "registered", "policy": req.model_dump()}


@app.get("/cache/policy/{tool_name:path}")
async def get_cache_policy(
    tool_name: str,
    auth: AuthContext = Depends(require_client),
):
    """Retrieve the effective cache policy for a specific tool."""
    policy = gateway_service.policy_registry.get(tool_name)
    return {
        "tool_name": policy.tool_name,
        "enabled": policy.enabled,
        "read_only": policy.read_only,
        "ttl_seconds": policy.ttl_seconds,
        "policy_version": policy.policy_version,
        "max_response_bytes": policy.max_response_bytes,
        "invalidation_tags": list(policy.invalidation_tags),
    }


@app.delete("/cache/invalidate/tool/{tool_name:path}")
async def invalidate_tool_cache(
    tool_name: str,
    auth: AuthContext = Depends(require_admin),
):
    """Invalidate all cached entries for a specific tool (ADMIN only)."""
    if gateway_service.cache_store is None:
        raise HTTPException(status_code=503, detail="Cache store not available.")
    try:
        deleted = await gateway_service.cache_store.invalidate_tool(tool_name)
        return {"deleted_entries": deleted, "tool": tool_name}
    except CacheError as err:
        raise HTTPException(status_code=503, detail=str(err))


@app.delete("/cache/invalidate/tag/{tag}")
async def invalidate_tag_cache(
    tag: str,
    auth: AuthContext = Depends(require_admin),
):
    """Invalidate all cached entries for a tag group (ADMIN only)."""
    if not TAG_REGEX.match(tag):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid tag '{tag}'. Must be 1-64 characters matching alphanumeric, hyphens, or underscores.",
        )
    if gateway_service.cache_store is None:
        raise HTTPException(status_code=503, detail="Cache store not available.")
    try:
        deleted = await gateway_service.cache_store.invalidate_tag(tag)
        return {"deleted_entries": deleted, "tag": tag}
    except CacheError as err:
        raise HTTPException(status_code=503, detail=str(err))


if __name__ == "__main__":
    if "--stdio" in sys.argv:
        import asyncio
        asyncio.run(initialize_default_mocks())
        gateway_service.mcp_server.run(transport="stdio")
    else:
        import uvicorn
        uvicorn.run("gateway.main:app", host="0.0.0.0", port=8000, reload=False)
