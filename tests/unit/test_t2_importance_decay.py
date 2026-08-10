"""Importance, decay, and consolidation proposals (v11.1.0 Track 2).

The score is deliberately boring — use plus recency, no model — so a user can
be told *why* something was offered for tidying. These tests pin the ranking
rules and the "only episodic memories are candidates" boundary.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from lattice_brain.graph.proactive import ProactiveBrain, _access_count
from lattice_brain.synthesis import CONSOLIDATION_KIND, propose_consolidation
from tests.unit.test_t2_support import RecordingReviewQueue, link, make_store, seed


class _SampleStore:
    """A store that returns a fixed sample (and optionally an access counter)."""

    def __init__(self, nodes, edges=None, *, stats=None, stats_error=False):
        self._nodes = nodes
        self._edges = edges or []
        self._stats = stats
        self._stats_error = stats_error
        if stats is not None or stats_error:
            self.access_stats = self._access_stats

    def graph(self, _limit=300, **_kwargs):
        return {"nodes": self._nodes, "edges": self._edges}

    def _access_stats(self, _ids=None):
        if self._stats_error:
            raise RuntimeError("projection unavailable")
        return self._stats


def _node(node_id, node_type, *, days_old=0, **extra):
    stamp = (datetime.now(timezone.utc) - timedelta(days=days_old)).isoformat()
    return {"id": node_id, "type": node_type, "title": node_id, "updated_at": stamp, **extra}


# ── scoring ──────────────────────────────────────────────────────────────────


def test_old_and_unused_memories_score_lowest():
    store = _SampleStore(
        [
            _node("fresh", "Chat", days_old=0),
            _node("ancient", "Chat", days_old=365),
            _node("middle", "Chat", days_old=30),
        ]
    )
    report = ProactiveBrain(store).importance_report()
    assert [item["id"] for item in report["candidates"]] == ["ancient", "middle", "fresh"]
    assert report["candidates"][0]["score"] < report["candidates"][-1]["score"]
    assert report["nodes_scanned"] == 3
    assert report["strongest"][0]["id"] == "fresh"


def test_connected_and_frequently_opened_memories_survive_decay():
    nodes = [_node("hub", "Chat", days_old=200), _node("lonely", "Chat", days_old=200)]
    edges = [
        {"source": "hub", "target": "other"},
        {"source": "third", "target": "hub"},
        # A half-formed edge contributes to nobody's degree rather than to a
        # phantom "" node.
        {"source": "", "target": None},
    ]
    plain = ProactiveBrain(_SampleStore(nodes, edges)).importance_report()
    by_id = {item["id"]: item for item in plain["candidates"]}
    assert by_id["hub"]["degree"] == 2 and by_id["lonely"]["degree"] == 0
    assert by_id["hub"]["score"] > by_id["lonely"]["score"]


def test_only_episodic_memories_are_offered_for_consolidation():
    store = _SampleStore(
        [
            _node("chat", "Chat", days_old=400),
            _node("decision", "Decision", days_old=400),
            _node("doc", "Document", days_old=400),
            _node("chunk", "Chunk", days_old=400),
        ]
    )
    report = ProactiveBrain(store).importance_report()
    assert {item["id"] for item in report["candidates"]} == {"chat", "chunk"}
    # A decayed Decision is still knowledge — reported, never folded away.
    assert {item["id"] for item in report["strongest"]} >= {"decision", "doc"}


def test_a_memory_without_a_timestamp_is_not_treated_as_ancient():
    store = _SampleStore([{"id": "undated", "type": "Chat", "title": "u"}])
    report = ProactiveBrain(store).importance_report()
    assert report["candidates"][0]["age_days"] == 0.0
    assert report["candidates"][0]["score"] == 1.0


def test_the_candidate_list_is_capped():
    store = _SampleStore([_node(f"c{i}", "Chat", days_old=100) for i in range(30)])
    report = ProactiveBrain(store).importance_report(max_candidates=4)
    assert report["candidate_count"] == 4
    assert ProactiveBrain(store).importance_report(max_candidates=0)["candidate_count"] == 1


def test_half_life_has_a_floor():
    store = _SampleStore([_node("c", "Chat", days_old=1)])
    assert ProactiveBrain(store).importance_report(half_life_days=0)["half_life_days"] == 0.5


# ── where the access count comes from ────────────────────────────────────────


def test_ingested_metadata_access_counts_win_over_the_read_counter():
    node = {"id": "a", "type": "Chat", "metadata": {"access_count": 7}}
    assert _access_count(node, {"accesses": 2.0}) == 7.0
    # A real zero from metadata is an answer, not a missing value.
    assert _access_count({"metadata": {"accesses": 0}}, {"accesses": 9.0}) == 0.0
    assert _access_count({"metadata": {"access": True}}, None) == 0.0
    assert _access_count({"metadata": "not a dict"}, {"accesses": 3.0}) == 3.0
    assert _access_count({}, None) == 0.0


def test_the_store_counter_is_used_when_metadata_has_none():
    store = _SampleStore(
        [_node("a", "Chat", days_old=0), _node("b", "Chat", days_old=0)],
        stats={"a": {"accesses": 5.0}},
    )
    report = ProactiveBrain(store).importance_report()
    by_id = {item["id"]: item for item in report["candidates"]}
    assert report["access_source"] == "store"
    assert by_id["a"]["accesses"] == 5.0 and by_id["b"]["accesses"] == 0.0


def test_a_broken_access_counter_degrades_the_report_instead_of_failing():
    store = _SampleStore([_node("a", "Chat")], stats_error=True)
    report = ProactiveBrain(store).importance_report()
    assert report["access_source"] == "metadata"
    assert report["candidate_count"] == 1


def test_a_store_without_the_counter_still_reports(tmp_path):
    store = _SampleStore([_node("a", "Chat")])
    assert not hasattr(store, "access_stats")
    assert ProactiveBrain(store).importance_report()["access_source"] == "metadata"


def test_the_real_store_feeds_the_report_from_its_own_read_counter(tmp_path):
    store = make_store(tmp_path)
    seed(
        store,
        [
            ("c1", "Chat", "morning standup", "we talked about the release"),
            ("c2", "Chat", "afternoon standup", "we talked about the release again"),
        ],
    )
    link(store, "c1", "c2")
    store.get_node("c1")
    report = ProactiveBrain(store).importance_report()
    by_id = {item["id"]: item for item in report["candidates"]}
    assert report["access_source"] == "store"
    assert by_id["c1"]["accesses"] == 1.0 and by_id["c2"]["accesses"] == 0.0
    assert by_id["c1"]["score"] > by_id["c2"]["score"]


# ── consolidation proposals ──────────────────────────────────────────────────


def test_a_pile_of_decayed_fragments_becomes_one_proposal():
    store = _SampleStore([_node(f"c{i}", "Chat", days_old=300) for i in range(6)])
    queue = RecordingReviewQueue()
    result = propose_consolidation(store, queue)

    assert result["proposed_count"] == 1
    item = queue.created[0]
    assert item["kind"] == CONSOLIDATION_KIND
    assert len(item["payload"]["candidates"]) == 6
    assert "원본은 그대로 남습니다" in item["payload"]["summary_ko"]
    assert result["report"]["candidate_count"] == 6


def test_a_handful_of_fragments_is_not_worth_asking_about():
    store = _SampleStore([_node("c0", "Chat", days_old=300)])
    queue = RecordingReviewQueue()
    result = propose_consolidation(store, queue)
    assert result["proposed_count"] == 0
    assert queue.created == []


def test_the_same_batch_is_not_proposed_twice():
    store = _SampleStore([_node(f"c{i}", "Chat", days_old=300) for i in range(6)])
    queue = RecordingReviewQueue()
    propose_consolidation(store, queue)
    second = propose_consolidation(store, queue)
    assert second["proposed_count"] == 0 and second["suppressed"] == 1


def test_consolidation_reuses_a_sample_it_was_handed():
    nodes = [_node(f"c{i}", "Chat", days_old=300) for i in range(4)]

    class _Refuse:
        def graph(self, *_args, **_kwargs):
            raise AssertionError("the sample should have been reused")

    result = propose_consolidation(
        _Refuse(), RecordingReviewQueue(), sample={"nodes": nodes, "edges": []}
    )
    assert result["proposed_count"] == 1
