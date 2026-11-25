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
    module = types.ModuleType(TARGET_MODULE)
    module.embed_texts = lambda texts: rows
    monkeypatch.setitem(sys.modules, TARGET_MODULE, module)
    kwargs.setdefault("model", "")
    return CustomEmbeddingProvider(
        _RemoteConfig(extra={"target": f"{TARGET_MODULE}:embed_texts"}, **kwargs)
    )


def test_mlx_guesses_the_width_of_a_known_model_when_none_is_pinned():
    prov = MLXEmbeddingProvider(_RemoteConfig(model="bge-m3", dim=0))

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

    assert prov.dim == 2
    assert prov.model_id == "third-party-embedder"


def test_the_other_model_backed_providers_track_the_width_too(monkeypatch):
    calls: List[str] = []

    class _Response:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self):
            return {
                "embeddings": [[3.0, 4.0]],
                "data": [{"index": 0, "embedding": [3.0, 4.0]}],
            }

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
