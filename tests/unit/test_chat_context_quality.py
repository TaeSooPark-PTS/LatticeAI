"""RAG honest-signaling (context_quality, v9.8.0) tests.

Covers: the shared ``context_quality_signal`` shape, the additive
``context_for_query`` meta path (``with_meta`` /
``context_for_query_with_meta``) with byte-identical default context, the
chat-layer ``build_context_quality`` helper over hybrid / lexical-only /
missing / failing stores, and the streaming trailer carrying the signal on
the same meta event as the answer trace.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lattice_brain.graph.retrieval import context_quality_signal
from lattice_brain.graph.store import KnowledgeGraphStore
from latticeai.api.chat_helpers import build_context_quality
from latticeai.api.chat_stream import stream_chat

QUALITY_KEYS = {"mode", "nodes", "limited", "reason"}


def _assert_quality(quality):
    assert set(quality) == QUALITY_KEYS
    assert quality["mode"] in {"hybrid", "lexical_only", "none"}
    assert isinstance(quality["nodes"], int)
    assert isinstance(quality["limited"], bool)
    assert quality["reason"] is None or isinstance(quality["reason"], str)


def _store(tmp_path: Path) -> KnowledgeGraphStore:
    store = KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")
    store.ingest_source(
        source_type="note",
        title="Hybrid Retrieval Design",
        text=(
            "Hybrid retrieval fuses lexical keyword matching with vector "
            "cosine similarity for grounded chat answers."
        ),
        source_uri="note:hybrid-design",
    )
    store.ingest_source(
        source_type="note",
        title="Vector Index Operations",
        text="The vector index is rebuilt incrementally after ingestion.",
        source_uri="note:vector-ops",
    )
    return store


# ── context_quality_signal shape ─────────────────────────────────────────


def test_signal_zero_nodes_collapse_to_none_mode():
    quality = context_quality_signal("hybrid", 0)
    _assert_quality(quality)
    assert quality["mode"] == "none"
    assert quality["limited"] is True
    assert quality["reason"]


def test_signal_lexical_fallback_is_limited_with_reason():
    quality = context_quality_signal("lexical_only", 4)
    _assert_quality(quality)
    assert quality == {
        "mode": "lexical_only",
        "nodes": 4,
        "limited": True,
        "reason": "벡터 검색을 사용할 수 없어 키워드 검색 결과만 사용했습니다",
    }


def test_signal_single_hybrid_node_is_limited():
    quality = context_quality_signal("hybrid", 1)
    assert quality["limited"] is True
    assert quality["reason"] == "그래프 기반 컨텍스트가 제한적입니다"


def test_signal_rich_hybrid_context_is_not_limited():
    quality = context_quality_signal("hybrid", 5)
    assert quality == {"mode": "hybrid", "nodes": 5, "limited": False, "reason": None}


# ── context_for_query meta path (graph layer) ────────────────────────────


def test_context_for_query_with_meta_reports_hybrid(tmp_path):
    store = _store(tmp_path)
    query = "hybrid retrieval vector similarity"

    result = store.context_for_query_with_meta(query)

    assert set(result) == {"context", "quality"}
    _assert_quality(result["quality"])
    assert result["quality"]["mode"] == "hybrid"
    assert result["quality"]["nodes"] >= 1
    # Byte-identical context vs the legacy string return for the same args.
    assert result["context"] == store.context_for_query(query, use_hybrid=True)


def test_context_for_query_default_return_stays_a_string(tmp_path):
    store = _store(tmp_path)
    legacy = store.context_for_query("hybrid retrieval")
    assert isinstance(legacy, str)
    meta = store.context_for_query("hybrid retrieval", with_meta=True)
    assert meta["context"] == legacy
    assert meta["quality"]["mode"] == "lexical_only"  # default path skips vectors


def test_context_for_query_with_meta_reports_vector_fallback(tmp_path):
    store = _store(tmp_path)

    def boom(*args, **kwargs):
        raise RuntimeError("vector index offline")

    store.vector_search = boom
    result = store.context_for_query_with_meta("hybrid retrieval vector similarity")
    _assert_quality(result["quality"])
    assert result["quality"]["mode"] == "lexical_only"
    assert result["quality"]["limited"] is True
    assert result["context"]


def test_context_for_query_with_meta_empty_and_unmatched_queries(tmp_path):
    store = _store(tmp_path)

    empty = store.context_for_query_with_meta("")
    assert empty["context"] == ""
    assert empty["quality"]["mode"] == "none"

    # Brute-force cosine over deterministic embeddings scores every indexed
    # item, so a truly matchless outcome needs the vector side disabled.
    def boom(*args, **kwargs):
        raise RuntimeError("vector index offline")

    store.vector_search = boom
    unmatched = store.context_for_query_with_meta("zzz qqq xyzzy nothing here")
    assert unmatched["quality"]["mode"] == "none"
    assert unmatched["quality"]["nodes"] == 0
    assert unmatched["quality"]["limited"] is True


# ── chat-layer helper ────────────────────────────────────────────────────


class _HybridKG:
    def __init__(self, mode="hybrid", count=3):
        self._mode = mode
        self._count = count
        self.seen_kwargs = None

    def hybrid_search(self, query, *, top_k=6, allowed_workspaces=None):
        self.seen_kwargs = {"top_k": top_k, "allowed_workspaces": allowed_workspaces}
        return {
            "mode": self._mode,
            "matches": [{"node_id": f"n{i}"} for i in range(self._count)],
        }


class _LexicalOnlyKG:
    def search(self, query, limit=30, **kwargs):
        return {"matches": [{"id": "a"}, {"id": "b"}]}


class _FailingKG:
    def hybrid_search(self, query, **kwargs):
        raise RuntimeError("search backend down")


def test_build_context_quality_hybrid_store():
    kg = _HybridKG(count=4)
    quality = build_context_quality(
        "what do we know", knowledge_graph=kg, allowed_workspaces={"ws1"}
    )
    _assert_quality(quality)
    assert quality == {"mode": "hybrid", "nodes": 4, "limited": False, "reason": None}
    assert kg.seen_kwargs["allowed_workspaces"] == {"ws1"}


def test_build_context_quality_reports_lexical_fallback():
    quality = build_context_quality(
        "query", knowledge_graph=_HybridKG(mode="lexical_only", count=3)
    )
    assert quality["mode"] == "lexical_only"
    assert quality["limited"] is True
    assert quality["reason"]


def test_build_context_quality_without_graph_is_none_mode():
    quality = build_context_quality("query", knowledge_graph=None)
    _assert_quality(quality)
    assert quality["mode"] == "none"
    assert quality["limited"] is True


def test_build_context_quality_lexical_store_without_hybrid_mixin():
    quality = build_context_quality("query", knowledge_graph=_LexicalOnlyKG())
    assert quality["mode"] == "lexical_only"
    assert quality["nodes"] == 2


def test_build_context_quality_search_failure_never_raises():
    quality = build_context_quality("query", knowledge_graph=_FailingKG())
    _assert_quality(quality)
    assert quality["mode"] == "none"
    assert quality["reason"] == "그래프 검색에 실패했습니다"


# ── streaming trailer channel ────────────────────────────────────────────


class _FakeRouter:
    current_model_id = "m1"

    async def stream_generate_as(self, model_id, message, context, max_tokens, temperature, image):
        yield "hello "
        yield "world"


class _FakeChatService:
    def build_graph_trace(self, *args, **kwargs):
        return {}

    async def persist_answer(self, **kwargs):
        return {"id": "trace-1", **kwargs["trace"]}


def _collect_stream(**overrides):
    quality = overrides.pop(
        "context_quality",
        {"mode": "lexical_only", "nodes": 1, "limited": True, "reason": "제한적"},
    )
    req = SimpleNamespace(
        message="question",
        conversation_id="c1",
        user_email="owner@example.com",
        user_nickname=None,
        source="web",
        max_tokens=64,
        temperature=0.2,
    )
    trace_seed = {"context_quality": quality}

    async def run():
        events = []
        async for line in stream_chat(
            req,
            "ctx",
            None,
            router=_FakeRouter(),
            chat_service=_FakeChatService(),
            knowledge_graph=None,
            enable_graph=False,
            notify=None,
            trace_seed=trace_seed,
            effective_email="owner@example.com",
            history_meta={},
            model_id="m1",
            workspace_id=None,
            context_quality=quality,
        ):
            events.append(line)
        return events

    return asyncio.run(run()), quality


def test_stream_trailer_carries_context_quality_with_trace():
    events, quality = _collect_stream()
    assert events[-1] == "data: [DONE]\n\n"
    trailer = json.loads(events[-2][len("data: "):])
    # Same meta event as the answer trace (existing evidence channel).
    assert trailer["trace_id"] == "trace-1"
    assert trailer["context_quality"] == quality
    assert trailer["trace"]["context_quality"] == quality
