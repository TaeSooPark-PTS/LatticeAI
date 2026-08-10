"""T8 integration close — the four wiring gaps the v11.2.0 audit found.

Each class of test here exists because a mechanism was *implemented and unit
tested* while no shipped surface reached it. Unit tests that construct the
collaborator directly cannot see that kind of gap, so every test below drives
the production entry point and asserts on what a user would get:

1. cloud-derived knowledge really lands in the Review Center as a proposal,
   driven through ``POST /chat`` with the network dial on ``cloud_allowed``
   (audit Finding 6);
2. ``GET /api/brain/vector-freshness`` carries the pending-backlog split
   without disturbing the four keys the freshness chip reads (Finding 4);
3. ``context_quality`` gains its ``multimodal`` key on a live image-inclusive
   search (Finding 5);
4. an empty Brain no longer grades itself 100 / "excellent" (Finding 3).

Hermetic throughout: fake stores, fake adapters, a policy service pointed at
``tmp_path``, and unique scope keys so the process-wide token budget cannot
leak between tests.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from fastapi import FastAPI
from fastapi.testclient import TestClient

from latticeai.api import chat_hybrid
from latticeai.api.brain_intelligence import create_brain_intelligence_router
from latticeai.api.chat import create_chat_router
from latticeai.api.chat_helpers import build_context_quality
from latticeai.core.context_builder import retrieve_context_for_generation
from latticeai.core.network_boundary import NetworkBoundaryMode
from latticeai.services import hybrid_chat
from latticeai.services.app_context import AppContext
from latticeai.services.brain_intelligence import BrainIntelligenceService
from latticeai.services.hybrid_policy import HybridPolicyService

FRESHNESS_CONTRACT = {"status", "pending_items", "total_items", "detail"}


# ── fakes ────────────────────────────────────────────────────────────────


class _ReviewQueue:
    """The Review Center seam: records what was staged, like the real one."""

    def __init__(self) -> None:
        self.items: List[Dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Dict[str, Any]:
        item = {"id": f"rv-{len(self.items) + 1}", **kwargs}
        self.items.append(item)
        return item


class _CloudStore:
    """Hybrid store that can also accept a write, so auto_commit is visible."""

    def __init__(self) -> None:
        self.written: List[Any] = []

    def hybrid_search(self, query, *, top_k=20, **_kwargs):
        return {
            "mode": "hybrid",
            "matches": [
                {
                    "node_id": "kg-1",
                    "title": "릴리스 절차",
                    "summary": "태그를 만들고 CI를 통과시킨다",
                    "type": "Decision",
                    "score": 0.9,
                    "metadata": {},
                }
            ],
        }

    def upsert_nodes(self, nodes, edges):
        self.written.append((list(nodes), list(edges)))


class _Adapter:
    provider_name = "t8-cloud"
    default_model = "t8-model"

    def stream(self, *, system, user, context, model=None):
        async def _gen():
            yield "Decision: 하이브리드 모드를 먼저 출시한다.\n"
            yield "- [ ] 리뷰 큐 배선 확인\n"

        return _gen()


def _policy(tmp_path: Path, **patch: Any) -> HybridPolicyService:
    service = HybridPolicyService(data_dir=tmp_path)
    if patch:
        service.set_policy(patch)
    return service


def _bind_policy(monkeypatch, service: HybridPolicyService) -> None:
    """Point the api seam at a tmp-dir policy — never the real data dir."""
    monkeypatch.setattr(
        chat_hybrid, "get_hybrid_policy_service", lambda: service
    )


def _payloads(chunks: List[str]) -> List[Dict[str, Any]]:
    out = []
    for chunk in chunks:
        body = chunk[len("data: "):].strip()
        if body != "[DONE]":
            out.append(json.loads(body))
    return out


def _drain_stream(**kwargs: Any) -> List[Dict[str, Any]]:
    async def _run():
        return [chunk async for chunk in hybrid_chat.stream_hybrid_cloud_turn(**kwargs)]

    return _payloads(asyncio.run(_run()))


# ── 1. cloud memory write-back reaches the Review Center ─────────────────


def test_a_cloud_turn_stages_what_it_learned_as_a_review_proposal():
    review = _ReviewQueue()
    store = _CloudStore()

    frames = _drain_stream(
        user_message="릴리스 절차 알려줘",
        knowledge_graph=store,
        mode=NetworkBoundaryMode.CLOUD_ALLOWED,
        adapter=_Adapter(),
        user_email="t8-stage@example.com",
        workspace_id="org:t8",
        review_queue=review,
    )

    done = next(frame for frame in frames if frame["type"] == "hybrid_done")
    expansion = done["kg_expansion"]
    assert expansion["status"] == "queued_for_review"
    assert expansion["review_item_id"] == "rv-1"
    # Nothing was written: a proposal is the whole point.
    assert expansion["written_nodes"] == 0
    assert store.written == []

    staged = review.items[0]
    assert staged["source"] == "change_proposal"
    assert staged["kind"] == "kg_cloud_expansion"
    assert staged["provenance"]["source"] == "hybrid_cloud"
    assert staged["user_email"] == "t8-stage@example.com"
    assert staged["workspace_id"] == "org:t8"
    assert staged["payload"]["auto_commit"] is False
    assert staged["payload"]["plan"]["new_nodes"]


def test_without_a_sink_a_cloud_turn_still_writes_nothing():
    """The pre-11.2.0 shape stays the *safe* one, not the shipped one."""
    store = _CloudStore()

    frames = _drain_stream(
        user_message="릴리스 절차 알려줘",
        knowledge_graph=store,
        mode=NetworkBoundaryMode.CLOUD_ALLOWED,
        adapter=_Adapter(),
        user_email="t8-nosink@example.com",
    )

    expansion = next(f for f in frames if f["type"] == "hybrid_done")["kg_expansion"]
    assert expansion["status"] == "staged"
    assert expansion["review_item_id"] is None
    assert store.written == []


def test_auto_commit_writes_through_and_still_records_the_proposal():
    review = _ReviewQueue()
    store = _CloudStore()

    frames = _drain_stream(
        user_message="릴리스 절차 알려줘",
        knowledge_graph=store,
        mode=NetworkBoundaryMode.CLOUD_ALLOWED,
        adapter=_Adapter(),
        user_email="t8-autocommit@example.com",
        review_queue=review,
        auto_commit=True,
    )

    expansion = next(f for f in frames if f["type"] == "hybrid_done")["kg_expansion"]
    assert expansion["status"] == "accepted"
    assert expansion["review_item_id"] == "rv-1"
    assert expansion["written_nodes"] >= 1
    assert store.written
    assert review.items[0]["payload"]["auto_commit"] is True


def test_the_non_streaming_turn_stages_through_the_same_seam():
    review = _ReviewQueue()

    result = asyncio.run(
        hybrid_chat.run_hybrid_cloud_turn(
            user_message="릴리스 절차 알려줘",
            knowledge_graph=_CloudStore(),
            mode=NetworkBoundaryMode.CLOUD_ALLOWED,
            adapter=_Adapter(),
            user_email="t8-nonstream@example.com",
            review_queue=review,
        )
    )

    assert result.usage["kg_expansion"]["status"] == "queued_for_review"
    assert review.items[0]["source"] == "change_proposal"


# ── 1b. the policy flag finally has a consumer ───────────────────────────


def test_auto_commit_is_read_from_the_scoped_hybrid_policy(tmp_path, monkeypatch):
    _bind_policy(monkeypatch, _policy(tmp_path))
    assert (
        chat_hybrid.resolve_hybrid_auto_commit(
            user_email="t8-policy@example.com", workspace_id=None
        )
        is False
    )

    _bind_policy(monkeypatch, _policy(tmp_path / "on", auto_commit=True))
    assert (
        chat_hybrid.resolve_hybrid_auto_commit(
            user_email="t8-policy@example.com", workspace_id=None
        )
        is True
    )


def test_an_unreadable_policy_is_not_permission_to_write(monkeypatch):
    class _Broken:
        def resolve(self, **_kwargs):
            raise RuntimeError("policy file is corrupt")

    monkeypatch.setattr(chat_hybrid, "get_hybrid_policy_service", lambda: _Broken())

    assert (
        chat_hybrid.resolve_hybrid_auto_commit(user_email=None, workspace_id=None)
        is False
    )


# ── 1c. end to end through POST /chat ────────────────────────────────────


class _ChatRouter:
    current_model_id = "local-default"
    loaded_model_ids = ["local-default"]

    async def generate_as(self, *_args, **_kwargs):
        return "답변입니다"

    async def generate(self, *args, **kwargs):
        return await self.generate_as(*args, **kwargs)


def _chat_client(tmp_path: Path, **overrides: Any) -> TestClient:
    fields: Dict[str, Any] = {
        "config": SimpleNamespace(
            is_public=False, auto_read_chat_paths=False, require_auth=True
        ),
        "model_router": _ChatRouter(),
        "chat_service": SimpleNamespace(
            build_graph_trace=lambda *_a, **_k: {"graph_nodes": []},
            record_trace=lambda **_k: {"id": "trace-t8"},
        ),
        "workspace_store": SimpleNamespace(),
        "workspace_graph": lambda: None,
        "require_user": lambda _request: "t8-route@example.com",
        "enforce_rate_limit": lambda *_a, **_k: None,
        "get_history_user": lambda email, nickname: {
            "user_email": email,
            "user_nickname": nickname,
        },
        "save_to_history": lambda *_a, **_k: None,
        "append_audit_event": lambda *_a, **_k: None,
        "clear_history": lambda *_a, **_k: {"removed": 0, "kept": 0},
        "clear_conversation": lambda *_a, **_k: {"removed": 0, "kept": 0},
        "get_history": lambda **_scope: [],
        "group_history_conversations": lambda entries: [],
        "get_conversation_messages": lambda *_a, **_k: [],
        "conversation_title": lambda _item: "Conversation",
        "allowed_workspaces_for": lambda _user: {"org:t8"},
        "enable_graph": True,
        "knowledge_graph": _CloudStore(),
        "public_model": "",
        "base_dir": tmp_path,
        "data_dir": tmp_path / "data",
    }
    fields.update(overrides)
    app = FastAPI()
    app.include_router(create_chat_router(AppContext(**fields)))
    return TestClient(app)


def _cloud_post(client: TestClient) -> List[Dict[str, Any]]:
    response = client.post(
        "/chat",
        json={
            "message": "릴리스 절차 알려줘",
            "stream": True,
            "conversation_id": "conv-t8",
            "network_mode": "cloud_allowed",
        },
    )
    assert response.status_code == 200
    assert response.headers["x-hybrid"] == "1"
    return _payloads(
        [chunk for chunk in response.text.split("\n\n") if chunk.strip()]
    )


def test_the_chat_route_delivers_cloud_knowledge_to_the_review_center(
    tmp_path, monkeypatch
):
    """The whole point of the row: `/chat` → cloud → Review Center proposal."""
    review = _ReviewQueue()
    monkeypatch.setattr(hybrid_chat, "OpenAICompatibleAdapter", _Adapter)
    _bind_policy(monkeypatch, _policy(tmp_path))

    client = _chat_client(tmp_path, review_queue=lambda: review)
    frames = _cloud_post(client)

    done = next(frame for frame in frames if frame["type"] == "hybrid_done")
    assert done["kg_expansion"]["status"] == "queued_for_review"
    assert review.items[0]["source"] == "change_proposal"
    assert review.items[0]["user_email"] == "t8-route@example.com"


def test_the_chat_route_runs_unchanged_when_no_review_center_is_wired(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(hybrid_chat, "OpenAICompatibleAdapter", _Adapter)
    _bind_policy(monkeypatch, _policy(tmp_path))

    frames = _cloud_post(_chat_client(tmp_path))

    done = next(frame for frame in frames if frame["type"] == "hybrid_done")
    assert done["kg_expansion"]["status"] == "staged"


def test_the_hybrid_seam_forwards_the_sink_and_the_policy_decision(
    tmp_path, monkeypatch
):
    captured: Dict[str, Any] = {}
    review = _ReviewQueue()

    async def fake_turn(**kwargs):
        captured.update(kwargs)
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(chat_hybrid, "stream_hybrid_cloud_turn", fake_turn)
    _bind_policy(monkeypatch, _policy(tmp_path, auto_commit=True))

    response = chat_hybrid.maybe_hybrid_stream_response(
        req=SimpleNamespace(message="질문", source="web"),
        mode=NetworkBoundaryMode.CLOUD_ALLOWED,
        knowledge_graph=_CloudStore(),
        enable_graph=True,
        effective_email="t8-seam@example.com",
        workspace_id="org:t8",
        history_meta={},
        history_user={},
        chat_service=None,
        notify=None,
        model_id="cloud-model",
        review_queue=review,
    )

    assert response is not None
    # The generator body only runs once the response is drained.
    async def _drain():
        return [chunk async for chunk in response.body_iterator]

    assert asyncio.run(_drain()) == ["data: [DONE]\n\n"]
    assert captured["review_queue"] is review
    assert captured["auto_commit"] is True


# ── 2. vector freshness breakdown has a surface ──────────────────────────


class _BreakdownStore:
    def __init__(self, breakdown: Any = None, *, raises: bool = False) -> None:
        self._breakdown = breakdown
        self._raises = raises

    def vector_freshness(self):
        return {
            "status": "pending",
            "pending_items": 12,
            "total_items": 40,
            "detail": "12 of 40 items are missing or stale in the vector index",
        }

    def vector_freshness_breakdown(self):
        if self._raises:
            raise RuntimeError("index locked")
        return self._breakdown


class _NoBreakdownStore:
    def vector_freshness(self):
        return {"status": "ready", "pending_items": 0, "total_items": 3, "detail": "ok"}


def _freshness(kg, *, enable_graph: bool = True) -> Dict[str, Any]:
    return BrainIntelligenceService(
        knowledge_graph=kg, enable_graph=enable_graph
    ).vector_freshness()


def test_freshness_carries_the_split_without_touching_the_chip_contract():
    payload = _freshness(
        _BreakdownStore(
            {
                "status": "pending",
                "detail": "12 of 40 items are missing or stale in the vector index",
                "embedded": 28,
                "pending": 12,
                "missing": 9,
                "stale": 3,
                "total": 40,
                "queued": 2,
            }
        )
    )

    # The four keys the frontend reads are byte-for-byte the old contract.
    assert {key: payload[key] for key in FRESHNESS_CONTRACT} == {
        "status": "pending",
        "pending_items": 12,
        "total_items": 40,
        "detail": "12 of 40 items are missing or stale in the vector index",
    }
    # And the distinction the row promises is finally visible.
    assert payload["breakdown"]["missing"] == 9
    assert payload["breakdown"]["stale"] == 3
    assert payload["breakdown"]["queued"] == 2


def test_a_store_that_cannot_split_its_backlog_reports_no_breakdown():
    assert set(_freshness(_NoBreakdownStore())) == FRESHNESS_CONTRACT


def test_an_unreadable_breakdown_is_omitted_rather_than_zeroed():
    payload = _freshness(_BreakdownStore(raises=True))
    assert set(payload) == FRESHNESS_CONTRACT


def test_a_non_dict_breakdown_is_refused():
    assert set(_freshness(_BreakdownStore(["queued", 2]))) == FRESHNESS_CONTRACT
    assert set(_freshness(_BreakdownStore(None))) == FRESHNESS_CONTRACT


def test_a_disabled_graph_reports_neither_freshness_nor_breakdown():
    payload = _freshness(_BreakdownStore({"queued": 1}), enable_graph=False)
    assert payload["status"] == "unavailable"
    assert set(payload) == FRESHNESS_CONTRACT


def test_the_router_serves_the_breakdown():
    app = FastAPI()
    app.include_router(
        create_brain_intelligence_router(
            service=BrainIntelligenceService(
                knowledge_graph=_BreakdownStore({"queued": 4, "missing": 1})
            ),
            require_user=lambda request: "t8@example.com",
            gate_read=lambda request: None,
            gate_write=lambda request: None,
            append_audit_event=lambda *a, **k: None,
        )
    )

    body = TestClient(app).get("/api/brain/vector-freshness").json()

    assert FRESHNESS_CONTRACT <= set(body)
    assert body["breakdown"] == {"queued": 4, "missing": 1}


# ── 3. multimodal fires on a live image-inclusive search ─────────────────


class _ImageKG:
    """Hybrid store whose answer really rests on pictures."""

    def __init__(self, types=("Image", "ImageText", "Document")) -> None:
        self._types = types

    def hybrid_search(self, query, top_k=6, **_kwargs):
        return {"mode": "hybrid", "matches": self._matches()}

    def search(self, query, limit=6, **_kwargs):
        return {"matches": self._matches()}

    def _matches(self):
        return [
            {"id": f"n{i}", "type": kind, "title": kind, "summary": ""}
            for i, kind in enumerate(self._types)
        ]


class _LexicalImageKG:
    """No hybrid mixin — the lexical arm must report pictures too."""

    def search(self, query, limit=6, **_kwargs):
        return {"matches": [{"id": "n0", "type": "Image", "title": "스크린샷"}]}


def test_chat_context_quality_names_the_pictures_it_leaned_on():
    quality = build_context_quality("스크린샷 뭐였지", knowledge_graph=_ImageKG())

    assert quality["multimodal"] == {"images": 2, "types": ["Image", "ImageText"]}
    assert quality["nodes"] == 3
    assert quality["mode"] == "hybrid"


def test_an_all_text_answer_keeps_the_four_key_shape():
    quality = build_context_quality(
        "배포 절차", knowledge_graph=_ImageKG(types=("Document", "Decision"))
    )

    assert "multimodal" not in quality


def test_the_lexical_arm_reports_pictures_too():
    quality = build_context_quality("스크린샷", knowledge_graph=_LexicalImageKG())

    assert quality["mode"] == "lexical_only"
    assert quality["multimodal"] == {"images": 1, "types": ["Image"]}


class _DocGenKG:
    def __init__(self, types) -> None:
        self._types = types

    def search_for_document_generation(self, query, limit=10, **_kwargs):
        return [
            {"id": f"d{i}", "type": kind, "title": kind, "summary": "본문"}
            for i, kind in enumerate(self._types)
        ]

    def multi_hop_context(self, seed_ids, max_hops=2, **_kwargs):
        return {"nodes": [], "edges": []}


def test_document_generation_reports_a_picture_backed_context():
    result = retrieve_context_for_generation(
        _DocGenKG(("Image", "Document")), "보고서 써줘"
    )

    assert result["context_quality"]["multimodal"] == {"images": 1, "types": ["Image"]}

    text_only = retrieve_context_for_generation(
        _DocGenKG(("Document", "Decision")), "보고서 써줘"
    )
    assert "multimodal" not in text_only["context_quality"]


# ── 4. an empty Brain does not grade itself excellent ────────────────────


class _HealthKG:
    def __init__(self, nodes=(), edges=(), index_status: Optional[Dict] = None) -> None:
        self._nodes = list(nodes)
        self._edges = list(edges)
        self._index_status = index_status

    def graph(self, limit, **_kwargs):
        return {"nodes": self._nodes, "edges": self._edges}

    def index_status(self):
        if self._index_status is None:
            raise RuntimeError("no vector index")
        return self._index_status


_EMPTY_INDEX = {"status": "ready", "scale": {"coverage_ratio": 1.0, "source_items": 0}}
_FULL_INDEX = {
    "status": "ready",
    "scale": {
        "coverage_ratio": 1.0,
        "source_items": 2,
        "ready_items": 2,
        "pending_items": 0,
    },
}


def _health(kg, *, enable_graph: bool = True) -> Dict[str, Any]:
    return BrainIntelligenceService(
        knowledge_graph=kg, enable_graph=enable_graph
    ).health_report()


def test_a_brand_new_brain_reports_no_grade_and_says_why():
    report = _health(_HealthKG(index_status=_EMPTY_INDEX))

    assert report["overall_score"] is None
    assert report["grade"] is None
    assert report["coverage"] == {
        "measured": 0,
        "total": 4,
        "unavailable": [
            "connectivity",
            "consistency",
            "embedding_coverage",
            "freshness",
        ],
        "partial": True,
    }
    assert "no health dimension could be measured yet" in report["reason"]
    assert "no indexable items yet" in report["reason"]
    assert "no knowledge saved yet" in report["reason"]
    assert report["dimensions"]["embedding_coverage"]["score"] is None


def test_an_unreadable_graph_says_so_instead_of_claiming_emptiness():
    report = _health(None, enable_graph=False)

    assert report["overall_score"] is None
    assert (
        report["dimensions"]["freshness"]["reason"]
        == "the knowledge graph could not be read"
    )
    assert (
        report["dimensions"]["embedding_coverage"]["reason"]
        == "this knowledge store does not report vector index coverage"
    )


def test_a_store_without_coverage_reporting_names_that_gap():
    nodes = [{"id": "a", "type": "Document", "title": "A", "updated_at": _now_iso()}]
    report = _health(_HealthKG(nodes=nodes))

    assert (
        report["dimensions"]["embedding_coverage"]["reason"]
        == "this knowledge store does not report vector index coverage"
    )
    assert report["coverage"]["measured"] == 2
    assert report["coverage"]["partial"] is True


def test_a_graph_with_no_relationships_yet_says_that_much():
    nodes = [
        {"id": "a", "type": "Document", "title": "A", "updated_at": _now_iso()},
        {"id": "b", "type": "Decision", "title": "B", "updated_at": _now_iso()},
    ]
    report = _health(_HealthKG(nodes=nodes, index_status=_FULL_INDEX))

    assert report["dimensions"]["consistency"]["reason"] == (
        "no relationships recorded yet"
    )
    assert report["coverage"]["measured"] == 3
    assert "reason" not in report


def test_a_fully_measurable_brain_still_grades_itself():
    nodes = [
        {"id": "a", "type": "Document", "title": "A", "updated_at": _now_iso()},
        {"id": "b", "type": "Decision", "title": "B", "updated_at": _now_iso()},
    ]
    edges = [
        {
            "id": "e1",
            "from": "a",
            "to": "b",
            "type": "MENTIONS",
            "confidence": 0.9,
            "evidence": [],
        }
    ]
    report = _health(_HealthKG(nodes=nodes, edges=edges, index_status=_FULL_INDEX))

    assert report["grade"] == "excellent"
    assert report["overall_score"] == 100
    assert report["coverage"] == {
        "measured": 4,
        "total": 4,
        "unavailable": [],
        "partial": False,
    }


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
