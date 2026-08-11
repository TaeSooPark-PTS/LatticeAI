#!/usr/bin/env python3
"""Build the committed Python↔Rust retrieval parity fixture.

``rust/lattice-retrieval`` is a port, and a port is only worth having if
something keeps proving it is still one. This script is the Python half of that
proof: it builds a small, fully deterministic Brain with the **real** write path
(``KnowledgeGraphStore._upsert_node`` / ``_upsert_chunk`` / ``_upsert_edge``,
the real hash embedder, the real v2 projection and trigram FTS index), then runs
the real ``hybrid_search`` / ``search`` / ``vector_search`` over it and writes
their answers to ``rust/fixtures/golden/``.

v11.5.0 widens it past search: the same store also carries the conversation
corpus, and the same generator drives the KG relationship/traverse reads, the
service-layer graph and three-channel hybrid, the durable history reads and the
context assembler (:data:`SUITES`).

Two consumers read what it writes:

* ``tests/unit/test_rust_parity_contract.py`` re-runs the Python engines against
  the committed database and asserts the goldens still hold — so a change to
  Python semantics fails loudly instead of silently invalidating the contract
  the Rust side is pinned to;
* ``rust/lattice-retrieval/tests/parity.rs`` runs the Rust port against the same
  database and the same goldens.

Determinism is the whole design constraint: every timestamp is written by the
real code and then **backdated**; the two ``datetime.now()`` calls the ports
reach are frozen at :data:`FROZEN_NOW` (recorded in the manifest); LLM concept
extraction is forced off; every environment knob is pinned to its default.

Usage::

    .venv/bin/python scripts/generate_rust_parity_fixtures.py
"""

from __future__ import annotations

import json
import logging
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

#: The wall clock the ports see: a golden built against a moving clock is none.
FROZEN_NOW = "2026-08-01T12:00:00"

#: Every environment variable the ported path reads, pinned to the configuration
#: it targets (brute backend, RRF/expansion/rerank off, rewrite on).
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
# Shaped on purpose: every ``type_boost`` type appears and so do types outside it;
# titles/summaries are half Korean (the tokenizer, classifier and extractor all
# branch on script); the ``tie:`` block is five rows sharing one timestamp, one
# type and nothing to match, pinning the (hits, boost, updated_at) → id ASC
# tie-break; two workspaces plus NULL-workspace rows cover all three scoping
# answers (no scoping / empty set / a specific workspace).
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
# The shape mirrors ``KnowledgeGraphIngestMixin``: every chunk is BOTH a ``Chunk``
# node (lexical lane + workspace scoping) and a ``chunks`` row with its own
# embedding (vector lane, rolled up to its parent). Getting that duality wrong is
# the whole reason chunk-heavy queries are in the query set.
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

# (from, to, type, weight, created_at)
#
# Weights and timestamps are assigned rather than inherited: ``relationship_search``
# orders by ``weight DESC, created_at DESC, id ASC`` and ``traverse`` caps every BFS
# round with ``ORDER BY weight DESC, id ASC``, so an edge set sharing one weight and
# one clock proves nothing. Three shapes are deliberate — a weight tie broken by
# ``created_at`` (the ``org:lattice`` pair), a weight *and* clock tie broken by edge
# id (``topic:quality``/``deck:review``), and legacy-global endpoints for scoping.
# Types are canonicalized by the write door (``relates_to``/``owns`` → ``MENTIONS``),
# which is why the goldens record uppercase names the fixture never spells.
EDGES: List[Tuple[str, str, str, float, str]] = [
    ("dec:fusion-alpha", "concept:retrieval", "mentions", 0.9, "2026-07-01T00:00:00"),
    ("dec:fusion-alpha", "concept:ranking", "mentions", 0.8, "2026-07-02T00:00:00"),
    ("task:parity-harness", "dec:rust-foundation", "relates_to", 1.0, "2026-07-03T00:00:00"),
    ("doc:handbook", "page:onboarding", "contains", 0.7, "2026-07-04T00:00:00"),
    ("meeting:weekly", "dec:fusion-alpha", "discusses", 0.95, "2026-07-05T00:00:00"),
    ("person:jiwon", "task:parity-harness", "owns", 0.6, "2026-07-06T00:00:00"),
    ("person:minseo", "code:build-failure", "owns", 0.6, "2026-07-07T00:00:00"),
    ("meeting:weekly", "task:onboarding-checklist", "discusses", 0.55, "2026-07-08T00:00:00"),
    ("meeting:kickoff", "dec:rust-foundation", "discusses", 0.5, "2026-06-01T00:00:00"),
    ("doc:retrieval-spec", "concept:ranking", "mentions", 0.45, "2026-06-02T00:00:00"),
    ("concept:retrieval", "concept:ranking", "relates_to", 0.4, "2026-06-03T00:00:00"),
    ("file:ranking-notes", "dec:fusion-alpha", "relates_to", 0.35, "2026-06-04T00:00:00"),
    ("code:hybrid-search", "file:ranking-notes", "relates_to", 0.3, "2026-06-05T00:00:00"),
    # Same weight, different clock → created_at DESC decides.
    ("org:lattice", "person:jiwon", "contains", 0.25, "2026-06-06T00:00:00"),
    ("org:lattice", "person:minseo", "contains", 0.25, "2026-06-07T00:00:00"),
    ("slide:fusion", "concept:retrieval", "mentions", 0.25, "2026-06-08T00:00:00"),
    # Same weight AND same clock → the id ASC tie-break is the only thing left.
    ("topic:quality", "deck:review", "relates_to", 0.2, "2026-06-09T00:00:00"),
    ("deck:review", "meeting:weekly", "relates_to", 0.2, "2026-06-09T00:00:00"),
    ("task:onboarding-checklist", "page:onboarding", "relates_to", 0.15, "2026-05-01T00:00:00"),
    ("doc:handbook", "doc:retrieval-spec", "relates_to", 0.1, "2026-05-02T00:00:00"),
    ("tie:a", "tie:b", "relates_to", 1.0, "2026-07-03T00:00:00"),
]

# ── the conversation corpus (episodic memory, same database file) ────────────
# (conversation_id, role, content, user_email, nickname, source, timestamp,
#  workspace_id, organization_id, extra)
#
# Every branch the history reads take: two users × three workspaces, NULL and
# empty-string workspaces (the legacy rows ``_scope_sql`` admits), rows with no
# ``conversation_id`` (the ``legacy-previous-history`` bucket), a whitespace-only
# first message (the ``새 대화`` placeholder and its later upgrade), an
# assistant-first conversation (no upgrade), an empty timestamp (the ``or ""``
# fallbacks), extra keys ``metadata_json`` merges flat, and ko/en content.
MESSAGES: List[Tuple[Any, ...]] = [
    ("conv-a", "user", "온보딩 체크리스트 어떻게 시작해?", "jiwon@lattice.ai", "지원", "web", "2026-07-20T09:00:00", WS_ALPHA, "org-1", {}),
    ("conv-a", "assistant", "먼저 폴더를 연결하세요. 그다음 질문하면 됩니다.", "jiwon@lattice.ai", None, "web", "2026-07-20T09:00:05", WS_ALPHA, "org-1", {}),
    ("conv-a", "user", "고마워", "jiwon@lattice.ai", "지원", "web", "2026-07-20T09:01:00", WS_ALPHA, "org-1", {"trace_id": "t-1"}),
    ("conv-b", "user", "How does hybrid retrieval ranking work?", "minseo@lattice.ai", "Minseo", "telegram", "2026-07-21T10:00:00", WS_BETA, "org-1", {}),
    ("conv-b", "assistant", "The lexical channel scores one over rank.", "minseo@lattice.ai", None, "telegram", "2026-07-21T10:00:07", WS_BETA, "org-1", {"tokens": 42, "cited": ["doc:retrieval-spec"]}),
    ("conv-c", "user", "   \n  ", None, None, None, "2026-07-22T08:00:00", None, None, {}),
    ("conv-c", "assistant", "무엇을 도와드릴까요?", None, None, None, "2026-07-22T08:00:05", None, None, {}),
    ("conv-c", "user", "지난주 회의 기록 보여줘", None, None, None, "2026-07-22T08:01:00", None, None, {}),
    (None, "user", "이전 대화 기록입니다", "jiwon@lattice.ai", "지원", "web", "2026-06-01T09:00:00", "", None, {}),
    (None, "assistant", "네, 확인했습니다.", "jiwon@lattice.ai", None, "web", "2026-06-01T09:00:03", "", None, {}),
    (None, "user", "legacy english message about ranking", None, None, None, "", None, None, {}),
    ("conv-d", "user", "빌드 실패 원인 알려줘", "minseo@lattice.ai", "Minseo", "vscode", "2026-07-23T11:00:00", WS_ALPHA, "org-1", {}),
    ("conv-d", "assistant", "컴파일 오류 로그를 확인하세요.", "minseo@lattice.ai", None, "vscode", "2026-07-23T11:00:04", WS_ALPHA, "org-1", {}),
    ("conv-e", "user", "Ranking ties are broken by node id", "jiwon@lattice.ai", "지원", "web", "2026-07-24T12:00:00", WS_BETA, "org-2", {}),
    ("conv-e", "assistant", "Yes — id ascending keeps it stable.", "jiwon@lattice.ai", None, "web", "2026-07-24T12:00:05", WS_BETA, "org-2", {}),
    ("conv-f", "user", "검색 품질을 어떻게 측정하나요? 재현율과 정밀도를 모두 보고 싶고 주간 회의에서 공유할 예정입니다.", None, None, "web", "2026-07-25T13:00:00", None, None, {}),
    ("conv-f", "assistant", "재현율/정밀도 지표는 분기 리뷰 발표자료에 있습니다.", None, None, "web", "2026-07-25T13:00:05", None, None, {}),
    ("conv-g", "assistant", "assistant-first conversation", None, None, "web", "2026-07-26T14:00:00", None, None, {}),
    ("conv-g", "user", "follow up question about ranking", None, None, "web", "2026-07-26T14:00:10", None, None, {}),
    ("conv-h", "user", "Empty user and empty workspace row", "", "", "", "2026-07-27T15:00:00", "", "", {}),
    ("conv-h", "assistant", "회의 결정 사항을 정리했습니다.", "", "", "", "2026-07-27T15:00:06", "", "", {}),
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
    # A vector floor nothing clears — the only way to reach the stale-embedder
    # probe and the lexical-only fusion label without breaking the store.
    {"key": "min_vector_floor", "query": "hybrid retrieval ranking", "min_vector": 0.95},
    # top_k small enough that the rerank window (top_k * 2) cuts the candidates.
    {"key": "top_k_small", "query": "hybrid retrieval ranking", "top_k": 3},
    # Pinned alpha: no policy, so no class, no rewrite, and no age decay on a
    # query that would otherwise be recency-classed.
    {"key": "alpha_pinned", "query": "지난주 회의 기록", "alpha": 0.2},
    # A limit below the FTS hit count, so `ORDER BY rank LIMIT ?` decides which
    # rows exist at all — the one place bm25 ordering (and therefore a SQLite
    # version difference between the two runtimes) is observable.
    {"key": "fts_rank_cut", "query": "Tie candidate", "limit": 2, "top_k": 2},
]

#: Every branch of the memories section: a workspace hit, one with no kind and an
#: empty snippet, a non-workspace row to drop, and one past the limit.
CONTEXT_MEMORIES: Dict[str, Any] = {
    "results": [
        {"id": "mem-1", "kind": "preference", "snippet": "답변은 한국어로", "score": 0.91, "source": "workspace"},
        {"id": "mem-2", "kind": None, "snippet": "", "score": 0.0, "source": "workspace"},
        {"id": "mem-3", "kind": "decision", "snippet": "ranking keeps alpha fusion", "score": 0.4, "source": "personal"},
        {"id": "mem-4", "kind": "fact", "snippet": "온보딩은 다섯 걸음", "score": 0.3, "source": "workspace"},
    ]
}

#: A ledger with a pathless row, a non-dict row, and more than the ten-row cut.
CONTEXT_ARTIFACTS: List[Any] = [
    {"path": "notes/ranking.md", "at": "2026-07-20T09:00:00", "run_id": "run-1"},
    {"path": "notes/onboarding.md", "run_id": "run-2"},
    {"path": "", "at": "2026-07-20T09:00:01"},
    "not-a-dict",
] + [{"path": f"out/file-{index}.md", "at": None, "run_id": f"r{index}"} for index in range(10)]

#: The Phase-2/3 suites: one spec list per ported entry point.
SUITES: Dict[str, List[Dict[str, Any]]] = {
    "relationship": [
        {"key": "all"},
        {"key": "by_type_mention", "relationship_type": "mention"},
        {"key": "by_type_contains", "relationship_type": "CONTAINS"},
        {"key": "by_type_unknown", "relationship_type": "relates_to"},
        {"key": "by_node", "node_id": "dec:fusion-alpha"},
        {"key": "by_query_ko", "query": "회의"},
        {"key": "by_query_en", "query": "ranking"},
        {"key": "by_query_meta", "query": "lattice"},
        {"key": "combined", "node_id": "dec:fusion-alpha", "relationship_type": "mention", "query": "retrieval"},
        {"key": "limit_one", "limit": 1},
        {"key": "limit_zero", "limit": 0},
        {"key": "limit_over", "limit": 500},
        {"key": "scoped_alpha", "allowed": [WS_ALPHA]},
        {"key": "scoped_alpha_legacy", "allowed": [WS_ALPHA], "legacy": True},
        {"key": "scoped_beta", "allowed": [WS_BETA]},
        {"key": "scoped_empty", "allowed": []},
        {"key": "no_hit", "query": "zzqq wumpus"},
    ],
    "traverse": [
        # 9 clamps to 4 and -1 clamps to 0; both are on purpose.
        *[{"key": f"hub_d{depth}", "node_id": "dec:fusion-alpha", "depth": depth}
          for depth in (0, 1, 2, 3, 9)],
        {"key": "hub_dneg", "node_id": "dec:fusion-alpha", "depth": -1},
        {"key": "leaf_d2", "node_id": "code:hybrid-search", "depth": 2},
        {"key": "isolated", "node_id": "tie:c", "depth": 2},
        {"key": "limit_two", "node_id": "dec:fusion-alpha", "depth": 3, "limit": 2},
        {"key": "limit_five", "node_id": "dec:fusion-alpha", "depth": 3, "limit": 5},
        {"key": "limit_zero", "node_id": "dec:fusion-alpha", "depth": 2, "limit": 0},
        {"key": "limit_over", "node_id": "dec:fusion-alpha", "depth": 2, "limit": 900},
        {"key": "org_hub", "node_id": "org:lattice", "depth": 2},
        {"key": "tie_pair", "node_id": "tie:a", "depth": 2},
        {"key": "scoped_alpha", "node_id": "dec:fusion-alpha", "depth": 2, "allowed": [WS_ALPHA]},
        {"key": "scoped_alpha_legacy", "node_id": "dec:fusion-alpha", "depth": 2, "allowed": [WS_ALPHA], "legacy": True},
        {"key": "scoped_seed_hidden", "node_id": "dec:fusion-alpha", "allowed": [WS_BETA]},
        {"key": "scoped_empty", "node_id": "dec:fusion-alpha", "allowed": []},
        {"key": "empty_id", "node_id": ""},
        {"key": "missing_seed", "node_id": "nope:missing"},
    ],
    "graph_search": [
        {"key": "en_fact", "query": "hybrid retrieval ranking"},
        {"key": "ko_fact", "query": "회의 결정 사항"},
        {"key": "person", "query": "who owns the onboarding checklist"},
        {"key": "code", "query": "빌드 실패 원인"},
        {"key": "expand0", "query": "hybrid retrieval ranking", "expand_depth": 0},
        {"key": "expand3", "query": "hybrid retrieval ranking", "expand_depth": 3},
        {"key": "expand_clamp", "query": "회의 결정 사항", "expand_depth": 9},
        {"key": "limit_small", "query": "hybrid retrieval ranking", "limit": 3},
        {"key": "limit_over", "query": "회의 결정 사항", "limit": 500},
        {"key": "scoped_alpha", "query": "hybrid retrieval ranking", "allowed": [WS_ALPHA]},
        {"key": "scoped_alpha_legacy", "query": "회의 결정 사항", "allowed": [WS_ALPHA], "legacy": True},
        {"key": "scoped_empty", "query": "hybrid retrieval ranking", "allowed": []},
        {"key": "no_hit", "query": "zzqq wumpus nonsense"},
        {"key": "empty_query", "query": ""},
    ],
    "service_hybrid": [
        {"key": "en_fact", "query": "hybrid retrieval ranking"},
        {"key": "ko_recency", "query": "지난주 회의 기록"},
        {"key": "en_code", "query": "vector_search() returns"},
        {"key": "ko_person", "query": "담당자 누구"},
        {"key": "en_filler", "query": "  what is   the retrieval specification please  "},
        # Explicit weights disable BOTH the rewrite and the age decay, on a
        # query that would otherwise get both. That asymmetry is the contract.
        {"key": "pinned_recency", "query": "지난주 회의 기록", "weights": {"keyword": 0.5, "vector": 0.3, "graph": 0.2}},
        {"key": "pinned_partial", "query": "hybrid retrieval ranking", "weights": {"graph": 1.0}},
        {"key": "pinned_zero", "query": "회의 결정 사항", "weights": {"keyword": 0.0, "vector": 0.0, "graph": 0.0}},
        {"key": "limit_small", "query": "hybrid retrieval ranking", "limit": 3},
        {"key": "limit_over", "query": "회의 결정 사항", "limit": 500},
        {"key": "channel_limits", "query": "hybrid retrieval ranking", "keyword_limit": 5, "vector_limit": 5, "graph_limit": 5},
        {"key": "scoped_alpha", "query": "hybrid retrieval ranking", "allowed": [WS_ALPHA]},
        {"key": "scoped_alpha_legacy", "query": "회의 결정 사항", "allowed": [WS_ALPHA], "legacy": True},
        {"key": "scoped_empty", "query": "hybrid retrieval ranking", "allowed": []},
        {"key": "no_hit", "query": "zzqq wumpus nonsense"},
        {"key": "empty_query", "query": ""},
    ],
    "history": [
        {"key": "all"},
        {"key": "limit_two", "limit": 2},
        {"key": "limit_zero", "limit": 0},
        {"key": "conv_a", "conversation_id": "conv-a"},
        {"key": "conv_missing", "conversation_id": "nope"},
        {"key": "conv_null", "conversation_id": ""},
        {"key": "user_jiwon", "user_email": "jiwon@lattice.ai"},
        {"key": "user_jiwon_strict", "user_email": "jiwon@lattice.ai", "legacy": False},
        {"key": "user_unknown", "user_email": "ghost@lattice.ai"},
        {"key": "ws_alpha", "allowed": [WS_ALPHA]},
        {"key": "ws_alpha_strict", "allowed": [WS_ALPHA], "legacy": False},
        {"key": "ws_both_strict", "allowed": [WS_ALPHA, WS_BETA], "legacy": False},
        {"key": "ws_empty_legacy", "allowed": []},
        {"key": "ws_empty_strict", "allowed": [], "legacy": False},
        {"key": "user_and_ws", "user_email": "jiwon@lattice.ai", "allowed": [WS_ALPHA], "legacy": False},
        {"key": "conv_and_user", "conversation_id": "conv-a", "user_email": "jiwon@lattice.ai", "legacy": False},
        {"key": "ws_blank_only", "allowed": [""], "legacy": False},
    ],
    "conversations": [
        {"key": "all"},
        {"key": "user_jiwon", "user_email": "jiwon@lattice.ai"},
        {"key": "user_jiwon_strict", "user_email": "jiwon@lattice.ai", "legacy": False},
        {"key": "ws_alpha_strict", "allowed": [WS_ALPHA], "legacy": False},
        {"key": "ws_beta_strict", "allowed": [WS_BETA], "legacy": False},
        {"key": "ws_empty_strict", "allowed": [], "legacy": False},
    ],
    "conversation_messages": [
        {"key": "conv_a", "conversation_id": "conv-a"},
        {"key": "legacy_bucket", "conversation_id": "legacy-previous-history"},
        {"key": "missing", "conversation_id": "nope"},
        {"key": "scoped_alpha_strict", "conversation_id": "conv-a", "allowed": [WS_ALPHA], "legacy": False},
        {"key": "legacy_bucket_scoped", "conversation_id": "legacy-previous-history", "allowed": [WS_ALPHA], "legacy": False},
    ],
    "history_search": [
        {"key": "ko_hit", "query": "회의"},
        {"key": "ko_partial", "query": "체크리스트"},
        {"key": "en_hit", "query": "ranking"},
        {"key": "case_insensitive", "query": "RANKING"},
        {"key": "blank", "query": "   "},
        {"key": "no_hit", "query": "zzqq"},
        {"key": "limit_one", "query": "ranking", "limit": 1},
        {"key": "scoped_strict", "query": "ranking", "allowed": [WS_BETA], "legacy": False},
    ],
    "context_assemble": [
        {"key": "all_seams", "query": "회의 결정 사항", "memories": CONTEXT_MEMORIES, "artifacts": CONTEXT_ARTIFACTS, "notes": "정원 노트: 랭킹은 alpha 융합을 유지한다.", "recent": {"limit": 4}},
        {"key": "knowledge_only", "query": "hybrid retrieval ranking"},
        {"key": "no_seams", "query": "hybrid retrieval ranking", "knowledge": False},
        {"key": "memories_only", "query": "온보딩", "knowledge": False, "memories": CONTEXT_MEMORIES, "memory_limit": 2},
        {"key": "artifacts_only", "query": "온보딩", "knowledge": False, "artifacts": CONTEXT_ARTIFACTS},
        {"key": "notes_blank", "query": "온보딩", "knowledge": False, "notes": "   "},
        {"key": "recent_conversation", "query": "온보딩", "knowledge": False, "recent": {"conversation_id": "conv-a", "limit": 10}},
        {"key": "recent_personal_workspace", "query": "온보딩", "knowledge": False, "recent": {"workspace_id": "personal", "limit": 6}},
        {"key": "recent_user_scoped", "query": "온보딩", "knowledge": False, "recent": {"user_email": "jiwon@lattice.ai", "limit": 5}},
        {"key": "budget_tiny", "query": "회의 결정 사항", "memories": CONTEXT_MEMORIES, "artifacts": CONTEXT_ARTIFACTS, "notes": "정원 노트: 랭킹은 alpha 융합을 유지한다.", "recent": {"limit": 4}, "budget": 20},
        {"key": "budget_one", "query": "회의 결정 사항", "memories": CONTEXT_MEMORIES, "artifacts": CONTEXT_ARTIFACTS, "notes": "노트", "recent": {"limit": 4}, "budget": 1},
        {"key": "budget_zero", "query": "회의 결정 사항", "memories": CONTEXT_MEMORIES, "notes": "노트", "budget": 0},
        {"key": "knowledge_limit_one", "query": "hybrid retrieval ranking", "knowledge_limit": 1},
    ],
}

#: Texts whose tokenizer output, hashes and vectors pin the embedding port.
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

#: Values separating CPython round-half-even from naive scaling, plus real shapes.
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
    """Freeze every ported ``datetime.now()`` at :data:`FROZEN_NOW`.

    Only the recency-class age decay reads the clock, through the ``datetime``
    name its module imported — so rebinding that name is the whole patch. Two
    modules do it: the graph-layer and the service-layer ``hybrid_search``.
    """
    from lattice_brain.graph.retrieval import hybrid as hybrid_module
    from latticeai.services import search_service as service_module

    frozen = datetime.fromisoformat(FROZEN_NOW)

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ARG003 — mirrors datetime.now's signature
            return frozen

    modules = (hybrid_module, service_module)
    originals = [module.datetime for module in modules]
    for module in modules:
        module.datetime = _FrozenDatetime
    try:
        yield
    finally:
        for module, original in zip(modules, originals, strict=True):
            module.datetime = original


@contextmanager
def rules_only_extraction() -> Iterator[None]:
    """Force ``_topic_candidates`` down its rule-based path.

    The LLM path needs a bound router no fixture run has, but "no router happens
    to be bound" is an accident and this contract cannot rest on one.
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


def open_conversations(db_path: Path):
    """A ``ConversationStore`` over ``db_path`` (the same file as the graph)."""
    from lattice_brain.conversations import ConversationStore
    return ConversationStore(Path(db_path))


def write_conversations(db_path: Path) -> None:
    """Append :data:`MESSAGES` through the real durable-history write path."""
    conversations = open_conversations(db_path)
    for conv_id, role, content, email, nick, source, stamp, workspace, org, extra in MESSAGES:
        conversations.append({
            "conversation_id": conv_id, "role": role, "content": content,
            "user_email": email, "user_nickname": nick, "source": source,
            "timestamp": stamp, "workspace_id": workspace, "organization_id": org,
            **extra,
        })


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
        for from_node, to_node, edge_type, weight, stamp in EDGES:
            # ``_upsert_edge`` stamps ``created_at`` from the wall clock in BOTH
            # tables, and the read path is the ``kgv2_edges`` view over ``edges_v2``:
            # backdating only the legacy table (as this generator first did) left
            # the relationship ordering moving with the clock.
            edge_id = store._upsert_edge(conn, from_node, to_node, edge_type, weight, {})
            conn.execute("UPDATE edges SET created_at=? WHERE id=?", (stamp, edge_id))
            conn.execute("UPDATE edges_v2 SET created_at=? WHERE id=?", (stamp, edge_id))
        # ``indexed_at`` decides the candidate scan order (and, when the cap bites,
        # which candidates exist at all), so it is assigned rather than inherited.
        item_ids = [row["item_id"] for row in conn.execute(
            "SELECT item_id FROM vector_embeddings ORDER BY item_id ASC"
        ).fetchall()]
        for seq, item_id in enumerate(item_ids):
            stamp = f"2026-03-01T{seq // 60:02d}:{seq % 60:02d}:00"
            conn.execute(
                "UPDATE vector_embeddings SET indexed_at=? WHERE item_id=?", (stamp, item_id)
            )
    store.record_embedder_fingerprint()
    write_conversations(db_path)

    # Leave one self-contained file behind: checkpoint the WAL, drop back to a
    # rollback journal so no sidecar is committed, and compact.
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
        spec["query"], top_k=spec.get("top_k", 20), alpha=spec.get("alpha"),
        allowed_workspaces=_allowed(spec), min_vector_score=spec.get("min_vector", 0.0),
        include_legacy_global=spec.get("legacy", False),
    ),
    "keyword": lambda store, spec: store.search(
        spec["query"], spec.get("limit", 30), allowed_workspaces=_allowed(spec),
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


# ── suites (v11.5.0): KG reads, the service layer, history, context ──────────
#
# The Phase-1 engines share one query shape (a query set × an engine set); the
# Phase-2/3 ports do not, so each gets its own spec list and runner, both carried
# in the manifest so Rust and Python enumerate exactly the same work.


class Harness:
    """Every Python entry point the v11.5.0 goldens are produced from.

    ``require_auth=False`` is the loopback-owner configuration the native routes
    reproduce: the history scope is whatever the caller passes explicitly.
    """

    def __init__(self, db_path: Path):
        from latticeai.runtime.history_runtime import build_history_query_runtime
        from latticeai.services.chat_service import ChatService
        from latticeai.services.search_service import SearchService
        self.store = open_store(db_path)
        self.service = SearchService(graph_store=self.store)
        self.conversations = open_conversations(db_path)
        self.history_runtime = build_history_query_runtime(
            conversations=self.conversations,
            workspace_service=None,
            require_auth=False,
            logging=logging,
        )
        self.chat = ChatService(store=None, get_history=self.history_runtime["get_history"])


def _allowed_list(spec: Dict[str, Any]):
    """``allowed`` as the graph layer wants it: ``None`` or a set."""
    allowed = spec.get("allowed")
    return None if allowed is None else set(allowed)


def _history_scope(spec: Dict[str, Any]) -> Dict[str, Any]:
    """The identity/workspace scope every history read takes.

    ``include_legacy_global`` defaults to ``True`` — ``ConversationStore``'s own
    default, the opposite of the graph layer's and the kind of asymmetry a port
    gets wrong.
    """
    allowed = spec.get("allowed")
    return {
        "user_email": spec.get("user_email"),
        "allowed_workspaces": None if allowed is None else list(allowed),
        "include_legacy_global": spec.get("legacy", True),
    }


def _run_relationship(h: Harness, spec: Dict[str, Any]) -> Dict[str, Any]:
    return h.store.relationship_search(
        query=spec.get("query", ""), node_id=spec.get("node_id", ""),
        relationship_type=spec.get("relationship_type", ""), limit=spec.get("limit", 30),
        allowed_workspaces=_allowed_list(spec),
        include_legacy_global=spec.get("legacy", False),
    )


def _run_traverse(h: Harness, spec: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return h.store.traverse(
            spec.get("node_id", ""), depth=spec.get("depth", 1),
            limit=spec.get("limit", 100), allowed_workspaces=_allowed_list(spec),
            include_legacy_global=spec.get("legacy", False),
        )
    except ValueError as exc:
        # The two documented refusals (blank id, seed invisible to the caller's
        # scope) are contract, so they are recorded rather than skipped; a payload
        # never carries an "error" key, so the golden stays unambiguous.
        return {"error": str(exc)}


def _run_graph_search(h: Harness, spec: Dict[str, Any]) -> Dict[str, Any]:
    return h.service.graph_search(
        spec["query"], limit=spec.get("limit", 30),
        expand_depth=spec.get("expand_depth", 1), allowed_workspaces=_allowed_list(spec),
        include_legacy_global=spec.get("legacy", False),
    )


def _run_service_hybrid(h: Harness, spec: Dict[str, Any]) -> Dict[str, Any]:
    return h.service.hybrid_search(
        spec["query"], limit=spec.get("limit", 30),
        keyword_limit=spec.get("keyword_limit", 30),
        vector_limit=spec.get("vector_limit", 30),
        graph_limit=spec.get("graph_limit", 30), weights=spec.get("weights"),
        allowed_workspaces=_allowed_list(spec),
        include_legacy_global=spec.get("legacy", False),
    )


def _run_history(h: Harness, spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    return h.conversations.history(
        conversation_id=spec.get("conversation_id"), limit=spec.get("limit"),
        **_history_scope(spec),
    )


def _run_conversations(h: Harness, spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    history = h.history_runtime["get_history"](**_history_scope(spec))
    return h.history_runtime["group_history_conversations"](history)


def _run_conversation_messages(h: Harness, spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    return h.history_runtime["get_conversation_messages"](
        spec["conversation_id"], **_history_scope(spec)
    )


def _run_history_search(h: Harness, spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    return h.chat.search_history(
        spec["query"], scope=_history_scope(spec), limit=spec.get("limit", 30),
        conversation_title=h.history_runtime["conversation_title"],
    )


def _context_seams(h: Harness, spec: Dict[str, Any]) -> Dict[str, Any]:
    """The seam set for one context spec — data seams plus the real engines.

    ``memories`` / ``artifacts`` / ``notes`` are *data* seams: the payload is the
    spec, so both runtimes feed the assembler the same bytes and what is under
    test is the assembler. ``knowledge`` and ``recent`` are real — the
    service-layer hybrid search and the durable history reader.
    """
    from latticeai.api.chat_helpers import build_recent_chat_context

    # Signatures matter: the assembler inspects them to decide which context
    # fields a seam may be handed, so each one declares exactly what it accepts.
    seams: Dict[str, Any] = {}
    if spec.get("memories") is not None:
        memories = spec["memories"]
        seams["memory_recall"] = (
            lambda query, *, user_email=None, workspace_id=None, limit=5: memories
        )
    if spec.get("artifacts") is not None:
        artifacts = spec["artifacts"]
        seams["recent_artifacts"] = (
            lambda *, user_email=None, conversation_id=None, workspace_id=None: artifacts
        )
    if spec.get("knowledge", True):
        # Loopback trust: no workspace scoping, exactly as on the native route.
        seams["hybrid_search"] = (
            lambda query, *, limit=5, user_email=None, workspace_id=None:
            h.service.hybrid_search(query, limit=limit)
        )
    if spec.get("notes") is not None:
        notes = spec["notes"]
        seams["notes_context"] = lambda query, *, user_email=None, workspace_id=None: notes
    if spec.get("recent") is not None:
        recent = spec["recent"]
        seams["recent_chat"] = (
            lambda *, user_email=None, conversation_id=None, workspace_id=None:
            build_recent_chat_context(
                get_history=h.history_runtime["get_history"],
                limit=recent.get("limit", 10),
                include_image_missing_replies=recent.get("images", True),
                user_email=recent.get("user_email"),
                conversation_id=recent.get("conversation_id"),
                workspace_id=recent.get("workspace_id"),
            )
        )
    return seams


def _run_context_assemble(h: Harness, spec: Dict[str, Any]) -> Dict[str, Any]:
    from lattice_brain.context import ContextAssembler

    assembled = ContextAssembler(**_context_seams(h, spec)).assemble(
        spec["query"], user_email=spec.get("user_email"),
        workspace_id=spec.get("workspace_id"),
        conversation_id=spec.get("conversation_id"), budget=spec.get("budget", 2000),
        memory_limit=spec.get("memory_limit", 5),
        knowledge_limit=spec.get("knowledge_limit", 5),
    )
    return {"text": assembled.text, "approx_tokens": assembled.approx_tokens,
            "trace": assembled.trace()}


SUITE_RUNNERS: Dict[str, Callable[[Harness, Dict[str, Any]], Any]] = {
    "relationship": _run_relationship,
    "traverse": _run_traverse,
    "graph_search": _run_graph_search,
    "service_hybrid": _run_service_hybrid,
    "history": _run_history,
    "conversations": _run_conversations,
    "conversation_messages": _run_conversation_messages,
    "history_search": _run_history_search,
    "context_assemble": _run_context_assemble,
}


def run_suite(harness: Harness, suite: str, spec: Dict[str, Any]) -> Any:
    """Run one suite spec under the frozen clock and the rule-based extractor."""
    with frozen_clock(), rules_only_extraction():
        return SUITE_RUNNERS[suite](harness, spec)


def golden_path(engine: str, key: str) -> Path:
    return GOLDEN_DIR / f"{engine}__{key}.json"


def golden_payload(engine: str, spec: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "engine": engine,
        "key": spec["key"],
        "query": spec["query"],
        "params": {
            "top_k": spec.get("top_k", 20), "alpha": spec.get("alpha"),
            "limit": spec.get("limit", 30), "min_score": spec.get("min_score", 0.0),
            "min_vector_score": spec.get("min_vector", 0.0),
            "allowed_workspaces": spec.get("allowed"),
            "include_legacy_global": spec.get("legacy", False),
        },
        "result": result,
    }


def suite_payload(suite: str, spec: Dict[str, Any], result: Any) -> Dict[str, Any]:
    """One suite golden: the spec that produced it, verbatim, and the answer.

    The spec rides along rather than being flattened into a fixed ``params``
    block: these entry points share no parameter shape.
    """
    return {"suite": suite, "key": spec["key"], "spec": spec, "result": result}


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
    return [{"input": v, "expected": round(v, 6)} for v in ROUNDING_VALUES]


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
        "suites": {suite: SUITES[suite] for suite in sorted(SUITES)},
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
        harness = Harness(STORE_PATH)
        for suite in sorted(SUITES):
            for spec in SUITES[suite]:
                result = run_suite(harness, suite, spec)
                _dump(golden_path(suite, spec["key"]), suite_payload(suite, spec, result))
                written += 1
        _dump(GOLDEN_DIR / "embeddings_golden.json", embeddings_golden())
        _dump(GOLDEN_DIR / "rounding_golden.json", rounding_golden())
        _dump(GOLDEN_DIR / "manifest.json", manifest())
    # ``KnowledgeGraphStore.__init__`` creates its blob directory eagerly and the
    # fixture has no blobs; an empty directory beside an artefact is just noise.
    blob_dir = STORE_PATH.parent / "blobs"
    if blob_dir.is_dir() and not any(blob_dir.iterdir()):
        blob_dir.rmdir()
    size_kb = STORE_PATH.stat().st_size / 1024
    print(f"store: {STORE_PATH.relative_to(REPO_ROOT)} ({size_kb:.1f} KiB)")
    print(f"golden: {written} engine files + embeddings + rounding + manifest")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
