#!/usr/bin/env python3
"""The document-generation half of the Python↔Rust parity corpus (v11.5.1).

``scripts/generate_rust_parity_fixtures.py`` owns the store and the goldens;
this module owns the rows and specs the **document-generation** ports need, so
neither file has to grow past the tree's file-size ceiling. Nothing here runs
anything — it is data plus one normalization helper.

Three things are shaped on purpose:

* **Every allow-listed node type appears.** ``search_for_document_generation``
  admits fifteen types and boosts four of them; a corpus that only carried
  ``Document`` would prove the boost and the filter equally badly.
* **The Self-Model subgraph is real.** ``summary_for_prompt`` reads the legacy
  ``nodes`` table for ``self:%`` ids, so the profile rows are written through
  the same write door as everything else, including the root the read excludes
  and a ``self:`` row whose metadata carries no recognised kind (dropped).
* **``updated_at`` is spread against the frozen clock**, including one stamp
  that does not parse — the recency term's ``0.0`` branch is otherwise
  unreachable from a fixture whose every row is well formed.

:func:`normalize_multi_hop` is the one deliberate deviation, and it is a
*golden* normalization rather than a port divergence: see its docstring.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

WS_ALPHA = "ws-alpha"
WS_BETA = "ws-beta"

# ── the document-generation corpus ───────────────────────────────────────────
# (node_id, type, title, summary, metadata, workspace_id, updated_at)
DOCGEN_NODES: List[Tuple[str, str, str, str, Dict[str, Any], Optional[str], str]] = [
    # ── the Self-Model subgraph (read by `summary_for_prompt`) ──────────────
    # `self:root` is excluded by id, and `self:unknown:*` carries no
    # `self_model_kind`, so `_row_to_fact` drops it — both are read branches
    # that only exist if the fixture actually contains them.
    ("self:root", "Self", "나", "Brain이 사용자에 대해 알고 있는 사실의 뿌리입니다.",
     {"self_model": True, "self_model_kind": "root"}, None, "2026-07-01T09:00:00"),
    ("self:decision:a1", "Decision", "문서는 한국어로 쓴다",
     "문서는 한국어로 쓴다",
     {"self_model": True, "self_model_kind": "decision", "origin": "user",
      "confidence": 0.9, "signal": "ko_decision_marker", "workspace_id": WS_ALPHA},
     WS_ALPHA, "2026-07-02T09:00:00"),
    ("self:habit:b2", "Habit", "매주 월요일 회의록을 정리한다",
     "매주 월요일 회의록을 정리한다",
     {"self_model": True, "self_model_kind": "habit", "origin": "extraction",
      "confidence": 0.6, "signal": "ko_habit", "workspace_id": None},
     None, "2026-07-03T09:00:00"),
    ("self:preference:c3", "Preference", "설명은 짧고 근거를 먼저",
     "설명은 짧고 근거를 먼저",
     {"self_model": True, "self_model_kind": "preference", "origin": "user",
      "confidence": 0.8, "signal": "ko_prefer", "workspace_id": None},
     None, "2026-07-04T09:00:00"),
    ("self:relationship:d4", "Relationship", "박민서 님과 빌드 파이프라인을 함께 본다",
     "박민서 님과 빌드 파이프라인을 함께 본다",
     {"self_model": True, "self_model_kind": "relationship", "origin": "extraction",
      "confidence": 0.5, "signal": "ko_with", "workspace_id": WS_BETA},
     WS_BETA, "2026-07-05T09:00:00"),
    ("self:trait:e5", "Self", "검색 품질 담당자",
     "검색 품질 담당자",
     {"self_model": True, "self_model_kind": "trait", "origin": "user",
      "confidence": 0.7, "signal": "ko_role", "workspace_id": None},
     None, "2026-07-06T09:00:00"),
    ("self:unknown:f6", "Concept", "kind가 없는 self 노드",
     "self_model_kind가 없어 프로필에서 제외되는 행입니다.",
     {"self_model": True}, None, "2026-07-07T09:00:00"),
    # ── the proposal cluster: one hub document with more qualifying
    #    neighbours than the eight the related-concept join keeps ───────────
    ("doc:proposal", "Document", "제안서 초안: 검색 품질 개선",
     "검색 품질 개선 제안서 초안입니다. 근거는 주간 회의 기록과 분기 리뷰 발표자료입니다.",
     {"relative_path": "docs/proposal.md", "filename": "proposal.md", "ext": ".md"},
     WS_ALPHA, "2026-07-30T09:00:00"),
    ("doc:release-notes", "Document", "Release notes for the retrieval port",
     "Release notes describing the ranking port and the parity harness.",
     {"source": "release"}, None, "2026-07-28T09:00:00"),
    ("file:meeting-minutes", "File", "meeting-minutes.md",
     "회의록: 랭킹 개선과 온보딩 체크리스트를 확정했습니다.",
     {"filename": "meeting-minutes.md", "relative_path": "notes/meeting-minutes.md"},
     WS_ALPHA, "2026-08-01T11:00:00"),
    ("deck:proposal", "SlideDeck", "제안 발표자료",
     "제안서 초안을 발표용으로 정리한 자료입니다.",
     {"filename": "proposal.pptx", "ext": ".pptx"}, WS_BETA, "2026-07-29T09:00:00"),
    ("chat:proposal", "Chat", "제안서 관련 대화",
     "제안서 초안을 어떻게 쓸지 논의한 대화입니다.",
     {"conversation_id": "conv-f"}, WS_ALPHA, "2026-07-26T09:00:00"),
    ("task:proposal-review", "Task", "제안서 검토",
     "제안서 초안을 검토하고 근거를 확인합니다.", {"status": "open"},
     WS_ALPHA, "2026-07-27T09:00:00"),
    ("dec:doc-format", "Decision", "문서 형식은 마크다운으로 통일",
     "문서 생성 결과는 마크다운으로 통일하기로 했습니다.", {},
     WS_ALPHA, "2026-07-22T09:00:00"),
    ("feature:doc-generation", "Feature", "문서 생성",
     "질문에서 문서를 만들어 주는 기능입니다.", {}, WS_ALPHA, "2026-07-25T09:00:00"),
    ("concept:proposal", "Concept", "제안서",
     "제안서는 결정을 요청하는 문서입니다.", {}, None, "2026-07-24T09:00:00"),
    ("concept:evidence", "Concept", "근거",
     "근거는 주장을 뒷받침하는 자료입니다.", {}, WS_ALPHA, "2026-07-23T09:00:00"),
    ("page:proposal-outline", "Page", "제안서 목차",
     "제안서 목차 페이지입니다.", {}, None, "2026-07-21T09:00:00"),
    ("slide:proposal-1", "Slide", "제안 슬라이드 1",
     "제안서 요약 슬라이드입니다.", {}, WS_BETA, "2026-07-19T09:00:00"),
    ("sheet:proposal-budget", "Spreadsheet", "proposal-budget.xlsx",
     "제안서 예산 표입니다.", {"filename": "proposal-budget.xlsx", "ext": ".xlsx"},
     WS_ALPHA, "2026-07-17T09:00:00"),
    ("img:proposal-sketch", "Image", "proposal-sketch.png",
     "제안서 구조 스케치입니다.", {"filename": "proposal-sketch.png", "ext": ".png"},
     WS_BETA, "2026-07-16T09:00:00"),
    ("imgtext:proposal-ocr", "ImageText", "제안서 스케치 OCR",
     "제안서 구조: 배경, 근거, 결론.",
     {"filename": "proposal-sketch.png", "ocr": True}, WS_BETA, "2026-07-16T09:01:00"),
    ("audio:proposal-call", "Audio", "proposal-call.m4a",
     "제안서 관련 통화 녹음입니다.", {"filename": "proposal-call.m4a", "ext": ".m4a"},
     WS_ALPHA, "2026-07-15T09:00:00"),
    ("code:doc-builder", "CodeFile", "context_builder.py",
     "문서 생성 컨텍스트를 조합하는 코드입니다. 제안서 생성도 여기를 지납니다.",
     {"filename": "context_builder.py",
      "relative_path": "latticeai/core/context_builder.py"},
     WS_ALPHA, "2026-07-14T09:00:00"),
    # The recency term's `0.0` branch: a stamp `datetime.fromisoformat` refuses.
    ("doc:undated", "Document", "Undated 제안서 memo",
     "An undated memo about 제안서; its stamp does not parse.", {},
     None, "not-a-timestamp"),
]

# (from, to, type, weight, created_at) — appended to the base edge set.
DOCGEN_EDGES: List[Tuple[str, str, str, float, str]] = [
    ("doc:proposal", "concept:proposal", "mentions", 0.92, "2026-07-30T00:00:00"),
    ("doc:proposal", "concept:evidence", "mentions", 0.91, "2026-07-30T00:01:00"),
    ("doc:proposal", "feature:doc-generation", "relates_to", 0.90, "2026-07-30T00:02:00"),
    ("doc:proposal", "dec:doc-format", "relates_to", 0.89, "2026-07-30T00:03:00"),
    ("doc:proposal", "task:proposal-review", "relates_to", 0.88, "2026-07-30T00:04:00"),
    ("doc:proposal", "concept:retrieval", "mentions", 0.87, "2026-07-30T00:05:00"),
    ("doc:proposal", "concept:ranking", "mentions", 0.86, "2026-07-30T00:06:00"),
    ("doc:proposal", "feature:command-palette", "mentions", 0.85, "2026-07-30T00:07:00"),
    ("doc:proposal", "task:parity-harness", "relates_to", 0.84, "2026-07-30T00:08:00"),
    ("doc:proposal", "page:proposal-outline", "contains", 0.83, "2026-07-30T00:09:00"),
    ("deck:proposal", "doc:proposal", "relates_to", 0.82, "2026-07-29T00:00:00"),
    ("deck:proposal", "slide:proposal-1", "contains", 0.81, "2026-07-29T00:01:00"),
    ("file:meeting-minutes", "dec:doc-format", "relates_to", 0.80, "2026-07-28T00:00:00"),
    ("file:meeting-minutes", "meeting:weekly", "relates_to", 0.79, "2026-07-28T00:01:00"),
    ("chat:proposal", "doc:proposal", "mentions", 0.78, "2026-07-27T00:00:00"),
    ("img:proposal-sketch", "imgtext:proposal-ocr", "relates_to", 0.77, "2026-07-26T00:00:00"),
    ("imgtext:proposal-ocr", "concept:proposal", "mentions", 0.76, "2026-07-26T00:01:00"),
    ("audio:proposal-call", "task:proposal-review", "mentions", 0.75, "2026-07-25T00:00:00"),
    ("code:doc-builder", "feature:doc-generation", "relates_to", 0.74, "2026-07-24T00:00:00"),
    ("sheet:proposal-budget", "doc:proposal", "relates_to", 0.73, "2026-07-23T00:00:00"),
    ("doc:release-notes", "concept:ranking", "mentions", 0.72, "2026-07-22T00:00:00"),
    ("doc:undated", "concept:proposal", "mentions", 0.71, "2026-07-21T00:00:00"),
    # The Self-Model's own shape: every fact points at the root.
    ("self:decision:a1", "self:root", "PART_OF", 1.0, "2026-07-02T00:00:00"),
    ("self:habit:b2", "self:root", "PART_OF", 1.0, "2026-07-03T00:00:00"),
    ("self:preference:c3", "self:root", "PART_OF", 1.0, "2026-07-04T00:00:00"),
    ("self:relationship:d4", "self:root", "PART_OF", 1.0, "2026-07-05T00:00:00"),
    ("self:trait:e5", "self:root", "PART_OF", 1.0, "2026-07-06T00:00:00"),
    ("self:unknown:f6", "self:root", "PART_OF", 1.0, "2026-07-07T00:00:00"),
]

# ── suite specs ──────────────────────────────────────────────────────────────
DOCGEN_SEARCH_SPECS: List[Dict[str, Any]] = [
    {"key": "ko_proposal", "query": "제안서 초안"},
    {"key": "ko_meeting", "query": "회의 결정 사항"},
    {"key": "en_ranking", "query": "hybrid retrieval ranking"},
    {"key": "ko_sketch", "query": "스케치"},
    {"key": "ko_self_model", "query": "문서는 한국어로"},
    {"key": "en_undated", "query": "Undated"},
    {"key": "hub_deck", "query": "제안 발표자료"},
    {"key": "tie", "query": "Tie candidate"},
    {"key": "no_hit", "query": "zzqq wumpus nonsense"},
    {"key": "empty_query", "query": ""},
    {"key": "blank_query", "query": "   "},
    {"key": "limit_one", "query": "제안서", "limit": 1},
    {"key": "limit_zero", "query": "제안서", "limit": 0},
    {"key": "limit_over", "query": "제안서", "limit": 900},
    {"key": "scoped_alpha", "query": "제안서", "allowed": [WS_ALPHA]},
    {"key": "scoped_alpha_legacy", "query": "제안서", "allowed": [WS_ALPHA], "legacy": True},
    {"key": "scoped_beta", "query": "제안서", "allowed": [WS_BETA]},
    {"key": "scoped_empty", "query": "제안서", "allowed": []},
]

MULTI_HOP_SPECS: List[Dict[str, Any]] = [
    *[{"key": f"hub_h{hops}", "node_ids": ["doc:proposal"], "max_hops": hops}
      for hops in (0, 1, 2, 3)],
    {"key": "hub_hneg", "node_ids": ["doc:proposal"], "max_hops": -1},
    {"key": "multi_seed", "node_ids": ["doc:proposal", "dec:fusion-alpha"], "max_hops": 2},
    {"key": "leaf", "node_ids": ["slide:proposal-1"], "max_hops": 2},
    {"key": "isolated", "node_ids": ["tie:c"], "max_hops": 2},
    {"key": "self_root", "node_ids": ["self:root"], "max_hops": 2},
    {"key": "missing_seed", "node_ids": ["nope:missing"], "max_hops": 2},
    {"key": "no_seeds", "node_ids": [], "max_hops": 2},
    {"key": "scoped_alpha", "node_ids": ["doc:proposal"], "max_hops": 2,
     "allowed": [WS_ALPHA]},
    {"key": "scoped_alpha_legacy", "node_ids": ["doc:proposal"], "max_hops": 2,
     "allowed": [WS_ALPHA], "legacy": True},
    {"key": "scoped_beta", "node_ids": ["deck:proposal"], "max_hops": 2,
     "allowed": [WS_BETA]},
    {"key": "scoped_empty", "node_ids": ["doc:proposal"], "max_hops": 2, "allowed": []},
]

CONTEXT_DOCUMENT_SPECS: List[Dict[str, Any]] = [
    {"key": "default", "query": "제안서 초안"},
    {"key": "no_self_model", "query": "제안서 초안", "include_self_model": False},
    {"key": "self_model_tokens_small", "query": "제안서 초안", "self_model_tokens": 12},
    {"key": "budget_240", "query": "제안서 초안", "budget": 240},
    {"key": "budget_120", "query": "제안서 초안", "budget": 120},
    {"key": "budget_40", "query": "제안서 초안", "budget": 40},
    {"key": "budget_one", "query": "제안서 초안", "budget": 1},
    {"key": "budget_zero", "query": "제안서 초안", "budget": 0},
    {"key": "hops_zero", "query": "제안서 초안", "max_hops": 0},
    {"key": "hops_three", "query": "제안서 초안", "max_hops": 3},
    {"key": "results_two", "query": "제안서 초안", "max_results": 2},
    {"key": "ko_meeting", "query": "회의 결정 사항"},
    {"key": "en_ranking", "query": "hybrid retrieval ranking"},
    {"key": "ko_sketch", "query": "스케치"},
    {"key": "fallback_lexical", "query": "박민서 님"},
    {"key": "fallback_empty", "query": "zzqq wumpus nonsense"},
    {"key": "empty_query", "query": ""},
    {"key": "scoped_alpha", "query": "제안서 초안", "allowed": [WS_ALPHA]},
    {"key": "scoped_alpha_legacy", "query": "제안서 초안", "allowed": [WS_ALPHA],
     "legacy": True},
    {"key": "scoped_empty", "query": "제안서 초안", "allowed": []},
]


def normalize_multi_hop(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Order a ``multi_hop_context`` answer deterministically.

    **This is a golden normalization, not a port divergence.** Python's
    traversal iterates ``frontier``, a ``set`` — so the order in which the
    nodes of one hop are appended (and therefore the order their incident
    edges are appended) depends on string hashing, which CPython randomizes
    per process unless ``PYTHONHASHSEED`` is pinned. The *contents* are fully
    determined (which nodes are visited, which edges are collected, and every
    node's hop label); only the sequence is not, so the live response ordering
    is unspecified and there is nothing for a port to reproduce.

    Both sides therefore sort before comparing: nodes by ``(hop, id)``, edges
    by ``(from, to, type, weight)``. The Rust port emits that order natively —
    it is deterministic where Python is not, which is a strictly stronger
    promise and not a different answer.
    """
    return {
        "nodes": sorted(payload["nodes"], key=lambda node: (node["hop"], node["id"])),
        "edges": sorted(
            payload["edges"],
            key=lambda edge: (edge["from"], edge["to"], edge["type"], edge["weight"]),
        ),
    }


class OrderedMultiHopStore:
    """The store, with ``multi_hop_context`` ordered deterministically.

    ``retrieve_context_for_generation`` feeds the traversal's node list into
    ``extra_nodes``, and the concept section renders the first eight of it — so
    Python's unordered ``frontier`` iteration reaches the *rendered markdown*,
    not only the traversal payload. Both sides therefore see the normalized
    order (see :func:`normalize_multi_hop`); every other attribute is forwarded
    to the real store untouched.
    """

    def __init__(self, store: Any):
        self._store = store

    def __getattr__(self, name: str) -> Any:
        return getattr(self._store, name)

    def multi_hop_context(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        return normalize_multi_hop(self._store.multi_hop_context(*args, **kwargs))


def _scope(spec: Dict[str, Any]) -> Dict[str, Any]:
    """The graph-layer scope every document-generation read takes."""
    allowed = spec.get("allowed")
    return {
        "allowed_workspaces": None if allowed is None else set(allowed),
        "include_legacy_global": spec.get("legacy", False),
    }


def run_docgen_search(harness: Any, spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    """``KnowledgeGraphStore.search_for_document_generation`` for one spec."""
    return harness.store.search_for_document_generation(
        spec["query"], limit=spec.get("limit", 10), **_scope(spec)
    )


def run_multi_hop(harness: Any, spec: Dict[str, Any]) -> Dict[str, Any]:
    """``multi_hop_context`` for one spec, in the normalized order."""
    return normalize_multi_hop(
        harness.store.multi_hop_context(
            spec["node_ids"], max_hops=spec.get("max_hops", 2), **_scope(spec)
        )
    )


def run_context_document(harness: Any, spec: Dict[str, Any]) -> Dict[str, Any]:
    """``retrieve_context_for_generation`` plus the footnote over its sources.

    The footnote is a second public entry point over the same ``sources`` list;
    carrying it here proves that port too, without a suite of its own.
    """
    from lattice_brain.self_model import DEFAULT_SUMMARY_TOKENS
    from latticeai.core.context_builder import (
        DEFAULT_DOCUMENT_CONTEXT_BUDGET,
        format_sources_footnote,
        retrieve_context_for_generation,
    )

    context = retrieve_context_for_generation(
        OrderedMultiHopStore(harness.store),
        spec["query"],
        max_results=spec.get("max_results", 10),
        max_hops=spec.get("max_hops", 2),
        budget=spec.get("budget", DEFAULT_DOCUMENT_CONTEXT_BUDGET),
        include_self_model=spec.get("include_self_model", True),
        self_model_tokens=spec.get("self_model_tokens", DEFAULT_SUMMARY_TOKENS),
        **_scope(spec),
    )
    return {
        "context": context,
        "sources_footnote": format_sources_footnote(context["sources"]),
    }


#: The three document-generation suites, as the generator merges them into
#: :data:`SUITES` / :data:`SUITE_RUNNERS`. Keeping the pair side by side is what
#: stops a spec list from arriving without the runner that answers it.
DOCGEN_SUITES: Dict[str, List[Dict[str, Any]]] = {
    "docgen_search": DOCGEN_SEARCH_SPECS,
    "multi_hop": MULTI_HOP_SPECS,
    "context_document": CONTEXT_DOCUMENT_SPECS,
}

DOCGEN_RUNNERS: Dict[str, Any] = {
    "docgen_search": run_docgen_search,
    "multi_hop": run_multi_hop,
    "context_document": run_context_document,
}
