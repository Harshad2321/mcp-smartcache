"""
End-to-End Integration Tests for Phase 1.
Verifies the GatewayClient interacts with tools solely through the Gateway surface.
"""

import json
import pytest
from gateway.server import GatewayService
from gateway.client import GatewayClient
from gateway.registry import ServerConfig
from mocks.repo_metadata import MockRepoMetadataServer
from mocks.issues import MockIssuesServer


@pytest.fixture
async def gateway_integration():
    service = GatewayService()
    repo_mock = MockRepoMetadataServer()
    issues_mock = MockIssuesServer()

    await service.registry.register_server(
        ServerConfig(name="repo_metadata", transport="in-process", server_instance=repo_mock)
    )
    await service.registry.register_server(
        ServerConfig(name="issues", transport="in-process", server_instance=issues_mock)
    )

    client = GatewayClient(gateway_service=service)
    return client, repo_mock, issues_mock


@pytest.mark.asyncio
async def test_gateway_client_end_to_end(gateway_integration):
    """Verify end-to-end tool discovery and calls via GatewayClient."""
    client, repo_mock, issues_mock = gateway_integration

    # 1. Discover unified tools
    tools = await client.list_tools()
    tool_names = [t["name"] for t in tools]
    assert len(tool_names) == 6
    assert "repo_metadata__get_repo_info" in tool_names
    assert "issues__list_issues" in tool_names
    assert "repo_metadata__get_status" in tool_names
    assert "issues__get_status" in tool_names

    # 2. Call repo_metadata tool
    res1 = await client.call_tool(
        "repo_metadata__get_repo_info", {"owner": "testorg", "repo": "testrepo"}
    )
    assert not res1["is_error"]
    repo_data = json.loads(res1["content"][0]["text"])
    assert repo_data["stars"] == 128
    assert repo_mock.call_counters["get_repo_info"] == 1

    # 3. Call issues tool
    res2 = await client.call_tool(
        "issues__list_issues", {"owner": "testorg", "repo": "testrepo", "state": "open"}
    )
    assert not res2["is_error"]
    issues_data = json.loads(res2["content"][0]["text"])
    assert len(issues_data["issues"]) == 2
    assert issues_mock.call_counters["list_issues"] == 1

    # 4. Verify collision handling: call each get_status
    res_status1 = await client.call_tool("repo_metadata__get_status", {})
    assert not res_status1["is_error"]
    status1_data = json.loads(res_status1["content"][0]["text"])
    assert status1_data["service"] == "repo_metadata"

    res_status2 = await client.call_tool("issues__get_status", {})
    assert not res_status2["is_error"]
    status2_data = json.loads(res_status2["content"][0]["text"])
    assert status2_data["service"] == "issues"

    # 5. Verify argument validation error handled cleanly
    res_err = await client.call_tool("repo_metadata__get_repo_info", {"owner": "missing_repo"})
    assert res_err["is_error"]
    err_body = json.loads(res_err["content"][0]["text"])
    assert err_body["error"] == "argument_validation_failed"
