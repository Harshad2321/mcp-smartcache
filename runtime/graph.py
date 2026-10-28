"""
LangGraph Workflow Definition for MCP SmartCache Agent Runtime.
Implements the multi-step StateGraph with deterministic plan validation,
gateway tool discovery, dependency-aware orchestration, and evidence synthesis.
"""

import time
import uuid
from typing import Any, Dict, List, Optional, TypedDict

from langgraph.graph import StateGraph, START, END

from runtime.models import (
    EvidenceItem,
    TaskPlan,
    TaskRequest,
    TaskResponse,
    TaskResult,
    TraceEvent,
    WorkflowStatus,
)
from runtime.mcp_client import MCPGatewayClient
from runtime.planner import DeterministicRepositoryPlanner
from runtime.plan_validator import validate_plan, ValidationResult
from runtime.scheduler import DependencyAwareScheduler
from runtime.synthesis import synthesize_answer


class AgentState(TypedDict, total=False):
    """Explicit typed state for LangGraph workflow execution."""
    workflow_id: str
    original_request: str
    owner: str
    repo: str
    max_concurrency: int
    workflow_timeout_seconds: float
    custom_plan: Optional[TaskPlan]

    parsed_task: Dict[str, Any]
    available_tools: Dict[str, Any]
    plan: Optional[TaskPlan]
    plan_valid: bool
    validation_error: Optional[str]
    validation_error_type: Optional[str]

    completed_tasks: Dict[str, TaskResult]
    task_results: Dict[str, TaskResult]
    failed_tasks: Dict[str, TaskResult]

    evidence: Dict[str, EvidenceItem]
    final_answer: Optional[str]
    execution_trace: List[TraceEvent]
    timing: Dict[str, Any]
    workflow_status: str
    error: Optional[str]


def build_agent_graph(mcp_client: MCPGatewayClient):
    """Construct and compile the LangGraph StateGraph workflow."""

    async def parse_request_node(state: AgentState) -> Dict[str, Any]:
        workflow_id = state.get("workflow_id") or f"wf_{uuid.uuid4().hex[:12]}"
        prompt = state.get("original_request", "")
        owner = state.get("owner", "google")
        repo = state.get("repo", "smartcache")

        trace: List[TraceEvent] = list(state.get("execution_trace", []))
        trace.append(
            TraceEvent(
                event="workflow_started",
                timestamp=time.time(),
                details={"workflow_id": workflow_id, "prompt": prompt},
            )
        )

        parsed_task = {
            "target": f"{owner}/{repo}",
            "intent": "repository_intelligence",
            "scope": ["metadata", "commits", "issues"],
        }
        trace.append(
            TraceEvent(
                event="request_parsed",
                timestamp=time.time(),
                details=parsed_task,
            )
        )

        return {
            "workflow_id": workflow_id,
            "parsed_task": parsed_task,
            "workflow_status": WorkflowStatus.PLANNING.value,
            "execution_trace": trace,
        }

    async def discover_tools_node(state: AgentState) -> Dict[str, Any]:
        trace: List[TraceEvent] = list(state.get("execution_trace", []))
        try:
            tools_list = await mcp_client.list_tools()
            available_tools = {t["name"]: t for t in tools_list}
            trace.append(
                TraceEvent(
                    event="tools_discovered",
                    timestamp=time.time(),
                    details={
                        "tool_count": len(available_tools),
                        "tools": list(available_tools.keys()),
                    },
                )
            )
            return {
                "available_tools": available_tools,
                "execution_trace": trace,
            }
        except Exception as exc:
            trace.append(
                TraceEvent(
                    event="tool_discovery_failed",
                    timestamp=time.time(),
                    details={"error": str(exc)},
                )
            )
            return {
                "available_tools": {},
                "plan_valid": False,
                "validation_error": f"Failed to discover tools through gateway: {str(exc)}",
                "validation_error_type": "gateway_unreachable",
                "error": f"Failed to discover tools through gateway: {str(exc)}",
                "workflow_status": WorkflowStatus.FAILED.value,
                "execution_trace": trace,
            }

    async def plan_tasks_node(state: AgentState) -> Dict[str, Any]:
        trace: List[TraceEvent] = list(state.get("execution_trace", []))
        available_tools = state.get("available_tools", {})

        # If already failed during discovery, do not plan
        if not state.get("available_tools") and state.get("error"):
            return {}

        planner = DeterministicRepositoryPlanner()
        req = TaskRequest(
            prompt=state.get("original_request", ""),
            owner=state.get("owner", "google"),
            repo=state.get("repo", "smartcache"),
            max_concurrency=state.get("max_concurrency", 5),
            custom_plan=state.get("custom_plan"),
        )
        plan = planner.plan(req, available_tools)

        trace.append(
            TraceEvent(
                event="plan_created",
                timestamp=time.time(),
                details={
                    "plan_id": plan.plan_id,
                    "task_count": len(plan.tasks),
                    "task_ids": [t.task_id for t in plan.tasks],
                    "max_concurrency": plan.max_concurrency,
                },
            )
        )
        return {"plan": plan, "execution_trace": trace}

    async def validate_plan_node(state: AgentState) -> Dict[str, Any]:
        trace: List[TraceEvent] = list(state.get("execution_trace", []))
        plan = state.get("plan")
        available_tools = state.get("available_tools", {})

        if not plan:
            existing_err = state.get("error")
            return {
                "plan_valid": False,
                "validation_error": existing_err or "No plan was created",
                "validation_error_type": state.get("validation_error_type") or "missing_plan",
                "error": existing_err or "No plan was created",
                "workflow_status": WorkflowStatus.FAILED.value,
            }

        validation_result: ValidationResult = validate_plan(
            plan=plan,
            available_tools=available_tools,
        )

        if not validation_result.valid:
            trace.append(
                TraceEvent(
                    event="plan_validation_failed",
                    timestamp=time.time(),
                    details={
                        "error_type": validation_result.error_type,
                        "detail": validation_result.detail,
                        "invalid_task_id": validation_result.invalid_task_id,
                    },
                )
            )
            return {
                "plan_valid": False,
                "validation_error": validation_result.detail,
                "validation_error_type": validation_result.error_type,
                "error": validation_result.detail,
                "workflow_status": WorkflowStatus.FAILED.value,
                "execution_trace": trace,
            }

        trace.append(
            TraceEvent(
                event="plan_validated",
                timestamp=time.time(),
                details={"status": "valid"},
            )
        )
        return {
            "plan_valid": True,
            "workflow_status": WorkflowStatus.VALIDATED.value,
            "execution_trace": trace,
        }

    def route_after_validation(state: AgentState) -> str:
        """Conditional routing: execute if valid; bypass tools completely if invalid."""
        if state.get("plan_valid"):
            return "execute_workflow"
        return "finalize_trace"

    async def execute_workflow_node(state: AgentState) -> Dict[str, Any]:
        trace: List[TraceEvent] = list(state.get("execution_trace", []))
        plan = state["plan"]
        max_concurrency = state.get("max_concurrency", plan.max_concurrency)
        timeout = state.get("workflow_timeout_seconds", 60.0)

        scheduler = DependencyAwareScheduler(
            mcp_client=mcp_client,
            max_concurrency=max_concurrency,
            workflow_timeout=timeout,
        )

        task_results, sched_trace, timing = await scheduler.execute_plan(plan)
        trace.extend(sched_trace)

        completed = {k: v for k, v in task_results.items() if v.status == "completed"}
        failed = {k: v for k, v in task_results.items() if v.status in ("failed", "blocked", "timeout")}

        return {
            "task_results": task_results,
            "completed_tasks": completed,
            "failed_tasks": failed,
            "timing": timing,
            "execution_trace": trace,
            "workflow_status": WorkflowStatus.EXECUTING.value,
        }

    async def evaluate_completeness_node(state: AgentState) -> Dict[str, Any]:
        trace: List[TraceEvent] = list(state.get("execution_trace", []))
        results = state.get("task_results", {})
        completed_count = sum(1 for r in results.values() if r.status == "completed")
        failed_count = sum(1 for r in results.values() if r.status in ("failed", "blocked", "timeout"))

        if completed_count > 0 and failed_count == 0:
            status = WorkflowStatus.COMPLETED.value
        elif completed_count > 0 and failed_count > 0:
            status = WorkflowStatus.PARTIAL_SUCCESS.value
        else:
            status = WorkflowStatus.FAILED.value

        trace.append(
            TraceEvent(
                event="workflow_completeness_evaluated",
                timestamp=time.time(),
                details={
                    "status": status,
                    "completed_count": completed_count,
                    "failed_count": failed_count,
                },
            )
        )
        return {
            "workflow_status": status,
            "execution_trace": trace,
        }

    async def synthesize_answer_node(state: AgentState) -> Dict[str, Any]:
        trace: List[TraceEvent] = list(state.get("execution_trace", []))
        results = state.get("task_results", {})
        owner = state.get("owner", "google")
        repo = state.get("repo", "smartcache")

        final_answer, evidence = synthesize_answer(results, owner=owner, repo=repo)
        trace.append(
            TraceEvent(
                event="answer_synthesized",
                timestamp=time.time(),
                details={"evidence_items_count": len(evidence)},
            )
        )
        return {
            "final_answer": final_answer,
            "evidence": evidence,
            "execution_trace": trace,
        }

    async def finalize_trace_node(state: AgentState) -> Dict[str, Any]:
        trace: List[TraceEvent] = list(state.get("execution_trace", []))
        status = state.get("workflow_status", WorkflowStatus.FAILED.value)
        timing = dict(state.get("timing", {}))

        start_event = next((e for e in trace if e.event == "workflow_started"), None)
        total_time_ms = (time.time() - start_event.timestamp) * 1000.0 if start_event else 0.0
        timing["total_workflow_duration_ms"] = total_time_ms

        trace.append(
            TraceEvent(
                event="workflow_completed",
                timestamp=time.time(),
                details={
                    "workflow_status": status,
                    "total_duration_ms": total_time_ms,
                    "error": state.get("error"),
                },
            )
        )
        return {
            "timing": timing,
            "execution_trace": trace,
        }

    # Graph construction
    workflow = StateGraph(AgentState)

    workflow.add_node("parse_request", parse_request_node)
    workflow.add_node("discover_tools", discover_tools_node)
    workflow.add_node("plan_tasks", plan_tasks_node)
    workflow.add_node("validate_plan", validate_plan_node)
    workflow.add_node("execute_workflow", execute_workflow_node)
    workflow.add_node("evaluate_completeness", evaluate_completeness_node)
    workflow.add_node("synthesize_answer", synthesize_answer_node)
    workflow.add_node("finalize_trace", finalize_trace_node)

    # Graph edges
    workflow.add_edge(START, "parse_request")
    workflow.add_edge("parse_request", "discover_tools")
    workflow.add_edge("discover_tools", "plan_tasks")
    workflow.add_edge("plan_tasks", "validate_plan")

    # Conditional branching: execute if valid; skip tools if invalid
    workflow.add_conditional_edges(
        "validate_plan",
        route_after_validation,
        {
            "execute_workflow": "execute_workflow",
            "finalize_trace": "finalize_trace",
        },
    )

    workflow.add_edge("execute_workflow", "evaluate_completeness")
    workflow.add_edge("evaluate_completeness", "synthesize_answer")
    workflow.add_edge("synthesize_answer", "finalize_trace")
    workflow.add_edge("finalize_trace", END)

    return workflow.compile()
