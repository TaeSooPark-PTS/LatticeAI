"""Brain Chronicle router (v11.3.0 track B).

The router is thin on purpose, so these tests pin the three things that are
*not* the service's job: that the caller is identified and workspace-gated on
every path (the chronicle is a complete history of one Brain — an ungated read
would be a full disclosure), that a malformed date or instant answers 422 in
the reader's own language instead of 500 or a silently empty day, and that the
three paths are exactly the ones the contract names.

Both wirings are exercised: fakes, to observe the arguments the router hands
the service, and the real ``ChronicleService`` over a real tmp_path Brain, so
the route cannot pass while returning a shape nothing can produce.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from lattice_brain.conversations import ConversationStore
from lattice_brain.graph.store import KnowledgeGraphStore
from latticeai.api.chronicle import create_chronicle_router
from latticeai.core.messages import LANGUAGE_HEADER, MESSAGES
from latticeai.services.chronicle import ChronicleService


class FakeChronicle:
    """Records what the router asked for; answers with a marker payload."""

    def __init__(self) -> None:
        self.calls: list = []

    def overview(self, **kwargs):
        self.calls.append(("overview", kwargs))
        return {"totals": {"sources": 1}, "series": [], "kwargs": _readable(kwargs)}

    def day(self, date, **kwargs):
        self.calls.append(("day", {"date": date, **kwargs}))
        if date == "boom":
            raise ValueError("malformed")
        return {"date": date, "kwargs": _readable(kwargs)}

    def as_of(self, ts, **kwargs):
        self.calls.append(("as_of", {"ts": ts, **kwargs}))
        if ts == "boom":
            raise ValueError("malformed")
        return {"ts": ts, "kwargs": _readable(kwargs)}


def _readable(kwargs):
    return {key: value for key, value in kwargs.items()}


def _client(service, *, user="me@example.com", workspace=None):
    def require_user(_request: Request) -> str:
        if user is None:
            raise HTTPException(status_code=401, detail="auth required")
        return user

    def gate_read(_request: Request):
        return workspace

    app = FastAPI()
    app.include_router(
        create_chronicle_router(
            service=service, require_user=require_user, gate_read=gate_read
        )
    )
    return TestClient(app)


@pytest.fixture
def fake():
    return FakeChronicle()


# ── the contract's three paths, and only those ───────────────────────────────


def test_the_router_exposes_the_three_contracted_paths(fake):
    router = create_chronicle_router(
        service=fake, require_user=lambda _r: "u", gate_read=lambda _r: None
    )

    assert {route.path for route in router.routes} == {
        "/api/chronicle/overview",
        "/api/chronicle/day/{date}",
        "/api/chronicle/as-of",
    }
    assert all("GET" in route.methods for route in router.routes)


# ── identity and workspace gating ────────────────────────────────────────────


@pytest.mark.parametrize(
    "path",
    ["/api/chronicle/overview", "/api/chronicle/day/2026-08-01", "/api/chronicle/as-of?ts=2026-08-01"],
)
def test_every_path_refuses_an_unidentified_caller(fake, path):
    response = _client(fake, user=None).get(path)

    assert response.status_code == 401
    assert fake.calls == []


def test_the_reads_carry_the_caller_and_the_gated_workspace(fake):
    client = _client(fake, user="me@example.com", workspace="w1")

    client.get("/api/chronicle/overview")
    client.get("/api/chronicle/day/2026-08-01")
    client.get("/api/chronicle/as-of", params={"ts": "2026-08-01T00:00:00"})

    assert fake.calls == [
        ("overview", {"user_email": "me@example.com", "workspace_id": "w1"}),
        ("day", {"date": "2026-08-01", "user_email": "me@example.com", "workspace_id": "w1"}),
        ("as_of", {"ts": "2026-08-01T00:00:00", "workspace_id": "w1"}),
    ]


def test_an_unscoped_gate_reads_the_whole_machine(fake):
    _client(fake, workspace=None).get("/api/chronicle/overview")

    assert fake.calls[0][1]["workspace_id"] is None


# ── validation ───────────────────────────────────────────────────────────────


def test_a_malformed_date_is_422_in_the_readers_language(fake):
    client = _client(fake)

    korean = client.get("/api/chronicle/day/boom")
    english = client.get("/api/chronicle/day/boom", headers={LANGUAGE_HEADER: "en"})

    assert korean.status_code == english.status_code == 422
    assert korean.json()["detail"] == MESSAGES["chronicle.bad_date"]["ko"]
    assert english.json()["detail"] == MESSAGES["chronicle.bad_date"]["en"]


def test_a_malformed_timestamp_is_422_in_the_readers_language(fake):
    client = _client(fake)

    korean = client.get("/api/chronicle/as-of", params={"ts": "boom"})
    english = client.get(
        "/api/chronicle/as-of", params={"ts": "boom"}, headers={LANGUAGE_HEADER: "en"}
    )

    assert korean.status_code == english.status_code == 422
    assert korean.json()["detail"] == MESSAGES["chronicle.bad_timestamp"]["ko"]
    assert english.json()["detail"] == MESSAGES["chronicle.bad_timestamp"]["en"]


def test_the_rewind_requires_an_instant_to_rewind_to(fake):
    assert _client(fake).get("/api/chronicle/as-of").status_code == 422


def test_an_absurdly_long_timestamp_is_rejected_before_the_service_sees_it(fake):
    response = _client(fake).get("/api/chronicle/as-of", params={"ts": "2" * 500})

    assert response.status_code == 422
    assert fake.calls == []


# ── the real service behind the real router ──────────────────────────────────


@pytest.fixture
def real(tmp_path, monkeypatch):
    monkeypatch.setenv("LATTICE_TZ", "Asia/Seoul")
    db = tmp_path / "knowledge_graph.sqlite"
    store = KnowledgeGraphStore(db, tmp_path / "blobs")
    conversations = ConversationStore(db)
    with store._connect() as conn:
        store._upsert_node(conn, "a", "Concept", "Alpha", "", {})
        store._upsert_node(conn, "b", "Concept", "Beta", "", {})
        store._upsert_edge(conn, "a", "b", "mentions", 1.0, {})
        conn.execute("UPDATE nodes_v2 SET created_at=?, updated_at=?", ("2026-08-01T10:00:00",) * 2)
        conn.execute("UPDATE edges_v2 SET created_at=?", ("2026-08-01T11:00:00",))
        conn.execute("UPDATE edge_occurrences SET observed_at=?", ("2026-08-01T11:00:00",))
    store.record_provenance(
        node_id="a", source_type="upload", title="Report", captured_at="2026-08-01T09:00:00"
    )
    conversations.append(
        {
            "role": "user",
            "content": "What did I save?",
            "timestamp": "2026-08-01T12:00:00",
            "conversation_id": "c1",
            "user_email": "me@example.com",
        }
    )
    return _client(ChronicleService(knowledge_graph=store, conversations=conversations))


def test_the_overview_endpoint_returns_the_growth_of_a_real_brain(real):
    payload = real.get("/api/chronicle/overview").json()

    assert payload["first_activity_at"] == "2026-08-01T09:00:00"
    assert payload["totals"] == {
        "sources": 1,
        "entities": 2,
        "connections": 1,
        "conversations": 1,
    }
    assert payload["series"] == [
        {
            "date": "2026-08-01",
            "sources": 1,
            "entities": 2,
            "connections": 1,
            "conversations": 1,
        }
    ]


def test_the_day_endpoint_returns_the_days_story_from_a_real_brain(real):
    payload = real.get("/api/chronicle/day/2026-08-01").json()

    assert payload["date"] == "2026-08-01"
    assert payload["counts"] == {
        "sources": 1,
        "entities": 2,
        "conversations": 1,
        "changes": 0,
    }
    assert payload["groups"]["sources"][0]["title"] == "Report"
    assert payload["groups"]["conversations"][0]["preview"] == "What did I save?"


def test_a_real_day_with_nothing_in_it_answers_200_with_empty_groups(real):
    payload = real.get("/api/chronicle/day/2026-07-04").json()

    assert payload["counts"] == {"sources": 0, "entities": 0, "conversations": 0, "changes": 0}
    assert payload["groups"]["entities"] == []


def test_an_impossible_date_reaches_the_service_and_comes_back_422(real):
    assert real.get("/api/chronicle/day/2026-13-45").status_code == 422


def test_the_rewind_endpoint_reads_the_graph_through_the_store(real):
    payload = real.get(
        "/api/chronicle/as-of", params={"ts": "2026-08-02T00:00:00+09:00"}
    ).json()

    # The offset is normalized into the configured zone before the store sees it.
    assert payload["ts"] == "2026-08-02T00:00:00"
    assert payload["stats"] == {"entities": 2, "connections": 1}
    assert [item["label"] for item in payload["top_entities"]] == ["Alpha", "Beta"]


def test_the_rewind_before_the_first_memory_is_an_empty_brain(real):
    payload = real.get("/api/chronicle/as-of", params={"ts": "2026-07-01"}).json()

    assert payload["stats"] == {"entities": 0, "connections": 0}
    assert payload["top_entities"] == []
