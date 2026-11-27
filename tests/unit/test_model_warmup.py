from __future__ import annotations

import logging
import sys
import threading
import time
from types import SimpleNamespace
from typing import Any, Callable, List, Tuple

import pytest
from fastapi import FastAPI

from latticeai.core import rerankers
from latticeai.core.embedding_providers import (
    HuggingFaceEmbeddingProvider,
    MLXEmbeddingProvider,
    _RemoteConfig,
)
from latticeai.core.rerankers import (
    RERANKER_WARMUP_TEXT,
    RERANKER_WARMUP_THREAD,
    CrossEncoderReranker,
    RerankerResolution,
    warm_reranker,
)
from latticeai.runtime.build_phases import worker_profile


class _Model:
    def __init__(self) -> None:
        self.pairs: List[Tuple[str, str]] = []

    def predict(self, pairs: List[Tuple[str, str]], show_progress_bar: bool = False):
        self.pairs.extend(pairs)
        return [0.5 for _ in pairs]


def _resolution(loader: Callable[[str, int], Any]) -> RerankerResolution:
    return RerankerResolution(
        "ready", "m", "cached", CrossEncoderReranker("m", "/snap", loader=loader)
    )


def test_the_conftest_keeps_real_rerankers_out_of_the_suite() -> None:
    assert rerankers.resolve_reranker().status == "disabled"


def test_no_reranker_means_no_warm_up_thread() -> None:
    started: List[Any] = []
    assert warm_reranker(None, start=started.append) is False
    disabled = RerankerResolution("disabled", "m", "off")
    assert warm_reranker(disabled, start=started.append) is False
    assert started == []


def test_a_resolved_reranker_is_loaded_once_by_the_warm_up_task() -> None:
    loads: List[str] = []
    model = _Model()

    def loader(target: str, max_length: int) -> Any:
        loads.append(target)
        return model

    resolution = _resolution(loader)
    tasks: List[Callable[[], None]] = []
    assert warm_reranker(resolution, start=tasks.append) is True
    assert loads == []
    tasks[0]()
    assert loads == ["/snap"]
    assert model.pairs == [(RERANKER_WARMUP_TEXT, RERANKER_WARMUP_TEXT)]
    assert resolution.reranker is not None
    assert resolution.reranker.score("q", ["a"]) == [0.5]
    assert loads == ["/snap"]


def test_a_failed_warm_up_is_logged_and_never_raises(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def broken(target: str, max_length: int) -> Any:
        raise OSError("weights vanished")

    tasks: List[Callable[[], None]] = []
    warm_reranker(_resolution(broken), start=tasks.append)
    with caplog.at_level(logging.WARNING, logger=rerankers.__name__):
        tasks[0]()
    assert "warm-up failed" in caplog.text
    assert "weights vanished" in caplog.text


def test_a_warm_up_that_cannot_start_leaves_the_worker_running(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def refuse(task: Callable[[], None]) -> None:
        raise RuntimeError("can't start new thread")

    with caplog.at_level(logging.WARNING, logger=rerankers.__name__):
        assert warm_reranker(_resolution(lambda t, n: _Model()), start=refuse) is False
    assert "could not start" in caplog.text


def test_the_default_starter_runs_the_task_on_a_named_daemon_thread() -> None:
    ran = threading.Event()
    seen: List[threading.Thread] = []

    def task() -> None:
        seen.append(threading.current_thread())
        ran.set()

    thread = rerankers._start_daemon(task)
    thread.join(timeout=5)
    assert ran.is_set()
    assert seen == [thread]
    assert thread.daemon is True
    assert thread.name == RERANKER_WARMUP_THREAD


def test_the_worker_phase_warms_the_reranker_it_mounted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolution = RerankerResolution("disabled", "m", "off")
    mounted: List[Any] = []
    warmed: List[Any] = []
    import latticeai.api.worker_compute as compute

    original = compute.create_worker_compute_router

    def spy(**kwargs: Any) -> Any:
        mounted.append(kwargs["reranker"])
        return original(**kwargs)

    monkeypatch.setattr(compute, "create_worker_compute_router", spy)
    monkeypatch.setattr(rerankers, "resolve_reranker", lambda: resolution)
    monkeypatch.setattr(rerankers, "warm_reranker", warmed.append)
    monkeypatch.setattr(worker_profile, "apply_worker_route_filter", lambda app: None)
    worker_profile.phase_worker_routes(
        SimpleNamespace(
            app=FastAPI(),
            require_user=lambda request: "u",
            enforce_rate_limit=lambda user, bucket: None,
            model_router=None,
            EMBEDDER=None,
            MULTIMODAL_PORTS=None,
        )
    )
    assert mounted == [resolution]
    assert warmed == [resolution]


def _hf_provider() -> Any:
    return HuggingFaceEmbeddingProvider(
        _RemoteConfig(model="sentence-transformers/all-MiniLM-L6-v2", dim=8)
    )


def _mlx_provider() -> Any:
    return MLXEmbeddingProvider(
        _RemoteConfig(model="mlx-community/all-MiniLM-L6-v2-4bit", dim=8)
    )


PROVIDERS = pytest.mark.parametrize(
    "make_provider", [_hf_provider, _mlx_provider], ids=["huggingface", "mlx"]
)


@PROVIDERS
def test_concurrent_embedder_loads_open_the_model_once(
    monkeypatch: pytest.MonkeyPatch, make_provider: Callable[[], Any]
) -> None:
    provider = make_provider()
    opened: List[int] = []
    gate = threading.Barrier(4)
    encoder = ("sentence_transformers", object(), None)

    def slow_open() -> Any:
        opened.append(1)
        time.sleep(0.05)
        return encoder

    monkeypatch.setattr(provider, "_open_encoder", slow_open)
    results: List[Any] = []

    def worker() -> None:
        gate.wait()
        results.append(provider._load())

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    assert opened == [1]
    assert results == [encoder] * 4


@PROVIDERS
def test_a_load_that_lost_the_race_reuses_the_winner(
    monkeypatch: pytest.MonkeyPatch, make_provider: Callable[[], Any]
) -> None:
    provider = make_provider()
    winner = ("sentence_transformers", object(), None)

    class _LostRace:
        def __enter__(self) -> None:
            provider._encoder = winner

        def __exit__(self, *exc: Any) -> None:
            return None

    def must_not_open() -> Any:
        raise AssertionError("the winner already opened the model")

    monkeypatch.setattr(provider, "_load_lock", _LostRace())
    monkeypatch.setattr(provider, "_open_encoder", must_not_open)
    assert provider._load() is winner


def test_a_missing_mlx_runtime_is_reported_as_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from latticeai.core.embedding_providers import EmbeddingUnavailable

    monkeypatch.setitem(sys.modules, "mlx_embeddings", None)
    monkeypatch.setitem(sys.modules, "mlx_embeddings.utils", None)
    provider = _mlx_provider()
    with pytest.raises(EmbeddingUnavailable, match="MLX embedding model unavailable"):
        provider._load()
    assert provider._encoder is None
