"""
Payload sanitization and size bounding for audit, trace, and workflow persistence.
Redacts passwords, secrets, API keys, OIDC tokens, cookies, authorization headers,
database connection strings, and PEM private keys.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict

SENSITIVE_KEYS = {
    "password",
    "token",
    "secret",
    "api_key",
    "apikey",
    "authorization",
    "auth_token",
    "access_token",
    "private_key",
    "client_secret",
    "admin_key",
    "id_token",
    "refresh_token",
    "bearer",
    "cookie",
    "session",
    "x-api-key",
    "cert",
    "private_key_id",
}

# Regex patterns for sensitive tokens inside arbitrary string fields
BEARER_PATTERN = re.compile(r"(Bearer\s+)[A-Za-z0-9\-._~+/]+=*", re.IGNORECASE)
BASIC_PATTERN = re.compile(r"(Basic\s+)[A-Za-z0-9+/]+=*", re.IGNORECASE)
AWS_KEY_PATTERN = re.compile(r"\b(AKIA[0-9A-Z]{16})\b")
CONN_STRING_PATTERN = re.compile(
    r"((?:postgres(?:ql)?(?:\+asyncpg)?|redis|mysql|mongodb)://[^:@\s]*:)([^@\s]+)(@)",
    re.IGNORECASE,
)
PRIVATE_KEY_PEM_PATTERN = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z0-9 ]*PRIVATE KEY-----"
)
COOKIE_PATTERN = re.compile(
    r"((?:session|jwt|token|access_token|refresh_token|sid|auth_token)=)[^;\s]+",
    re.IGNORECASE,
)

MAX_STRING_LEN = 10000
MAX_PAYLOAD_BYTES = 64 * 1024  # 64 KB


def sanitize_string(val: str) -> str:
    """Mask tokens, connection strings, keys, and truncate string values."""
    if not val:
        return val
    # Mask private keys, connection strings, bearer/basic tokens, and cookies
    redacted = PRIVATE_KEY_PEM_PATTERN.sub("[REDACTED_PRIVATE_KEY]", val)
    redacted = CONN_STRING_PATTERN.sub(r"\1[REDACTED]\3", redacted)
    redacted = BEARER_PATTERN.sub(r"\1[REDACTED]", redacted)
    redacted = BASIC_PATTERN.sub(r"\1[REDACTED]", redacted)
    redacted = AWS_KEY_PATTERN.sub("[REDACTED_AWS_KEY]", redacted)
    redacted = COOKIE_PATTERN.sub(r"\1[REDACTED]", redacted)

    if len(redacted) > MAX_STRING_LEN:
        return redacted[:MAX_STRING_LEN] + "... [TRUNCATED]"
    return redacted


def sanitize_data(data: Any, max_depth: int = 5) -> Any:
    """
    Recursively sanitize dictionaries and lists:
    - Redact sensitive keys
    - Mask sensitive tokens in string values
    - Truncate long strings
    """
    if max_depth <= 0:
        return "[TRUNCATED_DEPTH]"

    if isinstance(data, dict):
        sanitized = {}
        for k, v in data.items():
            k_str = str(k).lower()
            if any(s in k_str for s in SENSITIVE_KEYS):
                sanitized[k] = "[REDACTED]"
            else:
                sanitized[k] = sanitize_data(v, max_depth - 1)
        return sanitized

    if isinstance(data, list):
        return [sanitize_data(item, max_depth - 1) for item in data[:100]]

    if isinstance(data, str):
        return sanitize_string(data)

    return data


def bound_json_payload(data: Any) -> Any:
    """
    Ensure the data can be serialized to JSON and does not exceed maximum byte limit.
    """
    if data is None:
        return None
    cleaned = sanitize_data(data)
    try:
        raw = json.dumps(cleaned)
        if len(raw.encode("utf-8")) > MAX_PAYLOAD_BYTES:
            return {"error": "payload_exceeded_maximum_size", "truncated": True}
        return cleaned
    except Exception:
        return str(cleaned)[:1000]
