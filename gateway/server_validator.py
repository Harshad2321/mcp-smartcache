"""
Phase 5: Upstream Server Registration Validation and Security Boundaries.
Protects against SSRF, loopback access in production, cloud metadata IP access,
shell metacharacter injection in stdio transport, and redacts sensitive credentials.
"""

from __future__ import annotations

import ipaddress
import os
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

# Cloud metadata services and forbidden hostnames
FORBIDDEN_HOSTS = {
    "169.254.169.254",          # AWS / GCP / Azure metadata IP
    "metadata.google.internal",  # GCP metadata hostname
    "metadata.goog",            # GCP metadata short
    "100.100.100.200",          # Alibaba cloud metadata
}

# Shell metacharacters forbidden in stdio command and arguments
SHELL_METACHARS_REGEX = re.compile(r"[;&|`$<>]")


class ServerConfigValidationError(ValueError):
    """Raised when an upstream server configuration violates security or schema rules."""
    pass


def is_production() -> bool:
    """Return True if running in production mode."""
    return os.environ.get("ENVIRONMENT", "development").lower() == "production"


def is_dev_mocks_enabled() -> bool:
    """Return True if development mock / local network testing is enabled."""
    return os.environ.get("ENABLE_DEV_MOCKS", "false").lower() in ("true", "1")


def redact_sensitive_config(config: Any) -> Any:
    """Recursively redact sensitive fields like tokens, keys, and passwords."""
    sensitive_keys = {
        "password", "secret", "token", "api_key", "key",
        "authorization", "auth", "credential", "private_key",
    }
    if isinstance(config, dict):
        clean = {}
        for k, v in config.items():
            if any(s in k.lower() for s in sensitive_keys):
                clean[k] = "[REDACTED]"
            else:
                clean[k] = redact_sensitive_config(v)
        return clean
    elif isinstance(config, list):
        return [redact_sensitive_config(item) for item in config]
    return config


def validate_server_config(
    server_id: str,
    name: str,
    transport: str,
    connection_config: Dict[str, Any],
) -> None:
    """
    Validate upstream server registration parameters.
    Raises ServerConfigValidationError if parameters are malformed or insecure.
    """
    # 1. Server ID validation
    if not server_id or not re.match(r"^[a-zA-Z0-9_\-\.]{1,64}$", server_id):
        raise ServerConfigValidationError(
            f"Invalid server_id '{server_id}'. Must be 1-64 alphanumeric characters, underscores, hyphens, or dots."
        )

    # 2. Server Name validation
    if not name or len(name.strip()) == 0 or len(name) > 255:
        raise ServerConfigValidationError("Server name must be non-empty and under 255 characters.")

    # 3. Transport Type validation
    allowed_transports = {"stdio", "sse", "in-process"}
    if transport not in allowed_transports:
        raise ServerConfigValidationError(
            f"Unsupported transport '{transport}'. Allowed transports: {sorted(allowed_transports)}"
        )

    # 4. SSE Transport validation & SSRF protection
    if transport == "sse":
        url = connection_config.get("url")
        if not url or not isinstance(url, str):
            raise ServerConfigValidationError("SSE transport requires a valid 'url' string in connection_config.")

        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise ServerConfigValidationError(
                f"Invalid URL scheme '{parsed.scheme}'. Only http and https are allowed."
            )

        hostname = parsed.hostname
        if not hostname:
            raise ServerConfigValidationError("Invalid URL: missing hostname.")

        # Check cloud metadata endpoints
        if hostname.lower() in FORBIDDEN_HOSTS:
            raise ServerConfigValidationError(
                f"Access to metadata endpoint '{hostname}' is strictly forbidden."
            )

        # Check IP ranges
        try:
            ip = ipaddress.ip_address(hostname)
            if ip.is_link_local or str(ip) in FORBIDDEN_HOSTS:
                raise ServerConfigValidationError(
                    f"Access to metadata endpoint '{hostname}' is strictly forbidden."
                )
            if (ip.is_private or ip.is_loopback) and is_production() and not is_dev_mocks_enabled():
                raise ServerConfigValidationError(
                    f"Connecting to private or loopback IP '{hostname}' is not permitted in production."
                )
        except ValueError:
            # Hostname is a domain name
            if hostname.lower() in ("localhost", "127.0.0.1") and is_production() and not is_dev_mocks_enabled():
                raise ServerConfigValidationError("Connecting to localhost is not permitted in production.")

    # 5. Stdio Transport validation & Shell Injection protection
    elif transport == "stdio":
        command = connection_config.get("command", "python")
        if not command or not isinstance(command, str) or len(command.strip()) == 0:
            raise ServerConfigValidationError("stdio transport requires a valid non-empty 'command' string.")

        if SHELL_METACHARS_REGEX.search(command):
            raise ServerConfigValidationError(
                f"Command '{command}' contains forbidden shell metacharacters (; & | ` $ < >)."
            )

        args = connection_config.get("args", [])
        if not isinstance(args, list):
            raise ServerConfigValidationError("stdio transport 'args' must be a list of strings.")

        for arg in args:
            if not isinstance(arg, str):
                raise ServerConfigValidationError(f"Invalid stdio argument: {arg} must be string.")
            if SHELL_METACHARS_REGEX.search(arg):
                raise ServerConfigValidationError(
                    f"Argument '{arg}' contains forbidden shell metacharacters."
                )
