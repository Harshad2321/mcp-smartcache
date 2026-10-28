"""
Gateway Router — Phase 2.
Validates incoming tool call arguments against schema,
enforces explicit cache policy, applies singleflight deduplication,
and routes to the appropriate upstream MCP server.

Phase 1 features preserved:
- Tool lookup in registry
- JSON-Schema argument validation
- Structured error results for all failure modes
- In-process and stdio upstream transports

Phase 2 additions:
- Explicit allowlist cache policy (CachePolicyRegistry)
- Deterministic cache keys (cache_key module)
- Redis-backed response cache (RedisCacheStore) with graceful degradation
- In-process singleflight deduplication (Singleflight)
- Prometheus metrics and timing

Logical sequence for an eligible tool call:
  1. Resolve tool and validate arguments.
  2. Resolve explicit cache policy.
  3. If not eligible → dispatch upstream (passthrough, same as Phase 1).
  4. Build canonical, scope-aware cache key.
  5. Cache lookup → hit → return without upstream call.
  6. Cache miss → enter singleflight for this key.
  7. Inside singleflight: re-check cache (double-check to handle race between
     leader selection and a concurrent write).
  8. Execute upstream exactly once.
  9. Validate response eligibility (no error, not oversized).
  10. Store in cache with TTL.
  11. Return result.

Single-tenant scope note:
    Phase 2 uses scope="none" — all requests share the same scope.
    Multi-tenant isolation will be wired in Phase 4 when real auth identities
    become available.  The cache key and store already have a scope field
    ready for this extension.
"""

import asyncio
import json
import logging
from typing import Any, Dict, Optional

import jsonschema
from mcp.types import CallToolResult, TextContent
from mcp.client.session import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters

from gateway.registry import ServerRegistry, RegisteredTool, ServerConfig
from gateway.cache_policy import CachePolicyRegistry, CachePolicy
from gateway.cache_store import RedisCacheStore, CacheError
from gateway.cache_key import build_cache_key
from gateway.singleflight import Singleflight
from gateway.policy_engine import PolicyEngine, policy_engine as default_policy_engine
from gateway.quota_manager import DistributedQuotaManager, quota_manager as default_quota_manager
import gateway.cache_metrics as metrics

logger = logging.getLogger(__name__)

# Scope used when no authentication is present (Phase 2 is single-tenant).
_DEFAULT_SCOPE = "none"

# Default upstream timeout in seconds. Prevents callers from hanging forever.
UPSTREAM_TIMEOUT_SECONDS: float = 30.0


def make_error_result(
    error_type: str,
    detail: str,
    extra: Optional[Dict[str, Any]] = None,
) -> CallToolResult:
    """Helper: structured JSON error in a CallToolResult."""
    payload: Dict[str, Any] = {
        "is_error": True,
        "error": error_type,
        "detail": detail,
    }
    if extra:
        payload.update(extra)
    return CallToolResult(
        is_error=True,
        content=[TextContent(type="text", text=json.dumps(payload))],
    )


class GatewayRouter:
    """
    Routes incoming MCP tool calls to registered upstream servers,
    integrating explicit cache policy and singleflight deduplication.
    """

    def __init__(
        self,
        registry: ServerRegistry,
        policy_registry: Optional[CachePolicyRegistry] = None,
        cache_store: Optional[RedisCacheStore] = None,
        singleflight: Optional[Singleflight] = None,
        upstream_timeout: float = UPSTREAM_TIMEOUT_SECONDS,
        policy_engine: Optional[PolicyEngine] = None,
        quota_manager: Optional[DistributedQuotaManager] = None,
    ) -> None:
        self.registry = registry
        self.policy_registry: CachePolicyRegistry = (
            policy_registry or CachePolicyRegistry()
        )
        self.cache_store: Optional[RedisCacheStore] = cache_store
        self.sf: Singleflight = singleflight or Singleflight()
        self.upstream_timeout = upstream_timeout
        self.policy_engine: PolicyEngine = policy_engine or default_policy_engine
        self.quota_manager: DistributedQuotaManager = quota_manager or default_quota_manager

    # ──────────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────────

    async def route_tool_call(
        self,
        namespaced_name: str,
        arguments: Optional[Dict[str, Any]] = None,
        scope: str = _DEFAULT_SCOPE,
    ) -> CallToolResult:
        """
        Validate, cache-check, optionally deduplicate, and dispatch a tool call.
        `scope` is the authorization scope identifier (opaque; never a raw secret).
        """
        arguments = arguments or {}
        tenant_id = scope if (scope and scope != "none") else "default"

        # ── Step 0: Dynamic Tenant Policy Access Control (Phase 6) ────────────
        effective_tenant_policy = await self.policy_engine.get_effective_policy(tenant_id)
        is_allowed, denial_reason = self.policy_engine.check_tool_access(
            effective_tenant_policy, namespaced_name
        )
        if not is_allowed:
            return make_error_result(
                error_type="policy_denied",
                detail=denial_reason,
                extra={"tenant_id": tenant_id, "tool": namespaced_name},
            )

        # ── Step 0.1: Input Payload Size Limit (Phase 6) ───────────────────────
        try:
            serialized_args = json.dumps(arguments)
            arg_bytes = len(serialized_args.encode("utf-8"))
        except Exception:
            arg_bytes = 0

        max_in_bytes = effective_tenant_policy.resource_limits.max_input_payload_bytes
        if arg_bytes > max_in_bytes:
            metrics.record_payload_rejection("input")
            return make_error_result(
                error_type="payload_too_large",
                detail=f"Input payload size ({arg_bytes} bytes) exceeds limit of {max_in_bytes} bytes.",
                extra={"tenant_id": tenant_id, "limit_bytes": max_in_bytes},
            )

        # ── Step 1: Tool registry lookup ──────────────────────────────────
        tool: Optional[RegisteredTool] = self.registry.get_tool(namespaced_name)
        if not tool:
            return make_error_result(
                error_type="tool_not_found",
                detail=f"Tool '{namespaced_name}' is not registered in the gateway catalog.",
                extra={"requested_tool": namespaced_name},
            )

        # ── Step 2: Argument schema validation ────────────────────────────
        if tool.input_schema and isinstance(tool.input_schema, dict):
            try:
                jsonschema.validate(instance=arguments, schema=tool.input_schema)
            except jsonschema.ValidationError as val_err:
                return make_error_result(
                    error_type="argument_validation_failed",
                    detail=f"Argument schema validation failed: {val_err.message}",
                    extra={
                        "tool": namespaced_name,
                        "failed_path": list(val_err.path),
                        "schema": tool.input_schema,
                    },
                )
            except Exception as schema_err:
                return make_error_result(
                    error_type="schema_evaluation_error",
                    detail=f"Schema validation error: {str(schema_err)}",
                )

        # ── Step 3: Server lookup ─────────────────────────────────────────
        server: Optional[ServerConfig] = self.registry.get_server(tool.server_name)
        if not server:
            return make_error_result(
                error_type="server_not_registered",
                detail=f"Upstream server '{tool.server_name}' is not configured.",
                extra={"server": tool.server_name},
            )

        # ── Step 4: Cache policy check ────────────────────────────────────
        dyn_eligible, dyn_ttl, _ = self.policy_engine.is_cache_eligible(
            effective_tenant_policy, namespaced_name
        )
        policy: CachePolicy = self.policy_registry.get(namespaced_name)
        cache_eligible = (
            dyn_eligible
            and policy.enabled
            and policy.read_only
            and self.cache_store is not None
        )

        ttl_seconds = dyn_ttl if dyn_ttl else policy.ttl_seconds
        max_response_bytes = min(
            effective_tenant_policy.cache_rules.max_response_bytes,
            policy.max_response_bytes,
        )

        if not cache_eligible:
            # Not cacheable — pure passthrough to upstream with concurrency bounds
            res = await self._dispatch_with_timeout(
                server, tool.original_name, arguments, namespaced_name,
                tenant_id=tenant_id, policy=effective_tenant_policy,
            )
            return self._check_output_payload_size(res, effective_tenant_policy, tenant_id)

        # ── Steps 5–11: Cache-aware path ──────────────────────────────────
        try:
            cache_key = build_cache_key(namespaced_name, arguments, policy, tenant_id)
        except TypeError as key_err:
            logger.warning("Cache key build failed for %s: %s", namespaced_name, key_err)
            res = await self._dispatch_with_timeout(
                server, tool.original_name, arguments, namespaced_name,
                tenant_id=tenant_id, policy=effective_tenant_policy,
            )
            return self._check_output_payload_size(res, effective_tenant_policy, tenant_id)

        # ── Step 5: Initial cache lookup ──────────────────────────────────
        try:
            with metrics.measure_cache_lookup(namespaced_name):
                cached = await self.cache_store.get(cache_key)
            if cached is not None:
                metrics.record_hit(namespaced_name)
                return cached
            metrics.record_miss(namespaced_name)
        except CacheError as cache_err:
            metrics.record_read_error(namespaced_name)
            logger.warning(
                "Cache read error for %s (failing open to upstream): %s",
                namespaced_name, cache_err,
            )
            res = await self._dispatch_with_timeout(
                server, tool.original_name, arguments, namespaced_name,
                tenant_id=tenant_id, policy=effective_tenant_policy,
            )
            return self._check_output_payload_size(res, effective_tenant_policy, tenant_id)

        # ── Steps 6–11: Singleflight + upstream ───────────────────────────
        is_leader = not self.sf.is_in_flight(cache_key)
        if is_leader:
            metrics.record_singleflight_leader(namespaced_name)
        else:
            metrics.record_singleflight_waiter(namespaced_name)

        async def _execute() -> CallToolResult:
            # Step 7: Double-check cache inside singleflight
            try:
                with metrics.measure_cache_lookup(namespaced_name):
                    cached_inner = await self.cache_store.get(cache_key)
                if cached_inner is not None:
                    metrics.record_hit(namespaced_name)
                    return cached_inner
            except CacheError:
                pass

            # Step 8: Execute upstream
            with metrics.measure_upstream(namespaced_name):
                result = await self._dispatch_with_timeout(
                    server, tool.original_name, arguments, namespaced_name,
                    tenant_id=tenant_id, policy=effective_tenant_policy,
                )

            # Validate output payload size
            checked_result = self._check_output_payload_size(result, effective_tenant_policy, tenant_id)
            if checked_result.is_error:
                if checked_result != result:
                    return checked_result
                metrics.record_upstream_failure(namespaced_name)
                return result

            # Step 10: Cache the successful eligible result
            try:
                try:
                    wrote = await self.cache_store.set(
                        cache_key,
                        checked_result,
                        ttl_seconds=ttl_seconds,
                        tool_name=namespaced_name,
                        tags=policy.invalidation_tags,
                        max_bytes=max_response_bytes,
                        scope=tenant_id,
                    )
                except TypeError:
                    wrote = await self.cache_store.set(
                        cache_key,
                        checked_result,
                        ttl_seconds=ttl_seconds,
                        tool_name=namespaced_name,
                        tags=policy.invalidation_tags,
                        max_bytes=max_response_bytes,
                    )
                if wrote:
                    metrics.record_write(namespaced_name)
                else:
                    metrics.record_write_skipped(namespaced_name)
            except CacheError as write_err:
                metrics.record_write_error(namespaced_name)
                logger.warning(
                    "Cache write error for %s (result still returned): %s",
                    namespaced_name, write_err,
                )

            return checked_result

        try:
            result, _ = await self.sf.do(cache_key, _execute)
            return result
        except Exception as exc:
            logger.exception("Singleflight execution failed for %s", namespaced_name)
            return make_error_result(
                error_type="upstream_execution_failed",
                detail=str(exc),
                extra={"server": server.name, "tool": tool.original_name},
            )

    def _check_output_payload_size(
        self, result: CallToolResult, policy: Any, tenant_id: str
    ) -> CallToolResult:
        """Enforce maximum output payload size limits."""
        max_out = policy.resource_limits.max_output_payload_bytes
        for item in result.content:
            if isinstance(item, TextContent):
                if len(item.text.encode("utf-8")) > max_out:
                    metrics.record_payload_rejection("output")
                    return make_error_result(
                        error_type="payload_too_large",
                        detail=f"Upstream response ({len(item.text.encode('utf-8'))} bytes) exceeds limit of {max_out} bytes.",
                        extra={"tenant_id": tenant_id, "limit_bytes": max_out},
                    )
        return result

    # ──────────────────────────────────────────────────────────────────────────
    # Upstream dispatch helpers
    # ──────────────────────────────────────────────────────────────────────────

    async def _dispatch_with_timeout(
        self,
        server: ServerConfig,
        original_tool_name: str,
        arguments: Dict[str, Any],
        namespaced_name: str,
        tenant_id: str = "default",
        policy: Optional[Any] = None,
    ) -> CallToolResult:
        """Dispatch with a configurable timeout and concurrency permit acquisition."""
        permit_id: Optional[str] = None
        if policy is not None:
            permit_acquired, permit_id, retry_after = await self.quota_manager.acquire_concurrency_permit(
                tenant_id, policy
            )
            if not permit_acquired:
                return make_error_result(
                    error_type="concurrency_limit_exceeded",
                    detail=(
                        f"Tenant '{tenant_id}' reached concurrent upstream limit "
                        f"({policy.resource_limits.max_concurrent_upstream}). "
                        f"Retry in {retry_after}s."
                    ),
                    extra={"tenant_id": tenant_id, "retry_after": retry_after},
                )

        try:
            return await asyncio.wait_for(
                self._dispatch(server, original_tool_name, arguments),
                timeout=self.upstream_timeout,
            )
        except asyncio.TimeoutError:
            return make_error_result(
                error_type="upstream_timeout",
                detail=(
                    f"Upstream call exceeded timeout of {self.upstream_timeout}s for "
                    f"tool '{original_tool_name}' on server '{server.name}'."
                ),
                extra={"server": server.name, "tool": original_tool_name},
            )
        except Exception as exc:
            logger.exception("Unexpected upstream failure for %s", namespaced_name)
            return make_error_result(
                error_type="upstream_execution_failed",
                detail=str(exc),
                extra={"server": server.name, "tool": original_tool_name},
            )
        finally:
            if permit_id:
                await self.quota_manager.release_concurrency_permit(tenant_id, permit_id)

    async def _dispatch(
        self,
        server: ServerConfig,
        original_tool_name: str,
        arguments: Dict[str, Any],
    ) -> CallToolResult:
        """Dispatch call to upstream transport. Identical to Phase 1."""
        if server.transport == "in-process":
            if not server.server_instance:
                return make_error_result(
                    error_type="upstream_misconfigured",
                    detail="In-process server instance is missing.",
                    extra={"server": server.name},
                )
            instance = getattr(server.server_instance, "mcp_server", server.server_instance)
            try:
                raw_result = await instance.call_tool(original_tool_name, arguments)
            except Exception as e:
                cause = getattr(e, "__cause__", None)
                detail_msg = f"{str(e)} (caused by: {str(cause)})" if cause else str(e)
                return make_error_result(
                    error_type="upstream_error",
                    detail=f"Upstream server raised exception: {detail_msg}",
                    extra={"server": server.name, "tool": original_tool_name},
                )
            return self._validate_and_normalize_result(raw_result, server.name)

        elif server.transport == "stdio":
            command = server.connection_config.get("command", "python")
            args = server.connection_config.get("args", [])
            env = server.connection_config.get("env")
            cwd = server.connection_config.get("cwd")
            try:
                params = StdioServerParameters(command=command, args=args, env=env, cwd=cwd)
                async with stdio_client(params) as (read_stream, write_stream):
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        raw_result = await session.call_tool(original_tool_name, arguments)
                        return self._validate_and_normalize_result(raw_result, server.name)
            except (FileNotFoundError, ProcessLookupError, ConnectionError) as conn_err:
                return make_error_result(
                    error_type="upstream_unreachable",
                    detail=f"Could not connect to upstream stdio server: {str(conn_err)}",
                    extra={"server": server.name, "command": command, "args": args},
                )
            except Exception as exc:
                return make_error_result(
                    error_type="upstream_error",
                    detail=f"Error during stdio execution: {str(exc)}",
                    extra={"server": server.name, "tool": original_tool_name},
                )

        else:
            return make_error_result(
                error_type="unsupported_transport",
                detail=f"Transport '{server.transport}' is not supported.",
                extra={"server": server.name},
            )

    def _validate_and_normalize_result(
        self, raw_result: Any, server_name: str
    ) -> CallToolResult:
        """Validate CallToolResult type and detect intentional malformed payloads."""
        if not isinstance(raw_result, CallToolResult):
            return make_error_result(
                error_type="upstream_malformed_response",
                detail=f"Expected CallToolResult but received {type(raw_result).__name__}",
                extra={"server": server_name},
            )
        for item in raw_result.content:
            if isinstance(item, TextContent) and item.text.startswith("<<<MALFORMED_"):
                return make_error_result(
                    error_type="upstream_malformed_response",
                    detail=f"Upstream returned malformed unparseable payload: {item.text}",
                    extra={"server": server_name},
                )
        return raw_result
