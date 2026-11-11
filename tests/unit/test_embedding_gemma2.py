"""Unit tests for Google EmbeddingGemma 2 (`google/embeddinggemma-2`) integration and Item 13 semantic activation."""

from __future__ import annotations

import math
import sys
import types
from pathlib import Path
from typing import Any, List

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from latticeai.api.agent_worker_seam import SEAM_ENV_VAR
from latticeai.api.search import create_search_router
from latticeai.api.worker_compute import create_worker_compute_router
from latticeai.core.embedding_providers import (
    _KNOWN_DIMS,
    AUTO_PROVIDER,
    DEFAULT_HF_EMBEDDING_MODEL,
    EMBEDDINGGEMMA_PREFIXES,
    LOCAL_HF_MODELS,
    LOCAL_MLX_MODELS,
    PRODUCTION_PROVIDER_PROFILES,
    PROVIDER_TYPES,
    RECOMMENDED_EMBEDDING_MODEL,
    RECOMMENDED_EMBEDDING_PROFILE,
    EmbeddingUnavailable,
    HuggingFaceEmbeddingProvider,
    _guess_dim,
    _RemoteConfig,
    build_embedding_provider,
    detect_embedder,
    detect_local_hf,
    detect_local_mlx,
    resolve_embedder,
    resolve_embedding_profile,
)
from latticeai.services.search_service import SearchService


def _plant_hf_snapshot(root: Path, repo_id: str) -> Path:
    snapshot = root / ("models--" + repo_id.replace("/", "--")) / "snapshots" / "rev1"
    snapshot.mkdir(parents=True, exist_ok=True)
    (snapshot / "config.json").write_text('{"hidden_size": 768}', encoding="utf-8")
    (snapshot / "model.safetensors").write_bytes(b"\x00" * 16)
    return snapshot


def test_known_dims_include_all_embeddinggemma_variants() -> None:
    expected = {
        "embeddinggemma-2": 768,
        "google/embeddinggemma-2": 768,
        "embeddinggemma-2-4bit": 768,
        "embeddinggemma-2-8bit": 768,
        "embeddinggemma-300m": 768,
        "google/embeddinggemma-300m": 768,
        "embeddinggemma-300m-4bit": 768,
        "embeddinggemma-300m-8bit": 768,
    }
    for key, dim in expected.items():
        assert _KNOWN_DIMS[key] == dim
        assert _guess_dim(key, 384) == dim
    assert _guess_dim("mlx-community/embeddinggemma-2-4bit", 384) == 768
    assert _guess_dim("mlx-community/embeddinggemma-300m-4bit", 384) == 768


def test_profiles_expose_embeddinggemma_2_and_300m() -> None:
    assert RECOMMENDED_EMBEDDING_MODEL == "google/embeddinggemma-2"
    assert RECOMMENDED_EMBEDDING_PROFILE == "local:embeddinggemma-2"

    local_gemma2 = resolve_embedding_profile("local:embeddinggemma-2")
    assert local_gemma2 == {
        "id": "local:embeddinggemma-2",
        "provider": "huggingface",
        "model": "google/embeddinggemma-2",
        "hf_repo_id": "google/embeddinggemma-2",
        "download_gb": 1.48,
        "dimensions": 768,
        "grade": "production",
        "family": "local",
        "recommended": True,
        "label": "Google EmbeddingGemma 2 (Hugging Face 로컬)",
        "detail": "Google DeepMind의 740M 다국어·멀티모달 시맨틱 임베딩 모델 (google/embeddinggemma-2, 768차원). 한국어·코드·문서 의미 검색에 최적화.",
    }

    mlx_gemma2 = resolve_embedding_profile("mlx:embeddinggemma-2")
    assert mlx_gemma2 == {
        "id": "mlx:embeddinggemma-2",
        "provider": "mlx",
        "model": "mlx-community/embeddinggemma-2-4bit",
        "hf_repo_id": "mlx-community/embeddinggemma-2-4bit",
        "download_gb": 0.42,
        "dimensions": 768,
        "grade": "production",
        "family": "mlx",
        "recommended": True,
        "label": "Google EmbeddingGemma 2 4-bit (Apple Silicon MLX)",
        "detail": "Apple Silicon 가속 4비트 양자화 EmbeddingGemma 2 (768차원, 420MB).",
    }

    local_300m = resolve_embedding_profile("local:embeddinggemma-300m")
    assert local_300m["provider"] == "huggingface"
    assert local_300m["model"] == "google/embeddinggemma-300m"
    assert local_300m["hf_repo_id"] == "google/embeddinggemma-300m"
    assert local_300m["mlx_model"] == "mlx-community/embeddinggemma-300m-4bit"
    assert local_300m["dimensions"] == 768
    assert local_300m["download_gb"] == 0.30
    assert "local:embeddinggemma-2" in PRODUCTION_PROVIDER_PROFILES
    assert "mlx:embeddinggemma-2" in PRODUCTION_PROVIDER_PROFILES
    assert "local:embeddinggemma-300m" in PRODUCTION_PROVIDER_PROFILES


def test_factory_builds_huggingface_provider_for_all_aliases() -> None:
    assert "huggingface" in PROVIDER_TYPES
    for alias in ("huggingface", "hf", "sentence-transformers", "sentence_transformers"):
        prov = build_embedding_provider(alias)
        assert isinstance(prov, HuggingFaceEmbeddingProvider)
        assert prov.provider == "huggingface"
        assert prov.dim == 768
        assert prov.model_id == "huggingface:google/embeddinggemma-2:768"
        assert prov.grade == "production"
        meta = prov.metadata()
        assert meta["hf_repo_id"] == DEFAULT_HF_EMBEDDING_MODEL
        assert meta["instruction_prefixes"] is True
        assert meta["max_tokens"] == 2048


def test_huggingface_provider_with_sentence_transformers(monkeypatch: pytest.MonkeyPatch) -> None:
    loaded_models: List[str] = []
    encoded_Batches: List[List[str]] = []

    class FakeSentenceTransformer:
        def __init__(self, model_name: str) -> None:
            loaded_models.append(model_name)

        def encode(self, texts: List[str], normalize_embeddings: bool = True) -> List[List[float]]:
            encoded_Batches.append(list(texts))
            return [[3.0, 4.0] + [0.0] * 766 for _ in texts]

    st_mod = types.ModuleType("sentence_transformers")
    st_mod.SentenceTransformer = FakeSentenceTransformer  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentence_transformers", st_mod)

    prov = build_embedding_provider("huggingface", model="google/embeddinggemma-2")
    assert prov.health() == {
        "status": "ok",
        "detail": "Hugging Face model google/embeddinggemma-2 loaded (sentence_transformers)",
    }

    q_vecs = prov.embed_batch_for(["한국어 의미 검색"], kind="query")
    p_vecs = prov.embed_batch_for(["문서 본문 내용"], kind="passage")

    assert loaded_models == ["google/embeddinggemma-2"]
    assert encoded_Batches == [
        [f"{EMBEDDINGGEMMA_PREFIXES['query']}한국어 의미 검색"],
        [f"{EMBEDDINGGEMMA_PREFIXES['passage']}문서 본문 내용"],
    ]
    assert len(q_vecs[0]) == 768
    assert math.isclose(sum(v * v for v in q_vecs[0]), 1.0, rel_tol=1e-6)
    assert math.isclose(q_vecs[0][0], 0.6, rel_tol=1e-6)
    assert math.isclose(q_vecs[0][1], 0.8, rel_tol=1e-6)
    assert len(p_vecs[0]) == 768


def test_huggingface_provider_with_transformers_mean_pooling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(sys.modules, "sentence_transformers", raising=False)

    class FakeTokenizer:
        @classmethod
        def from_pretrained(cls, name: str) -> "FakeTokenizer":
            return cls()

        def __call__(self, texts: List[str], **kwargs: Any) -> dict[str, Any]:
            return {
                "input_ids": [[1, 2] for _ in texts],
                "attention_mask": [[1, 1] for _ in texts],
            }

    class FakeOutputs:
        def __init__(self, batch_size: int) -> None:
            # 2 tokens per item, 4 dims: mean of [2, 0, 0, 0] and [4, 0, 0, 0] is [3, 0, 0, 0]
            self.last_hidden_state = [
                [[2.0, 0.0, 0.0, 0.0], [4.0, 0.0, 0.0, 0.0]] for _ in range(batch_size)
            ]

    class FakeModel:
        @classmethod
        def from_pretrained(cls, name: str) -> "FakeModel":
            return cls()

        def __call__(self, **encoded: Any) -> FakeOutputs:
            return FakeOutputs(len(encoded["input_ids"]))

    tf_mod = types.ModuleType("transformers")
    tf_mod.AutoTokenizer = FakeTokenizer  # type: ignore[attr-defined]
    tf_mod.AutoModel = FakeModel  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "transformers", tf_mod)

    prov = HuggingFaceEmbeddingProvider(_RemoteConfig(model="google/embeddinggemma-2", dim=4))
    vecs = prov.embed_batch(["hello world"])
    assert vecs == [[1.0, 0.0, 0.0, 0.0]]
    assert prov.dim == 4
    assert prov.model_id == "huggingface:google/embeddinggemma-2:4"


def test_autodetect_prioritizes_embeddinggemma_2_in_mlx_and_hf_caches(tmp_path: Path) -> None:
    assert LOCAL_MLX_MODELS[0] == ("mlx-community/embeddinggemma-2-4bit", 768, False)
    assert LOCAL_MLX_MODELS[1] == ("mlx-community/embeddinggemma-300m-4bit", 768, False)
    assert LOCAL_HF_MODELS[0] == ("google/embeddinggemma-2", 768, False)
    assert LOCAL_HF_MODELS[1] == ("google/embeddinggemma-300m", 768, False)

    # Plant both e5-small and embeddinggemma-2-4bit in MLX cache -> embeddinggemma-2-4bit wins.
    _plant_hf_snapshot(tmp_path, "mlx-community/multilingual-e5-small-mlx")
    _plant_hf_snapshot(tmp_path, "mlx-community/embeddinggemma-2-4bit")
    mlx_found = detect_local_mlx({"HF_HUB_CACHE": str(tmp_path)})
    assert mlx_found.found
    assert mlx_found.provider == "mlx"
    assert mlx_found.model == "mlx-community/embeddinggemma-2-4bit"
    assert mlx_found.dim == 768
    assert mlx_found.source == "local_model"

    # Plant google/embeddinggemma-2 in HF cache -> detect_local_hf and detect_embedder find it.
    hf_cache = tmp_path / "hf_only"
    _plant_hf_snapshot(hf_cache, "google/embeddinggemma-2")
    hf_found = detect_local_hf({"HF_HUB_CACHE": str(hf_cache)})
    assert hf_found.found
    assert hf_found.provider == "huggingface"
    assert hf_found.model == "google/embeddinggemma-2"
    assert hf_found.dim == 768
    assert hf_found.source == "local_model"

    detected = detect_embedder(AUTO_PROVIDER, "", {"HF_HUB_CACHE": str(hf_cache)}, probe_ollama=False)
    assert detected.found
    assert detected.provider == "huggingface"
    assert detected.model == "google/embeddinggemma-2"
    assert detected.dim == 768
    assert detected.source == "local_model"


def test_autodetect_finds_embeddinggemma_2_in_latticeai_models_dir(tmp_path: Path) -> None:
    home_dir = tmp_path / "home"
    direct_model_dir = home_dir / ".latticeai" / "models" / "google" / "embeddinggemma-2"
    direct_model_dir.mkdir(parents=True, exist_ok=True)
    (direct_model_dir / "config.json").write_text('{"hidden_size": 768}', encoding="utf-8")
    (direct_model_dir / "model.safetensors").write_bytes(b"\x00" * 16)

    found = detect_embedder("auto", "", {"HOME": str(home_dir)}, probe_ollama=False)
    assert found.found
    assert found.provider == "huggingface"
    assert found.model == "google/embeddinggemma-2"
    assert found.dim == 768
    assert found.source == "local_model"


def test_item13_auto_activates_semantic_embeddings_when_cached_and_falls_back_when_empty(
    tmp_path: Path,
) -> None:
    # 1. Empty cache -> offline zero-download deterministic hash fallback
    empty_cache = tmp_path / "empty"
    empty_cache.mkdir()
    offline = resolve_embedder(AUTO_PROVIDER, extra={"env": {"HF_HUB_CACHE": str(empty_cache)}})
    assert offline.active == "hash"
    assert offline.fell_back is False
    assert offline.provider.grade == "fallback"
    assert offline.provider.dim == 384

    # 2. Cached google/embeddinggemma-2 -> seamless semantic activation (grade="production", 768d)
    populated_cache = tmp_path / "populated"
    _plant_hf_snapshot(populated_cache, "google/embeddinggemma-2")
    semantic = resolve_embedder(
        AUTO_PROVIDER, extra={"env": {"HF_HUB_CACHE": str(populated_cache)}}
    )
    assert semantic.active == "huggingface"
    assert semantic.fell_back is False
    assert semantic.provider.grade == "production"
    assert semantic.provider.dim == 768
    assert semantic.provider.model_id == "huggingface:google/embeddinggemma-2:768"
    vec = semantic.provider.embed("시맨틱 임베딩 활성화 테스트")
    assert len(vec) == 768
    assert math.isclose(sum(x * x for x in vec), 1.0, rel_tol=1e-6)


def test_embeddings_status_and_worker_embed_expose_recommended_embeddinggemma_2(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(SEAM_ENV_VAR, "1")
    cache_dir = tmp_path / "cache"
    _plant_hf_snapshot(cache_dir, "google/embeddinggemma-2")
    resolved = resolve_embedder(
        AUTO_PROVIDER, extra={"env": {"HF_HUB_CACHE": str(cache_dir)}}
    )

    app = FastAPI()
    app.include_router(
        create_search_router(
            service=SearchService(embedder=resolved),
            require_user=lambda _r: "user@local",
            embedding_info=resolved.as_dict,
        )
    )
    app.include_router(
        create_worker_compute_router(
            embedder=resolved,
            transcriber=None,
            require_user=lambda _r: "user@local",
            enforce_rate_limit=lambda _u, _b: None,
        )
    )
    client = TestClient(app)

    status_resp = client.get("/api/embeddings/status")
    assert status_resp.status_code == 200
    status = status_resp.json()
    assert status["recommended_model"] == "google/embeddinggemma-2"
    assert status["recommended_profile"] == "local:embeddinggemma-2"
    assert status["active_provider"] == "huggingface"
    assert status["grade"] == "production"
    assert status["semantic"] is True
    assert status["dimensions"] == 768

    embed_resp = client.post(
        "/worker/embed", json={"texts": ["임베딩 문서 테스트"], "kind": "passage"}
    )
    assert embed_resp.status_code == 200
    embed_body = embed_resp.json()
    assert embed_body["provider"] == "huggingface"
    assert embed_body["dim"] == 768
    assert embed_body["grade"] == "production"
    assert embed_body["semantic"] is True
    assert embed_body["recommended_model"] == "google/embeddinggemma-2"
    assert embed_body["recommended_profile"] == "local:embeddinggemma-2"
    assert len(embed_body["vectors"][0]) == 768


def test_huggingface_provider_reports_unavailable_when_missing_runtime_and_snapshot(
    tmp_path: Path,
) -> None:
    prov = HuggingFaceEmbeddingProvider(
        _RemoteConfig(
            model="google/embeddinggemma-2",
            extra={"env": {"HF_HUB_CACHE": str(tmp_path)}, "require_runtime": True},
        )
    )
    health = prov.health()
    assert health["status"] == "unavailable"
    with pytest.raises(EmbeddingUnavailable):
        prov.embed_batch(["test"])
