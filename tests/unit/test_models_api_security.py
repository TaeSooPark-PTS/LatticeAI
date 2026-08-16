from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from latticeai.api.models import create_models_router


class _FakeRouter:
    def __init__(self) -> None:
        self.loaded_model_ids = ["loaded-model"]
        self.current_model_id = "loaded-model"

    def detected_cloud_models(self):
        return []

    def unload_model(self, model_id):
        self.loaded_model_ids = [item for item in self.loaded_model_ids if item != model_id]


def _models_client(
    *,
    identity: str | None,
    role: str = "user",
    require_auth: bool = True,
):
    calls = []

    def require_user(_request: Request) -> str:
        if require_auth and not identity:
            raise HTTPException(status_code=401, detail="auth required")
        return identity or ""

    def require_admin(_request: Request):
        if role != "admin":
            raise HTTPException(status_code=403, detail="admin required")
        return identity or "", {}

    async def prepare_and_load_model(model, _request, **kwargs):
        calls.append(("prepare", {"model": model, **kwargs}))
        return {"ok": True, "model": model, "user_email": kwargs.get("user_email")}

    async def prepare_and_load_model_stream(model, _request, **kwargs):
        calls.append(("prepare_stream", {"model": model, **kwargs}))
        yield "data: done\n\n"

    app = FastAPI()
    app.include_router(
        create_models_router(
            model_router=_FakeRouter(),
            require_user=require_user,
            require_admin=require_admin,
            prepare_and_load_model=prepare_and_load_model,
            prepare_and_load_model_stream=prepare_and_load_model_stream,
            sse_event=lambda event, data: f"event: {event}\ndata: {data}\n\n",
            engine_status=lambda: [],
            filter_lower_family_versions=lambda items: items,
            list_compat_profiles=lambda: [],
            engine_model_catalog={"local_mlx": []},
            model_engine_aliases={},
            is_public_mode=False,
            allow_local_models=True,
            require_auth=require_auth,
        )
    )
    return TestClient(app), calls


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("get", "/models", None),
        ("post", "/models/load", {"model_id": "openai:test"}),
    ],
)
def test_model_endpoints_reject_anonymous_callers(method, path, payload):
    client, _calls = _models_client(identity=None)

    response = client.request(method, path, json=payload)

    assert response.status_code == 401


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("get", "/models", None),
        ("post", "/engines/prepare-model", {"model": "example/model"}),
        ("post", "/engines/prepare-model/stream", {"model": "example/model"}),
        ("post", "/models/load", {"model_id": "openai:test"}),
        ("delete", "/models/unload/loaded-model", None),
    ],
)
def test_model_inventory_and_lifecycle_are_admin_only_in_auth_mode(method, path, payload):
    client, calls = _models_client(identity="member@example.com", role="user")

    response = client.request(method, path, json=payload)

    assert response.status_code == 403
    assert calls == []


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/engines/prepare-model", {"model": "example/model", "user_email": "victim@example.com"}),
        ("/engines/prepare-model/stream", {"model": "example/model", "user_email": "victim@example.com"}),
        ("/models/load", {"model_id": "openai:test", "user_email": "victim@example.com"}),
    ],
)
def test_body_identity_cannot_override_logged_in_admin(path, payload):
    client, calls = _models_client(identity="admin@example.com", role="admin")

    response = client.post(path, json=payload)

    assert response.status_code == 403
    assert calls == []


def test_prepare_uses_authenticated_identity_downstream():
    client, calls = _models_client(identity="admin@example.com", role="admin")

    response = client.post(
        "/engines/prepare-model",
        json={"model": "example/model", "user_email": "ADMIN@example.com"},
    )

    assert response.status_code == 200
    assert response.json()["user_email"] == "admin@example.com"
    assert calls == [
        (
            "prepare",
            {
                "model": "example/model",
                "engine": None,
                "user_email": "admin@example.com",
                "allow_download": False,
            },
        )
    ]
