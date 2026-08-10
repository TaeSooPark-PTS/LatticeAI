"""wpb02 branch coverage — Brain Core value objects, storage and scoring.

Every test here drives the *un-taken* side of a guard that the line-coverage
suite already exercises in one direction only: a context section that is blank
rather than filled, a restore where the WAL sidecar is absent rather than
present, a fusion override naming a class or key the table does not have, a
rerank candidate whose ``scores`` slot holds something other than a dict, and
an extraction that is long-lined rather than fragmented. Nothing is stubbed
that the product owns — the SQLite engine, the conversation store and the
ingestion pipeline are the real ones, on ``tmp_path``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List

from lattice_brain.context import AssembledContext, ContextSection
from lattice_brain.conversations import ConversationStore
from lattice_brain.graph import proactive as proactive_mod
from lattice_brain.graph import rerank as rerank_mod
from lattice_brain.graph._kg_fsutil import _excluded_directory_reason
from lattice_brain.graph.fusion import fusion_weight_table
from lattice_brain.ingestion import (
    IngestionItem,
    IngestionPipeline,
    assess_extraction_quality,
)
from lattice_brain.memory import BrainMemory
from lattice_brain.quiet import quiet
from lattice_brain.storage.sqlite import SQLiteEngine


class _RecordingPipeline:
    """Minimal ingestion port for :class:`BrainMemory`."""

    def __init__(self) -> None:
        self.items: List[IngestionItem] = []

    def available(self) -> bool:
        return True

    def ingest(self, item: IngestionItem, *, user_email: str = None) -> Any:
        self.items.append(item)

        class _Result:
            @staticmethod
            def as_dict() -> Dict[str, Any]:
                return {"status": "ok", "node_id": "node:1"}

        return _Result()


class _RefusingGraph:
    """A graph whose write door always fails, so every ingest is an error."""

    db_path = None

    def ingest_source(self, **_kwargs: Any) -> Dict[str, Any]:
        raise RuntimeError("graph is read-only")

    def search(self, *_args: Any, **_kwargs: Any) -> Dict[str, Any]:
        return {"matches": []}


# ── context.py ──────────────────────────────────────────────────────────────


def test_a_blank_section_is_dropped_from_the_assembled_context_text():
    context = AssembledContext(
        sections=[
            ContextSection(name="memory", content="   \n\t ", source="memory"),
            ContextSection(name="notes", content="ship the release", source="notes"),
        ],
        budget_approx_tokens=100,
    )

    assert context.text == "[notes]\nship the release"
    assert "[memory]" not in context.text


# ── quiet.py ────────────────────────────────────────────────────────────────


def test_quiet_records_a_suppression_whose_exception_lost_its_traceback(caplog):
    caplog.set_level("DEBUG", logger="lattice_brain.suppressed")
    try:
        raise ValueError("no frames left")
    except ValueError as exc:
        exc.__traceback__ = None
        assert sys.exc_info()[2] is None
        quiet("detached")

    assert "suppressed ValueError at <unknown> (detached)" in caplog.text


# ── memory.py ───────────────────────────────────────────────────────────────


def test_an_experience_without_a_run_carries_no_run_metadata():
    pipeline = _RecordingPipeline()

    result = BrainMemory(pipeline).record_experience(
        "Indexed the design folder",
        "42 files",
        user_email="alice@example.com",
        workspace_id="ws-1",
        metadata={"origin": "manual"},
    )

    assert result == {"status": "ok", "node_id": "node:1"}
    assert pipeline.items[0].metadata == {"origin": "manual"}
    assert "run_id" not in pipeline.items[0].metadata


# ── storage/sqlite.py ───────────────────────────────────────────────────────


def test_restore_replaces_the_database_when_the_wal_sidecars_are_absent(tmp_path: Path):
    engine = SQLiteEngine(tmp_path / "brain.db", load_vec=False)
    engine.initialize()
    backup = engine.backup(tmp_path / "backups" / "brain-backup.db")
    assert Path(backup["path"]).exists()
    # A cleanly closed WAL database leaves no -wal/-shm sidecar behind.
    assert not (tmp_path / "brain.db-wal").exists()
    assert not (tmp_path / "brain.db-shm").exists()

    restored = engine.restore(Path(backup["path"]))

    assert restored == {
        "engine": "sqlite",
        "restored": True,
        "path": str(tmp_path / "brain.db"),
    }
    assert (tmp_path / "brain.db").exists()


# ── conversations.py ────────────────────────────────────────────────────────


def test_clearing_one_conversation_without_a_start_time_keeps_unattributed_messages(
    tmp_path: Path,
):
    store = ConversationStore(tmp_path / "conversations.db")
    store.append({"role": "user", "content": "scoped", "conversation_id": "c1"})
    store.append({"role": "user", "content": "unattributed"})

    result = store.clear_conversation("c1")

    assert result["status"] == "cleared"
    assert result["removed"] == 1
    assert result["kept"] == 1
    assert [item["content"] for item in store.history()] == ["unattributed"]


# ── graph/fusion.py ─────────────────────────────────────────────────────────


def test_fusion_overrides_ignore_an_unknown_class_and_an_unknown_key():
    table = fusion_weight_table(
        {
            "not-a-class": {"keyword": 0.9},
            "code": {"not-a-channel": 0.9, "alpha": 0.1},
        }
    )

    assert "not-a-class" not in table
    assert "not-a-channel" not in table["code"]
    assert table["code"]["alpha"] == 0.1
    assert table["code"]["keyword"] == 0.55


def test_a_non_mapping_override_for_a_known_class_is_ignored():
    table = fusion_weight_table({"fact": [("alpha", 0.1)]})

    assert table["fact"]["alpha"] == 0.60


# ── graph/rerank.py ─────────────────────────────────────────────────────────


def test_rerank_leaves_a_candidate_whose_scores_slot_is_not_a_dict(monkeypatch):
    class _Model:
        @staticmethod
        def predict(pairs):
            return [0.9 for _ in pairs]

    monkeypatch.setattr(rerank_mod, "_load_cross_encoder", lambda _mid: _Model())
    candidates = [
        {"node_id": "n1", "title": "a", "score": 0.1, "scores": ["not", "a", "dict"]},
    ]

    result = rerank_mod.cross_encoder_rerank("q", candidates, model_id="fake/model")

    match = result["matches"][0]
    assert match["rerank_score"] == 0.9
    assert match["score"] == 0.9
    assert match["scores"] == ["not", "a", "dict"]


# ── graph/_kg_fsutil.py ─────────────────────────────────────────────────────


def test_an_ordinary_linux_directory_is_not_excluded():
    assert _excluded_directory_reason(Path("/home/alice/notes"), os_type="linux") is None


# ── ingestion.py ────────────────────────────────────────────────────────────


def test_long_lines_are_not_reported_as_fragmented():
    text = "\n".join(f"line {index} carries a full clause of prose" for index in range(10))

    verdict = assess_extraction_quality(text)

    assert "fragmented_lines" not in verdict["reasons"]
    assert verdict["score"] > 0.0


def test_the_quality_gate_observation_omits_a_similarity_it_was_not_given(monkeypatch):
    pipeline = IngestionPipeline(_RefusingGraph())
    monkeypatch.setattr(
        proactive_mod,
        "gate_ingest_candidate",
        lambda body, search: {
            "action": "skip_duplicate",
            "reason": "same content already ingested",
            "similarity": None,
            "match_id": "node:existing",
        },
    )

    observed = pipeline._observe_quality_gate(
        IngestionItem(source_type="text", title="t", text="body"),
        source_type="text",
        text="body text that is long enough to observe",
    )

    assert observed == {
        "action": "skip_duplicate",
        "detail": "same content already ingested; match=node:existing",
    }


def test_folder_ingest_stops_appending_errors_once_the_cap_is_zero(tmp_path: Path):
    root = tmp_path / "notes"
    root.mkdir()
    (root / "a.txt").write_text("alpha content", encoding="utf-8")
    (root / "b.txt").write_text("beta content", encoding="utf-8")
    pipeline = IngestionPipeline(_RefusingGraph())

    summary = pipeline.ingest_folder(root, max_errors=0)

    assert summary["status"] == "partial"
    assert summary["failed"] == 2
    assert summary["errors"] == []
