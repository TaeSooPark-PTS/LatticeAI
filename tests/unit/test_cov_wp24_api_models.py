"""wp24 coverage — ``latticeai.api.models`` (the /models + /engines router).

Every heavy dependency of this router is an injected service callable, so the
router is built by its factory with fakes and driven through ``TestClient``.
The two seams that would otherwise touch the machine are replaced explicitly:
``subprocess`` (an ``ollama pull`` is minutes of network I/O) and the model
compat / catalog helpers the handlers import lazily.

What is asserted is the router's own job — which failures become which status
code, that a download never starts without explicit consent, that identity is
taken from the session rather than the body, and that a streaming prepare
reports its failure as an SSE ``error`` event instead of a broken response.
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from latticeai.api import models as models_mod
from latticeai.api.models import _vision_capability, create_models_router
from latticeai.services.model_errors import ModelRuntimeError


class _FakeModelRouter:
    def __init__(self):
        self.loaded_model_ids = ["mlx-community/loaded-4bit"]
        self.current_model_id = "mlx-community/loaded-4bit"
        self.unloaded_all = False

    def detected_cloud_models(self):
        return [{"id": "openai:gpt-4o-mini"}]

    def switch_model(self, model_id):
        if model_id not in self.loaded_model_ids:
            raise KeyError(model_id)
        self.current_model_id = model_id

    def unload_model(self, model_id):
        self.loaded_model_ids = [item for item in self.loaded_model_ids if item != model_id]

    def unload_all(self):
        self.unloaded_all = True
        self.loaded_model_ids = []
        self.current_model_id = None


def _build(**overrides):
    """Build the router with recording fakes; overrides replace one dep each."""
    calls: list[tuple] = []

    async def verify_cloud_models(**kwargs):
        calls.append(("verify", kwargs))
        return [{"provider": "openai", "ok": True}]

    async def prepare_and_load_model(model, _request, **kwargs):
        calls.append(("prepare", {"model": model, **kwargs}))
        return {"ok": True, "model": model, "user_email": kwargs.get("user_email")}

    async def prepare_and_load_model_stream(model, _request, **kwargs):
        calls.append(("prepare_stream", {"model": model, **kwargs}))
        yield "data: ready\n\n"

    deps = dict(
        model_router=_FakeModelRouter(),
        require_user=lambda _request: "admin@example.com",
        require_admin=lambda _request: ("admin@example.com", {}),
        get_current_user=lambda _request: "admin@example.com",
        load_users=lambda: {"admin@example.com": {"role": "admin"}},
        get_user_role=lambda *_a, **_kw: "admin",
        install_engine=lambda engine, **kw: calls.append(("install", {"engine": engine, **kw})) or {"installed": engine},
        verify_cloud_models=verify_cloud_models,
        normalize_local_model_request=lambda model, _engine=None: str(model or "").strip(),
        download_hf_model=lambda model, provider: calls.append(("hf", {"model": model, "provider": provider})) or {"path": "/models/x"},
        prepare_and_load_model=prepare_and_load_model,
        prepare_and_load_model_stream=prepare_and_load_model_stream,
        sse_event=lambda event, data: f"event: {event}\ndata: {data}\n\n",
        ensure_ollama_server=lambda: calls.append(("ollama_server", {})),
        local_binary=lambda name: f"/usr/local/bin/{name}",
        engine_status=lambda: [{"id": "local_mlx", "name": "MLX", "installed": True, "models": []}],
        filter_lower_family_versions=lambda items: items,
        list_compat_profiles=lambda: [{"id": "mlx"}],
        set_user_api_key=lambda *args, **kw: calls.append(("set_key", args)),
        engine_model_catalog={"local_mlx": []},
        model_engine_aliases={},
        cloud_verify_ttl_seconds=600,
        is_public_mode=False,
        allow_local_models=True,
        require_auth=True,
    )
    deps.update(overrides)

    app = FastAPI()
    app.include_router(create_models_router(**deps))
    return TestClient(app, raise_server_exceptions=False), calls


def _fake_subprocess(run):
    return SimpleNamespace(run=run, TimeoutExpired=subprocess.TimeoutExpired)


# ── vision capability ───────────────────────────────────────────────────────


def test_vision_is_enabled_only_for_a_vision_capable_current_model(monkeypatch):
    monkeypatch.setattr(
        "latticeai.core.model_compat.get_model_profile",
        lambda model_id: {"supports_vision": "vlm" in model_id},
    )
    engines = [{"id": "local_mlx", "installed": True}]

    vlm = _vision_capability("mlx-community/gemma-vlm-4bit", engines)
    text_only = _vision_capability("mlx-community/qwen-text-4bit", engines)

    assert vlm["enabled"] is True
    assert vlm["current_supports_vision"] is True
    assert vlm["engine_available"] is True
    assert text_only["enabled"] is False
    assert text_only["engine_available"] is True


def test_vision_degrades_when_the_profile_or_engine_listing_fails(monkeypatch):
    def boom(_model_id):
        raise RuntimeError("profile cache is corrupt")

    monkeypatch.setattr("latticeai.core.model_compat.get_model_profile", boom)

    class _UnlistableEngines:
        def __iter__(self):
            raise RuntimeError("engine probe failed")

    payload = _vision_capability("mlx-community/anything", _UnlistableEngines())

    assert payload["current_supports_vision"] is False
    assert payload["engine_available"] is False
    assert payload["enabled"] is False


# ── engines ─────────────────────────────────────────────────────────────────


def test_engine_install_forwards_the_confirmation_token():
    client, calls = _build()

    response = client.post(
        "/engines/install", json={"engine": "local_mlx", "confirmation_token": "tok-1"},
    )

    assert response.status_code == 200
    assert response.json() == {"installed": "local_mlx"}
    assert calls == [("install", {"engine": "local_mlx", "confirmation_token": "tok-1"})]


def test_verify_cloud_returns_the_results_with_the_cache_ttl():
    client, calls = _build()

    response = client.post("/engines/verify-cloud", json={"force": True, "provider": "openai"})

    assert response.status_code == 200
    assert response.json() == {
        "verified": [{"provider": "openai", "ok": True}], "ttl_seconds": 600,
    }
    assert calls == [("verify", {"force": True, "provider_filter": "openai"})]


def test_verify_cloud_translates_a_model_runtime_error():
    async def failing(**_kwargs):
        raise ModelRuntimeError(status_code=502, detail={"status": "provider_unreachable"})

    client, _calls = _build(verify_cloud_models=failing)

    response = client.post("/engines/verify-cloud", json={})

    assert response.status_code == 502
    assert response.json()["detail"] == {"status": "provider_unreachable"}


def test_a_pull_never_starts_without_explicit_download_consent():
    client, calls = _build()

    response = client.post("/engines/pull-model", json={"model": "ollama:llama3"})

    assert response.status_code == 403
    assert calls == []


@pytest.mark.parametrize(
    ("model", "normalized"),
    [("   ", ""), ("ollama:   ", "ollama:")],
)
def test_a_pull_refuses_an_empty_model_identifier(model, normalized):
    client, _calls = _build(normalize_local_model_request=lambda value, _engine=None: normalized)

    response = client.post("/engines/pull-model", json={"model": model, "allow_download": True})

    assert response.status_code == 400


def test_an_ollama_pull_runs_the_daemon_and_the_pull_off_the_event_loop(monkeypatch):
    seen: dict = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["timeout"] = kwargs["timeout"]
        return subprocess.CompletedProcess(argv, 0, "pulled\n", "")

    monkeypatch.setattr(models_mod, "subprocess", _fake_subprocess(fake_run))
    client, calls = _build()

    response = client.post(
        "/engines/pull-model", json={"model": "ollama:llama3", "allow_download": True},
    )

    assert response.status_code == 200
    assert response.json() == {"provider": "ollama", "model": "llama3", "returncode": 0}
    assert seen["argv"] == ["/usr/local/bin/ollama", "pull", "llama3"]
    assert seen["timeout"] == 900
    assert ("ollama_server", {}) in calls


def test_an_ollama_pull_reports_a_failed_exit_code_as_a_server_error(monkeypatch):
    def fake_run(argv, **_kwargs):
        return subprocess.CompletedProcess(argv, 1, "", "manifest not found")

    monkeypatch.setattr(models_mod, "subprocess", _fake_subprocess(fake_run))
    client, _calls = _build()

    response = client.post(
        "/engines/pull-model", json={"model": "ollama:llama3", "allow_download": True},
    )

    assert response.status_code == 500
    assert response.json()["detail"] == "manifest not found"


def test_an_ollama_pull_that_times_out_is_a_408(monkeypatch):
    def fake_run(argv, **_kwargs):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=900)

    monkeypatch.setattr(models_mod, "subprocess", _fake_subprocess(fake_run))
    client, _calls = _build()

    response = client.post(
        "/engines/pull-model", json={"model": "ollama:llama3", "allow_download": True},
    )

    assert response.status_code == 408


def test_an_unstartable_ollama_daemon_keeps_its_status_code():
    def boom():
        raise ModelRuntimeError(status_code=503, detail="Ollama is not installed")

    client, _calls = _build(ensure_ollama_server=boom)

    response = client.post(
        "/engines/pull-model", json={"model": "ollama:llama3", "allow_download": True},
    )

    assert response.status_code == 503


def test_an_ollama_pull_without_the_binary_is_a_400():
    client, _calls = _build(local_binary=lambda _name: None)

    response = client.post(
        "/engines/pull-model", json={"model": "ollama:llama3", "allow_download": True},
    )

    assert response.status_code == 400


def test_lm_studio_models_are_not_pulled_by_lattice():
    client, calls = _build()

    response = client.post(
        "/engines/pull-model", json={"model": "lmstudio:qwen", "allow_download": True},
    )

    assert response.status_code == 400
    assert "LM Studio" in response.json()["detail"]
    assert calls == []


def test_a_local_model_pull_downloads_from_hugging_face():
    client, calls = _build()

    response = client.post(
        "/engines/pull-model", json={"model": "mlx:some/model", "allow_download": True},
    )

    assert response.status_code == 200
    assert response.json() == {
        "provider": "mlx", "model": "some/model", "returncode": 0, "path": "/models/x",
    }
    # `mlx` is an alias; the download is performed for the real local_mlx engine.
    assert calls == [("hf", {"model": "some/model", "provider": "local_mlx"})]


def test_a_failing_hugging_face_download_keeps_its_status_code():
    def boom(_model, _provider):
        raise ModelRuntimeError(status_code=507, detail="not enough disk space")

    client, _calls = _build(download_hf_model=boom)

    response = client.post(
        "/engines/pull-model", json={"model": "local_mlx:some/model", "allow_download": True},
    )

    assert response.status_code == 507


def test_a_reference_whose_prefix_is_not_an_engine_is_a_hugging_face_repo_id():
    client, calls = _build()

    response = client.post(
        "/engines/pull-model", json={"model": "org/model:rev", "allow_download": True},
    )

    assert response.status_code == 200
    assert response.json()["provider"] == "local_mlx"
    assert calls == [("hf", {"model": "org/model:rev", "provider": "local_mlx"})]


# ── prepare-model ───────────────────────────────────────────────────────────


def test_prepare_model_translates_a_model_runtime_error():
    async def failing(_model, _request, **_kwargs):
        raise ModelRuntimeError(status_code=409, detail={"status": "confirmation_required"})

    client, _calls = _build(prepare_and_load_model=failing)

    response = client.post("/engines/prepare-model", json={"model": "some/model"})

    assert response.status_code == 409
    assert response.json()["detail"] == {"status": "confirmation_required"}


def test_prepare_model_preserves_an_http_exception_from_the_service():
    async def failing(_model, _request, **_kwargs):
        raise HTTPException(status_code=402, detail="payment required")

    client, _calls = _build(prepare_and_load_model=failing)

    response = client.post("/engines/prepare-model", json={"model": "some/model"})

    assert response.status_code == 402
    assert response.json()["detail"] == "payment required"


def test_prepare_model_turns_an_unexpected_failure_into_friendly_guidance(monkeypatch):
    async def failing(_model, _request, **_kwargs):
        raise RuntimeError("mlx segfault")

    monkeypatch.setattr(
        "latticeai.core.model_compat.friendly_model_runtime_error",
        lambda exc, model_id=None, engine=None: {"status": "repair_model", "model_id": model_id},
    )
    client, _calls = _build(prepare_and_load_model=failing)

    response = client.post(
        "/engines/prepare-model", json={"model": "some/model", "engine": "local_mlx"},
    )

    assert response.status_code == 500
    assert response.json()["detail"] == {"status": "repair_model", "model_id": "some/model"}


def test_the_prepare_stream_yields_the_service_chunks():
    client, calls = _build()

    response = client.post("/engines/prepare-model/stream", json={"model": "some/model"})

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"
    assert response.text == "data: ready\n\n"
    assert calls[0][1]["user_email"] == "admin@example.com"


def test_the_prepare_stream_reports_a_known_failure_as_an_sse_error_event():
    async def failing(_model, _request, **_kwargs):
        raise ModelRuntimeError(status_code=409, detail="confirmation required")
        yield  # pragma: no cover — keeps this an async generator

    client, _calls = _build(prepare_and_load_model_stream=failing)

    response = client.post("/engines/prepare-model/stream", json={"model": "some/model"})

    assert response.status_code == 200
    assert "event: error" in response.text
    assert "409" in response.text


def test_the_prepare_stream_reports_an_unexpected_failure_as_an_sse_error_event(monkeypatch):
    async def failing(_model, _request, **_kwargs):
        raise RuntimeError("mlx segfault")
        yield  # pragma: no cover — keeps this an async generator

    monkeypatch.setattr(
        "latticeai.core.model_compat.friendly_model_runtime_error",
        lambda exc, model_id=None, engine=None: f"repair {model_id}",
    )
    client, _calls = _build(prepare_and_load_model_stream=failing)

    response = client.post("/engines/prepare-model/stream", json={"model": "some/model"})

    assert response.status_code == 200
    assert "event: error" in response.text
    assert "repair some/model" in response.text


# ── api keys ────────────────────────────────────────────────────────────────


def test_set_api_key_refuses_an_unknown_provider_and_an_empty_key():
    client, calls = _build()

    unknown = client.post("/setup/set-api-key", json={"provider": "nope", "key": "k"})
    empty = client.post("/setup/set-api-key", json={"provider": "openai", "key": "   "})

    assert unknown.status_code == 400
    assert empty.status_code == 400
    assert calls == []


def test_set_api_key_stores_the_key_for_the_session_identity():
    client, calls = _build()

    response = client.post(
        "/setup/set-api-key", json={"provider": "openai", "key": "  sk-live-1  "},
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True, "provider": "openai",
        "user_email": "admin@example.com", "scope": "user",
    }
    assert calls == [("set_key", ("admin@example.com", "openai", "sk-live-1"))]


def test_set_api_key_requires_an_identity_when_auth_is_disabled():
    client, calls = _build(require_auth=False, require_user=lambda _request: "")

    response = client.post("/setup/set-api-key", json={"provider": "openai", "key": "sk-1"})

    assert response.status_code == 400
    assert calls == []


# ── models ──────────────────────────────────────────────────────────────────


def test_the_model_listing_survives_a_broken_verified_registry(monkeypatch):
    def boom():
        raise RuntimeError("registry file is corrupt")

    monkeypatch.setattr("latticeai.services.model_catalog.get_verified_models", boom)
    client, _calls = _build()

    payload = client.get("/models").json()

    assert payload["registry"] == {"version": "5.2.0", "verified_count": 0, "verified": []}
    assert payload["current"] == "mlx-community/loaded-4bit"
    assert payload["cloud"] == [{"id": "openai:gpt-4o-mini"}]
    assert payload["compat_profiles"] == [{"id": "mlx"}]


def test_the_model_listing_reports_engine_options_per_recommendation(monkeypatch):
    monkeypatch.setattr(
        "latticeai.core.model_compat.model_runtime_compatibility",
        lambda model_id, engine=None: {
            "supported": True, "status": "supported", "preferred_runtime": "MLX",
        },
    )
    monkeypatch.setattr("latticeai.services.model_catalog.get_verified_models", lambda: [])
    catalog = {
        "local_mlx": [
            {"id": "mlx-community/loaded-4bit", "name": "Loaded", "tag": "local", "size": "4GB"},
            {"id": "mlx-community/absent-4bit", "name": "Absent", "tag": "local", "size": "4GB"},
        ]
    }
    engines = [{"id": "local_mlx", "name": "MLX", "installed": False, "models": []}]
    client, _calls = _build(engine_model_catalog=catalog, engine_status=lambda: engines)

    recommended = {item["id"]: item for item in client.get("/models").json()["recommended"]}

    # The currently loaded model is loadable regardless of engine state.
    assert recommended["mlx-community/loaded-4bit"]["load_status"] == "loaded"
    assert recommended["mlx-community/loaded-4bit"]["unavailable_reason"] is None
    # An absent model on an uninstalled engine explains itself.
    assert recommended["mlx-community/absent-4bit"]["load_status"] == "unavailable"
    assert "not installed" in recommended["mlx-community/absent-4bit"]["unavailable_reason"]


def test_a_ready_engine_reports_a_missing_model_as_download_required(monkeypatch):
    monkeypatch.setattr(
        "latticeai.core.model_compat.model_runtime_compatibility",
        lambda model_id, engine=None: {
            "supported": True, "status": "supported", "preferred_runtime": "MLX",
        },
    )
    monkeypatch.setattr("latticeai.services.model_catalog.get_verified_models", lambda: [])
    catalog = {
        "local_mlx": [
            {"id": "mlx-community/absent-4bit", "name": "Absent", "tag": "local", "size": "4GB"},
        ]
    }
    engines = [{"id": "local_mlx", "name": "MLX", "installed": True, "models": []}]
    client, _calls = _build(engine_model_catalog=catalog, engine_status=lambda: engines)

    item = client.get("/models").json()["recommended"][0]

    assert item["load_status"] == "download_required"
    assert item["download_required"] is True
    assert item["load_available"] is False
    assert "opt-in" in item["unavailable_reason"]


def test_compat_profiles_are_readable_by_any_signed_in_user():
    client, _calls = _build()

    response = client.get("/models/compat-profiles")

    assert response.status_code == 200
    assert response.json() == {"profiles": [{"id": "mlx"}]}


def test_load_model_refuses_an_incompatible_runtime(monkeypatch):
    monkeypatch.setattr(
        "latticeai.core.model_compat.model_runtime_compatibility",
        lambda model_id, engine=None: {
            "supported": False, "status": "runtime_update_needed", "model_id": model_id,
        },
    )
    client, calls = _build()

    response = client.post("/models/load", json={"model_id": "mlx-community/gemma-4-12b"})

    assert response.status_code == 400
    assert response.json()["detail"]["status"] == "runtime_update_needed"
    assert calls == []


def test_load_model_prepares_a_supported_model(monkeypatch):
    monkeypatch.setattr(
        "latticeai.core.model_compat.model_runtime_compatibility",
        lambda model_id, engine=None: {"supported": True},
    )
    client, calls = _build()

    response = client.post(
        "/models/load",
        json={
            "model_id": "mlx-community/loaded-4bit",
            "adapter_path": "/adapters/a",
            "draft_model_id": "mlx-community/draft",
            "allow_download": True,
        },
    )

    assert response.status_code == 200
    assert calls[0][1]["adapter_path"] == "/adapters/a"
    assert calls[0][1]["draft_model_id"] == "mlx-community/draft"
    assert calls[0][1]["allow_download"] is True


def test_load_model_is_blocked_for_local_engines_in_public_mode(monkeypatch):
    monkeypatch.setattr(
        "latticeai.core.model_compat.model_runtime_compatibility",
        lambda model_id, engine=None: {"supported": True},
    )
    client, calls = _build(is_public_mode=True, allow_local_models=False)

    response = client.post("/models/load", json={"model_id": "local_mlx:some/model"})

    assert response.status_code == 400
    assert calls == []


def test_load_model_translates_a_model_runtime_error(monkeypatch):
    monkeypatch.setattr(
        "latticeai.core.model_compat.model_runtime_compatibility",
        lambda model_id, engine=None: {"supported": True},
    )

    async def failing(_model, _request, **_kwargs):
        raise ModelRuntimeError(status_code=413, detail="model too large")

    client, _calls = _build(prepare_and_load_model=failing)

    response = client.post("/models/load", json={"model_id": "mlx-community/loaded-4bit"})

    assert response.status_code == 413


def test_load_model_turns_an_unexpected_failure_into_friendly_guidance(monkeypatch):
    monkeypatch.setattr(
        "latticeai.core.model_compat.model_runtime_compatibility",
        lambda model_id, engine=None: {"supported": True},
    )
    monkeypatch.setattr(
        "latticeai.core.model_compat.friendly_model_runtime_error",
        lambda exc, model_id=None, engine=None: {"status": "repair_model", "model_id": model_id},
    )

    async def failing(_model, _request, **_kwargs):
        raise RuntimeError("mlx segfault")

    client, _calls = _build(prepare_and_load_model=failing)

    response = client.post("/models/load", json={"model_id": "mlx-community/loaded-4bit"})

    assert response.status_code == 500
    assert response.json()["detail"]["status"] == "repair_model"


def test_switch_unload_and_unload_all_report_the_router_state():
    client, _calls = _build()

    switched = client.post("/models/switch/mlx-community/loaded-4bit")
    missing = client.post("/models/switch/mlx-community/never-loaded")
    unloaded = client.delete("/models/unload/mlx-community/loaded-4bit")
    unloaded_all = client.delete("/models/unload-all")

    assert switched.status_code == 200
    assert switched.json() == {"status": "ok", "current": "mlx-community/loaded-4bit"}
    assert missing.status_code == 404
    assert unloaded.json() == {"status": "ok", "unloaded": "mlx-community/loaded-4bit"}
    assert unloaded_all.json() == {"status": "ok", "unloaded": []}


def test_recommendations_combine_the_hardware_probe_with_the_registry(monkeypatch):
    monkeypatch.setattr(
        "latticeai.setup.auto_setup.probe",
        lambda: SimpleNamespace(to_json=lambda: {"ram_gb": 64, "chip": "M4"}),
    )
    monkeypatch.setattr(
        "latticeai.services.model_recommendation.recommend_catalog",
        lambda profile, engine="local_mlx": [{"id": "x", "engine": engine, "ram": profile["ram_gb"]}],
    )
    monkeypatch.setattr(
        "latticeai.services.model_catalog.get_verified_models", lambda: [{"id": "x"}],
    )
    client, _calls = _build()

    payload = client.get("/models/recommendations", params={"engine": "ollama"}).json()

    assert payload["profile"] == {"ram_gb": 64, "chip": "M4"}
    assert payload["recommendations"] == [{"id": "x", "engine": "ollama", "ram": 64}]
    assert payload["registry"] == {"version": "5.2.0", "verified_total": 1}


def test_recommendations_survive_a_broken_verified_registry(monkeypatch):
    def boom():
        raise RuntimeError("registry file is corrupt")

    monkeypatch.setattr(
        "latticeai.setup.auto_setup.probe",
        lambda: SimpleNamespace(to_json=lambda: {"ram_gb": 16}),
    )
    monkeypatch.setattr(
        "latticeai.services.model_recommendation.recommend_catalog",
        lambda profile, engine="local_mlx": [],
    )
    monkeypatch.setattr("latticeai.services.model_catalog.get_verified_models", boom)
    client, _calls = _build()

    payload = client.get("/models/recommendations").json()

    assert payload["registry"] == {"version": "5.2.0"}
    assert payload["recommendations"] == []


def test_a_signed_in_non_admin_cannot_act_as_another_user():
    def require_user(_request: Request) -> str:
        return "member@example.com"

    client, calls = _build(require_user=require_user)

    response = client.post(
        "/engines/prepare-model",
        json={"model": "some/model", "user_email": "victim@example.com"},
    )

    assert response.status_code == 403
    assert calls == []
