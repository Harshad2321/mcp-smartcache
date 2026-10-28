"""
Phase 5 Tests — Distributed Singleflight and Cross-Replica Cache Coordination.

Validates multi-replica coordination using Redis leases:
1. Multi-replica request coalescing: only 1 leader executes across 2 replicas.
2. Successful leader populates cache.
3. Failed leader does not poison cache.
4. Crashed/slow leader does not strand waiters (bounded timeout + fallback).
5. Lease ownership enforcement via Lua token check.
6. Stale leader cannot release a newer lease.
7. Invalidation race condition detection.
8. Separate tenant scopes do not share coordination or leases.
9. Redis outage triggers graceful local fallback.
"""

import asyncio
import os
import time
import uuid
import pytest

from mcp.types import CallToolResult, TextContent

from gateway.cache_store import RedisCacheStore
from gateway.distributed_singleflight import DistributedSingleflight, RELEASE_LEASE_LUA
from gateway.singleflight import Singleflight


REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
REDIS_TEST_DB = 15


def _test_redis_url() -> str:
    base = REDIS_URL.rstrip("/")
    if base.count("/") >= 3:
        base = base.rsplit("/", 1)[0]
    return f"{base}/{REDIS_TEST_DB}"


def _make_result(text: str = "ok", is_error: bool = False) -> CallToolResult:
    return CallToolResult(
        is_error=is_error,
        content=[TextContent(type="text", text=text)],
    )


@pytest.fixture
async def redis_store():
    url = _test_redis_url()
    store = RedisCacheStore(redis_url=url)
    try:
        await store.connect()
        await store.redis.flushdb()
        yield store
    finally:
        try:
            await store.redis.flushdb()
        except Exception:
            pass
        await store.close()


@pytest.mark.asyncio
async def test_distributed_coalescing_two_replicas(redis_store):
    """Two independent gateway replicas coordinate via Redis; only 1 leader executes."""
    # Replica A and Replica B sharing the same Redis instance
    replica_a = DistributedSingleflight(cache_store=redis_store, waiter_timeout=3.0)
    replica_b = DistributedSingleflight(cache_store=redis_store, waiter_timeout=3.0)

    call_count = 0
    cache_key = f"mcp-sc:test:coord:{uuid.uuid4().hex}"

    async def upstream_coro():
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.2)  # Simulate upstream latency
        res = _make_result("shared_distributed_result")
        # Populate cache as gateway router does
        await redis_store.set(cache_key, res, ttl_seconds=60)
        return res

    # Both replicas request the same key concurrently
    task_a = asyncio.create_task(replica_a.do(cache_key, upstream_coro, scope="tenant_1"))
    task_b = asyncio.create_task(replica_b.do(cache_key, upstream_coro, scope="tenant_1"))

    (res_a, leader_a), (res_b, leader_b) = await asyncio.gather(task_a, task_b)

    # Exactly one replica was the leader
    assert (leader_a and not leader_b) or (leader_b and not leader_a)
    assert call_count == 1
    assert res_a.content[0].text == "shared_distributed_result"
    assert res_b.content[0].text == "shared_distributed_result"


@pytest.mark.asyncio
async def test_successful_leader_populates_cache(redis_store):
    """When leader finishes, cache is populated and immediately readable."""
    dsf = DistributedSingleflight(cache_store=redis_store)
    cache_key = f"mcp-sc:test:pop:{uuid.uuid4().hex}"

    async def upstream_coro():
        res = _make_result("cached_payload")
        await redis_store.set(cache_key, res, ttl_seconds=60)
        return res

    res, is_leader = await dsf.do(cache_key, upstream_coro, scope="tenant_1")
    assert is_leader is True
    assert res.content[0].text == "cached_payload"

    # Verify cached in store
    cached = await redis_store.get(cache_key)
    assert cached is not None
    assert cached.content[0].text == "cached_payload"


@pytest.mark.asyncio
async def test_failed_leader_does_not_poison_cache(redis_store):
    """When upstream fails, cache remains empty and lock is released."""
    dsf = DistributedSingleflight(cache_store=redis_store)
    cache_key = f"mcp-sc:test:fail:{uuid.uuid4().hex}"

    async def failing_coro():
        await asyncio.sleep(0.05)
        raise RuntimeError("Upstream crashed!")

    with pytest.raises(RuntimeError, match="Upstream crashed!"):
        await dsf.do(cache_key, failing_coro, scope="tenant_1")

    # Cache must NOT be populated
    cached = await redis_store.get(cache_key)
    assert cached is None

    # Lock must be cleaned up
    lock_val = await redis_store.redis.get(f"mcp-sc:lock:flight:tenant_1:{cache_key}")
    assert lock_val is None


@pytest.mark.asyncio
async def test_slow_leader_timeout_fallback(redis_store):
    """When leader exceeds waiter timeout, waiter times out and falls back to independent execution."""
    replica_a = DistributedSingleflight(cache_store=redis_store, lease_ttl_ms=5000, waiter_timeout=0.3)
    replica_b = DistributedSingleflight(cache_store=redis_store, lease_ttl_ms=5000, waiter_timeout=0.3)

    cache_key = f"mcp-sc:test:timeout:{uuid.uuid4().hex}"
    b_called = False

    async def leader_slow():
        await asyncio.sleep(1.0)  # Exceeds waiter's 0.3s timeout
        return _make_result("leader_late")

    async def waiter_fallback():
        nonlocal b_called
        b_called = True
        return _make_result("waiter_fallback_result")

    # Start slow leader on replica A
    task_a = asyncio.create_task(replica_a.do(cache_key, leader_slow, scope="tenant_1"))
    await asyncio.sleep(0.05)  # Let A acquire lease

    # Start waiter on replica B with short timeout
    task_b = asyncio.create_task(replica_b.do(cache_key, waiter_fallback, scope="tenant_1"))

    res_b, was_leader_b = await task_b
    assert b_called is True
    assert res_b.content[0].text == "waiter_fallback_result"

    # Clean up task A
    await task_a


@pytest.mark.asyncio
async def test_atomic_lease_ownership_enforcement(redis_store):
    """Lua script deletes lease only if ownership token matches."""
    lock_key = f"mcp-sc:test:lock:{uuid.uuid4().hex}"
    correct_token = "correct_token_123"
    wrong_token = "wrong_token_456"

    # Set lock with correct token
    await redis_store.redis.set(lock_key, correct_token, px=10000)

    # Attempt delete with wrong token
    res_wrong = await redis_store.redis.eval(RELEASE_LEASE_LUA, 1, lock_key, wrong_token)
    assert res_wrong == 0
    # Key still exists
    assert await redis_store.redis.get(lock_key) == correct_token

    # Attempt delete with correct token
    res_correct = await redis_store.redis.eval(RELEASE_LEASE_LUA, 1, lock_key, correct_token)
    assert res_correct == 1
    # Key is gone
    assert await redis_store.redis.get(lock_key) is None


@pytest.mark.asyncio
async def test_invalidation_race_condition_cleans_stale_cache(redis_store):
    """If invalidation happens during upstream execution, key is evicted."""
    dsf = DistributedSingleflight(cache_store=redis_store)
    cache_key = f"mcp-sc:test:inv_race:{uuid.uuid4().hex}"
    tool_name = "test_server__list_items"
    scope = "tenant_1"

    async def upstream_coro():
        time.sleep(0.1)  # Ensure time advances
        # Simulate concurrent cache invalidation happening during execution
        inv_key = f"mcp-sc:inv_ts:{tool_name}:{scope}"
        await redis_store.redis.set(inv_key, str(time.time() + 0.05))
        # Leader sets cache
        res = _make_result("stale_invalidation_candidate")
        await redis_store.set(cache_key, res, ttl_seconds=60)
        return res

    res, is_leader = await dsf.do(
        cache_key,
        upstream_coro,
        namespaced_name=tool_name,
        scope=scope,
    )
    assert is_leader is True

    # Because invalidation occurred during execution, key must have been deleted
    cached = await redis_store.get(cache_key)
    assert cached is None


@pytest.mark.asyncio
async def test_tenant_isolation_coordination(redis_store):
    """Distinct tenant scopes use distinct grouping keys and execute independently."""
    dsf = DistributedSingleflight(cache_store=redis_store)
    base_key = f"mcp-sc:test:tenant_iso:{uuid.uuid4().hex}"

    calls_t1 = 0
    calls_t2 = 0

    async def coro_t1():
        nonlocal calls_t1
        calls_t1 += 1
        await asyncio.sleep(0.05)
        return _make_result("tenant_1_result")

    async def coro_t2():
        nonlocal calls_t2
        calls_t2 += 1
        await asyncio.sleep(0.05)
        return _make_result("tenant_2_result")

    # Run simultaneously for tenant 1 and tenant 2
    task1 = asyncio.create_task(dsf.do(base_key, coro_t1, scope="tenant_1"))
    task2 = asyncio.create_task(dsf.do(base_key, coro_t2, scope="tenant_2"))

    (res1, leader1), (res2, leader2) = await asyncio.gather(task1, task2)

    # Both must be leaders in their own tenant scope
    assert leader1 is True
    assert leader2 is True
    assert calls_t1 == 1
    assert calls_t2 == 1
    assert res1.content[0].text == "tenant_1_result"
    assert res2.content[0].text == "tenant_2_result"


@pytest.mark.asyncio
async def test_redis_unavailability_fallback_to_local_singleflight():
    """When Redis is unavailable (store is None), DistributedSingleflight safely uses local singleflight."""
    dsf = DistributedSingleflight(cache_store=None)

    call_count = 0

    async def coro():
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.05)
        return _make_result("fallback_ok")

    # Race 3 callers locally
    tasks = [asyncio.create_task(dsf.do("local_key", coro)) for _ in range(3)]
    results = await asyncio.gather(*tasks)

    assert call_count == 1
    for res, _ in results:
        assert res.content[0].text == "fallback_ok"
