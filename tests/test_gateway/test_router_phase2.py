"""
Phase 2 Tests — Router caching integration.

Tests the full GatewayRouter with both:
(a) An in-memory fake cache store (no Redis required) for deterministic tests.
(b) Real mock upstream servers (in-process).

Test scenarios:
- Passthrough when no cache policy or policy disabled.
- Cache miss → upstream called → response cached.
- Cache hit → upstream NOT called.
- Cache error (read) → fail open to upstream.
- Cache error (write) → upstream result still returned.
- Error results from upstream are NOT cached.
- Singleflight: N concurrent callers for the same eligible key → 1 upstream call.
- Singleflight: keys are independent.
- Schema validation still prevents bad arguments (Phase 1 regression).
- Tool not found (Phase 1 regression).
- Upstream timeout produces structured error.
"""

import asyncio
import json
from typing import Any, Dict, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mcp.types import CallToolResult, TextContent

from gateway.registry import ServerRegistry, ServerConfig
from gateway.router import GatewayRouter, make_error_result
from gateway.cache_policy import CachePolicy, CachePolicyRegistry
from gateway.cache_store import RedisCacheStore, CacheError
from gateway.singleflight import Singleflight
from mocks.repo_metadata import MockRepoMetadataServer
from mocks.issues import MockIssuesServer


# ── Fake in-memory cache store (no Redis) ────────────────────────────────────


class FakeCacheStore:
    """Deterministic in-memory replacement for RedisCacheStore in unit tests."""

    def __init__(self):
        self._data: Dict[str, CallToolResult] = {}
        self.get_call_count: int = 0
        self.set_call_count: int = 0
        # Inject failures.
        self.fail_get: bool = False
        self.fail_set: bool = False

    async def get(self, key: str) -> Optional[CallToolResult]:
        self.get_call_count += 1
        if self.fail_get:
            raise CacheError("fake read error")
        return self._data.get(key)

    async def set(
        self,
        key: str,
        result: CallToolResult,
        ttl_seconds: int,
        tool_name: Optional[str] = None,
        tags=None,
        max_bytes: int = 512 * 1024,
    ) -> bool:
        self.set_call_count += 1
        if self.fail_set:
            raise CacheError("fake write error")
        payload = json.dumps({
            "is_error": result.is_error,
            "content": [{"text": c.text} for c in result.content if isinstance(c, TextContent)],
        })
        if len(payload) > max_bytes:
            return False
        self._data[key] = result
        return True

    async def invalidate_tool(self, name: str) -> int:
        count = sum(1 for _ in list(self._data.keys()) if True)
        self._data.clear()
        return count


# ── Router factory helpers ────────────────────────────────────────────────────


def _make_policy(tool_name: str, enabled: bool = True, ttl: int = 60) -> CachePolicy:
    return CachePolicy(
        tool_name=tool_name,
        enabled=enabled,
        read_only=True,
        ttl_seconds=ttl,
    )


async def _make_router(
    server_instance=None,
    policy: Optional[CachePolicy] = None,
    policies: Optional[list[CachePolicy]] = None,
    cache_store=None,
    singleflight: Optional[Singleflight] = None,
    upstream_timeout: float = 5.0,
    servers: Optional[list[tuple[str, Any]]] = None,
) -> GatewayRouter:
    registry = ServerRegistry()
    if servers:
        for name, inst in servers:
            await registry.register_server(
                ServerConfig(
                    name=name,
                    transport="in-process",
                    server_instance=inst,
                )
            )
    elif server_instance is not None:
        await registry.register_server(
            ServerConfig(
                name="repo_metadata",
                transport="in-process",
                server_instance=server_instance,
            )
        )
    policy_reg = CachePolicyRegistry()
    if policy:
        policy_reg.register(policy)
    if policies:
        for p in policies:
            policy_reg.register(p)
    return GatewayRouter(
        registry=registry,
        policy_registry=policy_reg,
        cache_store=cache_store,
        singleflight=singleflight or Singleflight(),
        upstream_timeout=upstream_timeout,
    )


# ── Phase 1 regression tests (passthrough) ───────────────────────────────────


class TestRouterPassthrough:
    async def test_no_policy_is_passthrough(self):
        """No explicit policy → always calls upstream."""
        mock_server = MockRepoMetadataServer()
        router = await _make_router(server_instance=mock_server)
        initial_calls = mock_server.call_counters["get_repo_info"]

        result = await router.route_tool_call(
            "repo_metadata__get_repo_info",
            {"owner": "alice", "repo": "proj"},
        )
        assert not result.is_error
        # Upstream was called.
        assert mock_server.call_counters["get_repo_info"] == initial_calls + 1

    async def test_disabled_policy_is_passthrough(self):
        """enabled=False → no caching, upstream always called."""
        mock_server = MockRepoMetadataServer()
        cache = FakeCacheStore()
        policy = _make_policy("repo_metadata__get_repo_info", enabled=False)
        router = await _make_router(server_instance=mock_server, policy=policy, cache_store=cache)
        initial_calls = mock_server.call_counters["get_repo_info"]

        for _ in range(3):
            result = await router.route_tool_call(
                "repo_metadata__get_repo_info",
                {"owner": "alice", "repo": "proj"},
            )
            assert not result.is_error

        assert mock_server.call_counters["get_repo_info"] == initial_calls + 3
        assert cache.get_call_count == 0

    async def test_tool_not_found_returns_structured_error(self):
        router = await _make_router()
        result = await router.route_tool_call("nonexistent__tool", {})
        assert result.is_error
        payload = json.loads(result.content[0].text)
        assert payload["error"] == "tool_not_found"

    async def test_schema_validation_rejects_bad_args(self):
        mock_server = MockRepoMetadataServer()
        router = await _make_router(server_instance=mock_server)
        result = await router.route_tool_call(
            "repo_metadata__get_repo_info",
            {"owner": 999},  # owner must be a string
        )
        assert result.is_error
        payload = json.loads(result.content[0].text)
        assert payload["error"] == "argument_validation_failed"


# ── Phase 2 cache behaviour tests ────────────────────────────────────────────


class TestRouterCaching:
    async def _eligible_setup(self, tool_name: str = "repo_metadata__get_repo_info"):
        mock_server = MockRepoMetadataServer()
        cache = FakeCacheStore()
        policy = _make_policy(tool_name)
        router = await _make_router(server_instance=mock_server, policy=policy, cache_store=cache)
        return router, mock_server, cache, tool_name

    async def test_cache_miss_populates_cache(self):
        router, mock_server, cache, tool_name = await self._eligible_setup()
        initial_calls = mock_server.call_counters["get_repo_info"]

        result = await router.route_tool_call(tool_name, {"owner": "alice", "repo": "proj"})
        assert not result.is_error
        assert mock_server.call_counters["get_repo_info"] == initial_calls + 1
        assert cache.set_call_count == 1

    async def test_cache_hit_avoids_upstream(self):
        router, mock_server, cache, tool_name = await self._eligible_setup()
        args = {"owner": "alice", "repo": "proj"}

        # First call — miss.
        r1 = await router.route_tool_call(tool_name, args)
        calls_after_first = mock_server.call_counters["get_repo_info"]

        # Second call — must be a hit.
        r2 = await router.route_tool_call(tool_name, args)
        assert mock_server.call_counters["get_repo_info"] == calls_after_first, "Upstream was called on cache hit!"
        assert cache.set_call_count == 1  # Only written once.
        assert r2.content[0].text == r1.content[0].text

    async def test_different_args_different_cache_entries(self):
        router, mock_server, cache, tool_name = await self._eligible_setup()
        initial_calls = mock_server.call_counters["get_repo_info"]

        await router.route_tool_call(tool_name, {"owner": "alice", "repo": "proj"})
        await router.route_tool_call(tool_name, {"owner": "bob", "repo": "other"})
        assert mock_server.call_counters["get_repo_info"] == initial_calls + 2
        assert cache.set_call_count == 2

    async def test_arg_key_order_uses_same_cache_entry(self):
        router, mock_server, cache, tool_name = await self._eligible_setup()

        r1 = await router.route_tool_call(tool_name, {"owner": "alice", "repo": "proj"})
        calls_after_first = mock_server.call_counters["get_repo_info"]

        # Same args, different key order.
        r2 = await router.route_tool_call(tool_name, {"repo": "proj", "owner": "alice"})
        assert mock_server.call_counters["get_repo_info"] == calls_after_first, (
            "Argument key order must not cause a cache miss."
        )

    async def test_error_result_from_upstream_is_not_cached(self):
        """If the upstream returns is_error=True, the router must NOT write to cache."""
        mock_server = MockRepoMetadataServer()
        mock_server.inject_error = True
        cache = FakeCacheStore()
        policy = _make_policy("repo_metadata__get_repo_info")
        router = await _make_router(server_instance=mock_server, policy=policy, cache_store=cache)

        result = await router.route_tool_call(
            "repo_metadata__get_repo_info",
            {"owner": "alice", "repo": "proj"},
        )
        assert result.is_error, "Expected error result from injected upstream"
        assert cache.set_call_count == 0, "Error results must never be cached"

    async def test_cache_read_error_fails_open_to_upstream(self):
        router, mock_server, cache, tool_name = await self._eligible_setup()
        cache.fail_get = True
        initial_calls = mock_server.call_counters["get_repo_info"]

        result = await router.route_tool_call(tool_name, {"owner": "alice", "repo": "proj"})
        # Should fail open and still return a result from upstream.
        assert not result.is_error
        assert mock_server.call_counters["get_repo_info"] == initial_calls + 1

    async def test_cache_write_error_still_returns_result(self):
        router, mock_server, cache, tool_name = await self._eligible_setup()
        cache.fail_set = True
        initial_calls = mock_server.call_counters["get_repo_info"]

        result = await router.route_tool_call(tool_name, {"owner": "alice", "repo": "proj"})
        assert not result.is_error
        assert mock_server.call_counters["get_repo_info"] == initial_calls + 1
        # Second call should also hit upstream (no cache entry written).
        result2 = await router.route_tool_call(tool_name, {"owner": "alice", "repo": "proj"})
        assert not result2.is_error
        assert mock_server.call_counters["get_repo_info"] == initial_calls + 2

    async def test_no_cache_store_means_passthrough(self):
        """GatewayRouter without a cache_store is pure passthrough even with an eligible policy."""
        mock_server = MockRepoMetadataServer()
        policy = _make_policy("repo_metadata__get_repo_info")
        router = await _make_router(server_instance=mock_server, policy=policy, cache_store=None)
        initial_calls = mock_server.call_counters["get_repo_info"]

        for _ in range(3):
            result = await router.route_tool_call(
                "repo_metadata__get_repo_info",
                {"owner": "alice", "repo": "proj"},
            )
            assert not result.is_error

        assert mock_server.call_counters["get_repo_info"] == initial_calls + 3

    async def test_mutation_or_non_readonly_tool_is_never_cached(self):
        """A tool with read_only=False must never be cached even if enabled=True."""
        mock_server = MockRepoMetadataServer()
        cache = FakeCacheStore()
        policy = CachePolicy(
            tool_name="repo_metadata__get_repo_info",
            enabled=True,
            read_only=False,  # Mutation/write tool!
            ttl_seconds=60,
        )
        router = await _make_router(server_instance=mock_server, policy=policy, cache_store=cache)
        initial_calls = mock_server.call_counters["get_repo_info"]

        r1 = await router.route_tool_call("repo_metadata__get_repo_info", {"owner": "alice", "repo": "proj"})
        assert not r1.is_error
        assert cache.set_call_count == 0

        r2 = await router.route_tool_call("repo_metadata__get_repo_info", {"owner": "alice", "repo": "proj"})
        assert not r2.is_error
        assert mock_server.call_counters["get_repo_info"] == initial_calls + 2
        assert cache.set_call_count == 0

    async def test_malformed_upstream_response_is_not_cached(self):
        """Malformed response detected by gateway must result in error and NOT be cached."""
        mock_server = MockRepoMetadataServer()
        mock_server.inject_malformed = True
        cache = FakeCacheStore()
        policy = _make_policy("repo_metadata__get_repo_info")
        router = await _make_router(server_instance=mock_server, policy=policy, cache_store=cache)

        result = await router.route_tool_call("repo_metadata__get_repo_info", {"owner": "alice", "repo": "proj"})
        assert result.is_error
        assert cache.set_call_count == 0

    async def test_different_tool_identities_produce_distinct_cache_entries(self):
        """Distinct tools (even with identical args or return shapes) have isolated cache entries."""
        repo_server = MockRepoMetadataServer()
        issues_server = MockIssuesServer()
        cache = FakeCacheStore()
        p1 = _make_policy("repo_metadata__get_status")
        p2 = _make_policy("issues__get_status")
        router = await _make_router(
            servers=[("repo_metadata", repo_server), ("issues", issues_server)],
            policies=[p1, p2],
            cache_store=cache,
        )

        r1 = await router.route_tool_call("repo_metadata__get_status", {})
        r2 = await router.route_tool_call("issues__get_status", {})
        assert not r1.is_error
        assert not r2.is_error
        assert cache.set_call_count == 2

        # Cache hits for each
        await router.route_tool_call("repo_metadata__get_status", {})
        await router.route_tool_call("issues__get_status", {})
        assert repo_server.call_counters["get_status"] == 1
        assert issues_server.call_counters["get_status"] == 1

    async def test_scope_isolation_prevents_sharing_cache(self):
        """Requests from different authorization scopes must produce distinct cache entries."""
        mock_server = MockRepoMetadataServer()
        cache = FakeCacheStore()
        policy = _make_policy("repo_metadata__get_repo_info")
        router = await _make_router(server_instance=mock_server, policy=policy, cache_store=cache)

        args = {"owner": "alice", "repo": "proj"}
        # Scope A
        r1 = await router.route_tool_call("repo_metadata__get_repo_info", args, scope="tenant-A")
        assert not r1.is_error
        assert mock_server.call_counters["get_repo_info"] == 1
        assert cache.set_call_count == 1

        # Scope B with identical args must NOT hit Scope A's cached entry
        r2 = await router.route_tool_call("repo_metadata__get_repo_info", args, scope="tenant-B")
        assert not r2.is_error
        assert mock_server.call_counters["get_repo_info"] == 2
        assert cache.set_call_count == 2


# ── Phase 2 singleflight tests ────────────────────────────────────────────────


class TestRouterSingleflight:
    async def test_concurrent_requests_same_key_one_upstream_call(self):
        """N concurrent requests for the same eligible key must execute upstream once."""
        mock_server = MockRepoMetadataServer()
        # Add artificial delay to make concurrency observable.
        mock_server.artificial_delay = 0.05
        cache = FakeCacheStore()
        policy = _make_policy("repo_metadata__get_repo_info")
        sf = Singleflight()
        router = await _make_router(
            server_instance=mock_server,
            policy=policy,
            cache_store=cache,
            singleflight=sf,
        )
        initial_calls = mock_server.call_counters["get_repo_info"]
        n = 8
        args = {"owner": "alice", "repo": "proj"}

        tasks = [
            asyncio.create_task(router.route_tool_call("repo_metadata__get_repo_info", args))
            for _ in range(n)
        ]
        results = await asyncio.gather(*tasks)

        # Exactly one upstream call.
        after_calls = mock_server.call_counters["get_repo_info"]
        assert after_calls == initial_calls + 1, (
            f"Expected 1 upstream call, got {after_calls - initial_calls}"
        )
        # Only one write to cache.
        assert cache.set_call_count == 1

        # All results identical.
        texts = [r.content[0].text for r in results]
        assert all(t == texts[0] for t in texts)

    async def test_concurrent_requests_different_args_independent(self):
        """Different argument sets must NOT be coalesced."""
        mock_server = MockRepoMetadataServer()
        mock_server.artificial_delay = 0.05
        cache = FakeCacheStore()
        policy = _make_policy("repo_metadata__get_repo_info")
        router = await _make_router(
            server_instance=mock_server,
            policy=policy,
            cache_store=cache,
        )
        initial_calls = mock_server.call_counters["get_repo_info"]
        arg_sets = [{"owner": f"user{i}", "repo": "proj"} for i in range(3)]

        tasks = [
            asyncio.create_task(
                router.route_tool_call("repo_metadata__get_repo_info", args)
            )
            for args in arg_sets
        ]
        results = await asyncio.gather(*tasks)
        # Each distinct arg set must have triggered one upstream call.
        after_calls = mock_server.call_counters["get_repo_info"]
        assert after_calls == initial_calls + 3

    async def test_ineligible_requests_are_not_deduplicated_by_singleflight(self):
        """Uncacheable/ineligible requests must NOT be coalesced under singleflight."""
        mock_server = MockRepoMetadataServer()
        mock_server.artificial_delay = 0.05
        cache = FakeCacheStore()
        # Ineligible policy (enabled=False)
        policy = _make_policy("repo_metadata__get_repo_info", enabled=False)
        router = await _make_router(
            server_instance=mock_server,
            policy=policy,
            cache_store=cache,
        )
        initial_calls = mock_server.call_counters["get_repo_info"]
        n = 4
        args = {"owner": "alice", "repo": "proj"}

        tasks = [
            asyncio.create_task(router.route_tool_call("repo_metadata__get_repo_info", args))
            for _ in range(n)
        ]
        results = await asyncio.gather(*tasks)

        # All 4 executed upstream because caching is disabled!
        after_calls = mock_server.call_counters["get_repo_info"]
        assert after_calls == initial_calls + n, (
            f"Expected {n} upstream calls for ineligible requests, got {after_calls - initial_calls}"
        )
        assert cache.set_call_count == 0

    async def test_upstream_timeout_produces_structured_error(self):
        mock_server = MockRepoMetadataServer()
        mock_server.artificial_delay = 2.0   # Longer than timeout below.
        router = await _make_router(
            server_instance=mock_server,
            upstream_timeout=0.05,  # Very short timeout.
        )
        result = await router.route_tool_call(
            "repo_metadata__get_repo_info",
            {"owner": "alice", "repo": "proj"},
        )
        assert result.is_error
        payload = json.loads(result.content[0].text)
        assert payload["error"] == "upstream_timeout"
