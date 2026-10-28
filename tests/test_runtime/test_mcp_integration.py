"""
Integration tests for runtime MCP gateway communication.
Verifies tool discovery, invocation, error propagation, and session cleanup
using real MCP protocol sessions through the gateway.
"""

import json
import pytest
from mcp.types import CallToolResult, TextContent

from runtime.mcp_client import MCPGatewayClient, MCPClientError


@pytest.mark.asyncio
async def test_mcp_client_tool_discovery():
    """Verify tool discovery through gateway MCP interface preserves names and schemas."""
    client = MCPGatewayClient(transport="stdio")
    tools = await client.list_tools()

    assert len(tools) >= 5
    tool_names = [t["name"] for t in tools]
    assert "repo_metadata__get_repo_info" in tool_names
    assert "repo_metadata__get_commit_log" in tool_names
    assert "issues__list_issues" in tool_names

    # Verify input schemas are preserved
    repo_info_tool = next(t for t in tools if t["name"] == "repo_metadata__get_repo_info")
    assert "input_schema" in repo_info_tool
    assert "properties" in repo_info_tool["input_schema"]
    assert "owner" in repo_info_tool["input_schema"]["properties"]


@pytest.mark.asyncio
async def test_mcp_client_tool_invocation():
    """Verify tool invocation through the gateway MCP protocol interface."""
    client = MCPGatewayClient(transport="stdio")
    result: CallToolResult = await client.call_tool(
        tool_name="repo_metadata__get_repo_info",
        arguments={"owner": "google", "repo": "smartcache"},
    )

    assert result.is_error is False
    assert len(result.content) >= 1
    assert isinstance(result.content[0], TextContent)

    payload = json.loads(result.content[0].text)
    assert payload["owner"] == "google"
    assert payload["repo"] == "smartcache"
    assert payload["stars"] == 128
    assert payload["forks"] == 32


@pytest.mark.asyncio
async def test_mcp_client_upstream_error_propagation():
    """Verify that upstream tool errors are properly propagated as error results."""
    client = MCPGatewayClient(transport="stdio")
    # Calling with missing required arguments or unknown tool
    result: CallToolResult = await client.call_tool(
        tool_name="repo_metadata__get_repo_info",
        arguments={"owner": "google"},  # Missing 'repo'
    )
    # Gateway router returns a structured JSON error CallToolResult
    assert result.is_error is True
    text = result.content[0].text
    assert "error" in text.lower() or "validation" in text.lower() or "schema" in text.lower()


@pytest.mark.asyncio
async def test_mcp_client_session_context_cleanup():
    """Verify that MCP client sessions open and close cleanly without leaving dangling resources."""
    client = MCPGatewayClient(transport="stdio")
    async with client.session() as sess:
        tools = await sess.list_tools()
        assert len(tools.tools) > 0
    # Session exited cleanly
