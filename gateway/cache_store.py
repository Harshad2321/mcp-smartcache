"""
Phase 2: Redis-backed Cache Store.

Abstraction layer between the router and raw Redis commands.
All Redis errors are caught and re-raised as CacheError so callers
can fail open to upstream without knowing Redis implementation details.

Key design:
- set(key, value, ttl): stores a serialised CallToolResult as JSON.
- get(key): returns a deserialised CallToolResult or None on miss.
- delete(key): removes a single entry.
- invalidate_tool(namespaced_name): deletes all entries for one tool via
  an index Set (safe scan_iter-free approach).
- invalidate_tag(tag): deletes all entries for a tag group.
- close(): releases the connection pool.

Graceful degradation:
- If Redis is unavailable, every operation raises CacheError.
- The router catches CacheError and falls through to upstream execution.
- A CacheError must never fabricate a tool result.
"""

import json
import logging
import os
from typing import Any, Optional

import redis.asyncio as aioredis
from redis.exceptions import RedisError

from mcp.types import CallToolResult, TextContent

from gateway.cache_key import build_tool_index_key, build_tag_index_key

logger = logging.getLogger(__name__)

# Default Redis URL — overridden by REDIS_URL environment variable.
DEFAULT_REDIS_URL = "redis://localhost:6379/0"
CACHE_CONNECT_TIMEOUT = 2.0   # seconds
CACHE_SOCKET_TIMEOUT = 2.0    # seconds per command


class CacheError(Exception):
    """Raised when the cache store encounters an unrecoverable error."""


def _get_redis_url() -> str:
    return os.environ.get("REDIS_URL", DEFAULT_REDIS_URL)


def _serialise_result(result: CallToolResult) -> str:
    """Serialise a CallToolResult to a JSON string for storage."""
    items: list[dict[str, Any]] = []
    for c in result.content:
        if isinstance(c, TextContent):
            items.append({"type": "text", "text": c.text})
        else:
            # Fallback: use pydantic model_dump if available.
            items.append(getattr(c, "model_dump", lambda: {"raw": str(c)})())
    return json.dumps(
        {
            "is_error": result.is_error,
            "content": items,
        }
    )


def _deserialise_result(raw: str) -> CallToolResult:
    """Deserialise a JSON string from Redis into a CallToolResult."""
    data = json.loads(raw)
    content_items: list[TextContent] = []
    for item in data.get("content", []):
        if item.get("type") == "text":
            content_items.append(TextContent(type="text", text=item["text"]))
        else:
            # Unknown content type — wrap as text to preserve information.
            content_items.append(TextContent(type="text", text=json.dumps(item)))
    return CallToolResult(
        is_error=data.get("is_error", False),
        content=content_items,
    )


class RedisCacheStore:
    """
    Redis-backed cache store for MCP SmartCache.

    Lifecycle:
        store = RedisCacheStore.from_env()
        await store.connect()
        ...use store...
        await store.close()

    Or use as an async context manager:
        async with RedisCacheStore.from_env() as store:
            ...
    """

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url
        self._client: Optional[aioredis.Redis] = None

    @classmethod
    def from_env(cls) -> "RedisCacheStore":
        return cls(redis_url=_get_redis_url())

    async def connect(self) -> None:
        """Create the Redis connection pool. Raises CacheError if unreachable."""
        try:
            self._client = aioredis.from_url(
                self._redis_url,
                decode_responses=True,
                socket_connect_timeout=CACHE_CONNECT_TIMEOUT,
                socket_timeout=CACHE_SOCKET_TIMEOUT,
            )
            # Validate connectivity eagerly.
            await self._client.ping()
            logger.info("Redis cache store connected: %s", self._redis_url)
        except (RedisError, OSError, Exception) as err:
            self._client = None
            raise CacheError(f"Cannot connect to Redis at {self._redis_url}: {err}") from err

    async def close(self) -> None:
        if self._client:
            try:
                await self._client.aclose()
            except Exception:
                pass
            self._client = None

    async def __aenter__(self) -> "RedisCacheStore":
        await self.connect()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    def _require_client(self) -> aioredis.Redis:
        if self._client is None:
            raise CacheError("Redis client is not connected. Call connect() first.")
        return self._client

    # ──────────────────────────────────────────────────────────
    # Core cache operations
    # ──────────────────────────────────────────────────────────

    async def get(self, key: str) -> Optional[CallToolResult]:
        """
        Return a cached result or None on miss.
        Raises CacheError on Redis failure so callers can fail open.
        """
        client = self._require_client()
        try:
            raw = await client.get(key)
        except RedisError as err:
            raise CacheError(f"Redis GET failed for key={key}: {err}") from err

        if raw is None:
            return None

        try:
            return _deserialise_result(raw)
        except (json.JSONDecodeError, KeyError, TypeError) as err:
            # Corrupted cache entry — treat as a miss rather than surfacing
            # deserialization details to callers.
            logger.warning("Cache entry deserialization failed for key=%s: %s", key, err)
            return None

    async def set(
        self,
        key: str,
        result: CallToolResult,
        ttl_seconds: int,
        tool_name: Optional[str] = None,
        tags: Optional[frozenset[str]] = None,
        max_bytes: int = 512 * 1024,
    ) -> bool:
        """
        Store a result with TTL. Returns True on success, False if oversized.
        Raises CacheError on Redis failure.

        Also maintains per-tool and per-tag index Sets for safe invalidation.
        The index Set TTL is extended to ttl_seconds * 2 to account for the
        possibility that entries outlive the per-tool index entry.
        """
        client = self._require_client()

        payload = _serialise_result(result)
        if len(payload.encode("utf-8")) > max_bytes:
            logger.info(
                "Cache write skipped (oversized): key=%s size=%d max=%d",
                key,
                len(payload),
                max_bytes,
            )
            return False

        try:
            pipe = client.pipeline(transaction=False)
            pipe.set(key, payload, ex=ttl_seconds)

            # Maintain per-tool index.
            if tool_name:
                idx_key = build_tool_index_key(tool_name)
                pipe.sadd(idx_key, key)
                pipe.expire(idx_key, ttl_seconds * 2)

            # Maintain per-tag indexes.
            for tag in (tags or frozenset()):
                tag_key = build_tag_index_key(tag)
                pipe.sadd(tag_key, key)
                pipe.expire(tag_key, ttl_seconds * 2)

            await pipe.execute()
            return True
        except RedisError as err:
            raise CacheError(f"Redis SET pipeline failed for key={key}: {err}") from err

    async def delete(self, key: str) -> None:
        """Delete a single cache entry. Raises CacheError on failure."""
        client = self._require_client()
        try:
            await client.delete(key)
        except RedisError as err:
            raise CacheError(f"Redis DELETE failed for key={key}: {err}") from err

    async def invalidate_tool(self, namespaced_tool_name: str) -> int:
        """
        Delete all cached entries for a specific tool via the index Set.
        Returns number of entries deleted.
        Raises CacheError on Redis failure.

        This is O(N) where N is the number of cached entries for this tool —
        typically small and bounded by the tool's cardinality.
        Never uses KEYS * or full-keyspace scans.
        """
        client = self._require_client()
        idx_key = build_tool_index_key(namespaced_tool_name)
        try:
            members = await client.smembers(idx_key)
            if not members:
                return 0
            pipe = client.pipeline(transaction=False)
            for member in members:
                pipe.delete(member)
            pipe.delete(idx_key)
            results = await pipe.execute()
            # results is a list of ints (DEL return values) + final DEL for idx.
            return sum(r for r in results[:-1] if isinstance(r, int))
        except RedisError as err:
            raise CacheError(
                f"Redis invalidation failed for tool={namespaced_tool_name}: {err}"
            ) from err

    async def invalidate_tag(self, tag: str) -> int:
        """
        Delete all cached entries for a tag group.
        Returns number of entries deleted.
        """
        client = self._require_client()
        tag_key = build_tag_index_key(tag)
        try:
            members = await client.smembers(tag_key)
            if not members:
                return 0
            pipe = client.pipeline(transaction=False)
            for member in members:
                pipe.delete(member)
            pipe.delete(tag_key)
            results = await pipe.execute()
            return sum(r for r in results[:-1] if isinstance(r, int))
        except RedisError as err:
            raise CacheError(f"Redis tag invalidation failed for tag={tag}: {err}") from err
