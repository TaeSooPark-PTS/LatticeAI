"""Coverage for security redaction, rate limits, lifespan, and filesystem extras."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from latticeai.core.security import (
    _is_secret_key,
    bytes_match_extension,
    check_ip_rate_limit,
    enforce_rate_limit,
    redact_secret_text,
    redact_secrets,
)
from latticeai.runtime.lifespan_runtime import build_lifespan_runtime
from latticeai.tools import ToolError
from latticeai.tools.filesystem import grep, inspect_html, preview_url, todo_read


def test_redact_and_magic_and_rate_limits():
    assert redact_secret_text("") == ""
    assert "REDACTED" in redact_secret_text("api_key=sk-abcdefghijklmnopqrstuv")
    assert "REDACTED" in redact_secret_text("sk-abcdefghijklmnopqrstuvwxyz1234")
    assert _is_secret_key("api_key") is True
    assert _is_secret_key("title") is False
    redacted = redact_secrets({
        "api_key": "secret",
        "ok": "visible",
        "nested": ["sk-abcdefghijklmnopqrstuvwxyz1234"],
        "pair": ("token-value",),
        "num": 1,
    })
    assert redacted["api_key"] == "[REDACTED_SECRET]"
    assert redacted["ok"] == "visible"
    assert redacted["num"] == 1

    assert bytes_match_extension(b"hello", ".txt") is True
    assert bytes_match_extension(b"\xff\xd8\xffabc", ".jpg") is True
    assert bytes_match_extension(b"nope", ".jpg") is False

    check_ip_rate_limit("1.2.3.4", "login", max_calls=2, window_secs=60)
    check_ip_rate_limit("1.2.3.4", "login", max_calls=2, window_secs=60)
    with pytest.raises(HTTPException):
        check_ip_rate_limit("1.2.3.4", "login", max_calls=2, window_secs=60)

    enforce_rate_limit("", "chat", enabled=True)
    enforce_rate_limit("a@b.c", "chat", enabled=False)
    enforce_rate_limit("a@b.c", "chat", enabled=True)
    enforce_rate_limit("a@b.c", "chat", enabled=True)


def test_lifespan_helpers_cover_skip_and_idle_paths():
    class Router:
        def __init__(self):
            self.loaded = []
            self.unloaded = False

        async def load_model(self, model_id, draft_model_id=None):
            self.loaded.append((model_id, draft_model_id))
            return f"loaded {model_id}"

        def unload_idle_models(self, seconds):
            return ["idle-model"] if seconds else []

        def unload_all(self):
            self.unloaded = True

    router = Router()
    runtime = build_lifespan_runtime(
        app_mode="full",
        autoload_models=False,
        is_public_mode=False,
        public_model="openai:gpt",
        allow_local_models=True,
        local_model="local",
        local_draft_model="",
        model_idle_unload_seconds=0,
        model_router=router,
        local_server_processes={},
        logger=SimpleNamespace(warning=lambda *a, **k: None),
    )
    asyncio.run(runtime["autoload_default_model"]())
    asyncio.run(runtime["unload_idle_models_loop"]())

    public = build_lifespan_runtime(
        app_mode="public",
        autoload_models=True,
        is_public_mode=True,
        public_model="openai:gpt",
        allow_local_models=False,
        local_model="local",
        local_draft_model="draft",
        model_idle_unload_seconds=0,
        model_router=router,
        local_server_processes={},
        logger=SimpleNamespace(warning=lambda *a, **k: None),
    )
    asyncio.run(public["autoload_default_model"]())

    blocked = build_lifespan_runtime(
        app_mode="full",
        autoload_models=True,
        is_public_mode=False,
        public_model="",
        allow_local_models=False,
        local_model="local",
        local_draft_model="",
        model_idle_unload_seconds=0,
        model_router=router,
        local_server_processes={},
        logger=SimpleNamespace(warning=lambda *a, **k: None),
    )
    asyncio.run(blocked["autoload_default_model"]())


def test_filesystem_grep_tree_todo_html(tmp_path: Path, monkeypatch):
    import latticeai.tools as tools

    root = tmp_path / "ws"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "note.md").write_text("alpha beta alpha\n", encoding="utf-8")
    (root / "todo.json").write_text("[]", encoding="utf-8")
    (root / "page.html").write_text("<html><body><h1>Hi</h1></body></html>", encoding="utf-8")
    monkeypatch.setattr(tools, "AGENT_ROOT", root)

    with pytest.raises(ToolError):
        grep("")
    with pytest.raises(ToolError):
        grep("[")
    hits = grep("alpha", path="sub", case_insensitive=True, context_lines=1)
    assert hits["matches"]
    todos = todo_read()
    assert todos is not None
    html = inspect_html("page.html")
    assert html
    preview = preview_url("page.html")
    assert preview is not None
