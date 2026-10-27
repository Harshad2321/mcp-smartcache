"""
Unit tests for deterministic mock MCP servers.
Verifies tool behavior, call counters, artificial delay, and error/fault injection.
"""

import asyncio
import json
import time
import pytest
from mocks.repo_metadata import MockRepoMetadataServer
from mocks.issues import MockIssuesServer


@pytest.mark.asyncio
async def test_repo_metadata_server_tools_and_counter():
    """Verify repo_metadata tools return deterministic data and increment counters."""
    server = MockRepoMetadataServer()
    server.reset_counters()

    # Call get_repo_info
    res1 = await server.mcp_server.call_tool("get_repo_info", {"owner": "test-org", "repo": "test-repo"})
    assert not res1.is_error
    data1 = json.loads(res1.content[0].text)
    assert data1["owner"] == "test-org"
    assert data1["repo"] == "test-repo"
    assert data1["stars"] == 128
    assert server.call_counters["get_repo_info"] == 1

    # Call get_commit_log
    res2 = await server.mcp_server.call_tool("get_commit_log", {"owner": "test-org", "repo": "test-repo", "limit": 2})
    assert not res2.is_error
    data2 = json.loads(res2.content[0].text)
    assert len(data2["commits"]) == 2
    assert server.call_counters["get_commit_log"] == 1


@pytest.mark.asyncio
async def test_issues_server_tools_and_counter():
    """Verify issues tools return deterministic data and filter by state."""
    server = MockIssuesServer()
    server.reset_counters()

    # Call list_issues (open)
    res1 = await server.mcp_server.call_tool("list_issues", {"owner": "test-org", "repo": "test-repo", "state": "open"})
    assert not res1.is_error
    data1 = json.loads(res1.content[0].text)
    assert len(data1["issues"]) == 2
    assert server.call_counters["list_issues"] == 1

    # Call get_issue
    res2 = await server.mcp_server.call_tool("get_issue", {"owner": "test-org", "repo": "test-repo", "issue_id": 101})
    assert not res2.is_error
    data2 = json.loads(res2.content[0].text)
    assert data2["id"] == 101
    assert server.call_counters["get_issue"] == 1


@pytest.mark.asyncio
async def test_mock_servers_artificial_delay():
    """Verify artificial delay hook is respected."""
    server = MockRepoMetadataServer()
    server.artificial_delay = 0.15

    start = time.perf_counter()
    await server.mcp_server.call_tool("get_status", {})
    elapsed = time.perf_counter() - start

    assert elapsed >= 0.14


@pytest.mark.asyncio
async def test_mock_servers_error_injection():
    """Verify error injection hook raises an exception."""
    server = MockIssuesServer()
    server.inject_error = True
    server.error_message = "Custom failure triggered"

    with pytest.raises(Exception) as exc_info:
        await server.mcp_server.call_tool("list_issues", {"owner": "o", "repo": "r"})
    assert "Custom failure triggered" in str(exc_info.value) or "Custom failure triggered" in str(getattr(exc_info.value, "__cause__", ""))


@pytest.mark.asyncio
async def test_mock_servers_malformed_injection():
    """Verify malformed payload injection hook."""
    server = MockRepoMetadataServer()
    server.inject_malformed = True

    res = await server.mcp_server.call_tool("get_repo_info", {"owner": "o", "repo": "r"})
    assert "<<<MALFORMED_" in res.content[0].text
