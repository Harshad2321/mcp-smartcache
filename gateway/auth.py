"""
Production Authentication & Authorization for MCP SmartCache Gateway.

Enforces:
1. Role-based API key access:
   - ADMIN: cache policy management, cache invalidation, metrics access, tool calls.
   - CLIENT: tool calls, catalog inspection, status.
2. Constant-time secret comparison to prevent timing side-channel attacks.
3. Cryptographic Tenant Binding:
   - Client API keys are strictly bound to their authorized tenant scope.
   - Callers cannot choose arbitrary tenant IDs using a shared key.
   - Any attempt to impersonate or access an unauthorized tenant is rejected with 403 Forbidden.
4. Production safety check:
   - Refusing to start in production without strong credentials.
   - Rejecting known development keys or keys starting with 'dev-' in production mode.
"""

from dataclasses import dataclass
from enum import Enum
import json
import logging
import os
import re
import secrets
from typing import Dict, FrozenSet, Optional, Tuple
from fastapi import Header, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = logging.getLogger(__name__)

# Pattern for safe tenant IDs: alphanumeric, underscores, hyphens, dots; 1-64 chars.
TENANT_ID_REGEX = re.compile(r"^[a-zA-Z0-9_\-\.]{1,64}$")

# Insecure development secrets that must NEVER be used in production mode
KNOWN_DEV_SECRETS = {
    "dev-admin-key",
    "dev-client-key",
    "dev-admin-secret-key-32chars-long",
    "dev-client-secret-key-32chars-long",
    "replace-with-a-secure-random-admin-key-min-16-chars",
    "replace-with-a-secure-random-client-key-min-16-chars",
}

security_bearer = HTTPBearer(auto_error=False)


class Role(str, Enum):
    ADMIN = "admin"
    CLIENT = "client"


@dataclass(frozen=True)
class AuthContext:
    role: Role
    key_id: str
    tenant_id: str
    authorized_tenants: FrozenSet[str]


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


def get_default_client_tenant() -> str:
    """Return default tenant ID for single-tenant client deployments (default: 'default')."""
    return os.environ.get("DEFAULT_CLIENT_TENANT", "default").strip()


def get_client_tenant_map() -> Dict[str, str]:
    """
    Parse CLIENT_TENANT_KEYS mapping individual client API keys to authorized tenant IDs.
    Supports JSON: {"<key>": "<tenant_id>"} or comma-separated pairs: key1:tenant1,key2:tenant2
    """
    raw = os.environ.get("CLIENT_TENANT_KEYS", "").strip()
    if not raw:
        return {}
    if raw.startswith("{"):
        try:
            return json.loads(raw)
        except Exception as err:
            logger.error("Failed to parse CLIENT_TENANT_KEYS JSON: %s", err)
            return {}
    mapping = {}
    for pair in raw.split(","):
        if ":" in pair:
            k, v = pair.split(":", 1)
            mapping[k.strip()] = v.strip()
    return mapping


def validate_production_security_config() -> None:
    """
    Called on gateway startup.
    In production mode, verify that secrets are configured and are not development placeholders.
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
        if admin_key in KNOWN_DEV_SECRETS or admin_key.startswith("dev-") or admin_key.startswith("test-"):
            raise RuntimeError(
                "CRITICAL SECURITY CONFIGURATION ERROR: Development API keys cannot be used in production mode."
            )

        client_key = get_client_api_key()
        tenant_map = get_client_tenant_map()
        if not client_key and not tenant_map:
            raise RuntimeError(
                "CRITICAL SECURITY CONFIGURATION ERROR: In production, either GATEWAY_CLIENT_API_KEY "
                "or CLIENT_TENANT_KEYS must be configured."
            )
        if client_key:
            if len(client_key) < 16:
                raise RuntimeError(
                    "CRITICAL SECURITY CONFIGURATION ERROR: In production, GATEWAY_CLIENT_API_KEY "
                    "must be configured with at least 16 characters."
                )
            if client_key in KNOWN_DEV_SECRETS or client_key.startswith("dev-") or client_key.startswith("test-"):
                raise RuntimeError(
                    "CRITICAL SECURITY CONFIGURATION ERROR: Development client keys cannot be used in production mode."
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


def authenticate_credentials(token: Optional[str]) -> Tuple[Role, str, FrozenSet[str]]:
    """
    Authenticate a raw token using constant-time comparison.
    Returns (Role, key_id, authorized_tenants).
    """
    if not is_auth_enabled():
        return Role.ADMIN, "auth_disabled", frozenset(["*"])

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication credentials were not provided.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 1. Check Master Admin Key
    admin_key = get_admin_api_key()
    if admin_key and secrets.compare_digest(token, admin_key):
        return Role.ADMIN, "admin", frozenset(["*"])

    # 2. Check Multi-Tenant Client Key Map
    tenant_map = get_client_tenant_map()
    for mapped_key, tenant_id in tenant_map.items():
        if secrets.compare_digest(token, mapped_key):
            return Role.CLIENT, f"client:{tenant_id}", frozenset([tenant_id])

    # 3. Check Default Client API Key
    client_key = get_client_api_key()
    if client_key and secrets.compare_digest(token, client_key):
        default_tenant = get_default_client_tenant()
        return Role.CLIENT, f"client:{default_tenant}", frozenset([default_tenant])

    # 4. Development Fallback (only allowed in dev/test environment)
    env = get_environment()
    if env in ("development", "test"):
        if not admin_key and token == "dev-admin-key":
            logger.warning("INSECURE: Authenticated with default development admin key.")
            return Role.ADMIN, "dev_admin", frozenset(["*"])
        if not client_key and token == "dev-client-key":
            logger.warning("INSECURE: Authenticated with default development client key.")
            return Role.CLIENT, "dev_client", frozenset(["default"])

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid authentication credentials.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def authenticate_token(token: Optional[str]) -> Role:
    """Backward-compatible helper returning Role only."""
    role, _, _ = authenticate_credentials(token)
    return role


async def get_current_auth(
    bearer: Optional[HTTPAuthorizationCredentials] = Security(security_bearer),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID"),
) -> AuthContext:
    """
    FastAPI dependency to authenticate the request, enforce tenant authorization,
    and resolve the active tenant scope.
    """
    raw_token = extract_raw_token(bearer, x_api_key)
    role, key_id, authorized_tenants = authenticate_credentials(raw_token)

    # Validate and resolve tenant authorization
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

        # Enforce that CLIENT role cannot access arbitrary tenant IDs
        if role != Role.ADMIN and clean_tenant not in authorized_tenants:
            logger.warning(
                "SECURITY ALERT: Key '%s' attempted unauthorized access to tenant '%s'",
                key_id,
                clean_tenant,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access to tenant '{clean_tenant}' is not authorized for this credential.",
            )
        resolved_tenant = clean_tenant
    else:
        # Default tenant resolution when header is omitted
        if role == Role.ADMIN:
            resolved_tenant = "default"
        else:
            resolved_tenant = next(iter(authorized_tenants))

    return AuthContext(
        role=role,
        key_id=key_id,
        tenant_id=resolved_tenant,
        authorized_tenants=authorized_tenants,
    )


async def require_client(
    auth: AuthContext = Security(get_current_auth),
) -> AuthContext:
    """Allows either CLIENT or ADMIN role with bound tenant authorization."""
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
