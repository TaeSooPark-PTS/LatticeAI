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
    except ImportError:
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
    return {
        "os":           platform.system(),
        "os_version":   platform.mac_ver()[0] if platform.system() == "Darwin" else platform.version(),
        "chip":         chip,
        "ram_gb":       _detect_ram_gb(),
        "disk_free_gb": _detect_disk_free_gb(),
        "tools":        _detect_tools(),
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
                "action": {"type": "pip", "packages": ["mlx-lm", "mlx-vlm"]},
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
            "action": {"type": "brew", "package": "ollama"} if tools.get("brew") else {"type": "url", "url": "https://ollama.com/download"},
            "badge": hint,
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
                yield _sse({"id": item_id, "status": "done", "msg": f"{name} 설치 완료 ✅"})

        elif atype == "brew":
            pkg = action.get("package", "")
            yield _sse({"id": item_id, "status": "running", "msg": f"brew install {pkg} ..."})
            success, err = await _brew_install(pkg)
            if success:
                yield _sse({"id": item_id, "status": "done", "msg": f"{name} 설치 완료 ✅"})
            else:
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
            yield _sse({"id": item_id, "status": "waiting",
                        "msg": "설치 완료 후 서버를 재시작해 주세요"})

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
