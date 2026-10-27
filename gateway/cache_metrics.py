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
