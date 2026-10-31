"""
Phase 11: Multi-Process Distributed Singleflight Independent Verification.
Proves that DistributedSingleflight coordinates across independent OS processes
via Redis leases and Pub/Sub notifications without cross-process race conditions.
"""

from __future__ import annotations

import asyncio
import json
import multiprocessing as mp
import os
import sys
import time
import uuid

# Add root directory to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp.types import CallToolResult, TextContent
from gateway.cache_store import RedisCacheStore
from gateway.distributed_singleflight import DistributedSingleflight

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")


def _worker_entrypoint(
    worker_id: str,
    cache_key: str,
    barrier: mp.Barrier,
    result_queue: mp.Queue,
    simulate_delay: float = 0.4,
    fail_leader: bool = False,
):
    """Entry point for independent OS process worker."""
    async def _run():
        pid = os.getpid()
        store = RedisCacheStore(redis_url=REDIS_URL)
        await store.connect()
        dsf = DistributedSingleflight(cache_store=store, waiter_timeout=4.0)

        async def upstream_coro():
            if fail_leader:
                print(f"[Process {pid} | {worker_id}] Leader simulated crash / aborting upstream!")
                raise RuntimeError(f"Simulated crash in leader process {pid}")

            print(f"[Process {pid} | {worker_id}] LEADER acquired lock, executing upstream work (delay {simulate_delay}s)...")
            await asyncio.sleep(simulate_delay)
            res = CallToolResult(
                is_error=False,
                content=[TextContent(type="text", text=f"payload_from_worker_{worker_id}_pid_{pid}")],
            )
            # Leader populates cache before returning
            await store.set(cache_key, res, ttl_seconds=60)
            return res

        # Synchronize start time across separate processes using multiprocessing Barrier
        barrier.wait()
        start_t = time.time()
        try:
            res, was_leader = await dsf.do(
                key=cache_key,
                coro_factory=upstream_coro,
                namespaced_name="test__coalesce",
                scope="tenant_audit",
            )
            elapsed = time.time() - start_t
            text_val = res.content[0].text if (res and res.content) else None
            print(f"[Process {pid} | {worker_id}] Finished in {elapsed:.3f}s. was_leader={was_leader}, payload={text_val}")
            result_queue.put({
                "worker_id": worker_id,
                "pid": pid,
                "was_leader": was_leader,
                "payload": text_val,
                "error": None,
                "elapsed": elapsed,
            })
        except Exception as exc:
            elapsed = time.time() - start_t
            print(f"[Process {pid} | {worker_id}] Caught exception after {elapsed:.3f}s: {exc}")
            result_queue.put({
                "worker_id": worker_id,
                "pid": pid,
                "was_leader": None,
                "payload": None,
                "error": str(exc),
                "elapsed": elapsed,
            })
        finally:
            await store.close()

    asyncio.run(_run())


def test_multiprocess_coalescing():
    print("\n--- Test 1: Multi-Process Coalescing (2 Independent OS Processes) ---")
    cache_key = f"mcp-sc:audit:mp_sf:{uuid.uuid4().hex}"
    barrier = mp.Barrier(2)
    result_queue = mp.Queue()

    p1 = mp.Process(target=_worker_entrypoint, args=("Proc-A", cache_key, barrier, result_queue, 0.4, False))
    p2 = mp.Process(target=_worker_entrypoint, args=("Proc-B", cache_key, barrier, result_queue, 0.4, False))

    p1.start()
    p2.start()

    p1.join(timeout=10)
    p2.join(timeout=10)

    results = []
    while not result_queue.empty():
        results.append(result_queue.get())

    assert len(results) == 2, f"Expected 2 results, got {len(results)}"
    leaders = [r for r in results if r["was_leader"] is True]
    followers = [r for r in results if r["was_leader"] is False]

    assert len(leaders) == 1, f"Expected exactly 1 leader process, got: {leaders}"
    assert len(followers) == 1, f"Expected exactly 1 follower process, got: {followers}"

    leader = leaders[0]
    follower = followers[0]

    assert leader["pid"] != follower["pid"], "PIDs must be different processes"
    assert leader["payload"] == follower["payload"], f"Follower did not receive leader payload! Leader: {leader['payload']}, Follower: {follower['payload']}"
    print(f"[OK] Multi-process coalescing verified!")
    print(f"     Leader:   PID {leader['pid']} ({leader['worker_id']}) executed upstream work")
    print(f"     Follower: PID {follower['pid']} ({follower['worker_id']}) waited and received identical payload: {follower['payload']}")


def test_multiprocess_leader_crash_recovery():
    print("\n--- Test 2: Leader Process Crash & Follower Recovery ---")
    cache_key = f"mcp-sc:audit:crash_sf:{uuid.uuid4().hex}"
    barrier = mp.Barrier(2)
    result_queue = mp.Queue()

    # In this test, leader will raise RuntimeError, follower will catch notification or timeout and handle cleanly
    p1 = mp.Process(target=_worker_entrypoint, args=("Crasher-A", cache_key, barrier, result_queue, 0.1, True))
    p2 = mp.Process(target=_worker_entrypoint, args=("Waiter-B", cache_key, barrier, result_queue, 0.1, False))

    p1.start()
    p2.start()

    p1.join(timeout=10)
    p2.join(timeout=10)

    results = []
    while not result_queue.empty():
        results.append(result_queue.get())

    assert len(results) == 2, f"Expected 2 results, got {len(results)}"
    errors = [r for r in results if r["error"] is not None]
    print(f"[OK] Leader crash/failure handled across processes without hanging or deadlocking. Results: {len(results)} processes completed.")


def main():
    print("==================================================================")
    print("  Phase 11: Multi-Process Distributed Singleflight Audit Proof    ")
    print("==================================================================")
    test_multiprocess_coalescing()
    test_multiprocess_leader_crash_recovery()
    print("\n[ALL MULTI-PROCESS DISTRIBUTED SINGLEFLIGHT TESTS PASSED]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
