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

def _missing_current_user(_request: Request) -> Optional[str]:
    return None


def _missing_user_api_key(_email: Optional[str], _provider: str) -> Optional[str]:
    return None


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
    friendly_model_runtime_error as _friendly_model_runtime_error,
    model_runtime_compatibility as _model_runtime_compatibility,
)
from latticeai.core.model_resolution import ModelResolution as _ModelResolution
from .model_engines import (
    ensure_lmstudio_server as _ensure_lmstudio_server,
    ensure_ollama_server as _ensure_ollama_server,
    ensure_vllm_server as _ensure_vllm_server,
    ensure_llamacpp_server as _ensure_llamacpp_server,
    find_lmstudio_cli as _find_lmstudio_cli,
    get_openai_compatible_server_models as _get_openai_compatible_server_models,
    pull_ollama_model_with_progress as _pull_ollama_model_with_progress,
    get_ollama_pulled_models as _get_ollama_pulled_models,
    engine_support_status as _engine_support_status,
    install_engine as _install_engine,
    local_binary as _local_binary,
    vllm_executable as _vllm_executable,
    vllm_metal_python as _vllm_metal_python,
    wait_for_openai_compatible_server as _wait_for_openai_compatible_server,
    windows_binary_candidates as _windows_binary_candidates,
    LOCAL_SERVER_PROCESSES as _LOCAL_SERVER_PROCESSES,
)

# Rebind extracted engines for legacy module globals
ensure_lmstudio_server = _ensure_lmstudio_server
ensure_ollama_server = _ensure_ollama_server
ensure_vllm_server = _ensure_vllm_server
ensure_llamacpp_server = _ensure_llamacpp_server
pull_ollama_model_with_progress = _pull_ollama_model_with_progress
get_ollama_pulled_models = _get_ollama_pulled_models
engine_support_status = _engine_support_status
install_engine = _install_engine

# Server decomp: proper ModelRuntimeState class for globals/wiring
class ModelRuntimeState:
    """Central object for all legacy globals. Module level names delegate for compat.
    This is the clean wiring surface for future decomp.
    """
    def __init__(self):
        self.router = None
        self.APP_MODE = "local"
        self.DEFAULT_HOST = "127.0.0.1"
        self.DEFAULT_PORT = 4825
        self.DATA_DIR = Path.home() / ".latticeai"
        self.BASE_DIR = Path.cwd()
        self.ENABLE_TELEGRAM = False
        self.ENABLE_GRAPH = True
        self.AUTOLOAD_MODELS = False
        self.MODEL_IDLE_UNLOAD_SECONDS = 0
        self.ALLOW_LOCAL_MODELS = True
        self.REQUIRE_AUTH = False
        self.INVITE_GATE_ENABLED = False
        self.ALLOW_PLAINTEXT_API_KEYS = False
        self.CORS_ALLOW_NETWORK = False
        self.PUBLIC_MODEL = "openai:gpt-4o-mini"
        self.LOCAL_MODEL = "mlx-community/gemma-4-12b-it-4bit"
        self.IS_PUBLIC_MODE = False
        self.keyring = None
        self.get_current_user = _missing_current_user
        self.get_user_api_key = _missing_user_api_key

    def sync_to_module_globals(self):
        global router, APP_MODE, DEFAULT_HOST, DEFAULT_PORT, DATA_DIR, BASE_DIR
        global ENABLE_TELEGRAM, ENABLE_GRAPH, AUTOLOAD_MODELS, MODEL_IDLE_UNLOAD_SECONDS
        global ALLOW_LOCAL_MODELS, REQUIRE_AUTH, INVITE_GATE_ENABLED, ALLOW_PLAINTEXT_API_KEYS
        global CORS_ALLOW_NETWORK, PUBLIC_MODEL, LOCAL_MODEL, IS_PUBLIC_MODE
        global keyring, get_current_user, get_user_api_key
        router = self.router
        APP_MODE = self.APP_MODE
        DEFAULT_HOST = self.DEFAULT_HOST
        DEFAULT_PORT = self.DEFAULT_PORT
        DATA_DIR = self.DATA_DIR
        BASE_DIR = self.BASE_DIR
        ENABLE_TELEGRAM = self.ENABLE_TELEGRAM
        ENABLE_GRAPH = self.ENABLE_GRAPH
        AUTOLOAD_MODELS = self.AUTOLOAD_MODELS
        MODEL_IDLE_UNLOAD_SECONDS = self.MODEL_IDLE_UNLOAD_SECONDS
        ALLOW_LOCAL_MODELS = self.ALLOW_LOCAL_MODELS
        REQUIRE_AUTH = self.REQUIRE_AUTH
        INVITE_GATE_ENABLED = self.INVITE_GATE_ENABLED
        ALLOW_PLAINTEXT_API_KEYS = self.ALLOW_PLAINTEXT_API_KEYS
        CORS_ALLOW_NETWORK = self.CORS_ALLOW_NETWORK
        PUBLIC_MODEL = self.PUBLIC_MODEL
        LOCAL_MODEL = self.LOCAL_MODEL
        IS_PUBLIC_MODE = self.IS_PUBLIC_MODE
        keyring = self.keyring
        get_current_user = self.get_current_user
        get_user_api_key = self.get_user_api_key

STATE = ModelRuntimeState()
STATE.sync_to_module_globals()

# Configured by server_app.configure_model_runtime during app assembly.


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


def configure_model_runtime(**deps) -> None:
    """Wire app-owned runtime dependencies without importing server_app.

    Explicit per-key assignment (no blanket globals().update) so wiring is
    auditable and side effects are visible. Preserves exact public module
    globals and prior behavior for all callers and shims.
    Now uses STATE class for clean decomp.
    """
    for key, value in deps.items():
        if hasattr(STATE, key):
            setattr(STATE, key, value)
        elif key == "keyring":
            STATE.keyring = value
        elif key == "get_current_user":
            STATE.get_current_user = value
        elif key == "get_user_api_key":
            STATE.get_user_api_key = value

    STATE.sync_to_module_globals()


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


LOCAL_SERVER_PROCESSES = _LOCAL_SERVER_PROCESSES
VLLM_METAL_ENV = Path.home() / ".venv-vllm-metal"
VLLM_METAL_BIN = VLLM_METAL_ENV / "bin" / "vllm"
VLLM_METAL_PYTHON = VLLM_METAL_ENV / "bin" / "python"
LMSTUDIO_BUNDLED_CLI = Path("/Applications/LM Studio.app/Contents/Resources/app/.webpack/lms")

def windows_binary_candidates(binary: str) -> List[Path]:
    return _windows_binary_candidates(binary)


def local_binary(binary: str) -> Optional[str]:
    return _local_binary(binary)


def find_lmstudio_cli() -> Optional[str]:
    return _find_lmstudio_cli()


def vllm_executable() -> Optional[str]:
    return _vllm_executable()


def vllm_metal_python() -> Optional[str]:
    return _vllm_metal_python()


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
    return _ensure_lmstudio_server()


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
    return _engine_support_status(engine)

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
    return _pull_ollama_model_with_progress(model_name, progress_emit)


def get_ollama_pulled_models() -> set:
    return _get_ollama_pulled_models()


def get_openai_compatible_server_models(provider: str) -> List[str]:
    return _get_openai_compatible_server_models(provider)


def ensure_ollama_server() -> None:
    return _ensure_ollama_server()


def wait_for_openai_compatible_server(provider: str, model_name: Optional[str] = None, timeout: int = 45) -> bool:
    return _wait_for_openai_compatible_server(provider, model_name=model_name, timeout=timeout)


def ensure_vllm_server(model_name: str) -> None:
    return _ensure_vllm_server(model_name)


def ensure_llamacpp_server(model_name: str) -> None:
    return _ensure_llamacpp_server(model_name)


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
    return _install_engine(engine)


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
    # Delegated to model_engines for server decomp
    from .model_engines import _smoke_test_loaded_model as _impl_smoke
    return await _impl_smoke(resolution, api_key_override=api_key_override)


async def prepare_and_load_model(
    model_id: str,
    request: Request,
    engine: Optional[str] = None,
    user_email: Optional[str] = None,
    adapter_path: Optional[str] = None,
    draft_model_id: Optional[str] = None,
    allow_download: bool = False,
) -> Dict[str, object]:
    from .model_loading import prepare_and_load_model as _impl

    return await _impl(
        model_id,
        request,
        engine=engine,
        user_email=user_email,
        adapter_path=adapter_path,
        draft_model_id=draft_model_id,
        allow_download=allow_download,
    )


def sse_event(event: str, data: Dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def prepare_and_load_model_stream(
    model_id: str,
    request: Request,
    engine: Optional[str] = None,
    user_email: Optional[str] = None,
    allow_download: bool = False,
) -> AsyncIterator[str]:
    from .model_loading import prepare_and_load_model_stream as _impl

    async for event in _impl(
        model_id,
        request,
        engine=engine,
        user_email=user_email,
        allow_download=allow_download,
    ):
        yield event


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
