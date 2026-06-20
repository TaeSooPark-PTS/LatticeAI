"""Unit tests for the v2.0 Realtime Collaboration bus."""

import asyncio

from latticeai.core.realtime import RealtimeBus, sse_format
from latticeai.core.workspace_os import WorkspaceOSStore


def test_publish_and_recent_feed():
    bus = RealtimeBus()
    ev = bus.publish({"area": "workspace", "event_type": "test", "workspace_id": "personal", "payload": {"k": 1}})
    assert ev["seq"] == 1
    assert ev["contract"]["family"] == "agent-run-contract/v1"
    assert ev["contract"]["kind"] == "realtime_event"
    feed = bus.recent(limit=10)
    assert feed and feed[0]["event_type"] == "test"


def test_feed_respects_workspace_scope():
    bus = RealtimeBus()
    bus.publish({"area": "a", "event_type": "x", "workspace_id": "org-1", "payload": {}})
    bus.publish({"area": "a", "event_type": "y", "workspace_id": "org-2", "payload": {}})
    bus.publish({"area": "a", "event_type": "z", "workspace_id": None, "payload": {}})
    scoped = bus.recent(limit=10, workspace_scope={"org-1"})
    types = {e["event_type"] for e in scoped}
    assert "x" in types        # in scope
    assert "z" in types        # unscoped always visible
    assert "y" not in types    # other workspace filtered out


def test_presence_join_leave():
    bus = RealtimeBus()
    bus.join("client-1", user="alice@example.com", workspace_id="personal")
    assert len(bus.presence()) == 1
    bus.leave("client-1")
    assert len(bus.presence()) == 0


def test_presence_scope_filter():
    bus = RealtimeBus()
    bus.join("c1", user="a", workspace_id="org-1")
    bus.join("c2", user="b", workspace_id="org-2")
    assert len(bus.presence(workspace_scope={"org-1"})) == 1


def test_store_event_sink_publishes_timeline():
    import tempfile
    from pathlib import Path
    bus = RealtimeBus()
    store = WorkspaceOSStore(Path(tempfile.mkdtemp()), event_sink=bus)
    before = bus.stats()["feed_size"]
    store.record_timeline_event("workflow", "demo_event", {"hello": "world"})
    after = bus.stats()["feed_size"]
    assert after == before + 1
    assert bus.recent(limit=1)[0]["event_type"] == "demo_event"
    assert bus.recent(limit=1)[0]["contract"]["family"] == "agent-run-contract/v1"


def test_sse_format_frame():
    frame = sse_format({"event_type": "ping"})
    assert frame.startswith("data: ")
    assert frame.endswith("\n\n")


def test_stream_replays_recent_then_can_be_cancelled():
    async def scenario():
        bus = RealtimeBus()
        bus.publish({"area": "a", "event_type": "seed", "workspace_id": None, "payload": {}})
        sub = bus.add_subscriber("s1")
        gen = bus.stream(sub)
        first = await asyncio.wait_for(gen.__anext__(), timeout=2)
        await gen.aclose()
        return first

    frame = asyncio.run(scenario())
    assert "seed" in frame


def test_backpressure_drops_oldest_not_publisher():
    bus = RealtimeBus()
    sub = bus.add_subscriber("s1")
    # Fill beyond the queue cap; publish must never raise.
    for i in range(200):
        bus.publish({"area": "a", "event_type": f"e{i}", "workspace_id": None, "payload": {}})
    assert sub.queue.qsize() <= 100
