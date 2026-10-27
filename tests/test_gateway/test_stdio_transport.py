"""
Integration tests for out-of-process Stdio MCP transport.
Verifies real subprocess execution of mock servers over standard MCP JSON-RPC.
"""

import json
import os
import sys
import pytest
from gateway.registry import ServerRegistry, ServerConfig
from gateway.router import GatewayRouter
from gateway.client import GatewayClient
from gateway.server import GatewayService


@pytest.mark.asyncio
async def test_stdio_transport_end_to_end():
    """Verify registry and router communication with mock servers via stdio transport."""
    registry = ServerRegistry()

    # Determine script path for mock servers
    repo_script = "/app/mocks/repo_metadata.py"
    if not os.path.exists(repo_script):
        repo_script = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "mocks", "repo_metadata.py"))

    issues_script = "/app/mocks/issues.py"
    if not os.path.exists(issues_script):
        issues_script = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "mocks", "issues.py"))

    # Register both servers over stdio
    tools_repo = await registry.register_server(
        ServerConfig(
            name="repo_stdio",
            transport="stdio",
            connection_config={"command": sys.executable, "args": [repo_script]},
        )
    )
    assert len(tools_repo) == 3

    tools_issues = await registry.register_server(
        ServerConfig(
            name="issues_stdio",
            transport="stdio",
            connection_config={"command": sys.executable, "args": [issues_script]},
        )
    )
    assert len(tools_issues) == 3

    router = GatewayRouter(registry)

    # Call repo_stdio tool
    res1 = await router.route_tool_call(
        "repo_stdio__get_repo_info", {"owner": "google", "repo": "smartcache"}
    )
    assert not res1.is_error
    data1 = json.loads(res1.content[0].text)
    assert data1["full_name"] == "google/smartcache"
    assert data1["stars"] == 128

    # Call issues_stdio tool
    res2 = await router.route_tool_call(
        "issues_stdio__list_issues", {"owner": "google", "repo": "smartcache", "state": "open"}
    )
    assert not res2.is_error
    data2 = json.loads(res2.content[0].text)
    assert len(data2["issues"]) == 2

    # Call same-named get_status on both stdio servers to verify collision prevention
    res_status1 = await router.route_tool_call("repo_stdio__get_status", {})
    assert not res_status1.is_error
    data_status1 = json.loads(res_status1.content[0].text)
    assert data_status1["service"] == "repo_metadata"

    res_status2 = await router.route_tool_call("issues_stdio__get_status", {})
    assert not res_status2.is_error
    data_status2 = json.loads(res_status2.content[0].text)
    assert data_status2["service"] == "issues"
