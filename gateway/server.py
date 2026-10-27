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
from gateway.cache_policy import CachePolicyRegistry
from gateway.cache_store import RedisCacheStore
from gateway.singleflight import Singleflight


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
        scope: str = "none",
    ) -> CallToolResult:
        """Route tool invocation to upstream server via GatewayRouter."""
        return await self.router.route_tool_call(name, arguments, scope=scope)


class GatewayService:
    """
    Core Gateway orchestrator combining Registry, Router, MCP Server,
    Cache Policy Registry, Redis Cache Store, and Singleflight.

    Phase 2: cache_store is optional — when None, caching is disabled
    even for tools with enabled policies (graceful degradation).
    """

    def __init__(
        self,
        cache_store: Optional[RedisCacheStore] = None,
        policy_registry: Optional[CachePolicyRegistry] = None,
    ) -> None:
        self.registry = ServerRegistry()
        self.policy_registry: CachePolicyRegistry = policy_registry or CachePolicyRegistry()
        self.cache_store: Optional[RedisCacheStore] = cache_store
        self.singleflight: Singleflight = Singleflight()
        self.router = GatewayRouter(
            registry=self.registry,
            policy_registry=self.policy_registry,
            cache_store=self.cache_store,
            singleflight=self.singleflight,
        )
        self.mcp_server = GatewayMCPServer(self.router)
