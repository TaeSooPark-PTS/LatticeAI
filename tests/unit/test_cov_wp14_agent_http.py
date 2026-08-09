"""`/agent` HTTP adapter — eval harness, routes, and the degraded paths.

The approval loop's happy paths are covered by ``test_agent_approval_flow``.
This file drives the parts that only appear when a collaborator misbehaves or
an optional dependency is wired: the skill-eval endpoint, the registered route
functions, background-task bookkeeping, the autonomy dial failing, project
sessions, funnel metrics, the artifact ledger, and every durable-store failure
the controller is supposed to survive.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.testclient import TestClient

from latticeai.api.chat_agent_http import AgentHTTPController
from latticeai.api.chat_contracts import (
    AgentEvalRequest,
    AgentRequest,
    AgentResumeRequest,
)
from latticeai.core.agent import AgentState
from latticeai.core.run_store import AgentRunStore

# ── fakes ───────────────────────────────────────────────────────────────


class _FakeRuntime:
    """Scripted stand-in for :class:`SingleAgentRuntime`.

    Only the ports ``AgentHTTPController`` actually calls are implemented, so
    a test can pin a terminal state or make the autonomy dial fail without
    driving a whole LLM loop.
    """

    def __init__(
        self,
        *,
        requires_approval: bool = False,
        terminal: AgentState = AgentState.DONE,
        permission_mode: Optional[str] = "auto",
        permission_boom: bool = False,
        files: tuple = (),
    ) -> None:
        self.requires_approval = requires_approval
        self.terminal = terminal
        self.permission_mode = permission_mode
        self.permission_boom = permission_boom
        self.files = list(files)
        self.approvals: List[tuple] = []
        self.finished: List[Dict[str, Any]] = []
        self.memory_updates: List[str] = []
        self.seen_contexts: List[Any] = []

    def resolve_permission_mode(self, *, user_email, workspace_id):
        if self.permission_boom:
            raise RuntimeError("permission store offline")
        return SimpleNamespace(value=self.permission_mode)

    async def plan(self, ctx, req, language_hint, current_user, model_id=None):
        self.seen_contexts.append(ctx)
        ctx.plan = {"goal": req.message, "steps": [{"action": "write_file"}]}
        ctx.state = AgentState.PLANNING

    def approval_requirements(self, ctx):
        return {
            "requires_approval": self.requires_approval,
            "non_auto_steps": ["run_command"] if self.requires_approval else [],
            "plan_summary": str(ctx.plan.get("goal") or ""),
        }

    def approve(self, ctx, current_user, approved_by_human=False):
        self.approvals.append((current_user, approved_by_human))
        ctx.approved_by_human = approved_by_human

    async def run_to_completion(
        self, ctx, req, language_hint, current_user, max_steps, max_retry
    ):
        self.finished.append({
            "message": req.message,
            "max_steps": max_steps,
            "max_retry": max_retry,
            "project_context": ctx.project_context,
            "permission_mode": ctx.permission_mode,
            "plan": dict(ctx.plan),
        })
        for path in self.files:
            ctx.transcript.append({
                "state": AgentState.EXECUTING.value,
                "action": "write_file",
                "result": {"path": path, "bytes": 11},
            })
        ctx.state = self.terminal
        ctx.state_history.append(ctx.state.value)
        ctx.final_message = "완료했습니다"

    async def memory_update(self, ctx, req, current_user):
        self.memory_updates.append(current_user)


class _FlakyStore:
    """Real durable store with named operations forced to fail."""

    def __init__(self, inner: AgentRunStore, *, fail=()) -> None:
        self.inner = inner
        self.fail = set(fail)
        self.calls: List[str] = []

    def _guard(self, name: str) -> None:
        self.calls.append(name)
        if name in self.fail:
            raise OSError(name + " unavailable")

    def sweep_expired(self, *a, **k):
        self._guard("sweep_expired")
        return self.inner.sweep_expired(*a, **k)

    def save(self, run_id, **kwargs):
        self._guard("save")
        return self.inner.save(run_id, **kwargs)

    def delete(self, run_id):
        self._guard("delete")
        return self.inner.delete(run_id)

    def load(self, run_id):
        self._guard("load")
        return self.inner.load(run_id)

    def pending_summaries(self, user=None):
        self._guard("pending_summaries")
        return self.inner.pending_summaries(user)


class _Funnel:
    def __init__(self, *, boom=()) -> None:
        self.counts: Dict[str, int] = {}
        self.boom = set(boom)

    def increment(self, name: str) -> None:
        if name in self.boom:
            raise RuntimeError("metrics backend down")
        self.counts[name] = self.counts.get(name, 0) + 1


class _ProjectSessions:
    def __init__(self, *, summary_boom=False, record_boom=False) -> None:
        self.summary_boom = summary_boom
        self.record_boom = record_boom
        self.summary_calls: List[Dict[str, Any]] = []
        self.records: List[Dict[str, Any]] = []

    def summary(self, project_id, *, user_email=None, workspace_id=None):
        self.summary_calls.append({
            "project_id": project_id,
            "user_email": user_email,
            "workspace_id": workspace_id,
        })
        if self.summary_boom:
            raise RuntimeError("project store offline")
        return "지난 턴: index.html 작성 완료"

    def record_run(self, project_id, **kwargs):
        if self.record_boom:
            raise RuntimeError("project store offline")
        self.records.append({"project_id": project_id, **kwargs})


class _Ledger:
    def __init__(self, *, boom=False) -> None:
        self.boom = boom
        self.records: List[Dict[str, Any]] = []

    def record(self, files, *, user_email=None, conversation_id=None, workspace_id=None):
        if self.boom:
            raise RuntimeError("ledger offline")
        self.records.append({
            "files": files,
            "user_email": user_email,
            "conversation_id": conversation_id,
            "workspace_id": workspace_id,
        })


def _flaky(tmp_path, *, fail=()) -> _FlakyStore:
    return _FlakyStore(AgentRunStore(tmp_path / "data" / "agent_runs"), fail=fail)


def _controller(tmp_path, runtime=None, **overrides) -> AgentHTTPController:
    kwargs: Dict[str, Any] = {
        "runtime": runtime if runtime is not None else _FakeRuntime(),
        "model_router": SimpleNamespace(current_model_id="local-test"),
        # never touch ``request.user`` — starlette asserts on it without the
        # authentication middleware installed.
        "require_user": lambda request: getattr(request, "_test_user", "owner@example.com"),
        "require_admin": None,
        "enforce_rate_limit": lambda *a, **k: None,
        "authenticated_identity": lambda current, claimed, language="ko": current,
        "write_workspace": lambda requested, user: requested,
        "save_to_history": lambda *a, **k: None,
        "workspace_store": SimpleNamespace(record_agent_run=lambda **kw: {"id": "r"}),
        "workspace_graph": lambda: None,
        "hooks": None,
        "execute_tool": lambda name, args: {"success": True},
        "base_dir": tmp_path,
        "agent_root": tmp_path,
        "ensure_agent_root": lambda: None,
    }
    kwargs.update(overrides)
    return AgentHTTPController(**kwargs)


def _request(user="owner@example.com", headers=None, query=None):
    return SimpleNamespace(
        headers=headers if headers is not None else {},
        query_params=query if query is not None else {},
        _test_user=user,
    )


def _client(controller) -> TestClient:
    router = APIRouter()
    controller.register_routes(router)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _write_skill(base_dir, name: str, schema: Dict[str, Any]):
    skill_dir = base_dir / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "schema.json").write_text(
        json.dumps(schema, ensure_ascii=False), encoding="utf-8"
    )
    return skill_dir


# ── construction / bookkeeping ──────────────────────────────────────────

def test_startup_sweep_failure_never_blocks_construction(tmp_path, caplog):
    caplog.set_level(logging.WARNING)
    store = _flaky(tmp_path, fail={"sweep_expired"})
    controller = _controller(tmp_path, run_store=store)
    assert controller.run_store is store
    assert store.calls == ["sweep_expired"]
    assert "agent run store sweep failed" in caplog.text


def test_background_task_failure_and_cancellation_are_absorbed(tmp_path, caplog):
    caplog.set_level(logging.WARNING)
    controller = _controller(tmp_path)

    async def scenario():
        async def boom():
            raise ValueError("memory update exploded")

        controller._schedule_background_task(boom())
        failing = next(iter(controller._background_tasks))
        try:
            await failing
        except ValueError:
            pass
        await asyncio.sleep(0)
        assert failing not in controller._background_tasks

        async def forever():
            await asyncio.Event().wait()

        controller._schedule_background_task(forever())
        pending = next(iter(controller._background_tasks))
        await asyncio.sleep(0)
        pending.cancel()
        try:
            await pending
        except asyncio.CancelledError:
            pass
        await asyncio.sleep(0)
        assert pending not in controller._background_tasks

    asyncio.run(scenario())
    assert "background chat task failed" in caplog.text


# ── /agent/eval ─────────────────────────────────────────────────────────

def test_eval_prefers_the_admin_gate_when_one_is_wired(tmp_path):
    _write_skill(tmp_path, "demo", {"action": "noop", "evals": []})
    seen = {"admin": 0, "user": 0}
    controller = _controller(
        tmp_path,
        require_admin=lambda request: seen.__setitem__("admin", seen["admin"] + 1),
        require_user=lambda request: seen.__setitem__("user", seen["user"] + 1),
    )
    asyncio.run(controller.eval(AgentEvalRequest(skill="demo"), _request()))
    assert seen == {"admin": 1, "user": 0}


def test_eval_falls_back_to_the_user_gate_without_an_admin_gate(tmp_path):
    _write_skill(tmp_path, "demo", {"action": "noop", "evals": []})
    seen: List[str] = []
    controller = _controller(
        tmp_path,
        require_admin=None,
        require_user=lambda request: seen.append("user") or "owner@example.com",
    )
    asyncio.run(controller.eval(AgentEvalRequest(skill="demo"), _request()))
    assert seen == ["user"]


@pytest.mark.parametrize("skill", ["../secrets", "bad name", "", "has/slash"])
def test_eval_rejects_malformed_skill_names(tmp_path, skill):
    controller = _controller(tmp_path)
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(controller.eval(AgentEvalRequest(skill=skill), _request()))
    assert excinfo.value.status_code == 400
    assert excinfo.value.detail == "Invalid skill name."


def test_eval_rejects_a_skill_symlinked_out_of_the_skills_root(tmp_path):
    skills_root = tmp_path / "skills"
    skills_root.mkdir()
    outside = tmp_path / "elsewhere" / "escaped"
    outside.mkdir(parents=True)
    (outside / "schema.json").write_text("{}", encoding="utf-8")
    (skills_root / "escaped").symlink_to(outside, target_is_directory=True)

    controller = _controller(tmp_path)
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(controller.eval(AgentEvalRequest(skill="escaped"), _request()))
    assert excinfo.value.status_code == 400
    assert excinfo.value.detail == "Invalid skill path."


def test_eval_reports_a_missing_schema_as_not_found(tmp_path):
    (tmp_path / "skills" / "bare").mkdir(parents=True)
    controller = _controller(tmp_path)
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(controller.eval(AgentEvalRequest(skill="bare"), _request()))
    assert excinfo.value.status_code == 404
    assert "bare" in excinfo.value.detail


def test_eval_without_cases_returns_an_explicit_empty_report(tmp_path):
    _write_skill(tmp_path, "demo", {"action": "noop"})
    controller = _controller(tmp_path)
    report = asyncio.run(controller.eval(AgentEvalRequest(skill="demo"), _request()))
    assert report == {
        "skill": "demo",
        "total": 0,
        "passed": 0,
        "failed": 0,
        "results": [],
        "message": "No eval cases defined in schema.json",
    }


def test_eval_case_id_filter_that_matches_nothing_is_an_empty_report(tmp_path):
    _write_skill(tmp_path, "demo", {
        "action": "noop",
        "evals": [{"id": "a", "input": {"x": 1}}],
    })
    controller = _controller(tmp_path)
    report = asyncio.run(
        controller.eval(AgentEvalRequest(skill="demo", case_id="zzz"), _request())
    )
    assert report["total"] == 0
    assert report["message"] == "No eval cases defined in schema.json"


def test_eval_scores_every_case_against_its_pass_criteria(tmp_path):
    _write_skill(tmp_path, "demo", {
        "action": "make_note",
        "evals": [
            {"id": "ok", "description": "succeeds", "input": {"path": "a.md"},
             "pass_criteria": "success == true"},
            {"id": "denied", "description": "should have failed",
             "input": {"path": "b.md"}, "pass_criteria": "success == false"},
            {"id": "loose", "description": "no criteria", "input": {"path": "c.md"}},
        ],
    })
    seen: List[tuple] = []

    def execute_tool(action, args):
        seen.append((action, dict(args)))
        return {"success": True}

    controller = _controller(tmp_path, execute_tool=execute_tool)
    report = asyncio.run(controller.eval(AgentEvalRequest(skill="demo"), _request()))

    assert report["action"] == "make_note"
    assert (report["total"], report["passed"], report["failed"]) == (3, 2, 1)
    verdicts = {case["id"]: case["passed"] for case in report["results"]}
    assert verdicts == {"ok": True, "denied": False, "loose": True}
    # every case ran with *its own* input — the bound-thunk contract
    assert seen == [
        ("make_note", {"path": "a.md"}),
        ("make_note", {"path": "b.md"}),
        ("make_note", {"path": "c.md"}),
    ]


def test_eval_records_a_raising_case_as_a_failure_without_aborting(tmp_path):
    _write_skill(tmp_path, "demo", {
        "evals": [
            {"id": "boom", "description": "explodes", "input": {"n": 1},
             "pass_criteria": "success == true"},
            {"id": "fine", "description": "works", "input": {"n": 2},
             "pass_criteria": "success == true"},
        ],
    })

    def execute_tool(action, args):
        if args.get("n") == 1:
            raise RuntimeError("tool blew up")
        return {"success": True}

    controller = _controller(tmp_path, execute_tool=execute_tool)
    report = asyncio.run(controller.eval(AgentEvalRequest(skill="demo"), _request()))

    # the skill name is the default action when schema.json omits one
    assert report["action"] == "demo"
    assert (report["total"], report["passed"], report["failed"]) == (2, 1, 1)
    failed = report["results"][0]
    assert failed["id"] == "boom" and failed["passed"] is False
    assert "tool blew up" in failed["error"]


def test_eval_case_id_selects_exactly_one_case(tmp_path):
    _write_skill(tmp_path, "demo", {
        "evals": [
            {"id": "a", "input": {"n": 1}},
            {"id": "b", "input": {"n": 2}},
        ],
    })
    controller = _controller(tmp_path)
    report = asyncio.run(
        controller.eval(AgentEvalRequest(skill="demo", case_id="b"), _request())
    )
    assert [case["id"] for case in report["results"]] == ["b"]


# ── registered routes ───────────────────────────────────────────────────

def test_registered_eval_route_answers_over_http(tmp_path):
    _write_skill(tmp_path, "demo", {
        "action": "noop",
        "evals": [{"id": "a", "input": {}, "pass_criteria": "success == true"}],
    })
    controller = _controller(
        tmp_path,
        require_user=lambda request: "owner@example.com",
        execute_tool=lambda name, args: {"success": True},
    )
    response = _client(controller).post("/agent/eval", json={"skill": "demo"})
    assert response.status_code == 200
    assert response.json()["passed"] == 1


def test_registered_approvals_route_lists_the_paused_run(tmp_path):
    controller = _controller(
        tmp_path,
        _FakeRuntime(requires_approval=True),
        require_user=lambda request: "owner@example.com",
    )
    paused = asyncio.run(
        controller.agent(AgentRequest(message="run the deploy"), _request())
    )
    response = _client(controller).get("/agent/approvals")
    assert response.status_code == 200
    listed = response.json()["pending"]
    assert [item["run_id"] for item in listed] == [paused["run_id"]]
    assert listed[0]["goal"] == "run the deploy"


# ── agent() guards ──────────────────────────────────────────────────────

def test_workspace_id_conflicting_with_the_header_is_refused(tmp_path):
    controller = _controller(tmp_path)
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(controller.agent(
            AgentRequest(message="hi", workspace_id="ws-a"),
            _request(headers={"X-Workspace-Id": "ws-b"}),
        ))
    assert excinfo.value.status_code == 403
    assert excinfo.value.detail == "workspace_id must match X-Workspace-Id."


def test_agent_refuses_to_run_without_a_loaded_model(tmp_path):
    controller = _controller(
        tmp_path, model_router=SimpleNamespace(current_model_id=None)
    )
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(controller.agent(AgentRequest(message="hi"), _request()))
    assert excinfo.value.status_code == 400
    assert "No model loaded" in excinfo.value.detail


def test_a_failing_autonomy_dial_leaves_the_mode_unset_and_runs_anyway(tmp_path):
    runtime = _FakeRuntime(permission_boom=True)
    controller = _controller(tmp_path, runtime)
    result = asyncio.run(controller.agent(AgentRequest(message="hi"), _request()))
    assert result["status"] == "ok"
    assert runtime.finished[0]["permission_mode"] is None


# ── project sessions ────────────────────────────────────────────────────

def test_project_run_reads_the_session_summary_and_folds_the_outcome_back(tmp_path):
    sessions = _ProjectSessions()
    runtime = _FakeRuntime(files=("notes.md",))
    controller = _controller(tmp_path, runtime, project_sessions=sessions)
    result = asyncio.run(controller.agent(
        AgentRequest(message="continue the site", project_id="proj-1",
                     workspace_id="ws-1"),
        _request(),
    ))

    assert sessions.summary_calls == [{
        "project_id": "proj-1",
        "user_email": "owner@example.com",
        "workspace_id": "ws-1",
    }]
    assert runtime.finished[0]["project_context"] == "지난 턴: index.html 작성 완료"
    assert result["project_id"] == "proj-1"
    recorded = sessions.records[0]
    assert recorded["project_id"] == "proj-1"
    assert recorded["status"] == "ok"
    assert recorded["final_state"] == "DONE"
    assert [f["path"] for f in recorded["files"]] == ["notes.md"]


def test_a_failing_project_store_never_gates_the_run(tmp_path, caplog):
    caplog.set_level(logging.WARNING)
    runtime = _FakeRuntime()
    controller = _controller(
        tmp_path, runtime,
        project_sessions=_ProjectSessions(summary_boom=True, record_boom=True),
    )
    result = asyncio.run(controller.agent(
        AgentRequest(message="continue", project_id="proj-1"), _request()
    ))
    assert result["status"] == "ok"
    assert runtime.finished[0]["project_context"] == ""
    assert "project session summary failed" in caplog.text
    assert "project session record failed" in caplog.text


# ── funnel metrics ──────────────────────────────────────────────────────

def test_pause_and_resume_are_counted_by_the_funnel(tmp_path):
    funnel = _Funnel()
    controller = _controller(
        tmp_path, _FakeRuntime(requires_approval=True), funnel_metrics=funnel
    )
    paused = asyncio.run(controller.agent(AgentRequest(message="deploy"), _request()))
    assert funnel.counts == {"approval_pauses": 1}

    asyncio.run(controller.resume(
        AgentResumeRequest(
            run_id=paused["run_id"],
            approval_token=paused["approval"]["token"],
            approve=True,
        ),
        _request(),
    ))
    assert funnel.counts["approval_resumes"] == 1
    assert funnel.counts["agent_runs"] == 1


def test_funnel_failures_never_break_pause_resume_or_finish(tmp_path, caplog):
    caplog.set_level(logging.WARNING)
    funnel = _Funnel(boom={"approval_pauses", "approval_resumes", "agent_runs"})
    controller = _controller(
        tmp_path, _FakeRuntime(requires_approval=True), funnel_metrics=funnel
    )
    paused = asyncio.run(controller.agent(AgentRequest(message="deploy"), _request()))
    assert paused["status"] == "awaiting_approval"

    final = asyncio.run(controller.resume(
        AgentResumeRequest(
            run_id=paused["run_id"],
            approval_token=paused["approval"]["token"],
            approve=True,
        ),
        _request(),
    ))
    assert final["status"] == "ok"
    assert funnel.counts == {}
    assert "funnel metrics increment failed" in caplog.text


def test_needs_review_terminals_are_counted_separately(tmp_path):
    funnel = _Funnel()
    controller = _controller(
        tmp_path, _FakeRuntime(terminal=AgentState.NEEDS_REVIEW),
        funnel_metrics=funnel,
    )
    result = asyncio.run(controller.agent(AgentRequest(message="hi"), _request()))
    assert result["status"] == "failed"
    assert result["final_state"] == "NEEDS_REVIEW"
    assert funnel.counts == {"agent_runs": 1, "needs_review_runs": 1}


# ── artifact ledger ─────────────────────────────────────────────────────

def test_created_files_are_handed_to_the_artifact_ledger(tmp_path):
    ledger = _Ledger()
    controller = _controller(
        tmp_path, _FakeRuntime(files=("notes.md",)), artifact_ledger=ledger
    )
    result = asyncio.run(controller.agent(
        AgentRequest(message="write notes.md", conversation_id="conv-9",
                     workspace_id="ws-1"),
        _request(),
    ))
    assert [f["path"] for f in result["created_files"]] == ["notes.md"]
    assert ledger.records == [{
        "files": result["created_files"],
        "user_email": "owner@example.com",
        "conversation_id": "conv-9",
        "workspace_id": "ws-1",
    }]


def test_a_failing_ledger_never_fails_the_run(tmp_path, caplog):
    caplog.set_level(logging.WARNING)
    controller = _controller(
        tmp_path, _FakeRuntime(files=("notes.md",)), artifact_ledger=_Ledger(boom=True)
    )
    result = asyncio.run(controller.agent(
        AgentRequest(message="write notes.md"), _request()
    ))
    assert result["status"] == "ok"
    assert "artifact ledger record failed" in caplog.text


# ── approval hygiene: purge + listing ───────────────────────────────────

def _pause(controller, message="deploy the site", user="owner@example.com", **kwargs):
    return asyncio.run(
        controller.agent(AgentRequest(message=message, **kwargs), _request(user=user))
    )


def test_a_new_pause_purges_expired_ones_from_memory_and_disk(tmp_path):
    controller = _controller(tmp_path, _FakeRuntime(requires_approval=True))
    stale = _pause(controller, "old run")
    controller._approvals[stale["run_id"]]["expires_monotonic"] = time.monotonic() - 1

    fresh = _pause(controller, "new run")
    assert stale["run_id"] not in controller._approvals
    assert fresh["run_id"] in controller._approvals
    runs_dir = tmp_path / "data" / "agent_runs"
    assert not (runs_dir / (stale["run_id"] + ".json")).exists()
    assert (runs_dir / (fresh["run_id"] + ".json")).exists()


def test_purge_survives_a_store_that_cannot_delete(tmp_path):
    controller = _controller(
        tmp_path, _FakeRuntime(requires_approval=True), run_store=_flaky(tmp_path)
    )
    stale = _pause(controller, "old run")
    controller._approvals[stale["run_id"]]["expires_monotonic"] = time.monotonic() - 1
    controller.run_store.fail.add("delete")

    fresh = _pause(controller, "new run")
    assert stale["run_id"] not in controller._approvals
    assert fresh["status"] == "awaiting_approval"


def test_pending_approvals_never_leaks_another_users_paused_run(tmp_path):
    controller = _controller(tmp_path, _FakeRuntime(requires_approval=True))
    mine = _pause(controller, "my run")
    assert [p["run_id"] for p in
            controller.pending_approvals(_request())["pending"]] == [mine["run_id"]]
    assert controller.pending_approvals(
        _request(user="intruder@example.com")
    )["pending"] == []


def test_pending_approvals_drops_expired_entries_and_survives_a_listing_failure(
    tmp_path, caplog
):
    caplog.set_level(logging.WARNING)
    controller = _controller(
        tmp_path, _FakeRuntime(requires_approval=True), run_store=_flaky(tmp_path)
    )
    paused = _pause(controller)
    controller._approvals[paused["run_id"]]["expires_monotonic"] = time.monotonic() - 1
    controller.run_store.fail.add("pending_summaries")

    assert controller.pending_approvals(_request()) == {"pending": []}
    assert "agent run store listing failed" in caplog.text


# ── durable restore failures ────────────────────────────────────────────

def test_an_unreadable_run_record_resumes_as_not_found(tmp_path, caplog):
    caplog.set_level(logging.WARNING)
    controller = _controller(tmp_path, _FakeRuntime(requires_approval=True))
    paused = _pause(controller)

    reborn = _controller(
        tmp_path, _FakeRuntime(requires_approval=True),
        run_store=_flaky(tmp_path, fail={"load"}),
    )
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(reborn.resume(
            AgentResumeRequest(run_id=paused["run_id"],
                               approval_token=paused["approval"]["token"],
                               approve=True),
            _request(),
        ))
    assert excinfo.value.status_code == 404
    assert "agent run store load failed" in caplog.text


def test_an_expired_record_still_answers_410_when_cleanup_fails(tmp_path):
    controller = _controller(tmp_path, _FakeRuntime(requires_approval=True))
    paused = _pause(controller, "run the deploy")
    record_path = tmp_path / "data" / "agent_runs" / (paused["run_id"] + ".json")
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["expires_epoch"] = time.time() - 5
    record_path.write_text(json.dumps(record), encoding="utf-8")

    reborn = _controller(
        tmp_path, _FakeRuntime(requires_approval=True),
        run_store=_flaky(tmp_path, fail={"delete"}),
    )
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(reborn.resume(
            AgentResumeRequest(run_id=paused["run_id"],
                               approval_token=paused["approval"]["token"],
                               approve=True),
            _request(),
        ))
    assert excinfo.value.status_code == 410
    assert excinfo.value.detail["error"] == "approval_expired"
    assert excinfo.value.detail["replan"]["message"] == "run the deploy"
    # cleanup failed, so the record is still there — the answer is unchanged
    assert record_path.exists()


def test_an_unreconstructable_request_record_resumes_as_not_found(tmp_path, caplog):
    caplog.set_level(logging.WARNING)
    controller = _controller(tmp_path, _FakeRuntime(requires_approval=True))
    paused = _pause(controller)
    record_path = tmp_path / "data" / "agent_runs" / (paused["run_id"] + ".json")
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["req"] = {}  # AgentRequest.message is required
    record_path.write_text(json.dumps(record), encoding="utf-8")

    reborn = _controller(tmp_path, _FakeRuntime(requires_approval=True))
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(reborn.resume(
            AgentResumeRequest(run_id=paused["run_id"],
                               approval_token=paused["approval"]["token"],
                               approve=True),
            _request(),
        ))
    assert excinfo.value.status_code == 404
    assert "agent run store request restore failed" in caplog.text


def test_resume_without_any_token_is_refused(tmp_path):
    controller = _controller(tmp_path, _FakeRuntime(requires_approval=True))
    paused = _pause(controller)
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(controller.resume(
            AgentResumeRequest(run_id=paused["run_id"], approve=True), _request()
        ))
    assert excinfo.value.status_code == 403
    assert excinfo.value.detail == "Invalid approval token for this run."
    # a refused resume must not consume the pause
    assert paused["run_id"] in controller._approvals


# ── awaiting_approval resume: store failures + deny bookkeeping ─────────

def test_expired_run_answers_410_even_when_the_store_cannot_delete(tmp_path):
    controller = _controller(
        tmp_path, _FakeRuntime(requires_approval=True), run_store=_flaky(tmp_path)
    )
    paused = _pause(controller, "run the deploy")
    controller._approvals[paused["run_id"]]["expires_monotonic"] = time.monotonic() - 1
    controller.run_store.fail.add("delete")

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(controller.resume(
            AgentResumeRequest(run_id=paused["run_id"],
                               approval_token=paused["approval"]["token"],
                               approve=True),
            _request(),
        ))
    assert excinfo.value.status_code == 410
    assert excinfo.value.detail["replan"]["message"] == "run the deploy"
    assert paused["run_id"] not in controller._approvals


def test_resume_consumes_the_run_even_when_the_store_delete_fails(tmp_path, caplog):
    caplog.set_level(logging.WARNING)
    controller = _controller(
        tmp_path, _FakeRuntime(requires_approval=True), run_store=_flaky(tmp_path)
    )
    paused = _pause(controller)
    controller.run_store.fail.add("delete")

    final = asyncio.run(controller.resume(
        AgentResumeRequest(run_id=paused["run_id"],
                           approval_token=paused["approval"]["token"],
                           approve=True),
        _request(),
    ))
    assert final["status"] == "ok"
    assert paused["run_id"] not in controller._approvals
    assert "agent run store delete failed" in caplog.text


def test_denied_resume_is_still_cancelled_when_the_workspace_record_fails(
    tmp_path, caplog
):
    caplog.set_level(logging.WARNING)

    def record_agent_run(**kwargs):
        raise RuntimeError("workspace store offline")

    controller = _controller(
        tmp_path, _FakeRuntime(requires_approval=True),
        workspace_store=SimpleNamespace(record_agent_run=record_agent_run),
    )
    paused = _pause(controller)
    outcome = asyncio.run(controller.resume(
        AgentResumeRequest(run_id=paused["run_id"],
                           approval_token=paused["approval"]["token"],
                           approve=False),
        _request(),
    ))
    assert outcome == {
        "status": "cancelled",
        "run_id": paused["run_id"],
        "response": "사용자가 계획을 취소했습니다.",
    }
    assert "workspace agent run record failed" in caplog.text


# ── legacy context_id resume ────────────────────────────────────────────

def _legacy_pause(controller, message="run the deploy"):
    return asyncio.run(controller.agent(
        AgentRequest(message=message, human_in_loop=True), _request()
    ))


def test_expired_legacy_context_on_disk_answers_the_historical_404(tmp_path):
    controller = _controller(tmp_path)
    paused = _legacy_pause(controller)
    record_path = tmp_path / "data" / "agent_runs" / (paused["context_id"] + ".json")
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["expires_epoch"] = time.time() - 5
    record_path.write_text(json.dumps(record), encoding="utf-8")

    reborn = _controller(tmp_path)
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(reborn.resume(
            AgentResumeRequest(context_id=paused["context_id"], approved=True),
            _request(),
        ))
    assert excinfo.value.status_code == 404
    assert "Agent context not found or expired" in excinfo.value.detail


def test_legacy_resume_propagates_non_expiry_restore_errors(tmp_path, monkeypatch):
    controller = _controller(tmp_path)
    paused = _legacy_pause(controller)
    controller._approvals.clear()

    def blow_up(run_id):
        raise HTTPException(status_code=503, detail="run store unavailable")

    monkeypatch.setattr(controller, "_restore_persisted_approval", blow_up)
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(controller.resume(
            AgentResumeRequest(context_id=paused["context_id"], approved=True),
            _request(),
        ))
    assert excinfo.value.status_code == 503
    assert excinfo.value.detail == "run store unavailable"


def test_expired_legacy_context_in_memory_is_dropped_and_reported_missing(tmp_path):
    controller = _controller(tmp_path, run_store=_flaky(tmp_path))
    paused = _legacy_pause(controller)
    controller._approvals[paused["context_id"]]["expires_monotonic"] = (
        time.monotonic() - 1
    )
    controller.run_store.fail.add("delete")

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(controller.resume(
            AgentResumeRequest(context_id=paused["context_id"], approved=True),
            _request(),
        ))
    assert excinfo.value.status_code == 404
    assert paused["context_id"] not in controller._approvals


def test_legacy_resume_completes_even_when_the_store_delete_fails(tmp_path, caplog):
    caplog.set_level(logging.WARNING)
    runtime = _FakeRuntime()
    controller = _controller(tmp_path, runtime, run_store=_flaky(tmp_path))
    paused = _legacy_pause(controller)
    controller.run_store.fail.add("delete")

    final = asyncio.run(controller.resume(
        AgentResumeRequest(context_id=paused["context_id"], approved=True),
        _request(),
    ))
    assert final["status"] == "ok"
    assert runtime.approvals[-1] == ("owner@example.com", True)
    assert "agent run store delete failed" in caplog.text


def test_legacy_resume_with_a_modified_plan_normalizes_and_records_the_edit(tmp_path):
    runtime = _FakeRuntime()
    controller = _controller(tmp_path, runtime)
    paused = _legacy_pause(controller)

    final = asyncio.run(controller.resume(
        AgentResumeRequest(
            context_id=paused["context_id"],
            approved=True,
            modified_plan={
                "goal": "deploy only the docs",
                "steps": [{"action": "write_file"}, "junk"],
            },
            executing_model="exec-model",
            reviewing_model="review-model",
        ),
        _request(),
    ))
    assert final["status"] == "ok"
    edits = [step for step in final["steps"] if step.get("edited_plan")]
    assert len(edits) == 1
    assert edits[0]["state"] == "WAITING_APPROVAL"
    assert "steps_filtered" in edits[0]["plan_fixes"]
    # the normalized plan (junk step dropped) is what the run executed
    assert runtime.finished[-1]["plan"]["goal"] == "deploy only the docs"
    assert runtime.finished[-1]["plan"]["steps"] == [{"action": "write_file"}]
