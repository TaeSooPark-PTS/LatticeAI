#!/usr/bin/env python3
"""Build the committed Python↔Rust retrieval parity fixture.

``rust/lattice-retrieval`` is a port, and a port is only worth having if
something keeps proving it is still one. This script is the Python half of that
proof: it builds a small, fully deterministic Brain with the **real** write path
(``KnowledgeGraphStore._upsert_node`` / ``_upsert_chunk`` / ``_upsert_edge``,
the real hash embedder, the real v2 projection and trigram FTS index), then runs
the real ``hybrid_search`` / ``search`` / ``vector_search`` over it and writes
their answers to ``rust/fixtures/golden/``.

Two consumers read what it writes:

* ``tests/unit/test_rust_parity_contract.py`` re-runs the Python engines against
  the committed database and asserts the goldens still hold — so a change to
  Python retrieval semantics fails loudly instead of silently invalidating the
  contract the Rust side is pinned to;
* ``rust/lattice-retrieval/tests/parity.rs`` runs the Rust port against the same
  database and the same goldens.

Determinism is the whole design constraint:

* every timestamp is written by the real code and then **backdated** to a fixed
  value, so nothing in the fixture depends on when it was generated;
* ``hybrid_search``'s recency decay calls ``datetime.now()``, so the clock is
  frozen at :data:`FROZEN_NOW` (recorded in the manifest for the Rust side);
* LLM concept extraction is forced off, so ``_topic_candidates`` always takes
  the rule-based path a port can reproduce;
* every environment knob the retrieval stack reads is pinned to its default.

Usage::

    .venv/bin/python scripts/generate_rust_parity_fixtures.py
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

FIXTURE_DIR = REPO_ROOT / "rust" / "fixtures"
GOLDEN_DIR = FIXTURE_DIR / "golden"
STORE_PATH = FIXTURE_DIR / "parity_store.sqlite"

#: The wall clock ``hybrid_search`` sees. Recency decay is a function of "now",
#: so a golden generated against a moving clock is not a golden.
FROZEN_NOW = "2026-08-01T12:00:00"

#: Every environment variable the ported path reads, pinned to the default
#: configuration the port targets (brute backend, RRF off, graph expansion off,
#: cross-encoder rerank off, rewrite on).
PINNED_ENV: Dict[str, str] = {
    "LATTICEAI_VECTOR_DIM": "384",
    "LATTICEAI_VECTOR_INDEX": "brute",
    "LATTICEAI_VECTOR_MAX_CANDIDATES": "10000",
    "LATTICEAI_KG_READ_V2": "1",
    "LATTICEAI_FUSION_WEIGHTS": "",
    "LATTICEAI_FUSION_STRATEGY": "",
    "LATTICEAI_FUSION_RRF": "",
    "LATTICEAI_GRAPH_EXPANSION": "",
    "LATTICEAI_CROSS_ENCODER_RERANK": "",
    "LATTICEAI_QUERY_REWRITE": "",
    "LATTICEAI_LLM_EXTRACTION": "false",
}

WS_ALPHA = "ws-alpha"
WS_BETA = "ws-beta"

# ── the corpus ───────────────────────────────────────────────────────────────
# (node_id, type, title, summary, metadata, workspace_id, updated_at)
#
# Shaped on purpose:
#   * every type in ``search()``'s fixed ``type_boost`` set appears, and so do
#     types outside it, so the boost is observable;
#   * titles/summaries are half Korean and half English, because the tokenizer,
#     the query classifier and the concept extractor all branch on script;
#   * the ``tie:`` block is five rows sharing one timestamp and one type with
#     nothing to match, which pins the (hits, type_boost, updated_at) → id ASC
#     tie-break that both engines have to reproduce;
#   * two workspaces plus NULL-workspace legacy rows cover all three scoping
#     answers (no scoping / empty set / a specific workspace).
NODES: List[Tuple[str, str, str, str, Dict[str, Any], Optional[str], str]] = [
    ("dec:fusion-alpha", "Decision", "Hybrid retrieval fusion stays alpha weighted",
     "We decided the ranking keeps alpha fusion: lexical rank plus max normalized vector score.",
     {"category": "retrieval", "owner": "jiwon"}, WS_ALPHA, "2026-07-20T09:00:00"),
    ("dec:rust-foundation", "Decision", "Rust 기반 검색 이관 결정",
     "검색 랭킹을 Rust로 이관하기로 결정했습니다. 회의 결과 패리티 증명을 먼저 만듭니다.",
     {"category": "platform"}, WS_ALPHA, "2026-07-18T14:30:00"),
    ("task:parity-harness", "Task", "Build the retrieval parity harness",
     "Generate a golden fixture so the Rust ranking can be compared against the Python ranking.",
     {"status": "open"}, WS_ALPHA, "2026-07-15T11:00:00"),
    ("task:onboarding-checklist", "Task", "온보딩 체크리스트 정리",
     "새로운 사용자가 처음 다섯 걸음을 마칠 수 있도록 온보딩 체크리스트를 정리합니다.",
     {"status": "open"}, WS_BETA, "2026-06-30T08:20:00"),
    ("file:ranking-notes", "File", "ranking-notes.md",
     "Notes on ranking: lexical channel scores one over rank, vector channel is max normalized.",
     {"filename": "ranking-notes.md", "ext": ".md"}, WS_ALPHA, "2026-07-10T16:45:00"),
    ("doc:handbook", "Document", "Lattice AI 사용 안내서",
     "제품 안내서입니다. 검색, 회의 기록, 온보딩 체크리스트를 한 곳에서 설명합니다.",
     {"filename": "handbook.pdf", "ext": ".pdf", "source_node": "doc:handbook"},
     WS_ALPHA, "2026-06-12T10:00:00"),
    ("doc:retrieval-spec", "Document", "Retrieval specification",
     "The retrieval specification describes the lexical channel, the vector channel and their fusion.",
     {"filename": "retrieval-spec.pdf", "ext": ".pdf"}, WS_BETA, "2026-05-28T13:15:00"),
    ("code:hybrid-search", "CodeFile", "hybrid.py",
     "def hybrid_search(query): the ranking pipeline lives here and calls vector_search().",
     {"filename": "hybrid.py", "ext": ".py"}, WS_ALPHA, "2026-07-08T09:30:00"),
    ("code:build-failure", "CodeFile", "build_pipeline.py",
     "빌드 실패 원인을 남겨두는 파일입니다. 컴파일 오류가 나면 여기부터 확인합니다.",
     {"filename": "build_pipeline.py", "ext": ".py"}, WS_BETA, "2026-04-22T18:05:00"),
    ("sheet:metrics", "Spreadsheet", "retrieval-metrics.xlsx",
     "Recall and precision per query class for the ranking experiments.",
     {"filename": "retrieval-metrics.xlsx", "ext": ".xlsx"}, WS_ALPHA, "2026-05-06T09:45:00"),
    ("deck:review", "SlideDeck", "분기 리뷰 발표자료",
     "지난주 분기 리뷰에서 사용한 발표자료입니다. 검색 품질 지표를 담고 있습니다.",
     {"filename": "quarterly-review.pptx", "ext": ".pptx"}, WS_ALPHA, "2026-07-02T15:00:00"),
    ("img:whiteboard", "Image", "whiteboard-retrieval.png",
     "Whiteboard photo from the retrieval design session.",
     {"filename": "whiteboard-retrieval.png", "ext": ".png"}, WS_BETA, "2026-03-19T11:11:00"),
    ("img:ocr-notes", "ImageText", "화이트보드 OCR 텍스트",
     "회의 결정 사항: 랭킹은 alpha 융합을 유지한다.",
     {"filename": "whiteboard-retrieval.png", "ocr": True}, WS_BETA, "2026-03-19T11:12:00"),
    ("audio:standup", "Audio", "standup-2026-07-13.m4a",
     "Standup recording where the parity harness was assigned.",
     {"filename": "standup-2026-07-13.m4a", "ext": ".m4a"}, WS_ALPHA, "2026-07-13T09:05:00"),
    ("page:onboarding", "Page", "온보딩 첫 다섯 걸음",
     "온보딩 페이지입니다. 체크리스트와 안내 문구를 담습니다.", {}, None, "2026-06-01T12:00:00"),
    ("slide:fusion", "Slide", "Fusion slide",
     "One slide explaining alpha fusion of the lexical and vector channels.",
     {}, None, "2026-05-14T12:00:00"),
    ("concept:retrieval", "Concept", "Retrieval",
     "Retrieval is the act of finding the right memory for a question.",
     {}, WS_ALPHA, "2026-04-02T10:00:00"),
    ("concept:ranking", "Concept", "Ranking",
     "Ranking orders candidates so the best answer is first.", {}, WS_BETA, "2026-04-03T10:00:00"),
    ("person:jiwon", "Person", "김지원 님",
     "검색 품질 담당자입니다. 온보딩 개선도 함께 맡고 있습니다.",
     {"role": "owner"}, WS_ALPHA, "2026-04-11T10:00:00"),
    ("person:minseo", "Person", "박민서 님",
     "빌드 파이프라인 담당자입니다.", {"role": "reviewer"}, WS_BETA, "2026-04-12T10:00:00"),
    ("meeting:weekly", "Meeting", "주간 회의 2026-07-14",
     "지난주 주간 회의 기록입니다. 검색 랭킹과 온보딩을 논의했습니다.",
     {}, WS_ALPHA, "2026-07-14T10:00:00"),
    ("meeting:kickoff", "Meeting", "Kickoff meeting",
     "Kickoff meeting for the Rust foundation work.", {}, WS_BETA, "2026-02-09T10:00:00"),
    ("chat:ranking", "Chat", "랭킹 관련 대화",
     "랭킹이 왜 이렇게 나오는지 물어본 대화입니다.",
     {"conversation_id": "conv-1"}, WS_ALPHA, "2026-07-05T21:00:00"),
    ("source:repo", "Source", "github.com/lattice/ai",
     "Source repository for the product.", {"source": "git"}, WS_ALPHA, "2026-02-20T10:00:00"),
    ("repo:lattice", "Repository", "lattice-ai",
     "The monorepo holding the Python worker and the Rust workspace.",
     {}, WS_ALPHA, "2026-02-21T10:00:00"),
    ("org:lattice", "Organization", "Lattice", "The organization.", {}, None, "2026-02-22T10:00:00"),
    ("workflow:nightly", "Workflow", "Nightly reindex",
     "A workflow that reindexes the vector store overnight.", {}, WS_BETA, "2026-03-02T10:00:00"),
    ("agent:librarian", "Agent", "Librarian agent",
     "An agent that files new documents into the graph.", {}, WS_BETA, "2026-03-03T10:00:00"),
    ("error:timeout", "Error", "Search timeout",
     "An error where the ranking took longer than the request budget.",
     {}, WS_ALPHA, "2026-03-04T10:00:00"),
    ("feature:command-palette", "Feature", "Command palette",
     "The palette that opens search from anywhere.", {}, WS_ALPHA, "2026-03-05T10:00:00"),
    ("topic:quality", "Topic", "검색 품질",
     "검색 품질에 대한 주제입니다.", {}, WS_ALPHA, "2026-03-06T10:00:00"),
    # ── the tie block: identical type, identical timestamp, nothing to match ──
    ("tie:a", "Concept", "Tie candidate A", "", {}, WS_ALPHA, "2026-02-01T00:00:00"),
    ("tie:b", "Concept", "Tie candidate B", "", {}, WS_ALPHA, "2026-02-01T00:00:00"),
    ("tie:c", "Concept", "Tie candidate C", "", {}, WS_BETA, "2026-02-01T00:00:00"),
    ("tie:d", "Concept", "Tie candidate D", "", {}, None, "2026-02-01T00:00:00"),
    ("tie:e", "Concept", "Tie candidate E", "", {}, None, "2026-02-01T00:00:00"),
    # ── boosted twins on the same timestamp: type_boost breaks nothing here,
    #    id ASC does, and that is the assertion.
    ("twin:doc-a", "Document", "Twin document A", "동일한 시각의 문서 A", {}, WS_ALPHA,
     "2026-02-05T00:00:00"),
    ("twin:doc-b", "Document", "Twin document B", "동일한 시각의 문서 B", {}, WS_ALPHA,
     "2026-02-05T00:00:00"),
    ("twin:concept-a", "Concept", "Twin concept A", "동일한 시각의 개념 A", {}, WS_ALPHA,
     "2026-02-05T00:00:00"),
    ("legacy:global-note", "Document", "Legacy global note",
     "A legacy row with no workspace, visible only with include_legacy_global.",
     {}, None, "2026-06-20T10:00:00"),
]

# (chunk_id, parent_node_id, index, node_title, text, chunk_fields, workspace, updated_at)
#
# The shape mirrors ``KnowledgeGraphIngestMixin``: every chunk is BOTH a
# ``Chunk`` node (so the lexical lane can match it and workspace scoping applies)
# and a ``chunks`` row with its own embedding (so the vector lane returns it and
# has to roll it up to its parent). Getting that duality wrong is the whole
# reason chunk-heavy queries are in the query set.
CHUNKS: List[Tuple[str, str, int, str, str, Dict[str, Any], Optional[str], str]] = [
    ("chunk:handbook:1", "doc:handbook", 0, "handbook.pdf chunk 1",
     "온보딩 체크리스트: 첫째, 폴더를 연결합니다. 둘째, 질문을 합니다. 셋째, 근거를 확인합니다.",
     {"heading_path": "안내서 > 온보딩", "page": 3, "page_end": 4, "start_char": 0},
     WS_ALPHA, "2026-06-12T10:00:01"),
    ("chunk:handbook:2", "doc:handbook", 1, "handbook.pdf chunk 2",
     "검색 화면에서는 회의 결정 사항을 한 번에 찾을 수 있습니다.",
     {"heading_path": "안내서 > 검색", "page": 7, "start_char": 1200},
     WS_ALPHA, "2026-06-12T10:00:02"),
    ("chunk:spec:1", "doc:retrieval-spec", 0, "retrieval-spec.pdf chunk 1",
     "The lexical channel scores one over rank. The vector channel is max normalized before fusion.",
     {"heading_path": "Retrieval > Fusion", "page": 2, "start_char": 0},
     WS_BETA, "2026-05-28T13:15:01"),
    ("chunk:spec:2", "doc:retrieval-spec", 1, "retrieval-spec.pdf chunk 2",
     "Ranking ties are broken by node id ascending, which keeps the answer stable across runs.",
     {"start_char": 900}, WS_BETA, "2026-05-28T13:15:02"),
]

# (from, to, type)
EDGES: List[Tuple[str, str, str]] = [
    ("dec:fusion-alpha", "concept:retrieval", "mentions"),
    ("dec:fusion-alpha", "concept:ranking", "mentions"),
    ("task:parity-harness", "dec:rust-foundation", "relates_to"),
    ("doc:handbook", "page:onboarding", "contains"),
    ("meeting:weekly", "dec:fusion-alpha", "discusses"),
    ("person:jiwon", "task:parity-harness", "owns"),
]

# ── the query set ────────────────────────────────────────────────────────────
# ``allowed`` is None (no scoping), [] (a caller who may read nothing) or a list
# of workspace ids; ``legacy`` is ``include_legacy_global``.
QUERIES: List[Dict[str, Any]] = [
    {"key": "en_fact", "query": "hybrid retrieval ranking"},
    {"key": "ko_fact", "query": "회의 결정 사항"},
    {"key": "en_code", "query": "vector_search() returns"},
    {"key": "ko_code", "query": "빌드 실패 원인"},
    {"key": "en_person", "query": "who owns the onboarding checklist"},
    {"key": "ko_person", "query": "담당자 누구"},
    {"key": "en_recency", "query": "recent decisions last week"},
    {"key": "ko_recency", "query": "지난주 회의 기록"},
    {"key": "en_filler", "query": "  what is   the retrieval specification please  "},
    {"key": "ko_filler", "query": "온보딩 체크리스트 좀 알려줘"},
    {"key": "short_query", "query": "ai"},
    {"key": "no_hit", "query": "zzqq wumpus nonsense"},
    {"key": "tie_heavy", "query": "Tie candidate"},
    {"key": "chunk_heavy", "query": "온보딩 체크리스트"},
    {"key": "quoted", "query": 'said "retrieval" twice'},
    {"key": "empty_query", "query": ""},
    {"key": "ws_empty", "query": "hybrid retrieval ranking", "allowed": []},
    {"key": "ws_empty_legacy", "query": "회의 결정 사항", "allowed": [], "legacy": True},
    {"key": "ws_alpha", "query": "hybrid retrieval ranking", "allowed": [WS_ALPHA]},
    {"key": "ws_alpha_legacy", "query": "회의 결정 사항", "allowed": [WS_ALPHA], "legacy": True},
    {"key": "ws_beta", "query": "온보딩 체크리스트", "allowed": [WS_BETA]},
    # A vector floor nothing clears: the lane returns zero rows, which is the
    # only way to reach the stale-embedder probe and the lexical-only fusion
    # label without breaking the store.
    {"key": "min_vector_floor", "query": "hybrid retrieval ranking", "min_vector": 0.95},
    # A small top_k so the rerank window (top_k * 2) is narrower than the
    # candidate list and the cut is observable.
    {"key": "top_k_small", "query": "hybrid retrieval ranking", "top_k": 3},
    # An explicitly pinned alpha: no policy, so no class, no rewrite and — on a
    # query that would otherwise be recency-classed — no age decay either.
    {"key": "alpha_pinned", "query": "지난주 회의 기록", "alpha": 0.2},
    # A limit below the FTS hit count, so `ORDER BY rank LIMIT ?` decides which
    # rows exist at all. This is the one place bm25 ordering is observable
    # (`search()` re-sorts by id afterwards), and therefore the one place a
    # SQLite version difference between the two runtimes could show up.
    {"key": "fts_rank_cut", "query": "Tie candidate", "limit": 2, "top_k": 2},
]

#: Texts whose tokenizer output, hash pairs and full vectors pin the Rust
#: embedding port bit-for-bit (Korean, English, mixed, symbols, digits).
EMBEDDING_TEXTS: List[str] = [
    "hybrid retrieval ranking",
    "회의 결정 사항",
    "온보딩 체크리스트 v2",
    "vector_search() returns a dict",
    "!!! ??? ...",
    "MixedCase and snake_case and kebab-case",
    "2026-07-20T09:00:00",
    "a",
]

#: Values that separate CPython's round-half-even from naive scaling, plus the
#: shapes real fusion arithmetic produces.
ROUNDING_VALUES: List[float] = [
    0.0, 1.0, 5e-07, 1.5e-06, 2.5e-06, 2.6535895, 1 / 3, 1 / 7,
    0.1234565, 0.1234575, 0.9999999999, 123456.7890625, -0.0000005,
    0.6 * 0.5 + 0.4 * (1 / 3), 0.35 * 0.25 + 0.65 * (1 / 7), 0.5 + 0.5 * 0.7071067811865476,
]


@contextmanager
def pinned_environment() -> Iterator[None]:
    """Pin every env knob the retrieval stack reads, then restore."""
    previous = {key: os.environ.get(key) for key in PINNED_ENV}
    os.environ.update(PINNED_ENV)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@contextmanager
def frozen_clock() -> Iterator[None]:
    """Freeze ``hybrid_search``'s ``datetime.now()`` at :data:`FROZEN_NOW`.

    Only the recency-class age decay reads the clock, and it reads it through
    the ``datetime`` name that ``hybrid.py`` imported — so rebinding that name
    is the whole patch.
    """
    from lattice_brain.graph.retrieval import hybrid as hybrid_module

    frozen = datetime.fromisoformat(FROZEN_NOW)

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ARG003 — mirrors datetime.now's signature
            return frozen

    original = hybrid_module.datetime
    hybrid_module.datetime = _FrozenDatetime
    try:
        yield
    finally:
        hybrid_module.datetime = original


@contextmanager
def rules_only_extraction() -> Iterator[None]:
    """Force ``_topic_candidates`` down its rule-based path.

    The LLM path needs a bound router, which no fixture run has — but "no router
    happens to be bound" is an accident, and this contract cannot rest on one.
    """
    from lattice_brain.graph._kg_common import extraction

    original = extraction.ENABLE_LLM_EXTRACTION
    extraction.ENABLE_LLM_EXTRACTION = False
    try:
        yield
    finally:
        extraction.ENABLE_LLM_EXTRACTION = original


def open_store(db_path: Path):
    """A ``KnowledgeGraphStore`` over ``db_path`` (blobs beside it)."""
    from lattice_brain.graph import KnowledgeGraphStore

    return KnowledgeGraphStore(Path(db_path), Path(db_path).parent / "blobs")


def _backdate(conn: sqlite3.Connection, node_id: str, stamp: str) -> None:
    conn.execute("UPDATE nodes SET created_at=?, updated_at=? WHERE id=?", (stamp, stamp, node_id))
    conn.execute(
        "UPDATE nodes_v2 SET created_at=?, updated_at=? WHERE id=?", (stamp, stamp, node_id)
    )


def build_store(db_path: Path) -> None:
    """Create the fixture database from scratch with the real write path."""
    for sibling in (db_path, Path(f"{db_path}-wal"), Path(f"{db_path}-shm")):
        if sibling.exists():
            sibling.unlink()
    blob_dir = db_path.parent / "blobs"
    if blob_dir.exists():
        shutil.rmtree(blob_dir)

    store = open_store(db_path)
    with store._connect() as conn:
        for node_id, node_type, title, summary, metadata, workspace, stamp in NODES:
            store._upsert_node(
                conn, node_id, node_type, title, summary, metadata, workspace_id=workspace
            )
            _backdate(conn, node_id, stamp)
        for chunk_id, parent, index, title, text, fields, workspace, stamp in CHUNKS:
            metadata = {"index": index, "source_node": parent, **fields}
            store._upsert_node(
                conn, chunk_id, "Chunk", title, text[:500], metadata, workspace_id=workspace
            )
            store._upsert_chunk(
                conn, chunk_id=chunk_id, source_node=parent, text=text, metadata=metadata
            )
            _backdate(conn, chunk_id, stamp)
        for from_node, to_node, edge_type in EDGES:
            store._upsert_edge(conn, from_node, to_node, edge_type, 1.0, {})
            conn.execute(
                "UPDATE edges SET created_at=? WHERE from_node=? AND to_node=?",
                ("2026-07-01T00:00:00", from_node, to_node),
            )
        # ``indexed_at`` decides the candidate scan order (and, when the cap
        # bites, which candidates exist at all), so it is assigned explicitly
        # rather than inherited from the clock.
        item_ids = [row["item_id"] for row in conn.execute(
            "SELECT item_id FROM vector_embeddings ORDER BY item_id ASC"
        ).fetchall()]
        for seq, item_id in enumerate(item_ids):
            stamp = f"2026-03-01T{seq // 60:02d}:{seq % 60:02d}:00"
            conn.execute(
                "UPDATE vector_embeddings SET indexed_at=? WHERE item_id=?", (stamp, item_id)
            )
    store.record_embedder_fingerprint()

    # Leave one self-contained file behind: checkpoint the WAL, drop back to a
    # rollback journal so no -wal/-shm sidecar has to be committed, and compact.
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("PRAGMA journal_mode=DELETE")
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("VACUUM")
    conn.close()
    for sibling in (Path(f"{db_path}-wal"), Path(f"{db_path}-shm")):
        if sibling.exists():
            sibling.unlink()
    if blob_dir.exists():
        shutil.rmtree(blob_dir)


def _allowed(spec: Dict[str, Any]):
    allowed = spec.get("allowed")
    return None if allowed is None else set(allowed)


ENGINES: Dict[str, Callable[[Any, Dict[str, Any]], Dict[str, Any]]] = {
    "hybrid": lambda store, spec: store.hybrid_search(
        spec["query"],
        top_k=spec.get("top_k", 20),
        alpha=spec.get("alpha"),
        allowed_workspaces=_allowed(spec),
        include_legacy_global=spec.get("legacy", False),
        min_vector_score=spec.get("min_vector", 0.0),
    ),
    "keyword": lambda store, spec: store.search(
        spec["query"],
        spec.get("limit", 30),
        allowed_workspaces=_allowed(spec),
        include_legacy_global=spec.get("legacy", False),
    ),
    "vector": lambda store, spec: store.vector_search(
        spec["query"], limit=spec.get("limit", 30), min_score=spec.get("min_score", 0.0)
    ),
}


def run_engine(store, engine: str, spec: Dict[str, Any]) -> Dict[str, Any]:
    """Run one engine for one query spec under the frozen clock."""
    with frozen_clock(), rules_only_extraction():
        return ENGINES[engine](store, spec)


def golden_path(engine: str, key: str) -> Path:
    return GOLDEN_DIR / f"{engine}__{key}.json"


def golden_payload(engine: str, spec: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "engine": engine,
        "key": spec["key"],
        "query": spec["query"],
        "params": {
            "top_k": spec.get("top_k", 20),
            "alpha": spec.get("alpha"),
            "limit": spec.get("limit", 30),
            "min_score": spec.get("min_score", 0.0),
            "min_vector_score": spec.get("min_vector", 0.0),
            "allowed_workspaces": spec.get("allowed"),
            "include_legacy_global": spec.get("legacy", False),
        },
        "result": result,
    }


def _dump(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def embeddings_golden() -> Dict[str, Any]:
    """Tokenizer output, hash pairs and full vectors for the pinned texts."""
    from lattice_brain.embeddings import LocalEmbeddingModel, _hash_to_index, _tokenize

    model = LocalEmbeddingModel()
    cases = []
    for text in EMBEDDING_TEXTS:
        features = _tokenize(text)
        cases.append(
            {
                "text": text,
                "features": features,
                "hashes": [
                    {"feature": feature, "index": index, "sign": sign}
                    for feature, (index, sign) in (
                        (feature, _hash_to_index(feature, model.dim))
                        for feature in features[:8]
                    )
                ],
                "vector": model.embed(text),
                "encoded_hex": model.encode(model.embed(text)).hex(),
            }
        )
    return {"model_id": model.model_id, "dim": model.dim, "cases": cases}


def rounding_golden() -> List[Dict[str, float]]:
    """``round(x, 6)`` for values where the tie rule is observable."""
    return [{"input": value, "expected": round(value, 6)} for value in ROUNDING_VALUES]


def manifest() -> Dict[str, Any]:
    from lattice_brain.embeddings import LocalEmbeddingModel

    model = LocalEmbeddingModel()
    return {
        "frozen_now": FROZEN_NOW,
        "store": STORE_PATH.name,
        "embedding_model": model.model_id,
        "embedding_dim": model.dim,
        "engines": sorted(ENGINES),
        "queries": QUERIES,
        "pinned_env": PINNED_ENV,
    }


def main() -> int:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    if GOLDEN_DIR.exists():
        shutil.rmtree(GOLDEN_DIR)
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    with pinned_environment():
        build_store(STORE_PATH)
        store = open_store(STORE_PATH)
        written = 0
        for spec in QUERIES:
            for engine in sorted(ENGINES):
                result = run_engine(store, engine, spec)
                _dump(golden_path(engine, spec["key"]), golden_payload(engine, spec, result))
                written += 1
        _dump(GOLDEN_DIR / "embeddings_golden.json", embeddings_golden())
        _dump(GOLDEN_DIR / "rounding_golden.json", rounding_golden())
        _dump(GOLDEN_DIR / "manifest.json", manifest())
    # ``KnowledgeGraphStore.__init__`` creates its blob directory eagerly; the
    # fixture has no blobs, and an empty directory beside a committed artefact is
    # just a thing for the next reader to wonder about.
    blob_dir = STORE_PATH.parent / "blobs"
    if blob_dir.is_dir() and not any(blob_dir.iterdir()):
        blob_dir.rmdir()
    size_kb = STORE_PATH.stat().st_size / 1024
    print(f"store: {STORE_PATH.relative_to(REPO_ROOT)} ({size_kb:.1f} KiB)")
    print(f"golden: {written} engine files + embeddings + rounding + manifest")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
