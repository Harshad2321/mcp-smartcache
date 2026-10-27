"""
Phase 2 Tests — Singleflight.

Verifies the in-process Singleflight coordinator:
- Only one leader executes for the same key when N coroutines race.
- Waiters receive the leader's result.
- Exception propagates to all waiters.
- Different keys execute independently and concurrently.
- Cleanup: in-flight entry is always removed after completion or failure.
- Waiter cancellation does not kill the leader.
"""

import asyncio
import pytest

from mcp.types import CallToolResult, TextContent

from gateway.singleflight import Singleflight


def _make_result(text: str = "ok") -> CallToolResult:
    return CallToolResult(
        is_error=False,
        content=[TextContent(type="text", text=text)],
    )


class TestSingleflight:
    async def test_single_caller_executes_directly(self):
        sf = Singleflight()
        call_count = 0

        async def coro():
            nonlocal call_count
            call_count += 1
            return _make_result("single")

        result, is_leader = await sf.do("key", coro)
        assert is_leader is True
        assert result.content[0].text == "single"
        assert call_count == 1
        assert sf.in_flight_count() == 0

    async def test_concurrent_callers_same_key_deduplicated(self):
        """Only the leader executes; waiters get the same result."""
        sf = Singleflight()
        call_count = 0
        n_concurrent = 10

        async def slow_coro():
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.05)
            return _make_result(f"result-{call_count}")

        tasks = [asyncio.create_task(sf.do("key", slow_coro)) for _ in range(n_concurrent)]
        results = await asyncio.gather(*tasks)

        # Exactly one upstream call must have been made.
        assert call_count == 1, f"Expected 1 upstream call, got {call_count}"

        # All callers must get the same result.
        texts = [r.content[0].text for r, _ in results]
        assert all(t == texts[0] for t in texts), f"Results diverged: {texts}"

        # In-flight must be clean.
        assert sf.in_flight_count() == 0

    async def test_concurrent_callers_different_keys_execute_independently(self):
        """Different keys must NOT block each other."""
        sf = Singleflight()
        execution_order = []

        async def make_coro(key_name: str):
            execution_order.append(f"start-{key_name}")
            await asyncio.sleep(0.02)
            execution_order.append(f"end-{key_name}")
            return _make_result(key_name)

        tasks = [
            asyncio.create_task(sf.do(f"key-{i}", lambda k=f"key-{i}": make_coro(k)))
            for i in range(5)
        ]
        results = await asyncio.gather(*tasks)
        assert len(results) == 5
        # Each key produced its own result.
        texts = {r.content[0].text for r, _ in results}
        assert len(texts) == 5
        assert sf.in_flight_count() == 0

    async def test_leader_exception_propagates_to_waiters(self):
        """All waiters must receive the same exception as the leader raised."""
        sf = Singleflight()

        class UpstreamBoom(RuntimeError):
            pass

        async def failing_coro():
            await asyncio.sleep(0.05)
            raise UpstreamBoom("upstream exploded")

        tasks = [asyncio.create_task(sf.do("failing-key", failing_coro)) for _ in range(5)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        assert all(isinstance(r, UpstreamBoom) for r in results), (
            f"Expected all UpstreamBoom, got: {results}"
        )
        # In-flight must be cleaned up even on failure.
        assert sf.in_flight_count() == 0
        assert not sf.is_in_flight("failing-key")

    async def test_cleanup_on_success(self):
        sf = Singleflight()

        async def coro():
            return _make_result("ok")

        await sf.do("k", coro)
        assert not sf.is_in_flight("k")
        assert sf.in_flight_count() == 0

    async def test_cleanup_on_failure(self):
        sf = Singleflight()

        async def bad():
            raise ValueError("bad")

        with pytest.raises(ValueError):
            await sf.do("k", bad)

        assert not sf.is_in_flight("k")
        assert sf.in_flight_count() == 0

    async def test_sequential_calls_same_key_both_execute(self):
        """After the first call completes, the next call for the same key is a new leader."""
        sf = Singleflight()
        call_count = 0

        async def coro():
            nonlocal call_count
            call_count += 1
            return _make_result(f"call-{call_count}")

        r1, _ = await sf.do("key", coro)
        r2, _ = await sf.do("key", coro)
        assert call_count == 2
        assert r1.content[0].text == "call-1"
        assert r2.content[0].text == "call-2"

    async def test_waiter_cancellation_does_not_kill_leader(self):
        """
        Cancelling a waiter must not prevent the leader from completing.
        The cancelled waiter receives CancelledError; other waiters still get the result.
        """
        sf = Singleflight()
        leader_completed = asyncio.Event()

        async def slow_coro():
            await asyncio.sleep(0.1)
            leader_completed.set()
            return _make_result("leader_result")

        # Start leader.
        leader_task = asyncio.create_task(sf.do("cancel-key", slow_coro))
        # Brief delay so leader is registered.
        await asyncio.sleep(0.01)

        # Start a waiter, then cancel it immediately.
        waiter_task = asyncio.create_task(sf.do("cancel-key", slow_coro))
        waiter_task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await waiter_task

        # The leader must still complete.
        result, is_leader = await leader_task
        assert is_leader is True
        assert result.content[0].text == "leader_result"
        assert leader_completed.is_set()

    async def test_in_flight_count_reflects_active_requests(self):
        sf = Singleflight()
        ready = asyncio.Event()
        proceed = asyncio.Event()

        async def gated_coro():
            ready.set()
            await proceed.wait()
            return _make_result("gated")

        task = asyncio.create_task(sf.do("gated", gated_coro))
        await ready.wait()
        assert sf.in_flight_count() == 1
        assert sf.is_in_flight("gated")
        proceed.set()
        await task
        assert sf.in_flight_count() == 0
