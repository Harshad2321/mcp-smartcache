"""
Phase 6 Feature C Tests: Distributed Quotas and Backpressure.
Tests distributed rate limiting and concurrency limits using real Redis
coordination across independent gateway manager instances.
"""

import asyncio
import os
import pytest
import redis.asyncio as aioredis

from gateway.quota_manager import DistributedQuotaManager
from storage.models.entities import ResourceLimits, TenantPolicy

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")


@pytest.fixture
async def redis_client():
    r = aioredis.from_url(REDIS_URL, decode_responses=True)
    yield r
    await r.aclose()


# ── 1. Distributed Rate Limiter Tests ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_distributed_rate_limiting_across_instances(redis_client):
    # Two independent gateway instances sharing the same Redis
    gateway_inst_1 = DistributedQuotaManager(redis_client=redis_client)
    gateway_inst_2 = DistributedQuotaManager(redis_client=redis_client)

    tenant_id = "test_rate_tenant"
    # Clean any leftover keys
    async for key in redis_client.scan_iter(match=f"mcp-sc:rate:{tenant_id}:*"):
        await redis_client.delete(key)

    policy = TenantPolicy(
        policy_id="pol_rate",
        tenant_id=tenant_id,
        resource_limits=ResourceLimits(rate_limit_rpm=4, burst_limit=0),
    )

    # Instance 1 consumes 2 permits
    allowed_1a, _ = await gateway_inst_1.check_rate_limit(tenant_id, policy)
    allowed_1b, _ = await gateway_inst_1.check_rate_limit(tenant_id, policy)
    assert allowed_1a is True
    assert allowed_1b is True

    # Instance 2 consumes 2 permits
    allowed_2a, _ = await gateway_inst_2.check_rate_limit(tenant_id, policy)
    allowed_2b, _ = await gateway_inst_2.check_rate_limit(tenant_id, policy)
    assert allowed_2a is True
    assert allowed_2b is True

    # Limit of 4 is now reached across instances.
    # 5th request on Instance 1 MUST be rejected!
    allowed_rejected_1, retry_after_1 = await gateway_inst_1.check_rate_limit(tenant_id, policy)
    assert allowed_rejected_1 is False
    assert retry_after_1 > 0

    # 6th request on Instance 2 MUST ALSO be rejected!
    allowed_rejected_2, retry_after_2 = await gateway_inst_2.check_rate_limit(tenant_id, policy)
    assert allowed_rejected_2 is False
    assert retry_after_2 > 0


# ── 2. Distributed Concurrency Limiter Tests ──────────────────────────────────

@pytest.mark.asyncio
async def test_distributed_concurrency_across_instances(redis_client):
    gateway_inst_1 = DistributedQuotaManager(redis_client=redis_client, lease_timeout_seconds=5.0)
    gateway_inst_2 = DistributedQuotaManager(redis_client=redis_client, lease_timeout_seconds=5.0)

    tenant_id = "test_concurrency_tenant"
    await redis_client.delete(f"mcp-sc:concurrency:{tenant_id}")

    policy = TenantPolicy(
        policy_id="pol_concurrency",
        tenant_id=tenant_id,
        resource_limits=ResourceLimits(max_concurrent_upstream=2),
    )

    # 1. Instance 1 acquires permit 1
    acq1, prm1, _ = await gateway_inst_1.acquire_concurrency_permit(tenant_id, policy)
    assert acq1 is True
    assert prm1 is not None

    # 2. Instance 2 acquires permit 2
    acq2, prm2, _ = await gateway_inst_2.acquire_concurrency_permit(tenant_id, policy)
    assert acq2 is True
    assert prm2 is not None

    # 3. Capacity is 2. Both instance 1 and instance 2 must be rejected now
    acq3, prm3, retry3 = await gateway_inst_1.acquire_concurrency_permit(tenant_id, policy)
    assert acq3 is False
    assert prm3 is None
    assert retry3 > 0

    acq4, prm4, _ = await gateway_inst_2.acquire_concurrency_permit(tenant_id, policy)
    assert acq4 is False
    assert prm4 is None

    # 4. Instance 1 releases permit 1
    rel1 = await gateway_inst_1.release_concurrency_permit(tenant_id, prm1)
    assert rel1 is True

    # 5. Now Instance 2 can acquire a new permit!
    acq5, prm5, _ = await gateway_inst_2.acquire_concurrency_permit(tenant_id, policy)
    assert acq5 is True
    assert prm5 is not None

    # Clean release
    await gateway_inst_2.release_concurrency_permit(tenant_id, prm2)
    await gateway_inst_2.release_concurrency_permit(tenant_id, prm5)


@pytest.mark.asyncio
async def test_concurrency_lease_automatic_expiration(redis_client):
    # Short 1.0 second lease timeout to test expiration
    manager = DistributedQuotaManager(redis_client=redis_client, lease_timeout_seconds=1.0)
    tenant_id = "test_lease_expiry_tenant"
    await redis_client.delete(f"mcp-sc:concurrency:{tenant_id}")

    policy = TenantPolicy(
        policy_id="pol_expiry",
        tenant_id=tenant_id,
        resource_limits=ResourceLimits(max_concurrent_upstream=1),
    )

    # Acquire permit
    acq1, prm1, _ = await manager.acquire_concurrency_permit(tenant_id, policy)
    assert acq1 is True

    # Immediate second attempt fails
    acq2, _, _ = await manager.acquire_concurrency_permit(tenant_id, policy)
    assert acq2 is False

    # Wait for lease to expire (1.2 seconds) without releasing
    await asyncio.sleep(1.2)

    # Stale lease should be auto-purged on next acquire
    acq3, prm3, _ = await manager.acquire_concurrency_permit(tenant_id, policy)
    assert acq3 is True
    assert prm3 != prm1

    await manager.release_concurrency_permit(tenant_id, prm3)
