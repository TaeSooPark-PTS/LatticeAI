"""Engine server management, pull, install and support logic extracted from model_runtime monolith.

This module is the home for engine-specific server starting, model pulling and install flows.
``model_runtime`` exposes compatibility callables, while application configuration
is passed explicitly through the bound runtime service. Circular imports are
avoided by late imports inside functions.
"""
from __future__ import annotations

import importlib.util
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from latticeai.services.model_errors import ModelRuntimeError
from latticeai.services.model_catalog import ENGINE_INSTALLERS
from latticeai.services.process_audit import (
    CommandConfirmationError,
    append_process_audit_event,
    command_plan,
    require_command_confirmation,
)


def _progress_payload(*args, **kwargs) -> Dict[str, object]:
    try:
        from latticeai.services.model_runtime import model_download_progress_payload
    except Exception:
        return {}
    return model_download_progress_payload(*args, **kwargs)


LOCAL_SERVER_PROCESSES: Dict[str, subprocess.Popen] = {}
VLLM_METAL_ENV = Path.home() / ".venv-vllm-metal"
VLLM_METAL_BIN = VLLM_METAL_ENV / "bin" / "vllm"
VLLM_METAL_PYTHON = VLLM_METAL_ENV / "bin" / "python"
LMSTUDIO_BUNDLED_CLI = Path("/Applications/LM Studio.app/Contents/Resources/app/.webpack/lms")


def local_binary(binary: str) -> Optional[str]:
    found = shutil.which(binary)
    if found:
        return found
    if platform.system() == "Windows":
        for candidate in windows_binary_candidates(binary):
            if candidate.exists():
                return str(candidate)
    return None


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
    headers: Optional[Dict[str, str]] = None,
    payload: Optional[Dict[str, Any]] = None,
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
    # late to avoid issues
    try:
        from latticeai.services.model_runtime import OPENAI_COMPATIBLE_PROVIDERS  # type: ignore
        prov = OPENAI_COMPATIBLE_PROVIDERS
    except Exception:
        prov = {}
    return (os.getenv("LMSTUDIO_BASE_URL") or (prov.get("lmstudio", {}) or {}).get("base_url", "http://localhost:1234/v1")).rstrip("/")


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
        raise ModelRuntimeError(status_code=400, detail="LM Studio CLI를 찾지 못했습니다. LM Studio를 설치한 뒤 다시 시도하세요.")

    try:
        subprocess.Popen(
            [cli, "server", "start"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as e:
        raise ModelRuntimeError(status_code=500, detail=f"LM Studio 서버 시작 실패: {e}")

    deadline = time.time() + 45
    while time.time() < deadline:
        try:
            _json_request(f"{base_url}/api/v1/models", headers={"Authorization": "Bearer lmstudio"}, timeout=2.5)
            return
        except Exception:
            time.sleep(1)
    raise ModelRuntimeError(status_code=500, detail="LM Studio Local Server를 자동으로 시작하지 못했습니다.")


def ensure_ollama_server() -> None:
    ollama = local_binary("ollama")
    if not ollama:
        raise ModelRuntimeError(status_code=400, detail="Ollama가 설치되지 않았습니다.")
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
    raise ModelRuntimeError(status_code=500, detail="Ollama 서버를 자동으로 시작하지 못했습니다.")


def get_openai_compatible_server_models(provider: str) -> List[str]:
    from latticeai.services.model_runtime import OPENAI_COMPATIBLE_PROVIDERS, get_lmstudio_models

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


def wait_for_openai_compatible_server(provider: str, model_name: Optional[str] = None, timeout: int = 45) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        models = get_openai_compatible_server_models(provider)
        if models and (not model_name or model_name in models):
            return True
        time.sleep(1)
    return False


def ensure_vllm_server(model_name: str) -> None:
    from latticeai.services.model_runtime import download_hf_model, hf_model_dir, hf_model_ready

    served_models = get_openai_compatible_server_models("vllm")
    if model_name in served_models:
        return
    vllm_bin = vllm_executable()
    vllm_metal_py = vllm_metal_python()
    if not vllm_bin and not vllm_metal_py and importlib.util.find_spec("vllm") is None:
        raise ModelRuntimeError(status_code=400, detail="vLLM runtime이 설치되지 않았습니다.")

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
        raise ModelRuntimeError(status_code=409, detail="다른 vLLM 서버가 이미 실행 중입니다. 현재 서버를 종료한 뒤 다시 시도하세요.")

    running = LOCAL_SERVER_PROCESSES.get("vllm")
    if running and running.poll() is None:
        return

    host_args = ["--host", "127.0.0.1", "--port", "8000"]
    if vllm_metal_py:
        command = [vllm_metal_py, "-m", "vllm_metal.server", "--model", model_name, *host_args]
    elif vllm_bin:
        command = [vllm_bin, "serve", str(local_dir), "--served-model-name", model_name, *host_args]
    else:
        command = [sys.executable, "-m", "vllm.entrypoints.openai.api_server", "--model", str(local_dir), "--served-model-name", model_name, *host_args]
    LOCAL_SERVER_PROCESSES["vllm"] = subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    if not wait_for_openai_compatible_server("vllm", model_name, timeout=90):
        raise ModelRuntimeError(status_code=500, detail="vLLM 서버가 모델을 자동 로드하지 못했습니다.")


def ensure_llamacpp_server(model_name: str) -> None:
    from latticeai.services.model_runtime import download_hf_model, hf_model_dir, hf_model_ready

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
        raise ModelRuntimeError(status_code=409, detail="다른 llama.cpp 서버가 이미 실행 중입니다. 현재 서버를 종료한 뒤 다시 시도하세요.")
    if not shutil.which("llama-server"):
        raise ModelRuntimeError(status_code=400, detail="llama.cpp가 설치되지 않았습니다.")
    if not hf_model_ready(model_name, "llamacpp"):
        download_hf_model(model_name, "llamacpp")

    gguf_files = sorted(hf_model_dir(model_name).rglob("*.gguf"))
    if not gguf_files:
        raise ModelRuntimeError(status_code=500, detail="다운로드된 GGUF 파일을 찾지 못했습니다.")

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
        raise ModelRuntimeError(status_code=500, detail="llama.cpp 서버가 모델을 자동 로드하지 못했습니다.")


def pull_ollama_model_with_progress(model_name: str, progress_emit=None) -> Dict[str, object]:
    ollama = local_binary("ollama")
    if not ollama:
        raise ModelRuntimeError(status_code=400, detail="Ollama가 설치되지 않았습니다.")
    if progress_emit:
        progress_emit(_progress_payload(
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
                        progress_emit(_progress_payload(
                            "download",
                            "Ollama 모델 다운로드 중입니다.",
                            percent=last_percent,
                            detail=line[-180:],
                            eta_seconds=None,
                            indeterminate=False,
                        ))
                elif progress_emit:
                    progress_emit(_progress_payload(
                        "download",
                        "Ollama 모델 다운로드 중입니다.",
                        percent=last_percent,
                        detail=line[-180:],
                        eta_seconds=None,
                        indeterminate=last_percent is None,
                    ))
        returncode = process.wait()
    except Exception:
        process.kill()
        raise

    if returncode != 0:
        tail = "\n".join(lines[-12:])
        raise ModelRuntimeError(status_code=500, detail=tail[-2000:] or "Ollama 모델 다운로드 실패")

    if progress_emit:
        progress_emit(_progress_payload(
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


def _engine_install_command(
    engine: str,
    *,
    base_dir: Optional[Path] = None,
) -> tuple[list[str], str, bool]:
    if engine not in ENGINE_INSTALLERS:
        raise ModelRuntimeError(status_code=400, detail="지원하지 않는 엔진입니다.")
    installer = ENGINE_INSTALLERS[engine]
    required_binary = installer.get("requires_binary")
    if required_binary and shutil.which(required_binary) is None:
        raise ModelRuntimeError(status_code=400, detail=f"{required_binary}가 설치되어 있지 않아 자동 설치할 수 없습니다.")
    command = list(installer["command"])

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
    requires_admin = bool(command and command[0] in {"apt", "apt-get", "dnf", "pacman"})
    return command, str(base_dir or Path.cwd()), requires_admin


def engine_install_plan(
    engine: str,
    *,
    base_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    command, cwd, requires_admin = _engine_install_command(engine, base_dir=base_dir)
    return command_plan(
        command,
        name=f"engine:{engine}",
        purpose="engine_install",
        cwd=cwd,
        requires_admin=requires_admin,
        metadata={"engine": engine},
    )


def install_engine(
    engine: str,
    confirmation_token: Optional[str] = None,
    *,
    base_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    from latticeai.services.model_runtime import engine_installed

    command, cwd, _requires_admin = _engine_install_command(engine, base_dir=base_dir)
    plan = engine_install_plan(engine, base_dir=base_dir)
    try:
        require_command_confirmation(command, confirmation_token, cwd=cwd, purpose="engine_install")
    except CommandConfirmationError as exc:
        append_process_audit_event("engine_install", plan=plan, status="denied", error=str(exc))
        raise ModelRuntimeError(
            status_code=403,
            detail={
                "status": "confirmation_required",
                "reason": str(exc),
                "install_plan": plan,
            },
        ) from exc

    run_kwargs = {
        "cwd": cwd,
        "capture_output": True,
        "text": True,
        "timeout": 900,
        "check": False,
    }
    try:
        append_process_audit_event("engine_install", plan=plan, status="started")
        completed = subprocess.run(command, **run_kwargs)
    except subprocess.TimeoutExpired:
        append_process_audit_event("engine_install", plan=plan, status="timeout")
        raise ModelRuntimeError(status_code=408, detail="엔진 설치 시간이 초과되었습니다.")
    except Exception as exc:
        append_process_audit_event("engine_install", plan=plan, status="error", error=str(exc))
        raise
    append_process_audit_event(
        "engine_install",
        plan=plan,
        status="finished",
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )
    result = {
        "engine": engine,
        "command_hash": plan["command_hash"],
        "command_preview": plan["command_preview"],
        "install_plan": plan,
        "returncode": completed.returncode,
        "stdout": completed.stdout[-12000:],
        "stderr": completed.stderr[-12000:],
        "installed": engine_installed(engine),
    }
    ollama = local_binary("ollama")
    if engine == "ollama" and completed.returncode == 0 and ollama:
        already_up = False
        try:
            probe = subprocess.run([ollama, "list"], capture_output=True, timeout=2, check=False)
            already_up = probe.returncode == 0
        except Exception:
            already_up = False
        if already_up:
            result["daemon_started"] = "already_running"
        else:
            daemon_command = [ollama, "serve"]
            daemon_plan = command_plan(
                daemon_command,
                name="engine:ollama:serve",
                purpose="engine_daemon_start",
                metadata={"engine": "ollama"},
            )
            try:
                append_process_audit_event("engine_daemon_start", plan=daemon_plan, status="started")
                subprocess.Popen(
                    daemon_command,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                append_process_audit_event("engine_daemon_start", plan=daemon_plan, status="spawned")
                result["daemon_started"] = True
            except Exception as exc:
                append_process_audit_event("engine_daemon_start", plan=daemon_plan, status="error", error=str(exc))
                logging.warning("ollama serve spawn failed: %s", exc)
                result["daemon_started"] = False
    return result


# --- Smoke test extracted for server decomp wave ---
async def _smoke_test_loaded_model(
    resolution: Any,
    *,
    api_key_override: Optional[str] = None,
    model_router: Any = None,
) -> Dict[str, object]:
    """로드 직후 짧은 채팅 테스트를 돌려 ready_to_chat 여부를 판정한다.

    Cloud models are skipped to avoid cost.
    Failures are swallowed.
    """
    # late imports to avoid circular and keep lattice_brain/latticeai clean
    try:
        from latticeai.services.model_runtime import (
            _LOCAL_SMOKE_ENGINES,
            _SMOKE_PROMPT,
        )
        from latticeai.core.model_compat import (
            ensure_profile as _ensure_compat_profile,
            fast_postprocess as _compat_fast_postprocess,
            classify_smoke_response as _classify_smoke_response,
            record_smoke_result as _record_smoke_result,
        )
        import asyncio
    except Exception as e:
        return {"ok": False, "reason": f"smoke import failed: {e}", "skipped": True}

    if model_router is None:
        return {"ok": False, "reason": "model router is not configured", "skipped": True}

    if (getattr(resolution, "engine", "") or "").lower() not in _LOCAL_SMOKE_ENGINES:
        profile = _ensure_compat_profile(getattr(resolution, "load_id", ""), getattr(resolution, "engine", ""))
        return {
            "ok": True,
            "reason": "skipped (cloud model — smoke test would incur cost)",
            "answer": None,
            "profile": profile.to_dict(),
            "skipped": True,
        }
    try:
        text = await asyncio.wait_for(
            model_router.generate(
                _SMOKE_PROMPT,
                context=None,
                max_tokens=128,
                temperature=0.1,
            ),
            timeout=30,
        )
    except Exception as exc:
        reason = str(exc)[:200] or "generation_failed"
        profile = _record_smoke_result(
            getattr(resolution, "load_id", ""), getattr(resolution, "engine", ""), False, reason, status="failed"
        )
        return {
            "ok": False,
            "status": "failed",
            "reason": reason,
            "answer": None,
            "profile": profile.to_dict(),
        }

    profile = _ensure_compat_profile(getattr(resolution, "load_id", ""), getattr(resolution, "engine", ""))
    cleaned = _compat_fast_postprocess(str(text or ""), profile.to_dict())
    status, reason = _classify_smoke_response(cleaned)
    ok = status != "failed"
    profile = _record_smoke_result(
        getattr(resolution, "load_id", ""), getattr(resolution, "engine", ""), ok, reason, status=status
    )
    return {
        "ok": ok,
        "status": status,
        "reason": reason,
        "answer": cleaned,
        "profile": profile.to_dict(),
    }


__all__ = [
    "ensure_lmstudio_server",
    "ensure_ollama_server",
    "ensure_vllm_server",
    "ensure_llamacpp_server",
    "pull_ollama_model_with_progress",
    "get_ollama_pulled_models",
    "engine_support_status",
    "engine_install_plan",
    "install_engine",
    "_smoke_test_loaded_model",
]
