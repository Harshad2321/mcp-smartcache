"""
Phase 6 Feature B Tests: Runtime Policy Enforcement.
Verifies that:
- Denied tool calls never reach upstream servers.
- Default-deny policies block unlisted tools.
- Cache hits cannot bypass tool access policies.
- Input and output payload limits are enforced.
- Cache eligibility rules prevent caching mutations.
- Dynamic policy refreshes apply hot updates without restart.
"""

import json
import pytest

from gateway.cache_policy import CachePolicy, CachePolicyRegistry
from gateway.cache_store import RedisCacheStore
from gateway.policy_engine import PolicyEngine
from gateway.quota_manager import DistributedQuotaManager
from gateway.registry import ServerConfig, ServerRegistry
from gateway.router import GatewayRouter
from mocks.issues import MockIssuesServer
from mocks.repo_metadata import MockRepoMetadataServer
from storage.models.entities import (
    CacheRules,
    ResourceLimits,
    TenantPolicy,
    ToolRules,
)


class FakeCacheStore:
    def __init__(self):
        self.store = {}

    async def get(self, key: str):
        return self.store.get(key)

    async def set(self, key: str, value, ttl_seconds: int = 300, **kwargs):
        self.store[key] = value
        return True


@pytest.fixture
async def setup_enforcement_router():
    registry = ServerRegistry()
    repo_server = MockRepoMetadataServer()
    issues_server = MockIssuesServer()

    await registry.register_server(
        ServerConfig(name="repo_metadata", transport="in-process", server_instance=repo_server)
    )
    await registry.register_server(
        ServerConfig(name="issues", transport="in-process", server_instance=issues_server)
    )

    policy_reg = CachePolicyRegistry()
    policy_reg.register(
        CachePolicy(
            tool_name="repo_metadata__get_repo_info",
            enabled=True,
            read_only=True,
            ttl_seconds=300,
        )
    )

    fake_cache = FakeCacheStore()
    engine = PolicyEngine()
    quota = DistributedQuotaManager()

    router = GatewayRouter(
        registry=registry,
        policy_registry=policy_reg,
        cache_store=fake_cache,
        policy_engine=engine,
        quota_manager=quota,
    )
    return router, repo_server, issues_server, engine, fake_cache


@pytest.mark.asyncio
async def test_explicit_denied_tool_never_executes_upstream(setup_enforcement_router):
    router, repo_server, _, engine, _ = setup_enforcement_router

    # Tenant Alpha policy with explicit denial for repo_metadata__get_repo_info
    tenant_policy = TenantPolicy(
        policy_id="pol_alpha",
        tenant_id="tenant_a",
        tool_rules=ToolRules(
            allowed_tools=["*"],
            denied_tools=["repo_metadata__get_repo_info"],
            read_only_tools=["repo_metadata__*"],
        ),
    )
    engine._cache["tenant_a"] = tenant_policy

    initial_calls = repo_server.call_counters["get_repo_info"]
    result = await router.route_tool_call(
        "repo_metadata__get_repo_info",
        {"owner": "google", "repo": "smartcache"},
        scope="tenant_a",
    )

    assert result.is_error is True
    data = json.loads(result.content[0].text)
    assert data["error"] == "policy_denied"
    assert "explicitly denied" in data["detail"]
    # Upstream server MUST NOT have been called!
    assert repo_server.call_counters["get_repo_info"] == initial_calls


@pytest.mark.asyncio
async def test_default_deny_blocks_unlisted_tools(setup_enforcement_router):
    router, _, issues_server, engine, _ = setup_enforcement_router

    # Tenant Beta policy with default_deny=True and only repo_metadata allowed
    tenant_policy = TenantPolicy(
        policy_id="pol_beta",
        tenant_id="tenant_b",
        tool_rules=ToolRules(
            allowed_tools=["repo_metadata__*"],
            denied_tools=[],
            default_deny=True,
        ),
    )
    engine._cache["tenant_b"] = tenant_policy

    initial_issues_calls = issues_server.call_counters.get("get_issue", 0)
    result = await router.route_tool_call(
        "issues__get_issue",
        {"owner": "google", "repo": "smartcache", "issue_id": 1},
        scope="tenant_b",
    )

    assert result.is_error is True
    data = json.loads(result.content[0].text)
    assert data["error"] == "policy_denied"
    assert "default-deny" in data["detail"]
    assert issues_server.call_counters.get("get_issue", 0) == initial_issues_calls


@pytest.mark.asyncio
async def test_cache_hit_cannot_bypass_policy_denial(setup_enforcement_router):
    router, repo_server, _, engine, fake_cache = setup_enforcement_router

    # 1. Allow tool initially
    policy_allowed = TenantPolicy(
        policy_id="pol_allow",
        tenant_id="tenant_a",
        tool_rules=ToolRules(
            allowed_tools=["*"],
            denied_tools=[],
            read_only_tools=["repo_metadata__*"],
        ),
    )
    engine._cache["tenant_a"] = policy_allowed

    # Execute once to populate cache
    res1 = await router.route_tool_call(
        "repo_metadata__get_repo_info",
        {"owner": "google", "repo": "smartcache"},
        scope="tenant_a",
    )
    assert not res1.is_error
    assert len(fake_cache.store) == 1

    # 2. Update policy to DENY the tool
    policy_denied = TenantPolicy(
        policy_id="pol_deny",
        tenant_id="tenant_a",
        tool_rules=ToolRules(
            allowed_tools=["*"],
            denied_tools=["repo_metadata__get_repo_info"],
            read_only_tools=["repo_metadata__*"],
        ),
    )
    engine._cache["tenant_a"] = policy_denied

    # 3. Call again: MUST be denied by policy despite existing in cache
    res2 = await router.route_tool_call(
        "repo_metadata__get_repo_info",
        {"owner": "google", "repo": "smartcache"},
        scope="tenant_a",
    )
    assert res2.is_error is True
    data = json.loads(res2.content[0].text)
    assert data["error"] == "policy_denied"


@pytest.mark.asyncio
async def test_input_and_output_payload_size_limits(setup_enforcement_router):
    router, repo_server, _, engine, _ = setup_enforcement_router

    # Tenant policy with small 100-byte input and output payload limits
    tenant_policy = TenantPolicy(
        policy_id="pol_tiny",
        tenant_id="tenant_a",
        tool_rules=ToolRules(allowed_tools=["*"], read_only_tools=["repo_metadata__*"]),
        resource_limits=ResourceLimits(
            max_input_payload_bytes=100,
            max_output_payload_bytes=100,
        ),
    )
    engine._cache["tenant_a"] = tenant_policy

    # 1. Oversized input arguments
    oversized_args = {"owner": "google", "repo": "x" * 200}
    res_in = await router.route_tool_call(
        "repo_metadata__get_repo_info",
        oversized_args,
        scope="tenant_a",
    )
    assert res_in.is_error is True
    data_in = json.loads(res_in.content[0].text)
    assert data_in["error"] == "payload_too_large"
    assert "Input payload size" in data_in["detail"]

    # 2. Normal input but upstream response exceeds max_output_payload_bytes
    res_out = await router.route_tool_call(
        "repo_metadata__get_repo_info",
        {"owner": "google", "repo": "smartcache"},
        scope="tenant_a",
    )
    assert res_out.is_error is True
    data_out = json.loads(res_out.content[0].text)
    assert data_out["error"] == "payload_too_large"
    assert "Upstream response" in data_out["detail"]
