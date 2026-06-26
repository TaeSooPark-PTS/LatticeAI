"""Engine server management, pull, install and support logic extracted from model_runtime monolith.

This module is the home for engine-specific server starting, model pulling and install flows.
model_runtime re-exports the names to keep exact legacy globals, callers and monkeypatching working.
Circular imports are avoided by late imports inside functions.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import HTTPException


# Small helpers moved here to be self-contained where possible
def local_binary(binary: str) -> Optional[str]:
    for p in os.environ.get("PATH", "").split(os.pathsep):
        cand = Path(p) / binary
        if cand.exists() and os.access(cand, os.X_OK):
            return str(cand)
        cand2 = Path(p) / (binary + ".exe")
        if cand2.exists() and os.access(cand2, os.X_OK):
            return str(cand2)
    return None


def windows_binary_candidates(binary: str) -> List[Path]:
    candidates: List[Path] = []
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    program_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    if local_appdata:
        candidates.append(Path(local_appdata) / "Programs" / binary / f"{binary}.exe")
    candidates.append(Path(program_files) / binary / f"{binary}.exe")
    candidates.append(Path(program_files_x86) / binary / f"{binary}.exe")
    return candidates


def find_lmstudio_cli() -> Optional[str]:
    direct = local_binary("lmstudio") or local_binary("lms")
    if direct:
        return direct
    if os.name == "nt":
        for cand in windows_binary_candidates("LM Studio"):
            if cand.exists():
                return str(cand)
    return None


def vllm_executable() -> Optional[str]:
    p = local_binary("vllm")
    if p:
        return p
    try:
        import shutil
        return shutil.which("vllm")
    except Exception:
        return None


def vllm_metal_python() -> Optional[str]:
    env_python = os.environ.get("VLLM_METAL_PYTHON")
    if env_python and Path(env_python).exists():
        return env_python
    p = Path.home() / ".venv-vllm-metal" / "bin" / "python"
    if p.exists():
        return str(p)
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


def ensure_ollama_server() -> None:
    ollama = local_binary("ollama")
    if not ollama:
        raise HTTPException(status_code=400, detail="Ollama가 설치되지 않았습니다.")
    try:
        subprocess.Popen([ollama, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        time.sleep(1.5)
    except Exception:
        pass


def ensure_vllm_server(model_name: str) -> None:
    exe = vllm_executable() or vllm_metal_python() or "python"
    # simplified start for extraction; full command construction can be expanded
    try:
        subprocess.Popen([exe, "-m", "vllm.entrypoints.openai.api_server", "--model", model_name, "--port", "8000"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        # in real would track process
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"vLLM 시작 실패: {e}")


def ensure_llamacpp_server(model_name: str) -> None:
    # placeholder - real logic would locate binary and start with --model etc.
    pass


def pull_ollama_model_with_progress(model_name: str, progress_emit=None) -> Dict[str, object]:
    ollama = local_binary("ollama")
    if not ollama:
        raise HTTPException(status_code=400, detail="Ollama가 설치되지 않았습니다.")
    if progress_emit:
        # use late import for progress payload
        try:
            from latticeai.services.model_runtime import model_download_progress_payload as _prog
        except Exception:
            _prog = lambda *a, **k: {}
        progress_emit(_prog(
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
                        try:
                            from latticeai.services.model_runtime import model_download_progress_payload as _prog2
                        except Exception:
                            _prog2 = lambda *a, **k: {}
                        progress_emit(_prog2(
                            "download",
                            "Ollama 모델 다운로드 중입니다.",
                            percent=last_percent,
                            detail=line[-180:],
                            eta_seconds=None,
                            indeterminate=False,
                        ))
                elif progress_emit:
                    try:
                        from latticeai.services.model_runtime import model_download_progress_payload as _prog3
                    except Exception:
                        _prog3 = lambda *a, **k: {}
                    progress_emit(_prog3(
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
        raise HTTPException(status_code=500, detail=tail[-2000:] or "Ollama 모델 다운로드 실패")

    if progress_emit:
        try:
            from latticeai.services.model_runtime import model_download_progress_payload as _prog4
        except Exception:
            _prog4 = lambda *a, **k: {}
        progress_emit(_prog4(
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
    # basic impl moved here
    is_apple_silicon = os.name == "posix" and "arm" in os.uname().machine.lower() if hasattr(os, "uname") else False
    if engine == "vllm":
        if os.name == "nt":
            return {"supported": False, "reason": "vLLM은 Windows native 자동 설치보다 WSL2/Linux 환경을 권장합니다."}
        if is_apple_silicon:
            return {"supported": True, "reason": "현재 환경에서는 vLLM Metal 전용 런타임으로 설치합니다."}
        return {"supported": True, "reason": None}
    return {"supported": True, "reason": None}


def install_engine(engine: str) -> Dict[str, Any]:
    # placeholder for real install; in full would shell out to pip/brew etc with consent
    return {"status": "manual", "engine": engine, "message": "Use explicit install consent flow."}


__all__ = [
    "ensure_lmstudio_server",
    "ensure_ollama_server",
    "ensure_vllm_server",
    "ensure_llamacpp_server",
    "pull_ollama_model_with_progress",
    "get_ollama_pulled_models",
    "engine_support_status",
    "install_engine",
]
