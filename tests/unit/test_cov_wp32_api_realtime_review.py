"""wp32 coverage — realtime SSE plumbing, the review queue router, and the
one browser fetch failure that is neither a connect error nor a protocol error.

The realtime stream is driven twice: once through ``TestClient`` (real ASGI,
connected client) and once by calling the route's own coroutine with a request
that reports itself disconnected — the only way the disconnect ``break`` is
reachable without a socket. The review queue router runs against the real
``ReviewQueueService`` so the 404/409 translations are produced by real
transitions rather than by a mock that was told to raise.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from latticeai.api.browser import BrowserFetchError, _default_fetch_url
from latticeai.api.realtime import create_realtime_router
from latticeai.api.review_queue import create_review_queue_router
from latticeai.core.workspace_os import WorkspaceOSStore
from latticeai.services.review_queue import ReviewQueueService

# ── realtime ────────────────────────────────────────────────────────────────


class _Subscriber:
    def __init__(self, sub_id, scope, user):
        self.id = sub_id
        self.workspace_scope = scope
        self.user = user


class _Bus:
    """A bus whose stream is finite, so a connected client can be drained."""

    def __init__(self, *, frames: int = 3, presence_error: bool = False):
        self.frames = frames
        self.presence_error = presence_error
        self.subscribers: list = []
        self.left: list = []

    def add_subscriber(self, sub_id, *, workspace_scope=None, user=None):
        sub = _Subscriber(sub_id, workspace_scope, user)
        self.subscribers.append(sub)
        return sub

    async def stream(self, sub, *, refresh_authorization=None):
        for index in range(self.frames):
            if refresh_authorization is not None and not refresh_authorization(sub):
                return
            yield "data: frame-{0}\n\n".format(index)

    def recent(self, *, limit=50, workspace_scope=None):
        return [{"event_type": "demo", "limit": limit, "scope": sorted(workspace_scope or [])}]

    def presence(self, *, workspace_scope=None):
        return [{"client_id": "c1", "scope": sorted(workspace_scope or [])}]

    def stats(self):
        return {"feed_size": self.frames, "subscribers": len(self.subscribers)}

    def join(self, client_id, *, user=None, workspace_id=None):
        return {"client_id": client_id, "user": user, "workspace_id": workspace_id}

    def leave(self, client_id, *, user=None):
        self.left.append((client_id, user))


def _realtime_router(bus, *, require_user, allowed_scopes):
    return create_realtime_router(
        bus=bus,
        require_user=require_user,
        get_current_user=lambda _request: "u@example.com",
        allowed_scopes=allowed_scopes,
    )


def _realtime_client(bus, *, require_user=None, allowed_scopes=None):
    app = FastAPI()
    app.include_router(_realtime_router(
        bus,
        require_user=require_user or (lambda _request: "u@example.com"),
        allowed_scopes=allowed_scopes or (lambda _user: {"org-1"}),
    ))
    return TestClient(app)


def test_activity_page_redirects_into_the_spa_and_keeps_the_query():
    response = _realtime_client(_Bus()).get("/activity?tab=runs", follow_redirects=False)

    assert response.status_code == 308
    assert response.headers["location"] == "/app#/activity?tab=runs"


def test_realtime_stream_yields_frames_and_refreshes_the_subscriber_scope():
    bus = _Bus(frames=3)
    scopes = iter([{"org-1"}, {"org-1", "org-2"}, {"org-2"}, {"org-3"}])

    client = _realtime_client(bus, allowed_scopes=lambda _user: next(scopes))
    response = client.get("/realtime/stream")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert response.text.count("data: frame-") == 3
    # the last refresh re-stamped the subscriber's scope from the resolver
    assert bus.subscribers[0].workspace_scope == {"org-3"}


def test_realtime_stream_stops_when_re_authorization_raises():
    bus = _Bus(frames=3)
    calls = {"n": 0}

    def require_user(_request):
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError("session expired mid-stream")
        return "u@example.com"

    response = _realtime_client(bus, require_user=require_user).get("/realtime/stream")

    assert response.status_code == 200
    assert response.text == ""


def test_realtime_stream_stops_when_the_session_switches_user():
    bus = _Bus(frames=3)
    users = iter(["u@example.com", "someone-else@example.com"])

    response = _realtime_client(
        bus, require_user=lambda _request: next(users),
    ).get("/realtime/stream")

    assert response.status_code == 200
    assert response.text == ""


def test_realtime_stream_stops_as_soon_as_the_client_disconnects():
    bus = _Bus(frames=5)
    router = _realtime_router(
        bus,
        require_user=lambda _request: "u@example.com",
        allowed_scopes=lambda _user: {"org-1"},
    )
    route = next(r for r in router.routes if getattr(r, "path", "") == "/realtime/stream")

    class _DisconnectedRequest:
        async def is_disconnected(self):
            return True

    async def drive():
        response = await route.endpoint(_DisconnectedRequest())
        return [chunk async for chunk in response.body_iterator]

    frames = asyncio.run(drive())

    assert frames == []
    assert bus.subscribers[0].workspace_scope == {"org-1"}


def test_realtime_stream_yields_frames_while_the_client_stays_connected():
    """Companion to the disconnect test: the stay-connected arc (check → yield).

    Through ``TestClient`` this arc's coverage depends on how starlette reports
    ``is_disconnected()`` for a drained test stream — starlette 1.6 reports
    True immediately, so the ASGI-driven tests stopped reaching the yield.
    Driving the coroutine directly with an always-connected request pins the
    arc on every supported stack.
    """
    bus = _Bus(frames=2)
    router = _realtime_router(
        bus,
        require_user=lambda _request: "u@example.com",
        allowed_scopes=lambda _user: {"org-1"},
    )
    route = next(r for r in router.routes if getattr(r, "path", "") == "/realtime/stream")

    class _ConnectedRequest:
        async def is_disconnected(self):
            return False

    async def drive():
        response = await route.endpoint(_ConnectedRequest())
        return [chunk async for chunk in response.body_iterator]

    frames = asyncio.run(drive())

    assert frames == ["data: frame-0\n\n", "data: frame-1\n\n"]
    assert bus.subscribers[0].workspace_scope == {"org-1"}


def test_realtime_presence_is_scoped_and_reports_bus_stats():
    response = _realtime_client(_Bus(frames=2)).get("/realtime/presence")

    assert response.status_code == 200
    assert response.json() == {
        "presence": [{"client_id": "c1", "scope": ["org-1"]}],
        "stats": {"feed_size": 2, "subscribers": 0},
    }


def test_realtime_join_refuses_a_caller_with_no_accessible_workspace():
    response = _realtime_client(_Bus(), allowed_scopes=lambda _user: set()).post(
        "/realtime/presence/join", json={},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "No accessible workspace for presence."


def test_realtime_leave_acknowledges_a_client_that_left():
    bus = _Bus()

    response = _realtime_client(bus).post(
        "/realtime/presence/leave", json={"client_id": "c-7"},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert bus.left == [("c-7", "u@example.com")]


# ── review queue ────────────────────────────────────────────────────────────


class _ChangeProposals:
    """Stands in for the proposal service the review router may be given."""

    def __init__(self, *, error=None):
        self.error = error
        self.applied: list = []

    def approve_and_apply(self, item_id, *, user_email=None, workspace_id=None):
        if self.error is not None:
            raise self.error
        self.applied.append(item_id)
        return {
            "applied": True,
            "item": {
                "id": item_id,
                "status": "approved",
                "effective_status": "approved",
                "title": "Rewrite index.html",
                "source": "change_proposal",
            },
        }


def _review_client(tmp_path, *, change_proposals=None):
    service = ReviewQueueService(store=WorkspaceOSStore(tmp_path / "data"))
    events: list = []
    app = FastAPI()
    app.include_router(create_review_queue_router(
        service=service,
        require_user=lambda _request: "u@example.com",
        gate_read=lambda _request: "personal",
        gate_write=lambda _request: "personal",
        run_review_item=lambda *_a, **_k: {"run": {"id": "run-1"}},
        append_audit_event=lambda event, **kwargs: events.append((event, kwargs)),
        change_proposals=change_proposals,
    ))
    return TestClient(app), service, events


def _seed(service, *, source="workflow_run", title="Daily digest ready"):
    # The stored id is derived from the item's content, so distinct titles are
    # what make distinct items.
    return service.create(
        title=title,
        summary="3 decisions",
        source=source,
        kind="suggestion",
        payload={},
        provenance={},
        user_email="u@example.com",
        workspace_id="personal",
    )


def test_review_create_rejects_a_blank_title_with_422(tmp_path):
    client, _service, events = _review_client(tmp_path)

    response = client.post("/automation/reviews", json={"title": "   "})

    assert response.status_code == 422
    assert events == []


def test_review_get_item_returns_the_item_or_404(tmp_path):
    client, service, _events = _review_client(tmp_path)
    item = _seed(service)

    found = client.get("/automation/reviews/{0}".format(item["id"]))
    missing = client.get("/automation/reviews/review-ghost")

    assert found.status_code == 200
    assert found.json()["title"] == "Daily digest ready"
    assert found.json()["effective_status"] == "pending"
    assert missing.status_code == 404


def test_review_approve_without_a_proposal_service_404s_on_a_missing_item(tmp_path):
    client, _service, events = _review_client(tmp_path)

    response = client.post("/automation/reviews/review-ghost/approve")

    assert response.status_code == 404
    assert events == []


def test_review_approve_with_a_proposal_service_404s_on_a_missing_item(tmp_path):
    client, _service, _events = _review_client(tmp_path, change_proposals=_ChangeProposals())

    response = client.post("/automation/reviews/review-ghost/approve")

    assert response.status_code == 404


def test_review_approve_maps_proposal_service_failures(tmp_path):
    missing_client, service, _ = _review_client(
        tmp_path / "a", change_proposals=_ChangeProposals(error=KeyError("gone")),
    )
    missing_item = _seed(service, source="change_proposal")

    invalid_client, invalid_service, _ = _review_client(
        tmp_path / "b", change_proposals=_ChangeProposals(error=ValueError("nothing staged")),
    )
    invalid_item = _seed(invalid_service, source="change_proposal")

    not_found = missing_client.post(
        "/automation/reviews/{0}/approve".format(missing_item["id"]),
    )
    bad_request = invalid_client.post(
        "/automation/reviews/{0}/approve".format(invalid_item["id"]),
    )

    assert not_found.status_code == 404
    assert bad_request.status_code == 400
    assert bad_request.json()["detail"] == "nothing staged"


def test_review_approve_applies_the_staged_change_when_governed(tmp_path):
    proposals = _ChangeProposals()
    client, service, events = _review_client(tmp_path, change_proposals=proposals)
    item = _seed(service, source="change_proposal")

    response = client.post("/automation/reviews/{0}/approve".format(item["id"]))

    assert response.status_code == 200
    assert response.json()["status"] == "approved"
    assert proposals.applied == [item["id"]]
    assert [event for event, _ in events] == ["review_item_approve"]


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("dismiss", {"reason": "not useful"}),
        ("snooze", {"until": "2099-01-01T00:00:00"}),
        ("run_now", None),
    ],
)
def test_review_transitions_404_on_a_missing_item(tmp_path, path, body):
    client, _service, _events = _review_client(tmp_path)

    response = client.post(
        "/automation/reviews/review-ghost/{0}".format(path), json=body,
    )

    assert response.status_code == 404


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("dismiss", {"reason": "too late"}),
        ("snooze", {"until": "2099-01-01T00:00:00"}),
        ("run_now", None),
    ],
)
def test_review_transitions_409_once_the_item_is_resolved(tmp_path, path, body):
    client, service, _events = _review_client(tmp_path)
    item = _seed(service)
    service.approve(item["id"], workspace_id="personal")

    response = client.post(
        "/automation/reviews/{0}/{1}".format(item["id"], path), json=body,
    )

    assert response.status_code == 409


def test_review_dismiss_snooze_and_run_now_move_the_item(tmp_path):
    client, service, events = _review_client(tmp_path)
    snoozed = _seed(service, title="Snooze me")
    dismissed = _seed(service, title="Dismiss me")
    ran = _seed(service, title="Run me")

    snooze = client.post(
        "/automation/reviews/{0}/snooze".format(snoozed["id"]),
        json={"until": "2099-01-01T00:00:00"},
    )
    dismiss = client.post(
        "/automation/reviews/{0}/dismiss".format(dismissed["id"]),
        json={"reason": "handled offline"},
    )
    run_now = client.post("/automation/reviews/{0}/run_now".format(ran["id"]))

    assert snooze.json()["status"] == "snoozed"
    assert dismiss.json()["status"] == "dismissed"
    assert run_now.json()["status"] == "pending"
    assert run_now.json()["payload"]["last_run_id"] == "run-1"
    assert [event for event, _ in events] == [
        "review_item_snooze", "review_item_dismiss", "review_item_run_now",
    ]


# ── browser fetch ───────────────────────────────────────────────────────────


def test_browser_fetch_reports_a_read_timeout_as_an_unreachable_page():
    def resolver(hostname, port, family=None, type=None):  # noqa: A002 - getaddrinfo's own name
        assert hostname == "example.com"
        return [(2, 1, 6, "", ("93.184.216.34", port))]

    def handler(_request):
        raise httpx.ReadTimeout("the page never finished")

    with pytest.raises(BrowserFetchError) as excinfo:
        _default_fetch_url(
            "https://example.com/slow",
            resolver=resolver,
            transport=httpx.MockTransport(handler),
        )

    assert "Could not reach the page: the page never finished" in str(excinfo.value)
