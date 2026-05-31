# Lattice AI Realtime Collaboration

Realtime Collaboration is the v2.0.0 subsystem that gives a Lattice AI workspace
a live **presence** registry and an **activity feed**. It is delivered over
Server-Sent Events (SSE) by an in-process pub/sub bus, the
[`RealtimeBus`](../latticeai/core/realtime.py).

The design goal is to surface "what is happening in the workspace right now"
(workspaces created, graphs indexed, agents and workflows run, plugins enabled,
who is online) without adding a new transport, a new dependency, or a second
event system.

---

## Why SSE

SSE was chosen deliberately rather than WebSockets:

- The codebase **already streams model output over SSE**
  (`latticeai.services.model_runtime.sse_event`), so the wire format and the
  client patterns are familiar.
- SSE needs **no extra dependency** — it is plain `text/event-stream` over the
  existing single HTTP port used by the local-first deployment.
- It works through the existing single-port local-first server with no extra
  ports or upgrade handshakes.

The bus is **in-process** (one server, local-first) and fans events out to
in-memory subscriber queues.

> **Compatibility.** This subsystem is purely additive. It introduces new
> `/realtime/*` endpoints and an `/activity` page, and it attaches to the
> existing `WorkspaceOSStore` through an optional `event_sink` hook. No v1.x
> data shape, API, or behavior changes. With zero subscribers the bus is a
> no-op, so single-user local mode behaves exactly as before.

---

## Architecture at a glance

```
record_timeline_event(...)        (any workspace/graph/agent/workflow write)
        │
        ▼
WorkspaceOSStore.event_sink ──► RealtimeBus.publish(event)
                                      │
                  ┌───────────────────┼───────────────────────┐
                  ▼                   ▼                         ▼
        ring-buffer feed     matching subscriber queues   presence registry
        (capped, 200)        (bounded, drop-oldest)
                                      │
                                      ▼
                         GET /realtime/stream  (SSE frames)
```

The bus is created once and wired into the store in `server_app.py`:

```python
REALTIME_BUS = RealtimeBus()
WORKSPACE_OS = WorkspaceOSStore(DATA_DIR, event_sink=REALTIME_BUS)
```

### The key integration: a single `event_sink`

`WorkspaceOSStore` exposes exactly one realtime hook. Its
`record_timeline_event` method already runs on **every** meaningful workspace
write, and it ends by firing the sink:

```python
def record_timeline_event(self, area, event_type, payload, workspace_id=None):
    state = self.load_state()
    event = {
        "id": f"timeline-{...}",
        "area": area,
        "event_type": event_type,
        "timestamp": _now(),
        "workspace_id": self._resolve_scope(workspace_id, state),
        "payload": payload,
    }
    # ... persist to the timeline ...
    if self.event_sink is not None:
        try:
            self.event_sink(event)
        except Exception:
            # Realtime delivery is best-effort and must never break a write.
            pass
    return event
```

Because the store calls `event_sink(event)` positionally and `RealtimeBus`
implements `__call__` as an alias for `publish`, wiring the bus as the sink
makes **all** workspace / graph / agent / workflow / memory / skill / plugin
activity flow into the realtime feed automatically. There is **no per-call
instrumentation** to maintain and no duplicated event system — anything that
already records a timeline event is realtime by construction.

Representative `area` / `event_type` pairs already emitted by the store
include:

| `area`      | example `event_type`                              |
|-------------|---------------------------------------------------|
| `workspace` | `workspace_created`, `member_added`, `workspace_archived` |
| `graph`     | `answer_trace`, `indexing_paused`, `indexing_resumed` |
| `agent`     | `agent_run`                                       |
| `workflow`  | `workflow_created`, `workflow_run`, `workflow_edited` |
| `memory`    | `memory_upserted`, `memory_deleted`               |
| `skills`    | `skill_installed`, `skill_enabled`                |
| `plugins`   | `plugin_installed`, `plugin_enabled`              |
| `presence`  | `join`, `leave` (emitted by the bus itself)       |

---

## `publish()` — the core contract

```python
def publish(self, event: Dict[str, Any]) -> Dict[str, Any]: ...
```

`publish` is the heart of the bus and is built so it is always safe to call
from the store's synchronous write path:

- **Sync-callable.** No `await`, callable from any synchronous code.
- **Never raises.** Queue overflow and other failures are swallowed; the worst
  case is a dropped frame, never a broken write. (The store also wraps the call
  in its own `try/except` as a second layer.)
- **Never blocks.** Subscriber queues are bounded; the publisher never waits on
  a slow or disconnected consumer.

On each call it:

1. Assigns a monotonically increasing `seq` and a `received_at` timestamp, and
   normalizes the event into a stable enriched shape.
2. Appends the enriched event to a **capped ring-buffer feed** (`_FEED_LIMIT =
   200`); older events fall off the front.
3. Fans the event out to every subscriber whose scope **accepts** the event's
   `workspace_id` (see [Workspace isolation](#workspace-isolation)).

### Enriched event shape

```json
{
  "seq": 42,
  "received_at": "2026-06-01T10:15:30",
  "area": "workflow",
  "event_type": "workflow_run",
  "workspace_id": "ws_marketing",
  "payload": { "run_id": "wf-run-7", "workflow_id": "wf_3", "status": "ok" },
  "id": "timeline-9f1c...",
  "timestamp": "2026-06-01T10:15:30"
}
```

`area`, `event_type`, `workspace_id`, and `payload` are always present
(defaulting to `"workspace"`, `"event"`, `None`, and `{}` respectively). Any
extra keys on the source event (such as the store's `id` and `timestamp`) are
preserved alongside them.

### Backpressure: bounded queues drop the oldest

Each subscriber has an `asyncio.Queue(maxsize=100)`. On overflow the publisher
does **not** block — it discards the oldest queued event to make room for the
newest:

```python
try:
    sub.queue.put_nowait(enriched)
except asyncio.QueueFull:
    try:
        sub.queue.get_nowait()      # drop oldest
        sub.queue.put_nowait(enriched)
    except Exception:
        pass
```

A slow client therefore sees gaps rather than stalling the whole server.

---

## Workspace isolation

Every event carries a `workspace_id`. A subscriber is created with an allowed
**workspace scope** — a `Set[str]` of workspace IDs the caller may see — and
only receives events that its scope accepts:

```python
def accepts(self, workspace_id: Optional[str]) -> bool:
    # ``None`` scope = see everything the local user can (personal/unscoped).
    if self.workspace_scope is None:
        return True
    if workspace_id is None:
        return True
    return workspace_id in self.workspace_scope
```

Two rules fall out of this:

- **Unscoped events are always delivered.** An event with `workspace_id` of
  `None` reaches every subscriber. So do events for a subscriber whose scope is
  `None`. This is what makes **single-user local mode** work with no scope
  restriction — there is nothing to filter and everything is visible.
- **Scoped events are filtered.** When a subscriber has a concrete scope set,
  it only receives events whose `workspace_id` is in that set (plus the always-
  delivered unscoped events).

The scope is resolved per request, not hard-coded. The API layer calls
`PlatformRuntime.allowed_scopes`, which derives the set from the workspaces the
user can actually list:

```python
def allowed_scopes(self, user: Optional[str]) -> Optional[Set[str]]:
    try:
        workspaces = self.svc.list_workspaces(user or None).get("workspaces", [])
        return {ws.get("workspace_id") for ws in workspaces if ws.get("workspace_id")}
    except Exception:
        return None
```

If scope resolution fails for any reason it returns `None` — the permissive,
local-friendly default — rather than erroring the stream. The feed
(`recent`) and presence (`presence`) reads apply the same scope filter, so a
caller can never read across workspaces it is not entitled to.

---

## `stream()` — replay tail, live frames, heartbeats

```python
async def stream(self, sub: _Subscriber, *, heartbeat: float = 15.0) -> AsyncIterator[str]: ...
```

When a client connects, `stream` first **replays a short tail** (up to the 10
most recent in-scope events) so a fresh subscriber immediately has context,
then yields **live frames** as they arrive. If no event arrives within the
`heartbeat` interval (default 15 seconds) it emits an SSE comment:

```
: heartbeat
```

The heartbeat keeps proxies from closing an idle connection and stops
single-user local mode from looking "stuck" when nothing is happening. When the
async generator is closed (client disconnect), the subscriber is removed in a
`finally` block, so queues do not leak.

Each event is encoded with `sse_format`:

```python
def sse_format(event: Dict[str, Any]) -> str:
    """Encode an event as an SSE ``data:`` frame."""
    return f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
```

---

## API reference

All endpoints require an authenticated user via `require_user`. In local mode
with auth disabled, `require_user` returns an empty string and the request
proceeds; the resolved scope is then `None` (see everything). The realtime
router is mounted in `server_app.py` with the live bus, the auth helpers, and
`PlatformRuntime.allowed_scopes` as the scope resolver.

### `GET /activity`

Serves the Activity UI page (`static/activity.html`). Returns `404` if the UI
file or static directory is not available.

### `GET /realtime/stream`

The SSE stream. Resolves the caller's allowed scope, registers a new subscriber
with a random ID, and returns a `StreamingResponse` of
`media_type="text/event-stream"`. The response sets streaming-friendly headers:

```
Cache-Control: no-cache
X-Accel-Buffering: no
Connection: keep-alive
```

The server stops generating frames as soon as `request.is_disconnected()` is
true.

### `GET /realtime/feed`

Reads the recent activity feed (scope-filtered). Query parameter `limit`
defaults to `50` and is clamped to the `200`-entry buffer. Returns newest-first:

```json
{
  "events": [ /* enriched events, newest first */ ],
  "stats": {
    "version": "2.0.0",
    "subscribers": 1,
    "presence": 2,
    "feed_size": 17,
    "transport": "sse"
  }
}
```

### `GET /realtime/presence`

Returns the scope-filtered presence registry plus the same `stats` block:

```json
{
  "presence": [
    {
      "client_id": "Hk3p...",
      "user": "rnlgnquvk@gmail.com",
      "workspace_id": "ws_marketing",
      "joined_at": "2026-06-01T10:14:00",
      "last_seen": "2026-06-01T10:15:30"
    }
  ],
  "stats": { "version": "2.0.0", "subscribers": 1, "presence": 1, "feed_size": 17, "transport": "sse" }
}
```

### `POST /realtime/presence/join`

Registers a client as present. Request body:

```json
{
  "client_id": "optional-client-id",
  "workspace_id": "ws_marketing"
}
```

Both fields are optional. If `client_id` is omitted, the server generates one.
A `presence`/`join` event is published to subscribers in scope. Response:

```json
{
  "presence": {
    "client_id": "Hk3p...",
    "user": "rnlgnquvk@gmail.com",
    "workspace_id": "ws_marketing",
    "joined_at": "2026-06-01T10:14:00",
    "last_seen": "2026-06-01T10:14:00"
  }
}
```

### `POST /realtime/presence/leave`

Removes a client from the presence registry (publishing a `presence`/`leave`
event) when a `client_id` is supplied. Request body uses the same
`PresenceRequest` shape; only `client_id` is read.

```json
{ "status": "ok" }
```

---

## Client example (`EventSource`)

The stream is standard SSE, so a browser can consume it with the built-in
`EventSource`. Join presence first, then subscribe to the feed:

```javascript
// 1. Announce presence (optional but enables the presence registry).
const clientId = crypto.randomUUID();
await fetch("/realtime/presence/join", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ client_id: clientId, workspace_id: "ws_marketing" }),
});

// 2. Subscribe to the live activity stream.
const source = new EventSource("/realtime/stream");

source.onmessage = (e) => {
  const event = JSON.parse(e.data);
  console.log(`[${event.seq}] ${event.area}/${event.event_type}`, event.payload);
  // e.g. render into an activity panel...
};

source.onerror = () => {
  // EventSource auto-reconnects; on the next connect the server replays
  // a short tail so missed-while-offline context is restored.
};

// 3. On unload, leave presence.
window.addEventListener("beforeunload", () => {
  navigator.sendBeacon(
    "/realtime/presence/leave",
    new Blob([JSON.stringify({ client_id: clientId })], { type: "application/json" }),
  );
});
```

Heartbeat lines (`: heartbeat`) are SSE comments and never fire `onmessage`, so
no client-side filtering is needed.

---

## Operational notes

- **Limits.** Feed ring buffer: `200` events (`_FEED_LIMIT`). Per-subscriber
  queue: `100` events (`_QUEUE_MAX`). Heartbeat interval: `15` seconds.
- **No persistence.** The feed, presence registry, and subscriber set live in
  memory and reset on server restart. The durable record of activity remains
  the store's own `timeline` (capped at 500 events) — realtime is a live view
  on top of it, not a replacement.
- **Single process.** The bus is in-process by design for the local-first
  deployment; it does not coordinate across multiple server processes.
- **`stats()`** reports `version` (`2.0.0`), live `subscribers`, `presence`
  count, `feed_size`, and the `transport` (`"sse"`) for health/observability.
