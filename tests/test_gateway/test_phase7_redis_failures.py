"""
Phase 7 Automated Security Regression Test:
Redis failure modes (fail-closed vs degrade-local) and quota permit lease isolation.
"""

from unittest.mock import AsyncMock, patch
import pytest

from gateway.quota_manager import DistributedQuotaManager
from storage.models.entities import ResourceLimits, TenantPolicy


@pytest.fixture
def test_policy():
    return TenantPolicy(
        policy_id="pol_test_7",
        tenant_id="tenant_failover",
        policy_version=1,
        is_active=True,
        resource_limits=ResourceLimits(
            rate_limit_rpm=10,
            burst_limit=2,
            max_concurrent_upstream=2,
        ),
    )


@pytest.mark.asyncio
async def test_redis_failure_fail_closed_policy(test_policy):
    """When QUOTA_REDIS_FAILURE_POLICY=fail_closed, Redis outage rejects requests."""
    qm = DistributedQuotaManager()
    failing_redis = AsyncMock()
    # Mock redis pipeline failing with connection error
    failing_redis.pipeline.side_effect = ConnectionError("Redis cluster unreachable")
    failing_redis.register_script.side_effect = ConnectionError("Redis cluster unreachable")
    qm.set_redis(failing_redis)

    with patch("gateway.quota_manager.get_redis_failure_policy", return_value="fail_closed"):
        # Rate limit must fail closed
        allowed, retry_after = await qm.check_rate_limit("tenant_failover", test_policy)
        assert allowed is False
        assert retry_after == 5

        # Concurrency acquisition must fail closed
        acquired, permit_id, retry_after = await qm.acquire_concurrency_permit("tenant_failover", test_policy)
        assert acquired is False
        assert permit_id is None
        assert retry_after == 5


@pytest.mark.asyncio
async def test_redis_failure_degrade_local_policy(test_policy):
    """When QUOTA_REDIS_FAILURE_POLICY=degrade_local, requests fall back to bounded local limits."""
    qm = DistributedQuotaManager()
    failing_redis = AsyncMock()
    failing_redis.pipeline.side_effect = ConnectionError("Redis cluster unreachable")
    qm.set_redis(failing_redis)

    with patch("gateway.quota_manager.get_redis_failure_policy", return_value="degrade_local"):
        # First request succeeds locally
        allowed, retry_after = await qm.check_rate_limit("tenant_failover", test_policy)
        assert allowed is True
        assert retry_after == 0


@pytest.mark.asyncio
async def test_concurrency_permit_namespace_isolation(test_policy):
    """Releasing a Redis permit does not corrupt or decrement local in-memory concurrency permits."""
    qm = DistributedQuotaManager()
    # No redis set: local permits only
    acq1, prm_loc1, _ = await qm.acquire_concurrency_permit("tenant_failover", test_policy)
    assert acq1 is True
    assert prm_loc1.startswith("prm_local_")
    assert qm._local_concurrency["tenant_failover"] == 1

    # Attempting to release an arbitrary or stranded Redis permit on the local manager
    # must NOT decrement the active local permit count!
    fake_redis_permit = "prm_redis_0123456789abcdef"
    res = await qm.release_concurrency_permit("tenant_failover", fake_redis_permit)
    assert res is False
    # Local concurrency count remains intact at 1
    assert qm._local_concurrency["tenant_failover"] == 1

    # Releasing the legitimate local permit decrements the local count to 0
    res_loc = await qm.release_concurrency_permit("tenant_failover", prm_loc1)
    assert res_loc is True
    assert qm._local_concurrency["tenant_failover"] == 0

