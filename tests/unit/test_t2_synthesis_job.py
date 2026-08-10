"""Event-driven synthesis job (v11.1.0 Track 2).

Three properties: the trigger fires on *new knowledge only*, the passes are
deterministic (same graph → same proposals, no model involved), and every
output is a review proposal rather than a write.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from lattice_brain.synthesis import (
    CONCEPT_KIND,
    CONSOLIDATION_KIND,
    CONTRADICTION_KIND,
    DEFAULT_SYNTHESIS_THRESHOLD,
    EDGE_KIND,
    SYNTHESIS_THRESHOLD_ENV,
    BrainSynthesizer,
    SynthesisTrigger,
    _concept_clusters,
    _default_threshold,
    _recent_window,
    _unlinked_pairs,
)
from tests.unit.test_t2_support import RecordingReviewQueue, link, make_store, seed

# A corpus with: one contradicting pair, a token that recurs across three
# memories without being anyone's topic, and two notes that always co-occur.
CORPUS = [
    ("n1", "Concept", "roadmap alpha", "the roadmap covers latency budget work"),
    ("n2", "Concept", "roadmap beta", "the roadmap covers latency budget planning"),
    ("n3", "Concept", "roadmap gamma", "another roadmap latency budget review"),
    ("n4", "Concept", "unrelated", "kimchi recipe with cabbage and pepper"),
]


def _synth(tmp_path, **kwargs):
    store = make_store(tmp_path)
    seed(store, CORPUS)
    queue = RecordingReviewQueue()
    return store, queue, BrainSynthesizer(store, queue, **kwargs)


# ── trigger ──────────────────────────────────────────────────────────────────


def test_threshold_defaults_and_survives_a_broken_environment(monkeypatch):
    monkeypatch.delenv(SYNTHESIS_THRESHOLD_ENV, raising=False)
    assert _default_threshold() == DEFAULT_SYNTHESIS_THRESHOLD
    monkeypatch.setenv(SYNTHESIS_THRESHOLD_ENV, "not-a-number")
    assert _default_threshold() == DEFAULT_SYNTHESIS_THRESHOLD
    monkeypatch.setenv(SYNTHESIS_THRESHOLD_ENV, "0")
    assert _default_threshold() == DEFAULT_SYNTHESIS_THRESHOLD
    monkeypatch.setenv(SYNTHESIS_THRESHOLD_ENV, "4")
    assert _default_threshold() == 4
    assert SynthesisTrigger().threshold == 4


def test_trigger_fires_every_threshold_items_and_resets():
    trigger = SynthesisTrigger(threshold=3)
    assert [trigger.record() for _ in range(3)] == [False, False, True]
    assert trigger.status()["pending"] == 0
    assert trigger.status()["runs"] == 1
    assert trigger.status()["last_fired_at"]
    assert trigger.record(5) is True  # a batch can cross it in one go
    assert trigger.status()["runs"] == 2
    assert trigger.status()["due_in"] == 3


def test_trigger_clamps_a_nonsense_threshold():
    assert SynthesisTrigger(threshold=0).threshold == 1
    assert SynthesisTrigger(threshold=-5).record() is True


def test_only_genuinely_new_knowledge_moves_the_counter():
    trigger = SynthesisTrigger(threshold=2)

    class _Result:
        status = "ok"
        duplicate = False

    assert trigger.observe_ingest({"status": "failed"}) is False
    assert trigger.observe_ingest({"status": "ok", "duplicate": True}) is False
    assert trigger.observe_ingest({"status": "ok"}) is False  # first of two
    assert trigger.observe_ingest(_Result()) is True  # dataclass-shaped too
    assert trigger.status()["runs"] == 1


def test_run_if_due_only_runs_when_the_counter_crosses(tmp_path):
    store, queue, synth = _synth(tmp_path, trigger=SynthesisTrigger(threshold=2))
    assert synth.run_if_due({"status": "ok"}) is None
    assert queue.created == []
    result = synth.run_if_due({"status": "ok"})
    assert result is not None and result["proposed_total"] >= 1


# ── the passes ───────────────────────────────────────────────────────────────


def test_a_run_proposes_and_never_writes(tmp_path):
    store, queue, synth = _synth(tmp_path)
    result = synth.run()

    kinds = {item["kind"] for item in queue.created}
    assert kinds <= {CONTRADICTION_KIND, CONCEPT_KIND, EDGE_KIND, CONSOLIDATION_KIND}
    assert result["proposed_total"] == len(queue.created)
    assert queue.approved == []
    with store._connect() as conn:
        stamped = conn.execute(
            "SELECT COUNT(*) FROM nodes_v2 WHERE valid_to IS NOT NULL "
            "OR superseded_by IS NOT NULL"
        ).fetchone()[0]
    assert stamped == 0


def test_a_run_is_deterministic(tmp_path):
    store, queue, synth = _synth(tmp_path)
    first = synth.run()
    fresh_queue = RecordingReviewQueue()
    second = BrainSynthesizer(store, fresh_queue).run()
    assert first["counts"] == second["counts"]
    assert [item["title"] for item in queue.created] == [
        item["title"] for item in fresh_queue.created
    ]


def test_a_second_run_suppresses_what_is_still_waiting(tmp_path):
    _store, queue, synth = _synth(tmp_path)
    first = synth.run()
    second = synth.run()
    assert second["proposed_total"] == 0
    assert second["suppressed"] == first["proposed_total"]


def test_concept_clusters_name_a_recurring_topic(tmp_path):
    _store, queue, synth = _synth(tmp_path)
    result = synth.run()
    tokens = {cluster["token"] for cluster in result["concepts"]["clusters"]}
    assert "roadmap" in tokens
    concept = next(i for i in queue.created if i["kind"] == CONCEPT_KIND)
    assert concept["payload"]["size"] >= 3
    assert "반복해서" in concept["payload"]["summary_ko"]


def test_a_token_that_is_already_a_topic_is_not_proposed_again():
    filler = [
        {"id": f"f{i}", "title": f"filler {i}", "summary": "unrelated words here"}
        for i in range(8)
    ]
    nodes = filler + [
        {"id": "a", "title": "one", "summary": "roadmap discussion"},
        {"id": "b", "title": "two", "summary": "roadmap discussion"},
        {"id": "c", "title": "three", "summary": "roadmap discussion"},
    ]
    assert "roadmap" in {c["token"] for c in _concept_clusters(nodes)}
    named = [*nodes, {"id": "t", "title": "roadmap", "summary": ""}]
    assert "roadmap" not in {c["token"] for c in _concept_clusters(named)}


def test_boilerplate_tokens_and_thin_nodes_are_not_topics():
    nodes = [{"id": str(i), "title": "shared token here", "summary": ""} for i in range(10)]
    nodes.append({"id": "thin", "title": "x", "summary": ""})
    # "shared" appears in every node — that is boilerplate, not a topic.
    assert _concept_clusters(nodes) == []


def _link_pairs(store):
    result = BrainSynthesizer(store, RecordingReviewQueue()).run()
    return {(pair["left"]["id"], pair["right"]["id"]) for pair in result["links"]["pairs"]}


def test_link_proposals_skip_pairs_that_are_already_connected(tmp_path):
    store = make_store(tmp_path)
    seed(store, CORPUS)
    assert ("n1", "n2") in _link_pairs(store)
    link(store, "n1", "n2")
    assert ("n1", "n2") not in _link_pairs(store)


def test_unlinked_pairs_ignore_weak_overlap():
    nodes = [
        {"id": "a", "title": "alpha", "summary": "one two three four five six"},
        {"id": "b", "title": "beta", "summary": "one two three seven eight nine ten eleven twelve"},
        {"id": "c", "title": "gamma", "summary": "totally different words entirely"},
    ]
    pairs = _unlinked_pairs(nodes, [])
    # a/b share three tokens but the jaccard is under the bar; c shares none.
    assert pairs == []


def test_link_proposal_explains_itself_in_plain_language(tmp_path):
    _store, queue, synth = _synth(tmp_path)
    synth.run()
    edge = next(i for i in queue.created if i["kind"] == EDGE_KIND)
    assert edge["payload"]["edge_type"] == "RELATED_TO"
    assert edge["payload"]["shared_tokens"]
    assert "자주 같이 등장하는데" in edge["payload"]["summary_ko"]


# ── (a) the brief text ───────────────────────────────────────────────────────


def test_brief_section_is_deterministic_without_a_model(tmp_path):
    _store, _queue, synth = _synth(tmp_path)
    brief = synth.brief_section()
    assert brief["headline"] == brief["deterministic_headline"]
    assert brief["recent_nodes"] == 4 and brief["window_days"] == 7
    assert len(brief["lines"]) == 4


def test_a_model_may_reword_the_brief_but_never_the_numbers(tmp_path):
    _store, _queue, synth = _synth(tmp_path, summarizer=lambda text: "더 예쁜 문장")
    brief = synth.brief_section(counts={"contradictions": 2})
    assert brief["headline"] == "더 예쁜 문장"
    assert "2" in brief["lines"][0]
    assert brief["deterministic_headline"] != brief["headline"]


def test_a_broken_or_empty_summarizer_falls_back_to_the_written_sentence(tmp_path):
    _store, _queue, blank = _synth(tmp_path, summarizer=lambda text: "  ")
    assert blank.brief_section()["headline"].startswith("최근 7일")

    def _explode(_text):
        raise RuntimeError("model unavailable")

    _store2, _queue2, broken = _synth(tmp_path, summarizer=_explode)
    assert broken.brief_section()["headline"].startswith("최근 7일")


def test_recent_window_ignores_unparseable_and_handles_aware_stamps():
    now = datetime.now(timezone.utc)
    nodes = [
        {"updated_at": "not a timestamp"},
        {"updated_at": now.isoformat()},
        {"updated_at": (now - timedelta(days=30)).isoformat()},
        {"updated_at": datetime.now().isoformat(timespec="seconds")},
    ]
    assert _recent_window(nodes)["recent"] == 2


def test_the_run_carries_the_brief_and_the_trigger_state(tmp_path):
    _store, _queue, synth = _synth(tmp_path, trigger=SynthesisTrigger(threshold=7))
    result = synth.run()
    assert result["brief"]["counts"] == result["counts"]
    assert result["trigger"]["threshold"] == 7
    assert result["nodes_scanned"] == 4
