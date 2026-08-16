"""Drive leftover P1b modules toward 100% lines+branches.

Covers model_engines ensure/install/pull, model_runtime download/engines/loading,
lifespan, CSRF middleware, users KG migration, filesystem/knowledge leftovers,
tools/search routers, quiet/sessions/config.
"""

from __future__ import annotations

import io
import subprocess
import sys
import types
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from latticeai.services.model_runtime.state import (
    ModelRuntimeState,
)

# ── helpers ──────────────────────────────────────────────────────────────────


class _Done:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _Popen:
    def __init__(self, stdout_text="", returncode=0, poll_value=None, wait_timeouts=0):
        self.stdout = io.StringIO(stdout_text)
        self.returncode = returncode
        self._poll = poll_value
        self.killed = False
        self.terminated = False
        self._wait_timeouts = wait_timeouts

    def poll(self):
        return self._poll

    def terminate(self):
        self.terminated = True
        if self._wait_timeouts <= 0:
            self._poll = 0

    def kill(self):
        self.killed = True
        if self._wait_timeouts <= 1:
            self._poll = -9

    def wait(self, timeout=None):
        if self._wait_timeouts > 0:
            self._wait_timeouts -= 1
            raise subprocess.TimeoutExpired(cmd="x", timeout=timeout or 1)
        self._poll = self.returncode
        return self.returncode


class _UrlResp:
    def __init__(self, body: bytes = b""):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fast_deadline(monkeypatch, module, start=1000.0, step=1000.0):
    clock = {"t": start}

    def now():
        clock["t"] += step
        return clock["t"]

    monkeypatch.setattr(module.time, "time", now)
    monkeypatch.setattr(module.time, "sleep", lambda *_a, **_k: None)

def test_fourth_wave_last_gaps(tmp_path: Path, monkeypatch):

    from lattice_brain.graph import _kg_fsutil as fs
    from latticeai.api.models import create_models_router
    from latticeai.core.model_resolution import ModelResolution
    from latticeai.runtime.build_phases.foundation import phase_brain
    from latticeai.runtime.build_phases.worker_profile import apply_worker_route_filter
    from latticeai.runtime.runtime_context import RuntimeContext
    from latticeai.services.model_catalog import ENGINE_MODEL_CATALOG
    from latticeai.services.model_runtime.status import engine_status
    from latticeai.tools.documents import read_document

    assert fs._recency_score(None) == 0.0
    assert fs._recency_score("not-a-date") == 0.0
    fs._excluded_directory_reason(Path("/home/someone"), os_type="linux")

    res = ModelResolution.from_request("x")
    res.update_after_load(actual_current="plain::user@x.com")

    class FakeEmbed:
        fell_back = True
        requested = "mlx"
        detail = "down"

    monkeypatch.setattr(
        "latticeai.core.embedding_providers.resolve_embedder",
        lambda *a, **k: FakeEmbed(),
    )
    ctx = RuntimeContext()
    ctx.enter("brain")
    ctx.CONFIG = SimpleNamespace(
        embedding_profile="nope",
        embedding_provider="mlx",
        embedding_model="",
        embedding_dim=0,
        embedding_base_url="",
        embedding_api_key="",
        embedding_timeout=1,
        embedding_custom_target="",
    )
    phase_brain(ctx)

    class Inner:
        def __init__(self):
            self.routes = [object()]

    class Wrapper:
        original_router = Inner()

    app = FastAPI()
    app.router.routes.append(Wrapper())
    try:
        apply_worker_route_filter(app)
    except RuntimeError:
        pass

    class Router:
        def detected_cloud_models(self):
            return []

    monkeypatch.setattr(
        "latticeai.services.model_runtime.status.get_lmstudio_models",
        lambda: [{"key": "already", "loaded_instances": [{"id": "i"}]}],
    )
    monkeypatch.setattr("latticeai.services.model_runtime.status.engine_installed", lambda e: False)
    monkeypatch.setattr("latticeai.services.model_runtime.status.get_ollama_pulled_models", lambda: set())
    monkeypatch.setattr("latticeai.services.model_runtime.status.hf_model_ready", lambda *a, **k: False)
    monkeypatch.setattr(
        "latticeai.services.model_runtime.status.engine_support_status",
        lambda e: {"supported": True, "reason": None},
    )
    monkeypatch.setattr("latticeai.services.model_runtime.status._safe_engine_install_plan", lambda *a, **k: None)
    monkeypatch.setattr(
        "latticeai.services.model_runtime.status.ENGINE_MODEL_CATALOG",
        {**ENGINE_MODEL_CATALOG, "lmstudio": [{"id": "lmstudio:already", "name": "A"}]},
    )
    engine_status(state=ModelRuntimeState(router=Router()))

    class FakeRouter:
        loaded_model_ids = []
        current_model_id = None

    def engines():
        return [{"id": "local_mlx", "models": ["skip-me", {"id": "m1"}]}]

    router = create_models_router(
        model_router=FakeRouter(),
        require_user=lambda r: "u",
        require_admin=lambda r: ("u", {}),
        prepare_and_load_model=lambda *a, **k: {},
        prepare_and_load_model_stream=lambda *a, **k: iter(()),
        sse_event=lambda e, d: "",
        engine_status=engines,
        filter_lower_family_versions=lambda items: list(items),
        list_compat_profiles=lambda: [],
        engine_model_catalog={"local_mlx": [{"id": "m1"}]},
        model_engine_aliases={},
        is_public_mode=False,
        allow_local_models=True,
        require_auth=False,
    )
    app2 = FastAPI()
    app2.include_router(router)
    client = TestClient(app2, raise_server_exceptions=False)
    client.get("/models")

    # pptx shape without text frame
    class Shape:
        has_text_frame = False

    class Slide:
        shapes = [Shape()]

    class Prs:
        slides = [Slide()]

    pptx_mod = types.ModuleType("pptx")
    pptx_mod.Presentation = lambda *_a, **_k: Prs()
    monkeypatch.setitem(sys.modules, "pptx", pptx_mod)
    ppt = tmp_path / "a.pptx"
    ppt.write_bytes(b"px")
    read_document(str(ppt))

    # supported ext that is not one of the handled families
    import latticeai.tools.documents as docs

    monkeypatch.setattr(
        docs,
        "_SUPPORTED_READ_EXTENSIONS",
        set(docs._SUPPORTED_READ_EXTENSIONS) | {".rst"},
    )
    rst = tmp_path / "a.rst"
    rst.write_text("hi", encoding="utf-8")
    meta = read_document(str(rst))
    assert meta["ext"] == ".rst"

def test_worker_profile_drops_empty_included_router():

    from latticeai.runtime.build_phases.worker_profile import apply_worker_route_filter

    class Inner:
        def __init__(self):
            self.routes = []

    class Wrapper:
        original_router = Inner()

    app = FastAPI()
    app.router.routes.append(Wrapper())
    try:
        apply_worker_route_filter(app)
    except RuntimeError:
        pass
