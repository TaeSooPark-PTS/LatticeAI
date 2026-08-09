"""wp17 (second pass) — the setup wizard router driven through its factory.

The router is the seam between the setup UI and three collaborators that touch
the host: the environment scan, the zero-config auto-setup pipeline, and the
installer stream. Those are injected as fakes here (patched on the router
module, which is where the names are bound), so the tests assert what the
router itself does: which model it promotes to "primary", the install command
it derives for each runtime family, the confirmation token it attaches, how it
merges the zero-config block into the scan payload, and its localized refusals.

The demo-corpus endpoints run against a recording ingestion pipeline so the
partial/failed accounting and the workspace-scope resolution are observable.
"""

from __future__ import annotations

import json
import types
from typing import Any, Dict, List, Optional

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from latticeai.api import setup as api_setup
from latticeai.api.setup import create_setup_router
from latticeai.services.process_audit import confirmation_token
from latticeai.setup.demo_corpus import (
    DEMO_DOCUMENTS,
    DEMO_METADATA_FLAG,
    DEMO_URI_PREFIX,
)

USER = "user@example.com"

RAM_RATIONALE = "RAM 32768 MB → 7B 급 모델"
CHIP_RATIONALE = "Apple Silicon → Metal + MLX-VLM"


# ── auto-setup stage fakes ─────────────────────────────────────────────────

class _Profile:
    def to_json(self) -> Dict[str, Any]:
        return {"os": "darwin", "arch": "arm64", "ram_mb": 32768}


class _Recommendation:
    def to_json(self) -> Dict[str, Any]:
        return {
            "runtime": "llama.cpp",
            "quant": "q4_k_m",
            "rationale": [RAM_RATIONALE, CHIP_RATIONALE],
        }


class _InstallPlan:
    def to_json(self) -> Dict[str, Any]:
        return {"steps": [{"name": "node20"}], "notes": []}


def _patch_auto_setup(monkeypatch) -> Dict[str, Any]:
    profile, recommendation, install_plan = _Profile(), _Recommendation(), _InstallPlan()
    seen: Dict[str, Any] = {"profile": profile, "recommendation": recommendation}

    def _recommend(prof):
        seen["recommend_arg"] = prof
        return recommendation

    def _plan(prof, rec):
        seen["plan_args"] = (prof, rec)
        return install_plan

    def _verify(prof, rec):
        seen["verify_args"] = (prof, rec)
        return {"ok": True, "checks": [{"label": "Python 3.11+", "ok": True}]}

    def _preset(prof, rec):
        seen["preset_args"] = (prof, rec)
        return {"mode": "advanced", "theme": "ink", "model": {"id": "placeholder"}}

    monkeypatch.setattr(api_setup, "auto_setup_probe", lambda: profile)
    monkeypatch.setattr(api_setup, "auto_setup_recommend", _recommend)
    monkeypatch.setattr(api_setup, "auto_setup_plan", _plan)
    monkeypatch.setattr(api_setup, "auto_setup_verify", _verify)
    monkeypatch.setattr(api_setup, "auto_setup_preset", _preset)
    return seen


def _client(**kwargs) -> TestClient:
    app = FastAPI()
    app.include_router(
        create_setup_router(
            model_router=kwargs.pop("model_router", None),
            require_user=kwargs.pop("require_user", lambda request: USER),
            **kwargs,
        )
    )
    return TestClient(app)


def _scan_client(monkeypatch, *, models) -> Dict[str, Any]:
    seen = _patch_auto_setup(monkeypatch)
    environment = {"os": "darwin", "ram_gb": 32, "tools": {"ollama": True}}
    recommendations: Dict[str, Any] = {"models": models, "engines": []}

    def _get_recommendations(env):
        seen["recs_env"] = env
        return recommendations

    monkeypatch.setattr(api_setup, "scan_environment", lambda: environment)
    monkeypatch.setattr(api_setup, "get_recommendations", _get_recommendations)
    seen["environment"] = environment
    seen["body"] = _client().get("/setup/scan").json()
    return seen


def _sse_events(text: str) -> List[Dict[str, Any]]:
    return [
        json.loads(line[len("data: "):])
        for line in text.splitlines()
        if line.startswith("data: ")
    ]


# ── /setup/auto ────────────────────────────────────────────────────────────

def test_setup_auto_chains_probe_recommend_plan_verify_preset(monkeypatch):
    seen = _patch_auto_setup(monkeypatch)

    body = _client().get("/setup/auto").json()

    assert body["probe"] == {"os": "darwin", "arch": "arm64", "ram_mb": 32768}
    assert body["recommend"]["runtime"] == "llama.cpp"
    assert body["plan"] == {"steps": [{"name": "node20"}], "notes": []}
    assert body["verify"]["ok"] is True
    assert body["preset"]["mode"] == "advanced"
    # Every later stage sees the *same* probe/recommendation, not a re-probe.
    assert seen["recommend_arg"] is seen["profile"]
    assert seen["plan_args"] == (seen["profile"], seen["recommendation"])
    assert seen["verify_args"] == (seen["profile"], seen["recommendation"])
    assert seen["preset_args"] == (seen["profile"], seen["recommendation"])


def test_setup_auto_is_behind_the_user_gate(monkeypatch):
    _patch_auto_setup(monkeypatch)

    def _deny(request):
        raise HTTPException(status_code=401, detail="인증이 필요합니다.")

    assert _client(require_user=_deny).get("/setup/auto").status_code == 401


# ── /setup/scan ────────────────────────────────────────────────────────────

def test_scan_promotes_the_checked_ollama_model_to_a_pull_step(monkeypatch):
    seen = _scan_client(monkeypatch, models=[
        {"model_id": "ollama:llama3.2:3b", "disabled": True},
        "not-a-dict",
        {"name": "no identifier at all"},
        {"model_id": "ollama:qwen3:4b", "checked": True},
    ])
    body = seen["body"]
    zero_config = body["zero_config"]

    assert seen["recs_env"] is seen["environment"]
    assert zero_config["recommend"]["model_id"] == "ollama:qwen3:4b"
    assert zero_config["recommend"]["runtime"] == "ollama"
    # The stale RAM-derived rationale is replaced by the real, loadable model.
    assert RAM_RATIONALE not in zero_config["recommend"]["rationale"]
    assert zero_config["recommend"]["rationale"] == [
        CHIP_RATIONALE,
        "실제 다운로드 및 로드 가능한 ollama 모델 → ollama:qwen3:4b",
    ]

    step = zero_config["plan"]["steps"][0]
    assert step["command"] == ["ollama", "pull", "qwen3:4b"]
    assert step["name"] == "weights:ollama:qwen3:4b"
    assert step["requires_admin"] is False
    assert step["command_plan"]["metadata"] == {"model_id": "ollama:qwen3:4b"}
    assert step["confirmation_token"] == confirmation_token(
        ["ollama", "pull", "qwen3:4b"], purpose="auto_setup_install"
    )

    assert zero_config["preset"]["model"] == {"id": "ollama:qwen3:4b", "runtime": "ollama"}
    # The same block is mirrored into every consumer shape the UI reads.
    assert body["environment"]["zero_config"] == zero_config
    assert body["recommendations"]["install_plan"] == zero_config["plan"]
    assert body["recommendations"]["preset"] == zero_config["preset"]
    assert body["recommendations"]["summary"]["zero_config"] == zero_config["recommend"]


def test_scan_loads_openai_compatible_runtimes_through_the_cli(monkeypatch):
    seen = _scan_client(monkeypatch, models=[
        {"model_id": "vllm:Qwen/Qwen3-8B"},
        {"model_id": "lmstudio:qwen3-8b"},
    ])
    zero_config = seen["body"]["zero_config"]

    # No item is checked, so the first usable candidate wins.
    assert zero_config["recommend"]["model_id"] == "vllm:Qwen/Qwen3-8B"
    assert zero_config["recommend"]["runtime"] == "vllm"
    assert zero_config["plan"]["steps"][0]["command"] == [
        "lattice-ai", "models", "load", "vllm:Qwen/Qwen3-8B",
    ]


def test_scan_downloads_local_mlx_weights_from_the_hub(monkeypatch):
    seen = _scan_client(monkeypatch, models=[
        {"action": {"model_id": "mlx-community/Qwen3-4B-4bit"}},
    ])
    zero_config = seen["body"]["zero_config"]

    # The id lives on the action, and a bare repo id means local MLX.
    assert zero_config["recommend"]["model_id"] == "mlx-community/Qwen3-4B-4bit"
    assert zero_config["recommend"]["runtime"] == "mlx"
    assert zero_config["plan"]["steps"][0]["command"] == [
        "huggingface-cli", "download", "mlx-community/Qwen3-4B-4bit", "--quiet",
    ]


@pytest.mark.parametrize("models", [[], [{"disabled": True, "model_id": "ollama:x"}], {"nope": 1}])
def test_scan_leaves_the_plan_untouched_without_a_usable_model(monkeypatch, models):
    seen = _scan_client(monkeypatch, models=models)
    zero_config = seen["body"]["zero_config"]

    assert "model_id" not in zero_config["recommend"]
    assert zero_config["recommend"]["rationale"] == [RAM_RATIONALE, CHIP_RATIONALE]
    assert zero_config["plan"]["steps"] == [{"name": "node20"}]
    assert zero_config["preset"]["model"] == {"id": "placeholder"}
    assert seen["body"]["recommendations"]["install_plan"] == zero_config["plan"]


# ── /setup/install ─────────────────────────────────────────────────────────

def test_setup_install_streams_the_wizard_events_as_sse():
    response = _client().post(
        "/setup/install",
        json={"items": [{"id": "mlx-lm", "name": "MLX LM"}]},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"
    # An item with no action is reported as already satisfied, not installed,
    # and the stream is terminated by the wizard's completion event.
    events = _sse_events(response.text)
    assert [event["status"] for event in events] == ["skipped", "complete"]
    assert events[0]["id"] == "mlx-lm"


def test_setup_install_forwards_the_caller_and_confirmation_token(monkeypatch):
    captured: Dict[str, Any] = {}

    async def _fake_stream(items, router, *, confirmation_token=None, user_email=None):
        captured.update(
            items=items, router=router, token=confirmation_token, user=user_email
        )
        for status in ("starting", "done"):
            yield "data: " + json.dumps({"status": status}) + "\n\n"

    monkeypatch.setattr(api_setup, "install_stream", _fake_stream)
    model_router = object()
    client = _client(
        model_router=model_router,
        require_user=lambda request: "installer@example.com",
    )

    response = client.post(
        "/setup/install",
        json={"items": [{"id": "brew"}], "confirmation_token": "tok-123"},
    )

    assert [event["status"] for event in _sse_events(response.text)] == ["starting", "done"]
    assert captured["items"] == [{"id": "brew"}]
    assert captured["router"] is model_router
    assert captured["token"] == "tok-123"
    assert captured["user"] == "installer@example.com"


# ── browser hand-offs ──────────────────────────────────────────────────────

def test_open_auth_opens_the_connector_page(monkeypatch):
    opened: List[str] = []
    monkeypatch.setattr(api_setup, "open_url", opened.append)

    body = _client().post("/setup/open-auth/github").json()

    assert body == {
        "status": "ok",
        "opened": "https://github.com/apps",
        "mcp_id": "github",
    }
    assert opened == ["https://github.com/apps"]


@pytest.mark.parametrize(("language", "detail"), [
    ("en", "Unknown MCP: totally-unknown"),
    ("ko", "알 수 없는 MCP입니다: totally-unknown"),
])
def test_open_auth_refuses_an_unknown_mcp_in_the_caller_language(
    monkeypatch, language, detail
):
    opened: List[str] = []
    monkeypatch.setattr(api_setup, "open_url", opened.append)

    response = _client().post(
        "/setup/open-auth/totally-unknown",
        headers={"X-Lattice-Language": language},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == detail
    assert opened == []


def test_permission_route_opens_the_macos_settings_pane(monkeypatch):
    opened: List[str] = []
    monkeypatch.setattr(api_setup, "open_url", opened.append)

    body = _client().post("/permissions/open/screen").json()

    assert body["status"] == "ok"
    assert body["permission"] == "screen"
    assert body["opened"].endswith("Privacy_ScreenCapture")
    assert opened == [body["opened"]]


@pytest.mark.parametrize(("language", "detail"), [
    ("en", "Unknown permission setting."),
    ("ko", "알 수 없는 권한 설정입니다."),
])
def test_permission_route_refuses_an_unknown_pane(monkeypatch, language, detail):
    opened: List[str] = []
    monkeypatch.setattr(api_setup, "open_url", opened.append)

    response = _client().post(
        "/permissions/open/camera",
        headers={"X-Lattice-Language": language},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == detail
    assert opened == []


# ── demo corpus ────────────────────────────────────────────────────────────

class _FakePipeline:
    """Records what the router asks it to ingest and answers with outcomes."""

    def __init__(self, *, available: bool = True, outcomes: Optional[Dict[str, Any]] = None):
        self._available = available
        self._outcomes = outcomes or {}
        self.items: List[Any] = []
        self.actors: List[Optional[str]] = []

    def available(self) -> bool:
        return self._available

    def ingest(self, item, user_email=None):
        self.items.append(item)
        self.actors.append(user_email)
        status, duplicate = self._outcomes.get(item.metadata["demo_id"], ("ok", False))
        failed = status != "ok"
        return types.SimpleNamespace(
            status=status,
            node_id=None if failed else "node:" + item.metadata["demo_id"],
            duplicate=duplicate,
            chunk_count=0 if failed else 3,
            detail="disk full" if failed else "",
        )


class _FakeGraph:
    def __init__(self, documents: Optional[List[Dict[str, Any]]] = None):
        self.documents = documents or []
        self.deleted: List[str] = []

    def find_documents_by_uri_prefix(self, prefix: str) -> List[Dict[str, Any]]:
        return [doc for doc in self.documents if doc["source_uri"].startswith(prefix)]

    def delete_document_tree(self, node_id: str) -> Dict[str, Any]:
        self.deleted.append(node_id)
        return {"status": "ok", "removed_nodes": 4}


class _FakeWorkspaces:
    def __init__(self, *, scope: Optional[str] = None, error: Optional[str] = None):
        self.scope = scope
        self.error = error
        self.calls: List[Any] = []

    def resolve_write_scope(self, requested, user):
        self.calls.append((requested, user))
        if self.error:
            raise PermissionError(self.error)
        return self.scope


def _demo_client(**kwargs) -> TestClient:
    kwargs.setdefault("ingestion_pipeline", _FakePipeline())
    kwargs.setdefault("knowledge_graph", _FakeGraph())
    return _client(**kwargs)


@pytest.mark.parametrize(("language", "detail"), [
    ("en", "The Knowledge Graph is turned off."),
    ("ko", "지식 그래프가 꺼져 있습니다."),
])
def test_demo_corpus_needs_the_graph_even_when_ingestion_is_up(language, detail):
    client = _client(ingestion_pipeline=_FakePipeline(), knowledge_graph=None)

    response = client.get(
        "/api/setup/demo-corpus", headers={"X-Lattice-Language": language}
    )

    assert response.status_code == 503
    assert response.json()["detail"] == detail


def test_demo_corpus_refuses_conflicting_workspace_selectors():
    response = _demo_client().post(
        "/api/setup/demo-corpus",
        json={"workspace_id": "team-a"},
        headers={"X-Workspace-Id": "team-b", "X-Lattice-Language": "en"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Workspace selectors must match."


def test_demo_corpus_ingests_into_the_resolved_write_scope():
    pipeline = _FakePipeline()
    workspaces = _FakeWorkspaces(scope="ws-resolved")
    client = _client(
        ingestion_pipeline=pipeline,
        knowledge_graph=_FakeGraph(),
        workspace_service=workspaces,
    )

    body = client.post(
        "/api/setup/demo-corpus", headers={"X-Workspace-Id": "  team-a  "}
    ).json()

    assert workspaces.calls == [("team-a", USER)]
    assert [item.workspace_id for item in pipeline.items] == ["ws-resolved"] * 3
    assert [item.metadata[DEMO_METADATA_FLAG] for item in pipeline.items] == [True] * 3
    assert pipeline.actors == [USER] * 3
    assert body["status"] == "ok"
    assert body["ingested"] == 3
    assert [doc["source_uri"] for doc in body["documents"]] == [
        DEMO_URI_PREFIX + doc["id"] for doc in DEMO_DOCUMENTS
    ]


def test_demo_corpus_surfaces_a_workspace_permission_refusal():
    client = _client(
        ingestion_pipeline=_FakePipeline(),
        knowledge_graph=_FakeGraph(),
        workspace_service=_FakeWorkspaces(error="not a member of team-a"),
    )

    response = client.post("/api/setup/demo-corpus", json={"workspace_id": "team-a"})

    assert response.status_code == 403
    assert response.json()["detail"] == "not a member of team-a"


def test_demo_corpus_reports_a_partial_install_when_one_document_fails():
    pipeline = _FakePipeline(outcomes={
        "meeting-note": ("error", False),
        "project-doc": ("ok", True),
    })
    client = _client(ingestion_pipeline=pipeline, knowledge_graph=_FakeGraph())

    body = client.post("/api/setup/demo-corpus", json={}).json()

    assert body["status"] == "partial"
    assert (body["failed"], body["duplicates"], body["ingested"]) == (1, 1, 1)
    failed_doc = body["documents"][0]
    assert failed_doc["demo_id"] == "meeting-note"
    assert failed_doc["status"] == "error"
    assert failed_doc["node_id"] is None
    assert failed_doc["detail"] == "disk full"


def test_demo_corpus_reports_a_failed_install_when_nothing_lands():
    outcomes = {doc["id"]: ("error", False) for doc in DEMO_DOCUMENTS}
    client = _client(
        ingestion_pipeline=_FakePipeline(outcomes=outcomes),
        knowledge_graph=_FakeGraph(),
    )

    body = client.post("/api/setup/demo-corpus", json={}).json()

    assert body["status"] == "failed"
    assert body["failed"] == 3
    assert body["ingested"] == 0
