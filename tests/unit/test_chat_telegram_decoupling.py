"""T2: telegram is decoupled from the chat router.

``latticeai.api.chat`` used to do ``from telegram_bot import broadcast_web_chat``
at module scope, loading the 45KB telegram integration (which mutates
``os.environ`` at import) on every server start. The seam is now an injectable
``on_chat_message`` callback on the AppContext, registered by ``create_app``
only when ENABLE_TELEGRAM is truthy.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from latticeai.api.chat import create_chat_router
from latticeai.services.app_context import AppContext

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_importing_chat_router_does_not_import_telegram_bot():
    code = (
        "import json, sys\n"
        "import latticeai.api.chat\n"
        "print(json.dumps('telegram_bot' in sys.modules))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout.splitlines()[-1]) is False, (
        "importing latticeai.api.chat pulled in telegram_bot"
    )


def _chat_app(tmp_path: Path, recorded: list, *, on_chat_message=None) -> FastAPI:
    app = FastAPI()
    context = AppContext(
        config=SimpleNamespace(is_public=False, auto_read_chat_paths=False),
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
        enable_graph=False,
        knowledge_graph=None,
        public_model="",
        base_dir=tmp_path,
        on_chat_message=on_chat_message,
    )
    app.include_router(create_chat_router(context))
    return app


def test_on_chat_message_callback_fires_for_web_chat(tmp_path: Path):
    recorded = []
    app = _chat_app(
        tmp_path,
        recorded,
        on_chat_message=lambda role, text, source: recorded.append((role, text, source)),
    )

    # The current-URL fast path answers without a loaded model.
    response = TestClient(app).post(
        "/chat",
        json={
            "message": "현재 페이지 URL 알려줘",
            "client_url": "http://localhost:4825/app",
            "stream": False,
            "source": "web",
        },
    )

    assert response.status_code == 200
    roles = [item[0] for item in recorded]
    assert roles == ["user", "assistant"], f"bridge calls: {recorded}"
    assert recorded[1][1].endswith("http://localhost:4825/app")
    assert all(item[2] == "web" for item in recorded)


def test_telegram_originated_messages_are_not_echoed_back(tmp_path: Path):
    recorded = []
    app = _chat_app(
        tmp_path,
        recorded,
        on_chat_message=lambda role, text, source: recorded.append((role, text, source)),
    )

    response = TestClient(app).post(
        "/chat",
        json={
            "message": "현재 페이지 URL 알려줘",
            "client_url": "http://localhost:4825/app",
            "stream": False,
            "source": "telegram",
        },
    )

    assert response.status_code == 200
    assert recorded == [], "telegram-originated exchange must not echo to the bridge"


def test_no_bridge_registered_is_a_noop(tmp_path: Path):
    app = _chat_app(tmp_path, [], on_chat_message=None)

    response = TestClient(app).post(
        "/chat",
        json={
            "message": "현재 페이지 URL 알려줘",
            "client_url": "http://localhost:4825/app",
            "stream": False,
            "source": "web",
        },
    )

    assert response.status_code == 200
