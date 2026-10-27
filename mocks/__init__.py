"""
Mocks package: deterministic mock MCP servers with fault injection.
"""

from mocks.repo_metadata import MockRepoMetadataServer
from mocks.issues import MockIssuesServer

__version__ = "0.1.0"
__all__ = ["MockRepoMetadataServer", "MockIssuesServer"]
