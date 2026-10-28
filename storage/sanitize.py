"""
Payload sanitization and size bounding for audit, trace, and workflow persistence.
"""

from __future__ import annotations

import json
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
}

MAX_STRING_LEN = 10000
MAX_PAYLOAD_BYTES = 64 * 1024  # 64 KB


def sanitize_data(data: Any, max_depth: int = 5) -> Any:
    """
    Recursively sanitize dictionaries and lists:
    - Redact sensitive keys
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
        if len(data) > MAX_STRING_LEN:
            return data[:MAX_STRING_LEN] + "... [TRUNCATED]"
        return data

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
