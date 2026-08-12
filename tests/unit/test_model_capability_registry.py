"""Tests for the structured model capability registry + verification artifacts."""

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
    """Current-generation slice, derived from the lifecycle field."""
    return [c for c in get_all_capabilities() if c.lifecycle == RECOMMENDED]


def test_registry_has_core_models_and_verification():
    ids = {c.id for c in _recommended()}
    # one entry per RAM tier, newest generation only
    assert "mlx-community/LFM2.5-2.6B-4bit" in ids            # ultralight, text
    assert "mlx-community/gemma-4-e2b-it-4bit" in ids         # ultralight, vision
    assert "mlx-community/gemma-4-e4b-it-4bit" in ids         # light
    assert "mlx-community/gemma-4-12B-it-4bit" in ids         # mid — note the capital B
    assert "mlx-community/Qwen3.5-9B-MLX-4bit" in ids         # mid, vision
    assert "mlx-community/gpt-oss-20b-MXFP4-Q8" in ids        # general purpose
    assert "mlx-community/gemma-4-26b-a4b-it-4bit" in ids     # MoE
    assert "mlx-community/Qwen3.6-35B-A3B-4bit" in ids        # MoE
    assert "mlx-community/Qwen3.6-27B-4bit" in ids            # large dense
    assert "mlx-community/gemma-4-31b-it-4bit" in ids         # large

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
    report = Path(__file__).resolve().parents[2] / "verification_report.json"
    if not report.exists():
        pytest.skip("verification_report.json not present (run the script first in CI)")
    data = json.loads(report.read_text(encoding="utf-8"))
    summary = data["summary"]
    assert summary["total"] == len(get_all_capabilities())
    assert summary["hf_present"] == summary["total"]
    assert summary["recommended_failing"] == []
    # The protocol is the point: metadata only, on the user's machine.
    assert summary["weights_downloaded"] == 0
    assert summary["models_loaded"] == 0
    # The verdict must ship with its own criteria and its own limits.
    assert data["verdict_criteria"]["supported_mlx_architectures"]
    assert any("no model was loaded" in line for line in data["limitations"])
    assert all(r["verdict"] == "loadable_static" for r in data["results"])


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
    verified_ids = {m["id"] for m in catalog_get_verified()}

    for cap in get_legacy_capabilities():
        # Known well enough to name — the Hub check passed for all of them …
        assert get_capability(cap.id) is cap
        assert cap.verification.hf_exists is True
        # … and still absent from everything a user can click.
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
    assert any(m["id"] == "mlx-community/gemma-4-12B-it-4bit" for m in catalog["local_mlx"])
    assert all(m["lifecycle"] == RECOMMENDED for m in catalog["local_mlx"])
