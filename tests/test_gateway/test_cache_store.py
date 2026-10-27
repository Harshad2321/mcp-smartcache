"""
Phase 2 Tests — Redis Cache Store.

Requires a real Redis instance (or the gateway container's Redis access).
All tests are marked with @pytest.mark.asyncio and use the REDIS_URL env var.
If Redis is not available, tests are skipped rather than failing.

Tests verify:
- Set / get round-trip with TTL
- Miss on unknown key
- Oversized payload is skipped (returns False)
- Serialisation round-trip for is_error=False and is_error=True
- Per-tool index is maintained on set
- Per-tag index is maintained on set
- invalidate_tool deletes all entries and the index Set
- invalidate_tag deletes all entries and the tag Set
- Multiple concurrent writes to the same index key are safe
"""

import json
import os
import pytest
import asyncio

import redis.asyncio as aioredis

from mcp.types import CallToolResult, TextContent

from gateway.cache_store import RedisCacheStore, CacheError, _serialise_result, _deserialise_result
from gateway.cache_key import build_tool_index_key, build_tag_index_key


# ── Test fixtures ─────────────────────────────────────────────────────────────

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
REDIS_TEST_DB = 15   # Use DB 15 to avoid polluting the application DB.


def _test_redis_url() -> str:
    """Use a dedicated test database (db=15) to avoid polluting real cache."""
    base = REDIS_URL.rstrip("/")
    # Replace trailing /N with /15
    if base.count("/") >= 3:
        base = base.rsplit("/", 1)[0]
    return f"{base}/{REDIS_TEST_DB}"


def _make_result(text: str = "ok", is_error: bool = False) -> CallToolResult:
    return CallToolResult(
        is_error=is_error,
        content=[TextContent(type="text", text=text)],
    )


@pytest.fixture
async def store():
    """Provide a connected RedisCacheStore against the test DB, then flush on teardown."""
    url = _test_redis_url()
    s = RedisCacheStore(redis_url=url)
    try:
        await s.connect()
    except CacheError as err:
        pytest.skip(f"Redis unavailable, skipping cache store tests: {err}")
    # Flush only the test DB to avoid cross-contamination.
    await s._client.flushdb()
    yield s
    await s._client.flushdb()
    await s.close()


# ── Serialisation unit tests (no Redis needed) ────────────────────────────────


class TestSerialization:
    def test_round_trip_success_result(self):
        original = _make_result("some text", is_error=False)
        raw = _serialise_result(original)
        recovered = _deserialise_result(raw)
        assert recovered.is_error is False
        assert len(recovered.content) == 1
        assert recovered.content[0].text == "some text"

    def test_round_trip_error_result(self):
        original = _make_result('{"error": "not found"}', is_error=True)
        raw = _serialise_result(original)
        recovered = _deserialise_result(raw)
        assert recovered.is_error is True
        assert "not found" in recovered.content[0].text

    def test_serialised_form_is_valid_json(self):
        raw = _serialise_result(_make_result("hello"))
        data = json.loads(raw)
        assert "content" in data
        assert "is_error" in data

    def test_corrupted_json_returns_none_on_deserialise(self):
        # Simulate a corrupted Redis entry.
        from gateway.cache_store import _deserialise_result as des
        # _deserialise_result raises json.JSONDecodeError which is caught by get().
        with pytest.raises(json.JSONDecodeError):
            des("not json at all")


# ── Integration tests with real Redis ─────────────────────────────────────────


class TestRedisCacheStoreIntegration:
    async def test_set_and_get_round_trip(self, store):
        result = _make_result("test payload")
        key = "test:cache:key:1"
        wrote = await store.set(key, result, ttl_seconds=60)
        assert wrote is True
        retrieved = await store.get(key)
        assert retrieved is not None
        assert retrieved.is_error is False
        assert retrieved.content[0].text == "test payload"

    async def test_get_miss_returns_none(self, store):
        retrieved = await store.get("test:cache:key:nonexistent")
        assert retrieved is None

    async def test_ttl_is_respected(self, store):
        result = _make_result("ttl test")
        key = "test:cache:key:ttl"
        await store.set(key, result, ttl_seconds=1)
        retrieved_before = await store.get(key)
        assert retrieved_before is not None
        await asyncio.sleep(1.2)
        retrieved_after = await store.get(key)
        assert retrieved_after is None, "Entry should have expired"

    async def test_oversized_payload_is_skipped(self, store):
        # Create a result whose serialised form exceeds max_bytes.
        big_text = "x" * 1024
        result = _make_result(big_text)
        key = "test:cache:key:oversize"
        wrote = await store.set(key, result, ttl_seconds=60, max_bytes=100)
        assert wrote is False
        assert await store.get(key) is None

    async def test_error_result_is_not_cached_externally(self, store):
        """
        The store itself will store is_error=True if explicitly asked.
        The router is responsible for not calling store.set() on error results.
        This test verifies the store does NOT reject error results autonomously —
        policy is enforced at the router layer only.
        """
        result = _make_result('{"error": "oops"}', is_error=True)
        key = "test:cache:key:err"
        wrote = await store.set(key, result, ttl_seconds=60)
        assert wrote is True
        retrieved = await store.get(key)
        assert retrieved is not None
        assert retrieved.is_error is True

    async def test_delete_removes_entry(self, store):
        key = "test:cache:key:del"
        await store.set(key, _make_result("to delete"), ttl_seconds=60)
        assert await store.get(key) is not None
        await store.delete(key)
        assert await store.get(key) is None

    async def test_per_tool_index_maintained_on_set(self, store):
        tool_name = "repo_metadata__get_repo_info"
        idx_key = build_tool_index_key(tool_name)
        key = "test:cache:key:idx"
        await store.set(key, _make_result("indexed"), ttl_seconds=60, tool_name=tool_name)
        members = await store._client.smembers(idx_key)
        assert key in members

    async def test_per_tag_index_maintained_on_set(self, store):
        tag = "test-tag"
        tag_key = build_tag_index_key(tag)
        key = "test:cache:key:tag"
        await store.set(key, _make_result("tagged"), ttl_seconds=60, tags=frozenset([tag]))
        members = await store._client.smembers(tag_key)
        assert key in members

    async def test_invalidate_tool_removes_all_entries(self, store):
        tool_name = "svc__list_items"
        keys = [f"test:cache:key:tool:{i}" for i in range(3)]
        for key in keys:
            await store.set(key, _make_result(f"item {key}"), ttl_seconds=60, tool_name=tool_name)
        # All three should be present.
        for key in keys:
            assert await store.get(key) is not None

        deleted_count = await store.invalidate_tool(tool_name)
        assert deleted_count == 3

        # All three must now be absent.
        for key in keys:
            assert await store.get(key) is None

        # Index must also be cleared.
        idx_key = build_tool_index_key(tool_name)
        members = await store._client.smembers(idx_key)
        assert len(members) == 0

    async def test_invalidate_tool_on_empty_index_returns_zero(self, store):
        count = await store.invalidate_tool("nonexistent__tool")
        assert count == 0

    async def test_invalidate_tag_removes_all_entries(self, store):
        tag = "group-a"
        keys = [f"test:cache:key:tag:a:{i}" for i in range(4)]
        for key in keys:
            await store.set(key, _make_result(f"tagged {key}"), ttl_seconds=60, tags=frozenset([tag]))
        for key in keys:
            assert await store.get(key) is not None

        deleted_count = await store.invalidate_tag(tag)
        assert deleted_count == 4

        for key in keys:
            assert await store.get(key) is None

    async def test_multiple_tags_on_single_entry(self, store):
        key = "test:cache:key:multitag"
        tags = frozenset(["alpha", "beta"])
        await store.set(key, _make_result("multi"), ttl_seconds=60, tags=tags)
        for tag in tags:
            members = await store._client.smembers(build_tag_index_key(tag))
            assert key in members

    async def test_concurrent_writes_to_same_index_are_safe(self, store):
        """Multiple concurrent sets for the same tool must not lose index entries."""
        tool_name = "svc__concurrent_tool"
        keys = [f"test:cache:key:concurrent:{i}" for i in range(10)]

        async def write(k: str):
            await store.set(k, _make_result(f"val {k}"), ttl_seconds=60, tool_name=tool_name)

        await asyncio.gather(*[write(k) for k in keys])

        idx_key = build_tool_index_key(tool_name)
        members = await store._client.smembers(idx_key)
        for key in keys:
            assert key in members, f"Missing index entry for {key}"
