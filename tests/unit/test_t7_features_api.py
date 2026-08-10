"""v11.2.0 T7 — the two routes and the wiring that binds them to real gates.

The router is deliberately thin: it holds no list of features, no labels, no
defaults. Everything a client renders comes from the service, so these tests
assert the *contract* (what the payload must contain, which failures are 400s,
which language answered) rather than re-asserting the catalog.

The wiring tests cover the part that is easy to get wrong twice: a singleton
that a lazy first caller could pin to the wrong data dir, and a router mount
that a re-entrant app factory could duplicate.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, List

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from latticeai.api.features import create_features_router  # noqa: E402
from latticeai.runtime import feature_toggle_wiring as ftw  # noqa: E402
from latticeai.services.feature_toggles import (  # noqa: E402
    CATALOG,
    FeatureToggleService,
)

ENV_VARS = tuple(item.env_var for item in CATALOG)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    """No shell config decides these answers, and no binding outlives a test."""
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    ftw.reset_feature_toggle_service()
    yield
    ftw.reset_feature_toggle_service()


def _service(tmp_path: Path) -> FeatureToggleService:
    return FeatureToggleService(
        data_dir=tmp_path, probes={"hnsw": lambda: (False, "no hnswlib here")}
    )


def _client(service: FeatureToggleService) -> TestClient:
    app = FastAPI()
    app.include_router(
        create_features_router(service=service, require_user=lambda _r: "me@local")
    )
    return TestClient(app)


# ── GET /api/features ────────────────────────────────────────────────────────
def test_the_catalog_route_answers_with_every_switch_and_its_live_value(tmp_path):
    body = _client(_service(tmp_path)).get("/api/features").json()

    assert [item["id"] for item in body["features"]] == [item.id for item in CATALOG]
    multimodal = body["features"][0]
    assert multimodal["current"] is False and multimodal["source"] == "default"
    assert body["note"]


def test_the_catalog_speaks_the_language_the_request_asked_for(tmp_path):
    client = _client(_service(tmp_path))

    korean = client.get("/api/features").json()["features"][0]["label"]
    english = client.get(
        "/api/features", headers={"x-lattice-language": "en"}
    ).json()["features"][0]["label"]

    assert korean != english


# ── POST /api/features/{id} ──────────────────────────────────────────────────
def test_moving_a_switch_persists_it_and_answers_with_the_new_state(tmp_path):
    client = _client(_service(tmp_path))

    response = client.post("/api/features/vault_watch", json={"value": True})

    assert response.status_code == 200
    assert response.json()["current"] is True
    assert response.json()["source"] == "user"
    reread = client.get("/api/features").json()["features"]
    assert next(item for item in reread if item["id"] == "vault_watch")["current"]


def test_choosing_an_option_is_the_same_route_with_a_string(tmp_path):
    client = _client(_service(tmp_path))

    body = client.post("/api/features/vector_backend", json={"value": "quantized"}).json()

    assert body["current"] == "quantized"


def test_a_feature_this_build_does_not_have_is_a_400_that_names_it(tmp_path):
    response = _client(_service(tmp_path)).post(
        "/api/features/teleportation",
        json={"value": True},
        headers={"x-lattice-language": "en"},
    )

    assert response.status_code == 400
    assert "teleportation" in response.json()["detail"]


def test_a_value_the_feature_cannot_take_is_a_400_that_keeps_the_reason(tmp_path):
    client = _client(_service(tmp_path))

    wrong_type = client.post("/api/features/allow_multimodal", json={"value": "later"})
    not_installed = client.post(
        "/api/features/vector_backend",
        json={"value": "hnsw"},
        headers={"x-lattice-language": "en"},
    )

    assert wrong_type.status_code == 400
    assert "later" in wrong_type.json()["detail"]
    assert not_installed.status_code == 400
    # The reason survives the trip: "install required", plus what the import said.
    assert "Install required" in not_installed.json()["detail"]
    assert "no hnswlib here" in not_installed.json()["detail"]


# ── the singleton ────────────────────────────────────────────────────────────
def test_the_default_data_dir_follows_the_env_then_home(monkeypatch, tmp_path):
    monkeypatch.setenv("LATTICEAI_DATA_DIR", str(tmp_path))
    assert ftw._default_data_dir() == tmp_path

    monkeypatch.delenv("LATTICEAI_DATA_DIR", raising=False)
    assert ftw._default_data_dir() == Path.home() / ".ltcai"


def test_an_early_lazy_caller_does_not_pin_the_store_to_the_fallback(tmp_path):
    """The failure this guards: a gate asked before routers mount."""
    early = ftw.get_feature_toggle_service()
    assert early.path.parent != tmp_path

    events: List[Any] = []
    later = ftw.get_feature_toggle_service(
        data_dir=tmp_path, audit=lambda name, **payload: events.append(name)
    )

    assert later is early
    assert later.path.parent == tmp_path
    later.set("vault_watch", True)
    assert events == ["feature_toggle_changed"]
    # Arguments left out keep what the service already had.
    assert ftw.get_feature_toggle_service() is early
    assert early.path.parent == tmp_path


# ── binding: the step that turns a stored preference into behaviour ──────────
def test_binding_points_every_catalogued_gate_at_the_service(tmp_path):
    service = _service(tmp_path)
    ftw.bind_feature_gates(service)

    report = {
        feature_id: gate.describe() for feature_id, gate in ftw._boolean_gates()
    }

    assert set(report) == {feature_id for feature_id, _m, _a in ftw.GATE_BINDINGS}
    assert all(entry["source"] == "resolver" for entry in report.values())
    # Every bound gate still answers with its own default until someone moves it.
    assert report["allow_multimodal"]["enabled"] is False
    assert report["synthesis"]["enabled"] is True


def test_binding_without_an_explicit_service_uses_the_singleton(tmp_path):
    ftw.get_feature_toggle_service(data_dir=tmp_path)
    ftw.bind_feature_gates()

    from lattice_brain.ingestion import MULTIMODAL_GATE

    assert MULTIMODAL_GATE.source() == "resolver"


def test_unbinding_hands_every_gate_back_to_its_environment(tmp_path, monkeypatch):
    from lattice_brain.graph.vector_index.selector import resolve_vector_index
    from lattice_brain.ingestion import MULTIMODAL_GATE

    service = _service(tmp_path)
    service.set("allow_multimodal", True)
    service.set("vector_backend", "quantized")
    ftw.bind_feature_gates(service)
    assert MULTIMODAL_GATE.enabled() is True
    assert resolve_vector_index().name == "quantized"

    ftw.unbind_feature_gates()

    monkeypatch.setenv("LATTICEAI_ALLOW_MULTIMODAL", "1")
    assert MULTIMODAL_GATE.source() == "env"
    assert MULTIMODAL_GATE.enabled() is True
    assert resolve_vector_index().name == "brute"


def test_a_resolver_with_no_opinion_still_falls_through_to_the_environment(
    monkeypatch,
):
    from lattice_brain.graph.vector_index.selector import (
        bind_vector_index_resolver,
        resolve_vector_index,
    )

    monkeypatch.setenv("LATTICEAI_VECTOR_INDEX", "quantized")
    bind_vector_index_resolver(lambda: None)
    try:
        assert resolve_vector_index().name == "quantized"
    finally:
        bind_vector_index_resolver(None)


def test_resetting_drops_the_singleton_and_its_bindings(tmp_path):
    from lattice_brain.ingestion import MULTIMODAL_GATE

    first = ftw.get_feature_toggle_service(data_dir=tmp_path)
    ftw.bind_feature_gates()

    ftw.reset_feature_toggle_service()

    assert MULTIMODAL_GATE.source() == "default"
    assert ftw.get_feature_toggle_service(data_dir=tmp_path) is not first


# ── mounting ─────────────────────────────────────────────────────────────────
def _feature_route_paths(app: Any) -> List[str]:
    """Every mounted ``/api/features*`` path, on either fastapi generation.

    fastapi >= 0.140 wraps an included router in an opaque entry that has no
    flat ``path`` attribute at all, keeping the real ``APIRouter`` on
    ``original_router`` (this repo's routers bake their full paths, so no
    prefix re-joining is needed); older fastapi puts the ``APIRoute``s straight
    on ``app.routes``. Walking both is the wp36 idiom — see
    ``test_cov_wp36_build_phases_late._flat_routes``.

    Returns a list rather than a set on purpose: *counting* is the assertion,
    since the failure this test guards against is one mount becoming two.
    """
    found: List[str] = []

    def walk(routes: Any) -> None:
        for route in routes:
            path = getattr(route, "path", None)
            if path and str(path).startswith("/api/features"):
                found.append(str(path))
            original = getattr(route, "original_router", None)
            if original is not None:
                walk(original.routes)

    walk(app.routes)
    return found


def test_mounting_is_idempotent_so_a_re_entrant_factory_cannot_duplicate_it(
    tmp_path,
):
    app = FastAPI()

    first = ftw.register_features_router(
        app, require_user=lambda _r: "me@local", data_dir=tmp_path
    )
    second = ftw.register_features_router(app, require_user=lambda _r: "me@local")

    assert first is second
    assert app.state._ltcai_features_mounted is True
    # Exactly one of each: a second mount would make this four entries.
    assert sorted(_feature_route_paths(app)) == [
        "/api/features",
        "/api/features/{feature_id}",
    ]
    assert TestClient(app).get("/api/features").status_code == 200


def test_an_app_without_a_state_object_still_gets_its_routes(tmp_path):
    """A stub host (a test double, an embedded mount) has no ``app.state``."""

    class _App:
        def __init__(self) -> None:
            self.routers: List[Any] = []

        def include_router(self, router: Any) -> None:
            self.routers.append(router)

    app = _App()
    ftw.register_features_router(
        app, require_user=lambda _r: "me@local", data_dir=tmp_path
    )
    ftw.register_features_router(app, require_user=lambda _r: "me@local")

    assert len(app.routers) == 2
