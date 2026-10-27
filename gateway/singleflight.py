"""
Phase 2: In-process Singleflight.

Ensures that when N concurrent callers issue the same eligible cache-key request,
only ONE upstream execution is in flight; all waiters receive the same result.

Design:
- Uses asyncio.Event to signal waiters without polling.
- Each in-flight entry carries: the leader task, the event, and the result/error.
- Cancelling a waiter does NOT cancel the leader or other waiters.
- If the leader fails, waiters receive the same error.
- After completion (success or failure) the in-flight entry is always removed.
- Separate keys execute concurrently; there is no global lock.

Scope limitation (documented):
    This is in-process singleflight. Multiple gateway replicas will NOT
    deduplicate across processes. Distributed singleflight (using a Redis lock
    or an external coordination service) is deferred to a later phase.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from mcp.types import CallToolResult

logger = logging.getLogger(__name__)


@dataclass
class _InFlight:
    """State for a single in-flight singleflight request."""
    event: asyncio.Event = field(default_factory=asyncio.Event)
    result: Optional[CallToolResult] = None
    exception: Optional[BaseException] = None


class Singleflight:
    """
    In-process singleflight coordinator keyed by cache key strings.

    Usage:
        sf = Singleflight()

        result, was_leader = await sf.do(key, upstream_coroutine)

    - If no in-flight request exists for `key`, this call becomes the leader:
      it executes `upstream_coroutine` and caches the result for waiters.
    - If an in-flight request already exists, this call becomes a waiter:
      it awaits the event and returns the leader's result.
    """

    def __init__(self) -> None:
        # Protected by the asyncio event loop's single-thread guarantee.
        self._in_flight: dict[str, _InFlight] = {}

    async def do(
        self,
        key: str,
        coro_factory: Any,
    ) -> tuple[CallToolResult, bool]:
        """
        Execute coro_factory() as the leader or wait for an existing leader.

        Returns:
            (result, is_leader)

        Raises:
            The same exception the leader raised, if it failed.
        """
        # --- Waiter path: an in-flight entry already exists ---
        if key in self._in_flight:
            entry = self._in_flight[key]
            try:
                await entry.event.wait()
            except asyncio.CancelledError:
                # This waiter was cancelled. The leader continues.
                # Re-raise so the waiter's caller can handle it.
                raise

            # Propagate leader result or exception to this waiter.
            if entry.exception is not None:
                raise entry.exception
            return entry.result, False  # type: ignore[return-value]

        # --- Leader path: register and execute ---
        entry = _InFlight()
        self._in_flight[key] = entry
        try:
            result: CallToolResult = await coro_factory()
            entry.result = result
            return result, True
        except BaseException as exc:
            entry.exception = exc
            raise
        finally:
            # Always clean up, always signal waiters.
            entry.event.set()
            # Remove the entry only after signalling, so late-arriving waiters
            # see the completed entry rather than starting a new leader.
            self._in_flight.pop(key, None)

    def in_flight_count(self) -> int:
        """Number of currently in-flight keys (for observability)."""
        return len(self._in_flight)

    def is_in_flight(self, key: str) -> bool:
        return key in self._in_flight
