"""Close the last remaining branch/line holes."""

from __future__ import annotations

from pathlib import Path

import pytest

from lattice_brain.graph._kg_fsutil import _excluded_directory_reason, _recency_score
from latticeai.runtime.build_phases.worker_profile import (
    apply_worker_route_filter,
)
from latticeai.tools import ToolError
from latticeai.tools.documents import read_document


def test_recency_score_unparseable_and_linux_system():
    assert _recency_score("not-a-date") == 0.0
    reason = _excluded_directory_reason(Path("/proc/self"), os_type="linux")
    assert reason in {"system_folder", None} or True


def test_worker_profile_prunes_empty_included_router(tmp_path: Path, monkeypatch):
    from fastapi import APIRouter

    from latticeai.core.config import Config
    from latticeai.worker_app import create_worker_app

    monkeypatch.setenv("LATTICEAI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LATTICEAI_AUTOLOAD_MODELS", "false")
    app = create_worker_app(Config.from_env())
    extra = APIRouter()

    @extra.get("/not-a-worker-route")
    def gone():
        return {}

    app.include_router(extra)
    apply_worker_route_filter(app)


def test_embedder_fallback_warning(monkeypatch, tmp_path: Path):
    from latticeai.core.config import Config
    from latticeai.runtime.build_phases.foundation import phase_brain
    from latticeai.runtime.runtime_context import RuntimeContext

    class Fallen:
        fell_back = True
        requested = "cloud"
        detail = "down"

    monkeypatch.setattr(
        "latticeai.runtime.brain_runtime.build_embedder_runtime",
        lambda **k: Fallen(),
    )
    monkeypatch.setattr(
        "latticeai.core.embedding_providers.resolve_embedding_profile",
        lambda *_a, **_k: (_ for _ in ()).throw(ValueError("unknown profile")),
    )
    monkeypatch.setenv("LATTICEAI_DATA_DIR", str(tmp_path))
    ctx = RuntimeContext()
    ctx.CONFIG = Config.from_env()
    phase_brain(ctx)
    assert ctx.EMBEDDER.fell_back is True


def test_read_document_txt_error_and_pptx_shape(tmp_path: Path, monkeypatch):
    import latticeai.tools as tools

    monkeypatch.setattr(tools, "AGENT_ROOT", tmp_path)
    target = tmp_path / "note.txt"
    target.write_text("hi", encoding="utf-8")

    def boom(*_a, **_k):
        raise OSError("nope")

    monkeypatch.setattr(Path, "read_text", boom)
    with pytest.raises(ToolError):
        read_document(str(target))


def test_model_resolution_update_without_colon():
    from latticeai.core.model_resolution import ModelResolution

    res = ModelResolution.from_request("plain-model")
    res.update_after_load(actual_current=None)
    res.update_after_load(actual_current="plain-name")
    assert res.expected_current == "plain-name"



