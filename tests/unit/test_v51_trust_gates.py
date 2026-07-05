"""v5.1 Product Trust & Clarity release gates."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from latticeai.api.chat import create_chat_router
from latticeai.api.static_routes import PRODUCTION_CSP, ui_file_response
from latticeai.core.audit import append_audit_event, get_audit_log
from latticeai.core.config import Config
from latticeai.core.logging_safety import safe_log_text
from latticeai.core.security import redact_secret_text, redact_secrets
from latticeai.services.app_context import AppContext

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_tauri_production_csp_is_not_null_or_open():
    conf = json.loads((REPO_ROOT / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))
    csp = conf["app"]["security"].get("csp")

    assert isinstance(csp, str) and csp.strip()
    assert "default-src 'self'" in csp
    assert "script-src 'self'" in csp
    assert "object-src 'none'" in csp
    assert "frame" in csp and "'none'" in csp
    assert "script-src *" not in csp
    assert "'unsafe-eval'" not in csp
    assert csp != "*"


def test_tauri_remote_localhost_keeps_desktop_ipc_available():
    capability = json.loads((REPO_ROOT / "src-tauri" / "capabilities" / "default.json").read_text(encoding="utf-8"))

    assert capability["windows"] == ["main"]
    assert "core:default" in capability["permissions"]
    assert set(capability["remote"]["urls"]) >= {"http://127.0.0.1:*", "http://localhost:*"}


def test_app_shell_response_sets_csp_header(tmp_path: Path):
    page = tmp_path / "index.html"
    page.write_text("<html><body>Lattice</body></html>", encoding="utf-8")

    response = ui_file_response(page)

    assert response.headers["Content-Security-Policy"] == PRODUCTION_CSP
    assert response.headers["Cache-Control"] == "no-cache, no-store, must-revalidate"


def test_secret_redaction_is_shared_across_text_values_logs_and_audit(tmp_path: Path):
    secret = "OPENAI_API_KEY=sk-1234567890abcdefghij1234567890"
    dsn = "postgresql://alice:supersecret@localhost/lattice"
    nested = {"token": "ghp_abcdefghijklmnopqrstuvwxyz12345678", "note": dsn}

    assert "sk-1234567890" not in redact_secret_text(secret)
    assert "supersecret" not in redact_secret_text(dsn)
    assert redact_secrets(nested)["token"] == "[REDACTED_SECRET]"
    assert "supersecret" not in safe_log_text(dsn)

    audit_file = tmp_path / "audit.json"
    append_audit_event(audit_file, "secret_probe", message=secret, nested=nested, dsn=dsn)
    event = get_audit_log(audit_file)[0]
    assert event["contract"]["family"] == "agent-run-contract/v1"
    assert event["contract"]["kind"] == "audit_event"
    dumped = json.dumps(get_audit_log(audit_file), ensure_ascii=False)
    assert "sk-1234567890" not in dumped
    assert "ghp_abcdef" not in dumped
    assert "supersecret" not in dumped
    assert "[REDACTED_SECRET]" in dumped


def _chat_app_for_auto_read(tmp_path: Path, captured: dict, audit_events: list) -> FastAPI:
    app = FastAPI()

    async def generate(_message, context, *_args, **_kwargs):
        captured["context"] = context
        return "honest answer"

    context = AppContext(
        config=SimpleNamespace(is_public=False, auto_read_chat_paths=True),
        model_router=SimpleNamespace(
            current_model_id="local:test",
            loaded_model_ids=["local:test"],
            generate=generate,
            generate_as=lambda *_args, **_kwargs: "",
        ),
        chat_service=SimpleNamespace(
            build_graph_trace=lambda *_args, **_kwargs: {},
            record_trace=lambda **_kwargs: {"id": "trace-v51", "trace": _kwargs.get("trace") or {}},
        ),
        workspace_store=SimpleNamespace(),
        workspace_graph=lambda: None,
        gardener=SimpleNamespace(get_relevant_context=lambda _query: ""),
        require_user=lambda _request: "user@example.com",
        enforce_rate_limit=lambda *_args, **_kwargs: None,
        get_history_user=lambda *_args, **_kwargs: {},
        save_to_history=lambda *_args, **_kwargs: None,
        append_audit_event=lambda event_type, **payload: audit_events.append((event_type, payload)),
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
    )
    app.include_router(create_chat_router(context))
    return app


def test_auto_file_read_is_off_by_default_and_never_reads_without_approval(tmp_path: Path):
    assert Config.from_env({}).auto_read_chat_paths is False

    secret_file = tmp_path / "secret.txt"
    secret_file.write_text("OPENAI_API_KEY=sk-should-not-enter-context", encoding="utf-8")
    captured: dict = {}
    audit_events: list = []
    client = TestClient(_chat_app_for_auto_read(tmp_path, captured, audit_events))

    response = client.post(
        "/chat",
        json={"message": f"Please use {secret_file}", "stream": False},
    )

    assert response.status_code == 200
    assert "sk-should-not-enter-context" not in captured["context"]
    assert audit_events[0][0] == "auto_file_context_blocked"


def test_auto_file_read_explicit_request_fails_closed(tmp_path: Path):
    secret_file = tmp_path / "secret.txt"
    secret_file.write_text("secret", encoding="utf-8")
    client = TestClient(_chat_app_for_auto_read(tmp_path, {}, []))

    response = client.post(
        "/chat",
        json={"message": f"Please use {secret_file}", "stream": False, "allow_file_context": True},
    )

    assert response.status_code == 400
    assert "Automatic local file reads are disabled" in response.json()["detail"]


def test_network_exposed_mode_requires_auth_and_closes_registration():
    cfg = Config.from_env({"LATTICEAI_HOST": "0.0.0.0"})

    assert cfg.network_exposed is True
    assert cfg.require_auth is True
    assert cfg.open_registration is False


def test_production_python_paths_do_not_use_shell_true():
    roots = [REPO_ROOT / "latticeai", REPO_ROOT / "lattice_brain", REPO_ROOT / "tools"]
    offenders: list[str] = []
    for root in roots:
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    fn = node.func
                    is_subprocess_call = (
                        isinstance(fn, ast.Attribute)
                        and fn.attr in {"run", "Popen", "check_call", "check_output"}
                        and isinstance(fn.value, ast.Name)
                        and fn.value.id == "subprocess"
                    )
                    if not is_subprocess_call:
                        continue
                    for keyword in node.keywords:
                        if keyword.arg == "shell" and isinstance(keyword.value, ast.Constant) and keyword.value.value is True:
                            offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")

    assert offenders == []


def test_brain_core_package_never_imports_latticeai():
    offenders: list[str] = []
    for path in (REPO_ROOT / "lattice_brain").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "latticeai" or alias.name.startswith("latticeai."):
                        offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
            elif isinstance(node, ast.ImportFrom) and (node.module == "latticeai" or str(node.module).startswith("latticeai.")):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")

    assert offenders == []
