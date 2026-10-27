"""
Phase 2 Tests — Cache Policy and Key Builder.

These tests are pure-Python, no Redis, no network, no mocks.
They verify:
- CachePolicyRegistry allowlist semantics (caching disabled by default)
- Determinism and collision resistance of build_cache_key
- Key structure matches documented contract
- Argument canonicalisation (key order invariance)
- Policy version inclusion in key digest
- build_tag_index_key and build_tool_index_key helpers
"""

import hashlib
import json
import pytest

from gateway.cache_policy import (
    CachePolicy,
    CachePolicyRegistry,
    POLICY_SCHEMA_VERSION,
    DEFAULT_TTL_SECONDS,
)
from gateway.cache_key import (
    build_cache_key,
    build_tag_index_key,
    build_tool_index_key,
    _canonicalise,
)


# ── CachePolicyRegistry allowlist tests ───────────────────────────────────────


class TestCachePolicyRegistry:
    def test_unknown_tool_is_not_eligible(self):
        """Caching must be disabled for any tool not explicitly registered."""
        reg = CachePolicyRegistry()
        assert not reg.is_eligible("some_server__get_something")

    def test_unknown_tool_returns_disabled_policy(self):
        reg = CachePolicyRegistry()
        policy = reg.get("repo_metadata__get_repo_info")
        assert policy.enabled is False
        assert policy.read_only is False

    def test_partial_policy_no_read_only_is_not_eligible(self):
        """enabled=True alone is not sufficient; read_only must also be True."""
        reg = CachePolicyRegistry()
        reg.register(CachePolicy(tool_name="svc__tool", enabled=True, read_only=False))
        assert not reg.is_eligible("svc__tool")

    def test_partial_policy_read_only_not_enabled_is_not_eligible(self):
        """read_only=True alone is not sufficient."""
        reg = CachePolicyRegistry()
        reg.register(CachePolicy(tool_name="svc__tool", enabled=False, read_only=True))
        assert not reg.is_eligible("svc__tool")

    def test_explicit_enabled_and_read_only_is_eligible(self):
        """Only enabled=True AND read_only=True grants eligibility."""
        reg = CachePolicyRegistry()
        reg.register(CachePolicy(tool_name="svc__tool", enabled=True, read_only=True))
        assert reg.is_eligible("svc__tool")

    def test_policy_registration_replaces_existing(self):
        reg = CachePolicyRegistry()
        reg.register(CachePolicy(tool_name="svc__tool", enabled=True, read_only=True, ttl_seconds=60))
        reg.register(CachePolicy(tool_name="svc__tool", enabled=False, read_only=False))
        assert not reg.is_eligible("svc__tool")

    def test_all_policies_listing(self):
        reg = CachePolicyRegistry()
        reg.register(CachePolicy(tool_name="a__t", enabled=True, read_only=True))
        reg.register(CachePolicy(tool_name="b__t", enabled=True, read_only=True))
        assert {p.tool_name for p in reg.all_policies()} == {"a__t", "b__t"}

    def test_similar_tool_names_do_not_alias(self):
        """Different tool names must never share eligibility state."""
        reg = CachePolicyRegistry()
        reg.register(CachePolicy(tool_name="svc__get_repo_info", enabled=True, read_only=True))
        assert not reg.is_eligible("svc__get_repo_info_extended")
        assert not reg.is_eligible("svc__get")

    def test_name_contains_get_not_eligible_without_explicit_policy(self):
        """Eligibility is never inferred from tool name containing 'get', 'list', etc."""
        reg = CachePolicyRegistry()
        for name in [
            "repo_metadata__get_repo_info",
            "issues__list_open_issues",
            "svc__read_config",
            "svc__search_results",
        ]:
            assert not reg.is_eligible(name), f"Should not be eligible without explicit policy: {name}"


# ── Cache key builder tests ───────────────────────────────────────────────────


class TestCacheKeyBuilder:
    def _policy(self, version: str = "1") -> CachePolicy:
        return CachePolicy(
            tool_name="svc__tool",
            enabled=True,
            read_only=True,
            policy_version=version,
        )

    def test_key_prefix_and_structure(self):
        """Key must match: mcp-sc:v1:<schema_version>:<scope>:<sha256hex>"""
        key = build_cache_key("svc__tool", {"a": 1}, self._policy(), scope="none")
        parts = key.split(":")
        assert parts[0] == "mcp-sc"
        assert parts[1] == "v1"
        assert parts[2] == POLICY_SCHEMA_VERSION
        assert parts[3] == "none"
        assert len(parts[4]) == 64  # SHA-256 hex digest

    def test_determinism_same_args(self):
        p = self._policy()
        k1 = build_cache_key("svc__t", {"x": 1, "y": "z"}, p)
        k2 = build_cache_key("svc__t", {"x": 1, "y": "z"}, p)
        assert k1 == k2

    def test_argument_key_order_invariance(self):
        """Dict key order must NOT affect the cache key."""
        p = self._policy()
        k1 = build_cache_key("svc__t", {"a": 1, "b": 2}, p)
        k2 = build_cache_key("svc__t", {"b": 2, "a": 1}, p)
        assert k1 == k2

    def test_nested_argument_key_order_invariance(self):
        """Nested dicts must also be canonicalised."""
        p = self._policy()
        k1 = build_cache_key("svc__t", {"outer": {"x": 1, "y": 2}}, p)
        k2 = build_cache_key("svc__t", {"outer": {"y": 2, "x": 1}}, p)
        assert k1 == k2

    def test_list_order_is_preserved(self):
        """List element order is semantically significant and must be preserved."""
        p = self._policy()
        k1 = build_cache_key("svc__t", {"items": [1, 2, 3]}, p)
        k2 = build_cache_key("svc__t", {"items": [3, 2, 1]}, p)
        assert k1 != k2

    def test_different_tool_names_produce_different_keys(self):
        p = self._policy()
        k1 = build_cache_key("svc__tool_a", {"x": 1}, p)
        k2 = build_cache_key("svc__tool_b", {"x": 1}, p)
        assert k1 != k2

    def test_different_args_produce_different_keys(self):
        p = self._policy()
        k1 = build_cache_key("svc__t", {"x": 1}, p)
        k2 = build_cache_key("svc__t", {"x": 2}, p)
        assert k1 != k2

    def test_different_scopes_produce_different_keys(self):
        p = self._policy()
        k1 = build_cache_key("svc__t", {"x": 1}, p, scope="tenant-a")
        k2 = build_cache_key("svc__t", {"x": 1}, p, scope="tenant-b")
        assert k1 != k2

    def test_different_policy_versions_produce_different_keys(self):
        k1 = build_cache_key("svc__t", {"x": 1}, self._policy(version="1"))
        k2 = build_cache_key("svc__t", {"x": 1}, self._policy(version="2"))
        assert k1 != k2

    def test_int_vs_float_produces_different_keys(self):
        """int(1) and float(1.0) are distinct values and must produce different keys."""
        p = self._policy()
        k1 = build_cache_key("svc__t", {"x": 1}, p)    # int
        k2 = build_cache_key("svc__t", {"x": 1.0}, p)  # float
        assert k1 != k2, "int and float must not produce the same cache key"

    def test_empty_args(self):
        """Empty argument dicts must produce a stable, valid key."""
        p = self._policy()
        k = build_cache_key("svc__t", {}, p)
        assert k.startswith("mcp-sc:v1:")

    def test_none_values_in_args(self):
        """None is valid JSON and must be serialised correctly."""
        p = self._policy()
        k1 = build_cache_key("svc__t", {"x": None}, p)
        k2 = build_cache_key("svc__t", {"x": None}, p)
        assert k1 == k2

    def test_non_json_serialisable_raises_type_error(self):
        """Non-serialisable arguments must raise TypeError, not produce a key."""
        p = self._policy()
        with pytest.raises(TypeError, match="non-JSON-serialisable"):
            build_cache_key("svc__t", {"obj": object()}, p)


# ── Key index helpers ─────────────────────────────────────────────────────────


class TestIndexKeyHelpers:
    def test_tag_index_key_prefix(self):
        assert build_tag_index_key("team-a").startswith("mcp-sc:tag:")

    def test_tag_index_key_deterministic(self):
        assert build_tag_index_key("tag1") == build_tag_index_key("tag1")

    def test_tool_index_key_prefix(self):
        assert build_tool_index_key("repo__get_info").startswith("mcp-sc:idx:")

    def test_tool_index_key_double_underscore_safe(self):
        key = build_tool_index_key("repo_metadata__get_repo_info")
        # The __ separator must be rendered safely (. substitution)
        assert "__" not in key.split("mcp-sc:idx:")[1]

    def test_different_tags_produce_different_index_keys(self):
        assert build_tag_index_key("a") != build_tag_index_key("b")


# ── Canonicalise helper tests ─────────────────────────────────────────────────


class TestCanonicalize:
    def test_dict_sorted(self):
        result = _canonicalise({"z": 1, "a": 2})
        assert list(result.keys()) == ["a", "z"]

    def test_nested_dict_sorted(self):
        result = _canonicalise({"b": {"d": 1, "c": 2}, "a": 0})
        assert list(result.keys()) == ["a", "b"]
        assert list(result["b"].keys()) == ["c", "d"]

    def test_list_preserved_in_order(self):
        result = _canonicalise([3, 1, 2])
        assert result == [3, 1, 2]

    def test_scalar_unchanged(self):
        assert _canonicalise(42) == 42
        assert _canonicalise("hello") == "hello"
        assert _canonicalise(None) is None
        assert _canonicalise(True) is True
