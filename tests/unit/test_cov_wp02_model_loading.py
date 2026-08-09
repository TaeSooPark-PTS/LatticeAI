"""Model preparation — the per-engine source work and its streaming twin.

`_prepare_model_sources` and the `blocking_prepare` worker inside
`prepare_and_load_model_stream` are the same decision tree written twice: for
each engine, is the runtime installed, are the weights present, and may we go
and get them? Every one of those branches ends in a subprocess, a download or a
child server, so all of them arrive through the `runtime_state` dependency
surface and are replaced here with recorders.

The streaming half is asserted on the frames a browser would actually receive,
because that is the only thing the Model Load screen can react to — a refusal
that never reaches the wire is indistinguishable from a hang.
"""

from __future__ import annotations

import asyncio
import json
import types
from pathlib import Path

import pytest

from latticeai.core.model_resolution import ModelResolution
from latticeai.services import model_loading
from latticeai.services.model_errors import ModelRuntimeError

STREAM_TIMEOUT = 10.0


class _Router:
    def __init__(self, current="previously-loaded"):
        self.current_model_id = current
        self.loaded: list = []

    async def load_model(self, model_id, adapter_path, **kwargs):
        self.loaded.append({"model_id": model_id, "adapter_path": adapter_path, **kwargs})
        self.current_model_id = model_id
        return f"loaded {model_id}"


def _progress_payload(stage, message, **kwargs):
    return {"stage": stage, "message": message, **kwargs}


def _deps(**overrides):
    """A permissive dependency surface; override the one key under test."""
    router = overrides.pop("router", None) or _Router()
    calls: dict = {"blocked": [], "engines": [], "downloads": [], "servers": [], "lmstudio": []}

    def _download_block(provider, model):
        calls["blocked"].append(("download", provider, model))
        raise ModelRuntimeError(
            status_code=409,
            detail={"capability": "model_download", "provider": provider, "model": model},
        )

    def _engine_install_block(engine):
        calls["blocked"].append(("install", engine, None))
        raise ModelRuntimeError(
            status_code=409,
            detail={"capability": "engine_install", "engine": engine},
        )

    async def _smoke(_resolution, api_key_override=None):
        return {"ok": True, "status": "ok"}

    base = {
        "normalize_local_model_request": lambda model_id, engine: model_id,
        "_ModelResolution": ModelResolution,
        "parse_model_ref": lambda model_id: (
            tuple(model_id.split(":", 1)) if ":" in model_id else ("local_mlx", model_id)
        ),
        "_model_runtime_compatibility": lambda _model, engine=None: {"supported": True},
        "engine_installed": lambda _provider: True,
        "_download_allowed": lambda allow: bool(allow),
        "_engine_install_block": _engine_install_block,
        "ensure_engine_ready": lambda provider: (
            calls["engines"].append(provider) or {"installed_now": False}
        ),
        "hf_model_ready": lambda _model, _provider: True,
        "_download_block": _download_block,
        "download_hf_model": lambda model, provider, progress_emit=None: (
            calls["downloads"].append((provider, model)) or {"model": model, "path": "/models/" + model}
        ),
        "ensure_ollama_server": lambda: calls["servers"].append("ollama"),
        "local_binary": lambda name: "/usr/bin/" + name,
        "get_ollama_pulled_models": lambda: set(),
        "ensure_vllm_server": lambda model: calls["servers"].append(("vllm", model)),
        "ensure_llamacpp_server": lambda model: calls["servers"].append(("llamacpp", model)),
        "get_lmstudio_models": lambda: [],
        "ensure_lmstudio_model": lambda model: (
            calls["lmstudio"].append(model) or {"instance_id": model + ":1"}
        ),
        "get_current_user": lambda _request: "me@example.com",
        "get_user_api_key": lambda _email, _provider: None,
        "router": router,
        "_smoke_test_loaded_model": _smoke,
        "MODEL_ENGINE_ALIASES": {},
        "_friendly_model_runtime_error": lambda exc, model_id=None, engine=None: {
            "reason": str(exc),
            "model": model_id,
            "engine": engine,
        },
        "hf_model_dir": lambda model: Path("/models") / model,
        "model_download_progress_payload": _progress_payload,
        "pull_ollama_model_with_progress": lambda model, progress_emit=None: (
            calls["downloads"].append(("ollama", model)) or {"provider": "ollama", "model": model}
        ),
    }
    base.update(overrides)
    base["_calls"] = calls
    base["_router"] = router
    return base


@pytest.fixture()
def patched(monkeypatch):
    """Install a dependency surface and hand the test its recorder."""

    def install(**overrides):
        deps = _deps(**overrides)
        monkeypatch.setattr(model_loading, "_get_model_runtime_deps", lambda _state: deps)
        return deps

    return install


def _fake_subprocess(monkeypatch, *, returncode=0, stderr=""):
    ran: list = []
    completed = types.SimpleNamespace(returncode=returncode, stderr=stderr, stdout="")
    monkeypatch.setattr(
        model_loading,
        "subprocess",
        types.SimpleNamespace(run=lambda cmd, **kwargs: ran.append((cmd, kwargs)) or completed),
    )
    return ran


# ── _prepare_model_sources: the synchronous per-engine work ──────────────────


def test_ollama_refuses_to_pull_a_missing_model_without_consent(monkeypatch):
    deps = _deps()
    _fake_subprocess(monkeypatch)

    with pytest.raises(ModelRuntimeError) as err:
        model_loading._prepare_model_sources(deps, "ollama", "wp02-alpha", "ollama:wp02-alpha", False)

    assert err.value.status_code == 409
    assert deps["_calls"]["blocked"] == [("download", "ollama", "wp02-alpha")]


def test_ollama_pulls_through_its_cli_once_consent_is_given(monkeypatch):
    deps = _deps()
    ran = _fake_subprocess(monkeypatch)

    result = model_loading._prepare_model_sources(deps, "ollama", "wp02-alpha", "ollama:wp02-alpha", True)

    assert result["download_result"] == {"provider": "ollama", "model": "wp02-alpha", "returncode": 0}
    command, kwargs = ran[0]
    assert command == ["/usr/bin/ollama", "pull", "wp02-alpha"]
    assert kwargs["timeout"] == 900
    assert kwargs["check"] is False
    assert deps["_calls"]["servers"] == ["ollama"]


def test_a_failed_ollama_pull_surfaces_the_cli_stderr(monkeypatch):
    deps = _deps()
    _fake_subprocess(monkeypatch, returncode=1, stderr="pull model manifest: file does not exist")

    with pytest.raises(ModelRuntimeError) as err:
        model_loading._prepare_model_sources(deps, "ollama", "wp02-alpha", "ollama:wp02-alpha", True)

    assert err.value.status_code == 500
    assert "file does not exist" in str(err.value.detail)


def test_a_failed_ollama_pull_with_no_stderr_still_says_what_happened(monkeypatch):
    deps = _deps()
    _fake_subprocess(monkeypatch, returncode=2, stderr="")

    with pytest.raises(ModelRuntimeError) as err:
        model_loading._prepare_model_sources(deps, "ollama", "wp02-alpha", "ollama:wp02-alpha", True)

    assert "Ollama 모델 다운로드 실패" in str(err.value.detail)


def test_an_already_pulled_ollama_model_needs_neither_consent_nor_a_pull(monkeypatch):
    deps = _deps(get_ollama_pulled_models=lambda: {"wp02-alpha"})
    ran = _fake_subprocess(monkeypatch)

    result = model_loading._prepare_model_sources(deps, "ollama", "wp02-alpha", "ollama:wp02-alpha", False)

    assert result["download_result"] is None
    assert ran == []


def test_vllm_refuses_to_start_a_server_for_absent_weights_without_consent():
    deps = _deps(hf_model_ready=lambda _model, _provider: False)

    with pytest.raises(ModelRuntimeError):
        model_loading._prepare_model_sources(deps, "vllm", "org/wp02", "vllm:org/wp02", False)

    assert deps["_calls"]["blocked"] == [("download", "vllm", "org/wp02")]
    assert deps["_calls"]["servers"] == [], "the server must not start before the weights are allowed"


def test_vllm_starts_its_server_once_the_weights_are_present():
    deps = _deps()

    result = model_loading._prepare_model_sources(deps, "vllm", "org/wp02", "vllm:org/wp02", False)

    assert result["download_result"] == {"provider": "vllm", "model": "org/wp02", "server_ready": True}
    assert deps["_calls"]["servers"] == [("vllm", "org/wp02")]


def test_llamacpp_refuses_to_start_a_server_for_absent_weights_without_consent():
    deps = _deps(hf_model_ready=lambda _model, _provider: False)

    with pytest.raises(ModelRuntimeError):
        model_loading._prepare_model_sources(deps, "llamacpp", "org/wp02", "llamacpp:org/wp02", False)

    assert deps["_calls"]["blocked"] == [("download", "llamacpp", "org/wp02")]
    assert deps["_calls"]["servers"] == []


def test_llamacpp_starts_its_server_once_the_weights_are_present():
    deps = _deps()

    result = model_loading._prepare_model_sources(deps, "llamacpp", "org/wp02", "llamacpp:org/wp02", False)

    assert result["download_result"] == {"provider": "llamacpp", "model": "org/wp02", "server_ready": True}
    assert deps["_calls"]["servers"] == [("llamacpp", "org/wp02")]


def test_lm_studio_refuses_an_undownloaded_model_without_consent():
    deps = _deps(get_lmstudio_models=lambda: [{"key": "some-other-model"}, "not-a-dict"])

    with pytest.raises(ModelRuntimeError) as err:
        model_loading._prepare_model_sources(deps, "lmstudio", "wp02-alpha", "lmstudio:wp02-alpha", False)

    assert err.value.status_code == 409
    assert deps["_calls"]["blocked"] == [("download", "lmstudio", "wp02-alpha")]
    assert deps["_calls"]["lmstudio"] == []


def test_an_already_downloaded_lm_studio_model_loads_without_consent():
    deps = _deps(get_lmstudio_models=lambda: [{"key": "wp02-alpha"}])

    result = model_loading._prepare_model_sources(deps, "lmstudio", "wp02-alpha", "lmstudio:wp02-alpha", False)

    assert result["model_id"] == "lmstudio:wp02-alpha:1"
    assert result["parsed_model"] == "wp02-alpha:1"


# ── prepare_and_load_model ───────────────────────────────────────────────────


def _load(**kwargs):
    async def _scenario():
        return await asyncio.wait_for(
            model_loading.prepare_and_load_model(
                kwargs.pop("model_id", "local_mlx:wp02-model"),
                request=object(),
                runtime_state=object(),
                **kwargs,
            ),
            STREAM_TIMEOUT,
        )

    return asyncio.run(_scenario())


def test_the_legacy_mlx_prefix_is_canonicalised_to_local_mlx(patched):
    """`mlx:` is the old spelling; everything downstream keys off local_mlx."""
    deps = patched(parse_model_ref=lambda _model_id: ("mlx", "wp02-model"))

    result = _load(model_id="mlx:wp02-model")

    assert result["engine"] == "local_mlx"
    assert deps["_calls"]["engines"] == ["local_mlx"]
    assert deps["_router"].loaded[0]["model_id"] == "mlx:wp02-model"


# ── prepare_and_load_model_stream ────────────────────────────────────────────


def _run_stream(model_id="local_mlx:wp02-model", *, events=None, **kwargs):
    collected = events if events is not None else []

    async def _scenario():
        async def _drain():
            async for event in model_loading.prepare_and_load_model_stream(
                model_id, request=object(), runtime_state=object(), **kwargs
            ):
                collected.append(event)

        await asyncio.wait_for(_drain(), STREAM_TIMEOUT)

    asyncio.run(_scenario())
    return collected


def _parse(frames):
    parsed = []
    for frame in frames:
        head, _, body = frame.partition("\n")
        parsed.append((head.removeprefix("event: "), json.loads(body.removeprefix("data: ").strip())))
    return parsed


def _stages(frames):
    return [data.get("stage") for _event, data in _parse(frames)]


def _done(frames):
    events = _parse(frames)
    assert events[-1][0] == "done", f"the stream must end with a done event, got {events[-1][0]}"
    return events[-1][1]


def test_a_stream_for_an_empty_model_id_is_refused_before_any_frame(patched):
    patched(normalize_local_model_request=lambda _model_id, _engine: "   ".strip())
    events: list = []

    with pytest.raises(ModelRuntimeError) as err:
        _run_stream(model_id="   ", events=events)

    assert err.value.status_code == 400
    assert events == []


def test_a_stream_for_an_incompatible_model_carries_the_compatibility_report(patched):
    report = {"supported": False, "reason_code": "mlx_vlm_missing_gemma4_unified_model"}
    patched(_model_runtime_compatibility=lambda _model, engine=None: report)
    events: list = []

    with pytest.raises(ModelRuntimeError) as err:
        _run_stream(events=events)

    assert err.value.status_code == 400
    assert err.value.detail == report
    assert events == []


def test_a_blocked_engine_install_reaches_the_client_as_a_stream_error(patched):
    deps = patched(engine_installed=lambda _provider: False)
    events: list = []

    with pytest.raises(ModelRuntimeError) as err:
        _run_stream(events=events, allow_download=False)

    assert err.value.status_code == 409
    assert err.value.detail == {"capability": "engine_install", "engine": "local_mlx"}
    assert deps["_calls"]["blocked"] == [("install", "local_mlx", None)]
    assert _stages(events) == ["engine"], "the client saw the engine check before the refusal"


def test_an_unexpected_worker_failure_becomes_a_500_with_a_readable_reason(patched):
    def _explode(_provider):
        raise ValueError("engine probe segfaulted")

    patched(ensure_engine_ready=_explode)
    events: list = []

    with pytest.raises(ModelRuntimeError) as err:
        _run_stream(events=events)

    assert err.value.status_code == 500
    assert err.value.detail == {
        "reason": "engine probe segfaulted",
        "model": "local_mlx:wp02-model",
        "engine": "local_mlx",
    }


def test_a_local_path_model_is_used_where_it_already_lives(patched, tmp_path):
    explicit = tmp_path / "wp02-local-model"
    explicit.mkdir()
    patched(parse_model_ref=lambda _model_id: ("mlx", str(explicit)))

    frames = _run_stream(model_id="mlx:" + str(explicit))
    result = _done(frames)

    assert result["engine"] == "local_mlx"
    assert result["download"] == {"model": str(explicit), "path": str(explicit), "cached": True}
    assert result["downloaded"] is False, "pointing at an existing folder moved no bytes"
    assert _stages(frames)[:3] == ["engine", "engine", "download"]


def test_a_missing_local_model_is_not_downloaded_by_the_stream_without_consent(patched):
    deps = patched(hf_model_ready=lambda _model, _provider: False)
    events: list = []

    with pytest.raises(ModelRuntimeError) as err:
        _run_stream(events=events, allow_download=False)

    assert err.value.status_code == 409
    assert deps["_calls"]["blocked"] == [("download", "local_mlx", "wp02-model")]
    assert deps["_calls"]["downloads"] == []


def test_a_missing_local_model_downloads_with_consent_and_reports_it(patched):
    deps = patched(hf_model_ready=lambda _model, _provider: False)

    result = _done(_run_stream(allow_download=True))

    assert deps["_calls"]["downloads"] == [("local_mlx", "wp02-model")]
    assert result["downloaded"] is True
    assert result["download"]["path"] == "/models/wp02-model"


def test_an_already_downloaded_local_model_reports_its_managed_directory(patched):
    patched()

    result = _done(_run_stream())

    assert result["download"] == {
        "model": "wp02-model",
        "path": str(Path("/models") / "wp02-model"),
        "cached": True,
    }
    assert result["downloaded"] is False


def test_the_stream_reports_load_smoke_and_done_in_order(patched):
    deps = patched()

    frames = _run_stream()
    result = _done(frames)

    assert _stages(frames) == ["engine", "engine", "download", "load", "smoke_test", "done", None]
    assert result["status"] == "ok"
    assert result["ready_to_chat"] is True
    assert result["compatibility_status"] == "ok"
    assert result["loaded"] is True
    assert result["current"] == "local_mlx:wp02-model"
    assert result["resolution"]["engine"] == "local_mlx"
    assert deps["_router"].loaded[0]["owner"] == "me@example.com"


def test_a_stream_smoke_test_that_raises_reports_unknown_rather_than_ok(patched):
    async def _explode(_resolution, api_key_override=None):
        raise RuntimeError("engine hung")

    patched(_smoke_test_loaded_model=_explode)

    result = _done(_run_stream())

    assert result["compatibility_status"] == "unknown"
    assert result["ready_to_chat"] is True, "the load itself did succeed"
    assert result["smoke_test"] == {}


def test_the_stream_refuses_an_ollama_pull_without_consent(patched):
    deps = patched()
    events: list = []

    with pytest.raises(ModelRuntimeError):
        _run_stream(model_id="ollama:wp02-alpha", events=events, allow_download=False)

    assert deps["_calls"]["servers"] == ["ollama"]
    assert deps["_calls"]["blocked"] == [("download", "ollama", "wp02-alpha")]
    assert _stages(events) == ["engine", "engine", "engine"]


def test_the_stream_pulls_an_ollama_model_with_consent(patched):
    deps = patched()

    result = _done(_run_stream(model_id="ollama:wp02-alpha", allow_download=True))

    assert deps["_calls"]["downloads"] == [("ollama", "wp02-alpha")]
    assert result["engine"] == "ollama"
    assert result["download"] == {"provider": "ollama", "model": "wp02-alpha"}


def test_an_already_pulled_ollama_model_streams_straight_through(patched):
    deps = patched(get_ollama_pulled_models=lambda: {"wp02-alpha"})

    frames = _run_stream(model_id="ollama:wp02-alpha")
    result = _done(frames)

    assert deps["_calls"]["downloads"] == []
    assert result["download"] == {"provider": "ollama", "model": "wp02-alpha", "cached": True}
    assert result["downloaded"] is False


def test_the_stream_refuses_a_vllm_download_without_consent(patched):
    deps = patched(hf_model_ready=lambda _model, _provider: False)

    with pytest.raises(ModelRuntimeError):
        _run_stream(model_id="vllm:org/wp02", allow_download=False)

    assert deps["_calls"]["servers"] == [], "no server start before the weights are allowed"


def test_the_stream_downloads_then_serves_a_vllm_model(patched):
    deps = patched(hf_model_ready=lambda _model, _provider: False)

    frames = _run_stream(model_id="vllm:org/wp02", allow_download=True)
    result = _done(frames)

    assert deps["_calls"]["downloads"] == [("vllm", "org/wp02")]
    assert deps["_calls"]["servers"] == [("vllm", "org/wp02")]
    assert result["download"]["server_ready"] is True
    assert result["download"]["path"] == "/models/org/wp02"
    assert "server" in _stages(frames)


def test_a_cached_vllm_model_still_starts_its_server(patched):
    deps = patched()

    result = _done(_run_stream(model_id="vllm:org/wp02"))

    assert deps["_calls"]["downloads"] == []
    assert deps["_calls"]["servers"] == [("vllm", "org/wp02")]
    assert result["download"] == {
        "provider": "vllm",
        "model": "org/wp02",
        "cached": True,
        "server_ready": True,
    }


def test_the_stream_refuses_a_llamacpp_download_without_consent(patched):
    deps = patched(hf_model_ready=lambda _model, _provider: False)

    with pytest.raises(ModelRuntimeError):
        _run_stream(model_id="llamacpp:org/wp02", allow_download=False)

    assert deps["_calls"]["servers"] == []


def test_the_stream_downloads_then_serves_a_llamacpp_model(patched):
    deps = patched(hf_model_ready=lambda _model, _provider: False)

    frames = _run_stream(model_id="llamacpp:org/wp02", allow_download=True)
    result = _done(frames)

    assert deps["_calls"]["downloads"] == [("llamacpp", "org/wp02")]
    assert deps["_calls"]["servers"] == [("llamacpp", "org/wp02")]
    assert result["download"]["server_ready"] is True
    assert "server" in _stages(frames)


def test_a_cached_llamacpp_model_still_starts_its_server(patched):
    deps = patched()

    result = _done(_run_stream(model_id="llamacpp:org/wp02"))

    assert deps["_calls"]["servers"] == [("llamacpp", "org/wp02")]
    assert result["download"] == {
        "provider": "llamacpp",
        "model": "org/wp02",
        "cached": True,
        "server_ready": True,
    }


def test_the_stream_refuses_an_undownloaded_lm_studio_model_without_consent(patched):
    deps = patched(get_lmstudio_models=lambda: [{"key": "another-model"}])

    with pytest.raises(ModelRuntimeError):
        _run_stream(model_id="lmstudio:wp02-alpha", allow_download=False)

    assert deps["_calls"]["blocked"] == [("download", "lmstudio", "wp02-alpha")]
    assert deps["_calls"]["lmstudio"] == []


def test_the_stream_loads_the_lm_studio_instance_the_server_handed_back(patched):
    deps = patched()

    frames = _run_stream(model_id="lmstudio:wp02-alpha", allow_download=True)
    result = _done(frames)

    assert deps["_calls"]["lmstudio"] == ["wp02-alpha"]
    assert result["model"] == "lmstudio:wp02-alpha:1"
    assert result["current"] == "lmstudio:wp02-alpha:1"
    assert deps["_router"].loaded[0]["model_id"] == "lmstudio:wp02-alpha:1"
    assert result["download"] == {"instance_id": "wp02-alpha:1"}


def test_a_cloud_model_skips_every_local_engine_step(patched):
    deps = patched(get_user_api_key=lambda _email, provider: "sk-" + provider)

    frames = _run_stream(model_id="openai:gpt-4o-mini")
    result = _done(frames)

    assert deps["_calls"]["engines"] == [], "no local engine is installed for a cloud model"
    assert deps["_calls"]["downloads"] == []
    assert result["engine"] == "openai"
    assert result["download"] is None
    assert result["installed_now"] is False
    assert _stages(frames)[0] == "engine"
    assert deps["_router"].loaded[0]["api_key_override"] == "sk-openai"


def test_a_local_model_never_looks_up_a_cloud_api_key(patched):
    seen: list = []
    deps = patched(get_user_api_key=lambda email, provider: seen.append((email, provider)) or "sk-leak")

    _run_stream()

    assert seen == []
    assert deps["_router"].loaded[0]["api_key_override"] is None
