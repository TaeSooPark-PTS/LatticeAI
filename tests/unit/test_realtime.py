"""Unit tests for the v2.0 Realtime Collaboration bus."""

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from latticeai.api.realtime import create_realtime_router
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
    assert "z" not in types    # authenticated scopes never inherit unscoped data
    assert "y" not in types    # other workspace filtered out


def test_empty_authenticated_scope_sees_no_unscoped_events_or_presence():
    bus = RealtimeBus()
    bus.publish({"area": "a", "event_type": "secret", "workspace_id": None, "payload": {}})
    bus.join("legacy", user="legacy", workspace_id=None)

    assert bus.recent(limit=10, workspace_scope=set()) == []
    assert bus.presence(workspace_scope=set()) == []
    assert bus.add_subscriber("empty", workspace_scope=set()).accepts(None) is False


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


def test_stream_rechecks_scope_before_delivering_queued_event():
    async def scenario():
        bus = RealtimeBus()
        scope = {"org-1"}
        sub = bus.add_subscriber("s1", workspace_scope=set(scope), user="alice")

        def refresh(current_sub):
            current_sub.workspace_scope = set(scope)
            return True

        gen = bus.stream(sub, heartbeat=0.01, refresh_authorization=refresh)
        bus.publish({"area": "a", "event_type": "allowed", "workspace_id": "org-1", "payload": {}})
        first = await asyncio.wait_for(gen.__anext__(), timeout=1)
        scope.clear()
        bus.publish({"area": "a", "event_type": "revoked-secret", "workspace_id": "org-1", "payload": {}})
        second = await asyncio.wait_for(gen.__anext__(), timeout=1)
        await gen.aclose()
        return first, second

    first, second = asyncio.run(scenario())
    assert "allowed" in first
    assert "revoked-secret" not in second
    assert second == ": heartbeat\n\n"


def test_stream_closes_when_session_revalidation_fails():
    async def scenario():
        bus = RealtimeBus()
        sub = bus.add_subscriber("s1", workspace_scope={"org-1"}, user="alice")
        gen = bus.stream(sub, refresh_authorization=lambda _sub: False)
        with pytest.raises(StopAsyncIteration):
            await gen.__anext__()
        return bus.stats()["subscribers"]

    assert asyncio.run(scenario()) == 0


def test_backpressure_drops_oldest_not_publisher():
    bus = RealtimeBus()
    sub = bus.add_subscriber("s1")
    # Fill beyond the queue cap; publish must never raise.
    for i in range(200):
        bus.publish({"area": "a", "event_type": f"e{i}", "workspace_id": None, "payload": {}})
    assert sub.queue.qsize() <= 100


def test_realtime_feed_exposes_contract_views():
    bus = RealtimeBus()
    bus.publish({"area": "workspace", "event_type": "agent_started", "workspace_id": "personal", "payload": {"run_id": "run-1"}})
    app = FastAPI()
    app.include_router(create_realtime_router(
        bus=bus,
        require_user=lambda request: "tester",
        get_current_user=lambda request: "tester",
        allowed_scopes=lambda user: {"personal"},
    ))

    payload = TestClient(app).get("/realtime/feed").json()

    assert payload["events"][0]["contract"]["family"] == "agent-run-contract/v1"
    assert payload["contracts"][0]["run_id"] == "run-1"


def test_presence_router_rejects_unauthorized_workspace_and_client_takeover():
    bus = RealtimeBus()
    current_user = {"value": "alice@example.com"}
    app = FastAPI()
    app.include_router(create_realtime_router(
        bus=bus,
        require_user=lambda request: current_user["value"],
        get_current_user=lambda request: current_user["value"],
        allowed_scopes=lambda user: {"personal", "org-1"} if user == "alice@example.com" else {"personal"},
    ))
    client = TestClient(app)

    denied = client.post(
        "/realtime/presence/join",
        json={"client_id": "alice-client", "workspace_id": "org-2"},
    )
    joined = client.post(
        "/realtime/presence/join",
        json={"client_id": "alice-client", "workspace_id": "org-1"},
    )
    current_user["value"] = "bob@example.com"
    takeover = client.post(
        "/realtime/presence/join",
        json={"client_id": "alice-client", "workspace_id": "personal"},
    )
    eviction = client.post(
        "/realtime/presence/leave",
        json={"client_id": "alice-client"},
    )

    assert denied.status_code == 403
    assert joined.status_code == 200
    assert takeover.status_code == 403
    assert eviction.status_code == 403
    assert bus.presence()[0]["user"] == "alice@example.com"


def test_presence_router_scopes_missing_workspace_for_authenticated_user():
    bus = RealtimeBus()
    app = FastAPI()
    app.include_router(create_realtime_router(
        bus=bus,
        require_user=lambda request: "alice@example.com",
        get_current_user=lambda request: "alice@example.com",
        allowed_scopes=lambda user: {"personal", "org-1"},
    ))

    response = TestClient(app).post("/realtime/presence/join", json={"client_id": "client"})

    assert response.status_code == 200
    assert response.json()["presence"]["workspace_id"] == "personal"
