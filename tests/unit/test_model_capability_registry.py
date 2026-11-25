from __future__ import annotations

import json
from pathlib import Path

import pytest

from latticeai.services.model_capability_registry import (
    RECOMMENDED,
    ModelCapability,
    VerificationStatus,
    build_engine_model_catalog,
    get_all_capabilities,
    get_capability,
    get_legacy_capabilities,
    get_verified_models,
)
from latticeai.services.model_catalog import (
    ENGINE_MODEL_CATALOG,
    filter_lower_family_versions,
)
from latticeai.services.model_catalog import (
    get_verified_models as catalog_get_verified,
)


def _recommended():
    return [c for c in get_all_capabilities() if c.lifecycle == RECOMMENDED]


def test_registry_has_core_models_and_verification():
    ids = {c.id for c in _recommended()}
    assert "mlx-community/LFM2.5-2.6B-4bit" in ids
    assert "mlx-community/gemma-4-e2b-it-4bit" in ids
    assert "mlx-community/gemma-4-e4b-it-4bit" in ids
    assert "mlx-community/gemma-4-12B-it-4bit" in ids
    assert "mlx-community/Qwen3.5-9B-MLX-4bit" in ids
    assert "mlx-community/gpt-oss-20b-MXFP4-Q8" in ids
    assert "mlx-community/gemma-4-26B-A4B-it-qat-4bit" in ids
    assert "mlx-community/gemma-4-26b-a4b-it-4bit" in ids
    assert "mlx-community/gemma-4-26B-A4B-it-qat-OptiQ-4bit" in ids
    assert "mlx-community/Qwen3.6-35B-A3B-4bit" in ids
    assert "mlx-community/Qwen3.8-27B-4bit" in ids
    assert "mlx-community/Qwen3.6-27B-4bit" in ids
    assert "mlx-community/gemma-4-31b-it-4bit" in ids

    verified = get_verified_models()
    assert len(verified) == len(ids)
    for v in verified:
        assert v.get("verification", {}).get("hf_exists") is True
        assert v.get("hf_repo_id")
        assert v["lifecycle"] == RECOMMENDED


def test_legacy_catalog_shape_preserved_and_enriched():
    assert "local_mlx" in ENGINE_MODEL_CATALOG
    mlx = ENGINE_MODEL_CATALOG["local_mlx"]
    assert len(mlx) >= 10
    m0 = mlx[0]
    for key in ("id", "name", "family", "size", "modality", "pullable"):
        assert key in m0
    assert "hf_repo_id" in m0
    assert "verification" in m0
    assert "hardware" in m0
    assert m0["verification"]["hf_exists"] is True
    filtered = filter_lower_family_versions(mlx)
    assert len(filtered) <= len(mlx)
    assert len(filtered) >= 6


def test_verification_report_exists_and_valid():
    report = Path(__file__).resolve().parents[2] / "verification_report.json"
    if not report.exists():
        pytest.skip("verification_report.json not present (run the script first in CI)")
    data = json.loads(report.read_text(encoding="utf-8"))
    summary = data["summary"]
    chat_caps = [c for c in get_all_capabilities() if c.category != "embedding"]
    assert summary["total"] in {len(get_all_capabilities()), len(chat_caps)}
    assert summary["hf_present"] == summary["total"]
    assert summary["recommended_failing"] == []
    assert summary["weights_downloaded"] == 0
    assert summary["models_loaded"] == 0
    assert data["verdict_criteria"]["supported_mlx_architectures"]
    assert any("no model was loaded" in line for line in data["limitations"])
    assert all(r["verdict"] == "loadable_static" for r in data["results"])


def test_embeddinggemma_models_in_capability_registry():
    from latticeai.services.model_capability_registry import (
        get_embedding_capabilities,
        recommend_embedding_for_hardware,
    )

    emb_ids = {c.id for c in get_embedding_capabilities()}
    assert "google/embeddinggemma-2" in emb_ids
    assert "mlx-community/embeddinggemma-2-4bit" in emb_ids
    assert "google/embeddinggemma-300m" in emb_ids
    assert "mlx-community/embeddinggemma-300m-4bit" in emb_ids

    cap = get_capability("google/embeddinggemma-2")
    assert cap is not None
    assert cap.category == "embedding"
    assert cap.task == "embedding"
    assert cap.dimensions == 768
    assert cap.hf_repo_id == "google/embeddinggemma-2"

    rec_hi = recommend_embedding_for_hardware(ram_gb=16.0, apple_silicon=True)
    assert rec_hi["id"] == "google/embeddinggemma-2"
    assert rec_hi["mlx_repo_id"] == "mlx-community/embeddinggemma-2-4bit"
    assert rec_hi["memory_footprint_gb"] == 0.5

    rec_lo = recommend_embedding_for_hardware(ram_gb=4.0, apple_silicon=False)
    assert rec_lo["id"] == "google/embeddinggemma-300m"
    assert rec_lo["memory_footprint_gb"] == 0.3


def test_verified_badge_requires_config_and_tokenizer():
    cap = ModelCapability(
        id="example/no-tokenizer",
        hf_repo_id="example/no-tokenizer",
        name="No Tokenizer",
        family="Example",
        tag="local-vlm",
        size="1GB",
        verification=VerificationStatus(
            hf_exists=True, has_config=True, has_tokenizer=False
        ),
    )

    verification = cap.to_legacy_dict()["verification"]

    assert verification["hf_exists"] is True
    assert verification["has_config"] is True
    assert verification["has_tokenizer"] is False
    assert verification["verified"] is False


def test_verified_catalog_requires_same_contract_as_badge():
    verified = get_verified_models()

    assert verified
    assert all(model["verification"]["verified"] is True for model in verified)
    assert all(model["verification"]["hf_exists"] is True for model in verified)
    assert all(model["verification"]["has_config"] is True for model in verified)
    assert all(model["verification"]["has_tokenizer"] is True for model in verified)
    assert all("has_weights_hint" in model["verification"] for model in verified)


def test_registry_only_models_do_not_enter_user_facing_catalog():
    all_catalog_ids = {
        str(model.get("id") or "").lower()
        for models in ENGINE_MODEL_CATALOG.values()
        for model in models
    }
    verified_ids = {m["id"] for m in catalog_get_verified()}

    for cap in get_legacy_capabilities():
        assert get_capability(cap.id) is cap
        assert cap.verification.hf_exists is True
        assert cap.id not in verified_ids
        assert cap.id.lower() not in all_catalog_ids


def test_get_capability_and_build_catalog_roundtrip():
    cap = get_capability("mlx-community/gemma-4-12B-it-4bit")
    assert cap is not None
    assert cap.family == "Gemma 4"
    assert cap.architecture == "gemma4_unified"
    assert cap.verification.hf_exists is True

    catalog = build_engine_model_catalog()
    assert "local_mlx" in catalog
    assert any(
        m["id"] == "mlx-community/gemma-4-12B-it-4bit" for m in catalog["local_mlx"]
    )
    assert all(m["lifecycle"] == RECOMMENDED for m in catalog["local_mlx"])
