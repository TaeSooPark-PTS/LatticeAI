"""Realtime Collaboration — an in-process pub/sub bus, presence registry, and
activity feed delivered over Server-Sent Events.

SSE is chosen deliberately: the codebase already streams model output over SSE
(``latticeai.services.model_runtime.sse_event``), it needs no extra dependency,
and it works through the existing single-port local-first deployment. The bus is
in-process (one server, local-first) and fans events out to subscriber queues.

Integration shape: the Workspace OS store exposes a single ``event_sink`` hook
that fires on every ``record_timeline_event``. Wiring ``RealtimeBus.publish`` as
that sink makes *all* workspace / graph / agent / workflow activity flow into
the realtime feed automatically — no per-call instrumentation, no duplicated
event system.

Guarantees:

* **Single-user local mode keeps working.** Publishing with zero subscribers is
  a no-op; the feed ring buffer is still maintained so a late subscriber can
  catch up.
* **Workspace isolation preserved.** Every event carries ``workspace_id``; a
  subscriber only receives events whose workspace is in its allowed scope set
  (``None`` scope = personal/local view sees unscoped + personal events).
* **Backpressure-safe.** Per-subscriber queues are bounded; on overflow the
  oldest event is dropped rather than blocking the publisher.
"""

from __future__ import annotations

import asyncio
import json
import threading
from datetime import datetime
from typing import Any, AsyncIterator, Dict, List, Optional, Set

from lattice_brain.runtime.contracts import realtime_event_contract


REALTIME_VERSION = "2.2.0"
_FEED_LIMIT = 200
_QUEUE_MAX = 100


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def sse_format(event: Dict[str, Any]) -> str:
    """Encode an event as an SSE ``data:`` frame."""
    return f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"


class _Subscriber:
    __slots__ = ("id", "queue", "workspace_scope", "user", "joined_at", "loop")

    def __init__(self, sub_id: str, workspace_scope: Optional[Set[str]], user: Optional[str]):
        self.id = sub_id
        self.queue: "asyncio.Queue[Dict[str, Any]]" = asyncio.Queue(maxsize=_QUEUE_MAX)
        self.workspace_scope = workspace_scope
        self.user = user
        self.joined_at = _now()
        try:
            self.loop: Optional[asyncio.AbstractEventLoop] = asyncio.get_running_loop()
        except RuntimeError:
            self.loop = None

    def accepts(self, workspace_id: Optional[str]) -> bool:
        # ``None`` scope = see everything the local user can (personal/unscoped).
        if self.workspace_scope is None:
            return True
        if workspace_id is None:
            return True
        return workspace_id in self.workspace_scope


class RealtimeBus:
    """In-process event bus with presence and a recent-activity ring buffer."""

    def __init__(self) -> None:
        self._subscribers: Dict[str, _Subscriber] = {}
        self._feed: List[Dict[str, Any]] = []
        self._presence: Dict[str, Dict[str, Any]] = {}
        self._seq = 0
        self._lock = threading.RLock()

    # ── publishing ────────────────────────────────────────────────────────

    def publish(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """Publish an event to matching subscribers + the activity feed.

        Safe to call from sync code (e.g. the store's timeline hook). Never
        raises and never blocks the caller.
        """
        with self._lock:
            self._seq += 1
            enriched = {
                "seq": self._seq,
                "received_at": _now(),
                "area": event.get("area", "workspace"),
                "event_type": event.get("event_type", "event"),
                "workspace_id": event.get("workspace_id"),
                "payload": event.get("payload", {}),
                **{k: v for k, v in event.items() if k not in {"area", "event_type", "workspace_id", "payload"}},
            }
            enriched["contract"] = realtime_event_contract(enriched)
            self._feed.append(enriched)
            if len(self._feed) > _FEED_LIMIT:
                self._feed = self._feed[-_FEED_LIMIT:]

            workspace_id = enriched.get("workspace_id")
            subscribers = [sub for sub in self._subscribers.values() if sub.accepts(workspace_id)]
        for sub in subscribers:
            if sub.loop is not None and sub.loop.is_running():
                sub.loop.call_soon_threadsafe(self._enqueue, sub, enriched)
            else:
                self._enqueue(sub, enriched)
        return enriched

    @staticmethod
    def _enqueue(sub: _Subscriber, event: Dict[str, Any]) -> None:
        try:
            sub.queue.put_nowait(event)
        except asyncio.QueueFull:
            try:
                sub.queue.get_nowait()  # drop oldest
                sub.queue.put_nowait(event)
            except Exception:
                pass

    # The store calls ``event_sink(event)`` positionally; expose a stable alias.
    def __call__(self, event: Dict[str, Any]) -> Dict[str, Any]:
        return self.publish(event)

    # ── subscription ──────────────────────────────────────────────────────

    def add_subscriber(self, sub_id: str, *, workspace_scope: Optional[Set[str]] = None, user: Optional[str] = None) -> _Subscriber:
        sub = _Subscriber(sub_id, workspace_scope, user)
        with self._lock:
            self._subscribers[sub_id] = sub
        return sub

    def remove_subscriber(self, sub_id: str) -> None:
        with self._lock:
            self._subscribers.pop(sub_id, None)

    async def stream(self, sub: _Subscriber, *, heartbeat: float = 15.0) -> AsyncIterator[str]:
        """Yield SSE frames for a subscriber until the client disconnects.

        Emits a periodic heartbeat comment so proxies keep the connection open
        and single-user local mode never looks "stuck" with no events.
        """
        # Replay a small tail so a fresh subscriber has immediate context.
        for event in self.recent(limit=10, workspace_scope=sub.workspace_scope):
            yield sse_format(event)
        try:
            while True:
                try:
                    event = await asyncio.wait_for(sub.queue.get(), timeout=heartbeat)
                    yield sse_format(event)
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
        finally:
            self.remove_subscriber(sub.id)

    # ── feed + presence ─────────────────────────────────────────────────────

    def recent(self, *, limit: int = 50, workspace_scope: Optional[Set[str]] = None) -> List[Dict[str, Any]]:
        with self._lock:
            events = list(self._feed)
        if workspace_scope is not None:
            events = [e for e in events if e.get("workspace_id") is None or e.get("workspace_id") in workspace_scope]
        return list(reversed(events[-max(1, min(limit, _FEED_LIMIT)):]))

    def join(self, client_id: str, *, user: Optional[str], workspace_id: Optional[str]) -> Dict[str, Any]:
        record = {
            "client_id": client_id,
            "user": user,
            "workspace_id": workspace_id,
            "joined_at": _now(),
            "last_seen": _now(),
        }
        with self._lock:
            self._presence[client_id] = record
        self.publish({"area": "presence", "event_type": "join", "workspace_id": workspace_id, "payload": {"user": user, "client_id": client_id}})
        return record

    def heartbeat(self, client_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            record = self._presence.get(client_id)
            if record:
                record["last_seen"] = _now()
            return record

    def leave(self, client_id: str) -> None:
        with self._lock:
            record = self._presence.pop(client_id, None)
        if record:
            self.publish({"area": "presence", "event_type": "leave", "workspace_id": record.get("workspace_id"), "payload": {"client_id": client_id}})

    def presence(self, *, workspace_scope: Optional[Set[str]] = None) -> List[Dict[str, Any]]:
        with self._lock:
            records = list(self._presence.values())
        if workspace_scope is not None:
            records = [r for r in records if r.get("workspace_id") is None or r.get("workspace_id") in workspace_scope]
        return records

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "version": REALTIME_VERSION,
                "subscribers": len(self._subscribers),
                "presence": len(self._presence),
                "feed_size": len(self._feed),
                "transport": "sse",
            }
