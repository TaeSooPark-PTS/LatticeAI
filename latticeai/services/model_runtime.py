"""Model runtime and provider helpers for Lattice AI.

This module owns local/cloud model preparation, engine detection, model download,
provider-specific server startup, smoke tests, and runtime feature payloads. It is
configured by ``server_app`` with app-level state but has no FastAPI app import.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import os
import platform
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import AsyncIterator, Dict, List, Optional

from fastapi import HTTPException, Request

from latticeai.models.router import (
    AsyncOpenAI,
    HF_MODELS_ROOT,
    OPENAI_COMPATIBLE_PROVIDERS,
    ensure_mlx_runtime,
    hf_cache_model_dir,
    hf_model_dir,
    parse_model_ref,
)
from latticeai.core.model_compat import (
    SMOKE_PROMPT as _SMOKE_PROMPT,
    classify_smoke_response as _classify_smoke_response,
    ensure_profile as _ensure_compat_profile,
    fast_postprocess as _compat_fast_postprocess,
    friendly_model_runtime_error as _friendly_model_runtime_error,
    model_runtime_compatibility as _model_runtime_compatibility,
    record_smoke_result as _record_smoke_result,
)
from latticeai.core.model_resolution import ModelResolution as _ModelResolution
from .model_engines import (
    ensure_lmstudio_server as _ensure_lmstudio_server,
    ensure_ollama_server as _ensure_ollama_server,
    ensure_vllm_server as _ensure_vllm_server,
    ensure_llamacpp_server as _ensure_llamacpp_server,
    engine_support_status as _engine_support_status,
    install_engine as _install_engine,
)

# Rebind extracted engines for legacy module globals
ensure_lmstudio_server = _ensure_lmstudio_server
ensure_ollama_server = _ensure_ollama_server
ensure_vllm_server = _ensure_vllm_server
ensure_llamacpp_server = _ensure_llamacpp_server
engine_support_status = _engine_support_status
install_engine = _install_engine

# Configured by server_app.configure_model_runtime during app assembly.
router = None
APP_MODE = "local"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 4825
DATA_DIR = Path.home() / ".latticeai"
BASE_DIR = Path.cwd()
ENABLE_TELEGRAM = False
ENABLE_GRAPH = True
AUTOLOAD_MODELS = False
MODEL_IDLE_UNLOAD_SECONDS = 0
ALLOW_LOCAL_MODELS = True
REQUIRE_AUTH = False
INVITE_GATE_ENABLED = False
ALLOW_PLAINTEXT_API_KEYS = False
CORS_ALLOW_NETWORK = False
PUBLIC_MODEL = "openai:gpt-4o-mini"
LOCAL_MODEL = "mlx-community/gemma-4-12b-it-4bit"
IS_PUBLIC_MODE = False
keyring = None


def _env_bool(key: str, default: bool = False) -> bool:
    raw = os.getenv(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _download_allowed(allow_download: bool = False) -> bool:
    return bool(allow_download) or _env_bool("LATTICEAI_ALLOW_MODEL_DOWNLOADS", default=False) or bool(AUTOLOAD_MODELS)


def _download_block(provider: str, model_name: str) -> None:
    raise HTTPException(
        status_code=409,
        detail={
            "status": "unavailable",
            "capability": "model_download",
            "provider": provider,
            "model": model_name,
            "reason": (
                "Model files are not present locally. Lattice AI does not start "
                "outbound model downloads by default, and token/model presence "
                "alone never authorizes network activity."
            ),
            "action": "Use the explicit pull/prepare flow with download consent, or set LATTICEAI_ALLOW_MODEL_DOWNLOADS=true.",
        },
    )


def _engine_install_block(engine: str) -> None:
    raise HTTPException(
        status_code=409,
        detail={
            "status": "unavailable",
            "capability": "engine_install",
            "engine": engine,
            "reason": (
                "The requested local runtime is not installed. Lattice AI does not "
                "run package-manager or installer commands from Model Load by default."
            ),
            "action": "Install the runtime explicitly from Library/System setup, or enable explicit download/install consent for this request.",
        },
    )


def _missing_current_user(_request: Request) -> Optional[str]:
    return None


def _missing_user_api_key(_email: Optional[str], _provider: str) -> Optional[str]:
    return None


get_current_user = _missing_current_user
get_user_api_key = _missing_user_api_key


def configure_model_runtime(**deps) -> None:
    """Wire app-owned runtime dependencies without importing server_app.

    Explicit per-key assignment (no blanket globals().update) so wiring is
    auditable and side effects are visible. Preserves exact public module
    globals and prior behavior for all callers and shims.
    """
    global router, APP_MODE, DEFAULT_HOST, DEFAULT_PORT, DATA_DIR, BASE_DIR
    global ENABLE_TELEGRAM, ENABLE_GRAPH, AUTOLOAD_MODELS, MODEL_IDLE_UNLOAD_SECONDS
    global ALLOW_LOCAL_MODELS, REQUIRE_AUTH, INVITE_GATE_ENABLED, ALLOW_PLAINTEXT_API_KEYS
    global CORS_ALLOW_NETWORK, PUBLIC_MODEL, LOCAL_MODEL, IS_PUBLIC_MODE
    global keyring, get_current_user, get_user_api_key

    router = deps.get("router", router)
    APP_MODE = deps.get("APP_MODE", APP_MODE)
    DEFAULT_HOST = deps.get("DEFAULT_HOST", DEFAULT_HOST)
    DEFAULT_PORT = deps.get("DEFAULT_PORT", DEFAULT_PORT)
    DATA_DIR = deps.get("DATA_DIR", DATA_DIR)
    BASE_DIR = deps.get("BASE_DIR", BASE_DIR)
    ENABLE_TELEGRAM = deps.get("ENABLE_TELEGRAM", ENABLE_TELEGRAM)
    ENABLE_GRAPH = deps.get("ENABLE_GRAPH", ENABLE_GRAPH)
    AUTOLOAD_MODELS = deps.get("AUTOLOAD_MODELS", AUTOLOAD_MODELS)
    MODEL_IDLE_UNLOAD_SECONDS = deps.get("MODEL_IDLE_UNLOAD_SECONDS", MODEL_IDLE_UNLOAD_SECONDS)
    ALLOW_LOCAL_MODELS = deps.get("ALLOW_LOCAL_MODELS", ALLOW_LOCAL_MODELS)
    REQUIRE_AUTH = deps.get("REQUIRE_AUTH", REQUIRE_AUTH)
    INVITE_GATE_ENABLED = deps.get("INVITE_GATE_ENABLED", INVITE_GATE_ENABLED)
    ALLOW_PLAINTEXT_API_KEYS = deps.get("ALLOW_PLAINTEXT_API_KEYS", ALLOW_PLAINTEXT_API_KEYS)
    CORS_ALLOW_NETWORK = deps.get("CORS_ALLOW_NETWORK", CORS_ALLOW_NETWORK)
    PUBLIC_MODEL = deps.get("PUBLIC_MODEL", PUBLIC_MODEL)
    LOCAL_MODEL = deps.get("LOCAL_MODEL", LOCAL_MODEL)
    IS_PUBLIC_MODE = deps.get("IS_PUBLIC_MODE", IS_PUBLIC_MODE)
    if "keyring" in deps:
        keyring = deps["keyring"]
    if "get_current_user" in deps:
        get_current_user = deps["get_current_user"]
    if "get_user_api_key" in deps:
        get_user_api_key = deps["get_user_api_key"]


# Catalog data + version-dedup helpers live in ``model_catalog``; re-exported
# here so existing ``from ...model_runtime import ENGINE_MODEL_CATALOG`` imports
# keep working.
from latticeai.services.model_catalog import (  # noqa: E402, F401 (re-export after the module globals it documents)
    ENGINE_INSTALLERS,
    ENGINE_MODEL_CATALOG,
    MODEL_ENGINE_ALIASES,
    _VERSIONED_MODEL_PATTERNS,
    _model_family_version,
    _version_tuple,
    filter_lower_family_versions,
)

def _update_env_file(env_file: Path, key: str, value: str) -> None:
    lines = []
    found = False
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{key}="):
                lines.append(f"{key}={value}")
                found = True
            else:
                lines.append(line)
    if not found:
        lines.append(f"{key}={value}")
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


LOCAL_SERVER_PROCESSES: Dict[str, subprocess.Popen] = {}
VLLM_METAL_ENV = Path.home() / ".venv-vllm-metal"
VLLM_METAL_BIN = VLLM_METAL_ENV / "bin" / "vllm"
VLLM_METAL_PYTHON = VLLM_METAL_ENV / "bin" / "python"
LMSTUDIO_BUNDLED_CLI = Path("/Applications/LM Studio.app/Contents/Resources/app/.webpack/lms")

def windows_binary_candidates(binary: str) -> List[Path]:
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    program_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    candidates = {
        "ollama": [
            Path(local_appdata) / "Programs" / "Ollama" / "ollama.exe" if local_appdata else None,
            Path(program_files) / "Ollama" / "ollama.exe",
        ],
        "lms": [
            Path(local_appdata) / "Programs" / "LM Studio" / "resources" / "app" / ".webpack" / "lms.exe" if local_appdata else None,
            Path(program_files) / "LM Studio" / "resources" / "app" / ".webpack" / "lms.exe",
        ],
        "nvidia-smi": [
            Path(program_files) / "NVIDIA Corporation" / "NVSMI" / "nvidia-smi.exe",
            Path(program_files_x86) / "NVIDIA Corporation" / "NVSMI" / "nvidia-smi.exe",
        ],
    }
    return [item for item in candidates.get(binary, []) if item is not None]


def local_binary(binary: str) -> Optional[str]:
    found = shutil.which(binary)
    if found:
        return found
    if platform.system() == "Windows":
        for candidate in windows_binary_candidates(binary):
            if candidate.exists():
                return str(candidate)
    return None


def find_lmstudio_cli() -> Optional[str]:
    cli = local_binary("lms")
    if cli:
        return cli
    if LMSTUDIO_BUNDLED_CLI.exists():
        return str(LMSTUDIO_BUNDLED_CLI)
    return None


def vllm_executable() -> Optional[str]:
    found = shutil.which("vllm")
    if found:
        return found
    if VLLM_METAL_BIN.exists():
        return str(VLLM_METAL_BIN)
    return None


def vllm_metal_python() -> Optional[str]:
    if VLLM_METAL_PYTHON.exists():
        return str(VLLM_METAL_PYTHON)
    return None


def _json_request(
    url: str,
    *,
    method: str = "GET",
    payload: Optional[Dict[str, object]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 10.0,
) -> Dict[str, object]:
    data = None
    req_headers = dict(headers or {})
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as res:
        raw = res.read().decode("utf-8", errors="replace")
    if not raw.strip():
        return {}
    return json.loads(raw)


def lmstudio_api_base() -> str:
    return (os.getenv("LMSTUDIO_BASE_URL") or OPENAI_COMPATIBLE_PROVIDERS["lmstudio"]["base_url"]).rstrip("/")


def lmstudio_native_api_base() -> str:
    base = lmstudio_api_base()
    return base[:-3] if base.endswith("/v1") else base


def ensure_lmstudio_server() -> None:
    base_url = lmstudio_native_api_base()
    try:
        _json_request(f"{base_url}/api/v1/models", headers={"Authorization": "Bearer lmstudio"}, timeout=2.5)
        return
    except Exception:
        pass

    cli = find_lmstudio_cli()
    if not cli:
        raise HTTPException(status_code=400, detail="LM Studio CLI를 찾지 못했습니다. LM Studio를 설치한 뒤 다시 시도하세요.")

    try:
        subprocess.Popen(
            [cli, "server", "start"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LM Studio 서버 시작 실패: {e}")

    deadline = time.time() + 45
    while time.time() < deadline:
        try:
            _json_request(f"{base_url}/api/v1/models", headers={"Authorization": "Bearer lmstudio"}, timeout=2.5)
            return
        except Exception:
            time.sleep(1)
    raise HTTPException(status_code=500, detail="LM Studio Local Server를 자동으로 시작하지 못했습니다.")


_LMSTUDIO_MODELS_CACHE: List[Dict[str, object]] = []
_LMSTUDIO_MODELS_CACHE_TS: float = 0.0
_LMSTUDIO_MODELS_CACHE_TTL: float = 10.0


def get_lmstudio_models(*, force: bool = False) -> List[Dict[str, object]]:
    global _LMSTUDIO_MODELS_CACHE, _LMSTUDIO_MODELS_CACHE_TS
    if not force and time.monotonic() - _LMSTUDIO_MODELS_CACHE_TS < _LMSTUDIO_MODELS_CACHE_TTL:
        return _LMSTUDIO_MODELS_CACHE
    try:
        payload = _json_request(
            f"{lmstudio_native_api_base()}/api/v1/models",
            headers={"Authorization": f"Bearer {os.getenv('LMSTUDIO_API_KEY') or 'lmstudio'}"},
            timeout=2.5,
        )
    except Exception:
        return _LMSTUDIO_MODELS_CACHE
    models = payload.get("models")
    _LMSTUDIO_MODELS_CACHE = models if isinstance(models, list) else []
    _LMSTUDIO_MODELS_CACHE_TS = time.monotonic()
    return _LMSTUDIO_MODELS_CACHE


def _lmstudio_candidate_keys(model_name: str) -> List[str]:
    raw = model_name.strip()
    if not raw:
        return []
    slug = raw.split("/")[-1].lower()
    slug = slug.replace("-gguf", "").replace("-awq", "")
    parts = [p for p in slug.split("-") if p]
    candidates = [raw.lower(), slug]
    if parts:
        candidates.append("-".join(parts[: min(4, len(parts))]))
    return list(dict.fromkeys(candidates))


def _find_lmstudio_model_key(model_name: str, models: List[Dict[str, object]]) -> Optional[str]:
    if not models:
        return None
    candidate_keys = _lmstudio_candidate_keys(model_name)
    exact = []
    fuzzy = []
    for item in models:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        display_name = str(item.get("display_name") or "").strip()
        haystacks = [key.lower(), display_name.lower()]
        if any(raw == key.lower() for raw in candidate_keys):
            exact.append(key)
            continue
        if any(token and token in hay for token in candidate_keys for hay in haystacks):
            fuzzy.append(key)
    return (exact or fuzzy or [None])[0]


def ensure_lmstudio_model(model_name: str) -> Dict[str, object]:
    ensure_lmstudio_server()
    auth_header = {"Authorization": f"Bearer {os.getenv('LMSTUDIO_API_KEY') or 'lmstudio'}"}
    models = get_lmstudio_models()
    found_key = _find_lmstudio_model_key(model_name, models)
    model_key = found_key or model_name

    if not found_key:
        try:
            job = _json_request(
                f"{lmstudio_native_api_base()}/api/v1/models/download",
                method="POST",
                payload={"model": model_name},
                headers=auth_header,
                timeout=30,
            )
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")[-2000:]
            raise HTTPException(status_code=500, detail=f"LM Studio 모델 다운로드 실패: {detail or e.reason}")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"LM Studio 모델 다운로드 실패: {e}")

        status = str(job.get("status") or "")
        job_id = str(job.get("job_id") or "")
        if status not in {"completed", "already_downloaded"} and job_id:
            deadline = time.time() + 3600
            while time.time() < deadline:
                polled = _json_request(
                    f"{lmstudio_native_api_base()}/api/v1/models/download/status/{job_id}",
                    headers=auth_header,
                    timeout=30,
                )
                polled_status = str(polled.get("status") or "")
                if polled_status == "completed":
                    break
                if polled_status == "failed":
                    raise HTTPException(status_code=500, detail=f"LM Studio 모델 다운로드 실패: {polled}")
                time.sleep(2)
            else:
                raise HTTPException(status_code=408, detail="LM Studio 모델 다운로드 시간이 초과되었습니다.")

        models = get_lmstudio_models(force=True)
        model_key = _find_lmstudio_model_key(model_name, models) or model_name

    target = next((item for item in models if isinstance(item, dict) and item.get("key") == model_key), None)
    loaded_instances = target.get("loaded_instances") if isinstance(target, dict) else None
    if loaded_instances:
        return {"provider": "lmstudio", "model": model_name, "resolved_model": model_key, "server_ready": True, "cached": True}

    try:
        loaded = _json_request(
            f"{lmstudio_native_api_base()}/api/v1/models/load",
            method="POST",
            payload={"model": model_key, "context_length": 4096},
            headers=auth_header,
            timeout=120,
        )
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[-2000:]
        raise HTTPException(status_code=500, detail=f"LM Studio 모델 로드 실패: {detail or e.reason}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LM Studio 모델 로드 실패: {e}")

    if str(loaded.get("status") or "") != "loaded":
        raise HTTPException(status_code=500, detail=f"LM Studio 모델 로드 실패: {loaded}")

    return {
        "provider": "lmstudio",
        "model": model_name,
        "resolved_model": model_key,
        "instance_id": loaded.get("instance_id"),
        "server_ready": True,
        "cached": False,
    }

def engine_support_status(engine: str) -> Dict[str, object]:
    if engine != "vllm":
        return {"supported": True, "reason": None}
    is_apple_silicon = sys.platform == "darwin" and platform.machine() == "arm64"
    if sys.platform.startswith("win"):
        return {"supported": False, "reason": "vLLM은 Windows native 자동 설치보다 WSL2/Linux 환경을 권장합니다."}
    if sys.platform == "darwin" and not is_apple_silicon:
        return {"supported": False, "reason": "vLLM Metal 자동 설치는 Apple Silicon macOS에서만 지원됩니다."}
    if sys.version_info >= (3, 13) and is_apple_silicon:
        return {"supported": True, "reason": "현재 환경에서는 vLLM Metal 전용 런타임으로 설치합니다."}
    if sys.version_info >= (3, 13):
        return {"supported": False, "reason": "vLLM 설치는 현재 Python 3.13 이하 또는 별도 전용 런타임이 필요합니다."}
    return {"supported": True, "reason": None}

def hf_model_ready(repo_id: str, provider: str = "local_mlx") -> bool:
    model_dir = hf_model_dir(repo_id)
    if provider in {"local_mlx", "vllm"} and (not model_dir.exists() or not model_dir.is_dir()):
        hf_cache_repo = Path.home() / ".cache" / "huggingface" / "hub" / f"models--{repo_id.replace('/', '--')}"
        if hf_cache_repo.exists() and any(hf_cache_repo.glob("snapshots/*")):
            if provider == "vllm":
                return True
            return hf_cache_model_dir(repo_id) is not None
        return False
    if not model_dir.exists() or not model_dir.is_dir():
        return False
    if provider == "llamacpp":
        return any(model_dir.rglob("*.gguf"))
    has_config = (model_dir / "config.json").exists()
    has_weights = any(model_dir.glob("*.safetensors")) or any(model_dir.glob("*.bin"))
    has_tokenizer = (
        (model_dir / "tokenizer.json").exists()
        or (model_dir / "tokenizer.model").exists()
        or (model_dir / "tokenizer_config.json").exists()
    )
    return has_config and has_weights and has_tokenizer


def model_download_progress_payload(
    stage: str,
    message: str,
    *,
    percent: Optional[float] = None,
    detail: Optional[str] = None,
    downloaded_bytes: Optional[int] = None,
    total_bytes: Optional[int] = None,
    eta_seconds: Optional[float] = None,
    file: Optional[str] = None,
    indeterminate: bool = False,
) -> Dict[str, object]:
    payload: Dict[str, object] = {
        "stage": stage,
        "message": message,
        "indeterminate": indeterminate,
        "ts": time.time(),
    }
    if percent is not None:
        payload["percent"] = max(0, min(100, round(float(percent), 1)))
    if detail:
        payload["detail"] = detail
    if downloaded_bytes is not None:
        payload["downloaded_bytes"] = max(0, int(downloaded_bytes))
    if total_bytes is not None:
        payload["total_bytes"] = max(0, int(total_bytes))
    if eta_seconds is not None:
        payload["eta_seconds"] = max(0, round(float(eta_seconds)))
    if file:
        payload["file"] = file
    return payload


def estimate_eta_seconds(started_at: float, percent: Optional[float]) -> Optional[float]:
    if percent is None or percent <= 0 or percent >= 100:
        return None
    elapsed = max(0.0, time.time() - started_at)
    return elapsed * (100.0 - percent) / percent


def hf_repo_files_with_sizes(repo_id: str) -> List[Dict[str, object]]:
    from huggingface_hub import HfApi

    api = HfApi()
    try:
        info = api.model_info(repo_id, files_metadata=True)
        files = []
        for sibling in getattr(info, "siblings", []) or []:
            name = str(getattr(sibling, "rfilename", "") or "").strip()
            if not name or name.endswith("/"):
                continue
            files.append({"name": name, "size": int(getattr(sibling, "size", 0) or 0)})
        if files:
            return files
    except TypeError:
        pass
    except Exception as e:
        logging.warning("huggingface model_info failed for %s: %s", repo_id, e)

    return [{"name": str(name), "size": 0} for name in api.list_repo_files(repo_id) if str(name).strip()]


def download_hf_model(
    repo_id: str,
    provider: str = "local_mlx",
    progress_emit=None,
) -> Dict[str, object]:
    if importlib.util.find_spec("huggingface_hub") is None:
        raise HTTPException(status_code=400, detail="huggingface_hub가 없습니다. 먼저 MLX runtime 설치를 진행해 주세요.")

    target_dir = hf_model_dir(repo_id)
    if hf_model_ready(repo_id, provider):
        cached_dir = hf_cache_model_dir(repo_id) if provider == "local_mlx" else None
        resolved_dir = cached_dir or target_dir
        if progress_emit:
            progress_emit(model_download_progress_payload(
                "download",
                "이미 다운로드된 모델을 확인했습니다.",
                percent=100,
                downloaded_bytes=0,
                total_bytes=0,
                eta_seconds=0,
            ))
        return {"model": repo_id, "path": str(resolved_dir), "cached": True}

    target_dir.mkdir(parents=True, exist_ok=True)
    try:
        from huggingface_hub import hf_hub_download

        started_at = time.time()
        all_files = hf_repo_files_with_sizes(repo_id)
        if provider == "llamacpp":
            ggufs = sorted(
                [item for item in all_files if str(item["name"]).lower().endswith(".gguf")],
                key=lambda item: str(item["name"]),
            )
            if not ggufs:
                raise RuntimeError("GGUF 파일을 찾지 못했습니다.")
            preference = ("q4_k_m", "q4_0", "q4_k_s", "q3_k_m", "q2_k")
            selected_files = [
                next(
                    (item for pref in preference for item in ggufs if pref in str(item["name"]).lower()),
                    ggufs[0],
                )
            ]
        else:
            selected_files = all_files

        total_bytes = sum(int(item.get("size") or 0) for item in selected_files) or None
        downloaded_bytes = 0
        total_files = max(1, len(selected_files))
        if progress_emit:
            progress_emit(model_download_progress_payload(
                "download",
                "모델 파일 정보를 확인했습니다.",
                percent=0,
                downloaded_bytes=0,
                total_bytes=total_bytes,
                indeterminate=total_bytes is None,
            ))

        for index, item in enumerate(selected_files, start=1):
            filename = str(item["name"])
            size = int(item.get("size") or 0)
            tqdm_class = None
            if progress_emit:
                current_percent = (
                    (downloaded_bytes / total_bytes) * 100 if total_bytes else ((index - 1) / total_files) * 100
                )
                progress_emit(model_download_progress_payload(
                    "download",
                    "모델 다운로드 중입니다.",
                    percent=current_percent,
                    detail=filename,
                    downloaded_bytes=downloaded_bytes,
                    total_bytes=total_bytes,
                    eta_seconds=estimate_eta_seconds(started_at, current_percent),
                    file=filename,
                    indeterminate=total_bytes is None and total_files <= 1,
                ))
                try:
                    from tqdm.auto import tqdm as base_tqdm

                    downloaded_before = downloaded_bytes
                    last_emit = {"at": 0.0, "percent": -1.0}

                    def emit_byte_progress(done_bytes: float) -> None:
                        done = max(0, int(done_bytes or 0))
                        if total_bytes:
                            aggregate = min(total_bytes, downloaded_before + done)
                            percent = (aggregate / total_bytes) * 100
                        else:
                            file_total = size or done
                            file_ratio = min(1.0, done / file_total) if file_total else 0.0
                            aggregate = downloaded_before + done
                            percent = ((index - 1) + file_ratio) / total_files * 100
                        now = time.time()
                        if percent < 100 and now - last_emit["at"] < 0.5 and percent - last_emit["percent"] < 0.3:
                            return
                        last_emit["at"] = now
                        last_emit["percent"] = percent
                        progress_emit(model_download_progress_payload(
                            "download",
                            "모델 다운로드 중입니다.",
                            percent=percent,
                            detail=filename,
                            downloaded_bytes=aggregate,
                            total_bytes=total_bytes,
                            eta_seconds=estimate_eta_seconds(started_at, percent),
                            file=filename,
                            indeterminate=total_bytes is None and total_files <= 1,
                        ))

                    class ProgressTqdm(base_tqdm):
                        def update(self, n=1):
                            result = super().update(n)
                            emit_byte_progress(float(getattr(self, "n", 0) or 0))
                            return result

                    tqdm_class = ProgressTqdm
                except Exception:
                    tqdm_class = None
            local_path = hf_hub_download(
                repo_id=repo_id,
                filename=filename,
                local_dir=str(target_dir),
                tqdm_class=tqdm_class,
            )
            if size <= 0:
                try:
                    size = Path(local_path).stat().st_size
                except OSError:
                    size = 0
            downloaded_bytes += size
            if progress_emit:
                current_percent = (
                    (downloaded_bytes / total_bytes) * 100 if total_bytes else (index / total_files) * 100
                )
                progress_emit(model_download_progress_payload(
                    "download",
                    "모델 다운로드 중입니다.",
                    percent=current_percent,
                    detail=filename,
                    downloaded_bytes=downloaded_bytes,
                    total_bytes=total_bytes,
                    eta_seconds=estimate_eta_seconds(started_at, current_percent),
                    file=filename,
                    indeterminate=False,
                ))

        if progress_emit:
            progress_emit(model_download_progress_payload(
                "download",
                "모델 다운로드가 완료되었습니다.",
                percent=100,
                downloaded_bytes=downloaded_bytes,
                total_bytes=total_bytes or downloaded_bytes,
                eta_seconds=0,
            ))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"{repo_id} 다운로드 실패: {str(e)[-2000:]}")

    if not hf_model_ready(repo_id, provider):
        raise HTTPException(status_code=500, detail=f"{repo_id} 다운로드가 완료되지 않았습니다. 모델 파일을 찾지 못했습니다.")

    return {"model": repo_id, "path": str(target_dir), "cached": False}


def pull_ollama_model_with_progress(model_name: str, progress_emit=None) -> Dict[str, object]:
    ollama = local_binary("ollama")
    if not ollama:
        raise HTTPException(status_code=400, detail="Ollama가 설치되지 않았습니다.")
    started_at = time.time()
    if progress_emit:
        progress_emit(model_download_progress_payload(
            "download",
            "Ollama 모델 다운로드를 시작합니다.",
            percent=0,
            detail=model_name,
            indeterminate=True,
        ))
    process = subprocess.Popen(
        [ollama, "pull", model_name],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    last_percent: Optional[float] = None
    lines: List[str] = []
    try:
        assert process.stdout is not None
        for raw_line in process.stdout:
            for part in re.split(r"[\r\n]+", raw_line):
                line = part.strip()
                if not line:
                    continue
                lines.append(line)
                match = re.search(r"(\d{1,3}(?:\.\d+)?)\s*%", line)
                if match:
                    last_percent = min(100.0, float(match.group(1)))
                    if progress_emit:
                        progress_emit(model_download_progress_payload(
                            "download",
                            "Ollama 모델 다운로드 중입니다.",
                            percent=last_percent,
                            detail=line[-180:],
                            eta_seconds=estimate_eta_seconds(started_at, last_percent),
                            indeterminate=False,
                        ))
                elif progress_emit:
                    progress_emit(model_download_progress_payload(
                        "download",
                        "Ollama 모델 다운로드 중입니다.",
                        percent=last_percent,
                        detail=line[-180:],
                        eta_seconds=estimate_eta_seconds(started_at, last_percent),
                        indeterminate=last_percent is None,
                    ))
        returncode = process.wait()
    except Exception:
        process.kill()
        raise

    if returncode != 0:
        tail = "\n".join(lines[-12:])
        raise HTTPException(status_code=500, detail=tail[-2000:] or "Ollama 모델 다운로드 실패")

    if progress_emit:
        progress_emit(model_download_progress_payload(
            "download",
            "Ollama 모델 다운로드가 완료되었습니다.",
            percent=100,
            detail=model_name,
            eta_seconds=0,
            indeterminate=False,
        ))
    return {"provider": "ollama", "model": model_name, "returncode": returncode}


def get_ollama_pulled_models() -> set:
    ollama = local_binary("ollama")
    if not ollama:
        return set()
    try:
        result = subprocess.run([ollama, "list"], capture_output=True, text=True, timeout=5, check=False)
        pulled = set()
        for line in result.stdout.splitlines()[1:]:
            parts = line.split()
            if parts:
                pulled.add(parts[0])
        return pulled
    except Exception:
        return set()


def get_openai_compatible_server_models(provider: str) -> List[str]:
    if provider == "lmstudio":
        models = []
        for item in get_lmstudio_models():
            if not isinstance(item, dict):
                continue
            key = str(item.get("key") or "").strip()
            loaded_instances = item.get("loaded_instances") or []
            if loaded_instances:
                instance_ids = [
                    str(instance.get("id") or "").strip()
                    for instance in loaded_instances
                    if isinstance(instance, dict) and instance.get("id")
                ]
                models.extend(instance_ids or ([key] if key else []))
        return list(dict.fromkeys([model for model in models if model]))

    config = OPENAI_COMPATIBLE_PROVIDERS.get(provider) or {}
    base_url = os.getenv(config.get("base_url_env", "")) if config.get("base_url_env") else None
    base_url = (base_url or config.get("base_url") or "").rstrip("/")
    if not base_url:
        return []

    api_key = os.getenv(config.get("env_key", "")) or config.get("api_key_fallback") or provider
    req = urllib.request.Request(
        f"{base_url}/models",
        headers={"Authorization": f"Bearer {api_key}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=2.5) as res:
            payload = json.loads(res.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return []

    models = []
    for item in payload.get("data") or []:
        model_id = item.get("id") if isinstance(item, dict) else None
        if model_id:
            models.append(str(model_id))
    return models


def ensure_ollama_server() -> None:
    ollama = local_binary("ollama")
    if not ollama:
        raise HTTPException(status_code=400, detail="Ollama가 설치되지 않았습니다.")
    try:
        probe = subprocess.run([ollama, "list"], capture_output=True, text=True, timeout=3, check=False)
        if probe.returncode == 0:
            return
    except Exception:
        pass
    subprocess.Popen(
        [ollama, "serve"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            probe = subprocess.run([ollama, "list"], capture_output=True, text=True, timeout=3, check=False)
            if probe.returncode == 0:
                return
        except Exception:
            pass
        time.sleep(0.5)
    raise HTTPException(status_code=500, detail="Ollama 서버를 자동으로 시작하지 못했습니다.")


def wait_for_openai_compatible_server(provider: str, model_name: Optional[str] = None, timeout: int = 45) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        models = get_openai_compatible_server_models(provider)
        if models and (not model_name or model_name in models):
            return True
        time.sleep(1)
    return False


def ensure_vllm_server(model_name: str) -> None:
    served_models = get_openai_compatible_server_models("vllm")
    if model_name in served_models:
        return
    vllm_bin = vllm_executable()
    vllm_metal_py = vllm_metal_python()
    if not vllm_bin and not vllm_metal_py and importlib.util.find_spec("vllm") is None:
        raise HTTPException(status_code=400, detail="vLLM runtime이 설치되지 않았습니다.")

    local_dir = hf_model_dir(model_name)
    if not vllm_metal_py and not hf_model_ready(model_name, "vllm"):
        download_hf_model(model_name, "vllm")

    running = LOCAL_SERVER_PROCESSES.get("vllm")
    if running and running.poll() is None:
        running.terminate()
        try:
            running.wait(timeout=10)
        except subprocess.TimeoutExpired:
            running.kill()
    elif served_models:
        raise HTTPException(status_code=409, detail="다른 vLLM 서버가 이미 실행 중입니다. 현재 서버를 종료한 뒤 다시 시도하세요.")

    running = LOCAL_SERVER_PROCESSES.get("vllm")
    if running and running.poll() is None:
        return

    _host_args = ["--host", "127.0.0.1", "--port", "8000"]
    if vllm_metal_py:
        command = [vllm_metal_py, "-m", "vllm_metal.server", "--model", model_name, *_host_args]
    elif vllm_bin:
        command = [vllm_bin, "serve", str(local_dir), "--served-model-name", model_name, *_host_args]
    else:
        command = [sys.executable, "-m", "vllm.entrypoints.openai.api_server", "--model", str(local_dir), "--served-model-name", model_name, *_host_args]
    LOCAL_SERVER_PROCESSES["vllm"] = subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    if not wait_for_openai_compatible_server("vllm", model_name, timeout=90):
        raise HTTPException(status_code=500, detail="vLLM 서버가 모델을 자동 로드하지 못했습니다.")


def ensure_llamacpp_server(model_name: str) -> None:
    served_models = get_openai_compatible_server_models("llamacpp")
    if model_name in served_models:
        return
    running = LOCAL_SERVER_PROCESSES.get("llamacpp")
    if running and running.poll() is None:
        running.terminate()
        try:
            running.wait(timeout=10)
        except subprocess.TimeoutExpired:
            running.kill()
    elif served_models:
        raise HTTPException(status_code=409, detail="다른 llama.cpp 서버가 이미 실행 중입니다. 현재 서버를 종료한 뒤 다시 시도하세요.")
    if not shutil.which("llama-server"):
        raise HTTPException(status_code=400, detail="llama.cpp가 설치되지 않았습니다.")
    if not hf_model_ready(model_name, "llamacpp"):
        download_hf_model(model_name, "llamacpp")

    gguf_files = sorted(hf_model_dir(model_name).rglob("*.gguf"))
    if not gguf_files:
        raise HTTPException(status_code=500, detail="다운로드된 GGUF 파일을 찾지 못했습니다.")

    preferred = next((p for p in gguf_files if "q4_k_m" in p.name.lower()), None)
    model_file = preferred or gguf_files[0]
    LOCAL_SERVER_PROCESSES["llamacpp"] = subprocess.Popen(
        [
            "llama-server",
            "-m",
            str(model_file),
            "--alias",
            model_name,
            "--host",
            "127.0.0.1",
            "--port",
            "8080",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    if not wait_for_openai_compatible_server("llamacpp", model_name, timeout=45):
        raise HTTPException(status_code=500, detail="llama.cpp 서버가 모델을 자동 로드하지 못했습니다.")


def engine_installed(engine: str) -> bool:
    if engine == "local_mlx":
        return bool(
            importlib.util.find_spec("mlx")
            and (importlib.util.find_spec("mlx_vlm") or importlib.util.find_spec("mlx_lm"))
        )
    if engine == "ollama":
        return local_binary("ollama") is not None
    if engine == "vllm":
        return vllm_metal_python() is not None or vllm_executable() is not None or importlib.util.find_spec("vllm") is not None
    if engine == "lmstudio":
        return find_lmstudio_cli() is not None or Path("/Applications/LM Studio.app").exists()
    if engine == "llamacpp":
        return shutil.which("llama-server") is not None
    if engine in {"openai", "openrouter", "groq", "together", "xai"}:
        return AsyncOpenAI is not None
    return False

def engine_status() -> List[Dict]:
    cloud_models = router.detected_cloud_models()
    cloud_by_provider = {}
    for model in cloud_models:
        cloud_by_provider.setdefault(model["provider"], []).append(model)

    ollama_installed = engine_installed("ollama")
    pulled = get_ollama_pulled_models() if ollama_installed else set()
    ollama_models = []
    for m in ENGINE_MODEL_CATALOG["ollama"]:
        pull_name = m["id"].removeprefix("ollama:")
        ollama_models.append({**m, "pulled": pull_name in pulled})
    ollama_models = filter_lower_family_versions(ollama_models)

    HF_MODELS_ROOT.mkdir(parents=True, exist_ok=True)
    mlx_models = []
    for m in ENGINE_MODEL_CATALOG.get("local_mlx", []):
        repo_id = m["id"]
        mlx_models.append({**m, "pulled": hf_model_ready(repo_id, "local_mlx")})
    mlx_models = filter_lower_family_versions(mlx_models)

    vllm_models = []
    for m in ENGINE_MODEL_CATALOG.get("vllm", []):
        repo_id = m["id"].removeprefix("vllm:")
        vllm_models.append({**m, "pulled": hf_model_ready(repo_id, "vllm")})
    vllm_models = filter_lower_family_versions(vllm_models)

    lmstudio_models = []
    downloaded_lmstudio = get_lmstudio_models()
    downloaded_by_key = {}
    for item in downloaded_lmstudio:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        if not key:
            continue
        downloaded_by_key[key] = item
        loaded_instances = item.get("loaded_instances") or []
        lmstudio_models.append({
            "id": f"lmstudio:{key}",
            "name": item.get("display_name") or f"LM Studio · {key}",
            "family": item.get("architecture") or item.get("publisher") or "LM Studio",
            "tag": "loaded-server-model" if loaded_instances else "downloaded",
            "size": item.get("params_string") or item.get("format") or "LM Studio",
            "pullable": True,
            "pulled": True,
        })

    if not lmstudio_models:
        for m in ENGINE_MODEL_CATALOG.get("lmstudio", []):
            lmstudio_models.append({**m, "pulled": False})
    else:
        known_ids = {item["id"] for item in lmstudio_models}
        for m in ENGINE_MODEL_CATALOG.get("lmstudio", []):
            repo_id = m["id"].removeprefix("lmstudio:")
            if f"lmstudio:{repo_id}" not in known_ids and repo_id not in downloaded_by_key:
                lmstudio_models.append({**m, "pulled": False})
    lmstudio_models = filter_lower_family_versions(lmstudio_models)

    llamacpp_models = []
    for m in ENGINE_MODEL_CATALOG.get("llamacpp", []):
        repo_id = m["id"].removeprefix("llamacpp:")
        llamacpp_models.append({**m, "pulled": hf_model_ready(repo_id, "llamacpp")})
    llamacpp_models = filter_lower_family_versions(llamacpp_models)

    local_server_specs = [
        {
            "id": "vllm",
            "name": "vLLM",
            "description": "vLLM OpenAI 호환 서버(예: http://localhost:8000/v1)에 연결합니다.",
            "requires": "VLLM_BASE_URL",
            "note": engine_support_status("vllm").get("reason"),
        },
        {
            "id": "lmstudio",
            "name": "LM Studio",
            "description": "LM Studio 로컬 OpenAI 호환 서버에 연결합니다.",
            "requires": "LMSTUDIO_BASE_URL",
            "note": (
                "다운로드된 모델은 자동 감지하고, 선택 시 필요하면 다운로드 후 바로 로드합니다."
                if downloaded_lmstudio else
                "LM Studio 설치 후 모델을 선택하면 Local Server 시작, 다운로드, 로드를 자동으로 진행합니다."
            ),
            "server_ready": bool(downloaded_lmstudio),
        },
        {
            "id": "llamacpp",
            "name": "llama.cpp",
            "description": "llama.cpp 서버(OpenAI 호환 /v1)에 연결합니다.",
            "requires": "LLAMACPP_BASE_URL",
        },
    ]

    engines = [
        {
            "id": "local_mlx",
            "name": "MLX",
            "kind": "local",
            "description": "Apple Silicon GPU에서 MLX-VLM 모델을 직접 실행하고, Gemma 4는 필요 시 MLX-LM 텍스트 경로로 재시도합니다.",
            "installed": engine_installed("local_mlx"),
            "installable": True,
            "install_label": ENGINE_INSTALLERS["local_mlx"]["label"],
            "models": mlx_models,
        },
        {
            "id": "ollama",
            "name": "Ollama",
            "kind": "local-server",
            "description": "Ollama 로컬 서버를 OpenAI 호환 엔진처럼 사용합니다.",
            "installed": ollama_installed,
            "installable": True,
            "install_label": ENGINE_INSTALLERS["ollama"]["label"],
            "models": ollama_models,
        },
    ]
    for spec in local_server_specs:
        support = engine_support_status(spec["id"])
        engines.append({
            "id": spec["id"],
            "name": spec["name"],
            "kind": "local-server",
            "description": spec["description"],
            "installed": engine_installed(spec["id"]),
            "supported": support["supported"],
            "support_reason": support["reason"],
            "installable": support["supported"] and spec["id"] in ENGINE_INSTALLERS,
            "install_label": ENGINE_INSTALLERS.get(spec["id"], {}).get("label"),
            "requires": spec["requires"],
            "models": (
                vllm_models if spec["id"] == "vllm"
                else lmstudio_models if spec["id"] == "lmstudio"
                else llamacpp_models if spec["id"] == "llamacpp"
                else ENGINE_MODEL_CATALOG.get(spec["id"], [])
            ),
            "note": spec.get("note") or support["reason"] or f"{spec['requires']} 설정 시 활성화됩니다.",
            "server_ready": spec.get("server_ready"),
        })
    for provider in ["openai", "openrouter", "groq", "together", "xai"]:
        env_key = next((item.get("requires") for item in cloud_by_provider.get(provider, []) if item.get("requires")), None)
        provider_models = []
        for model in cloud_by_provider.get(provider, []):
            cache = CLOUD_VERIFY_CACHE.get(model.get("id"))
            provider_models.append({
                **model,
                "verified": cache.get("ok") if cache else None,
                "verify_reason": cache.get("reason") if cache else None,
            })
        engines.append({
            "id": provider,
            "name": provider.title(),
            "kind": "cloud",
            "description": "OpenAI 호환 Chat Completions API로 cloud LLM을 실행합니다.",
            "installed": engine_installed(provider),
            "installable": True,
            "install_label": ENGINE_INSTALLERS[provider]["label"],
            "requires": env_key,
            "models": provider_models,
        })
    return engines

def runtime_features() -> Dict:
    return {
        "mode": APP_MODE,
        "public": IS_PUBLIC_MODE,
        "host": DEFAULT_HOST,
        "port": DEFAULT_PORT,
        "data_dir": str(DATA_DIR),
        "telegram_enabled": ENABLE_TELEGRAM,
        "graph_enabled": ENABLE_GRAPH,
        "autoload_models": AUTOLOAD_MODELS,
        "model_idle_unload_seconds": MODEL_IDLE_UNLOAD_SECONDS,
        "model_memory_policy": router.model_memory_policy(),
        "allow_local_models": ALLOW_LOCAL_MODELS,
        "security": {
            "host": DEFAULT_HOST,
            "require_auth": REQUIRE_AUTH,
            "invite_gate_enabled": INVITE_GATE_ENABLED,
            "keyring_available": keyring is not None,
            "plaintext_api_keys_allowed": ALLOW_PLAINTEXT_API_KEYS,
            "cors_allow_network": CORS_ALLOW_NETWORK,
        },
        "default_model": PUBLIC_MODEL if IS_PUBLIC_MODE else LOCAL_MODEL,
        "local_only_features": {
            "mlx": ALLOW_LOCAL_MODELS and not IS_PUBLIC_MODE,
            "telegram_bridge": ENABLE_TELEGRAM,
            "desktop_chrome_bridge": not IS_PUBLIC_MODE,
            "computer_use_bridge": not IS_PUBLIC_MODE,
        },
        "public_features": {
            "web_ui": True,
            "openai_compatible_models": True,
            "persistent_data_dir": str(DATA_DIR),
        },
    }

def install_engine(engine: str) -> Dict:
    if engine not in ENGINE_INSTALLERS:
        raise HTTPException(status_code=400, detail="지원하지 않는 엔진입니다.")
    installer = ENGINE_INSTALLERS[engine]
    required_binary = installer.get("requires_binary")
    if required_binary and shutil.which(required_binary) is None:
        raise HTTPException(status_code=400, detail=f"{required_binary}가 설치되어 있지 않아 자동 설치할 수 없습니다.")
    command = installer["command"]
    run_kwargs = {
        "cwd": str(BASE_DIR),
        "capture_output": True,
        "text": True,
        "timeout": 900,
        "check": False,
    }

    if engine == "vllm" and sys.platform == "darwin" and platform.machine() == "arm64":
        command = [
            "/bin/bash",
            "-lc",
            "set -euo pipefail; "
            "if [ ! -x /opt/homebrew/bin/python3.12 ]; then brew install python@3.12; fi; "
            "/opt/homebrew/bin/python3.12 -m venv ~/.venv-vllm-metal; "
            "~/.venv-vllm-metal/bin/pip install -U pip setuptools wheel; "
            "~/.venv-vllm-metal/bin/pip install vllm-metal",
        ]
    try:
        completed = subprocess.run(command, **run_kwargs)
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=408, detail="엔진 설치 시간이 초과되었습니다.")
    result = {
        "engine": engine,
        "command": " ".join(command),
        "returncode": completed.returncode,
        "stdout": completed.stdout[-12000:],
        "stderr": completed.stderr[-12000:],
        "installed": engine_installed(engine),
    }
    ollama = local_binary("ollama")
    if engine == "ollama" and completed.returncode == 0 and ollama:
        # Skip if already running to avoid orphan daemons.
        already_up = False
        try:
            probe = subprocess.run([ollama, "list"], capture_output=True, timeout=2, check=False)
            already_up = probe.returncode == 0
        except Exception:
            already_up = False
        if already_up:
            result["daemon_started"] = "already_running"
        else:
            try:
                # Detach so the daemon survives this request but doesn't become our zombie.
                subprocess.Popen(
                    [ollama, "serve"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                result["daemon_started"] = True
            except Exception as e:
                logging.warning("ollama serve spawn failed: %s", e)
                result["daemon_started"] = False
    return result


def _resolve_model_alias(model_id: str, engine: Optional[str] = None) -> str:
    raw = model_id.strip()
    engine_hint = (engine or "").strip().lower()
    provider: Optional[str] = None
    model_name = raw
    if ":" in raw:
        prefix, rest = raw.split(":", 1)
        prefix = prefix.strip().lower()
        if prefix in {"ollama", "vllm", "lmstudio", "llamacpp", "local_mlx", "mlx"}:
            provider = "local_mlx" if prefix in {"local_mlx", "mlx"} else prefix
            model_name = rest.strip()
    provider = provider or ("local_mlx" if engine_hint in {"", "local_mlx", "mlx"} else engine_hint)
    aliases = MODEL_ENGINE_ALIASES.get(model_name.lower())
    if not aliases:
        return raw
    mapped = aliases.get(provider)
    if not mapped:
        return raw
    return mapped if provider == "local_mlx" else f"{provider}:{mapped}"


def normalize_local_model_request(model_id: str, engine: Optional[str] = None) -> str:
    model_id = _resolve_model_alias(model_id, engine)
    engine = (engine or "").strip().lower()
    if engine in {"local_mlx", "mlx"} and model_id.startswith(("local_mlx:", "mlx:")):
        return model_id.split(":", 1)[1].strip()
    if engine and engine not in {"local_mlx", "mlx"} and ":" not in model_id:
        return f"{engine}:{model_id}"
    return model_id


def ensure_engine_ready(engine: str) -> Dict[str, object]:
    engine = "local_mlx" if engine == "mlx" else engine
    if engine not in ENGINE_INSTALLERS and engine not in OPENAI_COMPATIBLE_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"지원하지 않는 엔진입니다: {engine}")
    support = engine_support_status(engine)
    if not support["supported"]:
        raise HTTPException(status_code=400, detail=str(support["reason"]))

    if engine_installed(engine):
        if engine == "local_mlx":
            ensure_mlx_runtime()
        return {"engine": engine, "installed": True, "installed_now": False}

    if engine not in ENGINE_INSTALLERS:
        raise HTTPException(status_code=400, detail=f"{engine} 엔진 설치 방법이 등록되어 있지 않습니다.")

    result = install_engine(engine)
    if result.get("returncode") not in (0, None) or not engine_installed(engine):
        detail = result.get("stderr") or result.get("stdout") or f"{engine} 설치에 실패했습니다."
        raise HTTPException(status_code=500, detail=str(detail)[-2000:])

    if engine == "local_mlx":
        ensure_mlx_runtime()
    return {"engine": engine, "installed": True, "installed_now": True, "install": result}


def build_model_resolution(
    input_id: str,
    engine: Optional[str],
    *,
    user_email: Optional[str] = None,
    display_name: Optional[str] = None,
) -> _ModelResolution:
    """피드백 #1/#2 공용 ModelResolution 생성기.

    사용자가 클릭한 input_id + engine 힌트를 받아 모든 단계가 공유할
    canonical identity를 만든다.
    """
    normalized = normalize_local_model_request(input_id, engine)
    return _ModelResolution.from_request(
        normalized,
        engine=engine,
        user_email=user_email,
        display_name=display_name or input_id,
        engine_aliases=MODEL_ENGINE_ALIASES,
    )


_LOCAL_SMOKE_ENGINES = {"local_mlx", "ollama", "vllm", "lmstudio", "llamacpp"}


async def _smoke_test_loaded_model(
    resolution: _ModelResolution,
    *,
    api_key_override: Optional[str] = None,
) -> Dict[str, object]:
    """로드 직후 짧은 채팅 테스트를 돌려 ready_to_chat 여부를 판정한다.

    Cloud(OpenAI/Anthropic/OpenRouter 등) 모델은 사용자 비용 발생 가능성 때문에 skip.
    실패해도 예외를 던지지 않는다. 결과는 compat_cache에도 기록된다.
    """
    if (resolution.engine or "").lower() not in _LOCAL_SMOKE_ENGINES:
        profile = _ensure_compat_profile(resolution.load_id, resolution.engine)
        return {
            "ok": True,
            "reason": "skipped (cloud model — smoke test would incur cost)",
            "answer": None,
            "profile": profile.to_dict(),
            "skipped": True,
        }
    try:
        text = await asyncio.wait_for(
            router.generate(
                _SMOKE_PROMPT,
                context=None,
                max_tokens=128,
                temperature=0.1,
            ),
            timeout=30,
        )
    except Exception as exc:  # pragma: no cover - generator may not exist on all engines
        reason = str(exc)[:200] or "generation_failed"
        profile = _record_smoke_result(
            resolution.load_id, resolution.engine, False, reason, status="failed"
        )
        return {
            "ok": False,
            "status": "failed",
            "reason": reason,
            "answer": None,
            "profile": profile.to_dict(),
        }

    profile = _ensure_compat_profile(resolution.load_id, resolution.engine)
    cleaned = _compat_fast_postprocess(str(text or ""), profile.to_dict())
    # item 3-3: ok / degraded / failed 3분류. degraded는 채팅은 가능하다.
    status, reason = _classify_smoke_response(cleaned)
    ok = status != "failed"
    profile = _record_smoke_result(
        resolution.load_id, resolution.engine, ok, reason, status=status
    )
    return {
        "ok": ok,
        "status": status,
        "reason": reason,
        "answer": cleaned,
        "profile": profile.to_dict(),
    }


async def prepare_and_load_model(
    model_id: str,
    request: Request,
    engine: Optional[str] = None,
    user_email: Optional[str] = None,
    adapter_path: Optional[str] = None,
    draft_model_id: Optional[str] = None,
    allow_download: bool = False,
) -> Dict[str, object]:
    model_id = normalize_local_model_request(model_id, engine)
    if not model_id:
        raise HTTPException(status_code=400, detail="모델 식별자가 비어 있습니다.")

    # 피드백 #1: ModelResolution을 모든 단계가 공유한다.
    resolution = _ModelResolution.from_request(
        model_id,
        engine=engine,
        user_email=user_email or get_current_user(request),
        engine_aliases=MODEL_ENGINE_ALIASES,
    )

    parsed_provider, parsed_model = parse_model_ref(model_id)
    if parsed_provider == "mlx":
        parsed_provider = "local_mlx"
    compatibility = _model_runtime_compatibility(parsed_model, engine=parsed_provider)
    if compatibility.get("supported") is False:
        raise HTTPException(status_code=400, detail=compatibility)

    local_engines = {"local_mlx", "ollama", "vllm", "lmstudio", "llamacpp"}
    install_result: Dict[str, object] = {}
    download_result: Optional[Dict[str, object]] = None

    if parsed_provider in local_engines:
        if not engine_installed(parsed_provider) and not _download_allowed(allow_download):
            _engine_install_block(parsed_provider)
        install_result = ensure_engine_ready(parsed_provider)

    if parsed_provider == "local_mlx":
        explicit_path = Path(parsed_model).expanduser()
        if not explicit_path.exists() and not hf_model_ready(parsed_model, "local_mlx"):
            if not _download_allowed(allow_download):
                _download_block(parsed_provider, parsed_model)
            download_result = download_hf_model(parsed_model, "local_mlx")
    elif parsed_provider == "ollama":
        ensure_ollama_server()
        ollama = local_binary("ollama")
        if not ollama:
            raise HTTPException(status_code=400, detail="Ollama가 설치되지 않았습니다.")
        if parsed_model not in get_ollama_pulled_models():
            if not _download_allowed(allow_download):
                _download_block(parsed_provider, parsed_model)
            completed = subprocess.run(
                [ollama, "pull", parsed_model],
                capture_output=True,
                text=True,
                timeout=900,
                check=False,
            )
            if completed.returncode != 0:
                raise HTTPException(status_code=500, detail=completed.stderr[-2000:] or "Ollama 모델 다운로드 실패")
            download_result = {"provider": "ollama", "model": parsed_model, "returncode": completed.returncode}
    elif parsed_provider == "vllm":
        if not hf_model_ready(parsed_model, "vllm") and not _download_allowed(allow_download):
            _download_block(parsed_provider, parsed_model)
        ensure_vllm_server(parsed_model)
        download_result = {"provider": "vllm", "model": parsed_model, "server_ready": True}
    elif parsed_provider == "llamacpp":
        if not hf_model_ready(parsed_model, "llamacpp") and not _download_allowed(allow_download):
            _download_block(parsed_provider, parsed_model)
        ensure_llamacpp_server(parsed_model)
        download_result = {"provider": "llamacpp", "model": parsed_model, "server_ready": True}
    elif parsed_provider == "lmstudio":
        downloaded = {
            str(item.get("key") or "").strip()
            for item in get_lmstudio_models()
            if isinstance(item, dict)
        }
        if parsed_model not in downloaded and not _download_allowed(allow_download):
            _download_block(parsed_provider, parsed_model)
        ensured = ensure_lmstudio_model(parsed_model)
        resolved_model = str(
            ensured.get("instance_id")
            or ensured.get("resolved_model")
            or parsed_model
        ).strip()
        parsed_model = resolved_model
        model_id = f"lmstudio:{resolved_model}"
        download_result = ensured

    effective_email = (user_email or get_current_user(request) or "").strip()
    user_api_key = get_user_api_key(effective_email, parsed_provider) if parsed_provider != "local_mlx" else None
    msg = await router.load_model(
        model_id,
        adapter_path,
        draft_model_id=draft_model_id,
        api_key_override=user_api_key,
        owner=effective_email or None,
    )
    # 피드백 #1/#2: 로드 직후 ModelResolution을 실제 current로 동기화하고 smoke test 수행.
    resolution.update_after_load(actual_current=router.current_model_id)
    smoke_result: Dict[str, object] = {}
    ready_to_chat = True
    compat_status = "ok"
    try:
        smoke_result = await _smoke_test_loaded_model(resolution, api_key_override=user_api_key)
        ready_to_chat = bool(smoke_result.get("ok"))
        # item 3-3: smoke 결과의 3분류(ok/degraded/failed)를 그대로 노출한다.
        compat_status = str(smoke_result.get("status") or ("ok" if ready_to_chat else "degraded"))
    except Exception as exc:  # never break load on smoke test failures
        logging.warning("smoke test failed for %s: %s", resolution.load_id, exc)
        compat_status = "unknown"
    return {
        "status": "ok",
        "message": msg,
        "model": model_id,
        "current": router.current_model_id,
        "engine": parsed_provider,
        "installed_now": bool(install_result.get("installed_now")),
        "download": download_result,
        "resolution": resolution.to_dict(),
        "downloaded": bool(download_result and not (isinstance(download_result, dict) and download_result.get("cached"))),
        "loaded": True,
        "ready_to_chat": ready_to_chat,
        "compatibility_status": compat_status,
        "smoke_test": smoke_result,
    }


def sse_event(event: str, data: Dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def prepare_and_load_model_stream(
    model_id: str,
    request: Request,
    engine: Optional[str] = None,
    user_email: Optional[str] = None,
    allow_download: bool = False,
) -> AsyncIterator[str]:
    model_id = normalize_local_model_request(model_id, engine)
    if not model_id:
        raise HTTPException(status_code=400, detail="모델 식별자가 비어 있습니다.")

    parsed_provider, parsed_model = parse_model_ref(model_id)
    if parsed_provider == "mlx":
        parsed_provider = "local_mlx"
    compatibility = _model_runtime_compatibility(parsed_model, engine=parsed_provider)
    if compatibility.get("supported") is False:
        raise HTTPException(status_code=400, detail=compatibility)

    work_queue: "queue.Queue[Dict[str, object]]" = queue.Queue()
    work_result: Dict[str, object] = {}

    def emit_progress(payload: Dict[str, object]) -> None:
        work_queue.put({"kind": "progress", "data": payload})

    def blocking_prepare() -> None:
        try:
            local_engines = {"local_mlx", "ollama", "vllm", "lmstudio", "llamacpp"}
            install_result: Dict[str, object] = {}
            download_result: Optional[Dict[str, object]] = None
            prepared_model_id = model_id
            prepared_model_name = parsed_model

            if parsed_provider in local_engines:
                emit_progress(model_download_progress_payload(
                    "engine",
                    "실행 엔진을 확인하는 중입니다.",
                    percent=2,
                    indeterminate=True,
                ))
                if not engine_installed(parsed_provider) and not _download_allowed(allow_download):
                    _engine_install_block(parsed_provider)
                install_result = ensure_engine_ready(parsed_provider)
                emit_progress(model_download_progress_payload(
                    "engine",
                    "실행 엔진 준비가 완료되었습니다.",
                    percent=10,
                    indeterminate=False,
                ))

            if parsed_provider == "local_mlx":
                explicit_path = Path(parsed_model).expanduser()
                if explicit_path.exists():
                    download_result = {"model": parsed_model, "path": str(explicit_path), "cached": True}
                    emit_progress(model_download_progress_payload(
                        "download",
                        "로컬 모델 경로를 확인했습니다.",
                        percent=100,
                        detail=str(explicit_path),
                        eta_seconds=0,
                    ))
                elif not hf_model_ready(parsed_model, "local_mlx"):
                    if not _download_allowed(allow_download):
                        _download_block(parsed_provider, parsed_model)
                    download_result = download_hf_model(parsed_model, "local_mlx", progress_emit=emit_progress)
                else:
                    download_result = {"model": parsed_model, "path": str(hf_model_dir(parsed_model)), "cached": True}
                    emit_progress(model_download_progress_payload(
                        "download",
                        "이미 다운로드된 모델을 확인했습니다.",
                        percent=100,
                        eta_seconds=0,
                    ))
            elif parsed_provider == "ollama":
                emit_progress(model_download_progress_payload(
                    "engine",
                    "Ollama 서버를 확인하는 중입니다.",
                    percent=12,
                    indeterminate=True,
                ))
                ensure_ollama_server()
                if parsed_model not in get_ollama_pulled_models():
                    if not _download_allowed(allow_download):
                        _download_block(parsed_provider, parsed_model)
                    download_result = pull_ollama_model_with_progress(parsed_model, progress_emit=emit_progress)
                else:
                    download_result = {"provider": "ollama", "model": parsed_model, "cached": True}
                    emit_progress(model_download_progress_payload(
                        "download",
                        "이미 다운로드된 Ollama 모델을 확인했습니다.",
                        percent=100,
                        detail=parsed_model,
                        eta_seconds=0,
                    ))
            elif parsed_provider == "vllm":
                if not hf_model_ready(parsed_model, "vllm"):
                    if not _download_allowed(allow_download):
                        _download_block(parsed_provider, parsed_model)
                    download_result = download_hf_model(parsed_model, "vllm", progress_emit=emit_progress)
                else:
                    download_result = {"provider": "vllm", "model": parsed_model, "cached": True}
                    emit_progress(model_download_progress_payload(
                        "download",
                        "이미 다운로드된 모델을 확인했습니다.",
                        percent=100,
                        detail=parsed_model,
                        eta_seconds=0,
                    ))
                emit_progress(model_download_progress_payload(
                    "server",
                    "vLLM 서버를 시작하는 중입니다.",
                    percent=92,
                    indeterminate=True,
                ))
                ensure_vllm_server(parsed_model)
                download_result = {**(download_result or {}), "provider": "vllm", "model": parsed_model, "server_ready": True}
            elif parsed_provider == "llamacpp":
                if not hf_model_ready(parsed_model, "llamacpp"):
                    if not _download_allowed(allow_download):
                        _download_block(parsed_provider, parsed_model)
                    download_result = download_hf_model(parsed_model, "llamacpp", progress_emit=emit_progress)
                else:
                    download_result = {"provider": "llamacpp", "model": parsed_model, "cached": True}
                    emit_progress(model_download_progress_payload(
                        "download",
                        "이미 다운로드된 GGUF 모델을 확인했습니다.",
                        percent=100,
                        detail=parsed_model,
                        eta_seconds=0,
                    ))
                emit_progress(model_download_progress_payload(
                    "server",
                    "llama.cpp 서버를 시작하는 중입니다.",
                    percent=92,
                    indeterminate=True,
                ))
                ensure_llamacpp_server(parsed_model)
                download_result = {**(download_result or {}), "provider": "llamacpp", "model": parsed_model, "server_ready": True}
            elif parsed_provider == "lmstudio":
                downloaded = {
                    str(item.get("key") or "").strip()
                    for item in get_lmstudio_models()
                    if isinstance(item, dict)
                }
                if parsed_model not in downloaded and not _download_allowed(allow_download):
                    _download_block(parsed_provider, parsed_model)
                emit_progress(model_download_progress_payload(
                    "download",
                    "LM Studio 모델을 확인하는 중입니다.",
                    percent=35,
                    indeterminate=True,
                ))
                ensured = ensure_lmstudio_model(parsed_model)
                resolved_model = str(
                    ensured.get("instance_id")
                    or ensured.get("resolved_model")
                    or parsed_model
                ).strip()
                prepared_model_name = resolved_model
                prepared_model_id = f"lmstudio:{resolved_model}"
                download_result = ensured
            else:
                emit_progress(model_download_progress_payload(
                    "engine",
                    "모델 연결을 준비하는 중입니다.",
                    percent=30,
                    indeterminate=True,
                ))

            work_result.update({
                "model_id": prepared_model_id,
                "parsed_provider": parsed_provider,
                "parsed_model": prepared_model_name,
                "install_result": install_result,
                "download_result": download_result,
            })
            work_queue.put({"kind": "done"})
        except HTTPException as exc:
            work_queue.put({"kind": "error", "status_code": exc.status_code, "detail": exc.detail})
        except Exception as exc:
            logging.exception("model prepare stream worker failed")
            work_queue.put({
                "kind": "error",
                "status_code": 500,
                "detail": _friendly_model_runtime_error(exc, model_id=model_id, engine=parsed_provider),
            })

    worker = threading.Thread(target=blocking_prepare, daemon=True)
    worker.start()

    while True:
        item = await asyncio.to_thread(work_queue.get)
        kind = item.get("kind")
        if kind == "progress":
            yield sse_event("progress", item["data"])
        elif kind == "error":
            raise HTTPException(
                status_code=int(item.get("status_code") or 500),
                detail=item.get("detail") or "모델 준비에 실패했습니다.",
            )
        elif kind == "done":
            break

    prepared_model_id = str(work_result.get("model_id") or model_id)
    prepared_provider = str(work_result.get("parsed_provider") or parsed_provider)
    install_result = work_result.get("install_result") or {}
    download_result = work_result.get("download_result")

    yield sse_event("progress", model_download_progress_payload(
        "load",
        "모델을 메모리에 로드하는 중입니다.",
        percent=96,
        indeterminate=True,
    ))

    effective_email = (user_email or get_current_user(request) or "").strip()
    user_api_key = get_user_api_key(effective_email, prepared_provider) if prepared_provider != "local_mlx" else None
    msg = await router.load_model(
        prepared_model_id,
        None,
        draft_model_id=None,
        api_key_override=user_api_key,
        owner=effective_email or None,
    )
    # 피드백 #1/#2: SSE에도 ModelResolution과 smoke test 결과를 같이 내려준다.
    resolution_stream = _ModelResolution.from_request(
        prepared_model_id,
        engine=prepared_provider,
        user_email=effective_email or None,
        engine_aliases=MODEL_ENGINE_ALIASES,
    )
    resolution_stream.update_after_load(actual_current=router.current_model_id)
    yield sse_event("progress", model_download_progress_payload(
        "smoke_test",
        "채팅 호환성 테스트 중입니다.",
        percent=98,
        indeterminate=True,
    ))
    smoke_result: Dict[str, object] = {}
    ready_to_chat = True
    compat_status = "ok"
    try:
        smoke_result = await _smoke_test_loaded_model(resolution_stream, api_key_override=user_api_key)
        ready_to_chat = bool(smoke_result.get("ok"))
        # item 3-3: smoke 결과의 3분류(ok/degraded/failed)를 그대로 노출한다.
        compat_status = str(smoke_result.get("status") or ("ok" if ready_to_chat else "degraded"))
    except Exception as exc:
        logging.warning("smoke test (stream) failed for %s: %s", resolution_stream.load_id, exc)
        compat_status = "unknown"
    result = {
        "status": "ok",
        "message": msg,
        "model": prepared_model_id,
        "current": router.current_model_id,
        "engine": prepared_provider,
        "installed_now": bool(isinstance(install_result, dict) and install_result.get("installed_now")),
        "download": download_result,
        "resolution": resolution_stream.to_dict(),
        "downloaded": bool(download_result and not (isinstance(download_result, dict) and download_result.get("cached"))),
        "loaded": True,
        "ready_to_chat": ready_to_chat,
        "compatibility_status": compat_status,
        "smoke_test": smoke_result,
    }
    yield sse_event("progress", model_download_progress_payload(
        "done",
        "모델 준비가 완료되었습니다.",
        percent=100,
        eta_seconds=0,
    ))
    yield sse_event("done", result)


CLOUD_VERIFY_CACHE: Dict[str, Dict] = {}
CLOUD_VERIFY_TTL_SECONDS = 600

async def _probe_cloud_model(model_ref: str) -> Dict[str, object]:
    provider, model_name = parse_model_ref(model_ref)
    config = OPENAI_COMPATIBLE_PROVIDERS.get(provider)
    if not config:
        return {"ok": False, "reason": f"Unsupported provider: {provider}"}

    api_key = os.getenv(config["env_key"]) or config.get("api_key_fallback")
    if not api_key:
        return {"ok": False, "reason": f"Missing API key: {config['env_key']}"}

    base_url = os.getenv(config.get("base_url_env", "")) if config.get("base_url_env") else None
    base_url = base_url or config.get("base_url")
    client_kwargs = {"api_key": api_key}
    if base_url:
        client_kwargs["base_url"] = base_url

    try:
        client = AsyncOpenAI(**client_kwargs)
        await asyncio.wait_for(
            client.chat.completions.create(
                model=model_name,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1,
                temperature=0,
            ),
            timeout=15,
        )
        return {"ok": True, "reason": "ok"}
    except Exception as e:
        return {"ok": False, "reason": str(e)[:220]}


async def verify_cloud_models(force: bool = False, provider_filter: Optional[str] = None) -> Dict[str, Dict]:
    now = time.time()
    cloud_items = [item for item in router.detected_cloud_models() if item.get("tag") == "cloud"]
    if provider_filter:
        cloud_items = [item for item in cloud_items if item.get("provider") == provider_filter]

    results: Dict[str, Dict] = {}
    for item in cloud_items:
        model_ref = item["id"]
        cached = CLOUD_VERIFY_CACHE.get(model_ref)
        if not force and cached and (now - cached.get("ts", 0) <= CLOUD_VERIFY_TTL_SECONDS):
            results[model_ref] = cached
            continue
        if item.get("available") is False:
            record = {"ok": False, "reason": item.get("requires") or "API key missing", "ts": now}
            CLOUD_VERIFY_CACHE[model_ref] = record
            results[model_ref] = record
            continue
        probe = await _probe_cloud_model(model_ref)
        record = {"ok": bool(probe.get("ok")), "reason": probe.get("reason", ""), "ts": now}
        CLOUD_VERIFY_CACHE[model_ref] = record
        results[model_ref] = record
    return results
