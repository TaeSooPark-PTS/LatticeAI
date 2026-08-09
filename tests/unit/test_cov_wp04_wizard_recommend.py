"""wp04 — recommendation coverage for latticeai/setup/wizard.py.

`get_recommendations` is a pure function of a scanned environment, so every
branch (Apple/NVIDIA/AMD, engine preference, component install strategy) is
reachable by handing it a synthetic env instead of a real machine.
"""

import sys

from latticeai.setup import wizard as setup


class _ModuleShim:
    """Stand-in for an imported module: overrides some names, delegates the rest."""

    def __init__(self, real, **overrides):
        self.__dict__["_real"] = real
        self.__dict__["_overrides"] = overrides

    def __getattr__(self, name):
        overrides = self.__dict__["_overrides"]
        if name in overrides:
            return overrides[name]
        return getattr(self.__dict__["_real"], name)


def _env(**overrides):
    env = {
        "os": "Linux",
        "os_version": "6.8.0",
        "ram_gb": 32.0,
        "disk_free_gb": 500.0,
        "chip": {"name": "Test CPU", "arch": "x86_64", "is_apple_silicon": False, "gen": None},
        "cpu": {"model": "Test CPU", "physical_cores": 8, "logical_cores": 16, "instructions": ["avx2"]},
        "gpu": {"devices": [], "vendor": "none", "name": "", "vram_mb": 0, "vram_gb": 0.0, "backend": "cpu"},
        "cuda": {"available": False, "nvidia_smi": None, "nvcc": None, "version": ""},
        "wsl": {"is_wsl": False, "version": ""},
        "mlx": {"available": False, "mlx_vlm": False},
        "tools": {},
        "api_keys": {"openai": False, "openrouter": False, "groq": False, "together": False},
    }
    env.update(overrides)
    return env


def _apple_env(**overrides):
    base = _env(
        os="Darwin",
        chip={"name": "Apple M3", "arch": "arm64", "is_apple_silicon": True, "gen": 3},
        gpu={"devices": [], "vendor": "apple", "name": "Apple M3", "vram_mb": 0, "vram_gb": 0.0, "backend": "metal/mlx"},
        mlx={"available": True, "mlx_vlm": True},
    )
    base.update(overrides)
    return base


def _by_id(items):
    return {item["id"]: item for item in items}


# ── engine preference ─────────────────────────────────────────────────────────

def test_apple_silicon_recommends_installed_mlx_and_local_models():
    recs = setup.get_recommendations(_apple_env())

    engines = _by_id(recs["engines"])
    assert recs["summary"]["preferred_engine"] == "local_mlx"
    assert recs["summary"]["is_apple_silicon"] is True
    assert recs["summary"]["max_model_gb"] == 23.0
    assert engines["engine_mlx"]["status"] == "installed"
    assert engines["engine_mlx"]["action"] is None
    assert "engine_lmstudio" not in engines
    checked = [model for model in recs["models"] if model["checked"]]
    assert [model["model_id"] for model in checked] == ["mlx-community/gemma-4-26b-a4b-it-4bit"]
    assert checked[0]["action"] == {"type": "load_model", "model_id": "mlx-community/gemma-4-26b-a4b-it-4bit"}


def test_apple_silicon_offers_an_mlx_install_when_the_module_is_missing():
    recs = setup.get_recommendations(_apple_env(mlx={"available": False, "mlx_vlm": False}))

    mlx = _by_id(recs["engines"])["engine_mlx"]

    assert mlx["status"] == "available"
    assert mlx["badge"] == "설치 필요"
    assert mlx["action"]["packages"] == ["mlx-vlm"]
    assert mlx["action"]["command_plan"]["command_count"] == 1
    assert mlx["action"]["confirmation_token"] == mlx["action"]["command_plan"]["confirmation_token"]


def test_nvidia_linux_with_cuda_prefers_vllm():
    recs = setup.get_recommendations(
        _env(
            ram_gb=64.0,
            gpu={"devices": [], "vendor": "nvidia", "name": "RTX 4090", "vram_mb": 24576, "vram_gb": 24.0, "backend": "cuda"},
            cuda={"available": True, "nvidia_smi": "/nvidia-smi", "nvcc": "/nvcc", "version": "12.4"},
        )
    )

    engines = _by_id(recs["engines"])
    assert recs["summary"]["preferred_engine"] == "vllm"
    assert engines["engine_vllm"]["checked"] is True
    assert engines["engine_vllm"]["badge"] == "설치 가능"
    assert engines["engine_vllm"]["action"]["confirmation_token"]
    assert engines["engine_cuda"]["status"] == "installed"
    assert engines["engine_cuda"]["action"] is None
    assert engines["engine_cuda"]["badge"] == "12.4"
    assert engines["engine_lmstudio"]["status"] == "available"
    checked = [model["model_id"] for model in recs["models"] if model["checked"]]
    assert checked == ["vllm:suitch/gemma-4-31B-it-4bit"]
    assert recs["models"][0]["badge"].endswith("vllm")


def test_windows_nvidia_without_cuda_or_wsl_falls_back_to_llamacpp():
    recs = setup.get_recommendations(
        _env(
            os="Windows",
            gpu={"devices": [], "vendor": "nvidia", "name": "RTX 3060", "vram_mb": 12288, "vram_gb": 12.0, "backend": "cuda"},
        )
    )

    engines = _by_id(recs["engines"])
    assert recs["summary"]["preferred_engine"] == "llamacpp"
    assert engines["engine_cuda"]["status"] == "available"
    assert engines["engine_cuda"]["action"]["url"] == setup.OFFICIAL_DOWNLOADS["cuda"]
    assert engines["engine_cuda"]["badge"] == "설치 필요"
    assert engines["engine_vllm"]["badge"] == "WSL/Linux 권장"
    assert engines["engine_vllm"]["checked"] is False


def test_wsl_nvidia_host_still_prefers_vllm():
    recs = setup.get_recommendations(
        _env(
            os="Windows",
            ram_gb=64.0,
            gpu={"devices": [], "vendor": "nvidia", "name": "RTX 4090", "vram_mb": 24576, "vram_gb": 24.0, "backend": "cuda"},
            cuda={"available": True, "nvidia_smi": "/nvidia-smi", "nvcc": None, "version": ""},
            wsl={"is_wsl": True, "version": "2"},
        )
    )

    assert recs["summary"]["preferred_engine"] == "vllm"
    assert _by_id(recs["engines"])["engine_cuda"]["badge"] == "감지됨"


def test_lm_studio_is_preferred_when_the_cli_is_installed():
    recs = setup.get_recommendations(_env(tools={"lms": True}))

    engines = _by_id(recs["engines"])
    assert recs["summary"]["preferred_engine"] == "lmstudio"
    assert engines["engine_lmstudio"]["status"] == "installed"
    assert engines["engine_lmstudio"]["checked"] is True
    assert engines["engine_lmstudio"]["action"] is None
    assert recs["models"][0]["model_id"].startswith("lmstudio:")


def test_ollama_is_preferred_when_only_ollama_is_installed():
    recs = setup.get_recommendations(_env(tools={"ollama": True}))

    ollama = _by_id(recs["engines"])["engine_ollama"]
    assert recs["summary"]["preferred_engine"] == "ollama"
    assert ollama["status"] == "installed"
    assert ollama["priority"] == "recommended"
    assert ollama["action"] is None
    assert recs["models"][0]["model_id"].startswith("ollama:")


def test_missing_ollama_is_offered_through_brew_when_brew_exists():
    recs = setup.get_recommendations(_env(os="Darwin", tools={"brew": True}))

    ollama = _by_id(recs["engines"])["engine_ollama"]
    assert ollama["badge"] == "brew install 가능"
    assert ollama["action"]["type"] == "brew"
    assert ollama["action"]["package"] == "ollama"
    assert ollama["action"]["confirmation_token"]


def test_missing_ollama_falls_back_to_the_official_download():
    recs = setup.get_recommendations(_env(os="Linux"))

    ollama = _by_id(recs["engines"])["engine_ollama"]
    assert ollama["badge"] == "수동 설치 필요"
    assert ollama["action"] == {
        "type": "url",
        "url": setup.OFFICIAL_DOWNLOADS["ollama"],
        "binary": "ollama",
    }


def test_amd_gpu_is_offered_a_vulkan_directml_engine():
    recs = setup.get_recommendations(
        _env(
            gpu={"devices": [], "vendor": "amd", "name": "RX 7900", "vram_mb": 20480, "vram_gb": 20.0, "backend": "rocm/vulkan"},
        )
    )

    engine = _by_id(recs["engines"])["engine_vulkan_directml"]
    assert engine["priority"] == "recommended"
    assert engine["badge"] == "rocm/vulkan"
    assert engine["action"] is None
    assert recs["summary"]["gpu_vendor"] == "amd"


# ── components ────────────────────────────────────────────────────────────────

def test_homebrew_component_is_skipped_outside_macos():
    recs = setup.get_recommendations(_env(os="Linux"))

    components = _by_id(recs["components"])
    assert "component_homebrew" not in components
    assert components["component_git"]["action"]["type"] == "url"
    assert components["component_git"]["checked"] is True
    assert components["component_node"]["checked"] is False


def test_homebrew_component_points_at_the_official_installer_on_macos():
    recs = setup.get_recommendations(_env(os="Darwin"))

    components = _by_id(recs["components"])
    assert components["component_homebrew"]["action"] == {
        "type": "url",
        "url": setup.OFFICIAL_DOWNLOADS["homebrew"],
        "binary": "brew",
    }
    assert components["component_tesseract"]["action"]["type"] == "url"


def test_installed_components_are_reported_without_an_action():
    recs = setup.get_recommendations(_env(os="Darwin", tools={"brew": True, "git": True}))

    components = _by_id(recs["components"])
    assert components["component_homebrew"]["status"] == "installed"
    assert components["component_git"]["status"] == "installed"
    assert components["component_git"]["action"] is None
    assert components["component_node"]["action"]["type"] == "brew"
    assert components["component_node"]["action"]["package"] == "node"
    assert components["component_node"]["action"]["confirmation_token"]


def test_old_python_runtimes_get_an_upgrade_component(monkeypatch):
    monkeypatch.setattr(setup, "sys", _ModuleShim(sys, version_info=(3, 10, 4)))

    recs = setup.get_recommendations(_env(os="Linux"))

    assert recs["components"][0]["id"] == "component_python"
    assert recs["components"][0]["badge"] == "업데이트 필요"
    assert recs["components"][0]["action"]["url"] == setup.OFFICIAL_DOWNLOADS["python"]


def test_detected_api_keys_become_ready_cloud_engines():
    recs = setup.get_recommendations(
        _env(api_keys={"openai": True, "openrouter": False, "groq": False, "together": True})
    )

    engines = _by_id(recs["engines"])
    assert engines["engine_openai"]["status"] == "ready"
    assert engines["engine_openai"]["name"] == "Openai"
    assert engines["engine_together"]["badge"] == "준비됨"
    assert "engine_groq" not in engines


# ── models ────────────────────────────────────────────────────────────────────

def test_first_fitting_model_is_promoted_when_nothing_is_checked(monkeypatch):
    monkeypatch.setattr(setup, "_best_model_for_engine", lambda engine, ram_gb, rows: "")

    recs = setup.get_recommendations(_apple_env())

    checked = [model for model in recs["models"] if model["checked"]]
    assert len(checked) == 1
    assert checked[0] is recs["models"][0]
    assert checked[0]["priority"] == "recommended"
    assert checked[0]["disabled"] is False


def test_models_stay_disabled_when_the_disk_is_too_small():
    recs = setup.get_recommendations(_env(ram_gb=4.0, disk_free_gb=1.0))

    assert recs["models"]
    assert all(model["disabled"] for model in recs["models"])
    assert all(model["action"] is None for model in recs["models"])
    assert not any(model["checked"] for model in recs["models"])


def test_mcp_catalog_is_hydrated_with_no_install_commands():
    recs = setup.get_recommendations(_env())

    mcps = _by_id(recs["mcps"])
    assert mcps["mcp_files"]["needs_auth"] is False
    assert mcps["mcp_github"]["action"] == {
        "type": "auth",
        "url": "https://github.com/apps",
        "mcp_id": "github",
    }
    assert "confirmation_token" not in mcps["mcp_github"]["action"]


def test_summary_reports_the_scanned_hardware():
    recs = setup.get_recommendations(
        _env(
            ram_gb=64.0,
            gpu={"devices": [], "vendor": "nvidia", "name": "RTX 4090", "vram_mb": 24576, "vram_gb": 24.0, "backend": "cuda"},
            cuda={"available": True, "nvidia_smi": "/nvidia-smi", "nvcc": "/nvcc", "version": "12.4"},
        )
    )

    assert recs["summary"] == {
        "chip": "Test CPU",
        "cpu_cores": 16,
        "cpu_instructions": ["avx2"],
        "gpu": "RTX 4090",
        "gpu_vendor": "nvidia",
        "vram_gb": 24.0,
        "cuda": True,
        "cuda_version": "12.4",
        "wsl": {"is_wsl": False, "version": ""},
        "preferred_engine": "vllm",
        "ram_gb": 64.0,
        "disk_free_gb": 500.0,
        "is_apple_silicon": False,
        "max_model_gb": 46.1,
    }
