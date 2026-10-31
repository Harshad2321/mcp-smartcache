"""
Phase 8: Defensible Connection-Time Outbound Transport and SSRF Protection.
Guarantees:
1. Kernel-level socket peer verification (getpeername) to defeat DNS rebinding (TOCTOU).
2. Pre-connect DNS resolution validation for every resolved address.
3. Redirect interception and destination validation.
4. Safe client factory for MCP SSE and HTTP transports.
"""

from __future__ import annotations

import logging
import socket
from typing import Any, Dict, Optional
from urllib.parse import urljoin, urlparse

import anyio
import httpcore2
import httpcore2._backends.anyio as anyio_backend
import httpx2
from mcp.shared._httpx_utils import MCP_DEFAULT_TIMEOUT, MCP_DEFAULT_SSE_READ_TIMEOUT

from gateway.server_validator import (
    is_forbidden_ip,
    parse_flexible_ip,
)

logger = logging.getLogger(__name__)


class SSRFSecurityViolation(ConnectionError):
    """Raised when an outbound connection attempts to reach a forbidden network target."""
    pass


class SafeOutboundNetworkBackend(anyio_backend.AnyIOBackend):
    """
    Custom network backend for httpcore2 that enforces strict SSRF checks
    both before initiating connection and on the actual connected socket peer.
    """

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any | None = None,
    ) -> anyio_backend.AsyncNetworkStream:
        # 1. Pre-connection DNS check on all addresses
        parsed_ip = parse_flexible_ip(host)
        if parsed_ip is not None:
            forbidden, reason = is_forbidden_ip(parsed_ip)
            if forbidden:
                raise SSRFSecurityViolation(
                    f"Outbound connection to IP '{host}' blocked: {reason}"
                )
        else:
            try:
                addr_info = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
                for entry in addr_info:
                    resolved_ip_str = entry[4][0]
                    resolved_ip = parse_flexible_ip(resolved_ip_str)
                    if resolved_ip:
                        forbidden, reason = is_forbidden_ip(resolved_ip)
                        if forbidden:
                            raise SSRFSecurityViolation(
                                f"Outbound connection to host '{host}' blocked: "
                                f"resolves to forbidden address '{resolved_ip_str}': {reason}"
                            )
            except socket.gaierror as err:
                raise SSRFSecurityViolation(f"Failed to resolve host '{host}': {err}") from err

        # 2. Establish connection
        stream = await super().connect_tcp(
            host=host,
            port=port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

        # 3. Post-connection kernel socket peer verification (anti-DNS rebinding)
        try:
            # Query AsyncNetworkStream standard extra info or fallback to underlying socket
            server_addr = None
            if hasattr(stream, "get_extra_info"):
                server_addr = stream.get_extra_info("server_addr")
                raw_socket = stream.get_extra_info("socket")
            else:
                raw_socket = getattr(stream, "_raw_socket", None)

            peer_ip_str = None
            if server_addr and isinstance(server_addr, (tuple, list)) and len(server_addr) >= 1:
                peer_ip_str = str(server_addr[0])
            elif raw_socket and hasattr(raw_socket, "getpeername"):
                peer = raw_socket.getpeername()
                if isinstance(peer, (tuple, list)) and len(peer) >= 1:
                    peer_ip_str = str(peer[0])
                elif isinstance(peer, str):
                    peer_ip_str = peer

            if not peer_ip_str:
                # Security boundary: Fail closed when the connected peer address cannot be inspected
                await stream.aclose()
                raise SSRFSecurityViolation(
                    f"Outbound connection to '{host}:{port}' aborted: "
                    f"unable to verify peer address at connection boundary."
                )

            peer_ip = parse_flexible_ip(peer_ip_str)
            if not peer_ip:
                await stream.aclose()
                raise SSRFSecurityViolation(
                    f"Outbound connection to '{host}:{port}' aborted: "
                    f"connected peer '{peer_ip_str}' is an invalid or unparseable IP address."
                )

            forbidden, reason = is_forbidden_ip(peer_ip)
            if forbidden:
                await stream.aclose()
                raise SSRFSecurityViolation(
                    f"DNS rebinding / SSRF violation detected: "
                    f"actual connected peer '{peer_ip_str}' is forbidden: {reason}"
                )
        except SSRFSecurityViolation:
            raise
        except Exception as e:
            await stream.aclose()
            raise SSRFSecurityViolation(
                f"Peer verification failed for '{host}:{port}': {e}"
            ) from e

        return stream


class SafeAsyncHTTPTransport(httpx2.AsyncHTTPTransport):
    """
    Hardened HTTP transport enforcing connection-time SSRF validation
    and safe redirect target validation.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Wrap pool with SafeOutboundNetworkBackend
        self._pool = httpcore2.AsyncConnectionPool(
            ssl_context=getattr(self._pool, "_ssl_context", None),
            max_connections=getattr(self._pool, "_max_connections", 10),
            max_keepalive_connections=getattr(self._pool, "_max_keepalive_connections", None),
            keepalive_expiry=getattr(self._pool, "_keepalive_expiry", None),
            http1=getattr(self._pool, "_http1", True),
            http2=getattr(self._pool, "_http2", False),
            network_backend=SafeOutboundNetworkBackend(),
        )

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        # Pre-validate request target
        host = request.url.host
        parsed_ip = parse_flexible_ip(host)
        if parsed_ip is not None:
            forbidden, reason = is_forbidden_ip(parsed_ip)
            if forbidden:
                raise SSRFSecurityViolation(f"SSRF violation: destination '{host}' is forbidden: {reason}")
        else:
            try:
                addr_info = socket.getaddrinfo(host, request.url.port, proto=socket.IPPROTO_TCP)
                for entry in addr_info:
                    resolved_ip_str = entry[4][0]
                    resolved_ip = parse_flexible_ip(resolved_ip_str)
                    if resolved_ip:
                        forbidden, reason = is_forbidden_ip(resolved_ip)
                        if forbidden:
                            raise SSRFSecurityViolation(
                                f"SSRF violation: host '{host}' resolves to forbidden address '{resolved_ip_str}': {reason}"
                            )
            except socket.gaierror as err:
                raise SSRFSecurityViolation(f"Cannot resolve host '{host}': {err}") from err

        response = await super().handle_async_request(request)

        # Intercept and validate redirect locations (301, 302, 303, 307, 308)
        if response.status_code in (301, 302, 303, 307, 308) and "location" in response.headers:
            raw_loc = response.headers["location"]
            try:
                redirect_url = urljoin(str(request.url), raw_loc)
                parsed_redirect = urlparse(redirect_url)
            except Exception as err:
                await response.aclose()
                raise SSRFSecurityViolation(f"SSRF violation: malformed redirect Location '{raw_loc}': {err}") from err

            if parsed_redirect.scheme not in ("http", "https"):
                await response.aclose()
                raise SSRFSecurityViolation(
                    f"SSRF violation: redirect to unsupported scheme '{parsed_redirect.scheme}' in '{redirect_url}' is blocked."
                )

            redirect_host = parsed_redirect.hostname
            if not redirect_host:
                await response.aclose()
                raise SSRFSecurityViolation(f"SSRF violation: missing host in redirect destination '{redirect_url}'.")

            redirect_ip = parse_flexible_ip(redirect_host)
            if redirect_ip:
                forbidden, reason = is_forbidden_ip(redirect_ip)
                if forbidden:
                    await response.aclose()
                    raise SSRFSecurityViolation(
                        f"SSRF violation: redirect to forbidden destination '{redirect_url}' is blocked: {reason}"
                    )
            else:
                try:
                    port = parsed_redirect.port or (443 if parsed_redirect.scheme == "https" else 80)
                    addr_info = socket.getaddrinfo(redirect_host, port, proto=socket.IPPROTO_TCP)
                    for entry in addr_info:
                        resolved_ip_str = entry[4][0]
                        resolved_ip = parse_flexible_ip(resolved_ip_str)
                        if resolved_ip:
                            forbidden, reason = is_forbidden_ip(resolved_ip)
                            if forbidden:
                                await response.aclose()
                                raise SSRFSecurityViolation(
                                    f"SSRF violation: redirect destination '{redirect_host}' "
                                    f"resolves to forbidden address '{resolved_ip_str}': {reason}"
                                )
                except socket.gaierror as err:
                    await response.aclose()
                    raise SSRFSecurityViolation(
                        f"SSRF violation: cannot resolve redirect destination '{redirect_host}': {err}"
                    ) from err

        return response


def create_safe_mcp_http_client(
    headers: dict[str, str] | None = None,
    timeout: httpx2.Timeout | None = None,
    auth: httpx2.Auth | None = None,
) -> httpx2.AsyncClient:
    """
    Factory creating an httpx2.AsyncClient configured with SafeAsyncHTTPTransport.
    Used by MCP sse_client for zero-trust outbound network communication.
    """
    if timeout is None:
        timeout = httpx2.Timeout(MCP_DEFAULT_TIMEOUT, read=MCP_DEFAULT_SSE_READ_TIMEOUT)
    kwargs: dict[str, Any] = {
        "timeout": timeout,
        "transport": SafeAsyncHTTPTransport(),
        "follow_redirects": False,  # Redirects strictly controlled
    }
    if headers is not None:
        kwargs["headers"] = headers
    if auth is not None:
        kwargs["auth"] = auth
    return httpx2.AsyncClient(**kwargs)

