"""Brain Chronicle service (v11.3.0 track B) against a real SQLite Brain.

Every test here builds an actual ``KnowledgeGraphStore`` +
``ConversationStore`` in ``tmp_path`` and populates it through the store's own
write APIs, then backdates the stamps — because the one thing a chronicle must
get right is *when*, and a fake that returns hand-written dicts would prove
nothing about the SQL, the workspace predicate, or the timezone.

The cases that earn their place: an empty Brain (the screen a new user sees),
a workspace boundary (another workspace's rows must be invisible, not merely
unlisted), the timezone day boundary (a Seoul midnight upload filing itself
under yesterday is the exact bug ``latticeai/core/timezones.py`` exists to
prevent), unreadable stamps (the conversation store really does write empty
timestamps), and storage that cannot be read at all.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from lattice_brain.conversations import ConversationStore
from lattice_brain.graph.schema import KGStoreV2
from lattice_brain.graph.store import KnowledgeGraphStore
from latticeai.services.chronicle import (
    ChronicleService,
    parse_day,
    parse_timestamp,
)


@pytest.fixture(autouse=True)
def _seoul(monkeypatch):
    """One configured timezone for the whole file; tz tests override it."""
    monkeypatch.setenv("LATTICE_TZ", "Asia/Seoul")


class Brain:
    """A real Brain on disk: graph + conversations in one SQLite file."""

    def __init__(self, tmp_path: Path) -> None:
        self.db = tmp_path / "knowledge_graph.sqlite"
        self.store = KnowledgeGraphStore(self.db, tmp_path / "blobs")
        self.conversations = ConversationStore(self.db)

    # ── writes (real APIs, then backdated) ──────────────────────────────
    def node(
        self,
        node_id: str,
        *,
        label: str = "",
        node_type: str = "Concept",
        created_at: str = "2026-08-01T10:00:00",
        workspace_id=None,
    ) -> str:
        with self.store._connect() as conn:
            self.store._upsert_node(
                conn,
                node_id,
                node_type,
                label or node_id.title(),
                "",
                {},
                workspace_id=workspace_id,
            )
            conn.execute(
                "UPDATE nodes_v2 SET created_at=?, updated_at=? WHERE id=?",
                (created_at, created_at, node_id),
            )
        return node_id

    def edge(
        self,
        source: str,
        target: str,
        *,
        edge_type: str = "mentions",
        observed_at=None,
        created_at: str = "2026-08-01T11:00:00",
    ) -> None:
        with self.store._connect() as conn:
            self.store._upsert_edge(conn, source, target, edge_type, 1.0, {})
            conn.execute(
                "UPDATE edges_v2 SET created_at=? WHERE source=? AND target=?",
                (created_at, source, target),
            )
            conn.execute(
                "UPDATE edge_occurrences SET observed_at=? WHERE edge_id IN "
                "(SELECT id FROM edges_v2 WHERE source=? AND target=?)",
                (observed_at or created_at, source, target),
            )

    def source(
        self,
        node_id: str,
        *,
        title: str = "Notes",
        source_type: str = "file",
        captured_at=None,
        created_at=None,
        workspace_id=None,
    ) -> str:
        record = self.store.record_provenance(
            node_id=node_id,
            source_type=source_type,
            source_uri=f"file://{node_id}",
            title=title,
            workspace_id=workspace_id,
            captured_at=captured_at,
        )
        if created_at is not None:
            with self.store._connect() as conn:
                conn.execute(
                    "UPDATE ingestion_provenance SET created_at=? WHERE id=?",
                    (created_at, record["id"]),
                )
        return str(record["id"])

    def message(
        self,
        content: str,
        *,
        role: str = "user",
        timestamp: str = "2026-08-01T12:00:00",
        conversation_id="c1",
        user_email="me@example.com",
        workspace_id=None,
    ) -> None:
        self.conversations.append(
            {
                "role": role,
                "content": content,
                "timestamp": timestamp,
                "conversation_id": conversation_id,
                "user_email": user_email,
                "workspace_id": workspace_id,
            }
        )

    def stamp(self, node_id: str, **fields) -> None:
        KGStoreV2(self.db).stamp_node_validity(node_id, **fields)

    def raw(self, sql: str, params=()) -> None:
        with self.store._connect() as conn:
            conn.execute(sql, params)

    def service(self) -> ChronicleService:
        return ChronicleService(
            knowledge_graph=self.store, conversations=self.conversations
        )


@pytest.fixture
def brain(tmp_path):
    return Brain(tmp_path)


def _series(payload, date):
    return next((row for row in payload["series"] if row["date"] == date), None)


# ── an empty Brain answers, honestly ─────────────────────────────────────────


def test_a_brain_with_nothing_in_it_reports_nothing_rather_than_failing(brain):
    payload = brain.service().overview()

    assert payload["first_activity_at"] is None
    assert payload["last_activity_at"] is None
    assert payload["totals"] == {
        "sources": 0,
        "entities": 0,
        "connections": 0,
        "conversations": 0,
    }
    assert payload["series"] == []


def test_an_empty_day_is_an_answer_not_a_404(brain):
    payload = brain.service().day("2026-08-01")

    assert payload["date"] == "2026-08-01"
    assert payload["counts"] == {
        "sources": 0,
        "entities": 0,
        "conversations": 0,
        "changes": 0,
    }
    assert payload["groups"] == {
        "sources": [],
        "entities": [],
        "conversations": [],
        "changes": [],
    }


# ── one day, then several ────────────────────────────────────────────────────


def test_a_single_day_lands_in_one_bucket_with_every_lane_counted(brain):
    brain.node("a", label="Alpha", created_at="2026-08-01T10:00:00")
    brain.node("b", label="Beta", created_at="2026-08-01T10:05:00")
    brain.edge("a", "b", created_at="2026-08-01T11:00:00")
    brain.source("a", title="Notes", captured_at="2026-08-01T09:00:00")
    brain.message("Hello", timestamp="2026-08-01T12:00:00")

    payload = brain.service().overview(user_email="me@example.com")

    assert payload["series"] == [
        {
            "date": "2026-08-01",
            "sources": 1,
            "entities": 2,
            "connections": 1,
            "conversations": 1,
        }
    ]
    assert payload["totals"] == {
        "sources": 1,
        "entities": 2,
        "connections": 1,
        "conversations": 1,
    }
    assert payload["first_activity_at"] == "2026-08-01T09:00:00"
    assert payload["last_activity_at"] == "2026-08-01T12:00:00"


def test_the_series_is_sparse_and_ascending_over_several_days(brain):
    brain.node("a", created_at="2026-08-01T10:00:00")
    brain.node("b", created_at="2026-08-05T10:00:00")
    brain.node("c", created_at="2026-08-03T10:00:00")

    payload = brain.service().overview()

    # Sparse on purpose: the two silent days in between are not rows of zeros,
    # they are absent, and the growth curve reads the series as steps.
    assert [row["date"] for row in payload["series"]] == [
        "2026-08-01",
        "2026-08-03",
        "2026-08-05",
    ]


def test_a_document_is_a_source_not_a_second_concept(brain):
    """An upload must not read as one source *and* one thing learned."""
    brain.node("doc", node_type="Document", created_at="2026-08-01T10:00:00")
    brain.node("idea", node_type="Concept", created_at="2026-08-01T10:00:00")
    brain.source("doc", captured_at="2026-08-01T09:00:00")

    payload = brain.service().overview()

    assert payload["totals"]["sources"] == 1
    assert payload["totals"]["entities"] == 1
    assert [item["id"] for item in brain.service().day("2026-08-01")["groups"]["entities"]] == [
        "idea"
    ]


def test_a_relationship_is_filed_under_when_it_was_first_observed(brain):
    """``edge_occurrences.observed_at`` wins over the row's own created_at."""
    brain.node("a", created_at="2026-08-01T10:00:00")
    brain.node("b", created_at="2026-08-01T10:00:00")
    brain.edge(
        "a",
        "b",
        created_at="2026-08-09T11:00:00",
        observed_at="2026-08-02T11:00:00",
    )

    payload = brain.service().overview()

    assert _series(payload, "2026-08-02")["connections"] == 1
    assert _series(payload, "2026-08-09") is None


def test_an_edge_with_no_recorded_sighting_falls_back_to_its_created_at(brain):
    brain.node("a", created_at="2026-08-01T10:00:00")
    brain.node("b", created_at="2026-08-01T10:00:00")
    brain.edge("a", "b", created_at="2026-08-04T11:00:00")
    brain.raw("DELETE FROM edge_occurrences")

    payload = brain.service().overview()

    assert _series(payload, "2026-08-04")["connections"] == 1


# ── the day's story ──────────────────────────────────────────────────────────


def test_a_day_returns_what_came_in_what_formed_and_what_was_said(brain):
    brain.node("a", label="Alpha", created_at="2026-08-02T10:00:00")
    brain.node("old", label="Older", created_at="2026-08-01T10:00:00")
    brain.source("a", title="Report", source_type="upload", captured_at="2026-08-02T09:00:00")
    brain.source("old", title="Yesterday", captured_at="2026-08-01T09:00:00")
    brain.message("Morning question", timestamp="2026-08-02T12:00:00")
    brain.message("Answer", role="assistant", timestamp="2026-08-02T12:00:05")

    payload = brain.service().day("2026-08-02", user_email="me@example.com")

    assert payload["counts"] == {
        "sources": 1,
        "entities": 1,
        "conversations": 1,
        "changes": 0,
    }
    assert payload["groups"]["sources"] == [
        {
            "id": payload["groups"]["sources"][0]["id"],
            "title": "Report",
            "source_type": "upload",
            "captured_at": "2026-08-02T09:00:00",
            "node_id": "a",
        }
    ]
    assert payload["groups"]["entities"] == [
        {"id": "a", "label": "Alpha", "type": "Concept", "created_at": "2026-08-02T10:00:00"}
    ]
    assert payload["groups"]["conversations"] == [
        {
            "conversation_id": "c1",
            "preview": "Morning question",
            "messages": 2,
            "started_at": "2026-08-02T12:00:00",
        }
    ]


def test_a_source_with_no_capture_time_is_dated_by_when_it_was_recorded(brain):
    brain.source("a", title="Scanned", captured_at=None, created_at="2026-08-07T08:00:00")

    payload = brain.service().day("2026-08-07")

    assert payload["counts"]["sources"] == 1
    assert payload["groups"]["sources"][0]["captured_at"] == "2026-08-07T08:00:00"


# ── conversations ────────────────────────────────────────────────────────────


def test_the_preview_is_the_first_line_of_the_first_thing_the_person_said(brain):
    brain.message("Assistant opener", role="assistant", timestamp="2026-08-01T12:00:00")
    brain.message("<b>What</b> did I save?\nsecond line", timestamp="2026-08-01T12:00:01")

    card = brain.service().day("2026-08-01")["groups"]["conversations"][0]

    # Tags stripped (a preview is never raw HTML), first line only, and the
    # person's question rather than whatever the assistant happened to open with.
    assert card["preview"] == "What did I save?"
    assert card["messages"] == 2
    assert card["started_at"] == "2026-08-01T12:00:00"


def test_a_conversation_the_person_never_spoke_in_still_previews(brain):
    brain.message("Only the assistant", role="assistant", timestamp="2026-08-01T12:00:00")

    card = brain.service().day("2026-08-01")["groups"]["conversations"][0]

    assert card["preview"] == "Only the assistant"


def test_a_long_first_line_is_truncated_with_an_ellipsis(brain):
    brain.message("배" * 400, timestamp="2026-08-01T12:00:00")

    preview = brain.service().day("2026-08-01")["groups"]["conversations"][0]["preview"]

    assert len(preview) == 140
    assert preview.endswith("…")


def test_an_empty_message_previews_as_empty_rather_than_crashing(brain):
    brain.message("\n\n", timestamp="2026-08-01T12:00:00")

    card = brain.service().day("2026-08-01")["groups"]["conversations"][0]

    assert card["preview"] == ""


def test_imported_history_with_no_conversation_id_becomes_one_card_a_day(brain):
    brain.message("legacy one", conversation_id=None, timestamp="2026-08-01T12:00:00")
    brain.message("legacy two", conversation_id=None, timestamp="2026-08-01T13:00:00")

    payload = brain.service().day("2026-08-01")

    assert payload["counts"]["conversations"] == 1
    assert payload["groups"]["conversations"][0]["conversation_id"] == ""
    assert payload["groups"]["conversations"][0]["messages"] == 2


def test_two_conversations_on_one_day_are_counted_separately(brain):
    brain.message("first", conversation_id="c1", timestamp="2026-08-01T12:00:00")
    brain.message("second", conversation_id="c2", timestamp="2026-08-01T13:00:00")

    payload = brain.service().overview()

    assert _series(payload, "2026-08-01")["conversations"] == 2
    assert payload["totals"]["conversations"] == 2


# ── what changed ─────────────────────────────────────────────────────────────


def test_a_superseded_fact_shows_up_as_a_change_on_the_day_it_changed(brain):
    brain.node("old", label="Coffee at 3pm", created_at="2026-07-01T09:00:00")
    brain.node("new", label="Coffee at 4pm", created_at="2026-08-01T09:00:00")
    brain.stamp("old", valid_to="2026-08-01T09:00:00", superseded_by="new")

    payload = brain.service().day("2026-08-01")

    assert payload["counts"]["changes"] == 1
    assert payload["groups"]["changes"] == [
        {
            "kind": "fact_superseded",
            "label": "Coffee at 3pm",
            "at": "2026-08-01T09:00:00",
            "node_id": "old",
        }
    ]


def test_a_fact_retired_without_a_successor_reads_as_retired(brain):
    brain.node("gone", label="Old address", created_at="2026-07-01T09:00:00")
    brain.stamp("gone", valid_to="2026-08-01T10:00:00")

    changes = brain.service().day("2026-08-01")["groups"]["changes"]

    assert [card["kind"] for card in changes] == ["fact_retired"]


def test_a_supersede_with_no_end_stamp_is_dated_by_the_row_it_updated(brain):
    """``mark_superseded`` writes the chain but no ``valid_to``."""
    brain.node("a", label="A", created_at="2026-07-01T09:00:00")
    brain.node("b", label="B", created_at="2026-07-01T09:00:00")
    brain.store.mark_superseded("a", "b")
    brain.raw("UPDATE nodes_v2 SET updated_at=? WHERE id='a'", ("2026-08-06T15:00:00",))

    changes = brain.service().day("2026-08-06")["groups"]["changes"]

    assert changes == [
        {
            "kind": "fact_superseded",
            "label": "A",
            "at": "2026-08-06T15:00:00",
            "node_id": "a",
        }
    ]


def test_an_ended_relationship_is_a_change_too_and_names_both_ends(brain):
    brain.node("a", label="Alpha", created_at="2026-07-01T09:00:00")
    brain.node("b", label="Beta", created_at="2026-07-01T09:00:00")
    brain.node("c", label="Gamma", created_at="2026-07-01T09:00:00")
    brain.edge("a", "b", created_at="2026-07-01T10:00:00")
    brain.edge("a", "c", edge_type="relates", created_at="2026-07-01T10:00:00")
    brain.raw(
        "UPDATE edges_v2 SET valid_to=? WHERE source='a' AND target='b'",
        ("2026-08-08T10:00:00",),
    )
    brain.raw(
        "UPDATE edges_v2 SET valid_to=?, superseded_by='x' WHERE source='a' AND target='c'",
        ("2026-08-08T11:00:00",),
    )

    changes = brain.service().day("2026-08-08")["groups"]["changes"]

    assert [(card["kind"], card["label"], card["node_id"]) for card in changes] == [
        ("connection_ended", "Alpha → Beta", "a"),
        ("connection_superseded", "Alpha → Gamma", "a"),
    ]


# ── workspace boundary ───────────────────────────────────────────────────────


def test_another_workspaces_timeline_is_invisible(brain):
    brain.node("mine", created_at="2026-08-01T10:00:00", workspace_id="w1")
    brain.node("theirs", created_at="2026-08-02T10:00:00", workspace_id="w2")
    brain.source("mine", captured_at="2026-08-01T09:00:00", workspace_id="w1")
    brain.source("theirs", captured_at="2026-08-02T09:00:00", workspace_id="w2")
    brain.message("mine", timestamp="2026-08-01T12:00:00", workspace_id="w1")
    brain.message("theirs", timestamp="2026-08-02T12:00:00", workspace_id="w2")

    scoped = brain.service().overview(user_email="me@example.com", workspace_id="w1")

    assert [row["date"] for row in scoped["series"]] == ["2026-08-01"]
    assert scoped["totals"] == {
        "sources": 1,
        "entities": 1,
        "connections": 0,
        "conversations": 1,
    }
    assert scoped["last_activity_at"] == "2026-08-01T12:00:00"


def test_the_unscoped_read_sees_every_workspace(brain):
    brain.node("mine", created_at="2026-08-01T10:00:00", workspace_id="w1")
    brain.node("theirs", created_at="2026-08-02T10:00:00", workspace_id="w2")

    assert brain.service().overview()["totals"]["entities"] == 2


def test_a_relationship_whose_ends_belong_to_another_workspace_is_not_counted(brain):
    brain.node("a", created_at="2026-08-01T10:00:00", workspace_id="w1")
    brain.node("x", created_at="2026-08-01T10:00:00", workspace_id="w2")
    brain.node("y", created_at="2026-08-01T10:00:00", workspace_id="w2")
    brain.edge("x", "y", created_at="2026-08-01T11:00:00")

    scoped = brain.service().overview(workspace_id="w1")

    assert scoped["totals"]["connections"] == 0
    assert brain.service().overview()["totals"]["connections"] == 1


def test_a_change_in_another_workspace_stays_there(brain):
    brain.node("theirs", label="Theirs", created_at="2026-07-01T09:00:00", workspace_id="w2")
    brain.stamp("theirs", valid_to="2026-08-01T10:00:00")

    assert brain.service().day("2026-08-01", workspace_id="w1")["counts"]["changes"] == 0
    assert brain.service().day("2026-08-01", workspace_id="w2")["counts"]["changes"] == 1


def test_one_persons_conversations_do_not_leak_into_anothers_chronicle(brain):
    brain.message("mine", timestamp="2026-08-01T12:00:00", user_email="me@example.com")
    brain.message(
        "yours",
        conversation_id="c2",
        timestamp="2026-08-02T12:00:00",
        user_email="you@example.com",
    )
    brain.message("shared", conversation_id="c3", timestamp="2026-08-03T12:00:00", user_email=None)

    mine = brain.service().overview(user_email="me@example.com")

    # Pre-auth history (no email on the row) stays visible — dropping it would
    # empty a solo Brain's whole chronicle — but another account's does not.
    assert [row["date"] for row in mine["series"]] == ["2026-08-01", "2026-08-03"]
    assert brain.service().overview()["totals"]["conversations"] == 3


def test_an_empty_user_email_is_nobody_rather_than_a_user_called_empty(brain):
    brain.message("mine", timestamp="2026-08-01T12:00:00", user_email="me@example.com")

    assert brain.service().overview(user_email="   ")["totals"]["conversations"] == 1


# ── timezone ─────────────────────────────────────────────────────────────────


def test_an_offset_stamp_is_bucketed_in_the_configured_timezone(brain, monkeypatch):
    """20:00 UTC is 05:00 the next morning in Seoul — and files there."""
    brain.source("a", captured_at="2026-08-10T20:00:00+00:00")

    seoul = brain.service().overview()
    assert [row["date"] for row in seoul["series"]] == ["2026-08-11"]
    assert seoul["first_activity_at"] == "2026-08-11T05:00:00"
    assert brain.service().day("2026-08-11")["counts"]["sources"] == 1
    assert brain.service().day("2026-08-10")["counts"]["sources"] == 0

    monkeypatch.setenv("LATTICE_TZ", "UTC")
    utc = brain.service().overview()
    assert [row["date"] for row in utc["series"]] == ["2026-08-10"]
    assert utc["first_activity_at"] == "2026-08-10T20:00:00"


def test_a_naive_stamp_is_taken_as_already_local(brain):
    """The store writes naive local seconds; re-interpreting them would shift
    every historical row by the offset."""
    brain.source("a", captured_at="2026-08-10T20:00:00")

    assert [row["date"] for row in brain.service().overview()["series"]] == ["2026-08-10"]


def test_an_unreadable_stamp_is_dropped_instead_of_filed_under_today(brain):
    brain.node("dated", created_at="2026-08-01T10:00:00")
    brain.node("undated", created_at="not-a-date")
    brain.message("no clock", timestamp="")
    brain.message("clocked", conversation_id="c2", timestamp="2026-08-01T12:00:00")

    payload = brain.service().overview()

    assert payload["totals"] == {
        "sources": 0,
        "entities": 1,
        "connections": 0,
        "conversations": 1,
    }
    assert [row["date"] for row in payload["series"]] == ["2026-08-01"]
    assert brain.service().day("2026-08-01")["counts"]["entities"] == 1


# ── validation ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("value", ["", "   ", "nope", "2026/08/01", "20260801", "2026-8-1", None])
def test_a_date_that_is_not_a_day_is_refused(brain, value):
    with pytest.raises(ValueError):
        brain.service().day(value)


def test_a_well_shaped_impossible_date_is_refused_too(brain):
    with pytest.raises(ValueError):
        brain.service().day("2026-13-45")


def test_parse_day_normalizes_what_it_accepts():
    assert parse_day("  2026-08-01  ") == "2026-08-01"


@pytest.mark.parametrize("value", ["", "later", None])
def test_a_timestamp_that_is_not_an_instant_is_refused(brain, value):
    with pytest.raises(ValueError):
        brain.service().as_of(value)


def test_parse_timestamp_moves_an_offset_into_the_configured_zone():
    assert parse_timestamp("2026-08-10T20:00:00+00:00") == "2026-08-11T05:00:00"
    assert parse_timestamp("2026-08-10") == "2026-08-10T00:00:00"


# ── rewind ───────────────────────────────────────────────────────────────────


def _rewind_brain(brain):
    brain.node("a", label="Alpha", created_at="2026-08-01T10:00:00")
    brain.node("b", label="Beta", created_at="2026-08-05T10:00:00")
    brain.edge("a", "b", created_at="2026-08-05T11:00:00")
    return brain.service()


def test_before_the_first_memory_the_brain_was_empty(brain):
    payload = _rewind_brain(brain).as_of("2026-07-01T00:00:00")

    assert payload["ts"] == "2026-07-01T00:00:00"
    assert payload["stats"] == {"entities": 0, "connections": 0}
    assert payload["top_entities"] == []


def test_mid_history_shows_only_what_had_been_learned_by_then(brain):
    payload = _rewind_brain(brain).as_of("2026-08-03T00:00:00")

    assert payload["stats"] == {"entities": 1, "connections": 0}
    assert [item["id"] for item in payload["top_entities"]] == ["a"]
    assert payload["top_entities"][0]["label"] == "Alpha"


def test_now_shows_the_whole_brain(brain):
    payload = _rewind_brain(brain).as_of("2026-09-01T00:00:00")

    assert payload["stats"] == {"entities": 2, "connections": 1}
    assert [item["id"] for item in payload["top_entities"]] == ["a", "b"]


def test_a_fact_that_stopped_being_true_drops_out_of_the_rewind(brain):
    brain.node("old", label="Coffee at 3pm", created_at="2026-07-01T09:00:00")
    brain.node("new", label="Coffee at 4pm", created_at="2026-08-01T09:00:00")
    brain.stamp("old", valid_to="2026-08-01T09:00:00", superseded_by="new")
    service = brain.service()

    assert [item["id"] for item in service.as_of("2026-07-15T00:00:00")["top_entities"]] == ["old"]
    assert [item["id"] for item in service.as_of("2026-08-15T00:00:00")["top_entities"]] == ["new"]


def test_the_most_used_memories_come_first_and_the_list_is_capped(brain):
    for index in range(15):
        brain.node(f"n{index:02d}", created_at="2026-08-01T10:00:00")
    brain.raw("UPDATE nodes_v2 SET importance_score=7.5 WHERE id='n09'")
    brain.raw("UPDATE nodes_v2 SET importance_score=3.0 WHERE id='n11'")

    top = brain.service().as_of("2026-09-01T00:00:00")["top_entities"]

    assert len(top) == 12
    assert [item["id"] for item in top[:2]] == ["n09", "n11"]
    assert top[0]["importance_score"] == 7.5
    assert top[2]["importance_score"] == 0.0


def test_the_rewind_is_scoped_to_the_callers_workspace(brain):
    brain.node("mine", created_at="2026-08-01T10:00:00", workspace_id="w1")
    brain.node("theirs", created_at="2026-08-01T10:00:00", workspace_id="w2")
    service = brain.service()

    scoped = service.as_of("2026-09-01T00:00:00", workspace_id="w1")
    unscoped = service.as_of("2026-09-01T00:00:00")

    assert [item["id"] for item in scoped["top_entities"]] == ["mine"]
    assert unscoped["stats"]["entities"] == 2


# ── storage that cannot be read ──────────────────────────────────────────────


def test_a_brain_with_the_graph_switched_off_still_has_a_chronicle(tmp_path):
    conversations = ConversationStore(tmp_path / "kg.sqlite")
    conversations.append(
        {"role": "user", "content": "hi", "timestamp": "2026-08-01T12:00:00", "conversation_id": "c1"}
    )
    service = ChronicleService(
        knowledge_graph=None, conversations=conversations, enable_graph=False
    )

    overview = service.overview()
    day = service.day("2026-08-01")
    rewind = service.as_of("2026-08-01T12:00:00")

    assert overview["totals"] == {
        "sources": 0,
        "entities": 0,
        "connections": 0,
        "conversations": 1,
    }
    assert day["counts"]["conversations"] == 1
    assert day["counts"]["entities"] == 0
    assert rewind == {
        "ts": "2026-08-01T12:00:00",
        "stats": {"entities": 0, "connections": 0},
        "top_entities": [],
    }


def test_a_database_that_is_not_a_brain_reads_as_an_empty_timeline(tmp_path, caplog):
    """A file with no graph tables: report nothing, log why, never 500."""
    plain = tmp_path / "plain.sqlite"
    sqlite3.connect(str(plain)).close()

    class _Store:
        db_path = plain

    service = ChronicleService(knowledge_graph=_Store(), conversations=None)
    payload = service.overview()

    assert payload["totals"]["entities"] == 0
    assert payload["series"] == []
    assert "chronicle read failed" in caplog.text


def test_a_database_path_that_cannot_even_be_opened_reads_as_empty(tmp_path):
    class _Store:
        db_path = tmp_path  # a directory: sqlite3.connect refuses it outright

    service = ChronicleService(knowledge_graph=_Store(), conversations=None)

    assert service.overview()["totals"]["sources"] == 0
    assert service.day("2026-08-01")["counts"]["changes"] == 0
