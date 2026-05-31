"""Tests for hardware-aware local model recommendation.

Uses synthetic system profiles so the tri-state classification
(recommended / compatible / not_recommended) is verified deterministically
without touching real hardware.
"""

from latticeai.services import model_recommendation as mr


# ── size parsing ──────────────────────────────────────────────────────────────

def test_parse_size_gb_units():
    assert mr.parse_size_gb("4.7GB") == 4.7
    assert mr.parse_size_gb("963MB") == round(963 / 1024, 3)
    assert mr.parse_size_gb("40GB+") == 40.0
    assert mr.parse_size_gb("13.3GB") == 13.3


def test_parse_size_gb_non_numeric():
    assert mr.parse_size_gb("pull required") is None
    assert mr.parse_size_gb("server model") is None
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
    # ~1 GB model fits comfortably on 8 GB
    assert by_id["mlx-community/gemma-3-1b-it-4bit"]["status"] == mr.RECOMMENDED
    # 40 GB+ model cannot run on 8 GB
    assert by_id["mlx-community/Llama-3.3-70B-Instruct-4bit"]["status"] == mr.NOT_RECOMMENDED


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
    assert total > 10


# ── family grouping ─────────────────────────────────────────────────────────--

def test_families_grouped_and_ordered():
    result = mr.recommend_catalog(_mac(64), engine="local_mlx")
    fam_names = [f["family"] for f in result["families"]]
    assert "Gemma 4" in fam_names
    assert "Phi" in fam_names
    # Gemma 4 should rank before Gemma 2 (newer first)
    if "Gemma 4" in fam_names and "Gemma 2" in fam_names:
        assert fam_names.index("Gemma 4") < fam_names.index("Gemma 2")
    # each family exposes a best pick structure
    for fam in result["families"]:
        assert "best" in fam and "models" in fam


def test_server_models_compatible_without_size():
    # vLLM/LM Studio entries advertise "server model" (no fixed size).
    result = mr.recommend_catalog(_mac(16), engine="vllm")
    assert result["models"]
    assert all(m["status"] in (mr.COMPATIBLE, mr.RECOMMENDED) for m in result["models"])


def test_deepseek_family_present_for_ollama():
    result = mr.recommend_catalog(_mac(32), engine="ollama")
    fam_names = [f["family"] for f in result["families"]]
    assert "DeepSeek" in fam_names
