"""v1.2.0 server_app modularization / startup-safety checks.

Verifies the historical ``server:app`` import path still works, that the
extracted health and workspace routers are registered, that key API paths
exist, and that importing the app has no circular-import surprises.
"""

import importlib

import pytest


@pytest.fixture(scope="module")
def app():
    server = importlib.import_module("server")
    return server.app


def test_server_app_import_path_preserved(app):
    # Historical `server:app` must remain importable for uvicorn/tests.
    assert app is not None
    assert type(app).__name__ == "FastAPI"


def test_latticeai_server_app_exposes_same_app():
    server = importlib.import_module("server")
    sa = importlib.import_module("latticeai.server_app")
    assert server.app is sa.app


def test_no_import_cycle_for_routers_and_services():
    # Routers/services import cleanly on their own (no dependency on the app).
    for mod in (
        "latticeai.api.workspace",
        "latticeai.api.health",
        "latticeai.services.workspace_service",
        "latticeai.services.model_service",
        "latticeai.services.chat_service",
    ):
        assert importlib.import_module(mod) is not None


def test_router_factories_present():
    from latticeai.api.workspace import create_workspace_router
    from latticeai.api.health import create_health_router

    assert callable(create_workspace_router)
    assert callable(create_health_router)


def _paths(app):
    return {getattr(r, "path", "") for r in app.routes}


def test_health_routes_registered(app):
    paths = _paths(app)
    for p in ("/health", "/mode", "/runtime_features", "/engines"):
        assert p in paths, f"missing health route {p}"


def test_workspace_routes_registered(app):
    paths = _paths(app)
    for p in (
        "/workspace",
        "/onboarding",
        "/workspace/os",
        "/workspace/memories",
        "/workspace/snapshots",
        "/workspace/registry",
        "/workspace/editions",
        "/workspace/activate",
        "/workspace/orgs",
        "/workspace/orgs/{workspace_id}",
        "/workspace/audit-timeline",
    ):
        assert p in paths, f"missing workspace route {p}"


def test_compatibility_routes_preserved(app):
    # Routes from the other extracted routers / legacy handlers still present.
    paths = _paths(app)
    for p in ("/status", "/chat", "/graph"):
        assert p in paths, f"missing compatibility route {p}"
