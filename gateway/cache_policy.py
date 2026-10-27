"""
Phase 2: Cache Policy Model.

An explicit allowlist model — caching is disabled for every tool unless an explicit
CachePolicy with enabled=True and read_only=True is registered for its namespaced name.
"""

from dataclasses import dataclass, field
from typing import FrozenSet, Optional


# Hard cap defaults — never store a cached entry larger than this.
DEFAULT_MAX_RESPONSE_BYTES: int = 512 * 1024  # 512 KB
DEFAULT_TTL_SECONDS: int = 300               # 5 minutes
DEFAULT_UPSTREAM_TIMEOUT: float = 30.0       # seconds

# Policy schema version — bump when key semantics change to prevent unsafe reuse.
POLICY_SCHEMA_VERSION: str = "1"


@dataclass(frozen=True)
class CachePolicy:
    """
    Per-tool cache configuration.

    A tool MUST have enabled=True AND read_only=True to be eligible for caching.
    Any other combination means the tool will never be cached.
    """

    # Fully qualified namespaced tool name, e.g. "repo_metadata__get_repo_info"
    tool_name: str

    # Whether caching is explicitly allowed for this tool.
    enabled: bool = False

    # TTL in seconds. Only consulted when enabled=True.
    ttl_seconds: int = DEFAULT_TTL_SECONDS

    # This tool only reads state; it never mutates. Must be True to cache.
    read_only: bool = False

    # Policy schema version — included in cache keys to prevent stale reuse
    # across policy changes.
    policy_version: str = POLICY_SCHEMA_VERSION

    # Max cacheable serialised response in bytes. Responses exceeding this are
    # not stored (passthrough still succeeds).
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES

    # Optional human-readable tags for invalidation grouping.
    # Use a frozenset so the policy remains hashable.
    invalidation_tags: FrozenSet[str] = field(default_factory=frozenset)


class CachePolicyRegistry:
    """
    Registry of explicit per-tool cache policies.

    Access pattern: O(1) lookup by namespaced tool name.
    Unknown tools always return an empty (disabled) policy — caching is off by default.
    """

    def __init__(self) -> None:
        self._policies: dict[str, CachePolicy] = {}

    def register(self, policy: CachePolicy) -> None:
        """Register or replace a policy for a specific tool."""
        self._policies[policy.tool_name] = policy

    def get(self, tool_name: str) -> CachePolicy:
        """
        Return the policy for tool_name, or a disabled default policy.
        Callers must always check policy.enabled and policy.read_only.
        """
        return self._policies.get(
            tool_name,
            CachePolicy(tool_name=tool_name, enabled=False, read_only=False),
        )

    def is_eligible(self, tool_name: str) -> bool:
        """Return True only if this tool has an explicit enabled, read-only policy."""
        p = self.get(tool_name)
        return p.enabled and p.read_only

    def all_policies(self) -> list[CachePolicy]:
        """Return all registered policies."""
        return list(self._policies.values())
