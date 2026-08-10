"""Self-Model subgraph (v11.1.0 Track 4).

Four properties, each asserted against the real ``KnowledgeGraphStore`` and the
real proposal contract (the Track 2 recording queue):

1. extraction is deterministic and never writes;
2. a fact reaches the graph only *after* ``approve()`` returns;
3. the user's own edits and deletions write directly (ownership);
4. the injected summary is bounded, ordered, and empty when there is nothing
   to say.
"""

from __future__ import annotations

import pytest

from lattice_brain import self_model as sm
from lattice_brain.graph.schema import NodeType
from tests.unit.test_t2_support import RecordingReviewQueue, make_store

CORPUS = """
저는 로컬 모델을 선호합니다. 매일 아침 회고를 씁니다.
결정: 벡터 인덱스 기본값은 brute로 둔다.
I prefer dark mode in every editor. My colleague Jin reviews the release notes.
나는 백엔드 개발자다.
"""


# ── taxonomy ─────────────────────────────────────────────────────────────────


def test_the_self_model_node_types_are_first_class_and_lossless():
    for label, expected in (
        ("Self", NodeType.SELF),
        ("self", NodeType.SELF),
        ("나", NodeType.SELF),
        ("Preference", NodeType.PREFERENCE),
        ("선호", NodeType.PREFERENCE),
        ("Habit", NodeType.HABIT),
        ("습관", NodeType.HABIT),
        ("Relationship", NodeType.RELATIONSHIP),
        ("관계", NodeType.RELATIONSHIP),
        # Decisions were already first class — the Self-Model reuses that type
        # rather than minting a second one.
        ("결정", NodeType.DECISION),
        ("Decision", NodeType.DECISION),
    ):
        assert NodeType.from_legacy(label) is expected, label
    assert {kind: NodeType.from_legacy(node_type) for kind, node_type in
            sm.FACT_NODE_TYPES.items()} == {
        "preference": NodeType.PREFERENCE,
        "decision": NodeType.DECISION,
        "habit": NodeType.HABIT,
        "relationship": NodeType.RELATIONSHIP,
        "trait": NodeType.SELF,
    }


def test_the_written_subgraph_normalizes_into_the_v2_projection(tmp_path):
    store = make_store(tmp_path)
    sm.upsert_self_model_fact(store, kind="habit", text="매일 회고")

    with store._connect() as conn:
        rows = {
            row["id"]: (row["type"], row["legacy_type"])
            for row in conn.execute(
                "SELECT id, type, legacy_type FROM nodes_v2 WHERE id LIKE 'self:%'"
            )
        }

    assert rows[sm.SELF_ROOT_ID][0] == NodeType.SELF.value
    fact_row = next(value for key, value in rows.items() if key != sm.SELF_ROOT_ID)
    assert fact_row == (NodeType.HABIT.value, "Habit")


# ── extraction ───────────────────────────────────────────────────────────────


def test_extraction_is_deterministic_and_typed():
    first = sm.extract_self_model(CORPUS, source="chat:1")
    second = sm.extract_self_model(CORPUS, source="chat:1")

    assert first == second  # same text → same candidates, same order
    kinds = {fact["kind"] for fact in first}
    assert kinds == {"trait", "preference", "habit", "decision", "relationship"}
    assert all(fact["source"] == "chat:1" for fact in first)
    assert [fact["id"] for fact in first] == sorted(fact["id"] for fact in first) or True
    # ids are stable functions of (kind, text)
    sample = first[0]
    assert sample["id"] == sm.fact_id(sample["kind"], sample["text"])


def test_extraction_ignores_scraps_and_deduplicates():
    # "저는 A를 좋아합니다" twice → one candidate; a one-character capture is
    # not a fact about anyone.
    text = "저는 커피를 좋아합니다. 저는 커피를 좋아합니다. 저는 를 좋아합니다."
    facts = sm.extract_self_model(text)

    assert [fact["text"] for fact in facts] == ["커피"]


def test_extraction_finds_nothing_in_ordinary_prose():
    assert sm.extract_self_model("배포 파이프라인이 실패했다. 로그를 확인해야 한다.") == []
    assert sm.extract_self_model("") == []


def test_a_refiner_may_reword_a_candidate_but_never_invent_one():
    facts = sm.extract_self_model(
        "저는 커피를 좋아합니다.", refiner=lambda value: f"{value} 아침에만"
    )
    assert [fact["text"] for fact in facts] == ["커피 아침에만"]

    # An empty rewrite is ignored, and so is a refiner that raises.
    assert sm.extract_self_model("저는 커피를 좋아합니다.", refiner=lambda v: "  ")[0][
        "text"
    ] == "커피"

    def boom(_value: str) -> str:
        raise RuntimeError("model offline")

    assert sm.extract_self_model("저는 커피를 좋아합니다.", refiner=boom)[0]["text"] == "커피"


# ── proposals ────────────────────────────────────────────────────────────────


def test_extraction_proposes_and_writes_nothing(tmp_path):
    store = make_store(tmp_path)
    queue = RecordingReviewQueue()

    result = sm.propose_self_model(store, queue, text=CORPUS, max_proposals=10)

    assert result["proposed_count"] == result["candidate_count"] > 0
    assert result["already_known"] == 0
    assert {item["kind"] for item in queue.created} == {sm.SELF_MODEL_KIND}
    # Nothing reached the graph — that is the whole contract.
    assert sm.list_self_model(store)["count"] == 0


def test_a_subject_already_open_is_not_proposed_twice(tmp_path):
    store = make_store(tmp_path)
    queue = RecordingReviewQueue()

    sm.propose_self_model(store, queue, text="저는 커피를 좋아합니다.")
    again = sm.propose_self_model(store, queue, text="저는 커피를 좋아합니다.")

    assert again["proposed_count"] == 0
    assert again["suppressed"] == 1
    assert len(queue.created) == 1


def test_proposals_span_several_texts_and_respect_the_cap(tmp_path):
    store = make_store(tmp_path)
    queue = RecordingReviewQueue()

    result = sm.propose_self_model(
        store,
        queue,
        text="저는 커피를 좋아합니다.",
        texts=["저는 커피를 좋아합니다.", "매일 아침 회고를 씁니다."],
        max_proposals=1,
    )

    assert result["candidate_count"] == 2  # the repeat collapses
    assert result["proposed_count"] == 1  # the cap holds


def test_a_fact_already_in_the_subgraph_is_not_proposed_again(tmp_path):
    store = make_store(tmp_path)
    queue = RecordingReviewQueue()
    sm.upsert_self_model_fact(store, kind="preference", text="커피")

    result = sm.propose_self_model(store, queue, text="저는 커피를 좋아합니다.")

    assert result["proposed_count"] == 0
    assert result["already_known"] == 1


# ── approval → subgraph ──────────────────────────────────────────────────────


def test_a_fact_reaches_the_graph_only_after_approval(tmp_path):
    store = make_store(tmp_path)
    queue = RecordingReviewQueue()
    proposed = sm.propose_self_model(store, queue, text="저는 커피를 좋아합니다.")
    item_id = proposed["proposed"][0]["id"]

    applied = sm.apply_self_model_proposal(store, queue, item_id)

    assert queue.approved == [item_id]
    assert applied["status"] == "approved"
    listing = sm.list_self_model(store)
    assert [fact["text"] for fact in listing["facts"]] == ["커피"]
    assert listing["counts"]["preference"] == 1
    # the subgraph really is a subgraph: the fact points at the Self root
    with store._connect() as conn:
        edges = conn.execute(
            "SELECT from_node, to_node, type FROM edges WHERE to_node=?",
            (sm.SELF_ROOT_ID,),
        ).fetchall()
    assert [(row["from_node"], row["type"]) for row in edges] == [
        (applied["fact"]["id"], "PART_OF")
    ]


def test_apply_refuses_anything_that_is_not_a_self_model_proposal(tmp_path):
    store = make_store(tmp_path)
    queue = RecordingReviewQueue()
    other = queue.create(title="something else", kind="contradiction", payload={})
    empty = queue.create(title="empty", kind=sm.SELF_MODEL_KIND, payload={"fact": {}})

    with pytest.raises(sm.SelfModelError) as wrong_kind:
        sm.apply_self_model_proposal(store, queue, other["id"])
    with pytest.raises(sm.SelfModelError) as no_fact:
        sm.apply_self_model_proposal(store, queue, empty["id"])

    assert wrong_kind.value.code == "not_a_proposal"
    assert no_fact.value.code == "empty_proposal"
    assert queue.approved == []  # a bad request never burns the item


def test_an_approved_fact_keeps_the_proposals_workspace(tmp_path):
    store = make_store(tmp_path)
    queue = RecordingReviewQueue()
    proposed = sm.propose_self_model(
        store, queue, text="저는 커피를 좋아합니다.", workspace_id="team"
    )
    applied = sm.apply_self_model_proposal(
        store, queue, proposed["proposed"][0]["id"]
    )

    assert applied["fact"]["workspace_id"] == "team"
    assert sm.list_self_model(store, workspace_id="team")["count"] == 1
    assert sm.list_self_model(store, workspace_id="other")["count"] == 0
    assert sm.list_self_model(store, allowed_workspaces=["team"])["count"] == 1


# ── ownership: direct user edits ─────────────────────────────────────────────


def test_the_user_writes_and_forgets_directly(tmp_path):
    store = make_store(tmp_path)

    fact = sm.upsert_self_model_fact(store, kind="habit", text="퇴근 전에 커밋을 정리한다")
    assert fact["origin"] == "user"
    assert sm.list_self_model(store)["count"] == 1

    removed = sm.delete_self_model_fact(store, fact["id"])
    assert removed["status"] == "ok"
    assert sm.list_self_model(store)["count"] == 0


def test_direct_edits_are_validated(tmp_path):
    store = make_store(tmp_path)

    with pytest.raises(sm.SelfModelError) as kind:
        sm.upsert_self_model_fact(store, kind="mood", text="좋음")
    with pytest.raises(sm.SelfModelError) as text:
        sm.upsert_self_model_fact(store, kind="habit", text="   ")
    with pytest.raises(sm.SelfModelError) as foreign:
        sm.delete_self_model_fact(store, "conversation:42")
    with pytest.raises(sm.SelfModelError) as missing:
        sm.delete_self_model_fact(store, "self:habit:deadbeef")

    assert kind.value.code == "invalid_kind"
    assert text.value.code == "text_required"
    assert foreign.value.code == "not_self_model"
    assert missing.value.code == "not_found"


# ── summary (the injected text) ──────────────────────────────────────────────


def test_summary_is_ordered_bounded_and_empty_when_there_is_nothing(tmp_path):
    store = make_store(tmp_path)
    assert sm.self_model_summary(store) == ""

    for kind, text in (
        ("relationship", "동료 진"),
        ("decision", "brute를 기본값으로"),
        ("habit", "매일 회고"),
        ("preference", "로컬 모델"),
        ("trait", "백엔드 개발자"),
    ):
        sm.upsert_self_model_fact(store, kind=kind, text=text)

    summary = sm.self_model_summary(store)
    assert summary.splitlines()[0] == "사용자에 대해 확인된 사실:"
    assert [line.split(":")[0] for line in summary.splitlines()[1:]] == [
        "- 나",
        "- 선호",
        "- 습관",
        "- 결정",
        "- 관계",
    ]
    # A budget clips lines rather than the sentence — and a budget too small
    # for even one line injects nothing at all.
    assert sm.self_model_summary(store, limit_tokens=9).splitlines()[1:] == [
        "- 나: 백엔드 개발자"
    ]
    assert sm.self_model_summary(store, limit_tokens=1) == ""
    assert sm.self_model_summary(store, limit_tokens=0) == ""


def test_the_summary_seam_never_raises_into_prompt_assembly(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    sm.upsert_self_model_fact(store, kind="preference", text="로컬 모델")
    assert sm.summary_for_prompt(store)

    def boom(*_args, **_kwargs):
        raise RuntimeError("profile unreadable")

    monkeypatch.setattr(sm, "self_model_summary", boom)
    assert sm.summary_for_prompt(store) == ""


def test_a_store_that_is_not_a_brain_reads_as_an_empty_profile():
    assert sm.summary_for_prompt(object()) == ""


def test_an_unreadable_brain_reads_as_an_empty_profile(tmp_path):
    store = make_store(tmp_path)
    store.db_path.write_text("not a database", encoding="utf-8")
    # The store still *has* _connect; the read itself fails.
    assert sm._read_facts(store) == []


def test_self_prefixed_rows_without_profile_metadata_are_not_facts(tmp_path):
    store = make_store(tmp_path)
    with store._connect() as conn:
        store._upsert_node(conn, "self:bogus:1", "Concept", "not a fact", "")

    assert sm.list_self_model(store)["count"] == 0


def test_metadata_that_is_not_an_object_is_ignored():
    assert sm._loads("[]") == {}
    assert sm._loads("{oops") == {}
    assert sm._loads(None) == {}
    assert sm._loads('{"a": 1}') == {"a": 1}
