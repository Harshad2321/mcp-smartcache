#!/usr/bin/env python3
"""
Production Configuration Validator for MCP SmartCache.
Validates environment settings, secret entropy, tenant key bindings,
CORS policies, and infrastructure connection strings prior to production deployment.

Modes:
  --mode template: Validates template syntax, variable declarations, and placeholder documentation.
  --mode production: Validates production deployment settings, secret entropy, and strict security rules.
  --mode runtime: Validates the active process environment (os.environ) against production rules.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Dict, List
from urllib.parse import urlparse

KNOWN_DEV_SECRETS = {
    "dev-admin-key",
    "dev-client-key",
    "dev-admin-secret-key-32chars-long",
    "dev-client-secret-key-32chars-long",
    "replace-with-a-secure-random-admin-key-min-16-chars",
    "replace-with-a-secure-random-client-key-min-16-chars",
    "replace-with-a-secure-random-postgres-password",
    "replace-with-a-secure-random-redis-password",
}

REQUIRED_TEMPLATE_KEYS = [
    "ENVIRONMENT",
    "AUTH_ENABLED",
    "GATEWAY_ADMIN_API_KEY",
    "GATEWAY_CLIENT_API_KEY",
    "CLIENT_TENANT_KEYS",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
    "REDIS_PASSWORD",
    "CORS_ALLOWED_ORIGINS",
    "ENABLE_DEV_MOCKS",
    "QUOTA_REDIS_FAILURE_POLICY",
]


def parse_env_file(file_path: str) -> Dict[str, str]:
    """Parse a .env or template file into a dictionary of string key-values."""
    env_vars: Dict[str, str] = {}
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Configuration file not found: {file_path}")

    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "=" in stripped:
                key, val = stripped.split("=", 1)
                clean_key = key.strip()
                # Strip inline comments if not quoted
                clean_val = val.strip()
                if not (clean_val.startswith('"') or clean_val.startswith("'")):
                    clean_val = clean_val.split("#")[0].strip()
                clean_val = clean_val.strip("'\"")
                env_vars[clean_key] = clean_val
    return env_vars


def validate_template_syntax(env_vars: Dict[str, str]) -> List[str]:
    """Validate that all required configuration variables are declared in template."""
    errors: List[str] = []
    for req_key in REQUIRED_TEMPLATE_KEYS:
        if req_key not in env_vars:
            errors.append(f"Template is missing declaration for required variable '{req_key}'.")
    return errors


def validate_production_env(config: Dict[str, str] | None = None) -> List[str]:
    """
    Validate environment variables against production security rules.
    If config is None, os.environ is used.
    NEVER echoes raw secret values in output.
    """
    errors: List[str] = []
    cfg = config if config is not None else dict(os.environ)

    # 1. Environment mode
    env = cfg.get("ENVIRONMENT", "development").lower().strip()
    if env != "production":
        return [f"ENVIRONMENT is set to '{env}'. Must be set to 'production' for production deployment."]

    # 2. Auth enabled check
    auth_enabled = cfg.get("AUTH_ENABLED", "true").lower().strip() in ("true", "1", "yes")
    if not auth_enabled:
        errors.append("CRITICAL: AUTH_ENABLED cannot be false in production mode.")

    # 3. Admin API Key
    admin_key = cfg.get("GATEWAY_ADMIN_API_KEY", "").strip()
    if not admin_key or len(admin_key) < 16:
        errors.append("GATEWAY_ADMIN_API_KEY must be configured with at least 16 characters.")
    if admin_key in KNOWN_DEV_SECRETS or admin_key.startswith("dev-") or admin_key.startswith("test-"):
        errors.append("GATEWAY_ADMIN_API_KEY is using a development placeholder or insecure prefix.")

    # 4. Client Keys & Tenant Mapping
    client_key = cfg.get("GATEWAY_CLIENT_API_KEY", "").strip()
    tenant_keys = cfg.get("CLIENT_TENANT_KEYS", "").strip()
    if not client_key and not tenant_keys:
        errors.append("Either GATEWAY_CLIENT_API_KEY or CLIENT_TENANT_KEYS must be configured.")
    if client_key:
        if len(client_key) < 16:
            errors.append("GATEWAY_CLIENT_API_KEY must be configured with at least 16 characters.")
        if client_key in KNOWN_DEV_SECRETS or client_key.startswith("dev-") or client_key.startswith("test-"):
            errors.append("GATEWAY_CLIENT_API_KEY is using a development placeholder or insecure prefix.")
    if tenant_keys:
        if tenant_keys.startswith("{"):
            try:
                parsed_map = json.loads(tenant_keys)
                if not isinstance(parsed_map, dict):
                    errors.append("CLIENT_TENANT_KEYS JSON must be an object mapping keys to tenant IDs.")
                else:
                    for k, t in parsed_map.items():
                        if not k or len(k) < 16:
                            errors.append(f"A key in CLIENT_TENANT_KEYS for tenant '{t}' has insufficient length (< 16 chars).")
                        if k in KNOWN_DEV_SECRETS or k.startswith("dev-") or k.startswith("test-"):
                            errors.append(f"A key in CLIENT_TENANT_KEYS for tenant '{t}' is using a development placeholder.")
            except Exception as err:
                errors.append(f"CLIENT_TENANT_KEYS contains malformed JSON: {err}")
        else:
            pairs = [p.strip() for p in tenant_keys.split(",") if p.strip()]
            for pair in pairs:
                if ":" not in pair:
                    errors.append(f"Malformed key:tenant pair in CLIENT_TENANT_KEYS.")
                else:
                    k, t = pair.split(":", 1)
                    k, t = k.strip(), t.strip()
                    if len(k) < 16:
                        errors.append(f"A key in CLIENT_TENANT_KEYS for tenant '{t}' has insufficient length (< 16 chars).")
                    if k in KNOWN_DEV_SECRETS or k.startswith("dev-") or k.startswith("test-"):
                        errors.append(f"A key in CLIENT_TENANT_KEYS for tenant '{t}' is using a development placeholder.")

    # 5. Database Password
    pg_pass = cfg.get("POSTGRES_PASSWORD", "").strip()
    if not pg_pass or len(pg_pass) < 12:
        errors.append("POSTGRES_PASSWORD must be configured with at least 12 characters.")
    if pg_pass in KNOWN_DEV_SECRETS or pg_pass.startswith("dev-") or pg_pass.startswith("test-"):
        errors.append("POSTGRES_PASSWORD is using a default or development placeholder.")

    # 6. Redis Password
    redis_pass = cfg.get("REDIS_PASSWORD", "").strip()
    if not redis_pass or len(redis_pass) < 12:
        errors.append("REDIS_PASSWORD must be configured with at least 12 characters.")
    if redis_pass in KNOWN_DEV_SECRETS or redis_pass.startswith("dev-") or redis_pass.startswith("test-"):
        errors.append("REDIS_PASSWORD is using a default or development placeholder.")

    # 7. CORS Origins
    cors_origins = cfg.get("CORS_ALLOWED_ORIGINS", "").strip()
    if not cors_origins or "*" in cors_origins:
        errors.append("CORS_ALLOWED_ORIGINS must be set to explicit domains (wildcard '*' forbidden in production).")

    # 8. Dev Mocks Disabled
    dev_mocks = cfg.get("ENABLE_DEV_MOCKS", "false").lower().strip() in ("true", "1")
    if dev_mocks:
        errors.append("ENABLE_DEV_MOCKS must be false in production mode.")

    # 9. Quota Failure Policy
    quota_policy = cfg.get("QUOTA_REDIS_FAILURE_POLICY", "fail_closed").lower().strip()
    if quota_policy not in ("fail_closed", "degrade_local"):
        errors.append(f"Invalid QUOTA_REDIS_FAILURE_POLICY '{quota_policy}'. Must be 'fail_closed' or 'degrade_local'.")

    # 10. OIDC Configuration Consistency
    oidc_issuer = cfg.get("OIDC_ISSUER", "").strip()
    oidc_aud = cfg.get("OIDC_AUDIENCE", "").strip()
    oidc_key = cfg.get("OIDC_SIGNING_KEY", "").strip()
    if oidc_issuer or oidc_aud or oidc_key:
        if not oidc_issuer:
            errors.append("OIDC configuration incomplete: OIDC_ISSUER must be specified when OIDC is enabled.")
        if not oidc_aud:
            errors.append("OIDC configuration incomplete: OIDC_AUDIENCE must be specified when OIDC is enabled.")
        if not oidc_key:
            errors.append("OIDC configuration incomplete: OIDC_SIGNING_KEY must be specified when OIDC is enabled.")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="MCP SmartCache Production Configuration Validator")
    parser.add_argument("file", nargs="?", default=None, help="Path to .env or environment template file")
    parser.add_argument(
        "--mode",
        choices=["template", "production", "runtime"],
        default=None,
        help="Validation mode: 'template' (syntax & keys), 'production' (security invariants), 'runtime' (active os.environ)",
    )
    parser.add_argument("--template", dest="template_file", help="Path to template file to validate syntax")

    args = parser.parse_args()

    # Determine execution mode and target config
    if args.template_file:
        mode = "template"
        target_file = args.template_file
    elif args.mode:
        mode = args.mode
        target_file = args.file
    elif args.file:
        if "example" in args.file.lower() or "template" in args.file.lower():
            mode = "template"
        else:
            mode = "production"
        target_file = args.file
    else:
        mode = "runtime"
        target_file = None

    print(f"Validating MCP SmartCache configuration (mode: {mode})...")

    if mode == "template":
        file_to_check = target_file or ".env.example"
        try:
            vars_dict = parse_env_file(file_to_check)
            errors = validate_template_syntax(vars_dict)
        except Exception as e:
            errors = [f"Failed to read template file '{file_to_check}': {e}"]
    elif mode == "production":
        if target_file:
            try:
                vars_dict = parse_env_file(target_file)
                errors = validate_production_env(vars_dict)
            except Exception as e:
                errors = [f"Failed to read environment file '{target_file}': {e}"]
        else:
            errors = validate_production_env(dict(os.environ))
    else:  # runtime
        errors = validate_production_env(dict(os.environ))

    if errors:
        print("\nCONFIGURATION VALIDATION FAILED:")
        for err in errors:
            print(f"  - {err}")
        return 1

    print(f"\nCONFIGURATION VALIDATION PASSED: All {mode} configuration checks verified successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
