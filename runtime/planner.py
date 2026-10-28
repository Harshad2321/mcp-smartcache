"""
Task Planner for MCP SmartCache Agent Runtime.
Provides deterministic task planning for the Repository Intelligence Agent
and defines the extensible planner interface for future LLM integration.
"""

from abc import ABC, abstractmethod
import re
import uuid
from typing import Any, Dict, Optional

from runtime.models import TaskDefinition, TaskPlan, TaskRequest


class BasePlanner(ABC):
    """Abstract base class for agent task planners."""

    @abstractmethod
    def plan(self, request: TaskRequest, available_tools: Dict[str, Any]) -> TaskPlan:
        """Construct a structured TaskPlan from a user request and discovered tool catalog."""
        pass


class DeterministicRepositoryPlanner(BasePlanner):
    """
    Deterministic task planner for the Repository Intelligence demonstration use case.
    Constructs a dependency-aware DAG of tool invocations without external LLM dependencies.
    """

    def plan(self, request: TaskRequest, available_tools: Dict[str, Any]) -> TaskPlan:
        # If user supplied an explicit custom plan, honor it
        if request.custom_plan is not None:
            return request.custom_plan

        # Parse owner and repo from request prompt or explicit fields
        owner = request.owner or "google"
        repo = request.repo or "smartcache"

        # Simple prompt parsing for repository name if mentioned like "owner/repo" or "the X repository"
        match = re.search(r"([a-zA-Z0-9_-]+)/([a-zA-Z0-9_-]+)", request.prompt)
        if match:
            owner, repo = match.group(1), match.group(2)
        else:
            repo_match = re.search(r"([a-zA-Z0-9_-]+)\s+repo(?:sitory)?", request.prompt, re.IGNORECASE)
            if repo_match and repo_match.group(1).lower() not in ("smartcache", "the", "this"):
                repo = repo_match.group(1).lower()

        plan_id = f"plan_{uuid.uuid4().hex[:8]}"
        tasks = []

        # 1. Independent Task: Retrieve repository metadata
        if "repo_metadata__get_repo_info" in available_tools:
            tasks.append(
                TaskDefinition(
                    task_id="task_repo_info",
                    tool_name="repo_metadata__get_repo_info",
                    arguments={"owner": owner, "repo": repo},
                    dependencies=[],
                    description=f"Retrieve repository metadata for {owner}/{repo}",
                )
            )

        # 2. Independent Task: Retrieve commit logs
        if "repo_metadata__get_commit_log" in available_tools:
            tasks.append(
                TaskDefinition(
                    task_id="task_commit_log",
                    tool_name="repo_metadata__get_commit_log",
                    arguments={"owner": owner, "repo": repo, "limit": 3},
                    dependencies=[],
                    description=f"Retrieve recent commit history for {owner}/{repo}",
                )
            )

        # 3. Independent Task: Retrieve open issues
        if "issues__list_issues" in available_tools:
            tasks.append(
                TaskDefinition(
                    task_id="task_list_issues",
                    tool_name="issues__list_issues",
                    arguments={"owner": owner, "repo": repo, "state": "open"},
                    dependencies=[],
                    description=f"Retrieve open issues for {owner}/{repo}",
                )
            )

        # 4. Dependent Task: Retrieve specific issue detail after listing issues
        if "issues__get_issue" in available_tools and "issues__list_issues" in available_tools:
            tasks.append(
                TaskDefinition(
                    task_id="task_issue_detail",
                    tool_name="issues__get_issue",
                    arguments={"owner": owner, "repo": repo, "issue_id": 101},
                    dependencies=["task_list_issues"],
                    description=f"Retrieve detailed investigation for primary issue #101 on {owner}/{repo}",
                )
            )

        concurrency = request.max_concurrency or 5
        return TaskPlan(
            plan_id=plan_id,
            task_type="repository_intelligence",
            tasks=tasks,
            max_concurrency=concurrency,
            description=f"Deterministic repository intelligence investigation for {owner}/{repo}",
        )
