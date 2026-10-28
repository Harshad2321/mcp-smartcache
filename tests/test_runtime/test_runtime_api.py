"""
API tests for MCP SmartCache Agent Runtime service.
Verifies /health, /health/ready, and /tasks endpoints via httpx AsyncClient.
"""

import pytest
from httpx import AsyncClient, ASGITransport
from runtime.main import app


@pytest.mark.asyncio
async def test_runtime_health():
    """Verify runtime liveness probe."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert data["service"] == "runtime"
        assert data["version"] == "0.3.0"


@pytest.mark.asyncio
async def test_runtime_readiness():
    """Verify runtime readiness probe verifying gateway connection."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Note: during test execution, lifespan initializes mcp_client with stdio transport if http not present
        resp = await client.get("/health/ready")
        assert resp.status_code in (200, 503)


@pytest.mark.asyncio
async def test_runtime_submit_task():
    """Verify submitting a task through the REST API returns full plan, results, trace, and answer."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {
            "prompt": "Investigate the Google SmartCache repository and report findings.",
            "owner": "google",
            "repo": "smartcache",
            "max_concurrency": 4,
        }
        headers = {"X-Request-ID": "test_req_12345"}
        resp = await client.post("/tasks", json=payload, headers=headers)
        assert resp.status_code == 200
        data = resp.json()

        assert data["workflow_id"] == "test_req_12345"
        assert resp.headers.get("X-Request-ID") == "test_req_12345"
        assert data["status"] in ("completed", "partial_success")
        assert "plan" in data and data["plan"] is not None
        assert "task_results" in data
        assert "task_repo_info" in data["task_results"]
        assert "final_answer" in data and data["final_answer"] is not None
        assert "execution_trace" in data and len(data["execution_trace"]) > 0
        assert "timing" in data
