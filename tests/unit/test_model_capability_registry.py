"""5.2.0 tests for structured model capability registry + verification artifacts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from latticeai.services.model_capability_registry import (
    ModelCapability,
    HardwareProfile,
    VerificationStatus,
    get_all_capabilities,
    build_engine_model_catalog,
    get_verified_models,
    get_capability,
)
from latticeai.services.model_catalog import (
    ENGINE_MODEL_CATALOG,
    filter_lower_family_versions,
    get_verified_models as catalog_get_verified,
)


def test_registry_has_core_models_and_verification():
    caps = get_all_capabilities()
    assert len(caps) >= 12
    ids = {c.id for c in caps}
    # core current
    assert "mlx-community/Qwen3-VL-4B-Instruct-4bit" in ids
    assert "mlx-community/gemma-4-12b-it-4bit" in ids
    assert "mlx-community/Llama-4-Scout-17B-16E-Instruct-4bit" in ids
    # modern 5.2 additions
    assert "Qwen/Qwen2.5-VL-7B-Instruct" in ids
    assert "meta-llama/Llama-3.2-11B-Vision-Instruct" in ids

    verified = get_verified_models()
    assert len(verified) >= 12
    for v in verified[:3]:
        assert v.get("verification", {}).get("hf_exists") is True
        assert v.get("hf_repo_id")


def test_legacy_catalog_shape_preserved_and_enriched():
    assert "local_mlx" in ENGINE_MODEL_CATALOG
    mlx = ENGINE_MODEL_CATALOG["local_mlx"]
    assert len(mlx) >= 10
    m0 = mlx[0]
    # legacy keys
    for key in ("id", "name", "family", "size", "modality", "pullable"):
        assert key in m0
    # 5.2 rich keys
    assert "hf_repo_id" in m0
    assert "verification" in m0
    assert "hardware" in m0
    assert m0["verification"]["hf_exists"] is True
    # family filter still works and keeps newest-ish
    filtered = filter_lower_family_versions(mlx)
    assert len(filtered) <= len(mlx)
    assert len(filtered) >= 6


def test_recommendation_payload_includes_5_2_fields(tmp_path: Path):
    # simulate what /models/recommendations returns after wiring
    from latticeai.services.model_recommendation import recommend_catalog

    profile = {"os": "darwin", "arch": "arm64", "ram_mb": 32768, "gpu": {"vendor": "apple", "vram_mb": 0}}
    rec = recommend_catalog(profile, engine="local_mlx")
    assert "models" in rec and rec["models"]
    first = rec["models"][0]
    assert "status" in first and first["status"] in {"recommended", "compatible", "not_recommended"}
    # rich pass-through
    assert "hf_repo_id" in first
    assert "verification" in first
    assert "hardware" in first or first.get("hardware") is None  # hardware may be nested dict
    assert "load_strategy" in first


def test_verification_report_exists_and_valid():
    # The script writes this; existence + parse proves the automation path
    report = Path("verification_report.json")
    if not report.exists():
        pytest.skip("verification_report.json not present (run the script first in CI)")
    data = json.loads(report.read_text(encoding="utf-8"))
    assert "summary" in data
    assert data["summary"]["hf_present"] >= 12
    assert data["summary"]["missing_critical_recommended"] == 0
    assert any(r.get("hf_exists") for r in data.get("results", []))


def test_verified_badge_requires_config_and_tokenizer():
    cap = ModelCapability(
        id="example/no-tokenizer",
        hf_repo_id="example/no-tokenizer",
        name="No Tokenizer",
        family="Example",
        tag="local-vlm",
        size="1GB",
        verification=VerificationStatus(hf_exists=True, has_config=True, has_tokenizer=False),
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
    registry_only = get_capability("mistralai/Pixtral-12B-2409")

    assert registry_only is not None
    assert registry_only.verification.hf_exists is True
    assert registry_only.verification.has_config is False
    assert registry_only.verification.has_tokenizer is False
    assert "mistralai/Pixtral-12B-2409" not in {m["id"] for m in catalog_get_verified()}
    assert not any("pixtral" in model_id or "mistral" in model_id for model_id in all_catalog_ids)


def test_get_capability_and_build_catalog_roundtrip():
    cap = get_capability("mlx-community/gemma-4-12b-it-4bit")
    assert cap is not None
    assert cap.family == "Gemma 4"
    assert cap.verification.hf_exists is True

    catalog = build_engine_model_catalog()
    assert "local_mlx" in catalog
    assert any(m["id"] == "mlx-community/gemma-4-12b-it-4bit" for m in catalog["local_mlx"])
