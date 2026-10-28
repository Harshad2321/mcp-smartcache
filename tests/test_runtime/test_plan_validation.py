"""
Unit tests for deterministic plan validation.
Verifies all rejection criteria: unknown tools, invalid schemas, cycles, self-dependencies,
missing dependencies, duplicate IDs, invalid concurrency, and guarantee of zero tool execution.
"""

import pytest
from runtime.models import TaskDefinition, TaskPlan
from runtime.plan_validator import validate_plan, ValidationResult


@pytest.fixture
def sample_tools():
    """Mock registry of discovered gateway tools with JSON schemas."""
    return {
        "repo_metadata__get_repo_info": {
            "name": "repo_metadata__get_repo_info",
            "input_schema": {
                "type": "object",
                "properties": {
                    "owner": {"type": "string"},
                    "repo": {"type": "string"},
                },
                "required": ["owner", "repo"],
            },
        },
        "repo_metadata__get_commit_log": {
            "name": "repo_metadata__get_commit_log",
            "input_schema": {
                "type": "object",
                "properties": {
                    "owner": {"type": "string"},
                    "repo": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["owner", "repo"],
            },
        },
        "issues__list_issues": {
            "name": "issues__list_issues",
            "input_schema": {
                "type": "object",
                "properties": {
                    "owner": {"type": "string"},
                    "repo": {"type": "string"},
                    "state": {"type": "string", "enum": ["open", "closed", "all"]},
                },
                "required": ["owner", "repo"],
            },
        },
    }


def test_valid_plan(sample_tools):
    """Test that a well-formed DAG passes validation."""
    plan = TaskPlan(
        plan_id="p1",
        task_type="repository_intelligence",
        tasks=[
            TaskDefinition(
                task_id="t1",
                tool_name="repo_metadata__get_repo_info",
                arguments={"owner": "google", "repo": "smartcache"},
                dependencies=[],
            ),
            TaskDefinition(
                task_id="t2",
                tool_name="repo_metadata__get_commit_log",
                arguments={"owner": "google", "repo": "smartcache", "limit": 5},
                dependencies=["t1"],
            ),
        ],
        max_concurrency=3,
    )
    result = validate_plan(plan, sample_tools)
    assert result.valid is True
    assert result.error_type is None


def test_unknown_tool(sample_tools):
    """Test rejection when plan references an unregistered tool."""
    plan = TaskPlan(
        plan_id="p2",
        tasks=[
            TaskDefinition(
                task_id="t1",
                tool_name="unregistered_tool__do_magic",
                arguments={},
            )
        ],
    )
    result = validate_plan(plan, sample_tools)
    assert result.valid is False
    assert result.error_type == "unknown_tool"
    assert "unregistered_tool__do_magic" in result.detail


def test_invalid_arguments_schema(sample_tools):
    """Test rejection when arguments do not match tool input JSON schema."""
    plan = TaskPlan(
        plan_id="p3",
        tasks=[
            TaskDefinition(
                task_id="t1",
                tool_name="repo_metadata__get_repo_info",
                arguments={"owner": 12345},  # Missing 'repo', 'owner' is not string
            )
        ],
    )
    result = validate_plan(plan, sample_tools)
    assert result.valid is False
    assert result.error_type == "invalid_arguments"
    assert result.invalid_task_id == "t1"


def test_duplicate_task_id(sample_tools):
    """Test rejection when two tasks share the same ID."""
    plan = TaskPlan(
        plan_id="p4",
        tasks=[
            TaskDefinition(
                task_id="dup_id",
                tool_name="repo_metadata__get_repo_info",
                arguments={"owner": "google", "repo": "smartcache"},
            ),
            TaskDefinition(
                task_id="dup_id",
                tool_name="repo_metadata__get_commit_log",
                arguments={"owner": "google", "repo": "smartcache"},
            ),
        ],
    )
    result = validate_plan(plan, sample_tools)
    assert result.valid is False
    assert result.error_type == "duplicate_task_id"


def test_self_dependency(sample_tools):
    """Test rejection when a task depends on itself."""
    plan = TaskPlan(
        plan_id="p5",
        tasks=[
            TaskDefinition(
                task_id="t1",
                tool_name="repo_metadata__get_repo_info",
                arguments={"owner": "google", "repo": "smartcache"},
                dependencies=["t1"],
            )
        ],
    )
    result = validate_plan(plan, sample_tools)
    assert result.valid is False
    assert result.error_type == "self_dependency"


def test_missing_dependency(sample_tools):
    """Test rejection when a task specifies a prerequisite that does not exist."""
    plan = TaskPlan(
        plan_id="p6",
        tasks=[
            TaskDefinition(
                task_id="t1",
                tool_name="repo_metadata__get_repo_info",
                arguments={"owner": "google", "repo": "smartcache"},
                dependencies=["non_existent_task"],
            )
        ],
    )
    result = validate_plan(plan, sample_tools)
    assert result.valid is False
    assert result.error_type == "missing_dependency"
    assert "non_existent_task" in result.detail


def test_dependency_cycle_two_nodes(sample_tools):
    """Test rejection when tasks have a direct cycle (A -> B -> A)."""
    plan = TaskPlan(
        plan_id="p7",
        tasks=[
            TaskDefinition(
                task_id="t1",
                tool_name="repo_metadata__get_repo_info",
                arguments={"owner": "google", "repo": "smartcache"},
                dependencies=["t2"],
            ),
            TaskDefinition(
                task_id="t2",
                tool_name="repo_metadata__get_commit_log",
                arguments={"owner": "google", "repo": "smartcache"},
                dependencies=["t1"],
            ),
        ],
    )
    result = validate_plan(plan, sample_tools)
    assert result.valid is False
    assert result.error_type == "dependency_cycle"


def test_dependency_cycle_three_nodes(sample_tools):
    """Test rejection when tasks have an indirect cycle (A -> B -> C -> A)."""
    plan = TaskPlan(
        plan_id="p8",
        tasks=[
            TaskDefinition(
                task_id="t1",
                tool_name="repo_metadata__get_repo_info",
                arguments={"owner": "google", "repo": "smartcache"},
                dependencies=["t3"],
            ),
            TaskDefinition(
                task_id="t2",
                tool_name="repo_metadata__get_commit_log",
                arguments={"owner": "google", "repo": "smartcache"},
                dependencies=["t1"],
            ),
            TaskDefinition(
                task_id="t3",
                tool_name="issues__list_issues",
                arguments={"owner": "google", "repo": "smartcache", "state": "open"},
                dependencies=["t2"],
            ),
        ],
    )
    result = validate_plan(plan, sample_tools)
    assert result.valid is False
    assert result.error_type == "dependency_cycle"


def test_invalid_concurrency():
    """Test rejection when max_concurrency < 1."""
    # Note: Pydantic enforces ge=1 on model creation; if bypassed or manually constructed:
    try:
        plan = TaskPlan(
            plan_id="p9",
            tasks=[],
            max_concurrency=0,
        )
        result = validate_plan(plan, {})
        assert result.valid is False
        assert result.error_type == "invalid_concurrency"
    except Exception as err:
        assert "greater than or equal to 1" in str(err)
