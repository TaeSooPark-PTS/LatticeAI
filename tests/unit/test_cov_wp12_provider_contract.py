"""wp12 coverage — the EmbeddingProvider interface contract and the factory.

Everything in this file is offline. The shared codec/similarity/health surface
is exercised through a minimal subclass that implements only the one required
method (``embed_batch``), exactly as the module docstring says a provider must;
the factory is checked name-by-name; and ``resolve_embedder`` is pushed through
each of its degradation rules. The two resolutions that need a *failing* probe
patch the probe itself rather than reaching for a socket, so the outcome is the
same on every machine.
"""

from __future__ import annotations

import math

import pytest

from latticeai.core import embedding_providers as ep
from latticeai.core.embedding_providers import (
    CustomEmbeddingProvider,
    EmbeddingProvider,
    HashEmbeddingProvider,
    MLXEmbeddingProvider,
    OllamaEmbeddingProvider,
    OpenAICompatibleEmbeddingProvider,
    build_embedding_provider,
    resolve_embedder,
    resolve_embedding_profile,
)


class _ListProvider(EmbeddingProvider):
    """A third-party embedder that implements only the required method."""

    provider = "test"

    def __init__(self, rows):
        self._rows = dict(rows)
        self.model_id = "test-list:3"
        self.dim = 3

    def embed_batch(self, texts):
        return [self._rows[t] for t in texts if t in self._rows]


# ── the shared half of the interface ─────────────────────────────────────────


def test_a_single_embed_is_served_by_the_subclass_batch_method():
    prov = _ListProvider({"north": [1.0, 0.0, 0.0]})

    assert prov.embed("north") == [1.0, 0.0, 0.0]


def test_a_single_embed_falls_back_to_a_zero_vector_when_the_batch_is_empty():
    prov = _ListProvider({})

    assert prov.embed("nothing indexed") == [0.0, 0.0, 0.0]


def test_a_subclass_inherits_a_ready_health_and_the_index_identity():
    prov = _ListProvider({})

    assert prov.health() == {"status": "ok", "detail": "ready"}
    assert prov.metadata() == {
        "provider": "test",
        "model": "test-list:3",
        "model_id": "test-list:3",
        "dim": 3,
        "grade": "production",
    }


def test_decoding_an_empty_payload_yields_no_vector():
    assert HashEmbeddingProvider(dim=8).decode(b"") == []


def test_decoding_trusts_the_payload_length_over_a_disagreeing_dimension():
    prov = HashEmbeddingProvider(dim=8)
    payload = prov.encode([1.0, 2.0, 3.0])

    assert len(payload) == 12
    # the provider's own dim (8) and an explicit wrong dim (99) both lose to
    # the 12 bytes actually on disk
    assert prov.decode(payload) == [1.0, 2.0, 3.0]
    assert prov.decode(payload, dim=99) == [1.0, 2.0, 3.0]


def test_similarity_refuses_to_compare_vectors_from_different_models():
    prov = HashEmbeddingProvider(dim=4)

    with pytest.raises(ValueError, match="dimension mismatch: 4 vs 3"):
        prov.similarity([0.5, 0.5, 0.5, 0.5], [0.5, 0.5, 0.5])


def test_similarity_of_a_normalized_vector_with_itself_is_one():
    prov = HashEmbeddingProvider(dim=64)
    vec = prov.embed("hybrid retrieval over the knowledge graph")

    assert math.isclose(prov.similarity(vec, vec), 1.0, rel_tol=1e-6)


# ── profiles ─────────────────────────────────────────────────────────────────


def test_an_unknown_profile_is_rejected_by_name():
    with pytest.raises(ValueError, match="unknown embedding profile"):
        resolve_embedding_profile("openai:text-embedding-9-enormous")


def test_an_empty_profile_selects_nothing_rather_than_failing():
    assert resolve_embedding_profile("") == {}


# ── factory ──────────────────────────────────────────────────────────────────


def test_every_hash_alias_builds_the_offline_fallback():
    for alias in ("hash", "local", "fallback", ""):
        prov = build_embedding_provider(alias, dim=32)
        assert isinstance(prov, HashEmbeddingProvider)
        assert prov.dim == 32
        assert prov.grade == "fallback"


def test_the_factory_maps_each_provider_name_to_its_class_without_touching_the_network():
    mlx = build_embedding_provider("mlx", model="bge-m3")
    assert isinstance(mlx, MLXEmbeddingProvider)
    assert mlx.model_id == "mlx:bge-m3:384"

    ollama = build_embedding_provider("ollama", model="nomic-embed-text")
    assert isinstance(ollama, OllamaEmbeddingProvider)
    assert ollama.model_id == "ollama:nomic-embed-text:768"

    for alias in ("openai", "openai-compatible", "openai_compatible"):
        openai = build_embedding_provider(alias, model="text-embedding-3-small")
        assert isinstance(openai, OpenAICompatibleEmbeddingProvider)
        assert openai.model_id == "openai:text-embedding-3-small:1536"

    custom = build_embedding_provider("custom", extra={"target": "pkg.mod:embed"})
    assert isinstance(custom, CustomEmbeddingProvider)
    assert custom.model_id == "custom:pkg.mod:embed:384"


def test_an_unknown_provider_name_is_rejected_by_the_factory():
    with pytest.raises(ValueError, match="unknown embedding provider"):
        build_embedding_provider("teleport")


# ── resolution ───────────────────────────────────────────────────────────────


def test_resolution_falls_back_when_the_requested_provider_cannot_be_built():
    resolved = resolve_embedder("teleport", dim=16)

    assert resolved.fell_back is True
    assert resolved.requested == "teleport"
    assert resolved.active == "hash"
    assert resolved.health["status"] == "unavailable"
    assert "unknown embedding provider" in resolved.health["detail"]
    assert resolved.detail == "could not construct teleport; using hash fallback"
    # the caller still receives a working embedder
    assert len(resolved.provider.embed("still works")) == 16


def test_skipping_the_probe_keeps_the_requested_provider_and_says_it_is_unverified():
    resolved = resolve_embedder(
        "ollama", model="nomic-embed-text", base_url="http://127.0.0.1:9", probe=False
    )

    assert resolved.fell_back is False
    assert resolved.active == "ollama"
    assert resolved.health == {"status": "unknown", "detail": "not probed"}
    assert resolved.detail == ""
    assert resolved.as_dict()["model_id"] == "ollama:nomic-embed-text:768"


def test_a_health_probe_that_raises_degrades_instead_of_crashing_startup(monkeypatch):
    def _explode(self):
        raise RuntimeError("probe exploded")

    monkeypatch.setattr(ep.OllamaEmbeddingProvider, "health", _explode)

    resolved = resolve_embedder("ollama", model="bge-m3", base_url="http://ollama.invalid")

    assert resolved.fell_back is True
    assert resolved.active == "hash"
    assert resolved.health == {"status": "unavailable", "detail": "probe exploded"}
    assert "using hash fallback" in resolved.detail
