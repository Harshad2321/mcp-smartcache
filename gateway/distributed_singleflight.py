"""
Phase 5: Distributed Singleflight Request Coalescing.

Extends in-process singleflight across multiple gateway replicas using Redis leases,
atomic Lua release, Pub/Sub completion notification, invalidation race protection,
and bounded waiter timeouts with graceful fallback.
"""

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Callable, Optional, Tuple

from mcp.types import CallToolResult

from gateway.cache_store import RedisCacheStore, CacheError
from gateway.singleflight import Singleflight
import gateway.cache_metrics as metrics

logger = logging.getLogger(__name__)

# Lua script for atomic lease release: only delete if current value equals ownership token
RELEASE_LEASE_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""


class DistributedSingleflight:
    """
    Coordinates concurrent requests across gateway replicas using Redis leases.
    Falls back gracefully to process-local singleflight when Redis is unavailable.
    """

    def __init__(
        self,
        cache_store: Optional[RedisCacheStore] = None,
        lease_ttl_ms: int = 15000,
        waiter_timeout: float = 6.0,
        local_singleflight: Optional[Singleflight] = None,
    ):
        self.cache_store = cache_store
        self.lease_ttl_ms = lease_ttl_ms
        self.waiter_timeout = waiter_timeout
        self.local_sf = local_singleflight or Singleflight()

    async def do(
        self,
        key: str,
        coro_factory: Callable[[], Any],
        namespaced_name: str = "unknown__unknown",
        scope: str = "default",
    ) -> Tuple[CallToolResult, bool]:
        """
        Execute coro_factory() as a distributed leader or wait for an in-flight leader.
        Returns:
            (result, was_leader)
        """
        # If Redis is unavailable, fall back to in-process singleflight immediately
        if not self.cache_store or not self.cache_store.redis:
            metrics.record_fallback(namespaced_name)
            return await self.local_sf.do(key, coro_factory)

        group_key = f"flight:{scope}:{key}"
        lock_key = f"mcp-sc:lock:{group_key}"
        chan_name = f"mcp-sc:chan:{group_key}"
        owner_token = uuid.uuid4().hex

        # 1. Attempt to acquire distributed lease atomically with NX PX
        try:
            acquired = await self.cache_store.redis.set(
                lock_key,
                owner_token,
                nx=True,
                px=self.lease_ttl_ms,
            )
        except Exception as exc:
            logger.warning("Redis lease acquisition error for %s (fallback to local): %s", namespaced_name, exc)
            metrics.record_lock_failure(namespaced_name)
            metrics.record_fallback(namespaced_name)
            return await self.local_sf.do(key, coro_factory)

        # ── Distributed Leader Path ───────────────────────────────────────────
        if acquired:
            metrics.record_distributed_leader(namespaced_name)
            start_time = time.time()

            try:
                # Double-check cache inside lease to avoid duplicate execution
                try:
                    cached = await self.cache_store.get(key)
                    if cached is not None:
                        # Release lock early and return cached
                        await self._release_lease(lock_key, owner_token)
                        return cached, False
                except Exception:
                    pass

                # Execute upstream coroutine
                result: CallToolResult = await coro_factory()

                # Invalidation race check: verify no invalidation occurred during computation
                inv_key = f"mcp-sc:inv_ts:{namespaced_name}:{scope}"
                try:
                    inv_ts = await self.cache_store.redis.get(inv_key)
                    if inv_ts and float(inv_ts) > start_time:
                        logger.warning(
                            "Cache invalidation occurred during execution of %s (ts=%s > start=%s); invalidating key",
                            namespaced_name, inv_ts, start_time,
                        )
                        # Remove potentially stale populated key
                        await self.cache_store.redis.delete(key)
                except Exception:
                    pass

                # Notify distributed waiters that result is ready
                try:
                    payload = json.dumps({"status": "completed", "token": owner_token})
                    await self.cache_store.redis.publish(chan_name, payload)
                except Exception as pub_err:
                    logger.debug("Failed to publish distributed completion notification: %s", pub_err)

                return result, True

            except BaseException as exc:
                # Notify waiters of failure so they don't hang
                try:
                    payload = json.dumps({"status": "failed", "error": str(exc), "token": owner_token})
                    await self.cache_store.redis.publish(chan_name, payload)
                except Exception:
                    pass
                raise

            finally:
                # Always release lease atomically using ownership token
                await self._release_lease(lock_key, owner_token)

        # ── Distributed Waiter Path ───────────────────────────────────────────
        metrics.record_distributed_waiter(namespaced_name)
        pubsub = None

        try:
            pubsub = self.cache_store.redis.pubsub()
            await pubsub.subscribe(chan_name)
        except Exception as exc:
            logger.debug("Could not subscribe to distributed notification channel: %s", exc)
            pubsub = None

        deadline = time.time() + self.waiter_timeout
        poll_interval = 0.25

        try:
            while time.time() < deadline:
                # 1. Check if cache was populated by leader
                try:
                    cached = await self.cache_store.get(key)
                    if cached is not None:
                        return cached, False
                except Exception:
                    pass

                # 2. Check Pub/Sub notification if subscribed
                if pubsub:
                    try:
                        msg = await asyncio.wait_for(
                            pubsub.get_message(ignore_subscribe_messages=True, timeout=poll_interval),
                            timeout=poll_interval,
                        )
                        if msg and msg.get("data"):
                            data = json.loads(msg["data"])
                            if data.get("status") == "completed":
                                cached = await self.cache_store.get(key)
                                if cached is not None:
                                    return cached, False
                            elif data.get("status") == "failed":
                                break
                    except asyncio.TimeoutError:
                        pass
                    except Exception:
                        await asyncio.sleep(poll_interval)
                else:
                    await asyncio.sleep(poll_interval)

            # Waiter timed out waiting for leader or leader failed
            metrics.record_waiter_timeout(namespaced_name)
            metrics.record_fallback(namespaced_name)
            logger.warning(
                "Distributed waiter timed out waiting for %s after %.1fs; falling back to execution",
                namespaced_name, self.waiter_timeout,
            )
            # Execute with local singleflight fallback to prevent unbounded stampede
            return await self.local_sf.do(key, coro_factory)

        finally:
            if pubsub:
                try:
                    await pubsub.unsubscribe(chan_name)
                    if hasattr(pubsub, "aclose"):
                        await pubsub.aclose()
                    else:
                        await pubsub.close()
                except Exception:
                    pass

    async def _release_lease(self, lock_key: str, owner_token: str) -> None:
        """Atomically delete lock key only if held token matches owner_token."""
        if not self.cache_store or not self.cache_store.redis:
            return
        try:
            await self.cache_store.redis.eval(RELEASE_LEASE_LUA, 1, lock_key, owner_token)
        except Exception as exc:
            logger.debug("Failed to release distributed lease for %s: %s", lock_key, exc)
