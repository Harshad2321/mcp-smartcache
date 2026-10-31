"""
Phase 7 Automated Security Regression Test:
OAuth2 / OIDC Cryptographic JWT verification, algorithm restrictions,
tenant claim isolation, role mapping, and key rotation.
"""

import time
import pytest
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization

from gateway.auth import authenticate_credentials, Role, set_oidc_validator
from gateway.oidc import (
    JWKSCache,
    OIDCAuthValidator,
    OIDCValidationError,
    is_jwt_token,
)


@pytest.fixture(scope="module")
def rsa_key_pair():
    """Generate in-memory RSA 2048 keypair for cryptographic testing."""
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")

    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")

    return private_pem, public_pem


def test_is_jwt_token_detection():
    """Verify robust recognition of JWT formatted tokens vs standard API keys."""
    assert not is_jwt_token("dev-admin-key")
    assert not is_jwt_token("sec_apikey_1234567890abcdef")
    assert not is_jwt_token("")

    # A valid 3-part base64url string with JSON header
    valid_jwt = jwt.encode({"sub": "user1"}, "secret", algorithm="HS256")
    assert is_jwt_token(valid_jwt)


def test_oidc_rs256_valid_token_extraction(rsa_key_pair):
    """Cryptographically valid RS256 token extracts tenant scope and roles."""
    private_pem, public_pem = rsa_key_pair
    now = int(time.time())

    validator = OIDCAuthValidator(
        issuer="https://auth.company.com/",
        audience="mcp-smartcache",
        static_secret_or_key=public_pem,
    )

    token = jwt.encode(
        {
            "iss": "https://auth.company.com/",
            "aud": "mcp-smartcache",
            "sub": "user_finance_analyst",
            "tenant_id": "tenant_accounting",
            "roles": ["client"],
            "iat": now - 10,
            "exp": now + 3600,
            "nbf": now - 10,
        },
        private_pem,
        algorithm="RS256",
        headers={"kid": "key-2026-01"},
    )

    identity = validator.validate_token(token)
    assert identity.subject == "user_finance_analyst"
    assert identity.tenant_id == "tenant_accounting"
    assert identity.is_admin is False
    assert "client" in identity.roles


def test_oidc_admin_role_mapping(rsa_key_pair):
    """Admin role mapped from OIDC claims grants administrative context."""
    private_pem, public_pem = rsa_key_pair
    now = int(time.time())

    validator = OIDCAuthValidator(
        issuer="https://auth.company.com/",
        audience="mcp-smartcache",
        static_secret_or_key=public_pem,
    )

    token = jwt.encode(
        {
            "iss": "https://auth.company.com/",
            "aud": "mcp-smartcache",
            "sub": "ops_lead",
            "tenant_id": "tenant_ops",
            "roles": ["administrator"],
            "iat": now,
            "exp": now + 3600,
        },
        private_pem,
        algorithm="RS256",
    )

    identity = validator.validate_token(token)
    assert identity.is_admin is True


def test_oidc_strictly_rejects_none_algorithm():
    """Unsigned JWT tokens with alg=none are strictly rejected."""
    validator = OIDCAuthValidator(
        issuer="https://auth.company.com/",
        audience="mcp-smartcache",
        static_secret_or_key="some_key",
    )

    # Manually craft an unverified 'none' algorithm token
    none_token = "eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0.eyJzdWIiOiJhZG1pbiIsInRlbmFudF9pZCI6InRlbmFudF9hIn0."
    with pytest.raises(OIDCValidationError, match="Algorithm 'none' is not permitted"):
        validator.validate_token(none_token)


def test_oidc_expired_token_rejected(rsa_key_pair):
    """Expired JWT tokens are rejected."""
    private_pem, public_pem = rsa_key_pair
    past = int(time.time()) - 3600

    validator = OIDCAuthValidator(
        issuer="https://auth.company.com/",
        audience="mcp-smartcache",
        static_secret_or_key=public_pem,
    )

    expired_token = jwt.encode(
        {
            "iss": "https://auth.company.com/",
            "aud": "mcp-smartcache",
            "sub": "user_expired",
            "tenant_id": "tenant_a",
            "exp": past,
            "iat": past - 100,
        },
        private_pem,
        algorithm="RS256",
    )

    with pytest.raises(OIDCValidationError, match="Token has expired"):
        validator.validate_token(expired_token)


def test_oidc_tampered_signature_rejected(rsa_key_pair):
    """Tokens with invalid or tampered signatures are rejected."""
    private_pem, public_pem = rsa_key_pair
    # Generate a second keypair to simulate attacker signing with wrong key
    attacker_key = rsa.generate_private_key(65537, 2048)
    attacker_pem = attacker_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")

    validator = OIDCAuthValidator(
        issuer="https://auth.company.com/",
        audience="mcp-smartcache",
        static_secret_or_key=public_pem,
    )

    attacker_token = jwt.encode(
        {
            "iss": "https://auth.company.com/",
            "aud": "mcp-smartcache",
            "sub": "attacker",
            "tenant_id": "tenant_a",
            "exp": int(time.time()) + 3600,
        },
        attacker_pem,
        algorithm="RS256",
    )

    with pytest.raises(OIDCValidationError, match="Invalid cryptographic signature"):
        validator.validate_token(attacker_token)


def test_oidc_missing_tenant_claim_rejected(rsa_key_pair):
    """Non-admin tokens lacking tenant claim fail validation to preserve boundaries."""
    private_pem, public_pem = rsa_key_pair
    validator = OIDCAuthValidator(
        issuer="https://auth.company.com/",
        audience="mcp-smartcache",
        static_secret_or_key=public_pem,
    )

    token = jwt.encode(
        {
            "iss": "https://auth.company.com/",
            "aud": "mcp-smartcache",
            "sub": "user_no_tenant",
            "roles": ["client"],
            "exp": int(time.time()) + 3600,
        },
        private_pem,
        algorithm="RS256",
    )

    with pytest.raises(OIDCValidationError, match="missing required tenant claim"):
        validator.validate_token(token)


def test_oidc_gateway_auth_integration(rsa_key_pair):
    """Verify authenticate_credentials integrates OIDC and binds tenant scope."""
    private_pem, public_pem = rsa_key_pair
    validator = OIDCAuthValidator(
        issuer="https://auth.company.com/",
        audience="mcp-smartcache",
        static_secret_or_key=public_pem,
    )
    set_oidc_validator(validator)

    token = jwt.encode(
        {
            "iss": "https://auth.company.com/",
            "aud": "mcp-smartcache",
            "sub": "client_svc_user",
            "tenant_id": "finance_dept",
            "roles": ["client"],
            "exp": int(time.time()) + 3600,
        },
        private_pem,
        algorithm="RS256",
    )

    try:
        role, key_id, authorized_tenants = authenticate_credentials(token)
        assert role == Role.CLIENT
        assert key_id == "oidc:client_svc_user"
        assert "finance_dept" in authorized_tenants
        assert "*" not in authorized_tenants
    finally:
        set_oidc_validator(None)

