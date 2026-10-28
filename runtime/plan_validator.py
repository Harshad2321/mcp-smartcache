"""
Deterministic Task Plan Validator.
Validates plan topology, tool schemas, dependency graphs, and execution limits.
Rejects invalid plans before any tool execution occurs.
"""

from typing import Any, Dict, List, Optional, Set
import jsonschema
from pydantic import BaseModel, Field

from runtime.models import TaskPlan, TaskDefinition


class ValidationResult(BaseModel):
    """Result of plan validation."""
    valid: bool
    error_type: Optional[str] = None
    detail: Optional[str] = None
    invalid_task_id: Optional[str] = None
    extra: Dict[str, Any] = Field(default_factory=dict)


def validate_plan(
    plan: TaskPlan,
    available_tools: Dict[str, Any],
    supported_task_types: Optional[Set[str]] = None,
) -> ValidationResult:
    """
    Validate a TaskPlan deterministically against:
    1. Concurrency bounds (must be >= 1)
    2. Supported task types
    3. Duplicate task IDs
    4. Unknown tool names
    5. Self-dependencies
    6. Missing dependencies
    7. Dependency cycles (topological sort / cycle detection)
    8. Argument schemas (JSON Schema validation per tool)

    Returns ValidationResult indicating success or structured error details.
    """
    # 1. Concurrency validation
    if plan.max_concurrency < 1:
        return ValidationResult(
            valid=False,
            error_type="invalid_concurrency",
            detail=f"max_concurrency must be at least 1, received {plan.max_concurrency}",
            extra={"max_concurrency": plan.max_concurrency},
        )

    # 2. Supported task types validation
    if supported_task_types is not None and plan.task_type not in supported_task_types:
        return ValidationResult(
            valid=False,
            error_type="unsupported_task_type",
            detail=f"Task type '{plan.task_type}' is not supported. Supported types: {sorted(list(supported_task_types))}",
            extra={"task_type": plan.task_type},
        )

    # 3. Duplicate task ID check
    seen_ids: Set[str] = set()
    for task in plan.tasks:
        if task.task_id in seen_ids:
            return ValidationResult(
                valid=False,
                error_type="duplicate_task_id",
                detail=f"Duplicate task ID detected: '{task.task_id}'",
                invalid_task_id=task.task_id,
            )
        seen_ids.add(task.task_id)

    # 4. Unknown tool check
    for task in plan.tasks:
        if task.tool_name not in available_tools:
            return ValidationResult(
                valid=False,
                error_type="unknown_tool",
                detail=f"Tool '{task.tool_name}' in task '{task.task_id}' is not in the gateway registry",
                invalid_task_id=task.task_id,
                extra={"tool_name": task.tool_name, "available_tools": sorted(list(available_tools.keys()))},
            )

    # 5. Self-dependency check
    for task in plan.tasks:
        if task.task_id in task.dependencies:
            return ValidationResult(
                valid=False,
                error_type="self_dependency",
                detail=f"Task '{task.task_id}' cannot depend on itself",
                invalid_task_id=task.task_id,
            )

    # 6. Missing dependency check
    for task in plan.tasks:
        for dep in task.dependencies:
            if dep not in seen_ids:
                return ValidationResult(
                    valid=False,
                    error_type="missing_dependency",
                    detail=f"Task '{task.task_id}' references non-existent prerequisite task '{dep}'",
                    invalid_task_id=task.task_id,
                    extra={"missing_dependency": dep},
                )

    # 7. Dependency cycle detection (DFS-based)
    adjacency: Dict[str, List[str]] = {t.task_id: t.dependencies for t in plan.tasks}
    visited: Dict[str, int] = {}  # 0 = unvisited, 1 = visiting (on stack), 2 = visited

    def has_cycle(node: str, path: List[str]) -> Optional[List[str]]:
        visited[node] = 1
        path.append(node)
        for prereq in adjacency.get(node, []):
            if visited.get(prereq, 0) == 1:
                cycle_path = path[path.index(prereq):] + [prereq]
                return cycle_path
            if visited.get(prereq, 0) == 0:
                cycle = has_cycle(prereq, path)
                if cycle:
                    return cycle
        path.pop()
        visited[node] = 2
        return None

    for task_id in seen_ids:
        if visited.get(task_id, 0) == 0:
            cycle = has_cycle(task_id, [])
            if cycle:
                return ValidationResult(
                    valid=False,
                    error_type="dependency_cycle",
                    detail=f"Dependency cycle detected in plan: {' -> '.join(cycle)}",
                    extra={"cycle": cycle},
                )

    # 8. Argument JSON schema validation
    for task in plan.tasks:
        tool_meta = available_tools.get(task.tool_name, {})
        schema = tool_meta.get("input_schema")
        if schema:
            try:
                jsonschema.validate(instance=task.arguments, schema=schema)
            except jsonschema.ValidationError as err:
                return ValidationResult(
                    valid=False,
                    error_type="invalid_arguments",
                    detail=f"Arguments for task '{task.task_id}' failed schema validation: {err.message}",
                    invalid_task_id=task.task_id,
                    extra={"tool_name": task.tool_name, "schema_error": err.message},
                )

    return ValidationResult(valid=True)
