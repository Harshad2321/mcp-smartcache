"""
Event Publisher and Live Streaming (SSE) for MCP SmartCache Workflows.
Combines PostgreSQL persistence for durable historical replay with Redis Pub/Sub
and in-process queues for real-time live event streaming.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, Optional, Set
import redis.asyncio as aioredis
from redis.exceptions import RedisError

from storage.database import db
from storage.models.entities import TraceEvent, WorkflowStatus
from storage.repository import repository

logger = logging.getLogger(__name__)

DEFAULT_REDIS_URL = "redis://localhost:6379/0"


def _format_timestamp(ts: Any) -> str:
    if hasattr(ts, "isoformat"):
        return ts.isoformat()
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
    except Exception:
        return str(ts)


class ProcessLocalBroadcaster:
    """In-memory event distribution queue per workflow."""

    def __init__(self):
        self._listeners: Dict[str, Set[asyncio.Queue]] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self, workflow_id: str) -> asyncio.Queue:
        async with self._lock:
            if workflow_id not in self._listeners:
                self._listeners[workflow_id] = set()
            q: asyncio.Queue = asyncio.Queue(maxsize=200)
            self._listeners[workflow_id].add(q)
            return q

    async def unsubscribe(self, workflow_id: str, q: asyncio.Queue) -> None:
        async with self._lock:
            if workflow_id in self._listeners:
                self._listeners[workflow_id].discard(q)
                if not self._listeners[workflow_id]:
                    del self._listeners[workflow_id]

    async def broadcast(self, workflow_id: str, event: TraceEvent) -> None:
        async with self._lock:
            queues = list(self._listeners.get(workflow_id, []))
        for q in queues:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("Event queue full for subscriber on workflow %s", workflow_id)


class WorkflowEventHub:
    """
    Coordinates trace event persistence in PostgreSQL and distribution via
    Redis Pub/Sub and in-memory broadcaster.
    """

    def __init__(self, redis_url: Optional[str] = None):
        self.redis_url = redis_url or os.environ.get("REDIS_URL", DEFAULT_REDIS_URL)
        self.local_broadcaster = ProcessLocalBroadcaster()
        self._redis_client: Optional[aioredis.Redis] = None
        self._lock = asyncio.Lock()

    async def _get_redis(self) -> Optional[aioredis.Redis]:
        async with self._lock:
            if self._redis_client is None:
                try:
                    self._redis_client = aioredis.from_url(
                        self.redis_url,
                        decode_responses=True,
                        socket_connect_timeout=2.0,
                    )
                    await self._redis_client.ping()
                except Exception as err:
                    logger.debug("Redis pubsub disabled: %s", err)
                    self._redis_client = None
            return self._redis_client

    async def publish_event(self, event: TraceEvent) -> TraceEvent:
        """
        Durable workflow event publication:
        1. Persist to PostgreSQL (generates unique event.id).
        2. Dispatch to in-process subscribers.
        3. Publish to Redis Pub/Sub channel for cross-process distribution.
        """
        # 1. PostgreSQL Persistence
        saved_event = await repository.record_trace_event(event)

        # 2. Local In-Memory Broadcast
        await self.local_broadcaster.broadcast(event.workflow_id, saved_event)

        # 3. Redis Pub/Sub Broadcast
        client = await self._get_redis()
        if client:
            channel = f"mcp-sc:events:{event.workflow_id}"
            try:
                payload = json.dumps(
                    {
                        "id": saved_event.id,
                        "event_id": saved_event.event_id,
                        "workflow_id": saved_event.workflow_id,
                        "tenant_id": saved_event.tenant_id,
                        "correlation_id": saved_event.correlation_id,
                        "event_type": saved_event.event_type,
                        "task_id": saved_event.task_id,
                        "timestamp": _format_timestamp(saved_event.timestamp),
                        "details": saved_event.details,
                    }
                )
                await client.publish(channel, payload)
            except Exception as err:
                logger.warning("Redis event publish failed for workflow %s: %s", event.workflow_id, err)

        return saved_event

    async def stream_events(
        self,
        workflow_id: str,
        tenant_id: str,
        last_event_id: Optional[int] = None,
    ) -> AsyncGenerator[str, None]:
        """
        Server-Sent Events (SSE) stream generator:
        - Validates tenant authorization.
        - Replays historical events from PostgreSQL starting from last_event_id.
        - Streams live events as they occur.
        - Emits keep-alive comments.
        - Cleans up subscriptions on client disconnect.
        """
        # Authorization check: verify workflow exists and belongs to this tenant
        wf = await repository.get_workflow(workflow_id, tenant_id=tenant_id)
        if not wf:
            yield "event: error\ndata: {\"error\": \"Workflow not found or access denied\"}\n\n"
            return

        highest_event_id = last_event_id or 0

        # Step 1: Replay historical events from PostgreSQL
        historical = await repository.list_trace_events(
            workflow_id=workflow_id,
            tenant_id=tenant_id,
            since_id=last_event_id,
            limit=500,
        )

        for evt in historical:
            if evt.id and evt.id > highest_event_id:
                highest_event_id = evt.id
            yield self._format_sse(evt)

        # Check if already completed and no more events expected
        wf_current = await repository.get_workflow(workflow_id, tenant_id=tenant_id)
        if wf_current and wf_current.status in {
            WorkflowStatus.COMPLETED,
            WorkflowStatus.FAILED,
            WorkflowStatus.CANCELLED,
            WorkflowStatus.INTERRUPTED,
        }:
            yield f"event: workflow_end\ndata: {json.dumps({'status': wf_current.status.value, 'final_answer': wf_current.final_answer})}\n\n"
            return

        # Step 2: Stream live events via local broadcaster & Redis pubsub
        local_q = await self.local_broadcaster.subscribe(workflow_id)

        redis_client = await self._get_redis()
        pubsub = None
        if redis_client:
            try:
                pubsub = redis_client.pubsub()
                await pubsub.subscribe(f"mcp-sc:events:{workflow_id}")
            except Exception:
                pubsub = None

        try:
            while True:
                # Fetch next event with timeout for keep-alive
                try:
                    # Check local queue first
                    try:
                        event = await asyncio.wait_for(local_q.get(), timeout=1.0)
                        if event.id and event.id > highest_event_id:
                            highest_event_id = event.id
                            yield self._format_sse(event)
                            if event.event_type in ("workflow.completed", "workflow.failed", "workflow.cancelled", "workflow.interrupted"):
                                yield f"event: workflow_end\ndata: {json.dumps({'event_type': event.event_type})}\n\n"
                                break
                        continue
                    except asyncio.TimeoutError:
                        pass

                    # Check redis pubsub if available
                    if pubsub:
                        try:
                            msg = await asyncio.wait_for(pubsub.get_message(ignore_subscribe_messages=True), timeout=1.0)
                            if msg and msg.get("data"):
                                data = json.loads(msg["data"])
                                evt_id = data.get("id")
                                if evt_id and evt_id > highest_event_id:
                                    highest_event_id = evt_id
                                    yield f"id: {evt_id}\nevent: {data.get('event_type')}\ndata: {msg['data']}\n\n"
                                    if data.get("event_type") in ("workflow.completed", "workflow.failed", "workflow.cancelled", "workflow.interrupted"):
                                        yield f"event: workflow_end\ndata: {json.dumps({'event_type': data.get('event_type')})}\n\n"
                                        break
                        except asyncio.TimeoutError:
                            pass

                    # Periodic DB check for completion
                    wf_check = await repository.get_workflow(workflow_id, tenant_id=tenant_id)
                    if wf_check and wf_check.status in {
                        WorkflowStatus.COMPLETED,
                        WorkflowStatus.FAILED,
                        WorkflowStatus.CANCELLED,
                        WorkflowStatus.INTERRUPTED,
                    }:
                        # Drain any final historical events
                        final_events = await repository.list_trace_events(
                            workflow_id=workflow_id,
                            tenant_id=tenant_id,
                            since_id=highest_event_id,
                            limit=50,
                        )
                        for evt in final_events:
                            if evt.id and evt.id > highest_event_id:
                                highest_event_id = evt.id
                                yield self._format_sse(evt)

                        yield f"event: workflow_end\ndata: {json.dumps({'status': wf_check.status.value, 'final_answer': wf_check.final_answer})}\n\n"
                        break

                    # Keepalive comment
                    yield ": keep-alive\n\n"

                except asyncio.CancelledError:
                    break
                except Exception as loop_err:
                    logger.error("Error in SSE stream loop for %s: %s", workflow_id, loop_err)
                    yield ": keep-alive\n\n"
                    await asyncio.sleep(2.0)
        finally:
            await self.local_broadcaster.unsubscribe(workflow_id, local_q)
            if pubsub:
                try:
                    await pubsub.unsubscribe(f"mcp-sc:events:{workflow_id}")
                    await pubsub.aclose()
                except Exception:
                    pass

    def _format_sse(self, event: TraceEvent) -> str:
        """Format TraceEvent into standard SSE protocol string."""
        payload = {
            "id": event.id,
            "event_id": event.event_id,
            "workflow_id": event.workflow_id,
            "tenant_id": event.tenant_id,
            "correlation_id": event.correlation_id,
            "event_type": event.event_type,
            "task_id": event.task_id,
            "timestamp": _format_timestamp(event.timestamp),
            "details": event.details,
        }
        sse_lines = []
        if event.id:
            sse_lines.append(f"id: {event.id}")
        sse_lines.append(f"event: {event.event_type}")
        sse_lines.append(f"data: {json.dumps(payload)}")
        sse_lines.append("\n")
        return "\n".join(sse_lines)


# Global event hub instance
event_hub = WorkflowEventHub()
