"""
MCP Server Registry.
Handles registration of upstream MCP servers, tool discovery,
and namespacing to prevent tool-name collisions.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional
import logging
from mcp.client.session import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters

logger = logging.getLogger(__name__)

TransportType = Literal["in-process", "stdio", "sse", "streamable-http"]


@dataclass
class ServerConfig:
    """Configuration for an upstream MCP server."""
    name: str
    transport: TransportType
    connection_config: Dict[str, Any] = field(default_factory=dict)
    credentials: Dict[str, str] = field(default_factory=dict)
    server_instance: Optional[Any] = None  # In-process MCPServer or Mock object


@dataclass
class RegisteredTool:
    """Tool registered in the unified gateway catalog."""
    namespaced_name: str
    server_name: str
    original_name: str
    description: str
    input_schema: Dict[str, Any]


class ServerRegistry:
    """In-memory server registry managing upstream MCP servers and namespaced tools."""

    def __init__(self):
        self._servers: Dict[str, ServerConfig] = {}
        self._tools: Dict[str, RegisteredTool] = {}

    def get_server(self, server_name: str) -> Optional[ServerConfig]:
        """Get an upstream server config by name."""
        return self._servers.get(server_name)

    def list_servers(self) -> List[ServerConfig]:
        """List all registered upstream servers."""
        return list(self._servers.values())

    def get_tool(self, namespaced_name: str) -> Optional[RegisteredTool]:
        """Lookup a registered tool by its namespaced name."""
        return self._tools.get(namespaced_name)

    def list_tools(self, server_name: Optional[str] = None) -> List[RegisteredTool]:
        """List all tools, optionally filtered by server name."""
        if server_name:
            return [t for t in self._tools.values() if t.server_name == server_name]
        return list(self._tools.values())

    async def register_server(self, config: ServerConfig) -> List[RegisteredTool]:
        """Register an upstream server and discover its tools with namespacing."""
        self._servers[config.name] = config
        discovered = await self.discover_tools(config.name)
        return discovered

    async def unregister_server(self, server_name: str) -> None:
        """Unregister an upstream server and remove its tools from the catalog."""
        if server_name in self._servers:
            del self._servers[server_name]
        to_remove = [k for k, v in self._tools.items() if v.server_name == server_name]
        for k in to_remove:
            del self._tools[k]

    async def discover_tools(self, server_name: str) -> List[RegisteredTool]:
        """Discover tools exposed by an upstream server and store in catalog."""
        config = self._servers.get(server_name)
        if not config:
            raise ValueError(f"Server '{server_name}' is not registered.")

        raw_tools = []
        if config.transport == "in-process":
            if not config.server_instance:
                raise ValueError(f"In-process server '{server_name}' has no server_instance configured.")
            # Support both direct MCPServer and wrapper objects exposing .mcp_server
            instance = getattr(config.server_instance, "mcp_server", config.server_instance)
            raw_tools = await instance.list_tools()

        elif config.transport == "stdio":
            command = config.connection_config.get("command", "python")
            args = config.connection_config.get("args", [])
            env = config.connection_config.get("env")
            cwd = config.connection_config.get("cwd")
            try:
                params = StdioServerParameters(command=command, args=args, env=env, cwd=cwd)
                async with stdio_client(params) as (read_stream, write_stream):
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        result = await session.list_tools()
                        raw_tools = result.tools
            except (FileNotFoundError, ProcessLookupError, ConnectionError, Exception) as err:
                logger.warning("Could not connect to upstream stdio server '%s': %s", server_name, err)
                return []
        else:
            raise NotImplementedError(f"Transport '{config.transport}' discovery not implemented in Phase 1.")

        registered_tools: List[RegisteredTool] = []
        for tool in raw_tools:
            namespaced_name = f"{config.name}__{tool.name}"
            registered = RegisteredTool(
                namespaced_name=namespaced_name,
                server_name=config.name,
                original_name=tool.name,
                description=tool.description or "",
                input_schema=tool.input_schema if hasattr(tool, "input_schema") else {},
            )
            self._tools[namespaced_name] = registered
            registered_tools.append(registered)

        return registered_tools
