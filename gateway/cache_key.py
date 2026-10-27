"""
Phase 2: Deterministic, collision-resistant cache key builder.

Key structure (as SHA-256 hex of canonical JSON):
    mcp-sc:v1:<POLICY_SCHEMA_VERSION>:<SCOPE>:<SHA256(canonical_payload)>

Where canonical_payload is the UTF-8 encoding of:
    {"tool": "<namespaced_name>", "args": <sorted-key JSON>}

Safety properties:
- JSON object key ordering does not affect the key (sort_keys=True).
- Nested objects and arrays are serialised recursively with stable ordering.
- Numeric 1 and 1.0 are treated as distinct because Python's json module
  serialises int(1) as "1" and float(1.0) as "1.0". This is explicit and tested.
- Secrets must never appear in the scope segment. Only opaque scope identifiers
  (e.g. hashed tenant IDs) should be used.
- The authorization scope is a required parameter; "none" is used when Phase 2
  has no auth. Future phases replace it with a real scope token without touching
  key structure.
"""

import hashlib
import json
from typing import Any, Optional

from gateway.cache_policy import CachePolicy, POLICY_SCHEMA_VERSION

# Top-level namespace — bump only on incompatible serialisation changes.
_KEY_NAMESPACE = "mcp-sc"
_KEY_VERSION = "v1"


def _canonicalise(value: Any) -> Any:
    """
    Recursively sort dict keys so the same logical mapping always serialises
    identically regardless of insertion order. Lists are preserved as-is because
    list order is semantically significant in JSON argument values.
    """
    if isinstance(value, dict):
        return {k: _canonicalise(v) for k, v in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_canonicalise(item) for item in value]
    # Scalars (str, int, float, bool, None) are returned unchanged.
    # Values not representable by JSON are intentionally rejected below.
    return value


def build_cache_key(
    namespaced_tool_name: str,
    arguments: dict[str, Any],
    policy: CachePolicy,
    scope: str = "none",
) -> str:
    """
    Build a deterministic, collision-resistant Redis cache key.

    Args:
        namespaced_tool_name: e.g. "repo_metadata__get_repo_info"
        arguments:            The validated tool arguments dict.
        policy:               The CachePolicy for this tool (includes version).
        scope:                Opaque authorization scope identifier.
                              MUST NOT be a raw credential or secret.
                              Default "none" signals single-tenant / no-auth.

    Returns:
        A Redis key string of the form:
            mcp-sc:v1:1:none:<64-char hex SHA-256>

    Raises:
        TypeError: if arguments contain values not serialisable to JSON.
    """
    # Canonicalise arguments before serialisation.
    canonical_args = _canonicalise(arguments)

    # Payload: tool identity + canonical args.
    payload: dict[str, Any] = {
        "tool": namespaced_tool_name,
        "args": canonical_args,
        "policy_version": policy.policy_version,
    }

    try:
        payload_json = json.dumps(payload, sort_keys=True, ensure_ascii=True)
    except (TypeError, ValueError) as err:
        raise TypeError(
            f"Cannot build cache key: arguments contain non-JSON-serialisable "
            f"values for tool '{namespaced_tool_name}': {err}"
        ) from err

    digest = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()

    return f"{_KEY_NAMESPACE}:{_KEY_VERSION}:{POLICY_SCHEMA_VERSION}:{scope}:{digest}"


def build_tag_index_key(tag: str, scope: Optional[str] = None) -> str:
    """
    Build the Redis Set key that stores all cache entry keys for a given tag.
    Used for tag-based invalidation.
    If scope is provided, builds a tenant-isolated tag index.
    """
    if scope and scope != "*":
        return f"{_KEY_NAMESPACE}:tag:{scope}:{tag}"
    return f"{_KEY_NAMESPACE}:tag:{tag}"


def build_tool_index_key(namespaced_tool_name: str, scope: Optional[str] = None) -> str:
    """
    Build the Redis Set key that indexes all cache keys for a given tool.
    Used for per-tool invalidation.
    If scope is provided, builds a tenant-isolated tool index.
    """
    safe_name = namespaced_tool_name.replace("__", ".")
    if scope and scope != "*":
        return f"{_KEY_NAMESPACE}:idx:{scope}:{safe_name}"
    return f"{_KEY_NAMESPACE}:idx:{safe_name}"
