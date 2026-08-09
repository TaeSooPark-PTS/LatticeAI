"""wp12 coverage — the user-supplied callable provider and the shared batch path.

``CustomEmbeddingProvider`` resolves a dotted ``module:callable`` reference at
first use, so the whole file works against a throwaway module injected into
``sys.modules``; nothing is written to disk and no real package is imported.
The same provider is the cheapest way to drive the shared
``_NetworkEmbeddingProvider.embed_batch`` contract — truncation, the
dimension lock, and the L2 normalization of ordinary, zero, and missing rows.
"""

from __future__ import annotations

import math
import sys
import types

import pytest

from latticeai.core.embedding_providers import (
    CustomEmbeddingProvider,
    EmbeddingUnavailable,
    _RemoteConfig,
)

TARGET_MODULE = "wp12_fake_embedder"


def _install_target(monkeypatch, fn, *, name=TARGET_MODULE, attr="embed_texts"):
    module = types.ModuleType(name)
    if fn is not None:
        setattr(module, attr, fn)
    monkeypatch.setitem(sys.modules, name, module)
    return module


def _provider(monkeypatch, target, **kwargs):
    monkeypatch.delenv("LATTICEAI_EMBEDDING_CUSTOM_TARGET", raising=False)
    extra = {"target": target} if target is not None else {}
    kwargs.setdefault("model", "")
    return CustomEmbeddingProvider(_RemoteConfig(extra=extra, **kwargs))


def _rows(*vectors):
    def _embed_texts(texts):
        assert isinstance(texts, list)
        return [list(v) for v in vectors]

    return _embed_texts


# ── target resolution ────────────────────────────────────────────────────────


def test_the_target_may_come_from_the_config_or_from_the_environment(monkeypatch):
    configured = _provider(monkeypatch, "wp12_fake_embedder:embed_texts", model="")
    assert configured._target_ref == "wp12_fake_embedder:embed_texts"
    assert configured.model_id == "custom:wp12_fake_embedder:embed_texts:384"

    monkeypatch.setenv("LATTICEAI_EMBEDDING_CUSTOM_TARGET", "other.module.embed")
    from_env = CustomEmbeddingProvider(_RemoteConfig(model="", dim=8))
    assert from_env._target_ref == "other.module.embed"
    assert from_env.model_id == "custom:other.module.embed:8"


def test_an_unconfigured_provider_names_itself_a_callable_and_refuses_to_load(monkeypatch):
    prov = _provider(monkeypatch, None, model="")

    assert prov.model_id == "custom:callable:384"
    with pytest.raises(EmbeddingUnavailable, match="custom embedding target not configured"):
        prov.embed_batch(["alpha"])


def test_a_target_without_a_module_part_is_rejected(monkeypatch):
    prov = _provider(monkeypatch, "embed_texts")

    with pytest.raises(EmbeddingUnavailable, match="invalid custom embedding target: embed_texts"):
        prov.embed_batch(["alpha"])


def test_a_target_in_a_module_that_does_not_exist_is_unavailable(monkeypatch):
    prov = _provider(monkeypatch, "wp12_missing_module_xyz:embed_texts")

    with pytest.raises(EmbeddingUnavailable, match="custom embedding target unavailable"):
        prov.embed_batch(["alpha"])


def test_a_module_without_the_named_callable_is_unavailable(monkeypatch):
    _install_target(monkeypatch, _rows([1.0]), attr="something_else")
    prov = _provider(monkeypatch, "wp12_fake_embedder:embed_texts")

    with pytest.raises(EmbeddingUnavailable, match="custom embedding target unavailable"):
        prov.embed_batch(["alpha"])


def test_the_dotted_form_is_accepted_and_the_callable_is_loaded_once(monkeypatch):
    _install_target(monkeypatch, _rows([1.0, 0.0]))
    prov = _provider(monkeypatch, "wp12_fake_embedder.embed_texts", dim=2)

    assert prov.embed_batch(["alpha"]) == [[1.0, 0.0]]

    # once resolved the callable is held, so losing the module does not break
    # an already-running index build
    monkeypatch.delitem(sys.modules, TARGET_MODULE)
    assert prov.embed_batch(["beta"]) == [[1.0, 0.0]]


def test_a_callable_that_raises_is_reported_as_unavailable(monkeypatch):
    def _broken(texts):
        raise ZeroDivisionError("division by zero")

    _install_target(monkeypatch, _broken)
    prov = _provider(monkeypatch, "wp12_fake_embedder:embed_texts")

    with pytest.raises(EmbeddingUnavailable, match="custom embedding failed: division by zero"):
        prov.embed_batch(["alpha"])


def test_health_reports_the_loaded_target(monkeypatch):
    _install_target(monkeypatch, _rows([1.0]))
    prov = _provider(monkeypatch, "wp12_fake_embedder:embed_texts")

    assert prov.health() == {
        "status": "ok",
        "detail": "custom target wp12_fake_embedder:embed_texts loaded",
    }


def test_health_reports_an_unresolvable_target(monkeypatch):
    prov = _provider(monkeypatch, "wp12_missing_module_xyz:embed_texts")

    health = prov.health()

    assert health["status"] == "unavailable"
    assert "custom embedding target unavailable" in health["detail"]


# ── the shared batch contract ────────────────────────────────────────────────


def test_an_empty_batch_short_circuits_before_the_callable(monkeypatch):
    def _never(texts):
        raise AssertionError("the callable must not be invoked for an empty batch")

    _install_target(monkeypatch, _never)
    prov = _provider(monkeypatch, "wp12_fake_embedder:embed_texts")

    assert prov.embed_batch([]) == []


def test_rows_are_normalized_and_the_dimension_is_locked_to_the_real_model(monkeypatch):
    _install_target(monkeypatch, _rows([3.0, 4.0], [0.0, 0.0], []))
    prov = _provider(monkeypatch, "wp12_fake_embedder:embed_texts", dim=384)

    vectors = prov.embed_batch(["alpha", "beta", "gamma"])

    assert [round(x, 6) for x in vectors[0]] == [0.6, 0.8]
    assert math.isclose(sum(x * x for x in vectors[0]), 1.0, rel_tol=1e-9)
    # a zero vector has no direction: normalizing it would divide by zero
    assert vectors[1] == [0.0, 0.0]
    # a row the callable declined to produce is padded to the locked dimension
    assert vectors[2] == [0.0, 0.0]
    assert prov.dim == 2


def test_input_text_is_truncated_and_none_is_coerced_before_the_callable(monkeypatch):
    seen = []

    def _echo(texts):
        seen.extend(texts)
        return [[1.0] for _ in texts]

    _install_target(monkeypatch, _echo)
    prov = _provider(monkeypatch, "wp12_fake_embedder:embed_texts")

    prov.embed_batch(["x" * 60_000, None])

    assert len(seen[0]) == 50_000
    assert seen[1] == ""


def test_a_single_embed_returns_the_first_row_or_a_zero_vector(monkeypatch):
    _install_target(monkeypatch, _rows([0.0, 6.0]))
    prov = _provider(monkeypatch, "wp12_fake_embedder:embed_texts", dim=2)
    assert prov.embed("alpha") == [0.0, 1.0]

    _install_target(monkeypatch, _rows(), name="wp12_empty_embedder")
    empty = _provider(monkeypatch, "wp12_empty_embedder:embed_texts", dim=3)
    assert empty.embed("alpha") == [0.0, 0.0, 0.0]
