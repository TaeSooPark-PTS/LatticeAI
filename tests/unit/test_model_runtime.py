"""model_runtime trust gate unit tests for v6.1 hardening."""

from dataclasses import FrozenInstanceError

import pytest

from latticeai.models import router as model_router

# ``hf_model_dir`` reads the root from its own module globals, so after the
# v11.3.0 split the temp-dir stand-in lands on ``.local_models``.
from latticeai.models.router import local_models as router_local_models
from latticeai.services import model_runtime
from latticeai.services.model_errors import ModelRuntimeError
from latticeai.services.model_runtime import (
    ModelRuntimeService,
    ModelRuntimeState,
    _download_allowed,
    _download_block,
)


def test_download_blocked_without_model_download_consent():
    """External download must be blocked when no explicit consent is given."""
    # Default state: no consent
    state = ModelRuntimeState()
    assert _download_allowed(allow_download=False, state=state) is False
    assert _download_allowed(allow_download=True, state=state) is True

    # Without consent, calling block raises the expected 409 gate
    try:
        _download_block("huggingface", "some/model")
    except ModelRuntimeError as exc:
        assert exc.status_code == 409
        detail = exc.detail
        assert isinstance(detail, dict)
        assert detail.get("capability") == "model_download"
        assert "does not start outbound model downloads by default" in detail.get("reason", "")
    else:
        assert False, "_download_block must raise when consent is absent"


def test_configure_model_runtime_is_strict_isolated_factory():
    runtime = model_runtime.configure_model_runtime(PUBLIC_MODEL="test:model")

    assert isinstance(runtime, ModelRuntimeService)
    assert runtime.state.PUBLIC_MODEL == "test:model"
    assert not hasattr(model_runtime, "PUBLIC_MODEL")

    with pytest.raises(TypeError, match="UNTRUSTED_RUNTIME_GLOBAL"):
        model_runtime.configure_model_runtime(UNTRUSTED_RUNTIME_GLOBAL="leak")


def test_model_download_gate_uses_configured_runtime_state(monkeypatch):
    monkeypatch.setenv("LATTICEAI_ALLOW_MODEL_DOWNLOADS", "true")
    blocked = model_runtime.configure_model_runtime(
        ALLOW_MODEL_DOWNLOADS=False,
        AUTOLOAD_MODELS=False,
    )
    allowed = model_runtime.configure_model_runtime(ALLOW_MODEL_DOWNLOADS=True)

    assert _download_allowed(allow_download=False, state=blocked.state) is False
    assert _download_allowed(allow_download=False, state=allowed.state) is True
    assert blocked.state.ALLOW_MODEL_DOWNLOADS is False


def _write_minimal_model_dir(path):
    path.mkdir(parents=True, exist_ok=True)
    (path / "config.json").write_text('{"model_type": "test"}', encoding="utf-8")
    (path / "tokenizer.json").write_text("{}", encoding="utf-8")
    (path / "model.safetensors").write_bytes(b"model")


def test_local_mlx_reuses_existing_huggingface_cache(monkeypatch, tmp_path):
    """Already-downloaded HF cache snapshots should count as local MLX-ready."""
    monkeypatch.setenv("HOME", str(tmp_path))
    ltcai_root = tmp_path / ".ltcai" / "hf-models"
    monkeypatch.setattr(router_local_models, "HF_MODELS_ROOT", ltcai_root)

    snapshot = (
        tmp_path
        / ".cache"
        / "huggingface"
        / "hub"
        / "models--mlx-community--cached-model"
        / "snapshots"
        / "abc123"
    )
    _write_minimal_model_dir(snapshot)

    assert model_runtime.hf_model_ready("mlx-community/cached-model", "local_mlx") is True
    assert model_router.hf_cache_model_dir("mlx-community/cached-model") == snapshot
    assert model_router._resolve_local_hf_model("mlx-community/cached-model") == str(snapshot)


def test_model_runtime_state_is_immutable_and_not_module_ambient():
    state = ModelRuntimeState(PUBLIC_MODEL="test:model")

    with pytest.raises(FrozenInstanceError):
        state.PUBLIC_MODEL = "other:model"  # type: ignore[misc]

    assert "STATE" not in vars(model_runtime)
    assert "PUBLIC_MODEL" not in vars(model_runtime)
    assert not hasattr(model_runtime, "STATE")


def test_model_runtime_services_do_not_leak_between_apps():
    first = model_runtime.build_model_runtime(
        APP_MODE="public",
        PUBLIC_MODEL="openai:first",
        ALLOW_MODEL_DOWNLOADS=True,
    )
    second = model_runtime.build_model_runtime(
        APP_MODE="local",
        PUBLIC_MODEL="openai:second",
        ALLOW_MODEL_DOWNLOADS=False,
    )

    assert first.runtime_features()["mode"] == "public"
    assert second.runtime_features()["mode"] == "local"
    assert first.state.PUBLIC_MODEL == "openai:first"
    assert second.state.PUBLIC_MODEL == "openai:second"
    assert _download_allowed(state=first.state) is True
    assert _download_allowed(state=second.state) is False
