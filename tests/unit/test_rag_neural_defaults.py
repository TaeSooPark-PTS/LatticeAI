from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any, List, Tuple

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from latticeai.api.agent_worker_seam import SEAM_ENV_VAR
from latticeai.api.worker_compute import create_worker_compute_router
from latticeai.core import rerankers
from latticeai.core.config import Config
from latticeai.core.embedding_providers import (
    EmbeddingUnavailable,
    HuggingFaceEmbeddingProvider,
    _RemoteConfig,
    resolve_embedder,
)
from latticeai.core.embedding_providers import text as text_mod
from latticeai.core.messages import MESSAGES
from latticeai.core.rerankers import (
    DEFAULT_RERANKER_MODEL,
    RERANK_DOCUMENT_MAX_CHARS,
    RERANK_MAX_DOCUMENTS,
    CrossEncoderReranker,
    RerankerUnavailable,
    resolve_reranker,
)
from latticeai.runtime.brain_runtime import build_embedder_runtime

USER = "worker@local"


def _plant_snapshot(root: Path, repo_id: str) -> Path:
    snapshot = root / ("models--" + repo_id.replace("/", "--")) / "snapshots" / "rev1"
    snapshot.mkdir(parents=True, exist_ok=True)
    (snapshot / "config.json").write_text("{}", encoding="utf-8")
    (snapshot / "model.safetensors").write_bytes(b"\x00" * 16)
    return snapshot


class _Model:
    def __init__(self, scores: List[float]) -> None:
        self.scores = scores
        self.pairs: List[Tuple[str, str]] = []

    def predict(self, pairs: List[Tuple[str, str]], show_progress_bar: bool = False):
        self.pairs.extend(pairs)
        return self.scores[: len(pairs)]


def _ready(tmp_path: Path, model: Any) -> rerankers.RerankerResolution:
    _plant_snapshot(tmp_path, DEFAULT_RERANKER_MODEL)
    return resolve_reranker(
        {"HF_HUB_CACHE": str(tmp_path)},
        runtime_present=lambda: True,
        loader=lambda target, max_length: model,
    )


def _client(reranker: Any) -> TestClient:
    app = FastAPI()
    app.include_router(
        create_worker_compute_router(
            embedder=resolve_embedder(""),
            require_user=lambda request: USER,
            enforce_rate_limit=lambda user, bucket: None,
            reranker=reranker,
        )
    )
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _seam_open(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SEAM_ENV_VAR, "1")


def test_the_default_embedding_provider_is_auto_so_embeddinggemma_2_wins_when_present():
    assert Config.from_env({}).embedding_provider == "auto"
    assert (
        Config.from_env({"LATTICEAI_EMBEDDING_PROVIDER": "hash"}).embedding_provider
        == "hash"
    )


class _Cfg:
    embedding_provider = "auto"
    embedding_profile = ""
    embedding_model = ""
    embedding_base_url = ""
    embedding_api_key = ""
    embedding_dim = 0
    embedding_timeout = 30
    embedding_custom_target = ""


def test_a_cached_gemma_without_its_runtime_falls_back_to_the_384d_local_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delitem(sys.modules, "sentence_transformers", raising=False)
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    _plant_snapshot(tmp_path, "google/embeddinggemma-2")

    resolved = build_embedder_runtime(
        config=_Cfg(),
        profile={},
        resolve_embedder=resolve_embedder,
        detect=lambda **_: text_mod.detect_embedder(
            "auto", "", {"HF_HUB_CACHE": str(tmp_path)}, probe_ollama=False
        ),
    )
    assert resolved.requested == "huggingface"
    assert resolved.active == "hash"
    assert resolved.fell_back is True
    assert resolved.provider.dim == 384
    assert resolved.provider.model_id == "lattice-local-hash-v1:384"
    assert "EmbeddingGemma 2 needs" in resolved.health["detail"]

    via_auto = resolve_embedder("auto", extra={"env": {"HF_HUB_CACHE": str(tmp_path)}})
    assert via_auto.active == "hash"
    assert via_auto.provider.dim == 384


def test_runtime_gap_names_both_floors_and_clears_on_new_enough_versions():
    old = text_mod.embeddinggemma_2_runtime_gap(
        {"sentence-transformers": "5.4.1", "transformers": "4.57.6"}.get
    )
    assert "sentence-transformers>=6.1 (found 5.4.1)" in old
    assert "transformers>=5.18 (found 4.57.6)" in old
    assert "ltcai[rag]" in old
    missing = text_mod.embeddinggemma_2_runtime_gap(lambda name: "")
    assert "found none" in missing
    assert (
        text_mod.embeddinggemma_2_runtime_gap(
            {
                "sentence-transformers": "6.3.0",
                "transformers": "5.24.1",
                "torchvision": "0.29.2",
            }.get
        )
        == ""
    )
    assert text_mod._version_tuple("5.18.0.dev0") == (5, 18)
    assert text_mod._version_tuple("7") == (7, 0)
    assert isinstance(text_mod.embeddinggemma_2_runtime_gap(), str)


def test_a_real_sentence_transformers_loads_gemma_2_text_only_and_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: List[Tuple[str, dict]] = []

    class SentenceTransformer:
        def __init__(self, target: str, **options: Any) -> None:
            calls.append((target, options))

        def encode(self, texts: List[str], normalize_embeddings: bool = True):
            return [[0.0, 1.0] + [0.0] * 766 for _ in texts]

    SentenceTransformer.__module__ = "sentence_transformers.sentence_transformer"
    module = types.ModuleType("sentence_transformers")
    module.SentenceTransformer = SentenceTransformer
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)
    monkeypatch.setattr(text_mod, "embeddinggemma_2_runtime_gap", lambda: "")
    snapshot = _plant_snapshot(tmp_path, "google/embeddinggemma-2")

    provider = HuggingFaceEmbeddingProvider(
        _RemoteConfig(
            model="google/embeddinggemma-2",
            extra={"env": {"HF_HUB_CACHE": str(tmp_path)}},
        )
    )
    assert provider.health()["status"] == "ok"
    assert len(provider.embed_batch_for(["질의"], kind="query")[0]) == 768
    assert calls == [
        (
            str(snapshot),
            {
                "local_files_only": True,
                "config_kwargs": {"vision_config": None, "audio_config": None},
            },
        )
    ]

    monkeypatch.setattr(
        text_mod, "embeddinggemma_2_runtime_gap", lambda: "EmbeddingGemma 2 needs x"
    )
    stale = HuggingFaceEmbeddingProvider(_RemoteConfig(model="google/embeddinggemma-2"))
    assert stale.health() == {
        "status": "unavailable",
        "detail": "EmbeddingGemma 2 needs x",
    }


def test_a_missing_gemma_dependency_is_named_instead_of_an_empty_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SentenceTransformer:
        def __init__(self, target: str, **options: Any) -> None:
            raise ImportError("this processor requires the Torchvision library")

    SentenceTransformer.__module__ = "sentence_transformers.sentence_transformer"
    module = types.ModuleType("sentence_transformers")
    module.SentenceTransformer = SentenceTransformer
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)
    monkeypatch.setattr(text_mod, "embeddinggemma_2_runtime_gap", lambda: "")

    provider = HuggingFaceEmbeddingProvider(
        _RemoteConfig(model="google/embeddinggemma-2")
    )
    health = provider.health()
    assert health["status"] == "unavailable"
    assert "requires the Torchvision library" in health["detail"]


def test_a_failing_sentence_transformers_load_is_unavailable_not_faked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SentenceTransformer:
        def __init__(self, target: str, **options: Any) -> None:
            raise OSError("no weights on disk")

    module = types.ModuleType("sentence_transformers")
    module.SentenceTransformer = SentenceTransformer
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)
    provider = HuggingFaceEmbeddingProvider(
        _RemoteConfig(model="sentence-transformers/all-MiniLM-L6-v2", dim=8)
    )
    with pytest.raises(EmbeddingUnavailable, match="no weights on disk"):
        provider.embed_batch(["x"])


def test_reranker_resolution_never_fetches_weights(tmp_path: Path) -> None:
    disabled = resolve_reranker({"LATTICEAI_RERANKER": "off"})
    assert disabled.status == "disabled" and not disabled.ready
    no_runtime = resolve_reranker({}, runtime_present=lambda: False)
    assert no_runtime.status == "unavailable"
    assert "ltcai[rag]" in no_runtime.detail
    not_cached = resolve_reranker(
        {"HF_HUB_CACHE": str(tmp_path / "empty")}, runtime_present=lambda: True
    )
    assert not_cached.status == "unavailable"
    assert "never fetched" in not_cached.detail
    assert not_cached.as_dict()["model_id"] is None

    local_dir = tmp_path / "my-reranker"
    local_dir.mkdir()
    direct = resolve_reranker(
        {"LATTICEAI_RERANKER_MODEL": str(local_dir)}, runtime_present=lambda: True
    )
    assert direct.ready
    assert direct.reranker is not None and direct.reranker.target == str(local_dir)

    ready = _ready(tmp_path, _Model([0.1]))
    assert ready.status == "ready"
    assert ready.model == DEFAULT_RERANKER_MODEL
    assert ready.as_dict()["model_id"] == "cross-encoder:BAAI/bge-reranker-v2-m3"
    assert rerankers._runtime_present() in {True, False}


def test_the_cross_encoder_loads_once_truncates_and_checks_its_output() -> None:
    loads: List[Tuple[str, int]] = []
    model = _Model([0.2, 0.9])

    def loader(target: str, max_length: int) -> Any:
        loads.append((target, max_length))
        return model

    reranker = CrossEncoderReranker("m", "/snap", loader=loader)
    assert reranker.score("q", []) == []
    assert reranker.score("q", ["a", "b" * (RERANK_DOCUMENT_MAX_CHARS + 50)]) == [
        0.2,
        0.9,
    ]
    assert reranker.score("q", ["c", "d"]) == [0.2, 0.9]
    assert loads == [("/snap", 512)]
    assert len(model.pairs[1][1]) == RERANK_DOCUMENT_MAX_CHARS

    short = CrossEncoderReranker("m", "/snap", loader=lambda t, n: _Model([0.5]))
    with pytest.raises(RerankerUnavailable, match="1 scores for 2 pairs"):
        short.score("q", ["a", "b"])

    def broken_loader(target: str, max_length: int) -> Any:
        raise OSError("disk")

    with pytest.raises(RerankerUnavailable, match="could not load"):
        CrossEncoderReranker("m", "/snap", loader=broken_loader).score("q", ["a"])

    class Exploding:
        def predict(self, pairs: Any, show_progress_bar: bool = False) -> Any:
            raise RuntimeError("oom")

    with pytest.raises(RerankerUnavailable, match="scoring failed"):
        CrossEncoderReranker("m", "/snap", loader=lambda t, n: Exploding()).score(
            "q", ["a"]
        )

    class Tensorish:
        def tolist(self) -> List[float]:
            return [1.5]

    class TensorModel:
        def predict(self, pairs: Any, show_progress_bar: bool = False) -> Any:
            return Tensorish()

    assert CrossEncoderReranker("m", "/s", loader=lambda t, n: TensorModel()).score(
        "q", ["a"]
    ) == [1.5]


def test_the_default_loader_builds_a_sentence_transformers_cross_encoder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: List[Tuple[str, int, bool]] = []

    class CrossEncoder:
        def __init__(
            self, target: str, max_length: int, local_files_only: bool
        ) -> None:
            seen.append((target, max_length, local_files_only))

    module = types.ModuleType("sentence_transformers")
    module.CrossEncoder = CrossEncoder
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)
    assert isinstance(
        rerankers._sentence_transformers_cross_encoder("/s", 64), CrossEncoder
    )
    assert seen == [("/s", 64, True)]


def test_worker_rerank_scores_candidates_with_the_cross_encoder(tmp_path: Path) -> None:
    model = _Model([0.1, 0.8, 0.3])
    client = _client(_ready(tmp_path, model))
    body = client.post(
        "/worker/rerank",
        json={"query": "phoenix rollback", "documents": ["a", "b", "c"]},
    ).json()
    assert body == {
        "scores": [0.1, 0.8, 0.3],
        "count": 3,
        "model_id": "cross-encoder:BAAI/bge-reranker-v2-m3",
        "provider": "cross-encoder",
    }
    assert model.pairs[0] == ("phoenix rollback", "a")
    blank = client.post("/worker/rerank", json={"query": "  ", "documents": ["a", "b"]})
    assert blank.json()["scores"] == [0.0, 0.0]


def test_worker_rerank_is_503_when_unavailable_and_422_when_oversized(
    tmp_path: Path,
) -> None:
    missing = _client(resolve_reranker({}, runtime_present=lambda: False))
    response = missing.post("/worker/rerank", json={"query": "q", "documents": ["a"]})
    assert response.status_code == 503
    assert "sentence-transformers" in response.json()["detail"]
    assert (
        _client(None)
        .post("/worker/rerank", json={"query": "q", "documents": ["a"]})
        .status_code
        == 503
    )

    class Failing:
        def predict(self, pairs: Any, show_progress_bar: bool = False) -> Any:
            raise RuntimeError("oom")

    failing = _client(_ready(tmp_path, Failing()))
    assert (
        failing.post(
            "/worker/rerank", json={"query": "q", "documents": ["a"]}
        ).status_code
        == 503
    )
    oversized = failing.post(
        "/worker/rerank",
        json={"query": "q", "documents": ["x"] * (RERANK_MAX_DOCUMENTS + 1)},
    )
    assert oversized.status_code == 422
    assert MESSAGES["worker_compute.rerank_batch_too_large"]["en"]
