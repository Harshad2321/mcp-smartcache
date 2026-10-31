"""
Phase 5 & 7: Upstream Server Registration Validation and Security Boundaries.
Protects against SSRF, loopback access in production, cloud metadata IP access,
alternate IP encodings, DNS rebinding / resolution to private networks,
shell metacharacter injection in stdio transport, and redacts sensitive credentials.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import re
import socket
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Cloud metadata services and forbidden hostnames
FORBIDDEN_HOSTS = {
    "169.254.169.254",          # AWS / GCP / Azure metadata IP
    "metadata.google.internal",  # GCP metadata hostname
    "metadata.goog",            # GCP metadata short
    "100.100.100.200",          # Alibaba cloud metadata
    "168.63.129.16",            # Azure WireServer / IMDS host
    "192.0.0.192",              # Oracle cloud metadata
    "fd00:ec2::254",            # AWS IPv6 metadata
}

# Special forbidden subnets
CGNAT_NET = ipaddress.ip_network("100.64.0.0/10")

# Shell metacharacters forbidden in stdio command and arguments: ; & | ` $ < > ( ) \n \r " ' \
SHELL_METACHARS_REGEX = re.compile(r"[;&|`$<>()\n\r\"'\\]")

# Dangerous shell interpreters that must not be invoked as stdio command
FORBIDDEN_SHELL_COMMANDS = {
    "sh", "bash", "zsh", "csh", "tcsh", "dash", "ksh",
    "cmd", "cmd.exe", "powershell", "powershell.exe",
    "pwsh", "pwsh.exe", "wscript", "wscript.exe", "cscript", "cscript.exe",
}


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
        "password", "secret", "token", "api_key", "apikey", "key",
        "authorization", "auth", "credential", "private_key",
        "client_secret", "refresh_token", "id_token",
    }
    if isinstance(config, dict):
        clean = {}
        for k, v in config.items():
            if any(s in str(k).lower() for s in sensitive_keys):
                clean[k] = "[REDACTED]"
            else:
                clean[k] = redact_sensitive_config(v)
        return clean
    elif isinstance(config, list):
        return [redact_sensitive_config(item) for item in config]
    return config


def parse_flexible_ip(host: str) -> Optional[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """
    Parse an IP address supporting standard, integer, hex, octal, shortened, and IPv6 formats.
    Returns an IPv4Address or IPv6Address, or None if host is a domain name.
    """
    clean_host = host.strip("[]").strip()
    if not clean_host:
        return None

    # 1. Standard ipaddress parse (covers standard IPv4, IPv6, IPv4-mapped IPv6)
    try:
        ip = ipaddress.ip_address(clean_host)
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            return ip.ipv4_mapped
        return ip
    except ValueError:
        pass

    # 2. Check for integer (hex or decimal single integer representation)
    if re.match(r"^0x[0-9a-fA-F]+$", clean_host):
        try:
            val = int(clean_host, 16)
            if 0 <= val <= 0xFFFFFFFF:
                return ipaddress.IPv4Address(val)
        except ValueError:
            pass

    if re.match(r"^\d+$", clean_host):
        try:
            val = int(clean_host, 8 if (clean_host.startswith("0") and len(clean_host) > 1 and not re.search(r"[89]", clean_host)) else 10)
            if 0 <= val <= 0xFFFFFFFF:
                return ipaddress.IPv4Address(val)
        except ValueError:
            pass

    # 3. Check for multi-part dotted formats with hex/octal or shortened (e.g. 127.1, 0177.0.0.1)
    parts = clean_host.split(".")
    if 1 < len(parts) <= 4:
        parsed_parts = []
        is_ip_parts = True
        for p in parts:
            if not p:
                is_ip_parts = False
                break
            try:
                if p.startswith("0x") or p.startswith("0X"):
                    part_val = int(p, 16)
                elif p.startswith("0") and len(p) > 1 and not re.search(r"[89]", p):
                    part_val = int(p, 8)
                else:
                    part_val = int(p, 10)
                if part_val < 0:
                    is_ip_parts = False
                    break
                parsed_parts.append(part_val)
            except ValueError:
                is_ip_parts = False
                break

        if is_ip_parts:
            try:
                if len(parsed_parts) == 4:
                    if all(0 <= p <= 255 for p in parsed_parts):
                        return ipaddress.IPv4Address(
                            (parsed_parts[0] << 24) | (parsed_parts[1] << 16) | (parsed_parts[2] << 8) | parsed_parts[3]
                        )
                elif len(parsed_parts) == 2:  # e.g., 127.1 -> 127.0.0.1
                    if 0 <= parsed_parts[0] <= 255 and 0 <= parsed_parts[1] <= 0xFFFFFF:
                        return ipaddress.IPv4Address((parsed_parts[0] << 24) | parsed_parts[1])
                elif len(parsed_parts) == 3:  # e.g., 127.0.1 -> 127.0.0.1
                    if 0 <= parsed_parts[0] <= 255 and 0 <= parsed_parts[1] <= 255 and 0 <= parsed_parts[2] <= 0xFFFF:
                        return ipaddress.IPv4Address(
                            (parsed_parts[0] << 24) | (parsed_parts[1] << 16) | parsed_parts[2]
                        )
            except (ValueError, OverflowError):
                pass

    return None


def is_forbidden_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> Tuple[bool, str]:
    """
    Check if an IP address points to a forbidden location (metadata, loopback, private, etc.).
    Returns (is_forbidden, reason).
    """
    # 0. IPv4-mapped IPv6 address unwrapping (e.g. ::ffff:127.0.0.1 -> 127.0.0.1)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped

    ip_str = str(ip)

    # 1. Cloud metadata IP (strictly forbidden in all environments)
    if ip_str in FORBIDDEN_HOSTS or ip.is_link_local:
        return True, f"Access to metadata endpoint '{ip_str}' is strictly forbidden."

    # 2. Check CGNAT / Cloud metadata range
    if isinstance(ip, ipaddress.IPv4Address) and ip in CGNAT_NET:
        if ip_str == "100.100.100.200":
            return True, f"Access to metadata endpoint '{ip_str}' is strictly forbidden."
        if is_production() and not is_dev_mocks_enabled():
            return True, f"Connecting to carrier-grade NAT / private IP '{ip_str}' is not permitted in production."

    # 3. Multicast, reserved, unspecified
    if ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        return True, f"Connecting to reserved, multicast, or unspecified IP '{ip_str}' is not permitted."

    # 4. Loopback and private
    if (ip.is_loopback or ip.is_private) and is_production() and not is_dev_mocks_enabled():
        return True, f"Connecting to private or loopback IP '{ip_str}' is not permitted in production."

    return False, ""


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

        # Check cloud metadata endpoints by hostname
        if hostname.lower() in FORBIDDEN_HOSTS:
            raise ServerConfigValidationError(
                f"Access to metadata endpoint '{hostname}' is strictly forbidden."
            )

        # Check IP (handles decimal integers, hex, octal, shortened, standard IPv4/IPv6, IPv4-mapped)
        parsed_ip = parse_flexible_ip(hostname)
        if parsed_ip is not None:
            forbidden, reason = is_forbidden_ip(parsed_ip)
            if forbidden:
                raise ServerConfigValidationError(reason)
        else:
            # Hostname is a domain name
            if hostname.lower() in ("localhost", "127.0.0.1") and is_production() and not is_dev_mocks_enabled():
                raise ServerConfigValidationError("Connecting to localhost is not permitted in production.")

            # Resolve DNS in production or when dev mocks are not enabled
            # Also in dev, if resolves to cloud metadata, block it!
            try:
                addr_info = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
                for entry in addr_info:
                    resolved_ip_str = entry[4][0]
                    resolved_ip = parse_flexible_ip(resolved_ip_str)
                    if resolved_ip:
                        forbidden, reason = is_forbidden_ip(resolved_ip)
                        if forbidden:
                            raise ServerConfigValidationError(
                                f"Domain '{hostname}' resolves to forbidden address '{resolved_ip_str}': {reason}"
                            )
            except socket.gaierror as err:
                if is_production() and not is_dev_mocks_enabled():
                    raise ServerConfigValidationError(
                        f"Cannot resolve domain '{hostname}': {err}"
                    )
            except ServerConfigValidationError:
                raise
            except Exception as e:
                logger.warning("DNS resolution check failed for %s: %s", hostname, e)

    # 5. Stdio Transport validation & Shell Injection protection
    elif transport == "stdio":
        command = connection_config.get("command", "python")
        if not command or not isinstance(command, str) or len(command.strip()) == 0:
            raise ServerConfigValidationError("stdio transport requires a valid non-empty 'command' string.")

        # Disallow dangerous shell interpreters
        cmd_base = os.path.basename(command.strip()).lower()
        if cmd_base in FORBIDDEN_SHELL_COMMANDS:
            raise ServerConfigValidationError(
                f"Shell interpreter '{command}' is not permitted as a stdio command for security reasons."
            )

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
