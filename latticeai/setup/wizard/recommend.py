"""The wizard's recommendation: environment scan in, tailored checklist out.

Pure function over the :func:`~latticeai.setup.wizard.detect.scan_environment`
payload — no probing, no installing. It picks the preferred engine, filters the
catalogue to what fits, and returns the component / engine / model / MCP groups
the setup screen renders, each already carrying a confirmation-token plan.
"""

from __future__ import annotations

import sys
from typing import Any, Dict, List

from latticeai.setup.wizard.catalog import (
    _CROSS_PLATFORM_MODEL_CATALOG,
    _MODEL_CATALOG,
    _best_model_for_engine,
    _filter_lower_family_versions,
)
from latticeai.setup.wizard.paths import OFFICIAL_DOWNLOADS
from latticeai.setup.wizard.plans import _hydrate_install_actions

# ── Recommendation Logic ──────────────────────────────────────────────────────

def get_recommendations(env: Dict[str, Any]) -> Dict[str, Any]:
    ram       = env["ram_gb"]
    chip      = env["chip"]
    mlx       = env["mlx"]
    tools     = env["tools"]
    api_keys  = env["api_keys"]
    disk_free = env["disk_free_gb"]
    is_apple  = chip["is_apple_silicon"]
    gpu       = env.get("gpu", {})
    cuda      = env.get("cuda", {})
    wsl       = env.get("wsl", {})
    cpu       = env.get("cpu", {})
    os_name   = env.get("os", "")

    max_model_gb = ram * 0.72  # ~28% headroom for OS + apps

    if is_apple:
        preferred_engine = "local_mlx"
    elif gpu.get("vendor") == "nvidia" and cuda.get("available") and (os_name == "Linux" or wsl.get("is_wsl")):
        preferred_engine = "vllm"
    elif tools.get("lms"):
        preferred_engine = "lmstudio"
    elif tools.get("ollama"):
        preferred_engine = "ollama"
    else:
        preferred_engine = "llamacpp"

    apple_catalog = _filter_lower_family_versions(_MODEL_CATALOG)
    engine_catalog = (
        []
        if is_apple
        else _filter_lower_family_versions(_CROSS_PLATFORM_MODEL_CATALOG[preferred_engine])
    )
    best_id = _best_model_for_engine(
        "local_mlx" if is_apple else preferred_engine,
        ram,
        apple_catalog if is_apple else engine_catalog,
    )

    # ── Engines ──────────────────────────────────────────────────────────────
    engines: List[Dict] = []

    if is_apple:
        if mlx["available"] and mlx["mlx_vlm"]:
            engines.append({
                "id": "engine_mlx", "name": "MLX",
                "subtitle": f"{chip['name']} GPU 가속 · MLX-VLM 멀티모달 실행",
                "status": "installed", "priority": "recommended",
                "checked": True, "action": None, "badge": "설치됨",
            })
        else:
            engines.append({
                "id": "engine_mlx", "name": "MLX",
                "subtitle": f"{chip['name']} 전용 MLX-VLM 멀티모달 실행",
                "status": "available", "priority": "recommended",
                "checked": True,
                "action": {"type": "pip", "packages": ["mlx-vlm"], "verify_modules": ["mlx", "mlx_vlm"]},
                "badge": "설치 필요",
            })

    if tools.get("ollama"):
        engines.append({
            "id": "engine_ollama", "name": "Ollama",
            "subtitle": "범용 로컬 LLM 서버 · 크로스 플랫폼",
            "status": "installed", "priority": "recommended" if preferred_engine == "ollama" else "optional",
            "checked": preferred_engine == "ollama", "action": None, "badge": "설치됨",
        })
    else:
        hint = "brew install 가능" if (tools.get("brew") or env["os"] == "Darwin") else "수동 설치 필요"
        engines.append({
            "id": "engine_ollama", "name": "Ollama",
            "subtitle": "범용 로컬 LLM 서버 · 크로스 플랫폼",
            "status": "available", "priority": "recommended" if preferred_engine == "ollama" else "optional",
            "checked": preferred_engine == "ollama",
            "action": (
                {"type": "brew", "package": "ollama", "binary": "ollama", "official_url": OFFICIAL_DOWNLOADS["ollama"]}
                if tools.get("brew")
                else {"type": "url", "url": OFFICIAL_DOWNLOADS["ollama"], "binary": "ollama"}
            ),
            "badge": hint,
        })

    if not is_apple:
        lmstudio_installed = bool(tools.get("lms"))
        engines.append({
            "id": "engine_lmstudio", "name": "LM Studio",
            "subtitle": "Windows/macOS/Linux 데스크톱 GPU 서버 · 모델 다운로드 UI 포함",
            "status": "installed" if lmstudio_installed else "available",
            "priority": "recommended" if preferred_engine == "lmstudio" else "optional",
            "checked": preferred_engine == "lmstudio",
            "action": None if lmstudio_installed else {"type": "url", "url": OFFICIAL_DOWNLOADS["lmstudio"], "binary": "lms"},
            "badge": "설치됨" if lmstudio_installed else "설치 필요",
        })
        if gpu.get("vendor") == "nvidia":
            engines.append({
                "id": "engine_cuda", "name": "CUDA",
                "subtitle": f"NVIDIA {gpu.get('name') or 'GPU'} · VRAM {gpu.get('vram_gb') or 0} GB",
                "status": "installed" if cuda.get("available") else "available",
                "priority": "recommended",
                "checked": False,
                "action": None if cuda.get("available") else {"type": "url", "url": OFFICIAL_DOWNLOADS["cuda"], "binary": "nvcc"},
                "badge": cuda.get("version") or ("감지됨" if cuda.get("available") else "설치 필요"),
            })
            engines.append({
                "id": "engine_vllm", "name": "vLLM",
                "subtitle": "NVIDIA 서버형 추론 · Windows는 WSL/Linux 권장",
                "status": "available",
                "priority": "recommended" if preferred_engine == "vllm" else "optional",
                "checked": preferred_engine == "vllm",
                "action": {"type": "pip", "packages": ["vllm", "huggingface_hub[cli]"], "verify_modules": ["vllm", "huggingface_hub"]},
                "badge": "WSL/Linux 권장" if os_name == "Windows" and not wsl.get("is_wsl") else "설치 가능",
            })
        elif gpu.get("vendor") in {"amd", "intel"}:
            engines.append({
                "id": "engine_vulkan_directml", "name": "Vulkan/DirectML",
                "subtitle": f"{gpu.get('vendor', '').upper()} GPU 감지 · LM Studio 또는 llama.cpp 백엔드 권장",
                "status": "available",
                "priority": "recommended" if preferred_engine in {"lmstudio", "llamacpp"} else "optional",
                "checked": False,
                "action": None,
                "badge": gpu.get("backend") or "GPU",
            })

    components: List[Dict] = []
    component_specs = [
        ("homebrew", "Homebrew", "macOS 패키지 관리자 · 자동 설치 기반", "brew", None, "recommended"),
        ("git", "Git", "저장소 · 확장 · MCP 도구 연동에 필요", "git", "git", "recommended"),
        ("node", "Node.js", "npm 패키지와 VS Code 확장 개발에 필요", "node", "node", "optional"),
        ("tesseract", "Tesseract OCR", "이미지/PDF OCR 기능에 필요", "tesseract", "tesseract", "optional"),
    ]
    for cid, name, subtitle, binary, brew_pkg, priority in component_specs:
        installed = bool(tools.get(binary))
        if cid == "homebrew" and env["os"] != "Darwin":
            continue
        if installed:
            components.append({
                "id": f"component_{cid}", "name": name,
                "subtitle": subtitle, "status": "installed",
                "priority": priority, "checked": False, "action": None,
                "badge": "설치됨",
            })
            continue
        if cid == "homebrew":
            action = {"type": "url", "url": OFFICIAL_DOWNLOADS["homebrew"], "binary": "brew"}
        elif tools.get("brew") and brew_pkg:
            action = {
                "type": "brew",
                "package": brew_pkg,
                "binary": binary or "",
                "official_url": OFFICIAL_DOWNLOADS.get(cid, ""),
            }
        else:
            action = {
                "type": "url",
                "url": OFFICIAL_DOWNLOADS.get(cid, ""),
                "binary": binary or "",
            }
        components.append({
            "id": f"component_{cid}", "name": name,
            "subtitle": subtitle, "status": "available",
            "priority": priority, "checked": priority == "recommended",
            "action": action, "badge": "설치 필요",
        })

    python_ok = sys.version_info >= (3, 11)
    if not python_ok:
        components.insert(0, {
            "id": "component_python", "name": "Python 3.11+",
            "subtitle": "Lattice AI 서버 실행에 필요한 Python 런타임",
            "status": "available", "priority": "recommended", "checked": True,
            "action": {"type": "url", "url": OFFICIAL_DOWNLOADS["python"], "binary": "python3"},
            "badge": "업데이트 필요",
        })

    for provider, has_key in api_keys.items():
        if has_key:
            engines.append({
                "id": f"engine_{provider}", "name": provider.title(),
                "subtitle": f"{provider.upper()}_API_KEY 감지됨 · 클라우드 API",
                "status": "ready", "priority": "optional",
                "checked": False, "action": None, "badge": "준비됨",
            })

    # ── Models ───────────────────────────────────────────────────────────────
    models: List[Dict] = []

    if is_apple:
        for mid, mname, size_gb, tag, desc, min_ram in apple_catalog:
            fits    = ram >= min_ram and size_gb <= max_model_gb and disk_free >= size_gb + 2
            is_best = mid == best_id
            models.append({
                "id":       f"model_{mid.replace('/', '__').replace('-', '_')}",
                "model_id": mid,
                "name":     mname,
                "subtitle": desc,
                "size_gb":  size_gb,
                "tag":      tag,
                "fits":     fits,
                "priority": "recommended" if is_best else "optional",
                "checked":  is_best and fits,
                "disabled": not fits,
                "badge":    f"{size_gb} GB",
                "action":   {"type": "load_model", "model_id": mid} if fits else None,
            })
    else:
        vram_gb = float(gpu.get("vram_gb") or 0)
        gpu_budget_gb = vram_gb * 1.15 if gpu.get("vendor") in {"nvidia", "amd", "intel"} and vram_gb else max_model_gb
        model_budget_gb = min(max_model_gb, gpu_budget_gb)
        for mid, mname, size_gb, tag, desc, min_ram in engine_catalog:
            fits = ram >= min_ram and size_gb <= model_budget_gb and disk_free >= size_gb + 2
            is_best = mid == best_id
            models.append({
                "id":       f"model_{mid.replace('/', '__').replace(':', '__').replace('-', '_')}",
                "model_id": mid,
                "name":     mname,
                "subtitle": desc,
                "size_gb":  size_gb,
                "tag":      tag,
                "fits":     fits,
                "priority": "recommended" if is_best else "optional",
                "checked":  is_best and fits,
                "disabled": not fits,
                "badge":    f"{size_gb} GB · {preferred_engine}",
                "action":   {"type": "load_model", "model_id": mid} if fits else None,
            })
    if models and not any(item.get("checked") for item in models):
        for item in models:
            if not item.get("disabled"):
                item["priority"] = "recommended"
                item["checked"] = True
                break

    # ── MCPs ─────────────────────────────────────────────────────────────────
    mcps: List[Dict] = [
        {
            "id": "mcp_files", "name": "Workspace Files",
            "subtitle": "파일 읽기/쓰기 · 코드 생성 · 미리보기",
            "status": "active", "priority": "recommended",
            "checked": True, "action": None,
            "badge": "기본 탑재", "needs_auth": False,
        },
        {
            "id": "mcp_presentations", "name": "Presentations",
            "subtitle": "PPTX · 슬라이드 자동 생성",
            "status": "active", "priority": "optional",
            "checked": False, "action": None,
            "badge": "기본 탑재", "needs_auth": False,
        },
        {
            "id": "mcp_github", "name": "GitHub",
            "subtitle": "저장소 · PR · 이슈 · CI 연동",
            "status": "available", "priority": "optional",
            "checked": False,
            "action": {"type": "auth", "url": "https://github.com/apps", "mcp_id": "github"},
            "badge": "인증 필요", "needs_auth": True,
        },
        {
            "id": "mcp_googledrive", "name": "Google Drive",
            "subtitle": "Docs · Sheets · Drive 파일 연동",
            "status": "available", "priority": "optional",
            "checked": False,
            "action": {"type": "auth", "url": "https://chatgpt.com/connectors", "mcp_id": "google-drive"},
            "badge": "인증 필요", "needs_auth": True,
        },
        {
            "id": "mcp_slack", "name": "Slack",
            "subtitle": "팀 채널 공유 · 알림 워크플로",
            "status": "available", "priority": "optional",
            "checked": False,
            "action": {"type": "auth", "url": "https://chatgpt.com/connectors", "mcp_id": "slack"},
            "badge": "인증 필요", "needs_auth": True,
        },
    ]

    return _hydrate_install_actions({
        "components": components,
        "engines": engines,
        "models":  models,
        "mcps":    mcps,
        "summary": {
            "chip":            chip["name"],
            "cpu_cores":       cpu.get("logical_cores"),
            "cpu_instructions": cpu.get("instructions", []),
            "gpu":             gpu.get("name") or gpu.get("vendor"),
            "gpu_vendor":      gpu.get("vendor"),
            "vram_gb":         gpu.get("vram_gb"),
            "cuda":            cuda.get("available"),
            "cuda_version":    cuda.get("version"),
            "wsl":             wsl,
            "preferred_engine": preferred_engine,
            "ram_gb":          ram,
            "disk_free_gb":    disk_free,
            "is_apple_silicon": is_apple,
            "max_model_gb":    round(max_model_gb, 1),
        },
    })
