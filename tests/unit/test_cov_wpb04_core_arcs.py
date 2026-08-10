"""wpb04 — never-taken branch directions in ``latticeai/core``.

Same shape as the service file: each test forces the *other* side of a
decision that has only ever gone one way — a zero-iteration loop, a guard that
has never been false, an ``elif`` chain that has never fallen through.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from latticeai.core import model_compat
from latticeai.core import workspace_onboarding as onboarding_module
from latticeai.core.agent import AgentDeps, AgentRunContext, SingleAgentRuntime
from latticeai.core.audit import build_admin_audit_report
from latticeai.core.permission_mode import PermissionMode, effective_auto_approve
from latticeai.core.project_sessions import ProjectSessionStore
from latticeai.core.realtime import RealtimeBus
from latticeai.core.run_explain import explain_run
from latticeai.core.workspace_onboarding import WorkspaceOnboarding
from latticeai.core.workspace_relationships import WorkspaceRelationships, shortest_path
from latticeai.core.workspace_snapshots import WorkspaceSnapshots

# ── model_compat ─────────────────────────────────────────────────────────────


def test_a_config_without_a_model_type_moves_on_to_the_next_candidate(tmp_path, monkeypatch):
    """model_compat.py:162→157 — a readable config.json that names nothing."""
    monkeypatch.setenv("HOME", str(tmp_path))  # keeps _hf_model_dir off the real cache
    model_dir = tmp_path / "weights" / "some-model"
    model_dir.mkdir(parents=True)
    (model_dir / "config.json").write_text(json.dumps({"model_type": "  "}), encoding="utf-8")

    assert model_compat._local_model_type(str(model_dir)) is None


def test_a_config_that_does_name_a_type_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    model_dir = tmp_path / "weights" / "named"
    model_dir.mkdir(parents=True)
    (model_dir / "config.json").write_text(json.dumps({"model_type": "Llama"}), encoding="utf-8")

    assert model_compat._local_model_type(str(model_dir)) == "llama"


def test_an_unknown_postprocessor_name_is_skipped():
    """model_compat.py:421→419 — a profile naming a postprocessor that is not
    registered leaves the text untouched and keeps going."""
    profile = {"postprocess": ["not_a_registered_step", "strip_role_tokens"]}

    assert model_compat.fast_postprocess("assistant: 안녕하세요", profile) == "안녕하세요"


# ── permission_mode ──────────────────────────────────────────────────────────


def _policy(**over) -> Dict[str, Any]:
    policy = {"risk": "write", "destructive": False, "sandbox": "workspace"}
    policy.update(over)
    return policy


def test_trusted_mode_gates_a_workspace_write_with_an_unrecognised_shape():
    """permission_mode.py:210→216 — neither the risk nor the change class is
    one trusted mode auto-approves."""
    assert effective_auto_approve(
        PermissionMode.TRUSTED,
        "write_file",
        _policy(risk="exec"),
        change_class="deletion",
    ) is False


def test_trusted_mode_never_auto_approves_a_destructive_workspace_write():
    """permission_mode.py:213→216 — the change class is fine but the policy is
    flagged destructive."""
    assert effective_auto_approve(
        PermissionMode.TRUSTED,
        "write_file",
        _policy(destructive=True),
        change_class="additive",
    ) is False


def test_bypass_allows_a_system_sandbox_read_outside_the_desktop_tools():
    """permission_mode.py:223→225 — a system-sandbox tool whose risk is not a
    write or an exec still runs under bypass."""
    assert effective_auto_approve(
        PermissionMode.BYPASS,
        "local_list",
        _policy(risk="read", sandbox="system"),
    ) is True

    # …and the write/exec side of the same guard still refuses.
    assert effective_auto_approve(
        PermissionMode.BYPASS,
        "local_list",
        _policy(risk="write", sandbox="system"),
    ) is False


# ── project_sessions ─────────────────────────────────────────────────────────


class _RacedStore(ProjectSessionStore):
    """A store whose session file is removed by someone else mid-``delete``.

    ``delete`` re-reads the record for its scope check and only then unlinks;
    this reproduces the window between those two steps.
    """

    def get(self, session_id: str, **kwargs):
        record = super().get(session_id, **kwargs)
        if record is not None:
            path = self._path(session_id)
            if path is not None and path.exists():
                path.unlink()
        return record


def test_deleting_a_session_that_already_vanished_still_reports_success(tmp_path):
    """project_sessions.py:196→198 — the unlink is skipped, not an error."""
    store = _RacedStore(tmp_path / "projects")
    session_id = store.create(title="릴리스 준비")["id"]

    assert store.delete(session_id) is True
    assert store.get(session_id) is None


def test_recording_a_run_deduplicates_its_file_list(tmp_path):
    """project_sessions.py:253→250 — a repeated / empty path is not appended
    twice; and 262→265 — a run with no explanation."""
    store = ProjectSessionStore(tmp_path / "projects")
    session_id = store.create(title="릴리스 준비")["id"]

    record = store.record_run(
        session_id,
        run_id="run-1",
        status="ok",
        final_state="DONE",
        files=["index.html", "index.html", {"path": "  "}, None],
        explanation=None,
    )

    assert record is not None
    assert record["files"] == ["index.html"]
    entry = record["runs"][-1]
    assert entry["files"] == ["index.html"]
    assert "headline" not in entry and "next_step" not in entry


# ── run_explain ──────────────────────────────────────────────────────────────


def test_repaired_and_blocked_summaries_ignore_duplicates_and_pathless_steps():
    """run_explain.py:92→83 (a repaired step with no path) and 103→99 (the same
    action blocked twice)."""
    transcript = [
        # Repaired, but neither the result nor the args name a path.
        {"action": "write_file", "content_sanitize": {"repaired": True}, "result": {}, "args": {}},
        {"action": "write_file", "content_sanitize": {"repaired": True},
         "result": {"path": "a.html"}, "args": {}},
        # Same path again — recorded once.
        {"action": "write_file", "content_sanitize": {"repaired": True},
         "result": {"path": "a.html"}, "args": {}},
        {"action": "shell", "error": "BLOCKED: not allowed"},
        {"action": "shell", "error": "BLOCKED: still not allowed"},
    ]

    ko = [detail["ko"] for detail in explain_run(
        state="DONE", loop={}, transcript=transcript)["details"]]

    repaired = next(line for line in ko if "기본 뼈대로 대신 저장" in line)
    assert repaired.endswith(": a.html"), "the pathless and duplicate steps add nothing"
    blocked = next(line for line in ko if "정책상 막혀서" in line)
    assert blocked.endswith(": shell"), "the same blocked action is named once"


def test_a_coverage_block_with_a_non_list_missing_files_is_ignored():
    """run_explain.py:148→144 — ``missing_files`` that is not a sequence keeps
    the scan walking backwards instead of trusting it."""
    transcript = [
        {"action": "write_file", "requirement_coverage": {"missing_files": ["b.html"]}},
        {"action": "verify", "requirement_coverage": {"missing_files": 3}},
    ]

    payload = explain_run(state="NEEDS_REVIEW", loop={}, transcript=transcript)

    assert payload["code"] == "missing_files", (
        "the malformed block is skipped, not treated as 'nothing missing'"
    )
    ko = [detail["ko"] for detail in payload["details"]]
    assert any(line == "만들어지지 않은 파일: b.html" for line in ko)


# ── workspace_snapshots ──────────────────────────────────────────────────────


class _SnapshotStore:
    def __init__(self, root: Path) -> None:
        self.snapshots_dir = root / "snapshots"
        self.exports_dir = root / "exports"
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        self.exports_dir.mkdir(parents=True, exist_ok=True)
        self.state: Dict[str, Any] = {}
        self.timeline: List[Any] = []

    def _resolve_scope(self, workspace_id):
        return workspace_id or "personal"

    def _scoped(self, items, workspace_id):
        return list(items)

    def load_state(self):
        return self.state

    def save_state(self, state):
        self.state = state

    def record_timeline_event(self, area, event_type, payload, workspace_id=None):
        self.timeline.append((area, event_type, payload, workspace_id))


class _ImportingGraph:
    def __init__(self) -> None:
        self.imported: Optional[Dict[str, Any]] = None

    def import_graph(self, data, mode="merge"):
        return {"mode": mode, "nodes": len(data.get("nodes") or [])}


def test_a_snapshot_taken_without_a_graph_records_empty_counts(tmp_path):
    """workspace_snapshots.py:37→41 — the graph reads are skipped entirely."""
    store = _SnapshotStore(tmp_path)
    snapshots = WorkspaceSnapshots(store)

    meta = snapshots.create_snapshot(
        name="브레인 없음", graph=None, history=[{"role": "user"}],
        settings={"theme": "dark"}, models={"loaded_models": []},
    )["snapshot"]

    assert meta["node_count"] == 0 and meta["edge_count"] == 0
    assert meta["indexed_folder_count"] == 0
    assert meta["chat_count"] == 1
    body = json.loads((store.snapshots_dir / (meta["id"] + ".json")).read_text(encoding="utf-8"))
    assert body["graph"] == {"nodes": [], "edges": []}
    assert body["graph_stats"] == {}


def _write_snapshot(store: _SnapshotStore, snapshot_id: str, graph: Dict[str, Any]) -> None:
    (store.snapshots_dir / (snapshot_id + ".json")).write_text(
        json.dumps({"id": snapshot_id, "graph": graph}, ensure_ascii=False), encoding="utf-8",
    )


def test_restoring_without_a_graph_still_records_the_event(tmp_path):
    """workspace_snapshots.py:136→148 — nothing to import into."""
    store = _SnapshotStore(tmp_path)
    _write_snapshot(store, "snapshot-1", {"nodes": [], "edges": []})

    result = WorkspaceSnapshots(store).restore_snapshot("snapshot-1", graph=None)

    assert result == {"restored": True, "snapshot_id": "snapshot-1"}
    assert "imported" not in result
    assert store.timeline[-1][1] == "snapshot_restored"


def test_restoring_a_snapshot_that_carries_its_own_counts_keeps_them(tmp_path):
    """workspace_snapshots.py:145→147 — the placeholder counts are not added
    over a snapshot that already recorded real ones."""
    store = _SnapshotStore(tmp_path)
    graph_body = {"counts": {"nodes": 7, "edges": 5}, "nodes": [{"id": "n1"}], "edges": []}
    _write_snapshot(store, "snapshot-2", graph_body)
    graph = _ImportingGraph()

    result = WorkspaceSnapshots(store).restore_snapshot("snapshot-2", graph=graph)

    assert result["imported"] == {"mode": "merge", "nodes": 1}
    assert graph.imported == {"mode": "merge", "data": graph_body}
    assert graph.imported["data"]["counts"] == {"nodes": 7, "edges": 5}


# ── agent ────────────────────────────────────────────────────────────────────


async def _never_called(*_args, **_kwargs):  # pragma: no cover - guard
    raise AssertionError("the loop must not reach the model in these tests")


def _runtime() -> SingleAgentRuntime:
    return SingleAgentRuntime(AgentDeps(
        generate_as=_never_called,
        generate=_never_called,
        execute_tool=lambda name, args: {},
        policy_for=lambda name, args: {"risk": "write", "auto_approve": True},
        risk_level=lambda policy: "low",
        check_role=lambda name, user: None,
        tool_governance={"write_file": {"auto_approve": True}},
        file_create_actions=frozenset({"write_file"}),
        recent_chat_context=lambda **kwargs: "",
        clear_history=lambda keep_last: {},
        knowledge_save=lambda *a, **k: None,
        audit=lambda *a, **k: None,
        planner_prompt="PLAN", executor_prompt="EXEC",
        critic_prompt="CRIT", memory_updater_prompt="MEM",
        agent_root=Path("."),
    ))


class _Req:
    message = "그냥 인사만 해줘"


def test_the_direct_file_fallback_refuses_when_no_step_names_a_path():
    """agent.py:721→717 — a planned file step whose path is blank contributes
    nothing, so the fallback has no target and reports failure honestly."""
    runtime = _runtime()
    ctx = AgentRunContext()
    ctx.plan = {"goal": "인사", "steps": [
        {"action": "write_file", "args": {"path": "   "}},
        {"action": "write_file", "args": {}},
    ]}

    wrote = asyncio.run(runtime._direct_file_path(ctx, _Req(), "u@example.com", None))

    assert wrote is False


def test_the_rollback_snapshot_lookup_walks_past_other_paths():
    """agent.py:1254→1253 — an entry for a different path is skipped."""
    runtime = _runtime()
    ctx = AgentRunContext()
    ctx.rollback_log = [
        {"path": "other.html", "existed": True},
        {"path": "index.html", "existed": False},
    ]

    assert runtime._snapshot_for(ctx, "index.html") == {"path": "index.html", "existed": False}
    assert runtime._snapshot_for(ctx, "missing.html") is None


# ── audit ────────────────────────────────────────────────────────────────────


def test_the_admin_report_ignores_unclassifiable_events(tmp_path):
    """audit.py:214→228 (a chat message from neither side) and 223→228 (an
    event type the report does not bucket)."""
    events = [
        {"event_type": "chat_message", "role": "system", "user_email": "u@example.com"},
        {"event_type": "model_loaded", "user_email": "u@example.com"},
        {"event_type": "chat_message", "role": "user", "user_email": "u@example.com"},
    ]

    report = build_admin_audit_report(
        tmp_path / "audit.json",
        {"u@example.com": {"role": "user"}},
        get_user_role=lambda email, users=None: "user",
        audit_events=events,
    )

    summary = report["summary"]
    assert summary["total_events"] == 3
    assert summary["chat_events"] == 2, "the system-role row is still a chat event"
    assert summary["user_messages"] == 1
    assert summary["assistant_messages"] == 0
    assert summary["delete_events"] == 0
    bucket = next(row for row in report["per_user"] if row["email"] == "u@example.com")
    assert bucket["assistant_messages"] == 0
    assert bucket["user_messages"] == 1


# ── realtime bus ─────────────────────────────────────────────────────────────


def test_replay_rechecks_the_scope_the_membership_refresh_just_narrowed():
    """realtime.py:162→158 — an event that was in scope when the tail was read
    but not when the frame was about to leave the process."""
    bus = RealtimeBus()
    sub = bus.add_subscriber("sub-1", workspace_scope={"w1", "w2"}, user="u@example.com")
    bus.publish({"area": "workspace", "event_type": "changed", "workspace_id": "w2"})

    calls: List[int] = []

    def refresh(current_sub) -> bool:
        calls.append(1)
        if len(calls) == 2:
            current_sub.workspace_scope = {"w1"}  # membership revoked mid-stream
        return len(calls) < 3

    async def _drive() -> List[str]:
        return [frame async for frame in bus.stream(
            sub, heartbeat=30.0, refresh_authorization=refresh,
        )]

    frames = asyncio.run(_drive())

    assert frames == [], "the revoked-workspace event never reaches the client"
    assert len(calls) == 3
    assert bus.stats()["subscribers"] == 0, "the stream cleans its subscriber up"


def test_leaving_with_an_unknown_client_id_publishes_nothing():
    """realtime.py:220→exit — nothing was removed, so nothing is announced."""
    bus = RealtimeBus()

    bus.leave("never-joined", user="u@example.com")

    assert bus.recent() == []
    assert bus.presence() == []


# ── workspace_onboarding ─────────────────────────────────────────────────────


class _StateStore:
    def __init__(self) -> None:
        self.state: Dict[str, Any] = {}
        self.timeline: List[Any] = []

    def load_state(self):
        return self.state

    def save_state(self, state):
        self.state = state

    def record_timeline_event(self, area, event_type, payload, workspace_id=None):
        self.timeline.append((area, event_type, payload))


def test_completing_the_last_named_step_does_not_invent_a_next_one(monkeypatch):
    """workspace_onboarding.py:82→86 — no step follows the final one."""
    monkeypatch.setattr(onboarding_module, "ONBOARDING_STEPS", ["account", "admin"])
    store = _StateStore()

    status = WorkspaceOnboarding(store).update_step("admin", status="complete")

    assert [step["id"] for step in status["steps"]] == ["account", "admin"]
    assert "current_step" not in store.state["onboarding"], (
        "there is no step after the last one, so the cursor is left where it was"
    )
    assert store.state["onboarding"]["steps"]["admin"]["status"] == "complete"


def test_a_running_step_does_not_move_the_cursor():
    """workspace_onboarding.py:84→86 — a status that is neither finished nor
    failed leaves ``current_step`` alone."""
    store = _StateStore()
    service = WorkspaceOnboarding(store)
    service.update_step("account", status="complete")
    cursor = store.state["onboarding"]["current_step"]

    service.update_step("hardware", status="running")

    assert store.state["onboarding"]["current_step"] == cursor
    assert store.state["onboarding"]["steps"]["hardware"]["status"] == "running"


def test_an_unknown_step_or_status_is_refused():
    service = WorkspaceOnboarding(_StateStore())

    with pytest.raises(ValueError, match="unknown onboarding step"):
        service.update_step("nope")
    with pytest.raises(ValueError, match="unknown onboarding status"):
        service.update_step("account", status="nope")


# ── workspace_relationships ──────────────────────────────────────────────────


def test_shortest_path_ignores_half_written_edges():
    """workspace_relationships.py:29→26 — an edge missing an endpoint is not
    adjacency data."""
    edges = [
        {"from": "a", "to": None},
        {"from": None, "to": "b"},
        {"from": "a", "to": "b"},
    ]

    assert shortest_path(edges, "a", "b") == ["a", "b"]
    assert shortest_path([{"from": "a", "to": None}], "a", "b") == []


class _WindowGraph:
    def __init__(self, payload: Dict[str, Any]) -> None:
        self.payload = payload

    def graph(self, limit=300):
        return self.payload


def test_a_dangling_inbound_edge_contributes_no_related_entity():
    """workspace_relationships.py:89→87 — the edge's other end is missing."""
    graph = _WindowGraph({
        "nodes": [{"id": "n1"}, {"id": "n2"}],
        "edges": [
            {"to": "n1", "from": None, "type": "mentions"},
            {"to": "n1", "from": "n2", "type": "mentions"},
        ],
    })

    payload = WorkspaceRelationships(store=None).explore(graph, "n1")

    assert [n["id"] for n in payload["related_entities"]] == ["n2"]
    assert len(payload["inbound"]) == 2, "the dangling edge is still reported as inbound"
