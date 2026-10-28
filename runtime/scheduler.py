"""
Dependency-Aware Bounded Concurrency Task Scheduler.
Executes DAG-structured task plans through the MCP Gateway client.
Enforces prerequisite satisfaction, bounded concurrency, branch isolation, and timeout limits.
"""

import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from mcp.types import CallToolResult, TextContent
from runtime.models import TaskDefinition, TaskPlan, TaskResult, TraceEvent
from runtime.mcp_client import MCPGatewayClient, MCPClientError

logger = logging.getLogger(__name__)


class DependencyAwareScheduler:
    """
    Executes a TaskPlan respecting dependencies and bounded concurrency.
    Independent tasks are scheduled concurrently up to max_concurrency.
    Dependent tasks run only once all prerequisites have succeeded.
    Failed prerequisites block dependent tasks while independent branches continue.
    """

    def __init__(
        self,
        mcp_client: MCPGatewayClient,
        max_concurrency: int = 5,
        workflow_timeout: float = 60.0,
    ):
        self.mcp_client = mcp_client
        self.max_concurrency = max(1, max_concurrency)
        self.workflow_timeout = workflow_timeout

    async def execute_plan(
        self,
        plan: TaskPlan,
        workflow_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        initial_completed_tasks: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Dict[str, TaskResult], List[TraceEvent], Dict[str, Any]]:
        """
        Execute the plan's DAG with bounded concurrency and optional checkpoint resumption.
        Returns:
            task_results: Map of task_id -> TaskResult
            trace_events: Chronological sequence of TraceEvents
            timing: Execution statistics including durations and peak concurrency
        """
        start_time = time.time()
        trace_events: List[TraceEvent] = []
        task_results: Dict[str, TaskResult] = {}
        initial_completed_tasks = initial_completed_tasks or {}

        tasks_by_id: Dict[str, TaskDefinition] = {t.task_id: t.model_copy() for t in plan.tasks}
        remaining_deps: Dict[str, Set[str]] = {t.task_id: set(t.dependencies) for t in plan.tasks}
        dependents: Dict[str, Set[str]] = {t.task_id: set() for t in plan.tasks}
        for t in plan.tasks:
            for dep in t.dependencies:
                if dep in dependents:
                    dependents[dep].add(t.task_id)

        # Preload completed tasks from checkpoint
        for t_id, t_val in initial_completed_tasks.items():
            if isinstance(t_val, TaskResult):
                task_results[t_id] = t_val
            elif isinstance(t_val, dict):
                task_results[t_id] = TaskResult(
                    task_id=t_id,
                    tool_name=tasks_by_id[t_id].tool_name if t_id in tasks_by_id else "unknown",
                    arguments=tasks_by_id[t_id].arguments if t_id in tasks_by_id else {},
                    is_error=False,
                    content=t_val.get("result", t_val),
                    start_time=start_time,
                    end_time=start_time,
                    duration_ms=0.0,
                    evidence_id=f"evidence:{t_id}",
                    status="completed",
                )
            else:
                task_results[t_id] = TaskResult(
                    task_id=t_id,
                    tool_name=tasks_by_id[t_id].tool_name if t_id in tasks_by_id else "unknown",
                    arguments={},
                    is_error=False,
                    content={"value": str(t_val)},
                    start_time=start_time,
                    end_time=start_time,
                    duration_ms=0.0,
                    evidence_id=f"evidence:{t_id}",
                    status="completed",
                )
            trace_events.append(
                TraceEvent(
                    event="task_reused_from_checkpoint",
                    timestamp=time.time(),
                    task_id=t_id,
                    details={"reused_from_checkpoint": True},
                )
            )
            if t_id in remaining_deps:
                remaining_deps[t_id] = set()
            for dep_id in dependents.get(t_id, set()):
                if dep_id in remaining_deps:
                    remaining_deps[dep_id].discard(t_id)

        semaphore = asyncio.Semaphore(self.max_concurrency)
        active_count = 0
        peak_concurrency = 0
        active_lock = asyncio.Lock()

        # Find initial ready tasks (0 dependencies and not already completed)
        ready_queue: asyncio.Queue[TaskDefinition] = asyncio.Queue()
        for t in plan.tasks:
            if t.task_id not in initial_completed_tasks and len(remaining_deps[t.task_id]) == 0:
                ready_queue.put_nowait(t)
                trace_events.append(
                    TraceEvent(
                        event="task_scheduled",
                        timestamp=time.time(),
                        task_id=t.task_id,
                        details={"dependencies": t.dependencies},
                    )
                )

        pending_task_ids: Set[str] = set(tasks_by_id.keys()) - set(initial_completed_tasks.keys())
        running_tasks: Set[asyncio.Task] = set()

        def block_dependents(failed_task_id: str, reason: str):
            """Recursively block all dependent tasks when a prerequisite fails."""
            stack = list(dependents.get(failed_task_id, set()))
            while stack:
                curr_id = stack.pop()
                if curr_id in pending_task_ids and curr_id not in task_results:
                    pending_task_ids.remove(curr_id)
                    curr_task = tasks_by_id[curr_id]
                    task_results[curr_id] = TaskResult(
                        task_id=curr_id,
                        tool_name=curr_task.tool_name,
                        arguments=curr_task.arguments,
                        is_error=True,
                        content=None,
                        start_time=time.time(),
                        end_time=time.time(),
                        duration_ms=0.0,
                        evidence_id=f"evidence:{curr_id}",
                        status="blocked",
                        error_message=f"Prerequisite '{failed_task_id}' failed: {reason}",
                    )
                    trace_events.append(
                        TraceEvent(
                            event="task_blocked",
                            timestamp=time.time(),
                            task_id=curr_id,
                            details={"failed_prerequisite": failed_task_id, "reason": reason},
                        )
                    )
                    stack.extend(dependents.get(curr_id, set()))

        async def worker(task_def: TaskDefinition):
            nonlocal active_count, peak_concurrency
            async with semaphore:
                async with active_lock:
                    active_count += 1
                    if active_count > peak_concurrency:
                        peak_concurrency = active_count

                task_start = time.time()
                trace_events.append(
                    TraceEvent(
                        event="task_started",
                        timestamp=task_start,
                        task_id=task_def.task_id,
                        details={"tool_name": task_def.tool_name, "active_concurrency": active_count},
                    )
                )

                res: Optional[TaskResult] = None
                try:
                    call_res: CallToolResult = await self.mcp_client.call_tool(
                        tool_name=task_def.tool_name,
                        arguments=task_def.arguments,
                        timeout=task_def.timeout_seconds,
                    )
                    task_end = time.time()
                    duration_ms = (task_end - task_start) * 1000.0

                    # Parse contents
                    parsed_content = []
                    for c in call_res.content:
                        if isinstance(c, TextContent):
                            try:
                                parsed_content.append(json.loads(c.text))
                            except Exception:
                                parsed_content.append(c.text)
                        else:
                            parsed_content.append(getattr(c, "model_dump", lambda: str(c))())

                    single_content = parsed_content[0] if len(parsed_content) == 1 else parsed_content

                    res = TaskResult(
                        task_id=task_def.task_id,
                        tool_name=task_def.tool_name,
                        arguments=task_def.arguments,
                        is_error=call_res.is_error,
                        content=single_content,
                        start_time=task_start,
                        end_time=task_end,
                        duration_ms=duration_ms,
                        evidence_id=f"evidence:{task_def.task_id}",
                        status="completed" if not call_res.is_error else "failed",
                        error_message="Tool reported error" if call_res.is_error else None,
                    )

                except MCPClientError as err:
                    task_end = time.time()
                    res = TaskResult(
                        task_id=task_def.task_id,
                        tool_name=task_def.tool_name,
                        arguments=task_def.arguments,
                        is_error=True,
                        content=None,
                        start_time=task_start,
                        end_time=task_end,
                        duration_ms=(task_end - task_start) * 1000.0,
                        evidence_id=f"evidence:{task_def.task_id}",
                        status="failed" if err.error_type != "tool_timeout" else "timeout",
                        error_message=str(err),
                    )
                except Exception as err:
                    task_end = time.time()
                    res = TaskResult(
                        task_id=task_def.task_id,
                        tool_name=task_def.tool_name,
                        arguments=task_def.arguments,
                        is_error=True,
                        content=None,
                        start_time=task_start,
                        end_time=task_end,
                        duration_ms=(task_end - task_start) * 1000.0,
                        evidence_id=f"evidence:{task_def.task_id}",
                        status="failed",
                        error_message=str(err),
                    )
                finally:
                    async with active_lock:
                        active_count -= 1

                task_results[task_def.task_id] = res
                if res.is_error:
                    trace_events.append(
                        TraceEvent(
                            event="task_failed",
                            timestamp=res.end_time,
                            task_id=task_def.task_id,
                            details={"error": res.error_message, "duration_ms": res.duration_ms},
                        )
                    )
                    block_dependents(task_def.task_id, res.error_message or "Execution failed")
                else:
                    trace_events.append(
                        TraceEvent(
                            event="task_completed",
                            timestamp=res.end_time,
                            task_id=task_def.task_id,
                            details={"duration_ms": res.duration_ms},
                        )
                    )
                    # Release dependent tasks whose dependencies are now satisfied
                    for dep_id in dependents.get(task_def.task_id, set()):
                        if dep_id in remaining_deps:
                            remaining_deps[dep_id].discard(task_def.task_id)
                            if len(remaining_deps[dep_id]) == 0 and dep_id in pending_task_ids:
                                dep_task = tasks_by_id[dep_id]
                                ready_queue.put_nowait(dep_task)
                                trace_events.append(
                                    TraceEvent(
                                        event="task_scheduled",
                                        timestamp=time.time(),
                                        task_id=dep_id,
                                        details={"dependencies_cleared": True},
                                    )
                                )

                if task_def.task_id in pending_task_ids:
                    pending_task_ids.remove(task_def.task_id)

        # Loop until all pending tasks are resolved or timeout occurs
        loop_start = time.time()
        while pending_task_ids:
            if time.time() - loop_start > self.workflow_timeout:
                timeout_msg = f"Workflow timed out after {self.workflow_timeout}s"
                for remaining_id in list(pending_task_ids):
                    curr_task = tasks_by_id[remaining_id]
                    task_results[remaining_id] = TaskResult(
                        task_id=remaining_id,
                        tool_name=curr_task.tool_name,
                        arguments=curr_task.arguments,
                        is_error=True,
                        content=None,
                        start_time=time.time(),
                        end_time=time.time(),
                        duration_ms=0.0,
                        evidence_id=f"evidence:{remaining_id}",
                        status="timeout",
                        error_message=timeout_msg,
                    )
                    trace_events.append(
                        TraceEvent(
                            event="task_timeout",
                            timestamp=time.time(),
                            task_id=remaining_id,
                            details={"reason": timeout_msg},
                        )
                    )
                break

            # Launch any newly ready tasks
            while not ready_queue.empty():
                ready_task = ready_queue.get_nowait()
                t = asyncio.create_task(worker(ready_task))
                running_tasks.add(t)

            if not running_tasks:
                # No tasks are running and nothing in ready queue
                # Any remaining pending tasks must have unsatisfied dependencies
                if pending_task_ids:
                    for unreached_id in list(pending_task_ids):
                        curr_task = tasks_by_id[unreached_id]
                        task_results[unreached_id] = TaskResult(
                            task_id=unreached_id,
                            tool_name=curr_task.tool_name,
                            arguments=curr_task.arguments,
                            is_error=True,
                            content=None,
                            start_time=time.time(),
                            end_time=time.time(),
                            duration_ms=0.0,
                            evidence_id=f"evidence:{unreached_id}",
                            status="blocked",
                            error_message="Dependencies could not be resolved",
                        )
                        pending_task_ids.remove(unreached_id)
                break

            # Wait for at least one running task to complete
            done, running_tasks = await asyncio.wait(
                running_tasks,
                return_when=asyncio.FIRST_COMPLETED,
                timeout=1.0,
            )

        total_duration_ms = (time.time() - start_time) * 1000.0
        timing = {
            "total_duration_ms": total_duration_ms,
            "peak_concurrency": peak_concurrency,
            "tasks_executed": len(task_results),
            "completed_count": sum(1 for r in task_results.values() if r.status == "completed"),
            "failed_count": sum(1 for r in task_results.values() if r.status in ("failed", "timeout")),
            "blocked_count": sum(1 for r in task_results.values() if r.status == "blocked"),
        }

        return task_results, trace_events, timing
