"""
Unit tests for the ServerRegistry.
Verifies server registration, tool discovery, and namespacing.
"""

import pytest
from gateway.registry import ServerRegistry, ServerConfig
from mocks.repo_metadata import MockRepoMetadataServer
from mocks.issues import MockIssuesServer


@pytest.mark.asyncio
async def test_server_registration_and_discovery():
    """Verify in-process server registration and namespaced tool catalog generation."""
    registry = ServerRegistry()

    repo_server = MockRepoMetadataServer()
    issues_server = MockIssuesServer()

    # Register repo_metadata
    tools_repo = await registry.register_server(
        ServerConfig(name="repo_metadata", transport="in-process", server_instance=repo_server)
    )
    assert len(tools_repo) == 3
    assert any(t.namespaced_name == "repo_metadata__get_repo_info" for t in tools_repo)
    assert any(t.namespaced_name == "repo_metadata__get_commit_log" for t in tools_repo)
    assert any(t.namespaced_name == "repo_metadata__get_status" for t in tools_repo)

    # Register issues
    tools_issues = await registry.register_server(
        ServerConfig(name="issues", transport="in-process", server_instance=issues_server)
    )
    assert len(tools_issues) == 3
    assert any(t.namespaced_name == "issues__list_issues" for t in tools_issues)
    assert any(t.namespaced_name == "issues__get_issue" for t in tools_issues)
    assert any(t.namespaced_name == "issues__get_status" for t in tools_issues)

    # Check total catalog
    all_tools = registry.list_tools()
    assert len(all_tools) == 6


@pytest.mark.asyncio
async def test_server_unregistration():
    """Verify unregistering a server removes its tools from the catalog."""
    registry = ServerRegistry()
    repo_server = MockRepoMetadataServer()

    await registry.register_server(
        ServerConfig(name="repo_metadata", transport="in-process", server_instance=repo_server)
    )
    assert len(registry.list_tools()) == 3

    await registry.unregister_server("repo_metadata")
    assert len(registry.list_tools()) == 0
    assert registry.get_server("repo_metadata") is None
