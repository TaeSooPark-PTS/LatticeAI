"""v11.0.1 — the embedding index identity tracks the dimension it is filed at.

``model_id`` is what the knowledge graph stores next to every vector, and it
ends in the dimension those vectors have. Two ways it used to lie (D8):

* ``MLXEmbeddingProvider`` never guessed a width from the model name the way
  the Ollama and OpenAI providers do, so an unpinned ``mlx:bge-m3`` named
  itself ``:384`` while the model is 1024-wide.
* ``embed_batch`` locks ``dim`` to the width the model actually returned, but
  left the id at whatever it was built with — so vectors were filed under a
  dimension they did not have.

Everything here is offline: the dimension guess needs no model at all, and the
batch path is driven through ``CustomEmbeddingProvider``, whose "model" is a
callable injected into ``sys.modules`` (the ``test_cov_wp12_custom_provider``
idiom).
"""

from __future__ import annotations

import sys
import types
from typing import List, Sequence

from latticeai.core.embedding_providers import (
    DEFAULT_EMBEDDING_DIM,
    CustomEmbeddingProvider,
    MLXEmbeddingProvider,
    OllamaEmbeddingProvider,
    OpenAICompatibleEmbeddingProvider,
    _NetworkEmbeddingProvider,
    _RemoteConfig,
)

TARGET_MODULE = "fix_v1101_fake_embedder"


def _custom(monkeypatch, rows: List[List[float]], **kwargs) -> CustomEmbeddingProvider:
    """A provider whose "model" answers with ``rows``."""
    module = types.ModuleType(TARGET_MODULE)
    module.embed_texts = lambda texts: rows
    monkeypatch.setitem(sys.modules, TARGET_MODULE, module)
    kwargs.setdefault("model", "")
    return CustomEmbeddingProvider(
        _RemoteConfig(extra={"target": f"{TARGET_MODULE}:embed_texts"}, **kwargs)
    )


# ── (a) MLX names its index like every other model-backed provider ────────
def test_mlx_guesses_the_width_of_a_known_model_when_none_is_pinned():
    prov = MLXEmbeddingProvider(_RemoteConfig(model="bge-m3", dim=0))

    # the same guess Ollama and OpenAI already made for the same model name
    assert prov.dim == 1024
    assert prov.model_id == "mlx:bge-m3:1024"
    assert OllamaEmbeddingProvider(_RemoteConfig(model="bge-m3", dim=0)).dim == 1024


def test_mlx_falls_back_to_the_default_width_for_a_model_it_does_not_know():
    prov = MLXEmbeddingProvider(_RemoteConfig(model="some-org/private-embedder", dim=0))

    assert prov.dim == DEFAULT_EMBEDDING_DIM
    assert prov.model_id == "mlx:some-org/private-embedder:384"


def test_a_pinned_width_still_wins_over_the_guess():
    prov = MLXEmbeddingProvider(_RemoteConfig(model="bge-m3", dim=64))

    assert prov.dim == 64
    assert prov.model_id == "mlx:bge-m3:64"


# ── (b) the measured width rewrites the id ────────────────────────────────
def test_a_measured_width_moves_the_id_with_it(monkeypatch):
    prov = _custom(monkeypatch, [[3.0, 4.0]], dim=384)
    assert prov.model_id.endswith(":384")

    prov.embed_batch(["alpha"])

    assert prov.dim == 2
    assert prov.model_id == f"custom:{TARGET_MODULE}:embed_texts:2"
    assert prov.metadata()["model_id"] == prov.model_id, "the reported identity agrees"


def test_a_width_that_matches_the_configuration_leaves_the_id_alone(monkeypatch):
    prov = _custom(monkeypatch, [[1.0, 0.0]], dim=2)
    before = prov.model_id

    prov.embed_batch(["alpha"])

    assert prov.dim == 2
    assert prov.model_id == before, "an unchanged width is an unchanged index identity"


def test_an_id_that_carries_no_width_is_left_alone(monkeypatch):
    class _TailLess(_NetworkEmbeddingProvider):
        provider = "test"

        def __init__(self, cfg: _RemoteConfig) -> None:
            super().__init__(cfg)
            self.model_id = "third-party-embedder"

        def _embed_raw(self, texts: Sequence[str]) -> List[List[float]]:
            return [[0.0, 5.0] for _ in texts]

    prov = _TailLess(_RemoteConfig(model="x", dim=8))

    prov.embed_batch(["alpha"])

    # nothing in the id claimed a width, so nothing there was wrong to fix;
    # the index keys on model_id *and* dim, and dim moved.
    assert prov.dim == 2
    assert prov.model_id == "third-party-embedder"


def test_the_other_model_backed_providers_track_the_width_too(monkeypatch):
    """The sync lives on the shared base, so no provider can drift out of it."""
    calls: List[str] = []

    class _Response:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self):
            return {"embeddings": [[3.0, 4.0]], "data": [{"index": 0, "embedding": [3.0, 4.0]}]}

    class _Client:
        def __init__(self, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def post(self, url, json=None, headers=None):
            calls.append(url)
            return _Response()

    module = types.ModuleType("httpx")
    module.Client = _Client
    monkeypatch.setitem(sys.modules, "httpx", module)

    ollama = OllamaEmbeddingProvider(_RemoteConfig(model="nomic-embed-text", dim=0))
    openai = OpenAICompatibleEmbeddingProvider(
        _RemoteConfig(model="text-embedding-3-small", dim=0)
    )
    assert (ollama.model_id, openai.model_id) == (
        "ollama:nomic-embed-text:768",
        "openai:text-embedding-3-small:1536",
    )

    ollama.embed_batch(["alpha"])
    openai.embed_batch(["alpha"])

    assert ollama.model_id == "ollama:nomic-embed-text:2"
    assert openai.model_id == "openai:text-embedding-3-small:2"
    assert len(calls) == 2, "one request each, no probing"
