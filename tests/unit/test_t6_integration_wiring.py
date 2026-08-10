"""The seams the v11.1.0 tracks left for each other, driven end to end.

Track 2 built the synthesis trigger and named the ingestion pipeline's audit
wrapper as where a landed ingest would reach it; Track 3 shipped the ingest
path. The interesting question here is not whether ``note_ingest`` works —
``test_t2_brain_proactive_service.py`` proves that in isolation — but whether
an ordinary ingest actually arrives at it, and whether the Brain noticing
things can ever cost a person the memory they were saving.

So these run the real composition root (``build_persistence_runtime``) over a
real ``KnowledgeGraphStore`` and the real ``WorkspaceOSStore`` the Review
Center reads, with no service stubbed in between.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lattice_brain.graph.store import KnowledgeGraphStore  # noqa: E402
from lattice_brain.ingestion import IngestionItem  # noqa: E402
from lattice_brain.synthesis import (  # noqa: E402
    SYNTHESIS_REVIEW_SOURCE,
    SYNTHESIS_THRESHOLD_ENV,
)
from latticeai.runtime.persistence_runtime import (  # noqa: E402
    build_persistence_runtime,
)
from latticeai.services.review_queue import ReviewQueueService  # noqa: E402

#: Three notes that share a vocabulary — enough for the deterministic passes
#: (recurring topic, always-together-never-linked pair) to have something to
#: say once the counter crosses.
NOTES = [
    ("roadmap alpha", "the roadmap covers latency budget work"),
    ("roadmap beta", "the roadmap covers latency budget planning"),
    ("roadmap gamma", "another roadmap latency budget review"),
]


class _Runtime:
    """The composition root plus the two things a test wants to look at."""

    def __init__(self, tmp_path: Path):
        self.audited: list = []
        self.store = KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")
        self.runtime = build_persistence_runtime(
            data_dir=tmp_path,
            base_dir=tmp_path,
            enable_graph=True,
            knowledge_graph=self.store,
            hooks_registry=None,
            history_file=tmp_path / "chat_history.json",
            conversations=None,
            user_id_for_email=lambda email: email,
            audit=lambda action, _detail, _user: self.audited.append(action),
        )
        self.pipeline = self.runtime["INGESTION_PIPELINE"]
        self.queue = ReviewQueueService(store=self.runtime["WORKSPACE_OS"])

    def ingest(self, title: str, text: str):
        return self.pipeline.ingest(
            IngestionItem(source_type="note", title=title, text=text),
            user_email="me@local",
        )

    def proposals(self) -> list:
        return self.queue.list(source=SYNTHESIS_REVIEW_SOURCE)["items"]


@pytest.fixture()
def threshold_of_three(monkeypatch):
    """A three-node threshold, set before the runtime reads it."""
    monkeypatch.setenv(SYNTHESIS_THRESHOLD_ENV, "3")


# ── ingest drives synthesis ──────────────────────────────────────────────────


def test_enough_new_knowledge_through_the_pipeline_schedules_a_synthesis_run(
    threshold_of_three, tmp_path
):
    app = _Runtime(tmp_path)

    for title, text in NOTES[:2]:
        assert app.ingest(title, text).status == "ok"
    assert app.proposals() == [], "a run fired before the threshold was reached"

    assert app.ingest(*NOTES[2]).status == "ok"

    proposals = app.proposals()
    assert proposals, "the third new node did not schedule a synthesis run"
    assert {item["source"] for item in proposals} == {SYNTHESIS_REVIEW_SOURCE}
    # Proposals only: synthesis reached the Review Center, not the graph.
    assert {item["effective_status"] for item in proposals} == {"pending"}


def test_a_re_ingest_of_the_same_note_never_pushes_the_counter(
    threshold_of_three, tmp_path
):
    app = _Runtime(tmp_path)
    title, text = NOTES[0]

    for _ in range(4):
        result = app.ingest(title, text)
    assert result.duplicate is True
    assert app.proposals() == [], "duplicates counted as new knowledge"

    # Two genuinely new notes on top of the one original still land on three.
    for title, text in NOTES[1:]:
        app.ingest(title, text)
    assert app.proposals()


def test_synthesis_is_not_scheduled_by_events_that_are_not_ingests(
    threshold_of_three, tmp_path
):
    app = _Runtime(tmp_path)

    audit = app.pipeline._audit
    for _ in range(9):
        audit("workspace_event", {"duplicate": False}, "me@local")

    assert app.proposals() == []
    assert app.runtime["FUNNEL_METRICS"].snapshot()["counters"]["ingest_completions"] == 0


# ── and never at the ingest's expense ────────────────────────────────────────


def test_a_trigger_that_raises_does_not_break_the_ingest(tmp_path):
    """The sink guards itself; the wrapper guards the ingest anyway.

    ``note_ingest`` already swallows its own failures, so the only way to prove
    the wrapper isolates it is to make the call itself raise — which is exactly
    what a future refactor of that method could do by accident.
    """
    app = _Runtime(tmp_path)

    def _explode(*_args, **_kwargs):
        raise RuntimeError("synthesis exploded")

    app.runtime["BRAIN_INTELLIGENCE"].note_ingest = _explode

    result = app.ingest(*NOTES[0])

    assert result.status == "ok"
    assert app.store.get_node(result.node_id)["title"] == NOTES[0][0]
    # The real audit still saw the event, and the funnel still counted it.
    assert app.audited == ["kg_ingest"]
    assert app.runtime["FUNNEL_METRICS"].snapshot()["counters"]["ingest_completions"] == 1
