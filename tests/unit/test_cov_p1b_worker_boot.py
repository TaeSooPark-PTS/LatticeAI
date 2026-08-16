"""In-process worker boot + keep-set helpers.

Subprocess factory tests do not count toward coverage. This module boots
``create_worker_app`` in-process against a throwaway data dir and drives
every allowlisted route plus the 0% helper modules the filter never reaches.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from latticeai.core.config import Config
from latticeai.worker_app import create_worker_app


@pytest.fixture
def worker_env(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    data = tmp_path / "data"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("LATTICEAI_DATA_DIR", str(data))
    monkeypatch.setenv("LATTICEAI_AGENT_ROOT", str(tmp_path / "agent"))
    monkeypatch.setenv("LATTICEAI_BRAIN_DIR", str(tmp_path / "brain"))
    monkeypatch.setenv("LATTICEAI_AUTOLOAD_MODELS", "false")
    monkeypatch.setenv("LATTICEAI_REQUIRE_AUTH", "false")
    monkeypatch.delenv("LATTICEAI_AGENT_TOOL_SEAM", raising=False)
    return tmp_path


@pytest.fixture
def worker(worker_env):
    app = create_worker_app(Config.from_env())
    return TestClient(app, raise_server_exceptions=False)


def test_in_process_worker_serves_health(worker):
    response = worker.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"]
    assert "access" in body


def test_in_process_worker_hits_every_product_get(worker):
    for path in (
        "/health",
        "/models",
        "/api/embeddings/status",
    ):
        assert worker.get(path).status_code < 500, path


def test_in_process_worker_answers_404_for_the_routes_v11_8_0_removed(worker):
    """A deleted route is gone from the built app, not merely unmounted."""
    for path in (
        "/api/embeddings/providers",
        "/api/ingestion/multimodal",
        "/api/capture/voice/status",
        "/tools/pdf_pages",
    ):
        assert worker.get(path).status_code == 404, path


def test_in_process_worker_closed_seams_are_404(worker):
    assert worker.get("/worker/sysinfo").status_code == 404
    assert worker.post("/agent/tool", json={"tool": "read_file"}).status_code == 404


def test_in_process_worker_llm_seam_is_open_for_completion(worker):
    response = worker.post("/agent/llm", json={"message": "hi"})
    assert response.status_code < 500


def test_build_session_runtime_reads_tokens(tmp_path: Path):
    from latticeai.runtime.bootstrap import build_session_runtime

    runtime = build_session_runtime(user_id_resolver=lambda email: f"id:{email}")
    store = runtime["_session_store"]
    token = store.create("owner@example.com")
    assert runtime["get_session_email"](token) == "owner@example.com"
    assert runtime["get_session_email"]("missing") is None


def test_build_embedder_runtime_uses_the_profile(monkeypatch):
    from latticeai.runtime.brain_runtime import build_embedder_runtime

    seen = {}

    def resolve(provider, **kwargs):
        seen["provider"] = provider
        seen.update(kwargs)
        return SimpleNamespace(provider=provider)

    config = SimpleNamespace(
        embedding_provider="hash",
        embedding_model="",
        embedding_dim=0,
        embedding_profile="default",
        embedding_base_url="",
        embedding_api_key="",
        embedding_timeout=5,
        embedding_custom_target="",
    )
    result = build_embedder_runtime(
        config=config,
        profile={"model": "m", "dimensions": 8, "provider": "hash"},
        resolve_embedder=resolve,
    )
    assert result.provider == "hash"
    assert seen["model"] == "m"
    assert seen["dim"] == 8
    assert seen["probe"] is False


def test_build_model_service_and_payloads():
    from latticeai.runtime.platform_services_runtime import build_model_service
    from latticeai.services.model_service import ModelService

    router = SimpleNamespace(
        current_model_id="local",
        loaded_model_ids=["local"],
        detected_cloud_models=lambda: [{"id": "cloud"}],
    )
    service = build_model_service(
        model_router=router,
        runtime_features=lambda: {"voice": False},
        is_public=False,
    )
    assert isinstance(service, ModelService)
    base = service.health_base(version="11.5.2", mode="full")
    assert base["status"] == "ok"
    full = service.health_full(base, [{"id": "mlx"}])
    assert full["current_model"] == "local"
    assert full["engines"] == [{"id": "mlx"}]
    assert service.engines_payload([{"id": "mlx"}])["current"] == "local"
    assert service.runtime() == {"voice": False}

    public = ModelService(
        model_router=router,
        runtime_features=lambda: {},
        is_public=True,
    )
    assert "Public" in public.health_full(base, [])["device"]


def test_multimodal_ports_default_off(monkeypatch):
    from latticeai.services.multimodal_ports import (
        build_multimodal_ports,
        describe_multimodal,
        multimodal_enabled,
        text_to_image_port,
    )

    monkeypatch.delenv("LATTICEAI_ALLOW_MULTIMODAL", raising=False)
    monkeypatch.delenv("LATTICEAI_VISION_PROVIDER", raising=False)
    assert multimodal_enabled() is False
    ports = build_multimodal_ports()
    status = describe_multimodal(ports)
    assert status["enabled"] is False
    assert text_to_image_port(None) is None

    class Shared:
        shares_text_space = True
        def embed_batch(self, texts):
            return [[0.1, 0.2]]

    port = text_to_image_port(Shared())
    assert port("q") == [0.1, 0.2]

    class ImageOnly:
        shares_text_space = False

    assert text_to_image_port(ImageOnly()) is None


def test_session_store_create_get_expire_and_migrate(tmp_path: Path):
    from latticeai.core import sessions as sessions_mod
    from latticeai.core.sessions import SessionStore, load_sessions, persist_sessions

    store = SessionStore(tmp_path, ttl_seconds=60, refresh_threshold_seconds=1)
    token = store.create("alice@example.com", email="alice@example.com")
    assert store.get_email(token) == "alice@example.com"
    assert store.get_subject(token) == "alice@example.com"
    store.invalidate(token)
    assert store.get_email(token) is None

    # Pre-v4 plaintext key is rehashed on load.
    raw = tmp_path / "sessions.json"
    raw.write_text(json.dumps({"plaintext-token": ["bob@example.com", 9_999_999_999.0, "bob@example.com"]}))
    loaded = load_sessions(tmp_path)
    assert all(len(key) == 64 for key in loaded)

    persist_sessions({}, tmp_path)

    expired = SessionStore(tmp_path, ttl_seconds=1, refresh_threshold_seconds=0)
    token2 = expired.create("carol@example.com")
    key = sessions_mod._hash_token(token2)
    subject, created, email = expired._sessions[key]
    expired._sessions[key] = (subject, created - 10, email)
    assert expired.get_email(token2) is None

    # Corrupt file starts empty.
    raw.write_text("not-json")
    assert load_sessions(tmp_path) == {}

    # Missing Config path falls back to env.
    os.environ["LATTICEAI_DATA_DIR"] = str(tmp_path / "fallback")
    assert sessions_mod._sessions_file(None).name == "sessions.json"


def test_users_file_roundtrip_and_identity(tmp_path: Path):
    from latticeai.core.io_utils import atomic_write_json
    from latticeai.core.users import (
        load_users_file,
        migrate_users,
        stable_user_id,
        user_id_for_email,
    )

    path = tmp_path / "users.json"
    assert load_users_file(path) == {}
    atomic_write_json(path, {"Alice@Example.com": {"role": "admin"}})
    loaded = load_users_file(path)
    assert "alice@example.com" in loaded
    assert loaded["alice@example.com"]["id"].startswith("user:")

    path.write_text("[]")
    assert load_users_file(path) == {}
    path.write_text("not-json")
    assert load_users_file(path) == {}

    migrated, email_to_id, changed = migrate_users({
        "A@B.C": {"role": "user"},
        "a@b.c": {"role": "admin", "api_keys": {"k": "v"}},
        "skip": "not-a-dict",
    })
    assert changed is True
    assert "a@b.c" in migrated
    assert user_id_for_email(migrated, None) is None
    assert user_id_for_email(migrated, "user:already") == "user:already"
    assert user_id_for_email({}, "ghost@x.com") == stable_user_id("ghost@x.com")
    assert set(email_to_id) == {"a@b.c"}


def test_worker_app_main_uses_config(monkeypatch, tmp_path: Path):
    import latticeai.worker_app as worker_app

    monkeypatch.setenv("LATTICEAI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LATTICEAI_HOST", "127.0.0.1")
    monkeypatch.setenv("LATTICEAI_PORT", "4825")
    ran = []
    monkeypatch.setattr(
        "uvicorn.run",
        lambda app, **kwargs: ran.append((app, kwargs)),
    )
    worker_app.main()
    assert ran
    assert ran[0][1]["host"] == "127.0.0.1"
    assert ran[0][1]["port"] == 4825
