"""Cloud provider wiring: client construction, catalog, generation, streaming.

Every OpenAI-compatible provider is reached through one seam — the module
global ``AsyncOpenAI`` — so the whole surface is testable by replacing that
name. Nothing here opens a socket; the fake client records what the router
asked for and answers with the response shapes the OpenAI SDK produces.
"""

from __future__ import annotations

import asyncio
import types

import pytest

from latticeai.models import router as router_mod

OMITTED = "<omitted>"


class _FakeAsyncOpenAI:
    """Records how the router constructed it.

    ``base_url`` defaults to a sentinel rather than ``None`` so a test can tell
    "passed base_url=None" apart from "did not pass base_url at all" — the
    distinction the router's own comment calls out.
    """

    def __init__(self, *, api_key, base_url=OMITTED):
        self.api_key = api_key
        self.base_url = base_url


def _client(create):
    return types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create))
    )


def _cloud(create, provider: str = "openai"):
    return router_mod.CloudModel(
        provider=provider, model="gpt-4o", client=_client(create), cache_key="cloud-test"
    )


def _response(content):
    message = types.SimpleNamespace(content=content)
    return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])


class _AsyncEvents:
    """The async iterator an OpenAI streaming call returns."""

    def __init__(self, events):
        self._events = list(events)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._events:
            raise StopAsyncIteration
        return self._events.pop(0)


def _delta_event(content):
    delta = types.SimpleNamespace(content=content)
    return types.SimpleNamespace(choices=[types.SimpleNamespace(delta=delta)])


def _empty_event():
    """A keep-alive / usage-only frame: no choices at all."""
    return types.SimpleNamespace(choices=[])


def _collect(stream_factory, timeout: float = 10.0) -> list:
    chunks: list = []

    async def _scenario() -> None:
        async def _drain() -> None:
            async for chunk in stream_factory():
                chunks.append(chunk)

        await asyncio.wait_for(_drain(), timeout)

    asyncio.run(_scenario())
    return chunks


def _isolate_provider_env(monkeypatch) -> None:
    for config in router_mod.OPENAI_COMPATIBLE_PROVIDERS.values():
        monkeypatch.delenv(config["env_key"], raising=False)
        base_url_env = config.get("base_url_env")
        if base_url_env:
            monkeypatch.delenv(base_url_env, raising=False)
    monkeypatch.delenv("LATTICEAI_CLOUD_MODELS", raising=False)


# ── _load_cloud_model: refusals ──────────────────────────────────────────


def test_load_cloud_model_requires_the_openai_package(monkeypatch):
    monkeypatch.setattr(router_mod, "AsyncOpenAI", None)

    with pytest.raises(RuntimeError, match="openai package is not installed"):
        router_mod.LLMRouter()._load_cloud_model("openai", "gpt-4o")


def test_load_cloud_model_rejects_a_provider_outside_the_catalog(monkeypatch):
    monkeypatch.setattr(router_mod, "AsyncOpenAI", _FakeAsyncOpenAI)

    with pytest.raises(RuntimeError, match="Unsupported cloud provider: mystery"):
        router_mod.LLMRouter()._load_cloud_model("mystery", "some-model")


def test_load_cloud_model_names_the_env_var_it_needs(monkeypatch):
    monkeypatch.setattr(router_mod, "AsyncOpenAI", _FakeAsyncOpenAI)
    _isolate_provider_env(monkeypatch)
    router = router_mod.LLMRouter()

    with pytest.raises(RuntimeError, match="Missing API key env var: OPENAI_API_KEY"):
        router._load_cloud_model("openai", "gpt-4o")

    assert router.loaded_model_ids == []


# ── _load_cloud_model: client construction ───────────────────────────────


def test_load_cloud_model_omits_base_url_when_none_is_configured(monkeypatch):
    monkeypatch.setattr(router_mod, "AsyncOpenAI", _FakeAsyncOpenAI)
    _isolate_provider_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    router = router_mod.LLMRouter()

    router._load_cloud_model("openai", "gpt-4o")

    client = router._cache["openai:gpt-4o::global"].client
    assert client.api_key == "sk-env"
    # Passing base_url=None would override the SDK's own default endpoint.
    assert client.base_url == OMITTED


def test_load_cloud_model_uses_the_catalog_base_url_when_no_env_override(monkeypatch):
    monkeypatch.setattr(router_mod, "AsyncOpenAI", _FakeAsyncOpenAI)
    _isolate_provider_env(monkeypatch)
    router = router_mod.LLMRouter()

    # ollama ships an api_key_fallback, so no env key is required at all.
    router._load_cloud_model("ollama", "gemma-4")

    client = router._cache["ollama:gemma-4::global"].client
    assert client.api_key == "ollama"
    assert client.base_url == "http://localhost:11434/v1"


def test_load_cloud_model_prefers_the_env_base_url_over_the_catalog(monkeypatch):
    monkeypatch.setattr(router_mod, "AsyncOpenAI", _FakeAsyncOpenAI)
    _isolate_provider_env(monkeypatch)
    monkeypatch.setenv("LMSTUDIO_BASE_URL", "http://127.0.0.1:9999/v1")
    router = router_mod.LLMRouter()

    router._load_cloud_model("lmstudio", "local-model")

    assert router._cache["lmstudio:local-model::global"].client.base_url == (
        "http://127.0.0.1:9999/v1"
    )


def test_load_cloud_model_prefers_an_explicit_key_over_the_environment(monkeypatch):
    monkeypatch.setattr(router_mod, "AsyncOpenAI", _FakeAsyncOpenAI)
    _isolate_provider_env(monkeypatch)
    monkeypatch.setenv("GROQ_API_KEY", "sk-env")
    router = router_mod.LLMRouter()

    router._load_cloud_model("groq", "llama", api_key_override="sk-caller")

    client = router._cache["groq:llama::global"].client
    assert client.api_key == "sk-caller"
    assert client.base_url == "https://api.groq.com/openai/v1"


def test_load_cloud_model_scopes_the_cache_key_to_its_owner(monkeypatch):
    """Two users on the same provider+model must not share one client."""
    monkeypatch.setattr(router_mod, "AsyncOpenAI", _FakeAsyncOpenAI)
    _isolate_provider_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    router = router_mod.LLMRouter()

    alice = router._load_cloud_model("openai", "gpt-4o", api_key_override="sk-a", owner="alice")
    shared = router._load_cloud_model("openai", "gpt-4o")

    assert alice == "Cloud provider ready: openai:gpt-4o::alice"
    assert shared == "Cloud provider ready: openai:gpt-4o::global"
    assert router.loaded_model_ids == ["openai:gpt-4o::alice", "openai:gpt-4o::global"]
    assert router.current_model_id == "openai:gpt-4o::global"
    entry = router._cache["openai:gpt-4o::alice"]
    assert isinstance(entry, router_mod.CloudModel)
    assert (entry.provider, entry.model, entry.cache_key) == (
        "openai",
        "gpt-4o",
        "openai:gpt-4o::alice",
    )
    assert entry.client.api_key == "sk-a"
    assert router._cache["openai:gpt-4o::global"].client.api_key == "sk-env"
    assert set(router.model_memory_policy()["last_used"]) == set(router.loaded_model_ids)


# ── detected_cloud_models ────────────────────────────────────────────────


def test_detected_cloud_models_reports_availability_and_provenance(monkeypatch):
    _isolate_provider_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    items = router_mod.LLMRouter().detected_cloud_models()
    by_id = {item["id"]: item for item in items}

    configured = by_id["openai:gpt-4o"]
    assert configured["available"] is True
    assert configured["requires"] is None
    assert configured["tag"] == "cloud"
    assert configured["provider"] == "openai"
    assert configured["family"] == "GPT"
    assert configured["name"] == "GPT-4o"
    assert configured["source_company"] == "OpenAI"
    assert configured["execution_method"] == "인터넷 연결 후 사용"

    unconfigured = by_id["xai:grok-4.5"]
    assert unconfigured["available"] is False
    assert unconfigured["requires"] == "XAI_API_KEY"


def test_detected_cloud_models_falls_back_to_the_default_model_without_a_catalog(monkeypatch):
    _isolate_provider_env(monkeypatch)
    by_id = {item["id"]: item for item in router_mod.LLMRouter().detected_cloud_models()}

    # llamacpp has no PROVIDER_MODEL_CATALOG entry: one synthetic row is built.
    fallback = by_id["llamacpp:llama.cpp-model"]
    assert fallback["name"] == "Llamacpp · llama.cpp-model"
    assert fallback["family"] == "Llamacpp"
    # A built-in key fallback means a local server needs no configuration.
    assert fallback["available"] is True
    assert fallback["requires"] is None
    assert fallback["tag"] == "local-server"
    assert fallback["execution_method"] == "내 컴퓨터에서만 실행"
    assert fallback["source_country"] == "미상"
    assert len([item for item in by_id if item.startswith("llamacpp:")]) == 1


def test_detected_cloud_models_appends_custom_refs_with_their_own_identity(monkeypatch):
    """The custom loop must not reuse the catalog loop's entry.

    Each custom row carries the id/name of the ref the operator configured,
    and the catalog rows in front of it are untouched.
    """
    _isolate_provider_env(monkeypatch)
    monkeypatch.setenv(
        "LATTICEAI_CLOUD_MODELS",
        " openai:my-tuned-model , lmstudio:my-local , local_mlx:ignored ,mystery:skipped, ",
    )
    items = router_mod.LLMRouter().detected_cloud_models()
    ids = [item["id"] for item in items]

    assert "openai:my-tuned-model" in ids
    assert "lmstudio:my-local" in ids
    # Local MLX refs and unknown providers are not cloud models.
    assert not any(item.startswith(("local_mlx:", "mystery:")) for item in ids)

    custom = next(item for item in items if item["id"] == "openai:my-tuned-model")
    assert custom["name"] == "Openai · my-tuned-model"
    assert custom["model_name"] == "Openai · my-tuned-model"
    assert custom["source_company"] == "Openai"
    assert custom["source_country"] == "미상"
    assert custom["available"] is False
    assert custom["requires"] is None

    # A custom ref on a local-server provider keeps the offline provenance.
    local_custom = next(item for item in items if item["id"] == "lmstudio:my-local")
    assert local_custom["execution_method"] == "내 컴퓨터에서만 실행"
    assert local_custom["available"] is True

    # Catalog rows are unchanged by the custom pass.
    catalogued = next(item for item in items if item["id"] == "xai:grok-4.5")
    assert catalogued["model_name"] == "Grok 4.5"
    assert catalogued["source_company"] == "xAI"


def test_detected_cloud_models_ignores_an_empty_custom_list(monkeypatch):
    _isolate_provider_env(monkeypatch)
    baseline = len(router_mod.LLMRouter().detected_cloud_models())
    monkeypatch.setenv("LATTICEAI_CLOUD_MODELS", " , ,")

    assert len(router_mod.LLMRouter().detected_cloud_models()) == baseline


# ── _cloud_generate ──────────────────────────────────────────────────────


def test_cloud_generate_sends_the_grounded_system_prompt_and_rebrands_the_answer():
    seen = {}

    async def create(**kwargs):
        seen.update(kwargs)
        return _response("커넥트 AI 가 답합니다")

    result = asyncio.run(
        router_mod.LLMRouter()._cloud_generate(_cloud(create), "질문", "근거 문서", 256, 0.4)
    )

    assert result == "Lattice AI 가 답합니다"
    assert seen["model"] == "gpt-4o"
    assert seen["max_tokens"] == 256
    assert seen["temperature"] == 0.4
    system = seen["messages"][0]["content"]
    assert "Context:\n근거 문서" in system
    assert router_mod.CITATION_INSTRUCTION in system
    assert seen["messages"][1] == {"role": "user", "content": "질문"}
    assert "stream" not in seen


def test_cloud_generate_without_context_keeps_the_bare_system_prompt():
    seen = {}

    async def create(**kwargs):
        seen.update(kwargs)
        return _response(None)

    result = asyncio.run(
        router_mod.LLMRouter()._cloud_generate(_cloud(create), "질문", None, 128, 0.2)
    )

    # A refusal with no content is an empty answer, not a crash.
    assert result == ""
    assert seen["messages"][0]["content"] == router_mod.SYSTEM_PROMPT


def test_cloud_generate_wraps_a_backend_failure_with_the_local_server_hint():
    boom = ConnectionError("Connection error.")

    async def create(**_kwargs):
        raise boom

    with pytest.raises(RuntimeError) as excinfo:
        asyncio.run(
            router_mod.LLMRouter()._cloud_generate(
                _cloud(create, provider="lmstudio"), "질문", None, 128, 0.2
            )
        )

    message = str(excinfo.value)
    assert "LM Studio 연결 실패" in message
    assert "http://localhost:1234/v1" in message
    assert excinfo.value.__cause__ is boom


# ── _cloud_stream_generate ───────────────────────────────────────────────


def test_cloud_stream_generate_yields_only_real_deltas():
    seen = {}

    async def create(**kwargs):
        seen.update(kwargs)
        return _AsyncEvents(
            [
                _empty_event(),
                _delta_event(None),
                _delta_event("커넥트 AI "),
                _delta_event(""),
                _delta_event("입니다"),
            ]
        )

    router = router_mod.LLMRouter()
    chunks = _collect(
        lambda: router._cloud_stream_generate(_cloud(create), "질문", "근거", 64, 0.1)
    )

    assert chunks == ["Lattice AI ", "입니다"]
    assert seen["stream"] is True
    assert "Context:\n근거" in seen["messages"][0]["content"]


def test_cloud_stream_generate_reaches_the_public_api(monkeypatch):
    async def create(**_kwargs):
        return _AsyncEvents([_delta_event("연결됨")])

    router = router_mod.LLMRouter()
    router._cache = {"cloud": _cloud(create)}
    router._current = "cloud"

    assert _collect(lambda: router.stream_generate("질문")) == ["연결됨"]


# ── _cloud_generate_document ─────────────────────────────────────────────


def test_cloud_generate_document_uses_the_caller_supplied_system_prompt():
    seen = {}

    async def create(**kwargs):
        seen.update(kwargs)
        return _response("# 커넥트 AI 보고서")

    result = asyncio.run(
        router_mod.LLMRouter()._cloud_generate_document(
            _cloud(create), "보고서 써줘", "DOC SYSTEM", 4096, 0.3
        )
    )

    assert result == "# Lattice AI 보고서"
    # No SYSTEM_PROMPT, no Context block: the document prompt replaces both.
    assert seen["messages"][0] == {"role": "system", "content": "DOC SYSTEM"}
    assert seen["messages"][1] == {"role": "user", "content": "보고서 써줘"}
    assert seen["max_tokens"] == 4096
    assert seen["temperature"] == 0.3


def test_cloud_generate_document_returns_empty_text_for_an_empty_completion():
    async def create(**_kwargs):
        return _response(None)

    result = asyncio.run(
        router_mod.LLMRouter()._cloud_generate_document(
            _cloud(create), "보고서", "DOC SYSTEM", 128, 0.3
        )
    )

    assert result == ""


def test_cloud_generate_document_wraps_a_backend_failure():
    boom = ConnectionError("gateway timeout")

    async def create(**_kwargs):
        raise boom

    with pytest.raises(RuntimeError) as excinfo:
        asyncio.run(
            router_mod.LLMRouter()._cloud_generate_document(
                _cloud(create), "보고서", "DOC SYSTEM", 128, 0.3
            )
        )

    assert "gateway timeout" in str(excinfo.value)
    assert excinfo.value.__cause__ is boom


# ── _cloud_stream_document ───────────────────────────────────────────────


def test_cloud_stream_document_yields_only_real_deltas():
    seen = {}

    async def create(**kwargs):
        seen.update(kwargs)
        return _AsyncEvents(
            [_empty_event(), _delta_event("# 커넥트 AI"), _delta_event(None), _delta_event(" 보고서")]
        )

    router = router_mod.LLMRouter()
    chunks = _collect(
        lambda: router._cloud_stream_document(_cloud(create), "보고서", "DOC SYSTEM", 512, 0.3)
    )

    assert chunks == ["# Lattice AI", " 보고서"]
    assert seen["stream"] is True
    assert seen["messages"][0] == {"role": "system", "content": "DOC SYSTEM"}
    assert seen["max_tokens"] == 512
