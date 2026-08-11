"""The embed queue finally has a trigger (v11.5.0, plan §3a).

``vector_jobs`` has been durable since 11.1.0 and unreachable from outside the
process the whole time: FEATURE_STATUS.md:179-185 says "nothing in the server
drives the background embed queue yet", and it was right — the only drain was
``IngestionPipeline.drain_vector_queue``, which no HTTP path called.

So the load-bearing test here is not the router's plumbing but the scenario:
ingest something whose inline embedding fails, POST the new endpoint, and watch
the backlog actually go to zero against a real store on tmp_path. The fakes
around it pin what the real store cannot show — that the caller is identified
and workspace-gated before anything drains, that a nonsense limit is a 422 in
the reader's own language rather than an unbounded run, and that a Brain with
ingestion switched off answers 503 instead of 500.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lattice_brain.graph.store import KnowledgeGraphStore  # noqa: E402
from lattice_brain.graph.vector_index import DEFAULT_TICK_LIMIT  # noqa: E402
from lattice_brain.ingestion import IngestionItem, IngestionPipeline  # noqa: E402
from latticeai.api.index_jobs import (  # noqa: E402
    MAX_DRAIN_LIMIT,
    MIN_DRAIN_LIMIT,
    create_index_jobs_router,
)
from latticeai.core.messages import LANGUAGE_HEADER, MESSAGES  # noqa: E402

# ── harness ──────────────────────────────────────────────────────────────────


class FakePipeline:
    """Records the limit each drain asked for; answers with a marker tick."""

    def __init__(self, *, available: bool = True) -> None:
        self._available = available
        self.limits: list[int] = []

    def available(self) -> bool:
        return self._available

    def drain_vector_queue(self, limit):
        self.limits.append(limit)
        return {
            "claimed": 2,
            "indexed": 1,
            "retried": 1,
            "failed": 0,
            "detail": None,
        }


class FakeQueue:
    """A stand-in for ``KnowledgeGraphStore.vector_queue``."""

    def __init__(self, *, available: bool = True, counts=None) -> None:
        self.available = available
        self._counts = counts or {"pending": 3, "running": 1, "done": 7, "failed": 2}

    def snapshot(self):
        return dict(self._counts)

    def pending_count(self):
        return int(self._counts["pending"]) + int(self._counts["running"])


class FakeGraph:
    def __init__(self, queue) -> None:
        self.vector_queue = queue


def _client(pipeline, graph=None, *, user="me@example.com", scope=None, denied=None):
    """The router wired to whatever the test wants to observe.

    ``denied`` names the gate ("read"/"write") that refuses this caller, which
    is how a workspace mismatch reaches the router in production.
    """

    def require_user(_request: Request) -> str:
        if user is None:
            raise HTTPException(status_code=401, detail="auth required")
        return user

    def gate(kind):
        def _gate(_request: Request):
            if denied == kind:
                raise HTTPException(status_code=403, detail="wrong workspace")
            return scope

        return _gate

    app = FastAPI()
    app.include_router(
        create_index_jobs_router(
            pipeline=pipeline,
            knowledge_graph=graph,
            require_user=require_user,
            gate_read=gate("read"),
            gate_write=gate("write"),
        )
    )
    return TestClient(app)


@pytest.fixture
def fake():
    return FakePipeline()


# ── the contract's two paths, and only those ─────────────────────────────────


def test_the_router_exposes_the_two_contracted_paths(fake):
    router = create_index_jobs_router(
        pipeline=fake,
        knowledge_graph=None,
        require_user=lambda _r: "u",
        gate_read=lambda _r: None,
        gate_write=lambda _r: None,
    )

    assert {(route.path, tuple(sorted(route.methods))) for route in router.routes} == {
        ("/api/index/drain", ("POST",)),
        ("/api/index/queue", ("GET",)),
    }


# ── identity and workspace gating ────────────────────────────────────────────


def test_an_unidentified_caller_drains_nothing(fake):
    client = _client(fake, user=None)

    assert client.post("/api/index/drain", json={}).status_code == 401
    assert client.get("/api/index/queue").status_code == 401
    assert fake.limits == []


def test_the_drain_is_refused_by_the_write_gate_before_it_runs(fake):
    response = _client(fake, denied="write").post("/api/index/drain", json={})

    assert response.status_code == 403
    assert fake.limits == []


def test_the_read_gate_guards_the_counts(fake):
    assert _client(fake, denied="read").get("/api/index/queue").status_code == 403


def test_a_scoped_caller_still_drains_the_whole_machine(fake):
    payload = _client(fake, FakeGraph(FakeQueue()), scope="w1").post(
        "/api/index/drain", json={}
    ).json()

    # The gate authorizes; it does not narrow the backlog, and the payload says
    # so rather than letting a workspace member read the number as theirs.
    assert payload["scope"] == "machine"
    assert payload["queue"]["counts"]["pending"] == 3


# ── the limit ────────────────────────────────────────────────────────────────


def test_a_body_less_post_uses_the_queues_own_tick_size(fake):
    assert _client(fake).post("/api/index/drain").status_code == 200
    assert fake.limits == [DEFAULT_TICK_LIMIT]


def test_an_empty_body_uses_the_queues_own_tick_size(fake):
    _client(fake).post("/api/index/drain", json={})

    assert fake.limits == [DEFAULT_TICK_LIMIT]


def test_an_explicit_limit_reaches_the_queue_verbatim(fake):
    client = _client(fake)

    client.post("/api/index/drain", json={"limit": MIN_DRAIN_LIMIT})
    client.post("/api/index/drain", json={"limit": MAX_DRAIN_LIMIT})

    assert fake.limits == [MIN_DRAIN_LIMIT, MAX_DRAIN_LIMIT]


@pytest.mark.parametrize("limit", [0, -1, MAX_DRAIN_LIMIT + 1, 10_000])
def test_a_limit_outside_the_bounds_is_422_and_drains_nothing(fake, limit):
    response = _client(fake).post("/api/index/drain", json={"limit": limit})

    assert response.status_code == 422
    assert fake.limits == []


def test_the_bounds_are_explained_in_the_readers_language(fake):
    client = _client(fake)

    korean = client.post("/api/index/drain", json={"limit": 0})
    english = client.post(
        "/api/index/drain", json={"limit": 0}, headers={LANGUAGE_HEADER: "en"}
    )

    expected = {
        language: MESSAGES["index.limit_out_of_range"][language]
        .replace("{min}", str(MIN_DRAIN_LIMIT))
        .replace("{max}", str(MAX_DRAIN_LIMIT))
        for language in ("ko", "en")
    }
    assert korean.json()["detail"] == expected["ko"]
    assert english.json()["detail"] == expected["en"]
    assert "1" in expected["en"] and "100" in expected["en"]


def test_a_limit_that_is_not_a_number_never_reaches_the_queue(fake):
    response = _client(fake).post("/api/index/drain", json={"limit": "lots"})

    assert response.status_code == 422
    assert fake.limits == []


# ── ingestion switched off ───────────────────────────────────────────────────


@pytest.mark.parametrize("pipeline", [None, FakePipeline(available=False)])
def test_a_brain_without_ingestion_answers_503_on_both_paths(pipeline):
    client = _client(pipeline)

    drain = client.post("/api/index/drain", json={})
    queue = client.get("/api/index/queue")

    assert drain.status_code == queue.status_code == 503
    assert drain.json()["detail"] == MESSAGES["capture.ingestion_disabled"]["ko"]


def test_the_503_is_localized_too():
    response = _client(None).get("/api/index/queue", headers={LANGUAGE_HEADER: "en"})

    assert response.json()["detail"] == MESSAGES["capture.ingestion_disabled"]["en"]


# ── the counts are honest about what is counting ─────────────────────────────


def test_a_store_with_a_queue_reports_its_counts(fake):
    payload = _client(fake, FakeGraph(FakeQueue())).get("/api/index/queue").json()

    assert payload == {
        "available": True,
        "counts": {"pending": 3, "running": 1, "done": 7, "failed": 2},
        "pending": 4,
    }


@pytest.mark.parametrize(
    "graph",
    [None, FakeGraph(None), FakeGraph(FakeQueue(available=False))],
    ids=["no-graph", "no-queue", "queue-without-a-database"],
)
def test_a_brain_that_counts_nothing_says_so_instead_of_reporting_zero(fake, graph):
    payload = _client(fake, graph).get("/api/index/queue").json()

    assert payload["available"] is False
    assert payload["pending"] == 0
    assert payload["counts"] == {"pending": 0, "running": 0, "done": 0, "failed": 0}


def test_the_drain_reports_the_tick_verbatim_plus_the_backlog_after_it(fake):
    payload = _client(fake, FakeGraph(FakeQueue())).post(
        "/api/index/drain", json={"limit": 5}
    ).json()

    assert payload["claimed"] == 2
    assert payload["indexed"] == 1
    assert payload["retried"] == 1
    assert payload["failed"] == 0
    assert payload["detail"] is None
    assert payload["limit"] == 5
    assert payload["queue"]["pending"] == 4


# ── the real thing: a real store, a real backlog, a real drain ───────────────


@pytest.fixture
def real(tmp_path):
    """A Brain that owes one embedding, and the router over it."""
    store = KnowledgeGraphStore(tmp_path / "knowledge_graph.sqlite", tmp_path / "blobs")
    real_index = store.index_node_incremental
    attempts: list[str] = []

    def _flaky(node_id):
        attempts.append(node_id)
        if len(attempts) == 1:
            raise RuntimeError("embedding provider offline")
        return real_index(node_id)

    store.index_node_incremental = _flaky
    pipeline = IngestionPipeline(store)
    result = pipeline.ingest(
        IngestionItem(
            source_type="note",
            title="Deferred Note",
            text="Background embedding keeps ingested content searchable.",
        )
    )
    assert result.indexing_status == "pending"
    return store, _client(pipeline, store)


def test_the_backlog_is_visible_before_anyone_drains_it(real):
    store, client = real

    payload = client.get("/api/index/queue").json()

    assert payload["available"] is True
    assert payload["counts"]["pending"] == 1
    assert payload["pending"] == 1
    assert store.vector_freshness_breakdown()["queued"] == 1


def test_one_post_embeds_the_backlog_and_the_content_becomes_findable(real):
    store, client = real

    payload = client.post("/api/index/drain", json={"limit": 10}).json()

    assert (payload["claimed"], payload["indexed"], payload["failed"]) == (1, 1, 0)
    assert payload["limit"] == 10
    assert payload["queue"] == {
        "available": True,
        "counts": {"pending": 0, "running": 0, "done": 1, "failed": 0},
        "pending": 0,
    }
    assert store.vector_search("background embedding searchable")["matches"]


def test_draining_an_empty_queue_is_a_no_op_that_still_answers(real):
    _store, client = real
    client.post("/api/index/drain", json={})

    payload = client.post("/api/index/drain", json={}).json()

    assert (payload["claimed"], payload["indexed"], payload["retried"], payload["failed"]) == (
        0,
        0,
        0,
        0,
    )
    assert payload["queue"]["pending"] == 0


def test_a_real_store_with_no_queue_at_all_drains_nothing_and_says_why(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(KnowledgeGraphStore, "vector_queue", None)
    store = KnowledgeGraphStore(tmp_path / "knowledge_graph.sqlite", tmp_path / "blobs")
    client = _client(IngestionPipeline(store), store)

    payload = client.post("/api/index/drain", json={}).json()

    assert payload["claimed"] == 0
    assert "no background vector queue" in payload["detail"]
    assert payload["queue"]["available"] is False
