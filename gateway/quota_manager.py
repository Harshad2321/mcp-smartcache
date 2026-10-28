"""
Phase 6: Distributed Quotas and Backpressure Manager.
Coordinates multi-replica rate limiting and concurrency limits via Redis.
Guarantees clean permit leases with automatic TTL expiry, atomic Lua release,
and explicit graceful failure policies.
"""

import asyncio
import logging
import math
import os
import time
import uuid
from typing import Any, Dict, Optional, Tuple

from storage.models.entities import TenantPolicy
from gateway.cache_metrics import record_quota_rejection

logger = logging.getLogger(__name__)

# Lua script for atomic distributed concurrency permit acquisition
ACQUIRE_CONCURRENCY_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local lease_timeout = tonumber(ARGV[2])
local max_concurrent = tonumber(ARGV[3])
local permit_id = ARGV[4]

-- 1. Purge expired leases
redis.call('ZREMRANGEBYSCORE', key, '-inf', now - lease_timeout)

-- 2. Check active leases
local count = redis.call('ZCARD', key)
if count < max_concurrent then
    redis.call('ZADD', key, now, permit_id)
    redis.call('EXPIRE', key, math.ceil(lease_timeout * 2))
    return {1, count + 1}
else
    return {0, count}
end
"""

# Lua script for atomic distributed concurrency permit release
RELEASE_CONCURRENCY_LUA = """
local key = KEYS[1]
local permit_id = ARGV[1]
return redis.call('ZREM', key, permit_id)
"""


class DistributedQuotaManager:
    """
    Manages tenant quotas (rate limits and concurrency) across multiple gateway replicas.
    """

    def __init__(self, redis_client: Optional[Any] = None, lease_timeout_seconds: float = 60.0):
        self.redis = redis_client
        self.lease_timeout = lease_timeout_seconds
        self._acquire_script = None
        self._release_script = None
        # Local fallback tracking in case Redis is temporarily down
        self._local_rate: Dict[str, Tuple[int, int]] = {}  # tenant_id -> (minute_epoch, count)
        self._local_concurrency: Dict[str, int] = {}
        self._local_lock = asyncio.Lock()

    def set_redis(self, redis_client: Any) -> None:
        self.redis = redis_client
        self._acquire_script = None
        self._release_script = None

    async def check_rate_limit(self, tenant_id: str, policy: TenantPolicy) -> Tuple[bool, int]:
        """
        Check if the incoming request complies with tenant rate limits (RPM + burst).
        Returns (allowed: bool, retry_after_seconds: int).
        """
        rpm = policy.resource_limits.rate_limit_rpm
        burst = policy.resource_limits.burst_limit
        max_allowed = rpm + burst

        now = time.time()
        minute_epoch = int(now // 60)
        seconds_remaining = 60 - int(now % 60)

        if self.redis:
            rate_key = f"mcp-sc:rate:{tenant_id}:{minute_epoch}"
            try:
                # Pipeline INCR and EXPIRE for consistency
                pipe = self.redis.pipeline()
                pipe.incr(rate_key)
                pipe.expire(rate_key, 120)
                results = await pipe.execute()
                current_count = results[0]

                if current_count > max_allowed:
                    record_quota_rejection("rate_limit")
                    return False, max(1, seconds_remaining)
                return True, 0
            except Exception as e:
                logger.warning("Redis rate limit check failed for %s, falling back to local: %s", tenant_id, e)

        # Local in-memory fallback if Redis is unavailable
        async with self._local_lock:
            stored_epoch, count = self._local_rate.get(tenant_id, (minute_epoch, 0))
            if stored_epoch != minute_epoch:
                count = 0
                stored_epoch = minute_epoch
            count += 1
            self._local_rate[tenant_id] = (stored_epoch, count)

            if count > max_allowed:
                record_quota_rejection("rate_limit")
                return False, max(1, seconds_remaining)
            return True, 0

    async def acquire_concurrency_permit(
        self, tenant_id: str, policy: TenantPolicy
    ) -> Tuple[bool, Optional[str], int]:
        """
        Attempt to acquire an active upstream concurrency permit for the tenant.
        Returns (acquired: bool, permit_id: Optional[str], retry_after_seconds: int).
        """
        max_concurrent = policy.resource_limits.max_concurrent_upstream
        permit_id = f"prm_{uuid.uuid4().hex[:16]}"
        now = time.time()

        if self.redis:
            key = f"mcp-sc:concurrency:{tenant_id}"
            try:
                if not self._acquire_script:
                    self._acquire_script = self.redis.register_script(ACQUIRE_CONCURRENCY_LUA)
                res = await self._acquire_script(
                    keys=[key],
                    args=[now, self.lease_timeout, max_concurrent, permit_id],
                )
                success = bool(res[0])
                if success:
                    return True, permit_id, 0
                else:
                    record_quota_rejection("concurrency")
                    return False, None, 2
            except Exception as e:
                logger.warning("Redis concurrency permit acquisition failed for %s: %s", tenant_id, e)

        # Local fallback if Redis is down: bounded local concurrency
        async with self._local_lock:
            active = self._local_concurrency.get(tenant_id, 0)
            if active < max_concurrent:
                self._local_concurrency[tenant_id] = active + 1
                return True, permit_id, 0
            else:
                record_quota_rejection("concurrency")
                return False, None, 2

    async def release_concurrency_permit(self, tenant_id: str, permit_id: Optional[str]) -> bool:
        """
        Release a previously acquired concurrency permit.
        """
        if not permit_id:
            return False

        if self.redis:
            key = f"mcp-sc:concurrency:{tenant_id}"
            try:
                if not self._release_script:
                    self._release_script = self.redis.register_script(RELEASE_CONCURRENCY_LUA)
                res = await self._release_script(keys=[key], args=[permit_id])
                return bool(res)
            except Exception as e:
                logger.warning("Redis concurrency permit release failed for %s (%s): %s", tenant_id, permit_id, e)

        # Local fallback release
        async with self._local_lock:
            active = self._local_concurrency.get(tenant_id, 0)
            if active > 0:
                self._local_concurrency[tenant_id] = active - 1
            return True


# Global singleton quota manager
quota_manager = DistributedQuotaManager()
