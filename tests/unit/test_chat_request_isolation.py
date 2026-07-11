"""Security and concurrency boundaries for chat and agent requests."""

from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from latticeai.api import chat as chat_api
from latticeai.api.chat import create_chat_router
from latticeai.api.chat_helpers import build_recent_chat_context
from latticeai.core.agent import AgentState
from latticeai.services.app_context import AppContext


class _Router:
    current_model_id = "default"
    loaded_model_ids = ["default", "requested"]

    def __init__(self) -> None:
        self.generated_with = None

    def switch_model(self, _model_id):
        raise AssertionError("chat requests must not mutate the process-wide model")

    async def generate_as(self, model_id, *_args, **_kwargs):
        self.generated_with = model_id
        return f"answer:{model_id}"

    async def generate(self, *_args, **_kwargs):
        return await self.generate_as(self.current_model_id, *_args, **_kwargs)


def _app(
    tmp_path: Path,
    *,
    router=None,
    require_user=None,
    workspace_service=None,
    workspace_store=None,
    agent_runtime=None,
    history_entries=None,
) -> FastAPI:
    app = FastAPI()
    context = AppContext(
        config=SimpleNamespace(is_public=False, auto_read_chat_paths=False, require_auth=True),
        model_router=router or _Router(),
        chat_service=SimpleNamespace(
            build_graph_trace=lambda *_args, **_kwargs: {},
            record_trace=lambda **_kwargs: {"id": "trace-isolated"},
        ),
        workspace_store=workspace_store or SimpleNamespace(),
        workspace_service=workspace_service,
        workspace_graph=lambda: None,
        require_user=require_user or (lambda _request: "owner@example.com"),
        enforce_rate_limit=lambda *_args, **_kwargs: None,
        get_history_user=lambda *_args, **_kwargs: {},
        save_to_history=(
            lambda *args, **kwargs: history_entries.append((args, kwargs))
            if history_entries is not None
            else None
        ),
        append_audit_event=lambda *_args, **_kwargs: None,
        clear_history=lambda *_args, **_kwargs: {"removed": 0, "kept": 0},
        clear_conversation=lambda *_args, **_kwargs: {"removed": 0, "kept": 0},
        get_history=lambda **_kwargs: [],
        group_history_conversations=lambda *_args, **_kwargs: [],
        get_conversation_messages=lambda *_args, **_kwargs: [],
        conversation_title=lambda *_args, **_kwargs: "Conversation",
        allowed_workspaces_for=lambda _user: {"personal"},
        enable_graph=False,
        knowledge_graph=None,
        public_model="",
        base_dir=tmp_path,
        chat_agent_runtime=agent_runtime,
    )
    app.include_router(create_chat_router(context))
    return app


def test_chat_rejects_claimed_identity_that_differs_from_session(tmp_path: Path) -> None:
    response = TestClient(_app(tmp_path)).post(
        "/chat",
        json={"message": "hello", "stream": False, "user_email": "attacker@example.com"},
    )

    assert response.status_code == 403
    assert "authenticated user" in response.json()["detail"]


def test_chat_uses_explicit_model_without_switching_global_default(tmp_path: Path) -> None:
    router = _Router()

    response = TestClient(_app(tmp_path, router=router)).post(
        "/chat",
        json={"message": "hello", "stream": False, "model": "requested"},
    )

    assert response.status_code == 200
    assert response.json()["response"] == "answer:requested"
    assert router.generated_with == "requested"
    assert router.current_model_id == "default"


def test_chat_rejects_workspace_without_write_permission(tmp_path: Path) -> None:
    class DeniedWorkspace:
        def resolve_write_scope(self, requested, user):
            raise PermissionError(f"{user} cannot write {requested}")

    response = TestClient(_app(tmp_path, workspace_service=DeniedWorkspace())).post(
        "/chat",
        headers={"X-Workspace-Id": "org:secret"},
        json={"message": "hello", "stream": False},
    )

    assert response.status_code == 403
    assert "cannot write" in response.json()["detail"]


def test_network_intent_runs_shared_tool_policy_before_network_probe(tmp_path: Path, monkeypatch) -> None:
    calls = []

    def block_policy(tool_name, args, **kwargs):
        calls.append((tool_name, args, kwargs))
        raise HTTPException(status_code=403, detail="network inspection denied")

    def unexpected_probe():
        raise AssertionError("network_status executed before the policy gate")

    monkeypatch.setattr(chat_api, "enforce_tool_policy", block_policy)
    monkeypatch.setattr(chat_api, "network_status", unexpected_probe)

    response = TestClient(_app(tmp_path)).post(
        "/chat",
        json={"message": "네트워크 상태 확인", "stream": False},
    )

    assert response.status_code == 403
    assert calls == [(
        "network_status",
        {},
        {
            "current_user": "owner@example.com",
            "source": "chat_intent",
            "trusted_admin": False,
        },
    )]


def test_pending_agent_context_is_bound_to_its_authenticated_user(tmp_path: Path) -> None:
    class PausingRuntime:
        async def plan(self, ctx, req, _lang, _user, model_id=None):
            ctx.plan = {"goal": req.message, "steps": [], "requires_approval": True}
            ctx.transcript.append({"state": AgentState.PLANNING.value})
            ctx.state = AgentState.WAITING_APPROVAL

    app = _app(
        tmp_path,
        require_user=lambda request: request.headers.get("X-Test-User", ""),
        agent_runtime=PausingRuntime(),
    )
    client = TestClient(app)
    pending = client.post(
        "/agent",
        headers={"X-Test-User": "owner@example.com"},
        json={"message": "write a file", "human_in_loop": True},
    )
    context_id = pending.json()["context_id"]

    denied = client.post(
        "/agent/resume",
        headers={"X-Test-User": "other@example.com"},
        json={"context_id": context_id, "approved": False},
    )
    owner = client.post(
        "/agent/resume",
        headers={"X-Test-User": "owner@example.com"},
        json={"context_id": context_id, "approved": False},
    )

    assert denied.status_code == 403
    assert owner.status_code == 200
    assert owner.json()["status"] == "cancelled"


def test_agent_does_not_forge_human_approval_for_sensitive_plan(tmp_path: Path) -> None:
    approvals = []

    class SensitiveRuntime:
        async def plan(self, ctx, req, _lang, _user, model_id=None):
            ctx.plan = {
                "goal": req.message,
                "steps": [{"action": "run_command", "args": {"command": "ls"}}],
                "requires_approval": True,
            }
            ctx.state = AgentState.WAITING_APPROVAL

        def approve(self, ctx, _user, *, approved_by_human=False):
            approvals.append(approved_by_human)
            ctx.final_message = "approval required"
            ctx.state = AgentState.FAILED

        async def run_to_completion(self, *_args, **_kwargs):
            return None

        async def memory_update(self, *_args, **_kwargs):
            return None

    response = TestClient(_app(tmp_path, agent_runtime=SensitiveRuntime())).post(
        "/agent",
        json={"message": "run a command"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert approvals == [False]


def test_completed_agent_history_keeps_authenticated_user_and_request_scope(tmp_path: Path) -> None:
    history_entries = []
    run_records = []

    class CompletingRuntime:
        async def plan(self, ctx, req, _lang, _user, model_id=None):
            ctx.plan = {"goal": req.message, "steps": []}
            ctx.state = AgentState.PLANNING

        def approve(self, ctx, _user, *, approved_by_human=False):
            ctx.state = AgentState.EXECUTING

        async def run_to_completion(self, ctx, *_args, **_kwargs):
            ctx.final_message = "done"
            ctx.state = AgentState.DONE

        async def memory_update(self, *_args, **_kwargs):
            return None

    class ScopedWorkspace:
        def resolve_write_scope(self, requested, user):
            assert user == "owner@example.com"
            return requested

    class RecordingStore:
        def record_agent_run(self, **kwargs):
            run_records.append(kwargs)
            return {"id": "agent-run-test"}

    response = TestClient(
        _app(
            tmp_path,
            workspace_service=ScopedWorkspace(),
            workspace_store=RecordingStore(),
            agent_runtime=CompletingRuntime(),
            history_entries=history_entries,
        )
    ).post(
        "/agent",
        headers={"X-Workspace-Id": "org:one"},
        json={
            "message": "finish scoped work",
            "source": "web",
            "conversation_id": "conversation-one",
            "workspace_id": "org:one",
            "user_email": "OWNER@example.com",
            "user_nickname": "Owner",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert [entry[0][0] for entry in history_entries] == ["user", "assistant"]
    for _args, metadata in history_entries:
        assert metadata == {
            "user_email": "owner@example.com",
            "user_nickname": "Owner",
            "source": "web",
            "conversation_id": "conversation-one",
            "workspace_id": "org:one",
        }
    assert run_records[0]["user_email"] == "owner@example.com"
    assert run_records[0]["workspace_id"] == "org:one"
    assert run_records[0]["mode"] == "llm"


def test_recent_chat_context_filters_the_active_workspace():
    history = [
        {"role": "user", "content": "org one", "user_email": "alice@example.com", "workspace_id": "org-one"},
        {"role": "assistant", "content": "reply one", "workspace_id": "org-one"},
        {"role": "user", "content": "org two secret", "user_email": "alice@example.com", "workspace_id": "org-two"},
        {"role": "assistant", "content": "reply two secret", "workspace_id": "org-two"},
    ]

    context = build_recent_chat_context(
        get_history=lambda **_scope: history,
        user_email="alice@example.com",
        workspace_id="org-one",
    )

    assert "org one" in context
    assert "org two secret" not in context


def test_recent_chat_context_filters_other_users_in_same_conversation_and_workspace():
    history = [
        {
            "role": "user",
            "content": "alice context",
            "user_email": "alice@example.com",
            "workspace_id": "org-one",
            "conversation_id": "shared",
        },
        {
            "role": "assistant",
            "content": "alice reply",
            "workspace_id": "org-one",
            "conversation_id": "shared",
        },
        {
            "role": "user",
            "content": "bob secret",
            "user_email": "bob@example.com",
            "workspace_id": "org-one",
            "conversation_id": "shared",
        },
        {
            "role": "assistant",
            "content": "bob secret reply",
            "workspace_id": "org-one",
            "conversation_id": "shared",
        },
    ]

    context = build_recent_chat_context(
        get_history=lambda **_scope: history,
        user_email="alice@example.com",
        workspace_id="org-one",
        conversation_id="shared",
    )

    assert "alice context" in context
    assert "alice reply" in context
    assert "bob secret" not in context


def test_recent_chat_context_queries_strict_scope_and_skips_interleaved_assistant():
    calls = []
    history = [
        {
            "role": "user",
            "content": "alice context",
            "user_email": "alice@example.com",
            "workspace_id": "org-one",
            "conversation_id": "shared",
        },
        {
            "role": "assistant",
            "content": "bob interleaved secret",
            "user_email": "bob@example.com",
            "workspace_id": "org-one",
            "conversation_id": "shared",
        },
        {
            "role": "assistant",
            "content": "alice reply",
            "user_email": "alice@example.com",
            "workspace_id": "org-one",
            "conversation_id": "shared",
        },
    ]

    def get_history(**scope):
        calls.append(scope)
        return history

    context = build_recent_chat_context(
        get_history=get_history,
        user_email="alice@example.com",
        workspace_id="org-one",
        conversation_id="shared",
    )

    assert calls == [{
        "user_email": "alice@example.com",
        "allowed_workspaces": {"org-one"},
        "include_legacy_global": False,
    }]
    assert "alice context" in context
    assert "alice reply" in context
    assert "bob interleaved secret" not in context
