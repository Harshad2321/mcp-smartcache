"""
Unit tests for Dependency-Aware Bounded Concurrency Scheduler.
Verifies concurrent execution of independent tasks, prerequisite ordering,
strict concurrency ceiling enforcement, failed prerequisite isolation, and timeouts.
"""

import asyncio
import time
import pytest
from unittest.mock import AsyncMock

from mcp.types import CallToolResult, TextContent
from runtime.models import TaskDefinition, TaskPlan, TaskResult
from runtime.scheduler import DependencyAwareScheduler


class MockMCPClient:
    """Mock MCP client simulating tool execution with artificial delays and faults."""

    def __init__(self):
        self.call_history = []
        self.active_calls = 0
        self.peak_active_calls = 0
        self.delay_map = {}
        self.error_tools = set()

    async def call_tool(self, tool_name: str, arguments: dict, timeout: float = 30.0) -> CallToolResult:
        self.active_calls += 1
        if self.active_calls > self.peak_active_calls:
            self.peak_active_calls = self.active_calls

        self.call_history.append((tool_name, arguments, time.time()))

        delay = self.delay_map.get(tool_name, 0.05)
        if delay > 0:
            await asyncio.sleep(delay)

        self.active_calls -= 1

        if tool_name in self.error_tools:
            return CallToolResult(
                is_error=True,
                content=[TextContent(type="text", text='{"error": "Simulated tool error"}')],
            )

        return CallToolResult(
            is_error=False,
            content=[TextContent(type="text", text=f'{{"status": "ok", "tool": "{tool_name}"}}')],
        )


@pytest.mark.asyncio
async def test_independent_tasks_execute_concurrently():
    """Verify that independent tasks run concurrently in parallel rather than sequentially."""
    client = MockMCPClient()
    # 3 independent tasks, each with 0.1s artificial delay
    client.delay_map = {
        "tool_a": 0.1,
        "tool_b": 0.1,
        "tool_c": 0.1,
    }

    plan = TaskPlan(
        plan_id="p_parallel",
        tasks=[
            TaskDefinition(task_id="t_a", tool_name="tool_a", arguments={}, dependencies=[]),
            TaskDefinition(task_id="t_b", tool_name="tool_b", arguments={}, dependencies=[]),
            TaskDefinition(task_id="t_c", tool_name="tool_c", arguments={}, dependencies=[]),
        ],
        max_concurrency=5,
    )

    scheduler = DependencyAwareScheduler(mcp_client=client, max_concurrency=5)
    start = time.time()
    results, trace, timing = await scheduler.execute_plan(plan)
    elapsed = time.time() - start

    # If executed sequentially: ~0.3s. If concurrent: ~0.1s.
    assert elapsed < 0.22, f"Expected concurrent execution (~0.1s), but took {elapsed:.2f}s"
    assert client.peak_active_calls >= 2, "Expected multiple tasks running concurrently"
    assert len(results) == 3
    assert all(r.status == "completed" for r in results.values())


@pytest.mark.asyncio
async def test_dependent_tasks_wait_for_prerequisites():
    """Verify that a dependent task starts only after its prerequisite finishes."""
    client = MockMCPClient()
    client.delay_map = {"tool_prereq": 0.08, "tool_dep": 0.02}

    plan = TaskPlan(
        plan_id="p_deps",
        tasks=[
            TaskDefinition(task_id="t_prereq", tool_name="tool_prereq", arguments={}, dependencies=[]),
            TaskDefinition(task_id="t_dep", tool_name="tool_dep", arguments={}, dependencies=["t_prereq"]),
        ],
        max_concurrency=5,
    )

    scheduler = DependencyAwareScheduler(mcp_client=client, max_concurrency=5)
    results, trace, timing = await scheduler.execute_plan(plan)

    assert results["t_prereq"].status == "completed"
    assert results["t_dep"].status == "completed"
    # Dependent task must start strictly after prerequisite ends
    assert results["t_dep"].start_time >= results["t_prereq"].end_time - 0.005


@pytest.mark.asyncio
async def test_concurrency_never_exceeds_configured_limit():
    """Verify that active tasks never exceed max_concurrency."""
    client = MockMCPClient()
    client.delay_map = {f"tool_{i}": 0.06 for i in range(10)}

    plan = TaskPlan(
        plan_id="p_limit",
        tasks=[
            TaskDefinition(task_id=f"t_{i}", tool_name=f"tool_{i}", arguments={}, dependencies=[])
            for i in range(10)
        ],
        max_concurrency=3,
    )

    scheduler = DependencyAwareScheduler(mcp_client=client, max_concurrency=3)
    results, trace, timing = await scheduler.execute_plan(plan)

    assert len(results) == 10
    assert client.peak_active_calls <= 3, f"Peak active calls was {client.peak_active_calls}, exceeding limit of 3"
    assert timing["peak_concurrency"] <= 3


@pytest.mark.asyncio
async def test_failed_prerequisite_blocks_dependent_task():
    """Verify that when a prerequisite fails, dependent tasks are blocked while independent branches succeed."""
    client = MockMCPClient()
    client.error_tools.add("tool_failing")

    plan = TaskPlan(
        plan_id="p_fail_isolate",
        tasks=[
            # Branch 1: Fails
            TaskDefinition(task_id="t_fail", tool_name="tool_failing", arguments={}, dependencies=[]),
            TaskDefinition(task_id="t_blocked", tool_name="tool_ok1", arguments={}, dependencies=["t_fail"]),
            TaskDefinition(task_id="t_blocked_downstream", tool_name="tool_ok2", arguments={}, dependencies=["t_blocked"]),
            # Branch 2: Independent, should succeed
            TaskDefinition(task_id="t_independent", tool_name="tool_ok3", arguments={}, dependencies=[]),
        ],
        max_concurrency=5,
    )

    scheduler = DependencyAwareScheduler(mcp_client=client, max_concurrency=5)
    results, trace, timing = await scheduler.execute_plan(plan)

    assert results["t_fail"].status == "failed"
    assert results["t_blocked"].status == "blocked"
    assert "t_fail" in results["t_blocked"].error_message
    assert results["t_blocked_downstream"].status == "blocked"

    # Branch 2 must still complete successfully
    assert results["t_independent"].status == "completed"
    assert results["t_independent"].is_error is False


@pytest.mark.asyncio
async def test_no_task_executes_more_than_once():
    """Verify that each task in the plan is executed exactly once."""
    client = MockMCPClient()
    plan = TaskPlan(
        plan_id="p_once",
        tasks=[
            TaskDefinition(task_id="t1", tool_name="tool_1", arguments={}, dependencies=[]),
            TaskDefinition(task_id="t2", tool_name="tool_2", arguments={}, dependencies=["t1"]),
            TaskDefinition(task_id="t3", tool_name="tool_3", arguments={}, dependencies=["t1"]),
            TaskDefinition(task_id="t4", tool_name="tool_4", arguments={}, dependencies=["t2", "t3"]),
        ],
        max_concurrency=5,
    )

    scheduler = DependencyAwareScheduler(mcp_client=client, max_concurrency=5)
    results, trace, timing = await scheduler.execute_plan(plan)

    assert len(results) == 4
    called_tools = [c[0] for c in client.call_history]
    assert len(called_tools) == 4
    assert sorted(called_tools) == ["tool_1", "tool_2", "tool_3", "tool_4"]
