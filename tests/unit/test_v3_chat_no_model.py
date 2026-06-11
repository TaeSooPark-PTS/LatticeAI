from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from latticeai.api.chat import create_chat_router
from latticeai.services.app_context import AppContext


def test_chat_returns_clean_json_error_when_no_model_loaded(tmp_path: Path):
    app = FastAPI()
    context = AppContext(
        config=SimpleNamespace(is_public=False),
        model_router=SimpleNamespace(
            current_model_id=None,
            loaded_model_ids=[],
            generate_as=lambda *_args, **_kwargs: "",
            generate=lambda *_args, **_kwargs: "",
        ),
        chat_service=SimpleNamespace(),
        workspace_store=SimpleNamespace(),
        workspace_graph=lambda: None,
        gardener=SimpleNamespace(get_relevant_context=lambda _query: ""),
        require_user=lambda _request: "user@example.com",
        enforce_rate_limit=lambda *_args, **_kwargs: None,
        get_history_user=lambda *_args, **_kwargs: {},
        save_to_history=lambda *_args, **_kwargs: None,
        append_audit_event=lambda *_args, **_kwargs: None,
        clear_history=lambda *_args, **_kwargs: {"removed": 0, "kept": 0},
        clear_conversation=lambda *_args, **_kwargs: {"removed": 0, "kept": 0},
        get_history=lambda: [],
        group_history_conversations=lambda *_args, **_kwargs: [],
        get_conversation_messages=lambda *_args, **_kwargs: [],
        conversation_title=lambda *_args, **_kwargs: "Conversation",
        load_users=lambda: {},
        get_user_role=lambda *_args, **_kwargs: "user",
        enable_graph=False,
        knowledge_graph=None,
        public_model="",
        base_dir=tmp_path,
    )
    app.include_router(create_chat_router(context))

    response = TestClient(app).post("/chat", json={"message": "hello", "stream": False})

    assert response.status_code == 400
    payload = response.json()
    assert payload["error"] == "no_model_loaded"
    assert payload["action"] == "load_model"
    assert "No model loaded" in payload["detail"]
