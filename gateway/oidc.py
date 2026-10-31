"""
Phase 7: Production OAuth2 / OpenID Connect (OIDC) Authentication Provider.
Provides cryptographic JWT validation for enterprise identity providers (Auth0, Okta,
Google Workspace, Azure Entra ID, Keycloak) without weakening tenant boundaries.

Guarantees:
1. Strict algorithm whitelist (RS256, ES256, HS256). Rejection of 'none' algorithm.
2. Cryptographic signature verification.
3. Standard claim validation: iss, aud, exp, nbf, iat.
4. Tenant scope extraction with zero-trust validation.
5. Role mapping: administrator vs tenant client.
6. Key rotation readiness via dynamic JWKS cache / signing keys.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from enum import Enum
import json
import logging
import os
import re
import time
from typing import Any, Dict, FrozenSet, List, Optional, Set, Tuple
import jwt
from jwt.exceptions import (
    ExpiredSignatureError,
    ImmatureSignatureError,
    InvalidAudienceError,
    InvalidIssuerError,
    InvalidSignatureError,
    InvalidTokenError,
)

logger = logging.getLogger(__name__)

# Supported cryptographic signature algorithms
ALLOWED_ALGORITHMS = ["RS256", "ES256", "HS256"]


class OIDCValidationError(Exception):
    """Raised when an OIDC / OAuth2 token fails cryptographic or claim validation."""
    pass


@dataclass(frozen=True)
class OIDCIdentity:
    """Validated caller identity extracted from verified OIDC JWT."""
    subject: str
    tenant_id: str
    roles: FrozenSet[str]
    is_admin: bool
    issuer: str
    audience: str
    raw_claims: Dict[str, Any] = field(default_factory=dict, hash=False, repr=False)


class JWKSCache:
    """In-memory cache for Identity Provider JSON Web Key Sets (JWKS)."""

    def __init__(self, ttl_seconds: int = 3600):
        self._keys: Dict[str, Any] = {}
        self._last_fetched: float = 0.0
        self.ttl = ttl_seconds

    def get_key(self, kid: str) -> Optional[Any]:
        return self._keys.get(kid)

    def set_keys(self, jwks: Dict[str, Any]) -> None:
        self._keys.clear()
        keys_list = jwks.get("keys", [])
        for k in keys_list:
            kid = k.get("kid")
            if kid:
                try:
                    self._keys[kid] = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(k))
                except Exception as err:
                    logger.warning("Failed to parse JWK key kid=%s: %s", kid, err)
        self._last_fetched = time.time()

    def add_key(self, kid: str, public_key_or_secret: Any) -> None:
        self._keys[kid] = public_key_or_secret


class OIDCAuthValidator:
    """
    Validates OpenID Connect / OAuth2 Bearer JWTs against configured identity provider parameters.
    """

    def __init__(
        self,
        issuer: Optional[str] = None,
        audience: Optional[str] = None,
        allowed_algorithms: Optional[List[str]] = None,
        tenant_claim: str = "tenant_id",
        role_claim: str = "roles",
        admin_roles: Optional[Set[str]] = None,
        jwks_cache: Optional[JWKSCache] = None,
        static_secret_or_key: Optional[str] = None,
    ):
        self.issuer = issuer or os.environ.get("OIDC_ISSUER")
        self.audience = audience or os.environ.get("OIDC_AUDIENCE")
        self.allowed_algorithms = allowed_algorithms or ALLOWED_ALGORITHMS
        self.tenant_claim = tenant_claim or os.environ.get("OIDC_TENANT_CLAIM", "tenant_id")
        self.role_claim = role_claim or os.environ.get("OIDC_ROLE_CLAIM", "roles")
        self.admin_roles = admin_roles or {"admin", "administrator", "mcp_admin"}
        self.jwks_cache = jwks_cache or JWKSCache()
        self.static_secret_or_key = static_secret_or_key or os.environ.get("OIDC_SIGNING_KEY")

    def is_configured(self) -> bool:
        """Return True if OIDC provider parameters are configured."""
        return bool(self.issuer and self.audience)

    def validate_token(self, token: str) -> OIDCIdentity:
        """
        Cryptographically validate JWT token.
        Raises OIDCValidationError on any signature, expiration, claim, or algorithm failure.
        """
        if not token or not isinstance(token, str):
            raise OIDCValidationError("Invalid token: token must be a non-empty string.")

        if not self.issuer:
            raise OIDCValidationError("OIDC configuration is incomplete: missing required issuer.")
        if not self.audience:
            raise OIDCValidationError("OIDC configuration is incomplete: missing required audience.")

        # 1. Inspect unverified header for algorithm restrictions and key ID
        try:
            unverified_headers = jwt.get_unverified_header(token)
        except Exception as e:
            raise OIDCValidationError(f"Malformed JWT header: {e}")

        alg = unverified_headers.get("alg")
        if not alg:
            raise OIDCValidationError("Missing 'alg' header in token.")

        if alg.lower() == "none" or alg not in self.allowed_algorithms:
            raise OIDCValidationError(f"Algorithm '{alg}' is not permitted. Allowed: {self.allowed_algorithms}")

        kid = unverified_headers.get("kid")

        # 2. Resolve verification key
        key: Any = None
        if kid and self.jwks_cache:
            key = self.jwks_cache.get_key(kid)

        if not key:
            key = self.static_secret_or_key

        if not key:
            raise OIDCValidationError("Unable to resolve signing key for token verification.")

        # Key-type restriction & algorithm confusion defense (CVE-2015-9235 / CVE-2016-5431)
        if alg == "HS256":
            if isinstance(key, str) and any(h in key for h in ("BEGIN PUBLIC KEY", "BEGIN CERTIFICATE", "BEGIN RSA PUBLIC KEY")):
                raise OIDCValidationError(
                    "Algorithm confusion attack detected: cannot use asymmetric public key for symmetric HS256 validation."
                )
            if hasattr(key, "public_bytes") or "PublicKey" in type(key).__name__:
                raise OIDCValidationError(
                    "Algorithm confusion attack detected: cannot use asymmetric public key for symmetric HS256 validation."
                )
        elif alg in ("RS256", "ES256"):
            if isinstance(key, str) and not any(h in key for h in ("BEGIN PUBLIC KEY", "BEGIN CERTIFICATE", "BEGIN RSA PUBLIC KEY")):
                raise OIDCValidationError(
                    f"Key type mismatch: asymmetric algorithm '{alg}' requires an RSA or EC public key PEM."
                )

        # 3. Decode and cryptographically verify claims
        options = {
            "verify_signature": True,
            "require": ["exp", "iss", "aud"],
            "verify_exp": True,
            "verify_nbf": True,
            "verify_iat": True,
            "verify_iss": bool(self.issuer),
            "verify_aud": bool(self.audience),
        }

        try:
            claims = jwt.decode(
                token,
                key=key,
                algorithms=[alg],
                issuer=self.issuer if self.issuer else None,
                audience=self.audience if self.audience else None,
                options=options,
            )
        except ExpiredSignatureError:
            raise OIDCValidationError("Token has expired.")
        except ImmatureSignatureError:
            raise OIDCValidationError("Token is not valid yet (nbf).")
        except InvalidIssuerError:
            raise OIDCValidationError(f"Token issuer mismatch. Expected: {self.issuer}")
        except InvalidAudienceError:
            raise OIDCValidationError(f"Token audience mismatch. Expected: {self.audience}")
        except InvalidSignatureError:
            raise OIDCValidationError("Invalid cryptographic signature.")
        except InvalidTokenError as err:
            raise OIDCValidationError(f"JWT validation failed: {err}")
        except Exception as err:
            raise OIDCValidationError(f"Unexpected token error: {err}")

        # 4. Extract subject
        subject = claims.get("sub", "")
        if not subject:
            raise OIDCValidationError("Token missing 'sub' claim.")

        # 5. Extract role claim and determine if caller has administrative privileges
        roles_val = claims.get(self.role_claim, [])
        if isinstance(roles_val, str):
            roles = frozenset([roles_val.strip()])
        elif isinstance(roles_val, list):
            roles = frozenset(str(r).strip() for r in roles_val)
        else:
            roles = frozenset()

        is_admin = bool(roles.intersection(self.admin_roles)) or bool(claims.get("is_admin", False))

        # 6. Extract and validate tenant claim
        tenant_id = claims.get(self.tenant_claim)
        if not tenant_id:
            # Fallback checks for common tenant claims
            tenant_id = claims.get("tid") or claims.get("org_id") or claims.get("organization_id")

        if not tenant_id:
            if is_admin:
                tenant_id = "default"
            else:
                raise OIDCValidationError(f"Token missing required tenant claim '{self.tenant_claim}'.")

        tenant_str = str(tenant_id).strip()
        if not re.match(r"^[a-zA-Z0-9_\-\.]{1,64}$", tenant_str):
            raise OIDCValidationError(f"Invalid characters or length in tenant identifier '{tenant_str}'.")

        return OIDCIdentity(
            subject=subject,
            tenant_id=tenant_str,
            roles=roles,
            is_admin=is_admin,
            issuer=claims.get("iss", ""),
            audience=claims.get("aud", ""),
            raw_claims=claims,
        )


def is_jwt_token(token: str) -> bool:
    """Return True if string conforms to three-part base64url encoded JWT structure."""
    if not token or not isinstance(token, str):
        return False
    parts = token.split(".")
    if len(parts) != 3:
        return False
    try:
        # Check if first part looks like valid base64url JSON
        header_raw = parts[0]
        # Add padding if needed
        padding = "=" * ((4 - len(header_raw) % 4) % 4)
        decoded = base64.urlsafe_b64decode(header_raw + padding)
        data = json.loads(decoded)
        return isinstance(data, dict) and "alg" in data
    except Exception:
        return False

