"""The worker profile: the surface an AI-Worker process serves, and no more.

v11.6.0 inverts the gateway's proxy fall-through into an allowlist, and the
allowlist is only as good as the claim that Python serves nothing else. So the
assertions here are about the *route table*: the 25 KEEP_WORKER routes plus the
graph single-writer door plus the four ``/worker`` seams, exactly, with the
product surface gone.

Two levels, deliberately:

* the filter and the factory are exercised in-process over a synthetic route
  table, because a real ``create_worker_app()`` assembles a second full runtime
  and writes into the developer's data directory — which no unit test may do
  (the reason ``test_app_factory.py`` runs the factory in a subprocess);
* one sandboxed subprocess boots the real thing, because a contract about the
  routes construction produces cannot be proven by a table a test wrote.
"""

from __future__ import annotations

import dataclasses
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
    build_worker_history_deps,
    phase_worker_routes,
    worker_route_keys,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _schema_paths() -> set:
    """The worker's paths as OpenAPI writes them — converters are not in the spec."""
    return {path.replace(":path}", "}") for _method, path in worker_route_keys()}


# ── the contract, as data ───────────────────────────────────────────────────


def test_the_keep_worker_surface_is_the_remaining_compute_and_status_routes():
    # W3b retired drain/rebuild/create_*/upload/voice/ingest/mutate.
    assert len(WORKER_ROUTES) == 17
    assert len(set(WORKER_ROUTES)) == 17
    assert len(GRAPH_WRITER_ROUTES) == 0
    assert len(WORKER_SEAM_ROUTES) == 3
    assert len(WORKER_COMPUTE_ROUTES) == 8
    assert len(worker_route_keys()) == 28


def test_the_new_seams_are_absent_from_the_committed_product_contract():
    """``create_app`` must not mount them: the OpenAPI spec is byte-unchanged."""
    schema = json.loads((REPO_ROOT / "frontend/openapi.json").read_text(encoding="utf-8"))
    for _method, path in WORKER_SEAM_ROUTES + WORKER_COMPUTE_ROUTES:
        assert path not in schema["paths"]
    # …and every route the worker keeps *is* a product route today, so the
    # allowlist cannot name a path the gateway will never see.
    for _method, path in WORKER_ROUTES + GRAPH_WRITER_ROUTES:
        assert path.replace(":path}", "}") in schema["paths"], path


# ── the filter ──────────────────────────────────────────────────────────────


def _app_with(paths: List[Any], *, mount: bool = False) -> FastAPI:
    """A FastAPI app carrying exactly the given ``(method, path)`` routes."""
    app = FastAPI()
    for method, path in paths:
        app.add_api_route(path, lambda: {"ok": True}, methods=[method])
    if mount:
        app.mount("/static", StaticFiles(directory=str(REPO_ROOT / "static")), name="static")
    return app


def _keys(app: FastAPI) -> set:
    from fastapi.routing import APIRoute

    return {
        (method, route.path)
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in route.methods
    }


def test_the_filter_keeps_the_worker_surface_and_drops_the_product_one():
    product = [("GET", "/api/command/briefing"), ("POST", "/chat"), ("GET", "/workspace")]
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
    """Keep-if-any would let a second, unreviewed verb ride in on the path."""
    app = _app_with(list(worker_route_keys()))
    app.add_api_route("/health", lambda: {"ok": True}, methods=["DELETE"])

    apply_worker_route_filter(app)

    assert ("DELETE", "/health") not in _keys(app)
    assert ("GET", "/health") in _keys(app)


def test_a_missing_worker_route_fails_the_boot_instead_of_shipping_a_gap():
    app = _app_with([key for key in worker_route_keys() if key != ("POST", "/agent/llm")])

    with pytest.raises(RuntimeError, match=r"POST /agent/llm"):
        apply_worker_route_filter(app)


# ── the history bundle ──────────────────────────────────────────────────────


def _fake_ctx(app: FastAPI, *, enable_graph: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        app=app,
        CONVERSATIONS="conversations",
        append_audit_event="audit",
        classify_sensitive_message="classify",
        redact_secret_text="redact",
        INGESTION_PIPELINE="pipeline",
        ENABLE_GRAPH=enable_graph,
        KNOWLEDGE_GRAPH="graph",
        require_user="require_user",
        enforce_rate_limit="rate_limit",
    )


def test_the_history_bundle_wires_every_dependency_the_chain_needs():
    """A field left at its default is a step of the chain silently skipped."""
    deps = build_worker_history_deps(_fake_ctx(FastAPI()))

    values = {field.name: getattr(deps, field.name) for field in dataclasses.fields(deps)}
    assert values["conversations"] == "conversations"
    assert values["append_audit_event"] == "audit"
    assert values["classify_sensitive_message"] == "classify"
    assert values["redact_secret_text"] == "redact"
    assert values["ingestion_pipeline"] == "pipeline"
    assert values["enable_graph"] is True
    assert values["knowledge_graph"] == "graph"
    assert callable(values["normalize_branding"])
    assert callable(values["ingestion_item_factory"])
    assert None not in values.values()


# ── the phase ───────────────────────────────────────────────────────────────


def _worker_app_stub(**ctx_kwargs) -> FastAPI:
    """A context whose app already carries the 26 routes construction produces."""
    mounted_by_the_phase = set(WORKER_SEAM_ROUTES) | set(WORKER_COMPUTE_ROUTES)
    app = _app_with(
        [key for key in worker_route_keys() if key not in mounted_by_the_phase]
        + [("GET", "/api/command/briefing")]
    )
    phase_worker_routes(_fake_ctx(app, **ctx_kwargs))
    return app


def test_the_phase_mounts_the_three_seams_and_reduces_the_surface():
    app = _worker_app_stub()

    assert _keys(app) == worker_route_keys()
    assert ("GET", "/api/command/briefing") not in _keys(app)


def test_a_worker_with_the_graph_off_gets_no_store_to_delegate_writes_to(monkeypatch):
    captured: Dict[str, Any] = {}
    real = worker_profile.__dict__["phase_worker_routes"]
    assert real is phase_worker_routes  # the phase under test, not a stand-in

    import latticeai.api.worker_seams as seams

    original = seams.create_worker_seams_router

    def _spy(**kwargs):
        captured.update(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(seams, "create_worker_seams_router", _spy)

    _worker_app_stub(enable_graph=False)

    assert captured["graph_store"] is None
    assert captured["require_user"] == "require_user"
    assert captured["enforce_rate_limit"] == "rate_limit"

    captured.clear()
    _worker_app_stub(enable_graph=True)
    assert captured["graph_store"] == "graph"


# ── the factory ─────────────────────────────────────────────────────────────


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
    fake_uvicorn = SimpleNamespace(run=lambda app, **kwargs: served.append({"app": app, **kwargs}))
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)

    from latticeai.core.config import Config

    config = SimpleNamespace(host="127.0.0.1", port=4831)
    monkeypatch.setattr(Config, "from_env", classmethod(lambda cls: config))
    monkeypatch.setattr(worker_app, "create_worker_app", lambda cfg: f"app-for-{cfg.port}")

    worker_app.main()

    assert served == [
        {"app": "app-for-4831", "host": "127.0.0.1", "port": 4831, "log_level": "info"}
    ]


# ── the real thing, once ────────────────────────────────────────────────────


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


def test_a_real_worker_boots_serves_health_and_mounts_only_its_own_surface(tmp_path: Path):
    code = """
import json

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from latticeai.worker_app import create_worker_app

app = create_worker_app()
client = TestClient(app)
health = client.get("/health")
print(json.dumps({
    "routes": sorted(
        [method, route.path]
        for route in app.routes if isinstance(route, APIRoute)
        for method in route.methods
    ),
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
    # /health is the supervisor's boot gate and its posture source: lattice-host
    # gates every native /rust/* lane on exactly these two fields.
    assert result["health_status"] == 200
    assert set(result["access"]) == {"require_auth", "externally_reachable"}
    assert result["product_route_status"] == 404
    assert result["schema_paths"] == sorted(_schema_paths())
