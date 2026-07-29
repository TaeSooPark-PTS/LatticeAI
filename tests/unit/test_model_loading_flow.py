"""Model preparation and loading — the gates before a model is allowed to load.

`prepare_and_load_model` decides whether an engine may be installed, whether a
model may be downloaded, and whether the loaded model actually answers. Every
one of those is a consent boundary (downloads and installs are opt-in, per the
local-first contract), so the branches are worth asserting rather than assuming.

The module takes all of its collaborators through `runtime_state`, so these
tests drive it with a recording fake and never touch a real engine, network,
or subprocess.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from latticeai.services import model_loading
from latticeai.services.model_runtime import ModelRuntimeError


class _Resolution:
    """Stands in for _ModelResolution: records what was asked and loaded."""

    def __init__(self, model_id, engine=None, user_email=None, engine_aliases=None):
        self.load_id = model_id
        self.engine = engine
        self.user_email = user_email
        self.actual_current = None

    @classmethod
    def from_request(cls, model_id, *, engine=None, user_email=None, engine_aliases=None):
        return cls(model_id, engine=engine, user_email=user_email)

    def update_after_load(self, *, actual_current):
        self.actual_current = actual_current

    def to_dict(self):
        return {"load_id": self.load_id, "actual_current": self.actual_current}


class _Router:
    def __init__(self, current="loaded-model"):
        self.current_model_id = current
        self.loaded = []

    async def load_model(self, model_id, adapter_path, **kwargs):
        self.loaded.append({"model_id": model_id, "adapter_path": adapter_path, **kwargs})
        self.current_model_id = model_id
        return f"loaded {model_id}"


def _deps(**over):
    """A complete, permissive dependency surface; override one key per test."""
    router = over.pop("router", None) or _Router()
    calls: dict = {"blocked": [], "installed": [], "downloaded": []}

    def _download_block(provider, model):
        calls["blocked"].append(("download", provider, model))
        raise ModelRuntimeError(status_code=403, detail=f"download not allowed: {provider}/{model}")

    def _engine_install_block(provider):
        calls["blocked"].append(("install", provider, None))
        raise ModelRuntimeError(status_code=403, detail=f"install not allowed: {provider}")

    async def _smoke(resolution, api_key_override=None):
        return {"ok": True, "status": "ok"}

    base = {
        "normalize_local_model_request": lambda mid, engine: mid,
        "_ModelResolution": _Resolution,
        "parse_model_ref": lambda mid: tuple(mid.split(":", 1)) if ":" in mid else ("local_mlx", mid),
        "_model_runtime_compatibility": lambda model, engine=None: {"supported": True},
        "engine_installed": lambda provider: True,
        "_download_allowed": lambda allow: bool(allow),
        "_engine_install_block": _engine_install_block,
        "ensure_engine_ready": lambda provider: {"installed_now": False},
        "hf_model_ready": lambda model, engine: True,
        "_download_block": _download_block,
        "download_hf_model": lambda model, engine: {"provider": engine, "model": model},
        "ensure_ollama_server": lambda: None,
        "local_binary": lambda name: f"/usr/bin/{name}",
        "get_ollama_pulled_models": lambda: [],
        "ensure_vllm_server": lambda model: None,
        "ensure_llamacpp_server": lambda model: None,
        "get_lmstudio_models": lambda: [],
        "ensure_lmstudio_model": lambda model: {"instance_id": model},
        "get_current_user": lambda request: "me@local",
        "get_user_api_key": lambda email, provider: None,
        "router": router,
        "_smoke_test_loaded_model": _smoke,
        "MODEL_ENGINE_ALIASES": {},
        "_friendly_model_runtime_error": lambda exc: str(exc),
        "hf_model_dir": lambda model: Path("/tmp/models") / model,
        "model_download_progress_payload": lambda *a, **k: {},
        "get_lmstudio_models_raw": lambda: [],
        "pull_ollama_model_with_progress": lambda *a, **k: None,
    }
    base.update(over)
    base["_calls"] = calls
    return base


@pytest.fixture()
def patched(monkeypatch):
    """Install a dependency surface and hand the test its recorder."""
    def install(**over):
        deps = _deps(**over)
        monkeypatch.setattr(model_loading, "_get_model_runtime_deps", lambda state: deps)
        return deps
    return install


async def _load(deps_installer, **kwargs):
    return await model_loading.prepare_and_load_model(
        kwargs.pop("model_id", "local_mlx:some-model"),
        request=object(),
        runtime_state=object(),
        **kwargs,
    )


def test_an_empty_model_id_is_refused_before_anything_runs(patched):
    import asyncio

    patched(normalize_local_model_request=lambda mid, engine: "")
    with pytest.raises(ModelRuntimeError) as err:
        asyncio.run(_load(patched, model_id="   "))
    assert err.value.status_code == 400


def test_an_unsupported_model_is_refused_with_the_compatibility_report(patched):
    import asyncio

    report = {"supported": False, "reason": "needs 64GB"}
    patched(_model_runtime_compatibility=lambda model, engine=None: report)
    with pytest.raises(ModelRuntimeError) as err:
        asyncio.run(_load(patched))
    assert err.value.status_code == 400
    assert err.value.detail == report, "the refusal must carry why, not just that"


def test_engine_install_is_blocked_unless_the_user_allowed_it(patched):
    """Installing a runtime is a local-first consent boundary."""
    import asyncio

    deps = patched(engine_installed=lambda provider: False)
    with pytest.raises(ModelRuntimeError):
        asyncio.run(_load(patched, allow_download=False))
    assert ("install", "local_mlx", None) in deps["_calls"]["blocked"]


def test_engine_install_proceeds_once_allowed(patched):
    import asyncio

    installs = []
    patched(
        engine_installed=lambda provider: False,
        ensure_engine_ready=lambda provider: installs.append(provider) or {"installed_now": True},
    )
    result = asyncio.run(_load(patched, allow_download=True))
    assert installs == ["local_mlx"]
    assert result["installed_now"] is True


def test_a_missing_local_model_is_not_downloaded_without_consent(patched):
    import asyncio

    deps = patched(hf_model_ready=lambda model, engine: False)
    with pytest.raises(ModelRuntimeError):
        asyncio.run(_load(patched, allow_download=False))
    assert ("download", "local_mlx", "some-model") in deps["_calls"]["blocked"]


def test_a_missing_local_model_downloads_once_allowed(patched):
    import asyncio

    downloads = []
    patched(
        hf_model_ready=lambda model, engine: False,
        download_hf_model=lambda model, engine: downloads.append((model, engine)) or {"model": model},
    )
    result = asyncio.run(_load(patched, allow_download=True))
    assert downloads == [("some-model", "local_mlx")]
    assert result["downloaded"] is True


def test_a_cached_download_is_not_reported_as_downloaded(patched):
    """`downloaded: true` should mean bytes moved, not "we checked"."""
    import asyncio

    patched(
        hf_model_ready=lambda model, engine: False,
        download_hf_model=lambda model, engine: {"model": model, "cached": True},
    )
    result = asyncio.run(_load(patched, allow_download=True))
    assert result["downloaded"] is False


def test_ollama_without_the_binary_is_a_clear_refusal(patched):
    import asyncio

    patched(local_binary=lambda name: None)
    with pytest.raises(ModelRuntimeError) as err:
        asyncio.run(_load(patched, model_id="ollama:llama3"))
    assert err.value.status_code == 400
    assert "Ollama" in str(err.value.detail)


def test_lmstudio_resolves_to_the_instance_the_server_actually_gave(patched):
    """LM Studio can hand back a different instance id; the result must say so."""
    import asyncio

    router = _Router()
    patched(router=router, ensure_lmstudio_model=lambda m: {"instance_id": "qwen-3-8b:2"})
    result = asyncio.run(_load(patched, model_id="lmstudio:qwen-3-8b", allow_download=True))
    assert result["model"] == "lmstudio:qwen-3-8b:2"
    assert router.loaded[0]["model_id"] == "lmstudio:qwen-3-8b:2"


def test_a_loaded_model_that_fails_its_smoke_test_is_not_ready_to_chat(patched):
    """Loading is not the same as answering; the caller must be able to tell."""
    import asyncio

    async def failing_smoke(resolution, api_key_override=None):
        return {"ok": False, "status": "degraded"}

    patched(_smoke_test_loaded_model=failing_smoke)
    result = asyncio.run(_load(patched))
    assert result["loaded"] is True
    assert result["ready_to_chat"] is False
    assert result["compatibility_status"] == "degraded"


def test_a_smoke_test_that_raises_reports_unknown_rather_than_success(patched):
    import asyncio

    async def exploding_smoke(resolution, api_key_override=None):
        raise RuntimeError("engine hung")

    patched(_smoke_test_loaded_model=exploding_smoke)
    result = asyncio.run(_load(patched))
    assert result["compatibility_status"] == "unknown", (
        "an unverifiable model must not be reported as ok"
    )


def test_a_cloud_provider_gets_the_users_api_key_and_local_mlx_does_not(patched):
    import asyncio

    seen: list = []
    patched(
        get_user_api_key=lambda email, provider: seen.append((email, provider)) or "sk-test",
        _model_runtime_compatibility=lambda model, engine=None: {"supported": True},
    )
    asyncio.run(_load(patched, model_id="openai:gpt-4o-mini"))
    assert seen == [("me@local", "openai")]

    seen.clear()
    asyncio.run(_load(patched, model_id="local_mlx:some-model"))
    assert seen == [], "a local model must never look up a cloud key"


def test_the_result_reports_what_the_router_actually_holds(patched):
    import asyncio

    router = _Router(current="previously-loaded")
    patched(router=router)
    result = asyncio.run(_load(patched, model_id="local_mlx:new-model"))
    assert result["current"] == "local_mlx:new-model"
    assert result["resolution"]["actual_current"] == "local_mlx:new-model"


def test_sse_event_is_valid_utf8_json_framing():
    frame = model_loading.sse_event("progress", {"percent": 42, "model": "한글-모델"})
    assert frame.startswith("event: progress\ndata: ")
    assert frame.endswith("\n\n")
    assert "한글-모델" in frame, "non-ascii must survive rather than be escaped"
