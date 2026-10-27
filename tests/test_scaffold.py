"""
Unit tests validating Phase 0 repo scaffold, module imports, and stub health checks.
"""

import pytest
from httpx import AsyncClient, ASGITransport

from gateway.main import app as gateway_app
from control_api.main import app as control_api_app
from runtime.main import app as runtime_app
from observability.telemetry import get_telemetry_status
import gateway
import runtime
import control_api
import storage
import observability
import mocks
import benchmarks


def test_packages_and_versions():
    """Verify that all Phase 0 packages exist and export a version."""
    assert gateway.__version__ == "0.1.0"
    assert runtime.__version__ == "0.1.0"
    assert control_api.__version__ == "0.1.0"
    assert storage.__version__ == "0.1.0"
    assert observability.__version__ == "0.1.0"
    assert mocks.__version__ == "0.1.0"
    assert benchmarks.__version__ == "0.1.0"


def test_telemetry_stub():
    """Verify observability telemetry stub reports Phase 0 status."""
    status = get_telemetry_status()
    assert status["phase"] == 0
    assert status["tracing_enabled"] is False
    assert status["metrics_enabled"] is False


@pytest.mark.asyncio
async def test_gateway_health():
    """Test gateway stub health endpoint."""
    transport = ASGITransport(app=gateway_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/health")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "healthy"
        assert data["service"] == "gateway"


@pytest.mark.asyncio
async def test_control_api_health():
    """Test control_api stub health endpoint."""
    transport = ASGITransport(app=control_api_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/health")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "healthy"
        assert data["service"] == "control_api"


@pytest.mark.asyncio
async def test_runtime_health():
    """Test runtime stub health endpoint."""
    transport = ASGITransport(app=runtime_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/health")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "healthy"
        assert data["service"] == "runtime"
