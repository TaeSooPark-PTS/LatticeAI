"""Interactive approval loop — awaiting_approval status + token-gated resume.

A plan that needs human approval must pause (never silently execute, never
dead-end as FAILED): the response carries ``status=awaiting_approval`` and a
short-TTL approval token bound to the run and its user. Resume validates the
token (expired/mismatched/other-user → 4xx) and only then continues with the
approval granted — the fail-closed guarantee of ``approve()`` is untouched.
"""

import asyncio
import json
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from latticeai.api.chat_agent_http import AgentHTTPController
from latticeai.api.chat_contracts import AgentRequest, AgentResumeRequest
from latticeai.core.agent import AgentDeps, PhaseBudgets, SingleAgentRuntime


PLAN_JSON = json.dumps({
    "action": "plan",
    "state": "PLAN",
    "goal": "list workspace files",
    "steps": [
        {"id": 1, "description": "run ls in the workspace", "action": "run_command"},
    ],
    "requires_approval": True,
    "rollback_strategy": "none",
    "estimated_steps": 1,
})

VERDICT_JSON = json.dumps({
    "action": "verdict",
    "verdict": "PASS",
    "next_state": "DONE",
    "reason": "run_command result confirms the listing",
    "corrections": [],
})


def _runtime(executed, llm_calls=None, phase_budgets=None):
    exec_replies = [
        json.dumps({
            "thoughts": "list it",
            "action": "run_command",
            "args": {"command": "ls"},
        }),
        json.dumps({"thoughts": "verified", "action": "final", "message": "done"}),
    ]
    state = {"exec_index": 0}

    async def generate_as(model_id, *, message, context, max_tokens, temperature):
        if llm_calls is not None:
            llm_calls.append({"message": message, "max_tokens": max_tokens})
        if "execution plan" in message:
            return PLAN_JSON
        if message == "Execute the next step.":
            reply = exec_replies[min(state["exec_index"], len(exec_replies) - 1)]
            state["exec_index"] += 1
            return reply
        return VERDICT_JSON

    async def generate(*, message, context, max_tokens, temperature):
        return json.dumps({"action": "memory", "learnings": [], "save_to_knowledge": False})

    def execute_tool(name, args):
        executed.append({"action": name, "args": dict(args)})
        return {"success": True, "output": "file_a\nfile_b"}

    deps = AgentDeps(
        generate_as=generate_as,
        generate=generate,
        execute_tool=execute_tool,
        policy_for=lambda name, args: {
            "risk": "exec", "destructive": False, "shell": True,
            "network": False, "auto_approve": False, "sandbox": "workspace",
            "rollback": "none",
        },
        risk_level=lambda policy: "high",
        check_role=lambda name, user: None,
        tool_governance={"run_command": {"auto_approve": False}},
        file_create_actions=frozenset({"write_file"}),
        recent_chat_context=lambda **kwargs: "",
        clear_history=lambda keep_last: {},
        knowledge_save=lambda *a, **k: None,
        audit=lambda *a, **k: None,
        planner_prompt="PLAN", executor_prompt="EXEC",
        critic_prompt="CRIT", memory_updater_prompt="MEM",
        agent_root=None,
        phase_budgets=phase_budgets,
    )
    return SingleAgentRuntime(deps)


def _controller(tmp_path, runtime, run_records=None):
    def record_agent_run(**kwargs):
        if run_records is not None:
            run_records.append(kwargs)
        return {"id": "run-record"}

    return AgentHTTPController(
        runtime=runtime,
        model_router=SimpleNamespace(current_model_id="local-test"),
        require_user=lambda request: getattr(request, "user", "owner@example.com"),
        require_admin=None,
        enforce_rate_limit=lambda *a, **k: None,
        authenticated_identity=lambda current, claimed: current,
        write_workspace=lambda requested, user: requested,
        save_to_history=lambda *a, **k: None,
        workspace_store=SimpleNamespace(record_agent_run=record_agent_run),
        workspace_graph=lambda: None,
        hooks=None,
        execute_tool=lambda name, args: {},
        base_dir=tmp_path,
        agent_root=tmp_path,
        ensure_agent_root=lambda: None,
    )


def _request(user="owner@example.com"):
    return SimpleNamespace(headers={}, query_params={}, user=user)


def _start(controller):
    return asyncio.run(
        controller.agent(AgentRequest(message="run ls in the workspace"), _request())
    )


# ── pausing ─────────────────────────────────────────────────────────────

def test_approval_needing_plan_pauses_as_awaiting_approval(tmp_path):
    executed = []
    controller = _controller(tmp_path, _runtime(executed))
    result = _start(controller)

    assert result["status"] == "awaiting_approval"
    assert result["run_id"]
    approval = result["approval"]
    assert approval["token"] and approval["expires_at"]
    assert "run ls" in approval["plan_summary"]
    assert result["final_state"] == "WAITING_APPROVAL"
    assert result["non_auto_steps"] == ["run_command"]
    # fail-closed: nothing executed while paused
    assert executed == []


def test_auto_approvable_plan_still_runs_without_pausing(tmp_path):
    executed = []
    runtime = _runtime(executed)
    runtime.deps.tool_governance = {"run_command": {"auto_approve": True}}
    runtime.deps.policy_for = lambda name, args: {
        "risk": "exec", "destructive": False, "shell": True,
        "network": False, "auto_approve": True, "sandbox": "workspace",
        "rollback": "none",
    }

    async def plan_no_approval(model_id, *, message, context, max_tokens, temperature):
        if "execution plan" in message:
            plan = json.loads(PLAN_JSON)
            plan["requires_approval"] = False
            return json.dumps(plan)
        return await original(model_id, message=message, context=context,
                              max_tokens=max_tokens, temperature=temperature)

    original = runtime.deps.generate_as
    runtime.deps.generate_as = plan_no_approval
    controller = _controller(tmp_path, runtime)
    result = _start(controller)
    assert result["status"] == "ok"
    assert executed and executed[0]["action"] == "run_command"


# ── token validation ────────────────────────────────────────────────────

def test_resume_with_wrong_token_is_rejected(tmp_path):
    executed = []
    controller = _controller(tmp_path, _runtime(executed))
    result = _start(controller)

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(controller.resume(
            AgentResumeRequest(run_id=result["run_id"], approval_token="wrong", approve=True),
            _request(),
        ))
    assert excinfo.value.status_code == 403
    assert executed == []


def test_resume_with_expired_token_is_gone(tmp_path):
    executed = []
    controller = _controller(tmp_path, _runtime(executed))
    result = _start(controller)

    controller._approvals[result["run_id"]]["expires_monotonic"] = time.monotonic() - 1
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(controller.resume(
            AgentResumeRequest(
                run_id=result["run_id"],
                approval_token=result["approval"]["token"],
                approve=True,
            ),
            _request(),
        ))
    assert excinfo.value.status_code == 410
    assert result["run_id"] not in controller._approvals
    assert executed == []


def test_resume_for_unknown_run_is_not_found(tmp_path):
    controller = _controller(tmp_path, _runtime([]))
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(controller.resume(
            AgentResumeRequest(run_id="no-such-run", approval_token="x", approve=True),
            _request(),
        ))
    assert excinfo.value.status_code == 404


def test_resume_by_another_user_is_forbidden(tmp_path):
    executed = []
    controller = _controller(tmp_path, _runtime(executed))
    result = _start(controller)

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(controller.resume(
            AgentResumeRequest(
                run_id=result["run_id"],
                approval_token=result["approval"]["token"],
                approve=True,
            ),
            _request(user="intruder@example.com"),
        ))
    assert excinfo.value.status_code == 403
    assert executed == []


def test_resume_without_any_identifier_is_bad_request(tmp_path):
    controller = _controller(tmp_path, _runtime([]))
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(controller.resume(AgentResumeRequest(), _request()))
    assert excinfo.value.status_code == 400


# ── approve / deny ──────────────────────────────────────────────────────

def test_resume_approve_executes_governed_steps_to_done(tmp_path):
    executed = []
    run_records = []
    controller = _controller(tmp_path, _runtime(executed), run_records=run_records)
    result = _start(controller)

    final = asyncio.run(controller.resume(
        AgentResumeRequest(
            run_id=result["run_id"],
            approval_token=result["approval"]["token"],
            approve=True,
        ),
        _request(),
    ))
    assert final["status"] == "ok"
    assert final["final_state"] == "DONE"
    assert executed == [{"action": "run_command", "args": {"command": "ls"}}]
    # token is single-use
    assert result["run_id"] not in controller._approvals
    assert any(record["status"] == "ok" for record in run_records)


def test_resume_deny_cancels_without_executing(tmp_path):
    executed = []
    run_records = []
    controller = _controller(tmp_path, _runtime(executed), run_records=run_records)
    result = _start(controller)

    outcome = asyncio.run(controller.resume(
        AgentResumeRequest(
            run_id=result["run_id"],
            approval_token=result["approval"]["token"],
            approve=False,
        ),
        _request(),
    ))
    assert outcome["status"] == "cancelled"
    assert outcome["run_id"] == result["run_id"]
    assert executed == []
    assert any(record["status"] == "cancelled" for record in run_records)
    # the consumed run cannot be replayed
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(controller.resume(
            AgentResumeRequest(
                run_id=result["run_id"],
                approval_token=result["approval"]["token"],
                approve=True,
            ),
            _request(),
        ))
    assert excinfo.value.status_code == 404


def test_resume_with_edited_plan_normalizes_and_uses_it(tmp_path):
    executed = []
    controller = _controller(tmp_path, _runtime(executed))
    result = _start(controller)

    final = asyncio.run(controller.resume(
        AgentResumeRequest(
            run_id=result["run_id"],
            approval_token=result["approval"]["token"],
            approve=True,
            edited_plan={
                "goal": "run ls only in the project dir",
                "steps": [{"action": "run_command", "description": "ls project"}, "junk"],
            },
        ),
        _request(),
    ))
    assert final["status"] == "ok"
    # normalize_plan filtered the junk step and the edit is on the transcript
    assert any(step.get("edited_plan") for step in final["steps"])


# ── phase budgets ───────────────────────────────────────────────────────

def test_phase_budgets_from_env_overrides_and_clamps():
    budgets = PhaseBudgets.from_env({
        "LATTICEAI_AGENT_PLAN_TOKENS": "800",
        "LATTICEAI_AGENT_EXECUTE_TOKENS": "2048",
        "LATTICEAI_AGENT_VERIFY_TOKENS": "-5",
        "LATTICEAI_AGENT_MEMORY_TOKENS": "not-a-number",
    })
    assert budgets.plan_tokens == 800
    assert budgets.execute_tokens == 2048
    assert budgets.verify_tokens == 128  # floor — never brick the loop
    assert budgets.memory_tokens == 256  # unparseable falls back to default


def test_phase_budgets_defaults_match_historical_values():
    budgets = PhaseBudgets.from_env({})
    assert (budgets.plan_tokens, budgets.execute_tokens,
            budgets.verify_tokens, budgets.memory_tokens) == (1024, 4096, 512, 256)


def test_injected_phase_budgets_cap_each_phase(tmp_path):
    executed = []
    llm_calls = []
    budgets = PhaseBudgets(plan_tokens=333, execute_tokens=777, verify_tokens=222)
    controller = _controller(tmp_path, _runtime(executed, llm_calls=llm_calls,
                                                phase_budgets=budgets))
    result = _start(controller)
    asyncio.run(controller.resume(
        AgentResumeRequest(
            run_id=result["run_id"],
            approval_token=result["approval"]["token"],
            approve=True,
        ),
        _request(),
    ))

    by_message = {}
    for call in llm_calls:
        by_message.setdefault(call["message"], set()).add(call["max_tokens"])
    assert by_message["Produce a JSON execution plan for this request."] == {333}
    assert by_message["Execute the next step."] == {777}
    assert by_message["Review the execution transcript and return your verdict JSON."] == {222}
