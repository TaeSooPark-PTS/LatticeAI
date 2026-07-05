"""model_runtime trust gate unit tests for v6.1 hardening."""

from fastapi import HTTPException

from latticeai.models import router as model_router
from latticeai.services import model_runtime
from latticeai.services.model_runtime import _download_allowed, _download_block


def test_download_blocked_without_model_download_consent():
    """External download must be blocked when no explicit consent is given."""
    # Default state: no consent
    assert _download_allowed(allow_download=False) is False
    assert _download_allowed(allow_download=True) is True

    # Without consent, calling block raises the expected 409 gate
    try:
        _download_block("huggingface", "some/model")
    except HTTPException as exc:
        assert exc.status_code == 409
        detail = exc.detail
        assert isinstance(detail, dict)
        assert detail.get("capability") == "model_download"
        assert "does not start outbound model downloads by default" in detail.get("reason", "")
    else:
        assert False, "_download_block must raise when consent is absent"


def test_configure_model_runtime_ignores_unknown_globals(monkeypatch):
    monkeypatch.delattr(model_runtime, "UNTRUSTED_RUNTIME_GLOBAL", raising=False)
    original_public_model = model_runtime.PUBLIC_MODEL
    try:
        model_runtime.configure_model_runtime(
            PUBLIC_MODEL="test:model",
            UNTRUSTED_RUNTIME_GLOBAL="leak",
        )
        assert model_runtime.PUBLIC_MODEL == "test:model"
        assert not hasattr(model_runtime, "UNTRUSTED_RUNTIME_GLOBAL")
    finally:
        model_runtime.configure_model_runtime(PUBLIC_MODEL=original_public_model)


def _write_minimal_model_dir(path):
    path.mkdir(parents=True, exist_ok=True)
    (path / "config.json").write_text('{"model_type": "test"}', encoding="utf-8")
    (path / "tokenizer.json").write_text("{}", encoding="utf-8")
    (path / "model.safetensors").write_bytes(b"model")


def test_local_mlx_reuses_existing_huggingface_cache(monkeypatch, tmp_path):
    """Already-downloaded HF cache snapshots should count as local MLX-ready."""
    monkeypatch.setenv("HOME", str(tmp_path))
    ltcai_root = tmp_path / ".ltcai" / "hf-models"
    monkeypatch.setattr(model_router, "HF_MODELS_ROOT", ltcai_root)

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


def test_model_runtime_state_is_source_and_sync_warns():
    """STATE is the implementation source; explicit sync_to_module_globals warns (compat only)."""
    import warnings
    from latticeai.services.model_runtime import STATE

    # STATE carries the values
    assert hasattr(STATE, "APP_MODE")
    assert hasattr(STATE, "PUBLIC_MODEL")

    # Calling the public sync emits DeprecationWarning
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always", DeprecationWarning)
        STATE.sync_to_module_globals()
        deprecation = [x for x in w if issubclass(x.category, DeprecationWarning)]
        assert len(deprecation) >= 1
        assert "legacy compatibility shim" in str(deprecation[0].message)

    # Globals are still populated for compat readers
    import latticeai.services.model_runtime as mr_mod
    assert mr_mod.PUBLIC_MODEL == STATE.PUBLIC_MODEL
