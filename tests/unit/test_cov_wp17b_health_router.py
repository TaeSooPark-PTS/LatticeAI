"""wp17 (second pass) — the identified-caller branch of ``GET /health``.

An anonymous request against a server that requires auth gets the cheap base
payload and no engine sweep. As soon as the caller is identified — or the
server does not require auth at all — the router pays for the engine probe and
returns the full payload. Both directions are asserted here, since the second
one is what a signed-in dashboard actually renders.
"""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import FastAPI
from fastapi.testclient import TestClient

from latticeai.api.health import create_health_router


class _StatusService:
    """The projection ``server_app`` hands the router (see model_service.py)."""

    def health_base(self, *, version: str, mode: str) -> Dict[str, Any]:
        return {"status": "ok", "version": version, "mode": mode}

    def health_full(
        self, base: Dict[str, Any], engines: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        return {**base, "engines": engines, "current_model": "mlx:demo"}

    def runtime(self) -> Dict[str, Any]:
        return {"mode": "local", "graph_enabled": True}

    def engines_payload(self, engines: List[Dict[str, Any]]) -> Dict[str, Any]:
        return {"engines": engines, "current": "mlx:demo"}


ENGINES = [{"engine": "mlx", "installed": True}]


def _client(*, require_auth: bool):
    calls: Dict[str, int] = {"engine_status": 0}

    def engine_status() -> List[Dict[str, Any]]:
        calls["engine_status"] += 1
        return [dict(row) for row in ENGINES]

    app = FastAPI()
    app.include_router(
        create_health_router(
            model_service=_StatusService(),
            engine_status=engine_status,
            get_current_user=lambda request: request.headers.get("X-Test-User"),
            require_auth=require_auth,
            app_version="11.0.0-test",
            app_mode="local",
        )
    )
    return TestClient(app), calls


def test_health_for_an_identified_caller_includes_the_engine_sweep():
    client, calls = _client(require_auth=True)

    response = client.get("/health", headers={"X-Test-User": "user@example.com"})

    assert response.status_code == 200
    body = response.json()
    assert body["engines"] == ENGINES
    assert body["current_model"] == "mlx:demo"
    assert body["version"] == "11.0.0-test"
    assert body["mode"] == "local"
    assert calls["engine_status"] == 1


def test_health_without_auth_requirement_is_full_even_when_anonymous():
    client, calls = _client(require_auth=False)

    body = client.get("/health").json()

    assert body["engines"] == ENGINES
    assert calls["engine_status"] == 1


def test_health_for_an_anonymous_caller_skips_the_engine_sweep():
    client, calls = _client(require_auth=True)

    body = client.get("/health").json()

    assert body == {"status": "ok", "version": "11.0.0-test", "mode": "local"}
    assert "engines" not in body
    assert calls["engine_status"] == 0
