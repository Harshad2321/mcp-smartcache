"""
Phase 5 Tests — Dynamic Upstream Server Discovery and Atomic Registry Reload.

Tests:
1. Server configuration schema validation.
2. SSRF prevention: blocks cloud metadata endpoints (169.254.169.254).
3. Stdio shell injection prevention: blocks metacharacters (; & | ` $ < >).
4. Credential and secret redaction.
5. Atomic server reload and tool discovery.
6. Last known good registry preservation when discovery fails.
7. Namespace collision detection across distinct upstream servers.
"""

from unittest.mock import AsyncMock
import pytest

from mcp.types import Tool as MCPTool
from gateway.registry import ServerConfig, ServerRegistry, RegisteredTool
from gateway.server_validator import (
    ServerConfigValidationError,
    redact_sensitive_config,
    validate_server_config,
)


def test_server_config_validation_valid():
    """Valid SSE and stdio configurations pass validation."""
    validate_server_config(
        server_id="github_docs",
        name="GitHub Docs Server",
        transport="sse",
        connection_config={"url": "https://api.github.com/mcp/sse"},
    )
    validate_server_config(
        server_id="local_runner",
        name="Local Runner",
        transport="stdio",
        connection_config={"command": "python", "args": ["-m", "mcp_server"]},
    )


def test_server_config_validation_invalid_transport():
    """Unsupported transports are rejected."""
    with pytest.raises(ServerConfigValidationError, match="Unsupported transport"):
        validate_server_config(
            server_id="bad_transport",
            name="Bad Transport",
            transport="websocket_v2",  # Unsupported
            connection_config={},
        )


def test_server_config_validation_ssrf_cloud_metadata():
    """Cloud metadata IP (169.254.169.254) and metadata hostnames are strictly blocked."""
    with pytest.raises(ServerConfigValidationError, match="Access to metadata endpoint"):
        validate_server_config(
            server_id="ssrf_attempt",
            name="SSRF Server",
            transport="sse",
            connection_config={"url": "http://169.254.169.254/latest/meta-data/"},
        )

    with pytest.raises(ServerConfigValidationError, match="Access to metadata endpoint"):
        validate_server_config(
            server_id="ssrf_attempt_gcp",
            name="SSRF GCP Server",
            transport="sse",
            connection_config={"url": "http://metadata.google.internal/computeMetadata/v1/"},
        )


def test_server_config_validation_stdio_shell_injection():
    """Commands and arguments containing shell metacharacters are rejected."""
    with pytest.raises(ServerConfigValidationError, match="forbidden shell metacharacters"):
        validate_server_config(
            server_id="shell_inj_1",
            name="Shell Inj 1",
            transport="stdio",
            connection_config={"command": "python; rm -rf /"},
        )

    with pytest.raises(ServerConfigValidationError, match="forbidden shell metacharacters"):
        validate_server_config(
            server_id="shell_inj_2",
            name="Shell Inj 2",
            transport="stdio",
            connection_config={"command": "python", "args": ["--arg", "$(cat /etc/passwd)"]},
        )


def test_credential_redaction():
    """Sensitive keys in connection configuration are recursively redacted."""
    raw_config = {
        "url": "https://api.example.com",
        "api_key": "secret_token_12345",
        "headers": {
            "Authorization": "Bearer secret_bearer_token",
            "X-Client-ID": "public_client_1",
        },
        "nested": {
            "db_password": "super_secret_db_pass",
            "timeout": 30,
        },
    }

    clean = redact_sensitive_config(raw_config)
    assert clean["api_key"] == "[REDACTED]"
    assert clean["headers"]["Authorization"] == "[REDACTED]"
    assert clean["headers"]["X-Client-ID"] == "public_client_1"
    assert clean["nested"]["db_password"] == "[REDACTED]"
    assert clean["nested"]["timeout"] == 30


@pytest.mark.asyncio
async def test_atomic_reload_success():
    """Verify atomic reload discovers tools and registers them in the catalog."""
    registry = ServerRegistry()
    mock_server = AsyncMock()
    mock_server.list_tools.return_value = [
        MCPTool(name="get_data", description="Get data", input_schema={}),
        MCPTool(name="search_items", description="Search items", input_schema={}),
    ]

    cfg = ServerConfig(
        name="custom_srv",
        transport="in-process",
        server_instance=mock_server,
    )

    tools = await registry.reload_server(cfg)
    assert len(tools) == 2
    assert "custom_srv__get_data" in registry._tools
    assert "custom_srv__search_items" in registry._tools
    assert registry.get_server("custom_srv") == cfg


@pytest.mark.asyncio
async def test_atomic_reload_preserves_last_known_good_on_failure():
    """Verify a failed reload preserves the last known good server and tools."""
    registry = ServerRegistry()

    # 1. Register good initial version
    good_mock = AsyncMock()
    good_mock.list_tools.return_value = [
        MCPTool(name="good_tool", description="Good", input_schema={}),
    ]
    good_cfg = ServerConfig(
        name="fragile_srv",
        transport="in-process",
        server_instance=good_mock,
    )
    await registry.reload_server(good_cfg)
    assert "fragile_srv__good_tool" in registry._tools

    # 2. Candidate that fails during discovery
    bad_mock = AsyncMock()
    bad_mock.list_tools.side_effect = RuntimeError("Upstream crashed during list_tools")
    bad_cfg = ServerConfig(
        name="fragile_srv",
        transport="in-process",
        server_instance=bad_mock,
    )

    with pytest.raises(RuntimeError, match="Upstream crashed"):
        await registry.reload_server(bad_cfg)

    # 3. Last-known-good registry state is fully preserved!
    assert "fragile_srv__good_tool" in registry._tools
    assert registry.get_server("fragile_srv") == good_cfg


@pytest.mark.asyncio
async def test_atomic_reload_tool_namespace_collision():
    """Verify tool namespace collisions across distinct servers are rejected without affecting registry."""
    registry = ServerRegistry()

    # Server 1 owns "server2__collide_tool"
    srv1 = ServerConfig(name="server1", transport="in-process")
    registry._servers["server1"] = srv1
    registry._tools["server2__collide_tool"] = RegisteredTool(
        namespaced_name="server2__collide_tool",
        server_name="server1",  # Owned by server 1!
        original_name="collide_tool",
        description="Owned by server 1",
        input_schema={},
    )

    # Server 2 attempts to register colliding tool name
    srv2_mock = AsyncMock()
    srv2_mock.list_tools.return_value = [
        MCPTool(name="collide_tool", description="Attempting collision", input_schema={})
    ]
    srv2_cfg = ServerConfig(
        name="server2",
        transport="in-process",
        server_instance=srv2_mock,
    )

    with pytest.raises(ValueError, match="Tool namespace collision"):
        await registry.reload_server(srv2_cfg)

    # Server 1's ownership remains intact
    assert registry._tools["server2__collide_tool"].server_name == "server1"
