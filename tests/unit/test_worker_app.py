from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from latticeai import worker_app
from latticeai.runtime.build_phases import worker_profile
from latticeai.runtime.build_phases.worker_profile import (
    GRAPH_WRITER_ROUTES,
    WORKER_COMPUTE_ROUTES,
    WORKER_ROUTES,
    WORKER_SEAM_ROUTES,
    apply_worker_route_filter,
    phase_worker_routes,
    worker_route_keys,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _schema_paths() -> set:
    return {path.replace(":path}", "}") for _method, path in worker_route_keys()}


def test_the_keep_worker_surface_is_the_remaining_compute_and_status_routes():
    assert len(WORKER_ROUTES) == 9
    assert len(set(WORKER_ROUTES)) == 9
    assert len(GRAPH_WRITER_ROUTES) == 0
    assert len(WORKER_SEAM_ROUTES) == 2
    assert len(WORKER_COMPUTE_ROUTES) == 10
    assert len(worker_route_keys()) == 21


def test_the_new_seams_are_absent_from_the_committed_product_contract():
    schema = json.loads(
        (REPO_ROOT / "frontend/openapi.json").read_text(encoding="utf-8")
    )
    for _method, path in WORKER_SEAM_ROUTES + WORKER_COMPUTE_ROUTES:
        assert path not in schema["paths"]
    for _method, path in WORKER_ROUTES + GRAPH_WRITER_ROUTES:
        assert path.replace(":path}", "}") in schema["paths"], path


def _app_with(paths: List[Any], *, mount: bool = False) -> FastAPI:
    app = FastAPI()
    for method, path in paths:
        app.add_api_route(path, lambda: {"ok": True}, methods=[method])
    if mount:
        app.mount(
            "/static", StaticFiles(directory=str(REPO_ROOT / "static")), name="static"
        )
    return app


def _keys(app: FastAPI) -> set:
    from fastapi.routing import APIRoute

    def walk(routes: List[Any]) -> set:
        found: set = set()
        for route in routes:
            inner = getattr(route, "original_router", None)
            if inner is not None and hasattr(inner, "routes"):
                found |= walk(inner.routes)
            elif isinstance(route, APIRoute):
                found |= {(method, route.path) for method in route.methods}
        return found

    return walk(list(app.routes))


def test_the_filter_keeps_the_worker_surface_and_drops_the_product_one():
    product = [
        ("GET", "/api/command/briefing"),
        ("POST", "/chat"),
        ("GET", "/workspace"),
    ]
    app = _app_with(list(worker_route_keys()) + product)

    apply_worker_route_filter(app)

    assert _keys(app) == worker_route_keys()


def test_the_filter_drops_the_static_mounts_a_worker_serves_no_ui_from():
    from starlette.routing import Mount

    app = _app_with(list(worker_route_keys()), mount=True)
    assert any(isinstance(route, Mount) for route in app.routes)

    apply_worker_route_filter(app)

    assert not any(isinstance(route, Mount) for route in app.routes)


def test_the_filter_keeps_the_schema_route_the_openapi_composer_reads():
    app = _app_with(list(worker_route_keys()))

    apply_worker_route_filter(app)

    paths = {getattr(route, "path", None) for route in app.routes}
    assert "/openapi.json" in paths
    assert app.openapi_schema is None
    assert set(app.openapi()["paths"]) == _schema_paths()


def test_a_route_serving_an_unlisted_verb_on_a_listed_path_is_not_kept():
    app = _app_with(list(worker_route_keys()))
    app.add_api_route("/health", lambda: {"ok": True}, methods=["DELETE"])

    apply_worker_route_filter(app)

    assert ("DELETE", "/health") not in _keys(app)
    assert ("GET", "/health") in _keys(app)


def test_a_missing_worker_route_fails_the_boot_instead_of_shipping_a_gap():
    app = _app_with(
        [key for key in worker_route_keys() if key != ("POST", "/agent/llm")]
    )

    with pytest.raises(RuntimeError, match=r"POST /agent/llm"):
        apply_worker_route_filter(app)


def _fake_ctx(app: FastAPI) -> SimpleNamespace:
    return SimpleNamespace(
        app=app,
        require_user="require_user",
        enforce_rate_limit="rate_limit",
        model_router="router",
        EMBEDDER="embedder",
        MULTIMODAL_PORTS=SimpleNamespace(transcriber=None),
    )


def _worker_app_stub() -> FastAPI:
    mounted_by_the_phase = set(WORKER_SEAM_ROUTES) | set(WORKER_COMPUTE_ROUTES)
    app = _app_with(
        [key for key in worker_route_keys() if key not in mounted_by_the_phase]
        + [("GET", "/api/command/briefing")]
    )
    phase_worker_routes(_fake_ctx(app))
    return app


def test_the_phase_mounts_the_seams_and_reduces_the_surface():
    app = _worker_app_stub()

    assert _keys(app) == worker_route_keys()
    assert ("GET", "/api/command/briefing") not in _keys(app)


def test_the_phase_hands_the_seams_what_this_process_holds(monkeypatch):
    captured: Dict[str, Any] = {}
    real = worker_profile.__dict__["phase_worker_routes"]
    assert real is phase_worker_routes

    import latticeai.api.worker_seams as seams

    original = seams.create_worker_seams_router

    def _spy(**kwargs):
        captured.update(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(seams, "create_worker_seams_router", _spy)

    _worker_app_stub()

    assert captured["require_user"] == "require_user"
    assert captured["enforce_rate_limit"] == "rate_limit"
    assert captured["model_router"] == "router"


def test_create_worker_app_builds_the_context_then_applies_the_profile(monkeypatch):
    order: List[str] = []
    app = FastAPI()
    ctx = SimpleNamespace(app=app)

    def fake_build_context(config=None):
        order.append(f"build:{config}")
        return ctx

    def fake_phase(received):
        order.append("profile")
        assert received is ctx

    monkeypatch.setattr(worker_app, "build_context", fake_build_context)
    monkeypatch.setattr(worker_app, "phase_worker_routes", fake_phase)

    assert worker_app.create_worker_app("cfg") is app
    assert order == ["build:cfg", "profile"]


def test_main_serves_the_worker_profile_on_the_configured_address(monkeypatch):
    served: List[Dict[str, Any]] = []
    fake_uvicorn = SimpleNamespace(
        run=lambda app, **kwargs: served.append({"app": app, **kwargs})
    )
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)

    from latticeai.core.config import Config

    config = SimpleNamespace(host="127.0.0.1", port=4831)
    monkeypatch.setattr(Config, "from_env", classmethod(lambda cls: config))
    monkeypatch.setattr(
        worker_app, "create_worker_app", lambda cfg: f"app-for-{cfg.port}"
    )

    worker_app.main()

    assert served == [
        {"app": "app-for-4831", "host": "127.0.0.1", "port": 4831, "log_level": "info"}
    ]


def _sandbox_env(tmp_path: Path) -> dict:
    home = tmp_path / "home"
    home.mkdir()
    return {
        **os.environ,
        "HOME": str(home),
        "LATTICEAI_DATA_DIR": str(home / ".ltcai"),
        "LATTICEAI_AGENT_ROOT": str(home / "agent_workspace"),
        "LATTICEAI_BRAIN_DIR": str(home / ".ltcai-brain"),
        "LATTICEAI_ENABLE_TELEGRAM": "false",
        "LATTICEAI_AUTOLOAD_MODELS": "false",
        "PYTHONPATH": str(REPO_ROOT),
    }


def test_a_real_worker_boots_serves_health_and_mounts_only_its_own_surface(
    tmp_path: Path,
):
    code = """
import json

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from latticeai.worker_app import create_worker_app


def route_rows(routes):
    rows = []
    for route in routes:
        inner = getattr(route, "original_router", None)
        if inner is not None and hasattr(inner, "routes"):
            rows.extend(route_rows(inner.routes))
        elif isinstance(route, APIRoute):
            rows.extend([method, route.path] for method in route.methods)
    return rows


app = create_worker_app()
client = TestClient(app)
health = client.get("/health")
print(json.dumps({
    "routes": sorted(route_rows(app.routes)),
    "health_status": health.status_code,
    "access": health.json().get("access"),
    "product_route_status": client.get("/api/command/briefing").status_code,
    "schema_paths": sorted(client.get("/openapi.json").json()["paths"]),
}))
"""
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=_sandbox_env(tmp_path),
        timeout=300,
    )
    assert proc.returncode == 0, f"worker boot failed:\n{proc.stdout}\n{proc.stderr}"
    result = json.loads(proc.stdout.splitlines()[-1])

    assert {tuple(row) for row in result["routes"]} == worker_route_keys()
    assert result["health_status"] == 200
    assert set(result["access"]) == {"require_auth", "externally_reachable"}
    assert result["product_route_status"] == 404
    assert result["schema_paths"] == sorted(_schema_paths())


def test_worker_app_exposes_worker_route_contracts():
    assert len(WORKER_ROUTES) > 0
    assert len(WORKER_COMPUTE_ROUTES) > 0
    assert len(worker_route_keys()) == 21
