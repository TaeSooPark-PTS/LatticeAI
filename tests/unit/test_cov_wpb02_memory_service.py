"""wpb02 branch coverage — Brain brief, recall and compaction in MemoryService.

The brief builders each carry a "does this Brain have anything to recall?"
gate; the line suite only ever exercised the *has recall* side, so these tests
ask the same builders for the answer they give a Brain with none. The recall
and compaction tests cover the two records the happy path never produces: a
vector hit that carries no node id at all, and a duplicate memory row with no
id to delete by. Every backing store is an in-memory fake injected through the
constructor.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from latticeai.services.memory_service import MemoryService


class _Store:
    def __init__(self, memories: List[Dict[str, Any]] | None = None) -> None:
        self.memories = list(memories or [])
        self.deleted: List[str] = []

    def list_memories(self, **_kwargs: Any) -> Dict[str, Any]:
        return {"memories": list(self.memories)}

    def search_memories(self, _query: str, **_kwargs: Any) -> Dict[str, Any]:
        return {"memories": []}

    def delete_memory(self, memory_id: str) -> None:
        self.deleted.append(memory_id)
        self.memories = [m for m in self.memories if m.get("id") != memory_id]


class _KG:
    """A graph whose lexical search is empty but whose vector tier answers."""

    def __init__(self, vector_matches: List[Dict[str, Any]]) -> None:
        self._vector_matches = vector_matches

    def search(self, _query: str, _limit: int, **_kwargs: Any) -> Dict[str, Any]:
        return {"matches": []}

    def vector_search(self, _query: str, limit: int = 20) -> Dict[str, Any]:
        return {"matches": list(self._vector_matches)}


def _service(tmp_path: Path, store: _Store, kg: Any = None) -> MemoryService:
    return MemoryService(
        store=store,
        data_dir=tmp_path,
        knowledge_graph=kg,
        enable_graph=kg is not None,
    )


# ── brief builders without recall ───────────────────────────────────────────


def test_brief_actions_without_recall_offer_no_model_verification():
    actions = MemoryService._brain_brief_actions(
        state="alive",
        has_durable_evidence=True,
        has_recall=False,
        graph_concepts=3,
    )

    ids = [action["id"] for action in actions]
    assert "verify_model" not in ids
    assert ids == ["ask_brain", "inspect_topics", "backup_brain"]


def test_proactive_actions_without_recall_skip_the_evidence_trio():
    actions = MemoryService._brain_brief_proactive_actions(
        focus={"title": "Retrieval quality", "detail": "chunking"},
        state="alive",
        has_durable_evidence=True,
        has_recall=False,
        graph_concepts=4,
        vector_items=0,
        healthy_sources=1,
    )

    ids = [action["id"] for action in actions]
    assert "proactive_evidence_review" not in ids
    assert "proactive_delegate" not in ids
    assert "proactive_review_draft" not in ids
    assert "proactive_map_connections" in ids


def test_suggested_questions_without_recall_skip_the_evidence_check():
    questions = MemoryService._brain_brief_suggested_questions(
        focus={"title": "Retrieval quality", "kind": "concept"},
        has_durable_evidence=True,
        has_recall=False,
        graph_concepts=4,
        conversations=2,
    )

    ids = [question["id"] for question in questions]
    assert "evidence_check" not in ids
    assert ids[0] == "focus_next"
    assert "graph_connections" in ids


# ── _latest_recall_query ────────────────────────────────────────────────────


def test_an_empty_memory_is_skipped_when_choosing_the_latest_recall_query(tmp_path: Path):
    store = _Store(
        [
            {"id": "m1", "kind": "long_term", "content": "   "},
            {"id": "m2", "kind": "long_term", "content": "chunking strategy notes"},
        ]
    )

    query = _service(tmp_path, store)._latest_recall_query(
        user_email=None, workspace_id="personal"
    )

    assert query == "chunking strategy notes"


def test_an_empty_message_is_skipped_when_falling_back_to_conversations(tmp_path: Path):
    (tmp_path / "chat_history.json").write_text(
        json.dumps(
            {
                "conversations": [
                    {
                        "id": "c1",
                        "messages": [
                            {"role": "user", "content": "how is recall scored", "workspace_id": "personal"},
                            {"role": "assistant", "content": "  ", "workspace_id": "personal"},
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    query = _service(tmp_path, _Store())._latest_recall_query(
        user_email=None, workspace_id="personal"
    )

    # The newest message is blank, so the previous one answers instead.
    assert query == "how is recall scored"


# ── recall ──────────────────────────────────────────────────────────────────


def test_a_vector_hit_without_a_node_id_is_still_returned_but_not_indexed(tmp_path: Path):
    kg = _KG([{"score": 0.8, "title": "Chunking", "summary": "sentence aware", "type": "Concept"}])

    result = _service(tmp_path, _Store(), kg).recall("chunking")

    rows = result["results"]
    assert len(rows) == 1
    assert rows[0]["id"] is None
    assert rows[0]["vector_score"] == 0.8
    assert result["quality_gate"]["gate"] == "hybrid-evidence/v2"


# ── compact ─────────────────────────────────────────────────────────────────


def test_a_duplicate_memory_without_an_id_cannot_be_deleted(tmp_path: Path):
    store = _Store(
        [
            {"kind": "long_term", "content": "same thing"},
            {"id": "m1", "kind": "long_term", "content": "same thing"},
        ]
    )

    result = _service(tmp_path, store).compact()

    assert result == {
        "compacted": 0,
        "removed": [],
        "remaining": 1,
        "failed": [],
        "status": "ok",
    }
    assert store.deleted == []
