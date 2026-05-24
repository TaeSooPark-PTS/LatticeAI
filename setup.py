"""
Smart Setup Wizard — Environment Scanner, Recommender & Auto-Installer
Detects hardware, tools, and API keys; returns tailored recommendations;
streams SSE installation progress.
"""

import asyncio
import json as _json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Tuple

# ── Helpers ───────────────────────────────────────────────────────────────────

def _cmd(args: List[str], timeout: int = 10) -> str:
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
        return r.stdout.strip()
    except Exception:
        return ""

def _sse(data: Dict) -> str:
    return f"data: {_json.dumps(data, ensure_ascii=False)}\n\n"

OFFICIAL_DOWNLOADS: Dict[str, str] = {
    "homebrew": "https://brew.sh",
    "python": "https://www.python.org/downloads/",
    "node": "https://nodejs.org/en/download",
    "git": "https://git-scm.com/downloads",
    "ollama": "https://ollama.com/download",
    "lmstudio": "https://lmstudio.ai/download",
    "mlx": "https://ml-explore.github.io/mlx/build/html/install.html",
    "cloudflared": "https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/",
    "tesseract": "https://tesseract-ocr.github.io/tessdoc/Installation.html",
}

COMMON_PATH_DIRS = [
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/usr/bin",
    "/bin",
    str(Path.home() / ".local" / "bin"),
    str(Path.home() / ".cargo" / "bin"),
    str(Path.home() / ".latticeai" / "bin"),
]

PACKAGE_MODULES: Dict[str, str] = {
    "mlx-lm": "mlx_lm",
    "mlx-vlm": "mlx_vlm",
    "huggingface_hub[cli]": "huggingface_hub",
    "openai-whisper": "whisper",
}


def _project_env_file() -> Path:
    return Path(__file__).resolve().parent / ".env"


def _update_env_file(env_file: Path, key: str, value: str) -> None:
    lines: List[str] = []
    found = False
    if env_file.exists():
        lines = env_file.read_text(encoding="utf-8").splitlines()
    updated: List[str] = []
    for line in lines:
        if line.startswith(f"{key}="):
            updated.append(f"{key}={value}")
            found = True
        else:
            updated.append(line)
    if not found:
        updated.append(f"{key}={value}")
    env_file.write_text("\n".join(updated) + "\n", encoding="utf-8")


def _merge_path_dirs(dirs: List[str]) -> List[str]:
    current = os.environ.get("PATH", "")
    parts = [p for p in current.split(os.pathsep) if p]
    for item in dirs:
        expanded = str(Path(item).expanduser())
        if Path(expanded).exists() and expanded not in parts:
            parts.insert(0, expanded)
    os.environ["PATH"] = os.pathsep.join(parts)
    return parts


def _persist_extra_path(dirs: List[str]) -> None:
    existing = [
        p for p in os.environ.get("LATTICEAI_EXTRA_PATH", "").split(os.pathsep)
        if p
    ]
    merged = existing[:]
    for item in dirs:
        expanded = str(Path(item).expanduser())
        if Path(expanded).exists() and expanded not in merged:
            merged.append(expanded)
    if merged:
        os.environ["LATTICEAI_EXTRA_PATH"] = os.pathsep.join(merged)
        _update_env_file(_project_env_file(), "LATTICEAI_EXTRA_PATH", os.environ["LATTICEAI_EXTRA_PATH"])


def repair_path_for(binary: str | None = None) -> List[str]:
    before = shutil.which(binary) if binary else None
    paths = _merge_path_dirs(COMMON_PATH_DIRS)
    if binary and not before and shutil.which(binary):
        _persist_extra_path(COMMON_PATH_DIRS)
    return paths


def _which_detail(binary: str) -> Dict[str, Any]:
    path = shutil.which(binary)
    return {"installed": path is not None, "path": path}


def _module_available(module_name: str) -> bool:
    import importlib.util
    return importlib.util.find_spec(module_name) is not None


def _package_module(package: str) -> str:
    return PACKAGE_MODULES.get(package, package.replace("-", "_").split("[", 1)[0])


def _component_detail(name: str, binary: str | None = None, module: str | None = None) -> Dict[str, Any]:
    detail: Dict[str, Any] = {"official_url": OFFICIAL_DOWNLOADS.get(name)}
    if binary:
        detail.update(_which_detail(binary))
    if module:
        detail["module_available"] = _module_available(module)
        detail["installed"] = bool(detail.get("installed") or detail["module_available"])
    return detail


def _verify_binary(binary: str, version_args: List[str] | None = None, timeout: int = 20) -> Tuple[bool, str]:
    repair_path_for(binary)
    found = shutil.which(binary)
    if not found:
        return False, f"{binary} 실행 파일을 PATH에서 찾지 못했습니다."
    args = [found, *(version_args or ["--version"])]
    try:
        completed = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    except Exception as e:
        return False, str(e)
    output = (completed.stdout or completed.stderr or "").strip().splitlines()
    if completed.returncode == 0:
        return True, output[0] if output else found
    return False, (completed.stderr or completed.stdout or f"returncode={completed.returncode}")[-400:]


async def _wait_for_binary(binary: str, seconds: int = 300) -> Tuple[bool, str]:
    deadline = time.time() + seconds
    while time.time() < deadline:
        ok, msg = _verify_binary(binary)
        if ok:
            return True, msg
        await asyncio.sleep(2)
    return False, f"{binary} 설치 완료를 제한 시간 안에 감지하지 못했습니다."

# ── Environment Detection ─────────────────────────────────────────────────────

def _detect_chip() -> Dict[str, Any]:
    arch = platform.machine()
    is_apple = arch == "arm64" and platform.system() == "Darwin"
    name = "Unknown CPU"
    gen: Any = None

    if is_apple:
        profiler = _cmd(["system_profiler", "SPHardwareDataType"], timeout=8)
        m = re.search(r"Chip:\s+(Apple M\S+)", profiler)
        name = m.group(1) if m else "Apple Silicon"
        gm = re.search(r"M(\d+)", name)
        gen = int(gm.group(1)) if gm else 1
    else:
        brand = _cmd(["sysctl", "-n", "machdep.cpu.brand_string"])
        name = brand or platform.processor() or "Unknown CPU"

    return {"name": name, "arch": arch, "is_apple_silicon": is_apple, "gen": gen}

def _detect_ram_gb() -> float:
    raw = _cmd(["sysctl", "-n", "hw.memsize"])
    if raw:
        try:
            return round(int(raw) / 1_073_741_824, 1)
        except ValueError:
            pass
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return round(int(line.split()[1]) / 1_048_576, 1)
    except Exception:
        pass
    return 0.0

def _detect_disk_free_gb() -> float:
    try:
        path = "C:\\" if platform.system() == "Windows" else "/"
        return round(shutil.disk_usage(path).free / 1_073_741_824, 1)
    except Exception:
        return 0.0

def _detect_tools() -> Dict[str, bool]:
    repair_path_for()
    return {t: shutil.which(t) is not None
            for t in ["brew", "ollama", "python3", "node", "npm", "git", "tesseract"]}

def _detect_mlx() -> Dict[str, Any]:
    has_lm = has_vlm = False
    try:
        import mlx.core  # noqa: F401
        try:
            import mlx_lm; has_lm = True  # noqa: F401,E702
        except ImportError:
            pass
        try:
            import mlx_vlm; has_vlm = True  # noqa: F401,E702
        except ImportError:
            pass
        return {"available": True, "mlx_lm": has_lm, "mlx_vlm": has_vlm}
    except Exception:
        return {"available": False, "mlx_lm": False, "mlx_vlm": False}

def _detect_api_keys() -> Dict[str, bool]:
    return {
        "openai":     bool(os.getenv("OPENAI_API_KEY")),
        "openrouter": bool(os.getenv("OPENROUTER_API_KEY")),
        "groq":       bool(os.getenv("GROQ_API_KEY")),
        "together":   bool(os.getenv("TOGETHER_API_KEY")),
    }

def scan_environment() -> Dict[str, Any]:
    chip = _detect_chip()
    tools = _detect_tools()
    return {
        "os":           platform.system(),
        "os_version":   platform.mac_ver()[0] if platform.system() == "Darwin" else platform.version(),
        "chip":         chip,
        "ram_gb":       _detect_ram_gb(),
        "disk_free_gb": _detect_disk_free_gb(),
        "tools":        tools,
        "components": {
            "homebrew": _component_detail("homebrew", "brew"),
            "python": {**_component_detail("python", "python3"), "version": platform.python_version()},
            "node": _component_detail("node", "node"),
            "npm": _component_detail("node", "npm"),
            "git": _component_detail("git", "git"),
            "ollama": _component_detail("ollama", "ollama"),
            "lmstudio": _component_detail("lmstudio", "lms"),
            "tesseract": _component_detail("tesseract", "tesseract"),
            "mlx": _component_detail("mlx", module="mlx"),
            "mlx_lm": _component_detail("mlx", module="mlx_lm"),
            "mlx_vlm": _component_detail("mlx", module="mlx_vlm"),
        },
        "path": {
            "active": os.environ.get("PATH", ""),
            "extra": os.environ.get("LATTICEAI_EXTRA_PATH", ""),
        },
        "mlx":          _detect_mlx(),
        "api_keys":     _detect_api_keys(),
    }

# ── Model Catalog ─────────────────────────────────────────────────────────────
# (model_id, display_name, size_gb, tag, description, min_ram_gb)
_MODEL_CATALOG = [
    ("mlx-community/gemma-2-2b-it-4bit",             "Gemma 2 2B",           1.6,  "초경량",  "빠른 응답 · 간단한 작업",        4),
    ("mlx-community/Llama-3.2-3B-Instruct-4bit",     "Llama 3.2 3B",         2.0,  "경량",    "일상 대화 · 빠름",              4),
    ("mlx-community/Qwen2.5-Coder-7B-Instruct-4bit", "Qwen 2.5 Coder 7B",    4.3,  "코딩",    "코드 생성 특화",                8),
    ("mlx-community/Llama-3.1-8B-Instruct-4bit",     "Llama 3.1 8B",         4.7,  "범용",    "균형 잡힌 성능",                8),
    ("mlx-community/gemma-2-9b-it-4bit",             "Gemma 2 9B",           5.4,  "정확도",  "높은 응답 품질",               10),
    ("mlx-community/Qwen2.5-Coder-14B-Instruct-4bit","Qwen 2.5 Coder 14B",   8.3,  "코딩+",   "고품질 코드 생성",              16),
    ("mlx-community/Phi-4-4bit",                     "Phi 4",                8.4,  "코딩",    "Microsoft 코딩 특화",           16),
    ("mlx-community/gemma-4-26b-a4b-it-4bit",        "Gemma 4 26B",         18.0,  "VLM",     "이미지 지원 · 추천 모델",        24),
    ("mlx-community/Qwen2.5-Coder-32B-Instruct-4bit","Qwen 2.5 Coder 32B",  18.5,  "코딩★",   "최고 코딩 품질",               24),
    ("mlx-community/DeepSeek-R1-0528-4bit",          "DeepSeek R1",         38.0,  "추론",    "복잡한 수학 · 추론",            48),
]

# ── Recommendation Logic ──────────────────────────────────────────────────────

def get_recommendations(env: Dict[str, Any]) -> Dict[str, Any]:
    ram       = env["ram_gb"]
    chip      = env["chip"]
    mlx       = env["mlx"]
    tools     = env["tools"]
    api_keys  = env["api_keys"]
    disk_free = env["disk_free_gb"]
    is_apple  = chip["is_apple_silicon"]

    max_model_gb = ram * 0.72  # ~28% headroom for OS + apps

    # pick the single "best" default model for this RAM
    if ram >= 48:
        best_id = "mlx-community/gemma-4-26b-a4b-it-4bit"
    elif ram >= 24:
        best_id = "mlx-community/Qwen2.5-Coder-14B-Instruct-4bit"
    elif ram >= 8:
        best_id = "mlx-community/Qwen2.5-Coder-7B-Instruct-4bit"
    else:
        best_id = "mlx-community/Llama-3.2-3B-Instruct-4bit"

    # ── Engines ──────────────────────────────────────────────────────────────
    engines: List[Dict] = []

    if is_apple:
        if mlx["available"] and mlx["mlx_lm"]:
            engines.append({
                "id": "engine_mlx", "name": "MLX",
                "subtitle": f"{chip['name']} GPU 가속 · 최고 성능",
                "status": "installed", "priority": "recommended",
                "checked": True, "action": None, "badge": "설치됨",
            })
        else:
            engines.append({
                "id": "engine_mlx", "name": "MLX",
                "subtitle": f"{chip['name']} 전용 최고 성능 엔진",
                "status": "available", "priority": "recommended",
                "checked": True,
                "action": {"type": "pip", "packages": ["mlx-lm", "mlx-vlm"], "verify_modules": ["mlx", "mlx_lm", "mlx_vlm"]},
                "badge": "설치 필요",
            })

    if tools.get("ollama"):
        engines.append({
            "id": "engine_ollama", "name": "Ollama",
            "subtitle": "범용 로컬 LLM 서버 · 크로스 플랫폼",
            "status": "installed", "priority": "optional",
            "checked": False, "action": None, "badge": "설치됨",
        })
    else:
        hint = "brew install 가능" if (tools.get("brew") or env["os"] == "Darwin") else "수동 설치 필요"
        engines.append({
            "id": "engine_ollama", "name": "Ollama",
            "subtitle": "범용 로컬 LLM 서버 · 크로스 플랫폼",
            "status": "available", "priority": "optional",
            "checked": False,
            "action": (
                {"type": "brew", "package": "ollama", "binary": "ollama", "official_url": OFFICIAL_DOWNLOADS["ollama"]}
                if tools.get("brew")
                else {"type": "url", "url": OFFICIAL_DOWNLOADS["ollama"], "binary": "ollama"}
            ),
            "badge": hint,
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
            action = {"type": "brew", "package": brew_pkg, "binary": binary, "official_url": OFFICIAL_DOWNLOADS.get(cid)}
        else:
            action = {"type": "url", "url": OFFICIAL_DOWNLOADS.get(cid, ""), "binary": binary}
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
        for mid, mname, size_gb, tag, desc, _ in _MODEL_CATALOG:
            fits    = size_gb <= max_model_gb and disk_free >= size_gb + 2
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

    return {
        "components": components,
        "engines": engines,
        "models":  models,
        "mcps":    mcps,
        "summary": {
            "chip":            chip["name"],
            "ram_gb":          ram,
            "disk_free_gb":    disk_free,
            "is_apple_silicon": is_apple,
            "max_model_gb":    round(max_model_gb, 1),
        },
    }

# ── Installation Stream ───────────────────────────────────────────────────────

def _verify_action(action: Dict[str, Any]) -> Tuple[bool, str]:
    atype = action.get("type")
    if atype == "pip":
        modules = action.get("verify_modules") or [_package_module(pkg) for pkg in action.get("packages", [])]
        missing = [module for module in modules if not _module_available(module)]
        if missing:
            return False, "Python 모듈 감지 실패: " + ", ".join(missing)
        return True, "Python 모듈 import 테스트 통과"
    binary = action.get("binary")
    if binary:
        return _verify_binary(binary)
    return True, "검증 항목 없음"


async def _repair_action(action: Dict[str, Any]) -> Tuple[bool, str]:
    binary = action.get("binary")
    if binary:
        repair_path_for(binary)
        ok, msg = _verify_binary(binary)
        if ok:
            return True, f"PATH 자동 보정 완료: {msg}"
    if action.get("type") == "pip":
        packages = action.get("packages", [])
        if packages:
            for pkg in packages:
                success, err = await _pip_install(pkg)
                if not success:
                    return False, err
            return _verify_action(action)
    return False, "자동 복구 방법을 찾지 못했습니다."


async def install_stream(items: List[Dict], router: Any) -> AsyncIterator[str]:
    for item in items:
        item_id    = item.get("id", "unknown")
        name       = item.get("name", item_id)
        action     = item.get("action") or {}
        atype      = action.get("type")

        if not atype:
            yield _sse({"id": item_id, "status": "skipped", "msg": f"{name} — 이미 준비됨"})
            await asyncio.sleep(0.04)
            continue

        yield _sse({"id": item_id, "status": "starting", "msg": f"{name} 준비 중..."})

        if atype == "pip":
            packages = action.get("packages", [])
            ok = True
            for pkg in packages:
                yield _sse({"id": item_id, "status": "running", "msg": f"pip install {pkg} ..."})
                success, err = await _pip_install(pkg)
                if success:
                    yield _sse({"id": item_id, "status": "progress", "msg": f"{pkg} 설치 완료"})
                else:
                    yield _sse({"id": item_id, "status": "error", "msg": f"{pkg} 실패: {err[:400]}"})
                    ok = False
                    break
            if ok:
                yield _sse({"id": item_id, "status": "running", "msg": f"{name} 동작 테스트 중..."})
                verified, detail = _verify_action(action)
                if verified:
                    yield _sse({"id": item_id, "status": "done", "msg": f"{name} 설치 · 검증 완료 ✅\n{detail}"})
                else:
                    yield _sse({"id": item_id, "status": "running", "msg": f"검증 실패 — 자동 복구 중...\n{detail}"})
                    repaired, repair_msg = await _repair_action(action)
                    yield _sse({"id": item_id, "status": "done" if repaired else "error", "msg": repair_msg[:500]})

        elif atype == "brew":
            pkg = action.get("package", "")
            yield _sse({"id": item_id, "status": "running", "msg": f"brew install {pkg} ..."})
            success, err = await _brew_install(pkg)
            if success:
                yield _sse({"id": item_id, "status": "running", "msg": "설치 완료 감지 · PATH 보정 중..."})
                binary = action.get("binary")
                if binary:
                    repair_path_for(binary)
                verified, detail = _verify_action(action)
                if verified:
                    yield _sse({"id": item_id, "status": "done", "msg": f"{name} 설치 · 연결 · 검증 완료 ✅\n{detail}"})
                else:
                    yield _sse({"id": item_id, "status": "running", "msg": f"검증 실패 — 자동 복구 중...\n{detail}"})
                    repaired, repair_msg = await _repair_action(action)
                    yield _sse({"id": item_id, "status": "done" if repaired else "error", "msg": repair_msg[:500]})
            else:
                url = action.get("official_url") or action.get("url")
                if url:
                    yield _sse({"id": item_id, "status": "auth", "msg": f"자동 설치 실패 — 공식 다운로드 페이지를 엽니다.\n{err[:240]}", "auth_url": url})
                    open_url(url)
                yield _sse({"id": item_id, "status": "error", "msg": f"실패: {err[:400]}"})

        elif atype == "load_model":
            model_id = action.get("model_id", "")
            yield _sse({"id": item_id, "status": "running",
                        "msg": f"모델 다운로드 · 로딩 중...\n{model_id}\n(용량에 따라 수 분 소요)"})
            try:
                msg = await router.load_model(model_id)
                yield _sse({"id": item_id, "status": "done", "msg": f"{name} 로드 완료 ✅"})
            except Exception as e:
                yield _sse({"id": item_id, "status": "error", "msg": f"로드 실패: {str(e)[:400]}"})

        elif atype == "auth":
            url = action.get("url", "")
            yield _sse({"id": item_id, "status": "auth",
                        "msg": "브라우저에서 인증 페이지를 엽니다...", "auth_url": url})
            open_url(url)
            yield _sse({"id": item_id, "status": "waiting",
                        "msg": "브라우저에서 인증 완료 후 계속하세요"})

        elif atype == "url":
            url = action.get("url", "")
            yield _sse({"id": item_id, "status": "auth",
                        "msg": f"설치 페이지를 브라우저에서 엽니다...", "auth_url": url})
            open_url(url)
            binary = action.get("binary")
            if binary:
                yield _sse({"id": item_id, "status": "waiting",
                            "msg": f"{binary} 설치 완료를 자동 감지하는 중입니다..."})
                ok, detail = await _wait_for_binary(binary)
                if ok:
                    repair_path_for(binary)
                    yield _sse({"id": item_id, "status": "done",
                                "msg": f"{name} 설치 · PATH 연결 · 검증 완료 ✅\n{detail}"})
                else:
                    yield _sse({"id": item_id, "status": "error",
                                "msg": f"{detail}\n공식 페이지에서 설치 후 다시 시도하세요."})
            else:
                yield _sse({"id": item_id, "status": "waiting",
                            "msg": "브라우저에서 설치 또는 인증을 완료한 뒤 다시 시도하세요"})

        else:
            yield _sse({"id": item_id, "status": "error", "msg": f"알 수 없는 액션: {atype}"})

    yield _sse({"status": "complete", "msg": "모든 항목 처리 완료!"})


async def _pip_install(package: str) -> Tuple[bool, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "pip", "install", "--upgrade", package,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=600)
        if proc.returncode == 0:
            return True, ""
        return False, stderr.decode(errors="replace")
    except asyncio.TimeoutError:
        return False, "설치 시간 초과 (10분)"
    except Exception as e:
        return False, str(e)


async def _brew_install(package: str) -> Tuple[bool, str]:
    brew = shutil.which("brew")
    if not brew:
        return False, "Homebrew 미설치 — https://brew.sh 에서 설치하세요"
    try:
        proc = await asyncio.create_subprocess_exec(
            brew, "install", package,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
        if proc.returncode == 0:
            return True, ""
        return False, stderr.decode(errors="replace")
    except asyncio.TimeoutError:
        return False, "설치 시간 초과 (5분)"
    except Exception as e:
        return False, str(e)


if __name__ == "__main__":
    # Packaging entrypoint for legacy setuptools invocations used by `python -m build`.
    from setuptools import setup as _setuptools_setup
    _setuptools_setup()


def open_url(url: str) -> None:
    try:
        system = platform.system()
        if system == "Darwin":
            subprocess.Popen(["open", url])
        elif system == "Windows":
            subprocess.Popen(["start", "", url], shell=True)
        else:
            subprocess.Popen(["xdg-open", url])
    except Exception:
        pass
