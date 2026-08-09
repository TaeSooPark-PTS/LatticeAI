"""Post-load smoke test and the OpenAI-compatible cloud adapter.

Two async surfaces, both driven with ``asyncio.run`` from plain sync tests (the
repo idiom — there is no pytest-asyncio mode configured). Neither one is allowed
to reach a model: the smoke test gets a stub router, and the adapter gets a fake
``openai`` module injected through ``sys.modules`` so the import inside
``_client`` resolves to something this test built.
"""

from __future__ import annotations

import asyncio
import sys
import types

import pytest

from latticeai.services import model_engines
from latticeai.services.openai_compatible_adapter import OpenAICompatibleAdapter


def _resolution(engine: str, load_id: str):
    return types.SimpleNamespace(engine=engine, load_id=load_id)


class _Router:
    """A model router that answers without a model."""

    def __init__(self, *, answer: str = "", error: Exception = None) -> None:
        self.answer = answer
        self.error = error
        self.calls: list = []

    async def generate(self, prompt, *, context=None, max_tokens=None, temperature=None):
        self.calls.append({"prompt": prompt, "max_tokens": max_tokens, "temperature": temperature})
        if self.error is not None:
            raise self.error
        return self.answer


# ── _smoke_test_loaded_model ─────────────────────────────────────────────────


def test_smoke_test_skips_itself_when_its_helpers_cannot_be_imported(monkeypatch):
    """A broken import must degrade to "skipped", never break the load flow."""
    monkeypatch.setitem(sys.modules, "latticeai.core.model_compat", None)

    result = asyncio.run(
        model_engines._smoke_test_loaded_model(
            _resolution("ollama", "wp03/import-fail"), model_router=_Router(answer="4")
        )
    )

    assert result["ok"] is False
    assert result["skipped"] is True
    assert "smoke import failed" in result["reason"]


def test_smoke_test_without_a_router_is_reported_not_raised():
    result = asyncio.run(
        model_engines._smoke_test_loaded_model(_resolution("ollama", "wp03/no-router"))
    )

    assert result == {
        "ok": False,
        "reason": "model router is not configured",
        "skipped": True,
    }


def test_cloud_models_are_skipped_so_the_smoke_test_costs_nothing():
    router = _Router(answer="4")

    result = asyncio.run(
        model_engines._smoke_test_loaded_model(
            _resolution("openai", "wp03/cloud"), model_router=router
        )
    )

    assert result["ok"] is True
    assert result["skipped"] is True
    assert "cost" in result["reason"]
    assert result["answer"] is None
    assert result["profile"]["model_id"] == "wp03/cloud"
    assert router.calls == []  # nothing was generated


def test_a_local_model_that_answers_is_recorded_as_ok():
    router = _Router(answer="정답은 4입니다.")

    result = asyncio.run(
        model_engines._smoke_test_loaded_model(
            _resolution("ollama", "wp03/local-ok"), model_router=router
        )
    )

    assert result["status"] == "ok"
    assert result["ok"] is True
    assert result["answer"] == "정답은 4입니다."
    assert result["profile"]["chat_compatible"] is True
    assert result["profile"]["quality_status"] == "ok"
    assert router.calls[0]["max_tokens"] == 128


def test_a_local_model_that_runs_away_repeating_is_recorded_as_failed():
    """Post-processing runs first, so the verdict is on the cleaned answer."""
    router = _Router(answer="assistant: 네네네네네네네네네네")

    result = asyncio.run(
        model_engines._smoke_test_loaded_model(
            _resolution("lmstudio", "wp03/local-runaway"), model_router=router
        )
    )

    assert result["answer"] == "네네네네네네네네네네"  # the role marker was stripped
    assert result["status"] == "failed"
    assert result["ok"] is False
    assert result["reason"] == "runaway repetition"
    assert result["profile"]["quality_status"] == "failed"


def test_a_generation_failure_is_recorded_against_the_profile():
    router = _Router(error=RuntimeError("engine died mid-token"))

    result = asyncio.run(
        model_engines._smoke_test_loaded_model(
            _resolution("vllm", "wp03/local-error"), model_router=router
        )
    )

    assert result["ok"] is False
    assert result["status"] == "failed"
    assert result["answer"] is None
    assert "engine died mid-token" in result["reason"]
    assert result["profile"]["last_test_error"] == "engine died mid-token"


# ── OpenAICompatibleAdapter ──────────────────────────────────────────────────


class _Stream:
    def __init__(self, events) -> None:
        self._events = events

    def __aiter__(self):
        async def _iter():
            for event in self._events:
                yield event

        return _iter()


class _Completions:
    def __init__(self, events, seen) -> None:
        self._events = events
        self._seen = seen

    async def create(self, **kwargs):
        self._seen.update(kwargs)
        return _Stream(self._events)


def _fake_openai(monkeypatch, events, seen):
    class _AsyncOpenAI:
        def __init__(self, **kwargs):
            seen["client_kwargs"] = kwargs
            self.chat = types.SimpleNamespace(completions=_Completions(events, seen))

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(AsyncOpenAI=_AsyncOpenAI))
    return _AsyncOpenAI


def _event(content):
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(delta=types.SimpleNamespace(content=content))]
    )


def test_adapter_reads_its_whole_configuration_from_the_environment(monkeypatch):
    monkeypatch.setenv("LATTICEAI_CLOUD_API_KEY", "  sk-env  ")
    monkeypatch.setenv("LATTICEAI_CLOUD_BASE_URL", " https://gateway.example/v1 ")
    monkeypatch.setenv("LATTICEAI_CLOUD_MODEL", " env-model ")

    adapter = OpenAICompatibleAdapter()

    assert adapter.api_key == "sk-env"
    assert adapter.base_url == "https://gateway.example/v1"
    assert adapter.default_model == "env-model"


def test_adapter_defaults_when_nothing_is_configured(monkeypatch):
    for name in ("LATTICEAI_CLOUD_API_KEY", "LATTICEAI_CLOUD_BASE_URL", "LATTICEAI_CLOUD_MODEL"):
        monkeypatch.delenv(name, raising=False)

    adapter = OpenAICompatibleAdapter()

    assert adapter.api_key == ""
    assert adapter.base_url is None
    assert adapter.default_model == "gpt-4o-mini"


def test_explicit_arguments_win_over_the_environment(monkeypatch):
    monkeypatch.setenv("LATTICEAI_CLOUD_API_KEY", "sk-env")
    monkeypatch.setenv("LATTICEAI_CLOUD_MODEL", "env-model")

    adapter = OpenAICompatibleAdapter(
        api_key="sk-explicit", base_url="https://direct.example/v1", default_model="explicit-model"
    )

    assert (adapter.api_key, adapter.base_url, adapter.default_model) == (
        "sk-explicit",
        "https://direct.example/v1",
        "explicit-model",
    )


def test_an_unconfigured_adapter_says_so_instead_of_calling_out(monkeypatch):
    monkeypatch.delenv("LATTICEAI_CLOUD_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="LATTICEAI_CLOUD_API_KEY"):
        OpenAICompatibleAdapter()._client()


def test_the_base_url_is_only_passed_when_it_is_configured(monkeypatch):
    seen: dict = {}
    _fake_openai(monkeypatch, [], seen)

    OpenAICompatibleAdapter(api_key="sk-test")._client()
    assert seen["client_kwargs"] == {"api_key": "sk-test"}

    OpenAICompatibleAdapter(api_key="sk-test", base_url="https://gw.example/v1")._client()
    assert seen["client_kwargs"] == {"api_key": "sk-test", "base_url": "https://gw.example/v1"}


def test_streaming_yields_only_non_empty_deltas(monkeypatch):
    seen: dict = {}
    broken = types.SimpleNamespace(choices=[])  # malformed frame: no choices
    _fake_openai(
        monkeypatch,
        [_event("안녕"), _event(""), _event(None), broken, _event("하세요")],
        seen,
    )
    adapter = OpenAICompatibleAdapter(api_key="sk-test", default_model="fallback-model")

    async def _collect():
        return [
            piece
            async for piece in adapter.stream(system="be brief", user="hi", context="")
        ]

    assert asyncio.run(_collect()) == ["안녕", "하세요"]
    assert seen["model"] == "fallback-model"
    assert seen["stream"] is True
    assert seen["messages"] == [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "hi"},
    ]


def test_graph_context_is_sent_as_a_second_system_message(monkeypatch):
    seen: dict = {}
    _fake_openai(monkeypatch, [_event("ok")], seen)
    adapter = OpenAICompatibleAdapter(api_key="sk-test")

    async def _collect():
        return [
            piece
            async for piece in adapter.stream(
                system="be brief", user="hi", context="node A -> node B", model=" chosen-model "
            )
        ]

    assert asyncio.run(_collect()) == ["ok"]
    assert seen["model"] == "chosen-model"
    assert len(seen["messages"]) == 3
    assert seen["messages"][1]["role"] == "system"
    assert "node A -> node B" in seen["messages"][1]["content"]
    assert seen["messages"][2] == {"role": "user", "content": "hi"}
