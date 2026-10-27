"""
Deterministic Mock Upstream MCP Server: Repo Metadata.
Exposes read-only tools for repository information and commit logs.
Includes configurable artificial delay, error injection, and call counters.
"""

import asyncio
from typing import Any, Dict, List, Optional
from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent
import json


class MockRepoMetadataServer:
    """Mock Repo Metadata MCP Server with deterministic responses and fault hooks."""

    def __init__(self, name: str = "repo_metadata"):
        self.name = name
        self.artificial_delay: float = 0.0
        self.inject_error: bool = False
        self.error_message: str = "Injected upstream error from repo_metadata"
        self.inject_malformed: bool = False
        self.call_counters: Dict[str, int] = {
            "get_repo_info": 0,
            "get_commit_log": 0,
            "get_status": 0,
        }
        self.mcp_server = self._build_mcp_server()

    def reset_counters(self) -> None:
        """Reset call counters."""
        for k in self.call_counters:
            self.call_counters[k] = 0

    def _build_mcp_server(self) -> MCPServer:
        server = MCPServer(name=self.name)

        @server.tool(
            name="get_repo_info",
            description="Retrieve deterministic repository metadata including stars, forks, and default branch.",
        )
        async def get_repo_info(owner: str, repo: str) -> str:
            self.call_counters["get_repo_info"] += 1
            if self.artificial_delay > 0:
                await asyncio.sleep(self.artificial_delay)
            if self.inject_error:
                raise RuntimeError(self.error_message)
            if self.inject_malformed:
                return "<<<MALFORMED_JSON_PAYLOAD_NOT_PARSEABLE>>>"

            data = {
                "owner": owner,
                "repo": repo,
                "full_name": f"{owner}/{repo}",
                "stars": 128,
                "forks": 32,
                "default_branch": "main",
                "open_issues_count": 5,
                "archived": False,
            }
            return json.dumps(data)

        @server.tool(
            name="get_commit_log",
            description="Retrieve recent deterministic commit logs for a repository.",
        )
        async def get_commit_log(owner: str, repo: str, limit: int = 3) -> str:
            self.call_counters["get_commit_log"] += 1
            if self.artificial_delay > 0:
                await asyncio.sleep(self.artificial_delay)
            if self.inject_error:
                raise RuntimeError(self.error_message)
            if self.inject_malformed:
                return "<<<MALFORMED_JSON_PAYLOAD_NOT_PARSEABLE>>>"

            commits = [
                {"sha": "a1b2c3d", "message": "feat: initial commit", "author": "dev1"},
                {"sha": "e4f5g6h", "message": "fix: resolve deadlock", "author": "dev2"},
                {"sha": "i7j8k9l", "message": "docs: update architecture records", "author": "dev1"},
            ][:limit]
            return json.dumps({"owner": owner, "repo": repo, "commits": commits})

        @server.tool(
            name="get_status",
            description="Retrieve status of the repo metadata service (intentionally named to test collision).",
        )
        async def get_status() -> str:
            self.call_counters["get_status"] += 1
            if self.artificial_delay > 0:
                await asyncio.sleep(self.artificial_delay)
            if self.inject_error:
                raise RuntimeError(self.error_message)
            return json.dumps({
                "status": "healthy",
                "service": "repo_metadata",
                "type": "repository_indexer",
            })

        return server

    def run_stdio(self) -> None:
        """Run the server synchronously over stdio."""
        self.mcp_server.run(transport="stdio")


# Module-level factory and CLI runner
default_server = MockRepoMetadataServer()

if __name__ == "__main__":
    default_server.run_stdio()
