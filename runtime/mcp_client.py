"""
Official MCP Gateway Client for Agent Runtime.
Connects exclusively to the MCP SmartCache Gateway over standard MCP protocol
(Stdio or SSE transport) using the official MCP Python SDK.
Never connects directly to upstream or mock servers.
"""

import asyncio
import json
import logging
import os
import sys
from typing import Any, Dict, List, Optional
from contextlib import asynccontextmanager

from mcp.client.session import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.types import CallToolResult, Tool as MCPTool, TextContent

logger = logging.getLogger(__name__)


class MCPClientError(Exception):
    """Base error for runtime MCP client communication failures."""
    def __init__(self, message: str, error_type: str = "mcp_client_error", details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.error_type = error_type
        self.details = details or {}


class MCPGatewayClient:
    """
    Client for interacting with the MCP SmartCache Gateway using the official MCP SDK.
    Supports both SSE HTTP transport and Stdio subprocess transport.
    """

    def __init__(
        self,
        gateway_url: Optional[str] = None,
        transport: str = "auto",
        api_key: Optional[str] = None,
        tenant_id: str = "default",
        timeout_seconds: float = 30.0,
    ):
        self.gateway_url = gateway_url or os.environ.get("GATEWAY_MCP_URL", "http://gateway:8000")
        self.api_key = api_key or os.environ.get("GATEWAY_API_KEY")
        self.tenant_id = tenant_id
        self.timeout_seconds = timeout_seconds

        # Determine transport
        env_transport = os.environ.get("GATEWAY_TRANSPORT", "").lower()
        if transport == "auto":
            if env_transport in ("stdio", "sse"):
                self.transport = env_transport
            elif self.gateway_url == "stdio" or not self.gateway_url.startswith("http"):
                self.transport = "stdio"
            else:
                self.transport = "sse"
        else:
            self.transport = transport

        self._session: Optional[ClientSession] = None
        self._exit_stack: Optional[Any] = None

    @asynccontextmanager
    async def session(self):
        """Context manager establishing and tearing down an official MCP ClientSession."""
        session = None
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if self.tenant_id:
            headers["X-Tenant-ID"] = self.tenant_id

        try:
            if self.transport == "stdio":
                # Stdio transport runs gateway module in subprocess
                redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
                env = dict(os.environ)
                env["REDIS_URL"] = redis_url
                params = StdioServerParameters(
                    command=sys.executable,
                    args=["-m", "gateway.main", "--stdio"],
                    env=env,
                )
                async with stdio_client(params) as (read_stream, write_stream):
                    async with ClientSession(read_stream, write_stream) as sess:
                        await sess.initialize()
                        yield sess

            elif self.transport == "sse":
                # SSE transport connects to mounted /mcp/sse endpoint
                base = self.gateway_url.rstrip("/")
                sse_endpoint = f"{base}/mcp/sse" if not base.endswith("/sse") else base
                try:
                    async with sse_client(sse_endpoint, headers=headers, timeout=self.timeout_seconds) as (read_stream, write_stream):
                        async with ClientSession(read_stream, write_stream) as sess:
                            await sess.initialize()
                            yield sess
                except Exception as sse_err:
                    # In local dev or test environments where gateway HTTP service may not be running,
                    # provide automatic fallback to stdio transport if available
                    logger.warning("SSE connection to %s failed (%s), attempting fallback to stdio transport", sse_endpoint, sse_err)
                    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
                    env = dict(os.environ)
                    env["REDIS_URL"] = redis_url
                    params = StdioServerParameters(
                        command=sys.executable,
                        args=["-m", "gateway.main", "--stdio"],
                        env=env,
                    )
                    async with stdio_client(params) as (read_stream, write_stream):
                        async with ClientSession(read_stream, write_stream) as sess:
                            await sess.initialize()
                            yield sess
            else:
                raise MCPClientError(f"Unsupported transport '{self.transport}'", error_type="unsupported_transport")

        except Exception as exc:
            if isinstance(exc, MCPClientError):
                raise
            raise MCPClientError(
                f"Failed to communicate with gateway over MCP ({self.transport}): {str(exc)}",
                error_type="gateway_connection_error",
                details={"transport": self.transport, "error": str(exc)},
            ) from exc

    async def list_tools(self) -> List[Dict[str, Any]]:
        """Discover tools through the gateway MCP interface."""
        async with self.session() as sess:
            result = await sess.list_tools()
            tools: List[Dict[str, Any]] = []
            for t in result.tools:
                tools.append({
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.input_schema,
                })
            return tools

    async def call_tool(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        timeout: Optional[float] = None,
    ) -> CallToolResult:
        """Invoke a tool through the gateway MCP interface."""
        call_timeout = timeout or self.timeout_seconds
        async with self.session() as sess:
            try:
                result = await asyncio.wait_for(
                    sess.call_tool(tool_name, arguments),
                    timeout=call_timeout,
                )
                return result
            except asyncio.TimeoutError:
                raise MCPClientError(
                    f"Tool '{tool_name}' execution timed out after {call_timeout}s",
                    error_type="tool_timeout",
                    details={"tool_name": tool_name, "timeout_seconds": call_timeout},
                )
            except Exception as err:
                raise MCPClientError(
                    f"Tool '{tool_name}' execution failed: {str(err)}",
                    error_type="tool_execution_error",
                    details={"tool_name": tool_name, "error": str(err)},
                ) from err
