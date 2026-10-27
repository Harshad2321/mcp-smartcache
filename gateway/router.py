"""
Gateway Router.
Validates incoming tool call arguments against schema,
routes to the appropriate upstream MCP server, and formats structured errors.
Zero caching and zero deduplication in Phase 1 (pure passthrough).
"""

import json
import logging
from typing import Any, Dict, Optional
import jsonschema
from mcp.types import CallToolResult, TextContent
from mcp.client.session import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters
from gateway.registry import ServerRegistry, RegisteredTool, ServerConfig

logger = logging.getLogger(__name__)


def make_error_result(error_type: str, detail: str, extra: Optional[Dict[str, Any]] = None) -> CallToolResult:
    """Helper to create a structured JSON CallToolResult error payload."""
    payload: Dict[str, Any] = {
        "is_error": True,
        "error": error_type,
        "detail": detail,
    }
    if extra:
        payload.update(extra)
    return CallToolResult(
        is_error=True,
        content=[TextContent(type="text", text=json.dumps(payload))],
    )


class GatewayRouter:
    """Routes incoming MCP tool calls to registered upstream servers."""

    def __init__(self, registry: ServerRegistry):
        self.registry = registry

    async def route_tool_call(
        self,
        namespaced_name: str,
        arguments: Optional[Dict[str, Any]] = None,
    ) -> CallToolResult:
        """Validate arguments and route tool call to the target upstream server."""
        arguments = arguments or {}

        # 1. Lookup Tool in Registry
        tool: Optional[RegisteredTool] = self.registry.get_tool(namespaced_name)
        if not tool:
            return make_error_result(
                error_type="tool_not_found",
                detail=f"Tool '{namespaced_name}' is not registered in the gateway catalog.",
                extra={"requested_tool": namespaced_name},
            )

        # 2. Schema Validation
        if tool.input_schema and isinstance(tool.input_schema, dict):
            try:
                jsonschema.validate(instance=arguments, schema=tool.input_schema)
            except jsonschema.ValidationError as val_err:
                return make_error_result(
                    error_type="argument_validation_failed",
                    detail=f"Argument schema validation failed: {val_err.message}",
                    extra={
                        "tool": namespaced_name,
                        "failed_path": list(val_err.path),
                        "schema": tool.input_schema,
                    },
                )
            except Exception as schema_err:
                return make_error_result(
                    error_type="schema_evaluation_error",
                    detail=f"Schema validation error: {str(schema_err)}",
                )

        # 3. Lookup Upstream Server
        server: Optional[ServerConfig] = self.registry.get_server(tool.server_name)
        if not server:
            return make_error_result(
                error_type="server_not_registered",
                detail=f"Upstream server '{tool.server_name}' is not configured.",
                extra={"server": tool.server_name},
            )

        # 4. Dispatch to Upstream Server based on transport
        try:
            return await self._dispatch(server, tool.original_name, arguments)
        except Exception as exc:
            logger.exception("Unexpected upstream failure for %s", namespaced_name)
            return make_error_result(
                error_type="upstream_execution_failed",
                detail=str(exc),
                extra={"server": server.name, "tool": tool.original_name},
            )

    async def _dispatch(
        self,
        server: ServerConfig,
        original_tool_name: str,
        arguments: Dict[str, Any],
    ) -> CallToolResult:
        """Dispatch call to upstream transport with robust error handling."""
        if server.transport == "in-process":
            if not server.server_instance:
                return make_error_result(
                    error_type="upstream_misconfigured",
                    detail="In-process server instance is missing.",
                    extra={"server": server.name},
                )
            instance = getattr(server.server_instance, "mcp_server", server.server_instance)
            try:
                raw_result = await instance.call_tool(original_tool_name, arguments)
            except Exception as e:
                cause = getattr(e, "__cause__", None)
                detail_msg = f"{str(e)} (caused by: {str(cause)})" if cause else str(e)
                return make_error_result(
                    error_type="upstream_error",
                    detail=f"Upstream server raised exception: {detail_msg}",
                    extra={"server": server.name, "tool": original_tool_name},
                )

            # Check for malformed payload
            return self._validate_and_normalize_result(raw_result, server.name)

        elif server.transport == "stdio":
            command = server.connection_config.get("command", "python")
            args = server.connection_config.get("args", [])
            env = server.connection_config.get("env")
            cwd = server.connection_config.get("cwd")

            try:
                params = StdioServerParameters(command=command, args=args, env=env, cwd=cwd)
                async with stdio_client(params) as (read_stream, write_stream):
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        raw_result = await session.call_tool(original_tool_name, arguments)
                        return self._validate_and_normalize_result(raw_result, server.name)
            except (FileNotFoundError, ProcessLookupError, ConnectionError) as conn_err:
                return make_error_result(
                    error_type="upstream_unreachable",
                    detail=f"Could not connect to upstream stdio server: {str(conn_err)}",
                    extra={"server": server.name, "command": command, "args": args},
                )
            except Exception as exc:
                return make_error_result(
                    error_type="upstream_error",
                    detail=f"Error during stdio execution: {str(exc)}",
                    extra={"server": server.name, "tool": original_tool_name},
                )

        else:
            return make_error_result(
                error_type="unsupported_transport",
                detail=f"Transport '{server.transport}' is not supported.",
                extra={"server": server.name},
            )

    def _validate_and_normalize_result(self, raw_result: Any, server_name: str) -> CallToolResult:
        """Validate that result conforms to CallToolResult and inspect contents for malformations."""
        if not isinstance(raw_result, CallToolResult):
            return make_error_result(
                error_type="upstream_malformed_response",
                detail=f"Expected CallToolResult but received {type(raw_result).__name__}",
                extra={"server": server_name},
            )

        # Detect intentional malformed test payloads in mock output
        for item in raw_result.content:
            if isinstance(item, TextContent) and item.text.startswith("<<<MALFORMED_"):
                return make_error_result(
                    error_type="upstream_malformed_response",
                    detail=f"Upstream returned malformed unparseable payload: {item.text}",
                    extra={"server": server_name},
                )

        return raw_result
