"""Engine listing, cloud verification, and the bound ModelRuntimeService.

`engine_status` is the payload the Library screen renders, and it is assembled
from six independent probes (binary present? model pulled? server up? key
verified?). Each probe is replaced here with a fake, so the assertions are
about the *assembly* — ordering, per-engine model lists, the LM Studio
downloaded-vs-catalog merge, and which verification result reaches which model.

The service tests drive the same code through `ModelRuntimeService`, which is
the only object an application actually holds, and confirm the verification
cache lives on the instance rather than in module state.
"""

from __future__ import annotations

import asyncio
import types

import pytest

from latticeai.services import model_loading, model_runtime

CATALOG = {
    "local_mlx": [
        {"id": "org/wp02-alpha-4bit", "name": "WP02 Alpha", "family": "WP02", "tag": "local-vlm", "pullable": True},
    ],
    "ollama": [
        {"id": "ollama:wp02-alpha", "name": "WP02 Alpha", "family": "WP02", "tag": "ollama", "pullable": True},
        {"id": "ollama:wp02-beta", "name": "WP02 Beta", "family": "WP02", "tag": "ollama", "pullable": True},
    ],
    "vllm": [
        {"id": "vllm:org/wp02-alpha", "name": "WP02 Alpha", "family": "WP02", "tag": "vllm", "pullable": True},
    ],
    "lmstudio": [
        {"id": "lmstudio:org/wp02-catalog", "name": "WP02 Catalog", "family": "WP02", "tag": "lmstudio"},
    ],
    "llamacpp": [
        {"id": "llamacpp:org/wp02-alpha", "name": "WP02 Alpha", "family": "WP02", "tag": "gguf"},
    ],
}

CLOUD_MODELS = [
    {"id": "openai:gpt-4o-mini", "provider": "openai", "tag": "cloud", "requires": "OPENAI_API_KEY"},
    {"id": "openai:gpt-4.1", "provider": "openai", "tag": "cloud", "requires": "OPENAI_API_KEY"},
    {"id": "groq:wp02-fast", "provider": "groq", "tag": "cloud"},
]


class _Router:
    def __init__(self, cloud_models=None):
        self.cloud_models = list(CLOUD_MODELS if cloud_models is None else cloud_models)
        self.current_model_id = "org/wp02-alpha-4bit"
        self.loaded: list = []

    def detected_cloud_models(self):
        return list(self.cloud_models)

    def model_memory_policy(self):
        return {"idle_unload_seconds": 0}

    async def load_model(self, model_id, adapter_path, **kwargs):
        self.loaded.append((model_id, adapter_path, kwargs))
        self.current_model_id = model_id
        return f"loaded {model_id}"


def _stub_probes(monkeypatch, tmp_path, *, lmstudio_models, pulled=frozenset(), ready=frozenset()):
    monkeypatch.setattr(model_runtime, "ENGINE_MODEL_CATALOG", CATALOG)
    monkeypatch.setattr(model_runtime, "HF_MODELS_ROOT", tmp_path / "hf-models")
    monkeypatch.setattr(model_runtime, "engine_installed", lambda engine: engine in {"ollama", "local_mlx"})
    monkeypatch.setattr(model_runtime, "get_ollama_pulled_models", lambda: set(pulled))
    monkeypatch.setattr(model_runtime, "hf_model_ready", lambda repo, _provider: repo in ready)
    monkeypatch.setattr(model_runtime, "get_lmstudio_models", lambda: list(lmstudio_models))
    monkeypatch.setattr(model_runtime, "engine_support_status", lambda _engine: {"supported": True, "reason": None})
    monkeypatch.setattr(
        model_runtime,
        "_safe_engine_install_plan",
        lambda engine, *, base_dir: {"name": f"engine:{engine}", "cwd": str(base_dir)},
    )


def _service(tmp_path, **overrides) -> model_runtime.ModelRuntimeService:
    defaults = {"router": _Router(), "BASE_DIR": tmp_path}
    defaults.update(overrides)
    return model_runtime.build_model_runtime(**defaults)


def _by_id(engines):
    return {engine["id"]: engine for engine in engines}


# ── engine_status ────────────────────────────────────────────────────────────


def test_engine_status_lists_every_engine_in_the_products_order(monkeypatch, tmp_path):
    _stub_probes(monkeypatch, tmp_path, lmstudio_models=[])
    service = _service(tmp_path)

    engines = service.engine_status()

    assert [engine["id"] for engine in engines] == [
        "local_mlx",
        "ollama",
        "vllm",
        "lmstudio",
        "llamacpp",
        "openai",
        "openrouter",
        "groq",
        "together",
        "xai",
    ]
    assert [engine["kind"] for engine in engines[:5]] == [
        "local",
        "local-server",
        "local-server",
        "local-server",
        "local-server",
    ]
    assert (tmp_path / "hf-models").is_dir(), "the managed model root is created up front"


def test_engine_status_marks_which_local_models_are_already_on_disk(monkeypatch, tmp_path):
    _stub_probes(
        monkeypatch,
        tmp_path,
        lmstudio_models=[],
        pulled={"wp02-alpha"},
        ready={"org/wp02-alpha-4bit", "org/wp02-alpha"},
    )
    engines = _by_id(_service(tmp_path).engine_status())

    assert [(m["id"], m["pulled"]) for m in engines["ollama"]["models"]] == [
        ("ollama:wp02-alpha", True),
        ("ollama:wp02-beta", False),
    ]
    assert engines["local_mlx"]["models"][0]["pulled"] is True
    assert engines["vllm"]["models"][0]["pulled"] is True
    assert engines["llamacpp"]["models"][0]["pulled"] is True


def test_engine_status_falls_back_to_the_catalog_when_lm_studio_has_nothing(monkeypatch, tmp_path):
    _stub_probes(monkeypatch, tmp_path, lmstudio_models=[])

    lmstudio = _by_id(_service(tmp_path).engine_status())["lmstudio"]

    assert [m["id"] for m in lmstudio["models"]] == ["lmstudio:org/wp02-catalog"]
    assert lmstudio["models"][0]["pulled"] is False
    assert lmstudio["server_ready"] is False
    assert "LM Studio 설치 후" in lmstudio["note"]


def test_engine_status_merges_lm_studios_own_downloads_ahead_of_the_catalog(monkeypatch, tmp_path):
    _stub_probes(
        monkeypatch,
        tmp_path,
        lmstudio_models=[
            {"key": "   ", "display_name": "ignored — no key"},
            {
                "key": "org/wp02-downloaded",
                "display_name": "WP02 Downloaded",
                "architecture": "wp02",
                "params_string": "8B",
                "loaded_instances": [{"id": "org/wp02-downloaded:1"}],
            },
            {"key": "org/wp02-idle", "publisher": "wp02", "format": "gguf"},
        ],
    )

    lmstudio = _by_id(_service(tmp_path).engine_status())["lmstudio"]

    assert [m["id"] for m in lmstudio["models"]] == [
        "lmstudio:org/wp02-downloaded",
        "lmstudio:org/wp02-idle",
        "lmstudio:org/wp02-catalog",
    ]
    loaded, idle, catalog_entry = lmstudio["models"]
    assert loaded["tag"] == "loaded-server-model"
    assert loaded["size"] == "8B"
    assert loaded["family"] == "wp02"
    assert idle["tag"] == "downloaded"
    assert idle["name"] == "LM Studio · org/wp02-idle"
    assert idle["family"] == "wp02"
    assert catalog_entry["pulled"] is False, "a catalog suggestion is not a download"
    assert lmstudio["server_ready"] is True
    assert "자동 감지" in lmstudio["note"]


def test_engine_status_attaches_the_verification_cache_to_the_matching_cloud_model(monkeypatch, tmp_path):
    _stub_probes(monkeypatch, tmp_path, lmstudio_models=[])
    service = _service(tmp_path)
    service._cloud_verify_cache["openai:gpt-4o-mini"] = {"ok": False, "reason": "401 invalid key"}

    engines = _by_id(service.engine_status())

    verified, unchecked = engines["openai"]["models"]
    assert (verified["id"], verified["verified"], verified["verify_reason"]) == (
        "openai:gpt-4o-mini",
        False,
        "401 invalid key",
    )
    assert (unchecked["id"], unchecked["verified"], unchecked["verify_reason"]) == (
        "openai:gpt-4.1",
        None,
        None,
    )
    assert engines["openai"]["requires"] == "OPENAI_API_KEY"
    assert engines["groq"]["requires"] is None, "a provider that declares no env key reports none"
    assert engines["xai"]["models"] == []


def test_engine_status_reports_install_plans_rooted_at_the_applications_base_dir(monkeypatch, tmp_path):
    _stub_probes(monkeypatch, tmp_path, lmstudio_models=[])

    engines = _by_id(_service(tmp_path).engine_status())

    assert engines["local_mlx"]["install_plan"] == {"name": "engine:local_mlx", "cwd": str(tmp_path)}
    assert engines["local_mlx"]["installed"] is True
    assert engines["llamacpp"]["installed"] is False
    assert engines["llamacpp"]["installable"] is True
    assert engines["llamacpp"]["supported"] is True
    assert engines["openai"]["install_plan"] == {"name": "engine:openai", "cwd": str(tmp_path)}


def test_engine_status_without_a_router_lists_engines_but_no_cloud_models(monkeypatch, tmp_path):
    _stub_probes(monkeypatch, tmp_path, lmstudio_models=[])

    engines = _by_id(_service(tmp_path, router=None).engine_status())

    assert engines["openai"]["models"] == []
    assert engines["local_mlx"]["models"], "local engines do not depend on a loaded router"


# ── _probe_cloud_model ───────────────────────────────────────────────────────


def _fake_openai(monkeypatch, *, result=None, error=None):
    created: list = []

    class _Client:
        def __init__(self, **kwargs):
            created.append(kwargs)
            self.chat = types.SimpleNamespace(
                completions=types.SimpleNamespace(create=self._create)
            )

        async def _create(self, **kwargs):
            created.append({"call": kwargs})
            if error is not None:
                raise error
            return result

    monkeypatch.setattr(model_runtime, "AsyncOpenAI", _Client)
    return created


def test_probing_a_model_with_no_cloud_provider_is_refused_by_name():
    result = asyncio.run(model_runtime._probe_cloud_model("wp02-local-only"))

    assert result == {"ok": False, "reason": "Unsupported provider: local_mlx"}


def test_probing_without_an_api_key_names_the_env_var_to_set(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    result = asyncio.run(model_runtime._probe_cloud_model("openai:gpt-4o-mini"))

    assert result == {"ok": False, "reason": "Missing API key: OPENAI_API_KEY"}


def test_a_provider_without_a_base_url_is_constructed_with_the_key_alone(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-wp02")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    created = _fake_openai(monkeypatch, result=object())

    assert asyncio.run(model_runtime._probe_cloud_model("openai:gpt-4o-mini")) == {"ok": True, "reason": "ok"}
    assert created[0] == {"api_key": "sk-wp02"}
    assert created[1]["call"]["model"] == "gpt-4o-mini"
    assert created[1]["call"]["max_tokens"] == 1


def test_an_env_base_url_override_is_passed_to_the_client(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-wp02")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:9/v1")
    created = _fake_openai(monkeypatch, result=object())

    assert asyncio.run(model_runtime._probe_cloud_model("openai:gpt-4o-mini"))["ok"] is True
    assert created[0] == {"api_key": "sk-wp02", "base_url": "http://127.0.0.1:9/v1"}


def test_a_provider_with_a_catalog_base_url_uses_it(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-wp02")
    created = _fake_openai(monkeypatch, result=object())

    assert asyncio.run(model_runtime._probe_cloud_model("groq:wp02-fast"))["ok"] is True
    assert created[0]["base_url"] == model_runtime.OPENAI_COMPATIBLE_PROVIDERS["groq"]["base_url"]


def test_a_provider_with_a_fallback_key_needs_no_env_var(monkeypatch):
    """Local OpenAI-compatible servers ship a placeholder key on purpose."""
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    created = _fake_openai(monkeypatch, result=object())

    assert asyncio.run(model_runtime._probe_cloud_model("ollama:wp02-alpha"))["ok"] is True
    assert created[0]["api_key"] == "ollama"


def test_a_probe_that_raises_reports_the_reason_without_claiming_success(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-wp02")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    _fake_openai(monkeypatch, error=RuntimeError("x" * 400))

    result = asyncio.run(model_runtime._probe_cloud_model("openai:gpt-4o-mini"))

    assert result["ok"] is False
    assert len(result["reason"]) == 220, "the reason is truncated, not dropped"


# ── verify_cloud_models ──────────────────────────────────────────────────────


def _freeze(monkeypatch, now=5_000.0):
    monkeypatch.setattr(
        model_runtime,
        "time",
        types.SimpleNamespace(time=lambda: now, monotonic=lambda: now, sleep=lambda _s: None),
    )


def _probe_recorder(monkeypatch, answers):
    probed: list = []

    async def _probe(model_ref):
        probed.append(model_ref)
        return answers.get(model_ref, {"ok": True, "reason": "ok"})

    monkeypatch.setattr(model_runtime, "_probe_cloud_model", _probe)
    return probed


def test_verification_reuses_a_fresh_cache_entry_and_skips_unavailable_models(monkeypatch, tmp_path):
    _freeze(monkeypatch)
    probed = _probe_recorder(monkeypatch, {"groq:wp02-fast": {"ok": False, "reason": "429 rate limited"}})
    router = _Router(
        [
            {"id": "openai:cached", "provider": "openai", "tag": "cloud"},
            {"id": "openai:nokey", "provider": "openai", "tag": "cloud", "available": False, "requires": "OPENAI_API_KEY"},
            {"id": "groq:wp02-fast", "provider": "groq", "tag": "cloud"},
            {"id": "local:ignored", "provider": "openai", "tag": "local"},
        ]
    )
    service = _service(tmp_path, router=router)
    service._cloud_verify_cache["openai:cached"] = {"ok": True, "reason": "ok", "ts": 4_990.0}

    results = asyncio.run(service.verify_cloud_models())

    assert probed == ["groq:wp02-fast"], "only the uncached, available model is probed"
    assert set(results) == {"openai:cached", "openai:nokey", "groq:wp02-fast"}
    assert results["openai:cached"]["ts"] == 4_990.0, "a fresh entry is served untouched"
    assert results["openai:nokey"] == {"ok": False, "reason": "OPENAI_API_KEY", "ts": 5_000.0}
    assert results["groq:wp02-fast"] == {"ok": False, "reason": "429 rate limited", "ts": 5_000.0}
    assert service._cloud_verify_cache["groq:wp02-fast"]["ok"] is False


def test_a_stale_cache_entry_is_reprobed(monkeypatch, tmp_path):
    _freeze(monkeypatch)
    probed = _probe_recorder(monkeypatch, {})
    service = _service(tmp_path, router=_Router([{"id": "openai:cached", "provider": "openai", "tag": "cloud"}]))
    service._cloud_verify_cache["openai:cached"] = {
        "ok": False,
        "reason": "old failure",
        "ts": 5_000.0 - model_runtime.CLOUD_VERIFY_TTL_SECONDS - 1,
    }

    results = asyncio.run(service.verify_cloud_models())

    assert probed == ["openai:cached"]
    assert results["openai:cached"]["ok"] is True


def test_force_reprobes_even_a_fresh_entry(monkeypatch, tmp_path):
    _freeze(monkeypatch)
    probed = _probe_recorder(monkeypatch, {})
    service = _service(tmp_path, router=_Router([{"id": "openai:cached", "provider": "openai", "tag": "cloud"}]))
    service._cloud_verify_cache["openai:cached"] = {"ok": False, "reason": "old", "ts": 5_000.0}

    results = asyncio.run(service.verify_cloud_models(force=True))

    assert probed == ["openai:cached"]
    assert results["openai:cached"]["ok"] is True


def test_a_provider_filter_narrows_verification_to_that_provider(monkeypatch, tmp_path):
    _freeze(monkeypatch)
    probed = _probe_recorder(monkeypatch, {})
    service = _service(tmp_path)

    results = asyncio.run(service.verify_cloud_models(provider_filter="groq"))

    assert probed == ["groq:wp02-fast"]
    assert list(results) == ["groq:wp02-fast"]


def test_verification_without_a_router_has_nothing_to_verify(monkeypatch, tmp_path):
    _freeze(monkeypatch)
    _probe_recorder(monkeypatch, {})

    assert asyncio.run(_service(tmp_path, router=None).verify_cloud_models()) == {}


def test_two_services_do_not_share_a_verification_cache(monkeypatch, tmp_path):
    _freeze(monkeypatch)
    _probe_recorder(monkeypatch, {})
    first = _service(tmp_path)
    second = _service(tmp_path)

    asyncio.run(first.verify_cloud_models())

    assert first._cloud_verify_cache != {}
    assert second._cloud_verify_cache == {}, "one app's probe results must not leak into another's"


# ── the bound service surface ────────────────────────────────────────────────


def test_the_service_installs_an_engine_with_its_own_base_dir(monkeypatch, tmp_path):
    seen: list = []
    monkeypatch.setattr(
        model_runtime,
        "_install_engine",
        lambda engine, **kwargs: seen.append((engine, kwargs)) or {"returncode": 0},
    )

    assert _service(tmp_path).install_engine("ollama", "token-9") == {"returncode": 0}
    assert seen == [("ollama", {"confirmation_token": "token-9", "base_dir": tmp_path})]


def test_the_service_hands_the_whole_load_request_to_the_loading_module(monkeypatch, tmp_path):
    seen: list = []

    async def _impl(model_id, request, **kwargs):
        seen.append((model_id, request, kwargs))
        return {"status": "ok", "model": model_id}

    monkeypatch.setattr(model_loading, "prepare_and_load_model", _impl)
    service = _service(tmp_path)
    request = object()

    result = asyncio.run(
        service.prepare_and_load_model(
            "ollama:wp02-alpha",
            request,
            engine="ollama",
            user_email="me@example.com",
            adapter_path="/adapters/a",
            draft_model_id="draft",
            allow_download=True,
        )
    )

    assert result == {"status": "ok", "model": "ollama:wp02-alpha"}
    model_id, seen_request, kwargs = seen[0]
    assert (model_id, seen_request) == ("ollama:wp02-alpha", request)
    assert kwargs == {
        "engine": "ollama",
        "user_email": "me@example.com",
        "adapter_path": "/adapters/a",
        "draft_model_id": "draft",
        "allow_download": True,
        "runtime_state": service.state,
    }


def test_the_service_streams_every_event_the_loading_module_produces(monkeypatch, tmp_path):
    seen: list = []

    async def _impl(model_id, request, **kwargs):
        seen.append((model_id, kwargs))
        yield "event: progress\ndata: {}\n\n"
        yield "event: done\ndata: {}\n\n"

    monkeypatch.setattr(model_loading, "prepare_and_load_model_stream", _impl)
    service = _service(tmp_path)

    async def _drain():
        return [
            event
            async for event in service.prepare_and_load_model_stream(
                "ollama:wp02-alpha", object(), engine="ollama", user_email="me@example.com", allow_download=True
            )
        ]

    events = asyncio.run(asyncio.wait_for(_drain(), 10))

    assert events == ["event: progress\ndata: {}\n\n", "event: done\ndata: {}\n\n"]
    assert seen[0][1] == {
        "engine": "ollama",
        "user_email": "me@example.com",
        "allow_download": True,
        "runtime_state": service.state,
    }


def test_the_service_reports_the_features_of_its_own_state(tmp_path):
    service = _service(tmp_path, APP_MODE="public", IS_PUBLIC_MODE=True, PUBLIC_MODEL="openai:gpt-4o-mini")

    features = service.runtime_features()

    assert features["mode"] == "public"
    assert features["default_model"] == "openai:gpt-4o-mini"
    assert features["local_only_features"]["mlx"] is False
    assert features["model_memory_policy"] == {"idle_unload_seconds": 0}


def test_an_unknown_dependency_name_is_refused_rather_than_silently_ignored():
    with pytest.raises(TypeError, match="WP02_NOT_A_SETTING"):
        model_runtime.build_model_runtime(WP02_NOT_A_SETTING=True)
