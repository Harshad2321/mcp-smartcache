"""
Production Authentication & Authorization for MCP SmartCache Gateway.

Enforces:
1. Role-based API key access:
   - ADMIN: cache policy management, cache invalidation, metrics access, tool calls.
   - CLIENT: tool calls, catalog inspection, status.
2. Constant-time secret comparison to prevent timing side-channel attacks.
3. Tenant ID header validation for multi-tenant cache isolation.
4. Production safety check: in production, refusing to start without configured admin keys.
"""

from dataclasses import dataclass
from enum import Enum
import logging
import os
import re
import secrets
from typing import Optional
from fastapi import Header, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = logging.getLogger(__name__)

# Pattern for safe tenant IDs: alphanumeric, underscores, hyphens, dots; 1-64 chars.
TENANT_ID_REGEX = re.compile(r"^[a-zA-Z0-9_\-\.]{1,64}$")

security_bearer = HTTPBearer(auto_error=False)


class Role(str, Enum):
    ADMIN = "admin"
    CLIENT = "client"


@dataclass(frozen=True)
class AuthContext:
    role: Role
    key_id: str
    tenant_id: str


def is_auth_enabled() -> bool:
    """Return True if API authentication is enabled (default: True)."""
    return os.environ.get("AUTH_ENABLED", "true").lower() in ("true", "1", "yes")


def get_environment() -> str:
    """Return deployment environment name (default: production)."""
    return os.environ.get("ENVIRONMENT", "production").lower()


def get_admin_api_key() -> Optional[str]:
    return os.environ.get("GATEWAY_ADMIN_API_KEY")


def get_client_api_key() -> Optional[str]:
    return os.environ.get("GATEWAY_CLIENT_API_KEY")


def validate_production_security_config() -> None:
    """
    Called on startup.
    In production mode, verify that secrets are actually configured.
    Prevent starting an insecure public gateway.
    """
    env = get_environment()
    auth_enabled = is_auth_enabled()

    if env == "production":
        if not auth_enabled:
            raise RuntimeError(
                "CRITICAL SECURITY CONFIGURATION ERROR: AUTH_ENABLED cannot be false "
                "when ENVIRONMENT=production."
            )
        admin_key = get_admin_api_key()
        if not admin_key or len(admin_key) < 16:
            raise RuntimeError(
                "CRITICAL SECURITY CONFIGURATION ERROR: In production, GATEWAY_ADMIN_API_KEY "
                "must be configured with at least 16 characters."
            )
        client_key = get_client_api_key()
        if not client_key or len(client_key) < 16:
            raise RuntimeError(
                "CRITICAL SECURITY CONFIGURATION ERROR: In production, GATEWAY_CLIENT_API_KEY "
                "must be configured with at least 16 characters."
            )


def extract_raw_token(
    bearer: Optional[HTTPAuthorizationCredentials] = None,
    x_api_key: Optional[str] = None,
) -> Optional[str]:
    """Extract raw credential token from Bearer auth or X-API-Key header."""
    if bearer and bearer.credentials:
        return bearer.credentials.strip()
    if x_api_key:
        return x_api_key.strip()
    return None


def authenticate_token(token: Optional[str]) -> Role:
    """
    Authenticate a raw token using constant-time comparison.
    Returns the associated Role or raises HTTPException.
    """
    if not is_auth_enabled():
        return Role.ADMIN

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication credentials were not provided.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    admin_key = get_admin_api_key()
    if admin_key and secrets.compare_digest(token, admin_key):
        return Role.ADMIN

    client_key = get_client_api_key()
    if client_key and secrets.compare_digest(token, client_key):
        return Role.CLIENT

    # Development fallback: if in dev/test environment and no keys set, provide dev-keys
    env = get_environment()
    if env in ("development", "test"):
        if not admin_key and token == "dev-admin-key":
            logger.warning("INSECURE: Authenticated with default development admin key.")
            return Role.ADMIN
        if not client_key and token == "dev-client-key":
            logger.warning("INSECURE: Authenticated with default development client key.")
            return Role.CLIENT

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid authentication credentials.",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_auth(
    bearer: Optional[HTTPAuthorizationCredentials] = Security(security_bearer),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID"),
) -> AuthContext:
    """FastAPI dependency to authenticate the request and resolve role & tenant."""
    raw_token = extract_raw_token(bearer, x_api_key)
    role = authenticate_token(raw_token)

    # Validate and resolve tenant identifier
    resolved_tenant = "default"
    if x_tenant_id:
        clean_tenant = x_tenant_id.strip()
        if not TENANT_ID_REGEX.match(clean_tenant):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Invalid X-Tenant-ID '{clean_tenant}'. Must be 1-64 characters matching "
                    "alphanumeric, hyphens, underscores, or dots."
                ),
            )
        resolved_tenant = clean_tenant

    return AuthContext(
        role=role,
        key_id="admin" if role == Role.ADMIN else "client",
        tenant_id=resolved_tenant,
    )


async def require_client(
    auth: AuthContext = Security(get_current_auth),
) -> AuthContext:
    """Allows either CLIENT or ADMIN role."""
    return auth


async def require_admin(
    auth: AuthContext = Security(get_current_auth),
) -> AuthContext:
    """Requires ADMIN role. Rejects CLIENT role with 403 Forbidden."""
    if auth.role != Role.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrative privileges are required for this action.",
        )
    return auth
