"""
Unit tests for GatewayRouter.
Verifies routing, collision resolution, schema validation, and structured error handling.
"""

import json
import pytest
from gateway.registry import ServerRegistry, ServerConfig
from gateway.router import GatewayRouter
from mocks.repo_metadata import MockRepoMetadataServer
from mocks.issues import MockIssuesServer


@pytest.fixture
async def setup_gateway():
    registry = ServerRegistry()
    repo_server = MockRepoMetadataServer()
    issues_server = MockIssuesServer()

    await registry.register_server(
        ServerConfig(name="repo_metadata", transport="in-process", server_instance=repo_server)
    )
    await registry.register_server(
        ServerConfig(name="issues", transport="in-process", server_instance=issues_server)
    )

    router = GatewayRouter(registry)
    return router, repo_server, issues_server


@pytest.mark.asyncio
async def test_successful_routing_to_both_servers(setup_gateway):
    """Test successful tool calls to both mock servers through the router."""
    router, repo_server, issues_server = setup_gateway

    # Call repo_metadata
    res1 = await router.route_tool_call(
        "repo_metadata__get_repo_info", {"owner": "google", "repo": "smartcache"}
    )
    assert not res1.is_error
    data1 = json.loads(res1.content[0].text)
    assert data1["full_name"] == "google/smartcache"
    assert data1["stars"] == 128

    # Call issues
    res2 = await router.route_tool_call(
        "issues__get_issue", {"owner": "google", "repo": "smartcache", "issue_id": 101}
    )
    assert not res2.is_error
    data2 = json.loads(res2.content[0].text)
    assert data2["id"] == 101


@pytest.mark.asyncio
async def test_collision_resolution(setup_gateway):
    """Demonstrate that two servers exposing same-named 'get_status' tools do not collide."""
    router, repo_server, issues_server = setup_gateway

    # Call repo_metadata's get_status
    res_repo = await router.route_tool_call("repo_metadata__get_status", {})
    assert not res_repo.is_error
    data_repo = json.loads(res_repo.content[0].text)
    assert data_repo["service"] == "repo_metadata"
    assert data_repo["type"] == "repository_indexer"
    assert repo_server.call_counters["get_status"] == 1
    assert issues_server.call_counters["get_status"] == 0

    # Call issues's get_status
    res_issues = await router.route_tool_call("issues__get_status", {})
    assert not res_issues.is_error
    data_issues = json.loads(res_issues.content[0].text)
    assert data_issues["service"] == "issues"
    assert data_issues["type"] == "issue_tracker"
    assert issues_server.call_counters["get_status"] == 1


@pytest.mark.asyncio
async def test_argument_schema_validation(setup_gateway):
    """Verify that calling a tool with invalid arguments produces a structured error."""
    router, _, _ = setup_gateway

    # Missing required 'repo' argument
    res = await router.route_tool_call(
        "repo_metadata__get_repo_info", {"owner": "google"}
    )
    assert res.is_error
    err_payload = json.loads(res.content[0].text)
    assert err_payload["is_error"] is True
    assert err_payload["error"] == "argument_validation_failed"
    assert "'repo' is a required property" in err_payload["detail"]


@pytest.mark.asyncio
async def test_unregistered_tool_error(setup_gateway):
    """Verify calling an unknown tool produces a structured error."""
    router, _, _ = setup_gateway

    res = await router.route_tool_call("unknown_server__unknown_tool", {})
    assert res.is_error
    err_payload = json.loads(res.content[0].text)
    assert err_payload["error"] == "tool_not_found"


@pytest.mark.asyncio
async def test_upstream_error_injection(setup_gateway):
    """Verify that upstream server exception is caught and returned as structured error."""
    router, repo_server, _ = setup_gateway
    repo_server.inject_error = True
    repo_server.error_message = "Simulated upstream database failure"

    res = await router.route_tool_call(
        "repo_metadata__get_repo_info", {"owner": "google", "repo": "smartcache"}
    )
    assert res.is_error
    err_payload = json.loads(res.content[0].text)
    assert err_payload["error"] == "upstream_error"
    assert "Simulated upstream database failure" in err_payload["detail"]


@pytest.mark.asyncio
async def test_upstream_malformed_response(setup_gateway):
    """Verify upstream malformed response is detected and returned as structured error."""
    router, repo_server, _ = setup_gateway
    repo_server.inject_malformed = True

    res = await router.route_tool_call(
        "repo_metadata__get_repo_info", {"owner": "google", "repo": "smartcache"}
    )
    assert res.is_error
    err_payload = json.loads(res.content[0].text)
    assert err_payload["error"] == "upstream_malformed_response"


@pytest.mark.asyncio
async def test_unreachable_server_handling():
    """Verify that an unreachable server produces a structured error rather than a crash."""
    registry = ServerRegistry()
    # Register a server with a non-existent executable to simulate unreachable stdio server
    await registry.register_server(
        ServerConfig(
            name="dead_server",
            transport="stdio",
            connection_config={"command": "nonexistent_executable_12345"},
        )
    )
    # Register dummy tool manually for routing test
    from gateway.registry import RegisteredTool
    registry._tools["dead_server__ping"] = RegisteredTool(
        namespaced_name="dead_server__ping",
        server_name="dead_server",
        original_name="ping",
        description="ping",
        input_schema={},
    )

    router = GatewayRouter(registry)
    res = await router.route_tool_call("dead_server__ping", {})
    assert res.is_error
    err_payload = json.loads(res.content[0].text)
    assert err_payload["error"] == "upstream_unreachable"
