"""wp12 coverage — the MLX provider against an injected fake mlx runtime.

``mlx``/``mlx_embeddings`` are Apple-Silicon-only optional dependencies, so the
real ones would make these paths untestable on the Linux coverage leg (and
would download model weights on macOS). Both are injected into ``sys.modules``
per test via ``monkeypatch.setitem``, with just the surface the provider
touches: ``load`` → ``(model, tokenizer)``, ``mx.array``, ``mx.mean`` and the
``ndim``/``__getitem__``/``tolist`` trio. The pooled means below are real
arithmetic, so the asserted vectors are the ones the provider would produce
from a genuine encoder with the same output.
"""

from __future__ import annotations

import math
import sys
import types

import pytest

from latticeai.core.embedding_providers import (
    EmbeddingUnavailable,
    MLXEmbeddingProvider,
    _RemoteConfig,
)


class _FakeTensor:
    """The slice of the ``mx.array`` surface ``_embed_raw`` actually uses."""

    def __init__(self, data):
        self._data = data

    @property
    def ndim(self):
        rank, node = 0, self._data
        while isinstance(node, list):
            rank += 1
            node = node[0] if node else None
        return rank

    def __getitem__(self, index):
        return _FakeTensor(self._data[index])

    def tolist(self):
        return self._data


def _fake_core():
    core = types.ModuleType("mlx.core")

    def _array(data):
        return _FakeTensor(data)

    def _mean(tensor, axis=1):
        assert axis == 1, "the provider only ever pools over the token axis"
        pooled = []
        for rows in tensor.tolist():
            width = len(rows[0])
            pooled.append([sum(row[i] for row in rows) / len(rows) for i in range(width)])
        return _FakeTensor(pooled)

    core.array = _array
    core.mean = _mean
    return core


class _FakeTokenizer:
    def __init__(self):
        self.seen = []

    def encode(self, text):
        self.seen.append(text)
        return [1, 2, 3]


def _install_mlx(monkeypatch, load_fn):
    """Put fake ``mlx`` and ``mlx_embeddings`` packages in ``sys.modules``."""
    utils = types.ModuleType("mlx_embeddings.utils")
    utils.load = load_fn
    package = types.ModuleType("mlx_embeddings")
    package.utils = utils
    monkeypatch.setitem(sys.modules, "mlx_embeddings", package)
    monkeypatch.setitem(sys.modules, "mlx_embeddings.utils", utils)

    core = _fake_core()
    mlx = types.ModuleType("mlx")
    mlx.core = core
    monkeypatch.setitem(sys.modules, "mlx", mlx)
    monkeypatch.setitem(sys.modules, "mlx.core", core)


def _loader(model, tokenizer, calls):
    def _load(name):
        calls.append(name)
        return model, tokenizer

    return _load


def _provider(model="bge-m3", **kwargs):
    return MLXEmbeddingProvider(_RemoteConfig(model=model, **kwargs))


def test_the_mlx_provider_names_its_index_before_any_model_is_loaded():
    prov = _provider(dim=8)

    assert prov.provider == "mlx"
    assert prov.dim == 8
    assert prov.model_id == "mlx:bge-m3:8"
    assert prov._encoder is None


def test_a_three_dimensional_encoder_output_is_mean_pooled_over_the_tokens(monkeypatch):
    tokenizer = _FakeTokenizer()
    calls = []

    def _model(tokens):
        assert tokens.tolist() == [[1, 2, 3]]
        return _FakeTensor([[[1.0, 0.0], [3.0, 0.0]]])

    _install_mlx(monkeypatch, _loader(_model, tokenizer, calls))
    prov = _provider()

    vectors = prov.embed_batch(["alpha", "beta"])

    # mean over the token axis is [2.0, 0.0]; embed_batch L2-normalizes it
    assert vectors == [[1.0, 0.0], [1.0, 0.0]]
    assert tokenizer.seen == ["alpha", "beta"]
    # the model is loaded once and then cached for the life of the provider
    assert calls == ["bge-m3"]
    assert prov.dim == 2
    assert prov.model_id == "mlx:bge-m3:2"


def test_a_tuple_encoder_output_uses_its_first_element_and_a_flat_row(monkeypatch):
    def _model(tokens):
        return (_FakeTensor([[3.0, 4.0]]), {"attention": "ignored"})

    _install_mlx(monkeypatch, _loader(_model, _FakeTokenizer(), []))
    prov = _provider(model="e5-large")

    assert prov.model_id == "mlx:e5-large:384", "the configured width names the index"
    vectors = prov.embed_batch(["already pooled"])

    assert [round(x, 6) for x in vectors[0]] == [0.6, 0.8]
    assert math.isclose(sum(x * x for x in vectors[0]), 1.0, rel_tol=1e-9)
    # the encoder answered in 2 dimensions, so the index identity follows it
    # instead of staying frozen at the width nobody measured
    assert prov.dim == 2
    assert prov.model_id == "mlx:e5-large:2"


def test_a_pooled_row_that_is_a_scalar_is_reported_as_unavailable(monkeypatch):
    def _model(tokens):
        # a 1-D output: indexing it yields a scalar, not a vector
        return _FakeTensor([7.5, 8.5])

    _install_mlx(monkeypatch, _loader(_model, _FakeTokenizer(), []))
    prov = _provider()

    with pytest.raises(EmbeddingUnavailable, match="produced a scalar, not a vector"):
        prov.embed_batch(["scalar"])


def test_health_reports_the_loaded_model_and_reuses_the_cached_encoder(monkeypatch):
    calls = []
    _install_mlx(monkeypatch, _loader(lambda tokens: None, _FakeTokenizer(), calls))
    prov = _provider(model="gte-large")

    assert prov.health() == {"status": "ok", "detail": "MLX model gte-large loaded"}
    assert prov.health()["status"] == "ok"
    assert calls == ["gte-large"]


def test_health_reports_a_runtime_that_cannot_load_the_model(monkeypatch):
    def _load(name):
        raise OSError("no metal device")

    _install_mlx(monkeypatch, _load)
    prov = _provider(model="bge-m3")

    health = prov.health()

    assert health["status"] == "unavailable"
    assert "MLX embedding model unavailable" in health["detail"]
    assert "no metal device" in health["detail"]
