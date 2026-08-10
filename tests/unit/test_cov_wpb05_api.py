"""wpb05 — HTTP surfaces: the request shape that had never been served.

Each router is built through its own factory with injected fakes (the
``tests/unit/test_auth_router.py`` idiom) and driven with ``TestClient``, so
what is asserted is the response a browser would receive. The arcs closed here
are the ones a "happy" fixture never produces: an anonymous caller, a history
row with no ``content`` key, an engine listing carrying a bare string, a
zero-second run-now wait.
"""

from __future__ import annotations

import base64
import io
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from latticeai.api import chat_documents
from latticeai.api.automation_intelligence import (
    create_automation_intelligence_router,
)
from latticeai.api.chat_agent_http import AgentHTTPController
from latticeai.api.models import create_models_router
from latticeai.api.security_dashboard import create_security_router
from latticeai.api.tools import create_tools_router
from latticeai.services.automation_intelligence import AutomationIntelligenceService
from latticeai.services.brain_automation import build_brain_automation_workflow

# ── automation_intelligence: run-now with a zero-second wait ─────────────────


class _AutomationStore:
    def __init__(self, workflow: Dict[str, Any]) -> None:
        self.workflows = {workflow["id"]: workflow}
        self.metadata_updates: List[Dict[str, Any]] = []

    def get_workflow(self, workflow_id: str, workspace_id: Any = None) -> Dict[str, Any]:
        if workflow_id not in self.workflows:
            raise FileNotFoundError(workflow_id)
        return self.workflows[workflow_id]

    def list_workflows(self, query: str = "", workspace_id: Any = None) -> Dict[str, Any]:
        return {"workflows": list(self.workflows.values())}

    def update_workflow_definition(self, workflow_id, *, name=None, nodes=None,
                                   metadata=None, workspace_id=None):
        workflow = self.get_workflow(workflow_id)
        if metadata:
            workflow["metadata"] = {**(workflow.get("metadata") or {}), **metadata}
        self.metadata_updates.append({"workflow_id": workflow_id, "metadata": metadata})
        return workflow

    def get_workflow_run(self, run_id: str, workspace_id: Any = None) -> Dict[str, Any]:
        raise AssertionError("a zero-second wait must never poll the run row")


class _StartOnlyExecutor:
    async def start_workflow(self, workflow, *, workflow_id, user_email, scope, inputs=None):
        return {
            "run": {"id": "run-1", "workflow_id": workflow_id, "status": "running"},
            "accepted": True,
        }


def test_run_now_answers_immediately_when_the_wait_budget_is_zero(monkeypatch):
    monkeypatch.setenv("LATTICEAI_AUTOMATION_RUN_NOW_WAIT", "0")
    workflow = build_brain_automation_workflow("daily-memory-digest", enabled=True)
    workflow["id"] = "wf-auto-1"
    store = _AutomationStore(workflow)
    app = FastAPI()
    app.include_router(
        create_automation_intelligence_router(
            service=AutomationIntelligenceService(store=store),
            store=store,
            require_user=lambda request: "wpb05@example.com",
            gate_read=lambda request: None,
            gate_write=lambda request: None,
            append_audit_event=lambda event, **kw: None,
            workspace_graph=lambda: None,
            run_executor=_StartOnlyExecutor(),
        )
    )

    response = TestClient(app).post(
        "/api/automation/run-now", json={"workflow_id": "wf-auto-1", "dry_run": False},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "running"
    assert body["run_id"] == "run-1"
    assert body["last_execution"]["summary"] == "started — still running in the background"
    assert store.metadata_updates, "the run is still stamped on the workflow"


# ── chat_agent_http: an approval sweep that keeps a live entry ───────────────


class _RunStore:
    def __init__(self) -> None:
        self.deleted: List[str] = []

    def sweep_expired(self) -> None:
        return None

    def delete(self, run_id: str) -> None:
        self.deleted.append(run_id)


def test_the_approval_sweep_keeps_a_run_whose_token_has_not_expired(tmp_path: Path):
    run_store = _RunStore()
    controller = AgentHTTPController(
        runtime=SimpleNamespace(),
        model_router=SimpleNamespace(current_model_id="local-test"),
        require_user=lambda request: "wpb05@example.com",
        require_admin=None,
        enforce_rate_limit=lambda *a, **k: None,
        authenticated_identity=lambda current, claimed, language="ko": current,
        write_workspace=lambda requested, user: requested,
        save_to_history=lambda *a, **k: None,
        workspace_store=SimpleNamespace(record_agent_run=lambda **kw: {"id": "r"}),
        workspace_graph=lambda: None,
        hooks=None,
        execute_tool=lambda name, args: {"success": True},
        base_dir=tmp_path,
        agent_root=tmp_path,
        ensure_agent_root=lambda: None,
        run_store=run_store,
    )
    controller._approvals = {
        "run-stale": {"expires_monotonic": 100.0},
        "run-live": {"expires_monotonic": 900.0},
    }

    controller._purge_expired_approvals_locked(500.0)

    assert list(controller._approvals) == ["run-live"]
    assert run_store.deleted == ["run-stale"]


# ── chat_documents: OCR that never produced a temp file ──────────────────────


def _png_base64() -> str:
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), (10, 20, 30)).save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def test_screenshot_ocr_reports_the_failure_when_no_scratch_file_could_be_made(monkeypatch):
    """Nothing was written, so the cleanup step has nothing to remove."""
    def _explode(*_args: Any, **_kwargs: Any):
        raise OSError("no space left on device")

    monkeypatch.setattr(
        chat_documents, "shutil", SimpleNamespace(which=lambda name: "/usr/bin/tesseract")
    )
    monkeypatch.setattr(
        chat_documents, "tempfile", SimpleNamespace(NamedTemporaryFile=_explode)
    )
    monkeypatch.setattr(
        chat_documents,
        "subprocess",
        SimpleNamespace(run=lambda *a, **k: pytest.fail("tesseract must not run")),
    )

    context = chat_documents.extract_screenshot_context(_png_base64())

    lines = context.splitlines()
    assert lines[0] == "[SCREENSHOT INGESTION]"
    assert lines[1] == "- image_size: 4x4"
    assert lines[-1] == "- ocr_error: no space left on device"


# ── api/models: an engine listing carrying a bare string ─────────────────────


class _ModelRouter:
    loaded_model_ids: List[str] = []
    current_model_id = None

    def detected_cloud_models(self):
        return []


def test_engine_models_that_are_not_objects_are_skipped_by_the_recommender():
    engines = [
        {
            "id": "ollama",
            "name": "Ollama",
            "installed": True,
            "models": ["bare-string-entry", {"id": "gemma3:4b", "size": 1}],
        }
    ]
    app = FastAPI()
    app.include_router(
        create_models_router(
            model_router=_ModelRouter(),
            require_user=lambda _request: "wpb05@example.com",
            require_admin=lambda _request: ("wpb05@example.com", {}),
            get_current_user=lambda _request: "wpb05@example.com",
            load_users=lambda: {"wpb05@example.com": {"role": "admin"}},
            get_user_role=lambda *_a, **_kw: "admin",
            install_engine=lambda engine, **kw: {"installed": engine},
            verify_cloud_models=lambda **kw: [],
            normalize_local_model_request=lambda model, _engine=None: str(model or ""),
            download_hf_model=lambda model, provider: {},
            prepare_and_load_model=lambda *a, **k: {},
            prepare_and_load_model_stream=lambda *a, **k: None,
            sse_event=lambda event, data: "",
            ensure_ollama_server=lambda: None,
            local_binary=lambda name: None,
            engine_status=lambda: engines,
            filter_lower_family_versions=lambda items: items,
            list_compat_profiles=lambda: [],
            set_user_api_key=lambda *args, **kw: None,
            engine_model_catalog={
                "local_mlx": [
                    {"id": "gemma3", "name": "Gemma 3", "tag": "gemma3:4b", "size": "3.3GB"}
                ]
            },
            model_engine_aliases={"gemma3": {"ollama": "gemma3:4b"}},
            cloud_verify_ttl_seconds=600,
            is_public_mode=False,
            allow_local_models=True,
            require_auth=True,
        )
    )

    response = TestClient(app, raise_server_exceptions=False).get("/models")

    assert response.status_code == 200
    options = response.json()["recommended"][0]["engine_options"]
    ollama = [row for row in options if row["engine"] == "ollama"]
    assert ollama and ollama[0]["load_id"] == "ollama:gemma3:4b"
    assert ollama[0]["installed"] is True, "the dict entry still resolved its engine"


# ── security_dashboard: a history row with no content key ────────────────────


def test_raw_conversation_view_passes_through_a_row_that_has_no_content():
    history = [
        {"conversation_id": "c-1", "role": "system", "event": "conversation_started"},
        {"conversation_id": "c-1", "role": "user", "content": "키는 sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"},
    ]
    app = FastAPI()
    app.include_router(
        create_security_router(
            require_admin=lambda _request: ("admin@example.com", {}),
            get_history=lambda: history,
            get_audit_events=lambda: [],
            classify_sensitive_message=lambda item, index: {"index": index},
            build_sensitivity_report=lambda rows: {"summary": {}},
        )
    )

    messages = TestClient(app).get("/admin/security/conversations/c-1/raw").json()["messages"]

    assert messages[0] == history[0], "a row without content is returned untouched"
    assert "sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ012345" not in messages[1]["content"]


# ── api/tools: an anonymous caller skips the per-user policy gate ────────────


def test_an_unidentified_caller_still_runs_a_tool_through_the_hook_lifecycle(
    monkeypatch, tmp_path: Path
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "note.md").write_text("hi", encoding="utf-8")
    for module in ("latticeai.api.tools", "latticeai.tools", "latticeai.services.tool_dispatch"):
        monkeypatch.setattr(module + ".AGENT_ROOT", workspace)
    monkeypatch.setattr(
        "latticeai.api.tools.enforce_tool_policy",
        lambda *a, **k: pytest.fail("an anonymous call has no user policy to enforce"),
    )

    app = FastAPI()
    app.include_router(
        create_tools_router(
            config=SimpleNamespace(
                require_auth=False,
                discord_permission_webhook="",
                discord_bot_token="",
                discord_permission_channel="",
                permission_monitor_secret="",
                port=4825,
            ),
            data_dir=tmp_path / "data",
            static_dir=tmp_path / "static",
            model_router=SimpleNamespace(current_model_id=None),
            require_user=lambda _request: None,
            require_admin=lambda _request: (None, {}),
            get_current_user=lambda _request: None,
            clear_history=lambda keep_last, **scope: {"removed": 0, "kept": 0},
            append_audit_event=lambda event, **payload: None,
            enforce_rate_limit=lambda *a, **k: None,
            bytes_match_extension=lambda *a, **k: True,
            classify_sensitive_message=lambda *a, **k: None,
            save_to_history=lambda *a, **k: None,
            enable_graph=False,
            knowledge_graph=None,
            require_graph=lambda: None,
            local_kg_watcher=None,
            load_mcp_installs=lambda: {"installed": {}},
            recommend_mcps=lambda *a, **k: [],
            install_mcp=lambda *a, **k: {"ok": True},
            mcp_public_item=lambda item, _installs: dict(item),
            hooks=None,
        )
    )

    response = TestClient(app, raise_server_exceptions=False).post(
        "/tools/list_dir", json={"path": "."},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["workspace"] == str(workspace)
    assert any(entry["name"] == "note.md" for entry in body["result"]["items"])
