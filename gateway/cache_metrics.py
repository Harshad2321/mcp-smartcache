"""
Phase 2: Cache metrics — structured counters with bounded-cardinality labels.

Uses prometheus_client (already pinned in requirements.txt).
Labels are limited to:
  - outcome: hit | miss | write | write_skip | error
  - service: the upstream server name (e.g. "repo_metadata")
  - tool_category: the original tool name (e.g. "get_repo_info")

NEVER use cache keys, raw arguments, user IDs, tokens, or repository names
as label values — those are unbounded and would cause cardinality explosion.
"""

import logging
import time
from contextlib import contextmanager
from typing import Generator

from prometheus_client import Counter, Histogram

logger = logging.getLogger(__name__)

# ── Cache access counters ─────────────────────────────────────────────────────
cache_hits = Counter(
    "mcp_smartcache_cache_hits_total",
    "Total number of cache hits",
    ["service", "tool_category"],
)
cache_misses = Counter(
    "mcp_smartcache_cache_misses_total",
    "Total number of cache misses",
    ["service", "tool_category"],
)
cache_writes = Counter(
    "mcp_smartcache_cache_writes_total",
    "Total number of successful cache writes",
    ["service", "tool_category"],
)
cache_write_skipped = Counter(
    "mcp_smartcache_cache_write_skipped_total",
    "Cache writes skipped (e.g. oversized response)",
    ["service", "tool_category"],
)
cache_read_errors = Counter(
    "mcp_smartcache_cache_read_errors_total",
    "Cache read failures (Redis unavailable or error)",
    ["service", "tool_category"],
)
cache_write_errors = Counter(
    "mcp_smartcache_cache_write_errors_total",
    "Cache write failures (Redis unavailable or error)",
    ["service", "tool_category"],
)
cache_invalidations = Counter(
    "mcp_smartcache_cache_invalidations_total",
    "Total cache invalidation operations",
    ["scope"],  # scope: tool | tag
)

# ── Singleflight counters ─────────────────────────────────────────────────────
singleflight_leaders = Counter(
    "mcp_smartcache_singleflight_leaders_total",
    "Requests that became the singleflight leader for a key",
    ["service", "tool_category"],
)
singleflight_waiters = Counter(
    "mcp_smartcache_singleflight_waiters_total",
    "Requests that waited for an in-flight singleflight request",
    ["service", "tool_category"],
)
upstream_calls_avoided = Counter(
    "mcp_smartcache_upstream_calls_avoided_total",
    "Upstream calls avoided (cache hit or singleflight coalescing)",
    ["reason"],  # reason: cache_hit | singleflight
)
upstream_execution_failures = Counter(
    "mcp_smartcache_upstream_execution_failures_total",
    "Upstream tool execution failures",
    ["service", "tool_category"],
)

# ── Distributed Singleflight counters (Phase 5) ──────────────────────────────
distributed_leaders = Counter(
    "mcp_smartcache_distributed_leaders_total",
    "Distributed requests that acquired the Redis lease and became leader",
    ["service", "tool_category"],
)
distributed_waiters = Counter(
    "mcp_smartcache_distributed_waiters_total",
    "Distributed requests that waited for a cross-replica leader",
    ["service", "tool_category"],
)
lock_acquisition_failures = Counter(
    "mcp_smartcache_lock_acquisition_failures_total",
    "Failures to acquire distributed lease due to contention or error",
    ["service", "tool_category"],
)
lease_expirations = Counter(
    "mcp_smartcache_lease_expirations_total",
    "Distributed leases that expired before clean release",
    ["service", "tool_category"],
)
waiter_timeouts = Counter(
    "mcp_smartcache_waiter_timeouts_total",
    "Distributed waiters that timed out waiting for leader completion",
    ["service", "tool_category"],
)
coordination_fallbacks = Counter(
    "mcp_smartcache_coordination_fallbacks_total",
    "Requests falling back to process-local singleflight due to Redis outage or timeout",
    ["service", "tool_category"],
)

# ── Policy & Quota counters (Phase 6) ─────────────────────────────────────────
policy_evaluations = Counter(
    "mcp_smartcache_policy_evaluations_total",
    "Total policy evaluation attempts",
    ["decision"],  # decision: allow | deny
)
policy_denials = Counter(
    "mcp_smartcache_policy_denials_total",
    "Policy denials by rule type",
    ["reason"],  # reason: tool_denied | not_allowed | default_deny
)
quota_rejections = Counter(
    "mcp_smartcache_quota_rejections_total",
    "Quota rejections by limit type",
    ["limit_type"],  # limit_type: rate_limit | concurrency | workflow_tasks
)
payload_size_rejections = Counter(
    "mcp_smartcache_payload_size_rejections_total",
    "Payload size limit violations",
    ["direction"],  # direction: input | output
)
cache_eligibility_decisions = Counter(
    "mcp_smartcache_cache_eligibility_decisions_total",
    "Decisions regarding tool cache eligibility",
    ["decision"],  # decision: eligible | ineligible | disabled
)
policy_refresh_failures = Counter(
    "mcp_smartcache_policy_refresh_failures_total",
    "Failures during dynamic policy refresh",
    ["reason"],  # reason: db_error | validation_error | parse_error
)

# ── Latency histograms ────────────────────────────────────────────────────────
upstream_duration_seconds = Histogram(
    "mcp_smartcache_upstream_duration_seconds",
    "Duration of upstream tool executions",
    ["service", "tool_category"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0),
)
cache_lookup_duration_seconds = Histogram(
    "mcp_smartcache_cache_lookup_duration_seconds",
    "Duration of cache lookup operations",
    ["service", "tool_category"],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25),
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_labels(namespaced_name: str) -> tuple[str, str]:
    """
    Split "server__tool_name" into ("server", "tool_name").
    Falls back to ("unknown", namespaced_name) if the separator is absent.
    """
    parts = namespaced_name.split("__", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return "unknown", namespaced_name


def record_hit(namespaced_name: str) -> None:
    service, tool_cat = _parse_labels(namespaced_name)
    cache_hits.labels(service=service, tool_category=tool_cat).inc()
    upstream_calls_avoided.labels(reason="cache_hit").inc()
    logger.debug("cache_hit tool=%s", namespaced_name)


def record_miss(namespaced_name: str) -> None:
    service, tool_cat = _parse_labels(namespaced_name)
    cache_misses.labels(service=service, tool_category=tool_cat).inc()
    logger.debug("cache_miss tool=%s", namespaced_name)


def record_write(namespaced_name: str) -> None:
    service, tool_cat = _parse_labels(namespaced_name)
    cache_writes.labels(service=service, tool_category=tool_cat).inc()
    logger.debug("cache_write tool=%s", namespaced_name)


def record_write_skipped(namespaced_name: str) -> None:
    service, tool_cat = _parse_labels(namespaced_name)
    cache_write_skipped.labels(service=service, tool_category=tool_cat).inc()


def record_read_error(namespaced_name: str) -> None:
    service, tool_cat = _parse_labels(namespaced_name)
    cache_read_errors.labels(service=service, tool_category=tool_cat).inc()
    logger.warning("cache_read_error tool=%s", namespaced_name)


def record_write_error(namespaced_name: str) -> None:
    service, tool_cat = _parse_labels(namespaced_name)
    cache_write_errors.labels(service=service, tool_category=tool_cat).inc()
    logger.warning("cache_write_error tool=%s", namespaced_name)


def record_singleflight_leader(namespaced_name: str) -> None:
    service, tool_cat = _parse_labels(namespaced_name)
    singleflight_leaders.labels(service=service, tool_category=tool_cat).inc()
    logger.debug("singleflight_leader tool=%s", namespaced_name)


def record_singleflight_waiter(namespaced_name: str) -> None:
    service, tool_cat = _parse_labels(namespaced_name)
    singleflight_waiters.labels(service=service, tool_category=tool_cat).inc()
    upstream_calls_avoided.labels(reason="singleflight").inc()
    logger.debug("singleflight_waiter tool=%s", namespaced_name)


def record_distributed_leader(namespaced_name: str) -> None:
    service, tool_cat = _parse_labels(namespaced_name)
    distributed_leaders.labels(service=service, tool_category=tool_cat).inc()
    logger.debug("distributed_leader tool=%s", namespaced_name)


def record_distributed_waiter(namespaced_name: str) -> None:
    service, tool_cat = _parse_labels(namespaced_name)
    distributed_waiters.labels(service=service, tool_category=tool_cat).inc()
    logger.debug("distributed_waiter tool=%s", namespaced_name)


def record_lock_failure(namespaced_name: str) -> None:
    service, tool_cat = _parse_labels(namespaced_name)
    lock_acquisition_failures.labels(service=service, tool_category=tool_cat).inc()


def record_lease_expiration(namespaced_name: str) -> None:
    service, tool_cat = _parse_labels(namespaced_name)
    lease_expirations.labels(service=service, tool_category=tool_cat).inc()


def record_waiter_timeout(namespaced_name: str) -> None:
    service, tool_cat = _parse_labels(namespaced_name)
    waiter_timeouts.labels(service=service, tool_category=tool_cat).inc()


def record_fallback(namespaced_name: str) -> None:
    service, tool_cat = _parse_labels(namespaced_name)
    coordination_fallbacks.labels(service=service, tool_category=tool_cat).inc()
    logger.debug("coordination_fallback tool=%s", namespaced_name)


def record_upstream_failure(namespaced_name: str) -> None:
    service, tool_cat = _parse_labels(namespaced_name)
    upstream_execution_failures.labels(service=service, tool_category=tool_cat).inc()


@contextmanager
def measure_upstream(namespaced_name: str) -> Generator[None, None, None]:
    service, tool_cat = _parse_labels(namespaced_name)
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed = time.perf_counter() - start
        upstream_duration_seconds.labels(service=service, tool_category=tool_cat).observe(elapsed)


@contextmanager
def measure_cache_lookup(namespaced_name: str) -> Generator[None, None, None]:
    service, tool_cat = _parse_labels(namespaced_name)
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed = time.perf_counter() - start
        cache_lookup_duration_seconds.labels(service=service, tool_category=tool_cat).observe(elapsed)


def record_policy_eval(decision: str) -> None:
    policy_evaluations.labels(decision=decision).inc()


def record_policy_denial(reason: str) -> None:
    policy_denials.labels(reason=reason).inc()


def record_quota_rejection(limit_type: str) -> None:
    quota_rejections.labels(limit_type=limit_type).inc()


def record_payload_rejection(direction: str) -> None:
    payload_size_rejections.labels(direction=direction).inc()


def record_cache_eligibility(decision: str) -> None:
    cache_eligibility_decisions.labels(decision=decision).inc()


def record_policy_refresh_failure(reason: str) -> None:
    policy_refresh_failures.labels(reason=reason).inc()

