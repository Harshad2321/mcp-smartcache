"""
Deterministic Mock Upstream MCP Server: Issues.
Exposes read-only tools for issue tracking and query.
Includes configurable artificial delay, error injection, and call counters.
"""

import asyncio
from typing import Any, Dict, List, Optional
from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent
import json


class MockIssuesServer:
    """Mock Issues MCP Server with deterministic responses and fault hooks."""

    def __init__(self, name: str = "issues"):
        self.name = name
        self.artificial_delay: float = 0.0
        self.inject_error: bool = False
        self.error_message: str = "Injected upstream error from issues server"
        self.inject_malformed: bool = False
        self.call_counters: Dict[str, int] = {
            "list_issues": 0,
            "get_issue": 0,
            "get_status": 0,
        }
        self.mcp_server = self._build_mcp_server()

    @property
    def call_count(self) -> int:
        """Total calls across all tools on this mock server."""
        return sum(self.call_counters.values())

    def reset_counters(self) -> None:
        """Reset call counters."""
        for k in self.call_counters:
            self.call_counters[k] = 0

    def _build_mcp_server(self) -> MCPServer:
        server = MCPServer(name=self.name)

        @server.tool(
            name="list_issues",
            description="List issues for a repository filtered by state.",
        )
        async def list_issues(owner: str, repo: str, state: str = "open") -> str:
            self.call_counters["list_issues"] += 1
            if self.artificial_delay > 0:
                await asyncio.sleep(self.artificial_delay)
            if self.inject_error:
                raise RuntimeError(self.error_message)
            if self.inject_malformed:
                return "<<<MALFORMED_ISSUES_STREAM>>>"

            all_issues = [
                {
                    "id": 101,
                    "title": "Bug: Connection pool exhaustion under burst load",
                    "state": "open",
                    "author": "tester1",
                    "labels": ["bug", "priority-high"],
                },
                {
                    "id": 102,
                    "title": "Feature: Add Redis cluster support",
                    "state": "open",
                    "author": "architect",
                    "labels": ["enhancement"],
                },
                {
                    "id": 103,
                    "title": "Docs: Document Phase 1 routing behavior",
                    "state": "closed",
                    "author": "writer",
                    "labels": ["documentation"],
                },
            ]
            filtered = [i for i in all_issues if state == "all" or i["state"] == state]
            return json.dumps({"owner": owner, "repo": repo, "issues": filtered})

        @server.tool(
            name="get_issue",
            description="Retrieve detailed information for a single issue by ID.",
        )
        async def get_issue(owner: str, repo: str, issue_id: int) -> str:
            self.call_counters["get_issue"] += 1
            if self.artificial_delay > 0:
                await asyncio.sleep(self.artificial_delay)
            if self.inject_error:
                raise RuntimeError(self.error_message)
            if self.inject_malformed:
                return "<<<MALFORMED_ISSUE_DETAIL>>>"

            issue_data = {
                "id": issue_id,
                "owner": owner,
                "repo": repo,
                "title": f"Issue #{issue_id}",
                "body": "Deterministic issue description with reproduction steps.",
                "state": "open",
                "comments_count": 3,
            }
            return json.dumps(issue_data)

        @server.tool(
            name="get_status",
            description="Retrieve status of the issues service (intentionally named to test collision).",
        )
        async def get_status() -> str:
            self.call_counters["get_status"] += 1
            if self.artificial_delay > 0:
                await asyncio.sleep(self.artificial_delay)
            if self.inject_error:
                raise RuntimeError(self.error_message)
            return json.dumps({
                "status": "healthy",
                "service": "issues",
                "type": "issue_tracker",
            })

        return server

    def run_stdio(self) -> None:
        """Run the server synchronously over stdio."""
        self.mcp_server.run(transport="stdio")


# Module-level factory and CLI runner
default_server = MockIssuesServer()

if __name__ == "__main__":
    default_server.run_stdio()
