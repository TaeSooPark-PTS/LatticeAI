"""Agent-native workspace reorganization (Track 4).

"이 프로젝트를 정리해줘" has to be answerable without handing an agent the
filesystem. What is asserted here is the whole safety argument:

* the plan only moves files the Brain can justify, and never proposes a delete;
* the proposal is staged in the Review Center and nothing moves before approval;
* approving applies exactly the reviewed moves, and a stale move is skipped
  rather than forced.
"""

from __future__ import annotations

from pathlib import Path

from latticeai.core import workspace_reorganization as reorg
from latticeai.core.workspace_os import WorkspaceOSStore
from latticeai.services.change_proposals import ChangeProposalService
from latticeai.services.review_queue import ReviewQueueService
from tests.unit.test_t2_support import make_store


def _resolver(base: Path):
    def resolve(relative: str = "") -> Path:
        return base / relative if relative else base

    return resolve


def _workspace(tmp_path: Path, files=("notes/roadmap.md", "loose.txt")) -> Path:
    base = tmp_path / "workspace"
    for name in files:
        target = base / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"content of {name}", encoding="utf-8")
    (base / ".hidden").mkdir(parents=True, exist_ok=True)
    (base / ".hidden" / "secret.txt").write_text("nope", encoding="utf-8")
    return base


def _brain(tmp_path: Path, *, filename: str = "roadmap.md", topic: str = "출시 계획"):
    """A Brain that knows one file belongs to one topic."""
    store = make_store(tmp_path)
    with store._connect() as conn:
        store._upsert_node(
            conn, "file:roadmap", "File", filename, "",
            metadata={"filename": filename, "relative_path": f"notes/{filename}"},
        )
        store._upsert_node(conn, "topic:launch", "Concept", topic, "")
        store._upsert_edge(conn, "file:roadmap", "topic:launch", "MENTIONS", 2.0)
    return store


class _Boom:
    """A graph whose reads fail."""

    def graph(self, *_args, **_kwargs):
        raise RuntimeError("graph offline")

    def neighbors(self, *_args, **_kwargs):
        raise RuntimeError("graph offline")


# ── planning ─────────────────────────────────────────────────────────────────


def test_the_plan_moves_only_what_the_brain_can_justify(tmp_path):
    base = _workspace(tmp_path)
    plan = reorg.plan_reorganization(
        root="", resolve_path=_resolver(base), graph=_brain(tmp_path)
    )

    assert plan["basis"] == "graph"
    assert plan["moves"] == [{
        "source": "notes/roadmap.md",
        "target": "topics/출시-계획/roadmap.md",
        "topic": "출시 계획",
        "reason": "'출시 계획' 주제와 이어져 있습니다",
    }]
    # The unknown file stays where it is, and says why.
    assert plan["unplaced"] == [{"path": "loose.txt", "reason": "brain_has_no_topic"}]
    # Structural guarantee: this path has no delete.
    assert plan["deletes"] == []
    # Hidden folders are never touched.
    assert all(".hidden" not in move["source"] for move in plan["moves"])


def test_a_brain_that_cannot_answer_proposes_nothing_and_says_so(tmp_path):
    base = _workspace(tmp_path)
    resolve = _resolver(base)

    without = reorg.plan_reorganization(root="", resolve_path=resolve, graph=None)
    broken = reorg.plan_reorganization(root="", resolve_path=resolve, graph=_Boom())
    missing = reorg.plan_reorganization(
        root="nope", resolve_path=resolve, graph=_brain(tmp_path)
    )

    assert (without["basis"], without["move_count"]) == ("no_graph", 0)
    assert (broken["basis"], broken["move_count"]) == ("graph_unavailable", 0)
    assert missing["available"] is False and missing["basis"] == "folder_missing"


def test_an_unreadable_node_is_simply_unplaced(tmp_path):
    base = _workspace(tmp_path)

    class _HalfBroken(_Boom):
        def graph(self, *_args, **_kwargs):
            return {"nodes": [{"id": "file:roadmap", "type": "File",
                               "metadata": {"filename": "roadmap.md"}}], "edges": []}

    plan = reorg.plan_reorganization(
        root="", resolve_path=_resolver(base), graph=_HalfBroken()
    )

    assert plan["basis"] == "graph"
    assert plan["move_count"] == 0
    assert {entry["reason"] for entry in plan["unplaced"]} == {"brain_has_no_topic"}


def test_a_file_already_in_its_topic_folder_is_left_alone(tmp_path):
    base = _workspace(tmp_path, files=("topics/출시-계획/roadmap.md",))
    store = _brain(tmp_path)
    with store._connect() as conn:
        store._upsert_node(
            conn, "file:roadmap", "File", "roadmap.md", "",
            metadata={"relative_path": "topics/출시-계획/roadmap.md"},
        )

    plan = reorg.plan_reorganization(root="", resolve_path=_resolver(base), graph=store)

    assert plan["move_count"] == 0
    assert plan["unplaced"] == [
        {"path": "topics/출시-계획/roadmap.md", "reason": "already_in_place"}
    ]


def test_two_files_never_claim_the_same_target(tmp_path):
    base = _workspace(tmp_path, files=("a/roadmap.md", "b/roadmap.md"))
    store = make_store(tmp_path)
    with store._connect() as conn:
        store._upsert_node(
            conn, "file:a", "File", "roadmap.md", "",
            metadata={"relative_path": "a/roadmap.md"},
        )
        store._upsert_node(
            conn, "file:b", "File", "b-roadmap", "",
            metadata={"relative_path": "b/roadmap.md"},
        )
        store._upsert_node(conn, "topic:launch", "Concept", "출시 계획", "")
        store._upsert_edge(conn, "file:a", "topic:launch", "MENTIONS", 2.0)
        store._upsert_edge(conn, "file:b", "topic:launch", "MENTIONS", 2.0)

    plan = reorg.plan_reorganization(root="", resolve_path=_resolver(base), graph=store)

    assert plan["move_count"] == 1
    assert plan["unplaced"] == [{"path": "b/roadmap.md", "reason": "target_taken"}]


def test_the_strongest_topic_wins_and_ties_are_stable(tmp_path):
    base = _workspace(tmp_path, files=("notes/roadmap.md",))
    store = _brain(tmp_path)
    with store._connect() as conn:
        store._upsert_node(conn, "topic:weak", "Concept", "가벼운 주제", "")
        store._upsert_edge(conn, "file:roadmap", "topic:weak", "RELATED_TO", 1.0)
        store._upsert_node(conn, "person:jin", "Person", "진", "")
        store._upsert_edge(conn, "file:roadmap", "person:jin", "AUTHORED_BY", 9.0)
        store._upsert_node(conn, "topic:blank", "Concept", "", "")
        store._upsert_edge(conn, "file:roadmap", "topic:blank", "RELATED_TO", 5.0)

    plan = reorg.plan_reorganization(root="", resolve_path=_resolver(base), graph=store)

    # A person is not a topic and an untitled node is not a folder name.
    assert plan["moves"][0]["topic"] == "출시 계획"


def test_the_plan_is_capped_and_says_it_was(tmp_path):
    base = _workspace(tmp_path, files=("a.md", "b.md"))
    store = make_store(tmp_path)
    with store._connect() as conn:
        store._upsert_node(conn, "topic:launch", "Concept", "출시 계획", "")
        for name in ("a.md", "b.md"):
            store._upsert_node(
                conn, f"file:{name}", "File", name, "", metadata={"filename": name}
            )
            store._upsert_edge(conn, f"file:{name}", "topic:launch", "MENTIONS", 2.0)

    plan = reorg.plan_reorganization(
        root="", resolve_path=_resolver(base), graph=store, max_moves=1
    )

    assert plan["move_count"] == 1
    assert plan["truncated"] is True


def test_the_scan_is_bounded(tmp_path):
    base = _workspace(tmp_path, files=("a.md", "b.md", "c.md"))

    plan = reorg.plan_reorganization(
        root="", resolve_path=_resolver(base), graph=None, scan_limit=2
    )

    assert plan["unplaced_count"] == 2


# ── proposal → approval → move ───────────────────────────────────────────────


def _service(tmp_path, base: Path):
    store = WorkspaceOSStore(tmp_path / "data")
    queue = ReviewQueueService(store=store)
    audits = []
    service = ChangeProposalService(
        review_queue=queue,
        resolve_path=_resolver(base),
        audit=lambda event, **payload: audits.append((event, payload)),
    )
    return service, queue, audits


def test_a_reorganization_is_staged_and_applied_only_on_approval(tmp_path):
    base = _workspace(tmp_path)
    service, queue, audits = _service(tmp_path, base)

    staged = service.propose_reorganization(root="", graph=_brain(tmp_path))

    item = staged["proposed"]
    assert item["source"] == "change_proposal"
    assert item["kind"] == reorg.REORG_KIND
    assert item["payload"]["contract"]["deletions"] == "never_proposed"
    assert (base / "notes/roadmap.md").is_file()  # nothing moved yet
    assert ("workspace_reorg_proposed", ) == tuple(event for event, _ in audits)

    applied = service.approve_and_apply(item["id"])

    assert applied["moves"]["applied"] == [
        {"source": "notes/roadmap.md", "target": "topics/출시-계획/roadmap.md"}
    ]
    assert applied["moves"]["deleted"] == 0
    assert not (base / "notes/roadmap.md").exists()
    assert (base / "topics/출시-계획/roadmap.md").read_text(encoding="utf-8")
    assert queue.get(item["id"])["status"] == "approved"


def test_the_planner_can_be_driven_without_an_audit_sink(tmp_path):
    base = _workspace(tmp_path)
    created = []

    class _Queue:
        def create(self, **fields):
            created.append(fields)
            return {"id": "review-1", **fields}

    staged = reorg.propose_reorganization(
        root="", resolve_path=_resolver(base), review_queue=_Queue(),
        graph=_brain(tmp_path),
    )

    assert staged["proposed"]["id"] == "review-1"
    assert created[0]["source"] == "change_proposal"


def test_nothing_to_tidy_produces_no_proposal(tmp_path):
    base = _workspace(tmp_path)
    service, queue, _audits = _service(tmp_path, base)

    staged = service.propose_reorganization(root="", graph=None)

    assert staged["proposed"] is None
    assert staged["reason"] == "no_graph"
    assert queue.list()["items"] == []


def test_a_stale_move_is_skipped_rather_than_forced(tmp_path):
    base = _workspace(tmp_path, files=("here.md", "taken.md", "topics/t/taken.md"))
    payload = {
        "root": "",
        "moves": [
            {"source": "gone.md", "target": "topics/t/gone.md"},
            {"source": "taken.md", "target": "topics/t/taken.md"},
            {"source": "here.md"},
            {"source": "here.md", "target": "topics/t/here.md"},
        ],
    }

    result = reorg.apply_reorganization(payload, resolve_path=_resolver(base))

    assert result["applied"] == [{"source": "here.md", "target": "topics/t/here.md"}]
    assert result["skipped"] == [
        {"source": "gone.md", "reason": "source_missing"},
        {"source": "taken.md", "reason": "target_exists"},
        {"source": "here.md", "reason": "incomplete_move"},
    ]
    assert (base / "taken.md").is_file()  # never overwritten, never deleted


def test_moves_are_resolved_under_the_proposals_root(tmp_path):
    base = _workspace(tmp_path, files=("project/a.md",))
    payload = {"root": "project", "moves": [{"source": "a.md", "target": "docs/a.md"}]}

    result = reorg.apply_reorganization(payload, resolve_path=_resolver(base))

    assert result["applied_count"] == 1
    assert (base / "project/docs/a.md").is_file()


# ── Workspace OS seam ────────────────────────────────────────────────────────


def test_the_workspace_os_records_that_a_tidy_up_was_asked_for(tmp_path):
    base = _workspace(tmp_path)
    service, _queue, _audits = _service(tmp_path, base)
    store = WorkspaceOSStore(tmp_path / "data")

    asked = store.propose_reorganization(
        root="", change_proposals=service, graph=_brain(tmp_path), user_email="me@example.com"
    )
    nothing = store.propose_reorganization(root="", change_proposals=service, graph=None)

    events = [
        entry for entry in store.timeline()["events"]
        if entry.get("event_type") == "workspace_reorg_proposed"
    ]
    assert len(events) == 2
    recorded = {event["payload"]["proposal_id"] for event in events}
    assert None in recorded  # the empty ask is recorded too
    assert asked["proposed"]["id"] in recorded
    assert asked["proposed"] is not None
    assert nothing["proposed"] is None
