"""Brain Intelligence proactive surface (v11.1.0 Track 2).

Wires the synthesis loop into the product: the Brain Brief's proactive section,
the synthesis/propose/resolve endpoints, and the honest "the Brain is tidying
up" signal on the quality report. Every method here degrades to
``available: false`` when the review queue is missing — it never falls back to
writing the graph directly.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from lattice_brain.synthesis import CONTRADICTION_KIND, SYNTHESIS_REVIEW_SOURCE
from latticeai.api.brain_intelligence import create_brain_intelligence_router
from latticeai.core.workspace_os import WorkspaceOSStore
from latticeai.services.brain_intelligence import BrainIntelligenceService
from latticeai.services.review_queue import ReviewQueueService
from tests.unit.test_t2_support import RecordingReviewQueue, make_store, seed

PAIR = [
    ("n-old", "Concept", "coffee ritual", "I like coffee before the design review"),
    ("n-new", "Concept", "coffee ritual", "I do not like coffee before the design review"),
]


def _service(tmp_path, *, queue=None, enable_graph=True, memory_service=None):
    store = make_store(tmp_path)
    seed(store, PAIR)
    service = BrainIntelligenceService(
        knowledge_graph=store,
        memory_service=memory_service,
        enable_graph=enable_graph,
        review_queue=queue,
    )
    return store, service


class _MemoryWithStore:
    """Stands in for MemoryService: the only thing read is its workspace store."""

    def __init__(self, store):
        self._store = store

    def inspect(self, *_args, **_kwargs):
        return {"items": []}


# ── the review queue seam ────────────────────────────────────────────────────


def test_without_a_review_queue_every_write_path_reports_unavailable(tmp_path):
    _store, service = _service(tmp_path)
    for result in (
        service.synthesize(),
        service.propose_contradictions(),
        service.resolve_contradiction("anything", resolution="replace"),
    ):
        assert result["available"] is False
        assert "review queue" in result["detail"]
    brief = service.proactive_brief()
    assert brief["available"] is False and brief["pending"]["total"] == 0


def test_the_queue_is_derived_from_the_memory_service_store(tmp_path):
    workspace = WorkspaceOSStore(tmp_path / "workspace")
    _store, service = _service(tmp_path, memory_service=_MemoryWithStore(workspace))
    result = service.propose_contradictions(user_email="a@b.c")

    assert result["proposed_count"] >= 1
    items = ReviewQueueService(store=workspace).list(source=SYNTHESIS_REVIEW_SOURCE)
    assert items["items"] and items["items"][0]["kind"] == CONTRADICTION_KIND


def test_a_memory_service_without_a_usable_store_is_not_mistaken_for_a_queue(tmp_path):
    class _Bare:
        _store = object()

    _store, service = _service(tmp_path, memory_service=_Bare())
    assert service.propose_contradictions()["available"] is False


def test_an_explicitly_injected_queue_wins(tmp_path):
    queue = RecordingReviewQueue()
    _store, service = _service(tmp_path, queue=queue)
    assert service.propose_contradictions()["proposed_count"] >= 1
    assert queue.created


# ── synthesis ────────────────────────────────────────────────────────────────


def test_synthesize_runs_and_proposes(tmp_path):
    queue = RecordingReviewQueue()
    _store, service = _service(tmp_path, queue=queue)
    result = service.synthesize(user_email="a@b.c")
    assert result["available"] is True
    assert result["proposed_total"] == len(queue.created)
    assert queue.approved == []


def test_a_failed_synthesis_pass_is_reported_not_raised(tmp_path):
    class _BrokenGraph:
        def graph(self, *_args, **_kwargs):
            raise RuntimeError("graph is on fire")

    service = BrainIntelligenceService(
        knowledge_graph=_BrokenGraph(), review_queue=RecordingReviewQueue()
    )
    result = service.synthesize()
    assert result["available"] is False and "on fire" in result["error"]


def test_a_failed_proposal_pass_is_reported_not_raised(tmp_path):
    class _BrokenGraph:
        def graph(self, *_args, **_kwargs):
            raise RuntimeError("graph is on fire")

    service = BrainIntelligenceService(
        knowledge_graph=_BrokenGraph(), review_queue=RecordingReviewQueue()
    )
    result = service.propose_contradictions()
    assert result["available"] is False and "on fire" in result["error"]


def test_disabling_the_graph_disables_synthesis(tmp_path):
    _store, service = _service(
        tmp_path, queue=RecordingReviewQueue(), enable_graph=False
    )
    assert service.synthesize()["available"] is False
    assert service.propose_contradictions()["available"] is False


# ── the ingest trigger seam ──────────────────────────────────────────────────


def test_note_ingest_runs_only_once_the_threshold_is_reached(tmp_path, monkeypatch):
    monkeypatch.setenv("LATTICEAI_SYNTHESIS_THRESHOLD", "2")
    queue = RecordingReviewQueue()
    _store, service = _service(tmp_path, queue=queue)

    assert service.note_ingest({"status": "ok"}) is None
    assert queue.created == []
    fired = service.note_ingest({"status": "ok"})
    assert fired is not None and fired["proposed_total"] >= 1


def test_note_ingest_is_inert_without_a_queue_and_never_raises(tmp_path):
    _store, service = _service(tmp_path)
    assert service.note_ingest({"status": "ok"}) is None

    queue = RecordingReviewQueue()
    _store2, working = _service(tmp_path, queue=queue)

    class _Boom:
        def run_if_due(self, *_args, **_kwargs):
            raise RuntimeError("synthesis exploded")

    working._synthesizer = _Boom()
    assert working.note_ingest({"status": "ok"}) is None


# ── the Brain Brief proactive section ────────────────────────────────────────


def test_the_proactive_section_counts_what_is_waiting_and_writes_nothing(tmp_path):
    queue = RecordingReviewQueue()
    store, service = _service(tmp_path, queue=queue)
    service.propose_contradictions()
    created = len(queue.created)

    section = service.proactive_brief()

    assert section["available"] is True
    assert section["pending"]["total"] == created
    assert section["pending"]["by_kind"][CONTRADICTION_KIND] >= 1
    assert section["headline"].startswith("최근 7일")
    assert len(section["lines"]) == 4
    assert len(queue.created) == created  # reading the brief proposes nothing


def test_the_section_says_when_the_brain_is_tidying_up(tmp_path):
    queue = RecordingReviewQueue()
    _store, service = _service(tmp_path, queue=queue)
    assert service.proactive_brief()["tidying"] is False
    queue.create(title="정리", kind="consolidation", payload={})
    assert service.proactive_brief()["tidying"] is True


def test_the_section_degrades_when_the_inbox_or_the_brief_fails(tmp_path):
    _store, service = _service(tmp_path, queue=RecordingReviewQueue(fail_list=True))
    section = service.proactive_brief()
    assert section["available"] is True and section["pending"]["total"] == 0

    queue = RecordingReviewQueue()
    _store2, other = _service(tmp_path, queue=queue)

    class _Boom:
        def brief_section(self, **_kwargs):
            raise RuntimeError("brief exploded")

    other._synthesizer = _Boom()
    assert other.proactive_brief()["headline"] == ""


def test_the_section_still_reports_pending_work_without_a_graph(tmp_path):
    queue = RecordingReviewQueue()
    service = BrainIntelligenceService(
        knowledge_graph=None, enable_graph=False, review_queue=queue
    )
    queue.create(title="waiting", kind=CONTRADICTION_KIND, payload={})
    section = service.proactive_brief()
    assert section["available"] is True
    assert section["pending"]["total"] == 1 and section["headline"] == ""


# ── importance on the quality report ─────────────────────────────────────────


def test_the_quality_report_carries_the_decay_signal(tmp_path):
    store = make_store(tmp_path)
    seed(store, [("c1", "Chat", "old standup", "we talked about the release")])
    service = BrainIntelligenceService(knowledge_graph=store)
    report = service.quality_report()
    assert report["available"] is True
    assert report["importance"]["available"] is True
    assert report["summary"]["consolidation_candidates"] == 1
    assert report["tidying"] is True


def test_importance_report_degrades_honestly(tmp_path):
    class _BrokenGraph:
        def graph(self, *_args, **_kwargs):
            raise RuntimeError("nope")

    service = BrainIntelligenceService(knowledge_graph=_BrokenGraph())
    result = service.importance_report()
    assert result["available"] is False and result["candidates"] == []

    off = BrainIntelligenceService(knowledge_graph=None, enable_graph=False)
    assert off.importance_report()["available"] is False


# ── router ───────────────────────────────────────────────────────────────────


def _client(service) -> TestClient:
    app = FastAPI()
    app.include_router(
        create_brain_intelligence_router(
            service=service,
            require_user=lambda request: "owner@example.com",
            gate_read=lambda request: None,
            gate_write=lambda request: None,
            append_audit_event=lambda *a, **k: None,
        )
    )
    return TestClient(app)


def test_router_exposes_the_proactive_surface(tmp_path):
    queue = RecordingReviewQueue()
    _store, service = _service(tmp_path, queue=queue)
    client = _client(service)

    assert client.get("/api/brain/proactive-brief").json()["available"] is True
    assert client.get("/api/brain/importance").json()["available"] is True

    synthesized = client.post("/api/brain/synthesize").json()
    assert synthesized["proposed_total"] == len(queue.created)

    proposals = client.post("/api/brain/contradictions/propose").json()
    assert proposals["suppressed"] >= 1  # synthesis already raised them


def test_router_resolves_a_contradiction_through_approval(tmp_path):
    queue = RecordingReviewQueue()
    store, service = _service(tmp_path, queue=queue)
    client = _client(service)
    client.post("/api/brain/contradictions/propose")
    item = next(i for i in queue.created if i["kind"] == CONTRADICTION_KIND)

    response = client.post(
        "/api/brain/contradictions/resolve",
        json={"item_id": item["id"], "resolution": "replace"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "approved"
    assert queue.approved == [item["id"]]
    with store._connect() as conn:
        superseded = conn.execute(
            "SELECT COUNT(*) FROM nodes_v2 WHERE superseded_by IS NOT NULL"
        ).fetchone()[0]
    assert superseded == 1


def test_router_rejects_a_bad_resolution_and_a_missing_item(tmp_path):
    queue = RecordingReviewQueue()
    _store, service = _service(tmp_path, queue=queue)
    client = _client(service)
    queue.create(title="x", kind=CONTRADICTION_KIND, payload={})

    bad = client.post(
        "/api/brain/contradictions/resolve",
        json={"item_id": "review-1", "resolution": "nope"},
    )
    assert bad.status_code == 400

    class _Missing(RecordingReviewQueue):
        def get(self, item_id, **_kwargs):
            raise FileNotFoundError(item_id)

    _store2, other = _service(tmp_path, queue=_Missing())
    missing = _client(other).post(
        "/api/brain/contradictions/resolve",
        json={"item_id": "ghost", "resolution": "replace"},
    )
    assert missing.status_code == 404
