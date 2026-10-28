"""
Phase 6: Dynamic Tenant Policy Governance Engine.
Handles policy validation, in-memory caching with Redis Pub/Sub invalidation,
tool access control evaluation, and cache eligibility decisions.
"""

import asyncio
import fnmatch
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from storage.models.entities import (
    CacheRules,
    ResourceLimits,
    TenantPolicy,
    ToolRules,
)
from storage.repository import StorageRepository, repository as default_repo
from gateway.cache_metrics import (
    record_cache_eligibility,
    record_policy_denial,
    record_policy_eval,
    record_policy_refresh_failure,
)

logger = logging.getLogger(__name__)

POLICY_CHANNEL = "mcp-sc:policy:events"

SAFE_NAME_RE = re.compile(r"^[a-zA-Z0-9_\-\.\*]+$")
UNSAFE_TOOL_MUTATION_KEYWORDS = {"delete", "remove", "drop", "create", "insert", "update", "write", "post", "mutate"}


def validate_policy_configuration(data: Dict[str, Any]) -> List[str]:
    """
    Validate a candidate tenant policy payload against platform rules and safety constraints.
    Returns a list of validation error messages. An empty list indicates a valid policy.
    """
    errors: List[str] = []

    # 1. Identifier checks
    policy_id = data.get("policy_id")
    if not policy_id or not isinstance(policy_id, str) or len(policy_id) > 64:
        errors.append("policy_id must be a non-empty string up to 64 characters")
    elif not SAFE_NAME_RE.match(policy_id):
        errors.append(f"policy_id '{policy_id}' contains invalid characters")

    tenant_id = data.get("tenant_id")
    if not tenant_id or not isinstance(tenant_id, str) or len(tenant_id) > 64:
        errors.append("tenant_id must be a non-empty string up to 64 characters")
    elif not SAFE_NAME_RE.match(tenant_id):
        errors.append(f"tenant_id '{tenant_id}' contains invalid characters")

    policy_version = data.get("policy_version", 1)
    if not isinstance(policy_version, int) or policy_version < 1:
        errors.append("policy_version must be a positive integer >= 1")

    # 2. Tool Rules checks
    tool_rules = data.get("tool_rules", {})
    if not isinstance(tool_rules, dict):
        errors.append("tool_rules must be an object")
    else:
        allowed = tool_rules.get("allowed_tools", ["*"])
        if not isinstance(allowed, list) or not all(isinstance(p, str) and SAFE_NAME_RE.match(p) for p in allowed):
            errors.append("tool_rules.allowed_tools must be a list of safe pattern strings")

        denied = tool_rules.get("denied_tools", [])
        if not isinstance(denied, list) or not all(isinstance(p, str) and SAFE_NAME_RE.match(p) for p in denied):
            errors.append("tool_rules.denied_tools must be a list of safe pattern strings")

        read_only = tool_rules.get("read_only_tools", [])
        if not isinstance(read_only, list) or not all(isinstance(p, str) and SAFE_NAME_RE.match(p) for p in read_only):
            errors.append("tool_rules.read_only_tools must be a list of safe pattern strings")

        # Ensure tools classified as read-only do not contain explicit mutation keywords without admin override
        for ro_pattern in read_only:
            lower_pattern = ro_pattern.lower()
            if any(kw in lower_pattern for kw in UNSAFE_TOOL_MUTATION_KEYWORDS):
                errors.append(f"Tool pattern '{ro_pattern}' contains mutation keyword and cannot be classified as read-only")

    # 3. Cache Rules checks
    cache_rules = data.get("cache_rules", {})
    if not isinstance(cache_rules, dict):
        errors.append("cache_rules must be an object")
    else:
        default_ttl = cache_rules.get("default_ttl_seconds", 300)
        max_ttl = cache_rules.get("max_ttl_seconds", 3600)
        if not isinstance(default_ttl, int) or default_ttl <= 0:
            errors.append("cache_rules.default_ttl_seconds must be a positive integer > 0")
        if not isinstance(max_ttl, int) or max_ttl < default_ttl:
            errors.append("cache_rules.max_ttl_seconds must be an integer >= default_ttl_seconds")
        if isinstance(max_ttl, int) and max_ttl > 86400:
            errors.append("cache_rules.max_ttl_seconds cannot exceed 86400 (24 hours)")

        tool_ttls = cache_rules.get("tool_ttls", {})
        if not isinstance(tool_ttls, dict):
            errors.append("cache_rules.tool_ttls must be a dictionary of tool patterns to integer TTLs")
        else:
            for tool_k, ttl_v in tool_ttls.items():
                if not isinstance(ttl_v, int) or ttl_v <= 0:
                    errors.append(f"cache_rules.tool_ttls['{tool_k}'] must be a positive integer > 0")
                elif isinstance(max_ttl, int) and ttl_v > max_ttl:
                    errors.append(f"cache_rules.tool_ttls['{tool_k}'] ({ttl_v}s) exceeds max_ttl_seconds ({max_ttl}s)")

        max_response_bytes = cache_rules.get("max_response_bytes", 524288)
        if not isinstance(max_response_bytes, int) or max_response_bytes < 1024 or max_response_bytes > 52428800:
            errors.append("cache_rules.max_response_bytes must be between 1024 bytes and 52428800 bytes (50MB)")

    # 4. Resource Limits checks
    limits = data.get("resource_limits", {})
    if not isinstance(limits, dict):
        errors.append("resource_limits must be an object")
    else:
        max_concurrent_upstream = limits.get("max_concurrent_upstream", 5)
        if not isinstance(max_concurrent_upstream, int) or max_concurrent_upstream < 1 or max_concurrent_upstream > 500:
            errors.append("resource_limits.max_concurrent_upstream must be between 1 and 500")

        max_concurrent_workflows = limits.get("max_concurrent_workflows", 3)
        if not isinstance(max_concurrent_workflows, int) or max_concurrent_workflows < 1 or max_concurrent_workflows > 100:
            errors.append("resource_limits.max_concurrent_workflows must be between 1 and 100")

        max_tasks = limits.get("max_tasks_per_workflow", 20)
        if not isinstance(max_tasks, int) or max_tasks < 1 or max_tasks > 1000:
            errors.append("resource_limits.max_tasks_per_workflow must be between 1 and 1000")

        rate_limit_rpm = limits.get("rate_limit_rpm", 60)
        if not isinstance(rate_limit_rpm, int) or rate_limit_rpm < 1 or rate_limit_rpm > 100000:
            errors.append("resource_limits.rate_limit_rpm must be between 1 and 100000")

        burst_limit = limits.get("burst_limit", 10)
        if not isinstance(burst_limit, int) or burst_limit < 1 or burst_limit > 1000:
            errors.append("resource_limits.burst_limit must be between 1 and 1000")

        max_in_bytes = limits.get("max_input_payload_bytes", 1048576)
        if not isinstance(max_in_bytes, int) or max_in_bytes < 1024 or max_in_bytes > 52428800:
            errors.append("resource_limits.max_input_payload_bytes must be between 1024 and 52428800 bytes (50MB)")

        max_out_bytes = limits.get("max_output_payload_bytes", 1048576)
        if not isinstance(max_out_bytes, int) or max_out_bytes < 1024 or max_out_bytes > 52428800:
            errors.append("resource_limits.max_output_payload_bytes must be between 1024 and 52428800 bytes (50MB)")

    return errors


class PolicyEngine:
    """
    Evaluates tenant runtime policies with in-memory caching and real-time invalidation.
    """

    def __init__(
        self,
        repo: Optional[StorageRepository] = None,
        redis_client: Optional[Any] = None,
    ):
        self.repo = repo or default_repo
        self.redis = redis_client
        self._cache: Dict[str, TenantPolicy] = {}
        self._lock = asyncio.Lock()
        self._listener_task: Optional[asyncio.Task] = None

    def set_redis(self, redis_client: Any) -> None:
        self.redis = redis_client

    async def get_effective_policy(self, tenant_id: str) -> TenantPolicy:
        """
        Fetch the active policy for tenant_id from local cache or PostgreSQL.
        Falls back to a safe default policy if no record exists.
        """
        async with self._lock:
            if tenant_id in self._cache:
                return self._cache[tenant_id]

        # Fetch from PostgreSQL
        policy: Optional[TenantPolicy] = None
        try:
            policy = await self.repo.get_active_policy(tenant_id)
        except Exception as e:
            logger.error("Failed to fetch active policy for tenant %s from DB: %s", tenant_id, e)
            record_policy_refresh_failure("db_error")

        if not policy:
            # Safe default fallback
            policy = TenantPolicy(
                policy_id=f"default_{tenant_id}",
                tenant_id=tenant_id,
                policy_version=1,
                is_active=True,
                description="Automatic fallback default policy",
                tool_rules=ToolRules(allowed_tools=["*"], denied_tools=[], read_only_tools=["repo_metadata__*", "issues__*"]),
                cache_rules=CacheRules(enabled=True, default_ttl_seconds=300, max_ttl_seconds=3600),
                resource_limits=ResourceLimits(),
            )

        async with self._lock:
            self._cache[tenant_id] = policy

        return policy

    def check_tool_access(self, policy: TenantPolicy, namespaced_tool: str) -> Tuple[bool, str]:
        """
        Evaluate if tool invocation is permitted for the tenant based on tool rules.
        Matching order:
        1. Explicit Denied Tools -> DENY
        2. Default Deny active:
             If in Allowed Tools -> ALLOW
             Else -> DENY
        3. Default Deny inactive:
             If Allowed Tools is specified and not ["*"]:
                 If in Allowed Tools -> ALLOW
                 Else -> DENY
             Else -> ALLOW
        """
        rules = policy.tool_rules

        # Check explicit denials
        for pattern in rules.denied_tools:
            if fnmatch.fnmatch(namespaced_tool, pattern):
                record_policy_denial("tool_denied")
                record_policy_eval("deny")
                return False, f"Tool '{namespaced_tool}' is explicitly denied by tenant policy"

        # Check allowed tools under default deny
        if rules.default_deny:
            for pattern in rules.allowed_tools:
                if fnmatch.fnmatch(namespaced_tool, pattern):
                    record_policy_eval("allow")
                    return True, "allowed"
            record_policy_denial("default_deny")
            record_policy_eval("deny")
            return False, f"Tool '{namespaced_tool}' is not in allowed_tools (default-deny active)"

        # Check allowed tools under default allow
        if rules.allowed_tools and rules.allowed_tools != ["*"]:
            for pattern in rules.allowed_tools:
                if fnmatch.fnmatch(namespaced_tool, pattern):
                    record_policy_eval("allow")
                    return True, "allowed"
            record_policy_denial("not_allowed")
            record_policy_eval("deny")
            return False, f"Tool '{namespaced_tool}' does not match allowed tool patterns"

        record_policy_eval("allow")
        return True, "allowed"

    def is_cache_eligible(self, policy: TenantPolicy, namespaced_tool: str) -> Tuple[bool, int, str]:
        """
        Determine if a tool result is eligible for caching under the tenant's policy.
        Returns (eligible, ttl_seconds, reason).
        """
        cache_rules = policy.cache_rules
        if not cache_rules.enabled:
            record_cache_eligibility("disabled")
            return False, 0, "caching_disabled_for_tenant"

        if cache_rules.bypass_cache:
            record_cache_eligibility("disabled")
            return False, 0, "cache_bypassed_by_tenant_policy"

        # Check if classified as read-only
        is_read_only = False
        for pattern in policy.tool_rules.read_only_tools:
            if fnmatch.fnmatch(namespaced_tool, pattern):
                is_read_only = True
                break

        if not is_read_only:
            record_cache_eligibility("ineligible")
            return False, 0, "tool_is_not_read_only"

        # Determine TTL
        ttl = cache_rules.default_ttl_seconds
        for tool_pat, custom_ttl in cache_rules.tool_ttls.items():
            if fnmatch.fnmatch(namespaced_tool, tool_pat):
                ttl = custom_ttl
                break

        # Bound by max_ttl_seconds
        ttl = min(ttl, cache_rules.max_ttl_seconds)
        record_cache_eligibility("eligible")
        return True, ttl, "eligible"

    async def invalidate_policy(self, tenant_id: Optional[str] = None) -> None:
        """Invalidate in-memory policy cache locally."""
        async with self._lock:
            if tenant_id:
                self._cache.pop(tenant_id, None)
                logger.info("Invalidated local policy cache for tenant: %s", tenant_id)
            else:
                self._cache.clear()
                logger.info("Cleared all local policy cache")

    async def broadcast_policy_update(self, tenant_id: str, policy_id: str) -> None:
        """Publish policy update event to Redis Pub/Sub so all gateway replicas refresh."""
        if not self.redis:
            logger.warning("Redis client not available, skipping Pub/Sub policy broadcast")
            return
        payload = json.dumps({"event": "policy_updated", "tenant_id": tenant_id, "policy_id": policy_id})
        try:
            await self.redis.publish(POLICY_CHANNEL, payload)
            logger.info("Broadcasted policy update event for tenant %s (policy %s)", tenant_id, policy_id)
        except Exception as e:
            logger.error("Failed to broadcast policy update via Redis Pub/Sub: %s", e)

    async def start_pubsub_listener(self) -> None:
        """Listen to Redis Pub/Sub for policy change events from other replicas or control API."""
        if not self.redis:
            logger.warning("No Redis client configured for policy Pub/Sub listener")
            return

        async def _listener_loop():
            try:
                pubsub = self.redis.pubsub()
                await pubsub.subscribe(POLICY_CHANNEL)
                logger.info("Subscribed to policy update channel: %s", POLICY_CHANNEL)
                async for message in pubsub.listen():
                    if message and message.get("type") == "message":
                        try:
                            data = json.loads(message["data"])
                            tenant_id = data.get("tenant_id")
                            if tenant_id:
                                await self.invalidate_policy(tenant_id)
                                logger.info("Received policy update notification for tenant: %s", tenant_id)
                        except Exception as parse_err:
                            logger.error("Failed to parse policy notification: %s", parse_err)
                            record_policy_refresh_failure("parse_error")
            except asyncio.CancelledError:
                logger.info("Policy Pub/Sub listener cancelled")
            except Exception as e:
                logger.error("Error in policy Pub/Sub listener: %s", e)
                record_policy_refresh_failure("pubsub_error")

        self._listener_task = asyncio.create_task(_listener_loop())

    async def stop(self) -> None:
        """Stop background tasks."""
        if self._listener_task and not self._listener_task.done():
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass


# Global singleton policy engine
policy_engine = PolicyEngine()
