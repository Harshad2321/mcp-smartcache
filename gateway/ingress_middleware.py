"""
Production Ingress & Trusted Proxy Middleware for MCP SmartCache.

Enforces:
1. HTTP Host header validation against ALLOWED_HOSTS to prevent DNS rebinding / host header poisoning.
2. Trusted proxy boundary enforcement:
   - Only trusts X-Forwarded-* headers when the immediate network peer is in TRUSTED_PROXIES.
   - Rejects or sanitizes spoofed forwarding headers from untrusted clients.
3. Client IP and protocol resolution stored in request.state for downstream auditing and quota tracking.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
from typing import List, Optional, Set
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

DEFAULT_ALLOWED_HOSTS = {
    "localhost",
    "127.0.0.1",
    "::1",
    "[::1]",
    "test",
    "testserver",
    "gateway",
    "control_api",
    "control-api",
    "runtime",
    "dashboard",
    "ingress",
    "mcp-smartcache.example.com",
    "api.example.com",
    "dashboard.example.com",
}

DEFAULT_TRUSTED_PROXIES = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
]


def parse_allowed_hosts() -> Set[str]:
    """Parse ALLOWED_HOSTS from environment or return hardened defaults."""
    raw = os.environ.get("ALLOWED_HOSTS", "").strip()
    hosts = set(DEFAULT_ALLOWED_HOSTS)
    if raw:
        for h in raw.split(","):
            cleaned = h.strip().lower()
            if cleaned:
                hosts.add(cleaned)
    return hosts


def parse_trusted_proxies() -> List[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    """Parse TRUSTED_PROXIES CIDR ranges."""
    raw = os.environ.get("TRUSTED_PROXIES", "").strip()
    if not raw:
        return list(DEFAULT_TRUSTED_PROXIES)
    networks = []
    for item in raw.split(","):
        cleaned = item.strip()
        if cleaned:
            try:
                networks.append(ipaddress.ip_network(cleaned, strict=False))
            except ValueError as err:
                logger.warning("Invalid CIDR in TRUSTED_PROXIES '%s': %s", cleaned, err)
    return networks or list(DEFAULT_TRUSTED_PROXIES)


def is_peer_trusted_proxy(peer_ip: str, trusted_networks: List[ipaddress.IPv4Network | ipaddress.IPv6Network]) -> bool:
    """Check if the immediate peer IP address belongs to any trusted proxy CIDR."""
    try:
        ip = ipaddress.ip_address(peer_ip)
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        return any(ip in net for net in trusted_networks)
    except ValueError:
        return False


class TrustedProxyHostMiddleware(BaseHTTPMiddleware):
    """
    Validates Host headers and ensures X-Forwarded-* headers are only trusted
    when received from verified reverse-proxy networks.
    """

    def __init__(self, app):
        super().__init__(app)
        self.allowed_hosts = parse_allowed_hosts()
        self.trusted_proxies = parse_trusted_proxies()

    async def dispatch(self, request: Request, call_next):
        # 1. Validate Host header
        raw_host = request.headers.get("host", "").strip()
        if not raw_host:
            # If no Host header is present, reject
            return Response(
                content=json.dumps({"detail": "Missing mandatory Host header."}),
                status_code=400,
                media_type="application/json",
            )

        # Strip port if present (e.g., "localhost:8000" -> "localhost")
        host_name = raw_host.split(":")[0].strip().lower()
        if "*" not in self.allowed_hosts and host_name not in self.allowed_hosts:
            logger.warning("Rejected request with untrusted Host header: %s", raw_host)
            return Response(
                content=json.dumps({"detail": f"Untrusted or invalid Host header: '{raw_host}'."}),
                status_code=400,
                media_type="application/json",
            )

        # 2. Inspect peer IP
        peer_ip = request.client.host if request.client else "127.0.0.1"
        is_trusted = is_peer_trusted_proxy(peer_ip, self.trusted_proxies)

        forwarded_for = request.headers.get("x-forwarded-for")
        forwarded_proto = request.headers.get("x-forwarded-proto")

        if is_trusted and forwarded_for:
            # Trust reverse proxy's X-Forwarded-For header; take the client IP (first or trusted hop)
            client_hops = [hop.strip() for hop in forwarded_for.split(",") if hop.strip()]
            verified_client_ip = client_hops[0] if client_hops else peer_ip
            verified_proto = forwarded_proto.strip() if forwarded_proto else request.url.scheme
            forged = False
        else:
            # Direct connection or untrusted peer: do NOT trust any X-Forwarded headers
            verified_client_ip = peer_ip
            verified_proto = request.url.scheme
            forged = bool(forwarded_for or forwarded_proto)
            if forged:
                logger.warning(
                    "Untrusted client %s attempted to send spoofed X-Forwarded-* headers (X-Forwarded-For: %s). Ignored.",
                    peer_ip,
                    forwarded_for,
                )

        request.state.client_ip = verified_client_ip
        request.state.client_proto = verified_proto
        request.state.peer_ip = peer_ip
        request.state.proxy_trusted = is_trusted
        request.state.forged_headers_detected = forged

        return await call_next(request)
