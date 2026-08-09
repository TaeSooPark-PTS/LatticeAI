"""LM Studio model resolution — cache, key matching, download job, load.

`ensure_lmstudio_model` is the one engine path that talks to a *running*
third-party app over HTTP and can sit in a download-poll loop for an hour. Its
HTTP seam (`_json_request`), its clock and its model listing are all replaced
here, so every branch — including the failure and timeout ones an operator only
ever meets on a bad day — runs in milliseconds and on any platform.
"""

from __future__ import annotations

import io
import types
import urllib.error

import pytest

from latticeai.services import model_runtime
from latticeai.services.model_errors import ModelRuntimeError

BASE = "http://127.0.0.1:1234"


@pytest.fixture(autouse=True)
def _pinned_lmstudio_base(monkeypatch):
    """Pin the base URL so asserted request URLs never depend on the host."""
    monkeypatch.setenv("LMSTUDIO_BASE_URL", BASE + "/v1")
    monkeypatch.setenv("LMSTUDIO_API_KEY", "test-key")


def _frozen_clock(monkeypatch, values, slept=None):
    """Install a scripted clock in model_runtime only; never sleep for real."""
    remaining = list(values)
    state = {"value": remaining[-1]}

    def _time():
        if remaining:
            state["value"] = remaining.pop(0)
        return state["value"]

    monkeypatch.setattr(
        model_runtime,
        "time",
        types.SimpleNamespace(
            time=_time,
            monotonic=_time,
            sleep=(slept.append if slept is not None else (lambda _s: None)),
        ),
    )


def _http_error(status: int, body: bytes, reason: str = "Server Error") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(BASE + "/x", status, reason, {}, io.BytesIO(body))


# ── get_lmstudio_models ──────────────────────────────────────────────────────


def _reset_cache(monkeypatch, entries=(), *, ts=0.0):
    monkeypatch.setattr(model_runtime, "_LMSTUDIO_MODELS_CACHE", list(entries))
    monkeypatch.setattr(model_runtime, "_LMSTUDIO_MODELS_CACHE_TS", ts)


def test_a_fresh_cache_is_served_without_touching_lm_studio(monkeypatch):
    _reset_cache(monkeypatch, [{"key": "cached-model"}], ts=500.0)
    _frozen_clock(monkeypatch, [505.0])
    monkeypatch.setattr(
        model_runtime,
        "_json_request",
        lambda *_a, **_k: pytest.fail("a fresh cache must not re-query LM Studio"),
    )

    assert model_runtime.get_lmstudio_models() == [{"key": "cached-model"}]


def test_force_refreshes_the_cache_and_records_the_new_timestamp(monkeypatch):
    _reset_cache(monkeypatch, [{"key": "stale"}], ts=500.0)
    _frozen_clock(monkeypatch, [505.0])
    calls: list = []

    def _request(url, **kwargs):
        calls.append((url, kwargs))
        return {"models": [{"key": "fresh"}]}

    monkeypatch.setattr(model_runtime, "_json_request", _request)

    assert model_runtime.get_lmstudio_models(force=True) == [{"key": "fresh"}]
    assert model_runtime._LMSTUDIO_MODELS_CACHE == [{"key": "fresh"}]
    assert model_runtime._LMSTUDIO_MODELS_CACHE_TS == 505.0

    url, kwargs = calls[0]
    assert url == BASE + "/api/v1/models"
    assert kwargs["headers"] == {"Authorization": "Bearer test-key"}


def test_an_expired_cache_is_refetched(monkeypatch):
    _reset_cache(monkeypatch, [{"key": "stale"}], ts=100.0)
    _frozen_clock(monkeypatch, [500.0])
    monkeypatch.setattr(model_runtime, "_json_request", lambda *_a, **_k: {"models": [{"key": "fresh"}]})

    assert model_runtime.get_lmstudio_models() == [{"key": "fresh"}]


def test_an_unreachable_lm_studio_returns_the_last_known_list(monkeypatch):
    """A stopped Local Server must not erase what the UI already showed."""
    _reset_cache(monkeypatch, [{"key": "last-known"}], ts=100.0)
    _frozen_clock(monkeypatch, [500.0])

    def _boom(*_a, **_k):
        raise OSError("connection refused")

    monkeypatch.setattr(model_runtime, "_json_request", _boom)

    assert model_runtime.get_lmstudio_models() == [{"key": "last-known"}]
    assert model_runtime._LMSTUDIO_MODELS_CACHE_TS == 100.0, "a failed probe must not count as fresh"


def test_a_payload_without_a_model_list_caches_an_empty_list(monkeypatch):
    _reset_cache(monkeypatch, [{"key": "stale"}], ts=100.0)
    _frozen_clock(monkeypatch, [500.0])
    monkeypatch.setattr(model_runtime, "_json_request", lambda *_a, **_k: {"models": None})

    assert model_runtime.get_lmstudio_models() == []


# ── key matching ─────────────────────────────────────────────────────────────


def test_candidate_keys_are_empty_for_a_blank_model_name():
    assert model_runtime._lmstudio_candidate_keys("   ") == []


def test_candidate_keys_widen_from_the_full_ref_down_to_a_family_prefix():
    keys = model_runtime._lmstudio_candidate_keys("MLX-Community/Gemma-4-12b-it-4bit-AWQ")

    assert keys == [
        "mlx-community/gemma-4-12b-it-4bit-awq",
        "gemma-4-12b-it-4bit",
        "gemma-4-12b-it",
    ]


def test_no_downloaded_models_means_no_key():
    assert model_runtime._find_lmstudio_model_key("qwen3-8b-instruct", []) is None


def test_an_exact_key_wins_over_a_fuzzy_one():
    models = [
        {"key": "org/qwen3-8b-instruct-mlx", "display_name": "Qwen3 8B"},
        {"key": "qwen3-8b-instruct", "display_name": "Qwen3 8B Instruct"},
        {"key": "unrelated-model", "display_name": "Something Else"},
    ]

    assert model_runtime._find_lmstudio_model_key("Qwen/qwen3-8b-instruct", models) == "qwen3-8b-instruct"


def test_a_substring_match_is_used_when_nothing_matches_exactly():
    models = [
        {"key": "unrelated-model", "display_name": "Something Else"},
        {"key": "org/qwen3-8b-instruct-mlx", "display_name": "Qwen3 8B"},
    ]

    assert model_runtime._find_lmstudio_model_key("qwen3-8b-instruct", models) == "org/qwen3-8b-instruct-mlx"


def test_a_model_nothing_recognises_resolves_to_no_key():
    models = [{"key": "unrelated-model", "display_name": "Something Else"}]

    assert model_runtime._find_lmstudio_model_key("wp02-nothing-like-this", models) is None


# ── ensure_lmstudio_model ────────────────────────────────────────────────────


def _install_lmstudio(monkeypatch, *, models, handlers, listed_after_download=None):
    """Wire ensure_lmstudio_model to fakes and return the recorded calls."""
    recorded: dict = {"server_started": 0, "requests": [], "forced_lists": 0}
    listings = {"value": list(models)}

    def _ensure_server():
        recorded["server_started"] += 1

    def _list(*, force=False):
        if force:
            recorded["forced_lists"] += 1
            if listed_after_download is not None:
                listings["value"] = list(listed_after_download)
        return listings["value"]

    def _request(url, **kwargs):
        recorded["requests"].append((url, kwargs))
        for suffix, handler in handlers:
            if suffix in url:
                return handler(url, kwargs)
        raise AssertionError(f"unexpected LM Studio request: {url}")

    monkeypatch.setattr(model_runtime, "ensure_lmstudio_server", _ensure_server)
    monkeypatch.setattr(model_runtime, "get_lmstudio_models", _list)
    monkeypatch.setattr(model_runtime, "_json_request", _request)
    return recorded


def test_an_already_loaded_model_is_reported_as_cached_without_loading_it(monkeypatch):
    recorded = _install_lmstudio(
        monkeypatch,
        models=[{"key": "qwen3-8b-instruct", "loaded_instances": [{"id": "qwen3-8b-instruct:1"}]}],
        handlers=[],
    )

    result = model_runtime.ensure_lmstudio_model("qwen3-8b-instruct")

    assert result == {
        "provider": "lmstudio",
        "model": "qwen3-8b-instruct",
        "resolved_model": "qwen3-8b-instruct",
        "server_ready": True,
        "cached": True,
    }
    assert recorded["server_started"] == 1
    assert recorded["requests"] == [], "a loaded model needs no HTTP work at all"


def test_a_downloaded_but_unloaded_model_is_loaded_and_reports_its_instance(monkeypatch):
    recorded = _install_lmstudio(
        monkeypatch,
        models=[{"key": "qwen3-8b-instruct", "loaded_instances": []}],
        handlers=[("/models/load", lambda *_a: {"status": "loaded", "instance_id": "qwen3-8b-instruct:2"})],
    )

    result = model_runtime.ensure_lmstudio_model("qwen3-8b-instruct")

    assert result["instance_id"] == "qwen3-8b-instruct:2"
    assert result["cached"] is False
    assert result["resolved_model"] == "qwen3-8b-instruct"

    url, kwargs = recorded["requests"][0]
    assert url == BASE + "/api/v1/models/load"
    assert kwargs["payload"] == {"model": "qwen3-8b-instruct", "context_length": 4096}


def test_a_load_that_does_not_end_in_loaded_is_a_failure(monkeypatch):
    _install_lmstudio(
        monkeypatch,
        models=[{"key": "qwen3-8b-instruct", "loaded_instances": []}],
        handlers=[("/models/load", lambda *_a: {"status": "error", "reason": "out of memory"})],
    )

    with pytest.raises(ModelRuntimeError) as err:
        model_runtime.ensure_lmstudio_model("qwen3-8b-instruct")

    assert err.value.status_code == 500
    assert "out of memory" in str(err.value.detail)


def test_a_load_http_error_surfaces_the_servers_own_body(monkeypatch):
    def _fail(*_a):
        raise _http_error(500, b"model too large for this machine")

    _install_lmstudio(
        monkeypatch,
        models=[{"key": "qwen3-8b-instruct", "loaded_instances": []}],
        handlers=[("/models/load", _fail)],
    )

    with pytest.raises(ModelRuntimeError) as err:
        model_runtime.ensure_lmstudio_model("qwen3-8b-instruct")

    assert err.value.status_code == 500
    assert "model too large for this machine" in str(err.value.detail)


def test_a_load_transport_failure_is_reported_as_a_load_failure(monkeypatch):
    def _fail(*_a):
        raise OSError("connection reset by peer")

    _install_lmstudio(
        monkeypatch,
        models=[{"key": "qwen3-8b-instruct", "loaded_instances": []}],
        handlers=[("/models/load", _fail)],
    )

    with pytest.raises(ModelRuntimeError) as err:
        model_runtime.ensure_lmstudio_model("qwen3-8b-instruct")

    assert err.value.status_code == 500
    assert "로드 실패" in str(err.value.detail)
    assert "connection reset by peer" in str(err.value.detail)


def test_an_unknown_model_is_downloaded_then_loaded(monkeypatch):
    recorded = _install_lmstudio(
        monkeypatch,
        models=[],
        handlers=[
            ("/models/download", lambda *_a: {"status": "completed", "job_id": ""}),
            ("/models/load", lambda *_a: {"status": "loaded", "instance_id": "qwen3-8b-instruct:1"}),
        ],
        listed_after_download=[{"key": "qwen3-8b-instruct", "loaded_instances": []}],
    )

    result = model_runtime.ensure_lmstudio_model("qwen3-8b-instruct")

    assert result["resolved_model"] == "qwen3-8b-instruct"
    assert recorded["forced_lists"] == 1, "the listing must be re-read after a download"
    assert [url for url, _ in recorded["requests"]] == [
        BASE + "/api/v1/models/download",
        BASE + "/api/v1/models/load",
    ]


def test_a_download_http_error_names_the_servers_reason(monkeypatch):
    def _fail(*_a):
        raise _http_error(404, b"", reason="Not Found")

    _install_lmstudio(monkeypatch, models=[], handlers=[("/models/download", _fail)])

    with pytest.raises(ModelRuntimeError) as err:
        model_runtime.ensure_lmstudio_model("wp02-missing-model")

    assert err.value.status_code == 500
    assert "Not Found" in str(err.value.detail)


def test_a_download_transport_failure_is_reported_as_a_download_failure(monkeypatch):
    def _fail(*_a):
        raise OSError("no route to host")

    _install_lmstudio(monkeypatch, models=[], handlers=[("/models/download", _fail)])

    with pytest.raises(ModelRuntimeError) as err:
        model_runtime.ensure_lmstudio_model("wp02-missing-model")

    assert err.value.status_code == 500
    assert "다운로드 실패" in str(err.value.detail)
    assert "no route to host" in str(err.value.detail)


def test_a_queued_download_is_polled_until_it_completes(monkeypatch):
    slept: list = []
    _frozen_clock(monkeypatch, [0.0], slept=slept)
    polls = iter([{"status": "downloading"}, {"status": "completed"}])
    recorded = _install_lmstudio(
        monkeypatch,
        models=[],
        handlers=[
            ("/models/download/status/", lambda *_a: next(polls)),
            ("/models/download", lambda *_a: {"status": "queued", "job_id": "job-1"}),
            ("/models/load", lambda *_a: {"status": "loaded", "instance_id": "qwen:1"}),
        ],
        listed_after_download=[{"key": "qwen3-8b-instruct", "loaded_instances": []}],
    )

    result = model_runtime.ensure_lmstudio_model("qwen3-8b-instruct")

    assert result["instance_id"] == "qwen:1"
    assert slept == [2], "one wait between the two polls, and none after completion"
    assert recorded["requests"][1][0] == BASE + "/api/v1/models/download/status/job-1"


def test_a_failed_download_job_stops_the_flow(monkeypatch):
    _frozen_clock(monkeypatch, [0.0])
    _install_lmstudio(
        monkeypatch,
        models=[],
        handlers=[
            ("/models/download/status/", lambda *_a: {"status": "failed", "error": "disk full"}),
            ("/models/download", lambda *_a: {"status": "queued", "job_id": "job-1"}),
        ],
    )

    with pytest.raises(ModelRuntimeError) as err:
        model_runtime.ensure_lmstudio_model("qwen3-8b-instruct")

    assert err.value.status_code == 500
    assert "disk full" in str(err.value.detail)


def test_a_download_that_outlives_its_deadline_is_a_timeout(monkeypatch):
    # First reading sets the deadline; the next one is already past it.
    _frozen_clock(monkeypatch, [0.0, 10_000.0])
    _install_lmstudio(
        monkeypatch,
        models=[],
        handlers=[("/models/download", lambda *_a: {"status": "queued", "job_id": "job-1"})],
    )

    with pytest.raises(ModelRuntimeError) as err:
        model_runtime.ensure_lmstudio_model("qwen3-8b-instruct")

    assert err.value.status_code == 408
    assert "시간이 초과" in str(err.value.detail)
