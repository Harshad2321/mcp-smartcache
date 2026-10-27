"""
Gateway MCP Server.
Implements the MCP server surface for the gateway using official MCP Python SDK v2.
Exposes the unified namespaced tool catalog and delegates tool invocations to the router.
"""

from typing import Any, Dict, List, Optional
from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, Tool as MCPTool
from gateway.registry import ServerRegistry, RegisteredTool
from gateway.router import GatewayRouter


class GatewayMCPServer(MCPServer):
    """MCP Server that presents a unified catalog of namespaced tools."""

    def __init__(self, router: GatewayRouter, name: str = "mcp-smartcache-gateway"):
        super().__init__(name=name)
        self.router = router

    async def list_tools(self) -> List[MCPTool]:
        """Return the unified list of tools discovered across all registered servers."""
        registered_tools: List[RegisteredTool] = self.router.registry.list_tools()
        mcp_tools: List[MCPTool] = []
        for t in registered_tools:
            mcp_tools.append(
                MCPTool(
                    name=t.namespaced_name,
                    description=t.description,
                    input_schema=t.input_schema,
                )
            )
        return mcp_tools

    async def call_tool(
        self,
        name: str,
        arguments: Dict[str, Any],
        context: Optional[Any] = None,
    ) -> CallToolResult:
        """Route tool invocation to upstream server via GatewayRouter."""
        return await self.router.route_tool_call(name, arguments)


class GatewayService:
    """Core Gateway orchestrator combining Registry, Router, and MCP Server."""

    def __init__(self):
        self.registry = ServerRegistry()
        self.router = GatewayRouter(self.registry)
        self.mcp_server = GatewayMCPServer(self.router)
