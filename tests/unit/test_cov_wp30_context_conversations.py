"""wp30 coverage — context assembly seams and the durable conversation store.

Context: every retrieval seam is optional and every section builder is failure
isolated, so an unconfigured or exploding seam must degrade to "section
omitted" instead of taking the turn down — and the budget must trim from the
lowest-priority end. Conversations: the pre-v4 schema migration, malformed
metadata rows, legacy JSON import refusals, and workspace scoping when the
caller is allowed *no* workspace at all.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lattice_brain.context import (
    ContextAssembler,
    _call_context_seam,
    _call_keyword_seam,
    _SeamUnavailable,
)
from lattice_brain.conversations import ConversationStore

LEGACY_SCHEMA = """
CREATE TABLE conversation_messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  message_hash TEXT NOT NULL UNIQUE,
  conversation_id TEXT,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  user_email TEXT,
  user_nickname TEXT,
  source TEXT,
  timestamp TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}'
);
"""


class _Uninspectable:
    """Callable whose signature cannot be read (legacy adapter shape)."""

    __signature__ = "not a signature object"

    def __init__(self):
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return {"results": [], "matches": []}


def _boom(*args, **kwargs):
    raise RuntimeError("seam exploded")


# ── seam helpers ─────────────────────────────────────────────────────────────

def test_seams_refuse_when_unconfigured():
    with pytest.raises(_SeamUnavailable, match="not configured"):
        _call_context_seam(None, "query", limit=3)
    with pytest.raises(_SeamUnavailable, match="not configured"):
        _call_keyword_seam(None, limit=3)


def test_uninspectable_seams_receive_every_context_field():
    import inspect

    seam = _Uninspectable()
    with pytest.raises(TypeError):
        inspect.signature(seam)

    _call_context_seam(seam, "query", limit=5, user_email="u@x", workspace_id="w1")
    assert seam.calls[-1] == (("query",), {"limit": 5, "user_email": "u@x", "workspace_id": "w1"})

    _call_keyword_seam(seam, user_email="u@x", conversation_id="c1", workspace_id="w1")
    assert seam.calls[-1] == ((), {"user_email": "u@x", "conversation_id": "c1", "workspace_id": "w1"})


def test_narrow_legacy_signatures_only_receive_what_they_declare():
    def narrow(query, limit=3):
        return {"matches": [{"id": "m1", "title": "t", "score": 0.0}][:limit]}

    result = _call_context_seam(narrow, "q", limit=1, user_email="u@x", workspace_id="w1")
    assert result["matches"][0]["id"] == "m1"


# ── section builders: failure isolation ──────────────────────────────────────

def test_memories_section_without_a_configured_seam_is_empty():
    section = ContextAssembler()._memories_section("q", "u@x", "w1", 5)
    assert section.source == "memory"
    assert section.content == ""
    assert section.provenance == []


def test_every_section_survives_an_exploding_seam():
    assembler = ContextAssembler(
        memory_recall=_boom,
        hybrid_search=_boom,
        notes_context=_boom,
        recent_chat=_boom,
        recent_artifacts=_boom,
    )
    assembled = assembler.assemble("q", user_email="u@x", conversation_id="c1")
    # Every section degraded to empty and was dropped — no fabricated content.
    assert assembled.sections == []
    assert assembled.text == ""
    assert assembled.trace()["sections"] == []


def test_sections_carry_provenance_when_the_seams_answer():
    assembler = ContextAssembler(
        memory_recall=lambda q, **kw: {
            "results": [
                {"id": "m1", "source": "workspace", "kind": "preference",
                 "snippet": "dark mode", "score": 0.0},
                {"id": "m2", "source": "elsewhere", "snippet": "ignored"},
            ]
        },
        hybrid_search=lambda q, **kw: {"matches": [{"id": "k1", "title": "Doc", "summary": "body"}]},
        notes_context=lambda q, **kw: "garden note",
        recent_chat=lambda **kw: "user: hi",
        recent_artifacts=lambda **kw: [{"path": "/tmp/a.md", "at": "12:00", "run_id": "r1"}, "junk"],
    )
    assembled = assembler.assemble("q", user_email="u@x", conversation_id="c1")
    names = [section.name for section in assembled.sections]
    assert names == [
        "User memories",
        "Files created in this conversation",
        "Knowledge",
        "Garden notes",
        "Recent conversation",
    ]
    memories = assembled.sections[0]
    # score 0.0 is preserved, not dropped as falsy.
    assert memories.provenance == [{"id": "m1", "kind": "preference", "score": 0.0}]
    assert "/tmp/a.md (12:00)" in assembled.sections[1].content


def test_budget_trims_from_the_lowest_priority_end():
    assembler = ContextAssembler(
        memory_recall=lambda q, **kw: {
            "results": [{"id": "m1", "source": "workspace", "kind": "note",
                         "snippet": "x" * 400}]
        },
        notes_context=lambda q, **kw: "y" * 400,
        recent_chat=lambda **kw: "z" * 400,
    )
    assembled = assembler.assemble("q", budget=60)
    memories, notes, recent = assembled.sections
    assert memories.truncated is True and memories.content
    assert notes.truncated is True
    assert recent.truncated is True and recent.content == ""
    assert assembled.trace()["budget_approx_tokens"] == 60


# ── conversation store ───────────────────────────────────────────────────────

def _legacy_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "kg.sqlite"
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(LEGACY_SCHEMA)
        conn.execute(
            "INSERT INTO conversation_messages"
            " (message_hash, conversation_id, role, content, timestamp, metadata_json)"
            " VALUES ('h1', 'c1', 'user', 'hello', '2026-01-01T00:00:00Z', ?)",
            (json.dumps({"workspace_id": "w1", "organization_id": "org1"}),),
        )
        conn.execute(
            "INSERT INTO conversation_messages"
            " (message_hash, conversation_id, role, content, timestamp, metadata_json)"
            " VALUES ('h2', 'c1', 'assistant', 'hi', '2026-01-01T00:00:01Z', '{oops')"
        )
    conn.close()
    return db_path


def test_pre_v4_schema_is_migrated_and_backfilled_from_metadata(tmp_path):
    db_path = _legacy_db(tmp_path)
    store = ConversationStore(db_path)

    with sqlite3.connect(str(db_path)) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(conversation_messages)")}
    conn.close()
    assert {"workspace_id", "organization_id"} <= columns

    items = store.history(conversation_id="c1")
    assert [item["role"] for item in items] == ["user", "assistant"]
    assert items[0]["workspace_id"] == "w1"
    assert items[0]["organization_id"] == "org1"
    # The unparseable metadata row still reads back, without extra keys.
    assert items[1]["content"] == "hi"
    assert "workspace_id" not in items[1]


def test_legacy_json_import_refuses_unusable_files(tmp_path, caplog):
    store = ConversationStore(tmp_path / "kg.sqlite")

    assert store.import_legacy_json(tmp_path / "absent.json") == 0

    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    with caplog.at_level("WARNING"):
        assert store.import_legacy_json(broken) == 0
    assert "legacy import failed to read" in " ".join(r.getMessage() for r in caplog.records)

    not_a_list = tmp_path / "object.json"
    not_a_list.write_text(json.dumps({"role": "user"}), encoding="utf-8")
    assert store.import_legacy_json(not_a_list) == 0

    mixed = tmp_path / "mixed.json"
    mixed.write_text(
        json.dumps(["a bare string", {"role": "user", "content": "kept", "timestamp": "t"}]),
        encoding="utf-8",
    )
    assert store.import_legacy_json(mixed) == 1
    assert [item["content"] for item in store.history()] == ["kept"]


def test_history_limit_and_scoping_without_any_allowed_workspace(tmp_path):
    store = ConversationStore(tmp_path / "kg.sqlite")
    store.append({"role": "user", "content": "global", "timestamp": "1"})
    store.append({"role": "user", "content": "scoped", "timestamp": "2", "workspace_id": "w1"})

    assert [item["content"] for item in store.history(limit=1)] == ["global"]

    # Allowed-nothing + legacy visibility: only the unscoped row is readable.
    legacy_only = store.history(allowed_workspaces=[], include_legacy_global=True)
    assert [item["content"] for item in legacy_only] == ["global"]

    # Allowed-nothing without the legacy escape hatch: nothing at all.
    assert store.history(allowed_workspaces=[], include_legacy_global=False) == []


def test_size_bytes_reports_zero_when_the_database_cannot_be_stat_ed(tmp_path):
    store = ConversationStore(tmp_path / "kg.sqlite")
    assert store.size_bytes() > 0

    class _UnstatablePath:
        def exists(self):
            return True

        def stat(self):
            raise OSError("stat failed")

    store.db_path = _UnstatablePath()  # type: ignore[assignment]
    assert store.size_bytes() == 0
