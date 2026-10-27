"""
Gateway Client Interface.
Used exclusively by the agent runtime and external callers to interact with tools.
Guarantees the runtime never touches upstream MCP servers directly.
"""

from typing import Any, Dict, List, Optional
import httpx
from mcp.types import CallToolResult, TextContent
from gateway.server import GatewayService


class GatewayClient:
    """Client for invoking tools exclusively through the MCP SmartCache Gateway."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        gateway_service: Optional[GatewayService] = None,
        api_key: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ):
        self.base_url = base_url.rstrip("/") if base_url else None
        self.gateway_service = gateway_service
        self.api_key = api_key
        self.tenant_id = tenant_id

        if not self.base_url and not self.gateway_service:
            raise ValueError("GatewayClient requires either base_url or gateway_service.")

    def _headers(self, custom_tenant: Optional[str] = None) -> Dict[str, str]:
        headers: Dict[str, str] = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        tenant = custom_tenant or self.tenant_id
        if tenant:
            headers["X-Tenant-ID"] = tenant
        return headers

    async def list_tools(self) -> List[Dict[str, Any]]:
        """List unified namespaced tools available on the gateway."""
        if self.gateway_service:
            tools = await self.gateway_service.mcp_server.list_tools()
            return [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.input_schema,
                }
                for t in tools
            ]

        async with httpx.AsyncClient(base_url=self.base_url, timeout=10.0) as client:
            resp = await client.get("/tools", headers=self._headers())
            resp.raise_for_status()
            data = resp.json()
            return data.get("tools", [])

    async def call_tool(
        self,
        tool_name: str,
        arguments: Optional[Dict[str, Any]] = None,
        tenant_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Call a tool exclusively through the gateway."""
        arguments = arguments or {}
        resolved_tenant = tenant_id or self.tenant_id or "none"

        if self.gateway_service:
            result: CallToolResult = await self.gateway_service.mcp_server.call_tool(
                tool_name, arguments, scope=resolved_tenant
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
            }

        async with httpx.AsyncClient(base_url=self.base_url, timeout=15.0) as client:
            resp = await client.post(
                "/call",
                json={"tool": tool_name, "arguments": arguments},
                headers=self._headers(custom_tenant=tenant_id),
            )
            resp.raise_for_status()
            return resp.json()
