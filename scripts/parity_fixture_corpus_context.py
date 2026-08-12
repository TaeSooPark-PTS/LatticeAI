#!/usr/bin/env python3
"""The context-assembly half of the Python↔Rust parity corpus (v11.5.2).

``scripts/generate_rust_parity_fixtures.py`` owns the store, the harness and the
goldens; this module owns the specs and runners for the two context ports, so
neither file grows past the tree's file-size ceiling. The split follows the
``parity_fixture_corpus_docgen`` precedent: specs and their runners live side by
side, because a spec list that arrives without the runner that answers it is a
suite that silently checks nothing.

Two ports, deliberately separate:

* **``context_assemble``** pins :class:`~lattice_brain.context.ContextAssembler`
  over the seams the *production* assembler supplies
  (``latticeai/runtime/context_runtime.py``): memories, artifacts, hybrid
  knowledge and garden notes. It does **not** supply ``recent_chat`` — that seam
  has never been wired live, and a golden that pinned it claimed cross-runtime
  agreement about a code path the product does not execute.
* **``recent_chat``** pins the recent-conversation transcript as the product
  really builds it: ``chat.py`` prepends ``build_recent_chat_context(...)`` on
  every non-hybrid turn and the agent loop injects the same text each executor
  step. Its Rust twin is ``lattice_retrieval::history::recent_chat``.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List

#: ``h`` is the generator's ``Harness``. It is typed ``Any`` rather than
#: imported: the harness owns the live store and the service layer, so importing
#: it here would make the corpus depend on the generator that imports *it*.

#: The organization workspace the corpus seeds; the same literal the generator
#: and the docgen corpus use.
WS_ALPHA = "ws-alpha"

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

def _context_seams(h: Any, spec: Dict[str, Any]) -> Dict[str, Any]:
    """The seam set for one context spec — data seams plus the real engines.

    ``memories`` / ``artifacts`` / ``notes`` are *data* seams: the payload is the
    spec, so both runtimes feed the assembler the same bytes and what is under
    test is the assembler. ``knowledge`` is a real engine — the service-layer
    hybrid search.

    There is deliberately no ``recent_chat`` seam here: the production
    assembler does not supply one, so a golden that did would pin agreement
    about a path neither runtime reaches in the product. The recent transcript
    is pinned by the ``recent_chat`` suite instead.
    """
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
    return seams


def _run_context_assemble(h: Any, spec: Dict[str, Any]) -> Dict[str, Any]:
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


def _run_recent_chat(h: Any, spec: Dict[str, Any]) -> str:
    """The recent-conversation transcript, exactly as ``/chat`` builds it."""
    from latticeai.api.chat_helpers import build_recent_chat_context

    return build_recent_chat_context(
        get_history=h.history_runtime["get_history"],
        limit=spec.get("limit", 10),
        include_image_missing_replies=spec.get("images", True),
        user_email=spec.get("user_email"),
        conversation_id=spec.get("conversation_id"),
        workspace_id=spec.get("workspace_id"),
    )


#: The two context suites, as the generator merges them into :data:`SUITES` /
#: :data:`SUITE_RUNNERS`.
CONTEXT_SUITES: Dict[str, List[Dict[str, Any]]] = {
    # 11.5.2: these pin the assembler over the seams the *production* assembler
    # supplies (`latticeai/runtime/context_runtime.py`). The `recent_chat` seam
    # is not one of them — it has never been wired live — so pinning it here
    # claimed cross-runtime agreement about a code path the product does not
    # run. The recent-conversation text itself is still pinned, in the
    # `recent_chat` suite below, against the function chat really calls.
    "context_assemble": [
        {"key": "all_seams", "query": "회의 결정 사항", "memories": CONTEXT_MEMORIES, "artifacts": CONTEXT_ARTIFACTS, "notes": "정원 노트: 랭킹은 alpha 융합을 유지한다."},
        {"key": "knowledge_only", "query": "hybrid retrieval ranking"},
        {"key": "no_seams", "query": "hybrid retrieval ranking", "knowledge": False},
        {"key": "memories_only", "query": "온보딩", "knowledge": False, "memories": CONTEXT_MEMORIES, "memory_limit": 2},
        {"key": "artifacts_only", "query": "온보딩", "knowledge": False, "artifacts": CONTEXT_ARTIFACTS},
        {"key": "notes_blank", "query": "온보딩", "knowledge": False, "notes": "   "},
        {"key": "budget_tiny", "query": "회의 결정 사항", "memories": CONTEXT_MEMORIES, "artifacts": CONTEXT_ARTIFACTS, "notes": "정원 노트: 랭킹은 alpha 융합을 유지한다.", "budget": 20},
        {"key": "budget_one", "query": "회의 결정 사항", "memories": CONTEXT_MEMORIES, "artifacts": CONTEXT_ARTIFACTS, "notes": "노트", "budget": 1},
        {"key": "budget_zero", "query": "회의 결정 사항", "memories": CONTEXT_MEMORIES, "notes": "노트", "budget": 0},
        {"key": "knowledge_limit_one", "query": "hybrid retrieval ranking", "knowledge_limit": 1},
    ],
    # The recent-conversation transcript as the product actually builds it:
    # `chat.py` prepends `build_recent_chat_context(...)` on every non-hybrid
    # turn, and the agent loop injects the same text each executor step. This
    # is the live twin of `lattice_retrieval::history::recent_chat`.
    "recent_chat": [
        {"key": "everything"},
        {"key": "conversation", "conversation_id": "conv-a"},
        {"key": "conversation_missing", "conversation_id": "nope"},
        {"key": "personal_workspace", "workspace_id": "personal", "limit": 6},
        {"key": "workspace_alpha", "workspace_id": WS_ALPHA},
        {"key": "user_scoped", "user_email": "jiwon@lattice.ai", "limit": 5},
        {"key": "user_and_conversation", "user_email": "jiwon@lattice.ai", "conversation_id": "conv-a"},
        {"key": "limit_one", "limit": 1},
        {"key": "limit_zero", "limit": 0},
        {"key": "image_replies_dropped", "images": False},
    ],
}

CONTEXT_RUNNERS: Dict[str, Callable[..., Any]] = {
    "context_assemble": _run_context_assemble,
    "recent_chat": _run_recent_chat,
}
