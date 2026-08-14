"""Coverage for model_runtime download / loading / service leftovers."""

from __future__ import annotations

import time
from pathlib import Path

from latticeai.services.model_runtime.download import (
    download_hf_model,
    estimate_eta_seconds,
    hf_model_ready,
    model_download_progress_payload,
)
from latticeai.services.model_runtime.loading import (
    _resolve_model_alias,
    normalize_local_model_request,
    sse_event,
)
from latticeai.services.model_runtime.service import (
    ModelRuntimeService,
    build_model_runtime,
)
from latticeai.services.model_runtime.state import (
    ModelRuntimeState,
    create_model_runtime_state,
)


def test_hf_model_ready_and_progress(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "latticeai.services.model_runtime.download.hf_model_dir",
        lambda repo: tmp_path / "missing",
    )
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert hf_model_ready("org/model") is False

    model_dir = tmp_path / "ready"
    model_dir.mkdir()
    (model_dir / "config.json").write_text("{}")
    (model_dir / "model.safetensors").write_bytes(b"x")
    (model_dir / "tokenizer.json").write_text("{}")
    monkeypatch.setattr(
        "latticeai.services.model_runtime.download.hf_model_dir",
        lambda repo: model_dir,
    )
    assert hf_model_ready("org/model") is True

    gguf = tmp_path / "gguf"
    gguf.mkdir()
    (gguf / "w.gguf").write_bytes(b"g")
    monkeypatch.setattr(
        "latticeai.services.model_runtime.download.hf_model_dir",
        lambda repo: gguf,
    )
    assert hf_model_ready("org/model", provider="llamacpp") is True

    payload = model_download_progress_payload(
        "fetch",
        "downloading",
        percent=50,
        detail="file",
        downloaded_bytes=10,
        total_bytes=20,
        eta_seconds=3,
        file="a.bin",
    )
    assert payload["percent"] == 50
    assert estimate_eta_seconds(time.time() - 10, 50) is not None
    assert estimate_eta_seconds(time.time(), 0) is None
    assert estimate_eta_seconds(time.time(), 100) is None

    monkeypatch.setattr(
        "latticeai.services.model_runtime.download.hf_model_ready",
        lambda *a, **k: True,
    )
    monkeypatch.setattr(
        "latticeai.services.model_runtime.download.hf_model_dir",
        lambda repo: tmp_path / "ready",
    )
    monkeypatch.setattr(
        "latticeai.services.model_runtime.download.hf_cache_model_dir",
        lambda repo: tmp_path / "ready",
    )
    seen = []
    cached = download_hf_model("org/model", progress_emit=seen.append)
    assert cached["cached"] is True
    assert seen


def test_loading_alias_and_sse():
    assert isinstance(_resolve_model_alias("foo"), str)
    assert isinstance(normalize_local_model_request("foo"), str)
    frame = sse_event("done", {"ok": True})
    assert "event: done" in frame


def test_model_runtime_service_binds_state():
    state = create_model_runtime_state()
    assert isinstance(state, ModelRuntimeState)
    service = ModelRuntimeService(state=state)
    assert service.state is state
    built = build_model_runtime(router=object(), APP_MODE="full", DEFAULT_HOST="127.0.0.1", DEFAULT_PORT=1, DATA_DIR=Path("."), BASE_DIR=Path("."), ENABLE_GRAPH=False, AUTOLOAD_MODELS=False, MODEL_IDLE_UNLOAD_SECONDS=0, ALLOW_MODEL_DOWNLOADS=False, MODEL_DOWNLOAD_TIMEOUT=1, ALLOW_LOCAL_MODELS=True, REQUIRE_AUTH=False, ALLOW_PLAINTEXT_API_KEYS=False, CORS_ALLOW_NETWORK=False, PUBLIC_MODEL="", LOCAL_MODEL="", IS_PUBLIC_MODE=False, keyring=None, get_current_user=lambda r: None)
    assert built is not None
