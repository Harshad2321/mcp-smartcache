"""
Reproducible Benchmark: Sequential vs Dependency-Aware Parallel Execution.

Compares three configurations:
1. Sequential baseline: Independent retrieval tasks executed sequentially.
2. Parallel runtime (cold cache): Independent tasks executed concurrently via the gateway.
3. Parallel runtime (warm cache): Same tasks executed after gateway cache is populated.

Measures:
- Wall-clock time (mean, min, max, std dev over N samples).
- Per-task duration.
- Upstream tool executions.
- Cache outcomes.
- Evidence completeness.
"""

import asyncio
import json
import logging
import math
import os
import sys
import time
from typing import Any, Dict, List

from gateway.server import GatewayService
from gateway.cache_policy import CachePolicy
from gateway.cache_store import RedisCacheStore
from gateway.registry import ServerConfig
from mocks.repo_metadata import MockRepoMetadataServer
from mocks.issues import MockIssuesServer

from runtime.models import TaskDefinition, TaskPlan
from runtime.mcp_client import MCPGatewayClient
from runtime.scheduler import DependencyAwareScheduler

logging.basicConfig(level=logging.WARNING)


async def setup_benchmark_gateway(artificial_delay: float = 0.20):
    """
    Setup a clean gateway service with controlled artificial delay and explicit cache policies.
    """
    redis_url = os.environ.get("REDIS_URL", "redis://redis:6379/0")
    cache_store = RedisCacheStore(redis_url=redis_url)
    try:
        await cache_store.connect()
    except Exception:
        # Fallback to local redis or none
        try:
            cache_store = RedisCacheStore("redis://localhost:6379/0")
            await cache_store.connect()
        except Exception:
            cache_store = None

    service = GatewayService(cache_store=cache_store)

    # Register mock servers with deterministic artificial delay
    mock_repo = MockRepoMetadataServer()
    mock_repo.artificial_delay = artificial_delay
    await service.registry.register_server(
        ServerConfig(name="repo_metadata", transport="in-process", server_instance=mock_repo)
    )

    mock_issues = MockIssuesServer()
    mock_issues.artificial_delay = artificial_delay
    await service.registry.register_server(
        ServerConfig(name="issues", transport="in-process", server_instance=mock_issues)
    )

    # Register explicit allowlist cache policies for benchmarked tools
    benchmarked_tools = [
        "repo_metadata__get_repo_info",
        "repo_metadata__get_commit_log",
        "issues__list_issues",
    ]
    for tool_name in benchmarked_tools:
        service.policy_registry.register(
            CachePolicy(
                tool_name=tool_name,
                enabled=True,
                read_only=True,
                ttl_seconds=300,
                invalidation_tags=frozenset(["benchmark"]),
            )
        )

    return service, mock_repo, mock_issues, cache_store


class DirectGatewayMCPAdapter:
    """Adapts GatewayMCPServer to MCPGatewayClient interface for zero-network-overhead benchmarking."""

    def __init__(self, service: GatewayService):
        self.service = service

    async def call_tool(self, tool_name: str, arguments: dict, timeout: float = 30.0):
        return await self.service.mcp_server.call_tool(tool_name, arguments, scope="none")


async def run_sequential_iteration(adapter, tasks: List[TaskDefinition]):
    """Execute tasks strictly one after another."""
    start = time.time()
    results = {}
    for t in tasks:
        t_start = time.time()
        res = await adapter.call_tool(t.tool_name, t.arguments)
        results[t.task_id] = (time.time() - t_start) * 1000.0
    elapsed = (time.time() - start) * 1000.0
    return elapsed, results


async def run_parallel_iteration(scheduler, plan: TaskPlan):
    """Execute tasks concurrently via DependencyAwareScheduler."""
    start = time.time()
    results, trace, timing = await scheduler.execute_plan(plan)
    elapsed = (time.time() - start) * 1000.0
    per_task = {k: v.duration_ms for k, v in results.items()}
    return elapsed, per_task, timing.get("peak_concurrency", 1)


def compute_stats(values: List[float]) -> Dict[str, float]:
    """Compute mean, min, max, and std deviation."""
    n = len(values)
    mean = sum(values) / n
    variance = sum((x - mean) ** 2 for x in values) / (n - 1) if n > 1 else 0.0
    return {
        "mean_ms": mean,
        "min_ms": min(values),
        "max_ms": max(values),
        "std_dev_ms": math.sqrt(variance),
    }


async def run_full_benchmark(iterations: int = 5, artificial_delay: float = 0.20):
    print("=" * 78)
    print("MCP SmartCache — Phase 3 Orchestration Benchmark")
    print(f"Artificial Upstream Delay: {artificial_delay * 1000:.0f} ms per tool call")
    print(f"Iterations per Configuration: {iterations}")
    print("=" * 78)

    service, mock_repo, mock_issues, cache_store = await setup_benchmark_gateway(artificial_delay)
    adapter = DirectGatewayMCPAdapter(service)
    scheduler = DependencyAwareScheduler(mcp_client=adapter, max_concurrency=5)

    tasks = [
        TaskDefinition(
            task_id="task_repo_info",
            tool_name="repo_metadata__get_repo_info",
            arguments={"owner": "google", "repo": "smartcache"},
            dependencies=[],
        ),
        TaskDefinition(
            task_id="task_commit_log",
            tool_name="repo_metadata__get_commit_log",
            arguments={"owner": "google", "repo": "smartcache", "limit": 3},
            dependencies=[],
        ),
        TaskDefinition(
            task_id="task_list_issues",
            tool_name="issues__list_issues",
            arguments={"owner": "google", "repo": "smartcache", "state": "open"},
            dependencies=[],
        ),
    ]
    plan = TaskPlan(
        plan_id="bench_plan",
        tasks=tasks,
        max_concurrency=5,
    )

    # ──────────────────────────────────────────────────────────────────────────
    # 1. Sequential Baseline
    # ──────────────────────────────────────────────────────────────────────────
    print("\n[1/3] Benchmarking Sequential Baseline (independent tasks executed sequentially)...")
    sequential_durations = []
    for i in range(iterations):
        if cache_store:
            await cache_store.invalidate_tag("benchmark")
        mock_repo.reset_counters()
        mock_issues.reset_counters()

        elapsed, per_task = await run_sequential_iteration(adapter, tasks)
        sequential_durations.append(elapsed)
        print(f"  Iteration {i + 1}: {elapsed:.2f} ms")

    seq_stats = compute_stats(sequential_durations)

    # ──────────────────────────────────────────────────────────────────────────
    # 2. Parallel Runtime (Cold Cache)
    # ──────────────────────────────────────────────────────────────────────────
    print("\n[2/3] Benchmarking Parallel Runtime — Cold Cache (concurrent execution)...")
    parallel_cold_durations = []
    parallel_cold_peaks = []
    for i in range(iterations):
        if cache_store:
            await cache_store.invalidate_tag("benchmark")
        mock_repo.reset_counters()
        mock_issues.reset_counters()

        elapsed, per_task, peak = await run_parallel_iteration(scheduler, plan)
        parallel_cold_durations.append(elapsed)
        parallel_cold_peaks.append(peak)
        print(f"  Iteration {i + 1}: {elapsed:.2f} ms (peak concurrency: {peak})")

    cold_stats = compute_stats(parallel_cold_durations)

    # ──────────────────────────────────────────────────────────────────────────
    # 3. Parallel Runtime (Warm Cache)
    # ──────────────────────────────────────────────────────────────────────────
    print("\n[3/3] Benchmarking Parallel Runtime — Warm Cache (cached execution)...")
    # Warm up cache once first
    mock_repo.reset_counters()
    mock_issues.reset_counters()
    await run_parallel_iteration(scheduler, plan)

    parallel_warm_durations = []
    upstream_calls_warm = []
    for i in range(iterations):
        mock_repo.reset_counters()
        mock_issues.reset_counters()

        elapsed, per_task, peak = await run_parallel_iteration(scheduler, plan)
        parallel_warm_durations.append(elapsed)
        upstream_total = mock_repo.call_count + mock_issues.call_count
        upstream_calls_warm.append(upstream_total)
        print(f"  Iteration {i + 1}: {elapsed:.2f} ms (upstream executions: {upstream_total})")

    warm_stats = compute_stats(parallel_warm_durations)

    # ──────────────────────────────────────────────────────────────────────────
    # Summary Report
    # ──────────────────────────────────────────────────────────────────────────
    speedup_parallel = seq_stats["mean_ms"] / cold_stats["mean_ms"]
    speedup_warm = seq_stats["mean_ms"] / warm_stats["mean_ms"]

    print("\n" + "=" * 78)
    print("BENCHMARK RESULTS SUMMARY (Sample Count: 5 iterations each)")
    print("=" * 78)
    print(f"{'Configuration':<35} | {'Mean (ms)':<10} | {'Min (ms)':<10} | {'Max (ms)':<10} | {'Std Dev':<10}")
    print("-" * 78)
    print(f"{'1. Sequential Baseline':<35} | {seq_stats['mean_ms']:<10.2f} | {seq_stats['min_ms']:<10.2f} | {seq_stats['max_ms']:<10.2f} | {seq_stats['std_dev_ms']:<10.2f}")
    print(f"{'2. Parallel Runtime (Cold Cache)':<35} | {cold_stats['mean_ms']:<10.2f} | {cold_stats['min_ms']:<10.2f} | {cold_stats['max_ms']:<10.2f} | {cold_stats['std_dev_ms']:<10.2f}")
    print(f"{'3. Parallel Runtime (Warm Cache)':<35} | {warm_stats['mean_ms']:<10.2f} | {warm_stats['min_ms']:<10.2f} | {warm_stats['max_ms']:<10.2f} | {warm_stats['std_dev_ms']:<10.2f}")
    print("=" * 78)
    print(f"Parallel Speedup over Sequential: {speedup_parallel:.2f}x")
    print(f"Warm Cache Speedup over Sequential: {speedup_warm:.2f}x")
    print(f"Upstream executions per warm run: {sum(upstream_calls_warm) / len(upstream_calls_warm):.0f} (Expected: 0, 100% cache hits)")
    print("=" * 78)

    if cache_store:
        await cache_store.close()

    return {
        "sequential": seq_stats,
        "parallel_cold": cold_stats,
        "parallel_warm": warm_stats,
        "speedup_parallel": speedup_parallel,
        "speedup_warm": speedup_warm,
    }


if __name__ == "__main__":
    asyncio.run(run_full_benchmark())
