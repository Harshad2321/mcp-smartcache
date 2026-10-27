"""
MCP SmartCache Gateway Main Service.
Provides REST management/health endpoints and MCP routing layer.
Discovers and namespaces upstream tools from registered servers.
Zero caching and zero deduplication in Phase 1 (pure passthrough).
"""

import os
import sys
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from mcp.types import TextContent

from gateway.server import GatewayService
from gateway.registry import ServerConfig
from mocks.repo_metadata import MockRepoMetadataServer
from mocks.issues import MockIssuesServer

gateway_service = GatewayService()


class ToolCallRequest(BaseModel):
    tool: str = Field(..., description="Namespaced tool name, e.g. repo_metadata__get_repo_info")
    arguments: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Arguments dictionary")


async def initialize_default_mocks():
    """Register default mock servers on gateway startup."""
    # Check if files exist at /app/mocks for stdio or fallback to in-process instances
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
        # In-process mock instances
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle manager: register default upstream servers on boot."""
    await initialize_default_mocks()
    yield


app = FastAPI(
    title="MCP SmartCache Gateway",
    description="MCP Gateway server with tool registry, routing, and policy enforcement",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "service": "gateway",
        "version": "0.1.0",
    }


@app.get("/status")
async def status():
    servers = gateway_service.registry.list_servers()
    tools = gateway_service.registry.list_tools()
    return {
        "status": "ready",
        "server_count": len(servers),
        "servers": [s.name for s in servers],
        "tool_count": len(tools),
        "tools": [t.namespaced_name for t in tools],
    }


@app.get("/tools")
async def list_tools():
    """Return unified catalog of namespaced tools."""
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
async def call_tool(req: ToolCallRequest):
    """Invoke a tool through the gateway router."""
    result = await gateway_service.mcp_server.call_tool(req.tool, req.arguments or {})
    contents = []
    for c in result.content:
        if isinstance(c, TextContent):
            contents.append({"type": "text", "text": c.text})
        else:
            contents.append(getattr(c, "model_dump", lambda: str(c))())
    return {
        "is_error": result.is_error,
        "content": contents,
    }


if __name__ == "__main__":
    if "--stdio" in sys.argv:
        # Run standard MCP stdio server
        import asyncio
        asyncio.run(initialize_default_mocks())
        gateway_service.mcp_server.run(transport="stdio")
    else:
        import uvicorn
        uvicorn.run("gateway.main:app", host="0.0.0.0", port=8000, reload=False)
