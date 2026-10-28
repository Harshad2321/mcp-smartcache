"""
Server Registry for MCP SmartCache Gateway.
Handles registration of upstream MCP servers, tool discovery,
and namespacing to prevent tool-name collisions.
Supports in-process, stdio, and SSE transports, with staged atomic reload.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

from mcp.client.session import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.sse import sse_client

logger = logging.getLogger(__name__)

TransportType = Literal["stdio", "sse", "in-process"]


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
        to_remove = [k for k, tool in self._tools.items() if tool.server_name == server_name]
        for k in to_remove:
            del self._tools[k]

    async def _discover_raw_tools(self, config: ServerConfig) -> List[Any]:
        """Internal helper to discover raw tools from upstream transport."""
        raw_tools = []
        if config.transport == "in-process":
            if not config.server_instance:
                raise ValueError(f"In-process server '{config.name}' has no server_instance configured.")
            if hasattr(config.server_instance, "list_tools"):
                instance = config.server_instance
            else:
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
                logger.warning("Could not connect to upstream stdio server '%s': %s", config.name, err)
                raise ConnectionError(f"Could not connect to upstream stdio server '{config.name}': {err}")

        elif config.transport == "sse":
            url = config.connection_config.get("url")
            headers = config.connection_config.get("headers", {})
            timeout = float(config.connection_config.get("timeout", 10.0))
            try:
                async with sse_client(url, headers=headers) as (read_stream, write_stream):
                    async with ClientSession(read_stream, write_stream) as session:
                        await asyncio.wait_for(session.initialize(), timeout=timeout)
                        result = await asyncio.wait_for(session.list_tools(), timeout=timeout)
                        raw_tools = result.tools
            except Exception as err:
                logger.warning("Could not connect to upstream SSE server '%s': %s", config.name, err)
                raise ConnectionError(f"Could not connect to upstream SSE server '{config.name}': {err}")

        else:
            raise NotImplementedError(f"Transport '{config.transport}' discovery not implemented.")

        return raw_tools

    async def discover_tools(self, server_name: str) -> List[RegisteredTool]:
        """Discover tools exposed by an upstream server and store in catalog."""
        config = self._servers.get(server_name)
        if not config:
            raise ValueError(f"Server '{server_name}' is not registered.")

        try:
            raw_tools = await self._discover_raw_tools(config)
        except ConnectionError:
            return []

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

    async def reload_server(self, config: ServerConfig) -> List[RegisteredTool]:
        """
        Atomically reload or register an upstream server.
        Discovers tools first into a staging candidate set.
        Checks for tool collisions against other servers.
        If discovery or validation fails, existing server and tools remain completely untouched.
        """
        # Staged candidate discovery
        raw_tools = await self._discover_raw_tools(config)

        # Check collision against tools from other servers
        candidate_tools: Dict[str, RegisteredTool] = {}
        for tool in raw_tools:
            namespaced_name = f"{config.name}__{tool.name}"
            existing = self._tools.get(namespaced_name)
            if existing and existing.server_name != config.name:
                raise ValueError(
                    f"Tool namespace collision: tool '{namespaced_name}' is already registered by server '{existing.server_name}'"
                )
            registered = RegisteredTool(
                namespaced_name=namespaced_name,
                server_name=config.name,
                original_name=tool.name,
                description=tool.description or "",
                input_schema=tool.input_schema if hasattr(tool, "input_schema") else {},
            )
            candidate_tools[namespaced_name] = registered

        # Atomic swap
        self._servers[config.name] = config
        # Remove previous tools for this server
        for k in list(self._tools.keys()):
            if self._tools[k].server_name == config.name:
                del self._tools[k]
        # Install new candidate tools
        self._tools.update(candidate_tools)

        return list(candidate_tools.values())
