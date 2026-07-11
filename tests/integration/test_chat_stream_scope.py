"""In-process chat integration coverage for auth, streaming, and scope.

The live-server integration suite cannot load a real model in CI.  These tests
therefore assemble the production chat router with a deterministic injected
model while retaining the real workspace permission service, SSE finalization,
history persistence, and request authentication boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from latticeai.api.chat import create_chat_router
from latticeai.core.workspace_os import WorkspaceOSStore
from latticeai.services.app_context import AppContext
from latticeai.services.workspace_service import WorkspaceService


ALICE = "alice@example.com"
BOB = "bob@example.com"
CONVERSATION_ID = "shared-conversation"


class DeterministicStreamRouter:
    current_model_id = "integration-model"
    loaded_model_ids = ["integration-model"]

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def stream_generate_as(
        self,
        model_id,
        message,
        context,
        max_tokens,
        temperature,
        image_data,
    ):
        self.calls.append(
            {
                "model_id": model_id,
                "message": message,
                "context": context,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "image_data": image_data,
            }
        )
        yield "deterministic "
        yield "answer"


class TraceRecorder:
    def __init__(self) -> None:
        self.built: list[dict[str, Any]] = []
        self.recorded: list[dict[str, Any]] = []

    def build_graph_trace(
        self,
        question,
        graph,
        context,
        *,
        limit=8,
        allowed_workspaces=None,
    ):
        observed = {
            "question": question,
            "graph": graph,
            "context": context,
            "limit": limit,
            "allowed_workspaces": allowed_workspaces,
        }
        self.built.append(observed)
        # Production traces contain only JSON-safe retrieval metadata. Retain
        # the raw set in ``built`` for the scope assertion while mirroring the
        # serialized shape that is persisted and emitted in the SSE trailer.
        return {
            **observed,
            "allowed_workspaces": sorted(allowed_workspaces or []),
        }

    def record_trace(self, **payload):
        self.recorded.append(payload)
        return {"id": "trace-integration", **payload}


@dataclass
class ChatHarness:
    client: TestClient
    router: DeterministicStreamRouter
    history: list[dict[str, Any]]
    traces: TraceRecorder
    alice_workspace: str
    bob_workspace: str


def _build_harness(tmp_path: Path) -> ChatHarness:
    workspace_store = WorkspaceOSStore(tmp_path / "workspace-state")
    workspace_service = WorkspaceService(workspace_store)
    alice_workspace = workspace_service.create_organization_workspace(
        name="Alice integration",
        owner_user_id=ALICE,
    )["workspace_id"]
    bob_workspace = workspace_service.create_organization_workspace(
        name="Bob integration",
        owner_user_id=BOB,
    )["workspace_id"]

    history: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": "alice visible context",
            "user_email": ALICE,
            "workspace_id": alice_workspace,
            "conversation_id": CONVERSATION_ID,
        },
        {
            "role": "assistant",
            "content": "alice visible reply",
            "workspace_id": alice_workspace,
            "conversation_id": CONVERSATION_ID,
        },
        {
            "role": "user",
            "content": "bob same-workspace secret",
            "user_email": BOB,
            "workspace_id": alice_workspace,
            "conversation_id": CONVERSATION_ID,
        },
        {
            "role": "assistant",
            "content": "bob same-workspace secret reply",
            "workspace_id": alice_workspace,
            "conversation_id": CONVERSATION_ID,
        },
        {
            "role": "user",
            "content": "alice other-workspace secret",
            "user_email": ALICE,
            "workspace_id": bob_workspace,
            "conversation_id": CONVERSATION_ID,
        },
        {
            "role": "assistant",
            "content": "alice other-workspace secret reply",
            "workspace_id": bob_workspace,
            "conversation_id": CONVERSATION_ID,
        },
    ]
    model_router = DeterministicStreamRouter()
    traces = TraceRecorder()

    def require_user(request: Request) -> str:
        user = request.headers.get("X-Test-User", "").strip()
        if not user:
            raise HTTPException(status_code=401, detail="authentication required")
        return user

    def save_to_history(role: str, content: str, **metadata: Any) -> None:
        history.append({"role": role, "content": content, **metadata})

    context = AppContext(
        config=SimpleNamespace(
            is_public=False,
            auto_read_chat_paths=False,
            require_auth=True,
        ),
        model_router=model_router,
        workspace_store=workspace_store,
        workspace_service=workspace_service,
        chat_service=traces,
        chat_agent_runtime=object(),
        require_user=require_user,
        enforce_rate_limit=lambda *_args, **_kwargs: None,
        allowed_workspaces_for=lambda user: set(
            workspace_service.readable_workspaces(user)
        ),
        get_history=lambda **_scope: list(history),
        get_history_user=lambda email, nickname: {
            "user_email": email,
            "user_nickname": nickname,
        },
        save_to_history=save_to_history,
        clear_history=lambda *_args, **_kwargs: {"removed": 0, "kept": 0},
        clear_conversation=lambda *_args, **_kwargs: {"removed": 0, "kept": 0},
        group_history_conversations=lambda *_args, **_kwargs: [],
        get_conversation_messages=lambda *_args, **_kwargs: [],
        conversation_title=lambda *_args, **_kwargs: "Conversation",
        append_audit_event=lambda *_args, **_kwargs: None,
        enable_graph=False,
        knowledge_graph=None,
        workspace_graph=lambda: None,
        public_model="",
        base_dir=tmp_path,
    )
    app = FastAPI()
    app.include_router(create_chat_router(context))
    return ChatHarness(
        client=TestClient(app),
        router=model_router,
        history=history,
        traces=traces,
        alice_workspace=alice_workspace,
        bob_workspace=bob_workspace,
    )


def test_authenticated_chat_stream_is_user_and_workspace_scoped(tmp_path: Path) -> None:
    harness = _build_harness(tmp_path)

    response = harness.client.post(
        "/chat",
        headers={
            "X-Test-User": ALICE,
            "X-Workspace-Id": harness.alice_workspace,
        },
        json={
            "message": "continue the visible project",
            "conversation_id": CONVERSATION_ID,
            "stream": True,
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["x-model"] == "integration-model"
    assert '"chunk": "deterministic "' in response.text
    assert '"chunk": "answer"' in response.text
    assert "data: [DONE]" in response.text

    assert len(harness.router.calls) == 1
    model_context = harness.router.calls[0]["context"]
    assert "alice visible context" in model_context
    assert "alice visible reply" in model_context
    assert "bob same-workspace secret" not in model_context
    assert "alice other-workspace secret" not in model_context

    persisted = harness.history[-2:]
    assert [(item["role"], item["content"]) for item in persisted] == [
        ("user", "continue the visible project"),
        ("assistant", "deterministic answer"),
    ]
    assert all(item["user_email"] == ALICE for item in persisted)
    assert all(item["workspace_id"] == harness.alice_workspace for item in persisted)
    assert all(item["conversation_id"] == CONVERSATION_ID for item in persisted)
    assert harness.traces.built[0]["allowed_workspaces"] == {
        harness.alice_workspace
    }
    assert harness.traces.recorded[0]["user_email"] == ALICE
    assert harness.traces.recorded[0]["workspace_id"] == harness.alice_workspace


def test_chat_stream_rejects_missing_auth_and_cross_workspace_write(tmp_path: Path) -> None:
    harness = _build_harness(tmp_path)
    initial_history_size = len(harness.history)

    missing_auth = harness.client.post(
        "/chat",
        headers={"X-Workspace-Id": harness.alice_workspace},
        json={"message": "unauthenticated", "stream": True},
    )
    cross_workspace = harness.client.post(
        "/chat",
        headers={
            "X-Test-User": ALICE,
            "X-Workspace-Id": harness.bob_workspace,
        },
        json={"message": "cross workspace", "stream": True},
    )

    assert missing_auth.status_code == 401
    assert cross_workspace.status_code == 403
    assert "lacks 'write'" in cross_workspace.json()["detail"]
    assert harness.router.calls == []
    assert len(harness.history) == initial_history_size
    assert harness.traces.built == []
    assert harness.traces.recorded == []
