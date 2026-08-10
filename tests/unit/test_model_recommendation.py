"""Tests for hardware-aware local model recommendation.

Uses synthetic system profiles so the tri-state classification
(recommended / compatible / not_recommended) is verified deterministically
without touching real hardware.
"""

import pytest

from latticeai.services import model_recommendation as mr


def _runtime_supported(_model_id, *, engine=None):
    return {
        "model_id": _model_id,
        "engine": engine,
        "status": "supported",
        "supported": True,
        "checked": True,
    }


@pytest.fixture(autouse=True)
def assume_runtime_supported(monkeypatch):
    monkeypatch.setattr(mr, "model_runtime_compatibility", _runtime_supported)


def test_runtime_incompatibility_marks_model_not_recommended(monkeypatch):
    monkeypatch.setattr(
        mr,
        "model_runtime_compatibility",
        lambda model_id, *, engine=None: {
            "model_id": model_id,
            "engine": engine,
            "status": "unsupported",
            "supported": False,
            "user_message": "This model is not supported by the installed runtime.",
        } if "gemma-4-12b" in model_id.lower() else _runtime_supported(model_id, engine=engine),
    )

    result = mr.recommend_catalog(_mac(64), engine="local_mlx")
    by_id = {m["id"]: m for m in result["models"]}
    assert by_id["mlx-community/gemma-4-12B-it-4bit"]["status"] == mr.NOT_RECOMMENDED
    assert "installed runtime" in by_id["mlx-community/gemma-4-12B-it-4bit"]["reason"]


def test_standard_gemma4_fallback_available_is_not_marked_not_recommended(monkeypatch):
    monkeypatch.setattr(
        mr,
        "model_runtime_compatibility",
        lambda model_id, *, engine=None: {
            "model_id": model_id,
            "engine": engine,
            "status": "fallback_available",
            "supported": True,
            "preferred_runtime": "MLX-LM fallback",
        } if "gemma-4-26b" in model_id else _runtime_supported(model_id, engine=engine),
    )

    result = mr.recommend_catalog(_mac(64), engine="local_mlx")
    by_id = {m["id"]: m for m in result["models"]}
    assert by_id["mlx-community/gemma-4-26b-a4b-it-4bit"]["status"] != mr.NOT_RECOMMENDED
    assert by_id["mlx-community/gemma-4-26b-a4b-it-4bit"]["runtime_compatibility"]["status"] == "fallback_available"


def test_gemma4_12b_runtime_update_needed_is_not_recommended_but_26b_is_supported(monkeypatch):
    def fake_runtime(model_id, *, engine=None):
        if "gemma-4-12b" in model_id.lower():
            return {
                "model_id": model_id,
                "engine": engine,
                "status": "runtime_update_needed",
                "supported": False,
                "user_message": "Runtime update needed",
            }
        return _runtime_supported(model_id, engine=engine)

    monkeypatch.setattr(mr, "model_runtime_compatibility", fake_runtime)

    result = mr.recommend_catalog(_mac(64), engine="local_mlx")
    by_id = {m["id"]: m for m in result["models"]}
    assert by_id["mlx-community/gemma-4-12B-it-4bit"]["status"] == mr.NOT_RECOMMENDED
    assert by_id["mlx-community/gemma-4-26b-a4b-it-4bit"]["status"] != mr.NOT_RECOMMENDED


# ── size parsing ──────────────────────────────────────────────────────────────

def test_parse_size_gb_units():
    assert mr.parse_size_gb("4.7GB") == 4.7
    assert mr.parse_size_gb("963MB") == round(963 / 1024, 3)
    assert mr.parse_size_gb("40GB+") == 40.0
    assert mr.parse_size_gb("13.3GB") == 13.3


def test_parse_size_gb_non_numeric():
    assert mr.parse_size_gb("pull required") is None
    assert mr.parse_size_gb("실행 도구에서 관리") is None
    assert mr.parse_size_gb(None) is None


# ── platform gating ─────────────────────────────────────────────────────────--

def _mac(ram_gb):
    return {"os": "darwin", "arch": "arm64", "ram_mb": ram_gb * 1024,
            "gpu": {"vendor": "apple", "vram_mb": ram_gb * 1024}}


def _windows_nvidia(ram_gb, vram_gb):
    return {"os": "windows", "arch": "x86_64", "ram_mb": ram_gb * 1024,
            "gpu": {"vendor": "nvidia", "vram_mb": vram_gb * 1024}}


def test_is_apple_silicon():
    assert mr.is_apple_silicon(_mac(16)) is True
    assert mr.is_apple_silicon(_windows_nvidia(16, 8)) is False


def test_mlx_not_recommended_off_apple_silicon():
    result = mr.recommend_catalog(_windows_nvidia(64, 24), engine="local_mlx")
    assert result["engine_available"] is False
    assert result["top_pick"] is None
    assert all(m["status"] == mr.NOT_RECOMMENDED for m in result["models"])
    assert all("Apple Silicon" in m["reason"] for m in result["models"])


# ── sizing tiers ────────────────────────────────────────────────────────────--

def test_small_mac_recommends_small_models_only():
    result = mr.recommend_catalog(_mac(8), engine="local_mlx")
    assert result["engine_available"] is True
    by_id = {m["id"]: m for m in result["models"]}
    # 8GB fits only the ultralight tier: LFM2.5 outright, Gemma 4 E2B with
    # little headroom. Everything above stays off the table.
    assert by_id["mlx-community/LFM2.5-2.6B-4bit"]["status"] == mr.RECOMMENDED
    assert by_id["mlx-community/gemma-4-e2b-it-4bit"]["status"] == mr.COMPATIBLE
    assert by_id["mlx-community/gemma-4-31b-it-4bit"]["status"] == mr.NOT_RECOMMENDED


def test_large_mac_recommends_large_models():
    result = mr.recommend_catalog(_mac(128), engine="local_mlx")
    by_id = {m["id"]: m for m in result["models"]}
    assert by_id["mlx-community/gemma-4-31b-it-4bit"]["status"] == mr.RECOMMENDED
    assert result["top_pick"] is not None
    # top pick is the largest recommended model
    assert result["top_pick"]["size_gb"] == max(
        m["size_gb"] for m in result["models"]
        if m["status"] == mr.RECOMMENDED and m["size_gb"]
    )


def test_counts_sum_to_total():
    result = mr.recommend_catalog(_mac(32), engine="local_mlx")
    total = len(result["models"])
    assert sum(result["counts"].values()) == total
    assert total >= 10


# ── family grouping ─────────────────────────────────────────────────────────--

def test_families_grouped_and_ordered():
    result = mr.recommend_catalog(_mac(64), engine="local_mlx")
    fam_names = [f["family"] for f in result["families"]]
    assert "Gemma 4" in fam_names
    assert "Qwen3.6" in fam_names
    assert "Qwen3.5" in fam_names
    # Superseded generations never reach the catalog, so they cannot group.
    assert "Qwen3-VL" not in fam_names
    assert "Gemma 3" not in fam_names
    assert "Gemma 2" not in fam_names
    assert fam_names.index("Gemma 4") < fam_names.index("Qwen3.6")
    # each family exposes a best pick structure
    for fam in result["families"]:
        assert "best" in fam and "models" in fam


def test_server_models_compatible_without_size():
    # vLLM/LM Studio entries advertise tool-managed models (no fixed size).
    result = mr.recommend_catalog(_mac(16), engine="vllm")
    assert result["models"]
    assert all(m["status"] in (mr.COMPATIBLE, mr.RECOMMENDED) for m in result["models"])


def test_source_metadata_is_present_for_general_mode():
    result = mr.recommend_catalog(_mac(32), engine="ollama")
    sample = result["models"][0]
    # Text-only entries earn a place in the tiers, so the recommender is no
    # longer multimodal-only — but every row still states its own modality.
    assert sample["modality"] in mr._RECOMMENDABLE_MODALITIES
    assert sample["source_country"]
    assert sample["source_company"]
    assert sample["execution_method"]
    assert sample["internet_requirement"]
