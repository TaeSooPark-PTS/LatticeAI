"""Contradiction → proposal → approval → temporal stamp (v11.1.0 Track 2).

The property that matters most here is negative: **detection never writes**.
A contradiction becomes a review item; the graph changes only after
``ReviewQueueService.approve`` has returned. These tests assert that directly —
by snapshotting the temporal columns before approval, and by refusing the queue
its ``approve`` and checking the graph stayed untouched.
"""

from __future__ import annotations

import pytest

from lattice_brain.synthesis import (
    CONTRADICTION_KIND,
    SYNTHESIS_REVIEW_SOURCE,
    ContradictionResolutionError,
    propose_contradictions,
    resolve_contradiction,
)
from latticeai.core.workspace_os import WorkspaceOSStore
from latticeai.services.review_queue import REVIEW_SOURCES, ReviewQueueService
from tests.unit.test_t2_support import RecordingReviewQueue, make_store, seed

PAIR = [
    ("n-old", "Concept", "coffee ritual", "I like coffee before the design review"),
    ("n-new", "Concept", "coffee ritual", "I do not like coffee before the design review"),
]


def _graph(tmp_path):
    store = make_store(tmp_path)
    seed(store, PAIR)
    return store


def _temporal(store):
    with store._connect() as conn:
        return {
            row["id"]: (row["valid_from"], row["valid_to"], row["superseded_by"])
            for row in conn.execute(
                "SELECT id, valid_from, valid_to, superseded_by FROM nodes_v2"
            )
        }


def _contradiction_item(queue):
    return next(
        item for item in queue.created if item["kind"] == CONTRADICTION_KIND
    )


# ── proposal ─────────────────────────────────────────────────────────────────


def test_detection_proposes_and_writes_nothing_to_the_graph(tmp_path):
    store = _graph(tmp_path)
    before = _temporal(store)
    queue = RecordingReviewQueue()

    result = propose_contradictions(store, queue)

    assert result["proposed_count"] >= 1
    assert queue.approved == []
    assert _temporal(store) == before  # detection is read-only, by assertion
    item = _contradiction_item(queue)
    assert item["source"] == SYNTHESIS_REVIEW_SOURCE in REVIEW_SOURCES
    assert item["kind"] == CONTRADICTION_KIND


def test_proposal_payload_is_something_a_person_can_decide(tmp_path):
    store = _graph(tmp_path)
    queue = RecordingReviewQueue()
    propose_contradictions(store, queue)
    payload = _contradiction_item(queue)["payload"]

    assert {option["id"] for option in payload["options"]} == {
        "keep_old", "replace", "keep_both_temporal",
    }
    assert payload["older"]["id"] and payload["newer"]["id"]
    assert payload["older"]["id"] != payload["newer"]["id"]
    assert payload["older"]["content"] and payload["newer"]["content"]
    # A plain-language Korean sentence, not a JSON dump, is what the Review
    # Center renders.
    assert "어긋납니다" in payload["summary_ko"]
    assert payload["proposal_key"].startswith(f"{CONTRADICTION_KIND}:")


def test_the_same_pair_is_not_proposed_twice(tmp_path):
    store = _graph(tmp_path)
    queue = RecordingReviewQueue()
    first = propose_contradictions(store, queue)
    second = propose_contradictions(store, queue)

    assert second["proposed_count"] == 0
    assert second["suppressed"] == first["proposed_count"]
    assert len(queue.created) == first["proposed_count"]


def test_a_decided_pair_can_be_raised_again(tmp_path):
    store = _graph(tmp_path)
    queue = RecordingReviewQueue()
    propose_contradictions(store, queue)
    for item in queue.items.values():
        item["effective_status"] = "dismissed"

    again = propose_contradictions(store, queue)
    assert again["proposed_count"] >= 1


def test_an_unreadable_inbox_does_not_stop_the_pass(tmp_path):
    store = _graph(tmp_path)
    queue = RecordingReviewQueue(fail_list=True)
    result = propose_contradictions(store, queue)
    assert result["proposed_count"] >= 1


def test_a_pair_with_a_missing_side_is_skipped(tmp_path):
    store = _graph(tmp_path)
    queue = RecordingReviewQueue()

    class _SelfPair:
        def sample(self, **_kwargs):
            return {"nodes": [], "edges": []}

        def contradictions_in(self, *_args, **_kwargs):
            return {
                "node_pairs": [
                    {"left_id": "", "right_id": "x"},
                    {"left_id": "same", "right_id": "same"},
                ]
            }

    result = propose_contradictions(store, queue, brain=_SelfPair())
    assert result["proposed_count"] == 0
    assert queue.created == []


def test_the_older_memory_is_identified_whichever_side_it_arrives_on(tmp_path):
    """The pair is presented oldest-first even when detection reports it newest-first."""
    store = _graph(tmp_path)
    queue = RecordingReviewQueue()

    class _NewestFirst:
        def sample(self, **_kwargs):
            return {
                "nodes": [
                    {"id": "a", "title": "A", "updated_at": "2026-01-02T00:00:00"},
                    {"id": "b", "title": "B", "updated_at": "2026-01-01T00:00:00"},
                ],
                "edges": [],
            }

        def contradictions_in(self, *_args, **_kwargs):
            return {
                "node_pairs": [
                    {
                        "left_id": "a",
                        "left_content": "A says yes",
                        "right_id": "b",
                        "right_content": "B says no",
                    }
                ]
            }

    propose_contradictions(store, queue, brain=_NewestFirst())
    payload = _contradiction_item(queue)["payload"]
    assert payload["older"]["id"] == "b" and payload["newer"]["id"] == "a"
    # The content follows the memory, not the side it was detected on.
    assert payload["older"]["content"] == "B says no"
    assert payload["newer"]["content"] == "A says yes"


def test_a_review_item_without_a_proposal_key_does_not_suppress_anything(tmp_path):
    store = _graph(tmp_path)
    queue = RecordingReviewQueue()
    queue.create(title="hand-written note", kind="suggestion", payload={})
    assert propose_contradictions(store, queue)["proposed_count"] >= 1


def test_proposals_are_capped_per_pass(tmp_path):
    store = _graph(tmp_path)
    queue = RecordingReviewQueue()

    class _Flood:
        def sample(self, **_kwargs):
            return {"nodes": [], "edges": []}

        def contradictions_in(self, *_args, **_kwargs):
            return {
                "node_pairs": [
                    {"left_id": f"l{i}", "right_id": f"r{i}"} for i in range(9)
                ]
            }

    result = propose_contradictions(store, queue, brain=_Flood(), max_proposals=2)
    assert result["proposed_count"] == 2
    assert result["pairs_detected"] == 9


# ── approval-time stamping ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "resolution,expected",
    [
        ("replace", {"older": ("valid_to", "superseded_by"), "newer": ("valid_from",)}),
        ("keep_old", {"newer": ("valid_to", "superseded_by")}),
        ("keep_both_temporal", {"older": ("valid_to",), "newer": ("valid_from",)}),
    ],
)
def test_approval_applies_the_chosen_temporal_stamps(tmp_path, resolution, expected):
    store = _graph(tmp_path)
    queue = RecordingReviewQueue()
    propose_contradictions(store, queue)
    item = _contradiction_item(queue)
    older = item["payload"]["older"]["id"]
    newer = item["payload"]["newer"]["id"]
    assert _temporal(store)[older] == (None, None, None)

    result = resolve_contradiction(
        store, queue, item["id"], resolution=resolution, at="2026-07-01T09:00:00"
    )

    assert queue.approved == [item["id"]]
    assert result["status"] == "approved"
    assert result["applied_at"] == "2026-07-01T09:00:00"
    stamped = _temporal(store)
    fields = {"valid_from": 0, "valid_to": 1, "superseded_by": 2}
    for side, columns in expected.items():
        node_id = older if side == "older" else newer
        for column in columns:
            assert stamped[node_id][fields[column]] is not None
    if resolution == "replace":
        assert stamped[older][2] == newer
    if resolution == "keep_old":
        assert stamped[newer][2] == older
        assert stamped[older] == (None, None, None)


def test_an_unknown_resolution_is_refused_before_the_item_is_touched(tmp_path):
    store = _graph(tmp_path)
    queue = RecordingReviewQueue()
    propose_contradictions(store, queue)
    item = _contradiction_item(queue)

    with pytest.raises(ContradictionResolutionError, match="resolution must be"):
        resolve_contradiction(store, queue, item["id"], resolution="delete_everything")

    assert queue.approved == []
    assert _temporal(store)[item["payload"]["older"]["id"]] == (None, None, None)


def test_only_a_contradiction_proposal_can_be_resolved(tmp_path):
    store = _graph(tmp_path)
    queue = RecordingReviewQueue()
    other = queue.create(title="something else", kind="suggestion", payload={})

    with pytest.raises(ContradictionResolutionError, match="not a contradiction"):
        resolve_contradiction(store, queue, other["id"], resolution="replace")
    assert queue.approved == []


def test_a_proposal_without_a_pair_is_refused(tmp_path):
    store = _graph(tmp_path)
    queue = RecordingReviewQueue()
    broken = queue.create(title="broken", kind=CONTRADICTION_KIND, payload={"older": {}})

    with pytest.raises(ContradictionResolutionError, match="no memory pair"):
        resolve_contradiction(store, queue, broken["id"], resolution="replace")
    assert queue.approved == []


def test_stamping_a_pair_the_graph_never_had_reports_it_honestly(tmp_path):
    store = _graph(tmp_path)
    queue = RecordingReviewQueue()
    ghost = queue.create(
        title="ghost pair",
        kind=CONTRADICTION_KIND,
        payload={"older": {"id": "gone-a"}, "newer": {"id": "gone-b"}},
    )
    result = resolve_contradiction(store, queue, ghost["id"], resolution="replace")
    assert [stamp["updated"] for stamp in result["stamps"]] == [False, False]


# ── the real ReviewQueueService, end to end ──────────────────────────────────


def test_the_loop_runs_through_the_real_review_queue(tmp_path):
    store = _graph(tmp_path)
    workspace = WorkspaceOSStore(tmp_path / "workspace")
    queue = ReviewQueueService(store=workspace)

    proposed = propose_contradictions(store, queue, user_email="a@b.c")
    assert proposed["proposed_count"] >= 1

    listed = queue.list(source=SYNTHESIS_REVIEW_SOURCE)["items"]
    assert listed and listed[0]["effective_status"] == "pending"
    # A second pass sees the open item and stays quiet.
    assert propose_contradictions(store, queue)["proposed_count"] == 0

    item_id = listed[0]["id"]
    result = resolve_contradiction(store, queue, item_id, resolution="keep_both_temporal")
    assert result["status"] == "approved"
    assert queue.get(item_id)["status"] == "approved"
    older = listed[0]["payload"]["older"]["id"]
    assert _temporal(store)[older][1] is not None


def test_resolving_an_already_approved_item_is_refused_by_the_queue(tmp_path):
    store = _graph(tmp_path)
    workspace = WorkspaceOSStore(tmp_path / "workspace")
    queue = ReviewQueueService(store=workspace)
    propose_contradictions(store, queue)
    item_id = queue.list(source=SYNTHESIS_REVIEW_SOURCE)["items"][0]["id"]
    resolve_contradiction(store, queue, item_id, resolution="replace")

    # The queue owns the transition policy; synthesis does not get a second
    # bite by calling resolve again.
    with pytest.raises(Exception, match="cannot 'approve'"):
        resolve_contradiction(store, queue, item_id, resolution="replace")


class _ReadOnlyStore:
    """Exposes the graph read API and nothing else.

    Any attempt to reach a write primitive — or even ``db_path``, the handle
    the temporal stamper needs — raises, so a detection pass that touched the
    graph could not stay silent.
    """

    def __init__(self, store):
        self._store = store

    def graph(self, *args, **kwargs):
        return self._store.graph(*args, **kwargs)

    def __getattr__(self, name):
        raise AssertionError(f"detection reached the store attribute {name!r}")


def test_detection_cannot_reach_any_write_primitive(tmp_path):
    store = _graph(tmp_path)
    queue = RecordingReviewQueue()
    result = propose_contradictions(_ReadOnlyStore(store), queue)
    assert result["proposed_count"] >= 1
    assert queue.approved == []


def test_stamping_needs_the_store_handle_only_after_approval(tmp_path):
    """The same guard, from the other side: resolving *does* need db_path."""
    store = _graph(tmp_path)
    queue = RecordingReviewQueue()
    propose_contradictions(store, queue)
    item = _contradiction_item(queue)
    with pytest.raises(AssertionError, match="db_path"):
        resolve_contradiction(
            _ReadOnlyStore(store), queue, item["id"], resolution="replace"
        )
    assert queue.approved == [item["id"]]
