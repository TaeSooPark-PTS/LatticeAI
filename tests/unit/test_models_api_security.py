from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from latticeai.api.models import create_models_router
from latticeai.services.model_errors import ModelRuntimeError


class _FakeRouter:
    def __init__(self) -> None:
        self.loaded_model_ids = ["loaded-model"]
        self.current_model_id = "loaded-model"

    def detected_cloud_models(self):
        return []

    def switch_model(self, model_id):
        if model_id not in self.loaded_model_ids:
            raise KeyError(model_id)
        self.current_model_id = model_id

    def unload_model(self, model_id):
        self.loaded_model_ids = [item for item in self.loaded_model_ids if item != model_id]

    def unload_all(self):
        self.loaded_model_ids = []
        self.current_model_id = None


def _models_client(
    *,
    identity: str | None,
    role: str = "user",
    require_auth: bool = True,
    install_error: ModelRuntimeError | None = None,
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

    async def verify_cloud_models(**kwargs):
        calls.append(("verify", kwargs))
        return []

    async def prepare_and_load_model(model, _request, **kwargs):
        calls.append(("prepare", {"model": model, **kwargs}))
        return {"ok": True, "model": model, "user_email": kwargs.get("user_email")}

    async def prepare_and_load_model_stream(model, _request, **kwargs):
        calls.append(("prepare_stream", {"model": model, **kwargs}))
        yield "data: done\n\n"

    def install_engine(engine, **kwargs):
        if install_error is not None:
            raise install_error
        calls.append(("install", {"engine": engine, **kwargs}))
        return {}

    app = FastAPI()
    app.include_router(
        create_models_router(
            model_router=_FakeRouter(),
            require_user=require_user,
            require_admin=require_admin,
            get_current_user=lambda _request: identity,
            load_users=lambda: ({identity: {"role": role}} if identity else {}),
            get_user_role=lambda *_args, **_kwargs: role,
            install_engine=install_engine,
            verify_cloud_models=verify_cloud_models,
            normalize_local_model_request=lambda model, _engine=None: model,
            download_hf_model=lambda model, provider: calls.append(("pull", {"model": model, "provider": provider})) or {},
            prepare_and_load_model=prepare_and_load_model,
            prepare_and_load_model_stream=prepare_and_load_model_stream,
            sse_event=lambda event, data: f"event: {event}\ndata: {data}\n\n",
            ensure_ollama_server=lambda: None,
            local_binary=lambda _binary: None,
            engine_status=lambda: [],
            filter_lower_family_versions=lambda items: items,
            list_compat_profiles=lambda: [],
            set_user_api_key=lambda *args, **kwargs: calls.append(("set_key", {"args": args, **kwargs})),
            engine_model_catalog={"local_mlx": []},
            model_engine_aliases={},
            cloud_verify_ttl_seconds=600,
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
        ("post", "/engines/install", {"engine": "local_mlx"}),
        ("post", "/engines/verify-cloud", {}),
        ("post", "/engines/pull-model", {"model": "example/model", "allow_download": True}),
        ("post", "/engines/prepare-model", {"model": "example/model"}),
        ("post", "/engines/prepare-model/stream", {"model": "example/model"}),
        ("post", "/models/load", {"model_id": "openai:test"}),
        ("post", "/models/switch/loaded-model", None),
        ("delete", "/models/unload/loaded-model", None),
        ("delete", "/models/unload-all", None),
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
        ("/setup/set-api-key", {"provider": "openai", "key": "secret", "user_email": "victim@example.com"}),
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


def test_model_runtime_error_is_translated_only_at_http_boundary():
    client, calls = _models_client(
        identity="admin@example.com",
        role="admin",
        install_error=ModelRuntimeError(
            status_code=409,
            detail={"status": "confirmation_required"},
        ),
    )

    response = client.post("/engines/install", json={"engine": "local_mlx"})

    assert response.status_code == 409
    assert response.json()["detail"] == {"status": "confirmation_required"}
    assert calls == []
