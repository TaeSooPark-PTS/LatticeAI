"""Model loading, prepare and stream logic extracted from model_runtime for server decomp.

This moves the large prepare_and_load_model and stream functions out of the monolith.
Re-exports will be added in model_runtime for compat.
"""
from __future__ import annotations

import asyncio
import json
import logging
import queue
import subprocess
import threading
from pathlib import Path
from typing import AsyncIterator, Dict, Optional

from fastapi import HTTPException, Request

# Late imports to avoid circulars during extraction
def _get_model_runtime_deps():
    from .model_runtime import (
        _download_allowed,
        _download_block,
        _engine_install_block,
        _friendly_model_runtime_error,
        _ModelResolution,
        _model_runtime_compatibility,
        _smoke_test_loaded_model,
        download_hf_model,
        engine_installed,
        ensure_engine_ready,
        ensure_llamacpp_server,
        ensure_lmstudio_model,
        ensure_ollama_server,
        ensure_vllm_server,
        get_current_user,
        get_lmstudio_models,
        get_ollama_pulled_models,
        get_user_api_key,
        hf_model_dir,
        hf_model_ready,
        local_binary,
        MODEL_ENGINE_ALIASES,
        model_download_progress_payload,
        normalize_local_model_request,
        parse_model_ref,
        pull_ollama_model_with_progress,
        router,
    )
    return {
        "_download_allowed": _download_allowed,
        "_download_block": _download_block,
        "_engine_install_block": _engine_install_block,
        "_friendly_model_runtime_error": _friendly_model_runtime_error,
        "_ModelResolution": _ModelResolution,
        "_model_runtime_compatibility": _model_runtime_compatibility,
        "_smoke_test_loaded_model": _smoke_test_loaded_model,
        "download_hf_model": download_hf_model,
        "engine_installed": engine_installed,
        "ensure_engine_ready": ensure_engine_ready,
        "ensure_llamacpp_server": ensure_llamacpp_server,
        "ensure_lmstudio_model": ensure_lmstudio_model,
        "ensure_ollama_server": ensure_ollama_server,
        "ensure_vllm_server": ensure_vllm_server,
        "get_current_user": get_current_user,
        "get_lmstudio_models": get_lmstudio_models,
        "get_ollama_pulled_models": get_ollama_pulled_models,
        "get_user_api_key": get_user_api_key,
        "hf_model_dir": hf_model_dir,
        "hf_model_ready": hf_model_ready,
        "local_binary": local_binary,
        "MODEL_ENGINE_ALIASES": MODEL_ENGINE_ALIASES,
        "model_download_progress_payload": model_download_progress_payload,
        "normalize_local_model_request": normalize_local_model_request,
        "parse_model_ref": parse_model_ref,
        "pull_ollama_model_with_progress": pull_ollama_model_with_progress,
        "router": router,
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
    deps = _get_model_runtime_deps()
    model_id = deps["normalize_local_model_request"](model_id, engine)
    if not model_id:
        raise HTTPException(status_code=400, detail="모델 식별자가 비어 있습니다.")

    resolution = deps["_ModelResolution"].from_request(
        model_id,
        engine=engine,
        user_email=user_email or deps["get_current_user"](request),
        engine_aliases=deps["MODEL_ENGINE_ALIASES"],
    )

    parsed_provider, parsed_model = deps["parse_model_ref"](model_id)
    if parsed_provider == "mlx":
        parsed_provider = "local_mlx"
    compatibility = deps["_model_runtime_compatibility"](parsed_model, engine=parsed_provider)
    if compatibility.get("supported") is False:
        raise HTTPException(status_code=400, detail=compatibility)

    local_engines = {"local_mlx", "ollama", "vllm", "lmstudio", "llamacpp"}
    install_result: Dict[str, object] = {}
    download_result: Optional[Dict[str, object]] = None

    if parsed_provider in local_engines:
        if not deps["engine_installed"](parsed_provider) and not deps["_download_allowed"](allow_download):
            deps["_engine_install_block"](parsed_provider)
        install_result = deps["ensure_engine_ready"](parsed_provider)

    if parsed_provider == "local_mlx":
        explicit_path = Path(parsed_model).expanduser()
        if not explicit_path.exists() and not deps["hf_model_ready"](parsed_model, "local_mlx"):
            if not deps["_download_allowed"](allow_download):
                deps["_download_block"](parsed_provider, parsed_model)
            download_result = deps["download_hf_model"](parsed_model, "local_mlx")
    elif parsed_provider == "ollama":
        deps["ensure_ollama_server"]()
        ollama = deps["local_binary"]("ollama")
        if not ollama:
            raise HTTPException(status_code=400, detail="Ollama가 설치되지 않았습니다.")
        if parsed_model not in deps["get_ollama_pulled_models"]():
            if not deps["_download_allowed"](allow_download):
                deps["_download_block"](parsed_provider, parsed_model)
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
        if not deps["hf_model_ready"](parsed_model, "vllm") and not deps["_download_allowed"](allow_download):
            deps["_download_block"](parsed_provider, parsed_model)
        deps["ensure_vllm_server"](parsed_model)
        download_result = {"provider": "vllm", "model": parsed_model, "server_ready": True}
    elif parsed_provider == "llamacpp":
        if not deps["hf_model_ready"](parsed_model, "llamacpp") and not deps["_download_allowed"](allow_download):
            deps["_download_block"](parsed_provider, parsed_model)
        deps["ensure_llamacpp_server"](parsed_model)
        download_result = {"provider": "llamacpp", "model": parsed_model, "server_ready": True}
    elif parsed_provider == "lmstudio":
        downloaded = {
            str(item.get("key") or "").strip()
            for item in deps["get_lmstudio_models"]()
            if isinstance(item, dict)
        }
        if parsed_model not in downloaded and not deps["_download_allowed"](allow_download):
            deps["_download_block"](parsed_provider, parsed_model)
        ensured = deps["ensure_lmstudio_model"](parsed_model)
        resolved_model = str(
            ensured.get("instance_id")
            or ensured.get("resolved_model")
            or parsed_model
        ).strip()
        parsed_model = resolved_model
        model_id = f"lmstudio:{resolved_model}"
        download_result = ensured

    effective_email = (user_email or deps["get_current_user"](request) or "").strip()
    user_api_key = deps["get_user_api_key"](effective_email, parsed_provider) if parsed_provider != "local_mlx" else None
    msg = await deps["router"].load_model(
        model_id,
        adapter_path,
        draft_model_id=draft_model_id,
        api_key_override=user_api_key,
        owner=effective_email or None,
    )
    resolution.update_after_load(actual_current=deps["router"].current_model_id)
    smoke_result: Dict[str, object] = {}
    ready_to_chat = True
    compat_status = "ok"
    try:
        smoke_result = await deps["_smoke_test_loaded_model"](resolution, api_key_override=user_api_key)
        ready_to_chat = bool(smoke_result.get("ok"))
        compat_status = str(smoke_result.get("status") or ("ok" if ready_to_chat else "degraded"))
    except Exception as exc:
        logging.warning("smoke test failed for %s: %s", resolution.load_id, exc)
        compat_status = "unknown"
    return {
        "status": "ok",
        "message": msg,
        "model": model_id,
        "current": deps["router"].current_model_id,
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
    deps = _get_model_runtime_deps()
    model_id = deps["normalize_local_model_request"](model_id, engine)
    if not model_id:
        raise HTTPException(status_code=400, detail="모델 식별자가 비어 있습니다.")

    parsed_provider, parsed_model = deps["parse_model_ref"](model_id)
    if parsed_provider == "mlx":
        parsed_provider = "local_mlx"
    compatibility = deps["_model_runtime_compatibility"](parsed_model, engine=parsed_provider)
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
                emit_progress(deps["model_download_progress_payload"](
                    "engine",
                    "실행 엔진을 확인하는 중입니다.",
                    percent=2,
                    indeterminate=True,
                ))
                if not deps["engine_installed"](parsed_provider) and not deps["_download_allowed"](allow_download):
                    deps["_engine_install_block"](parsed_provider)
                install_result = deps["ensure_engine_ready"](parsed_provider)
                emit_progress(deps["model_download_progress_payload"](
                    "engine",
                    "실행 엔진 준비가 완료되었습니다.",
                    percent=10,
                    indeterminate=False,
                ))

            if parsed_provider == "local_mlx":
                explicit_path = Path(parsed_model).expanduser()
                if explicit_path.exists():
                    download_result = {"model": parsed_model, "path": str(explicit_path), "cached": True}
                    emit_progress(deps["model_download_progress_payload"](
                        "download",
                        "로컬 모델 경로를 확인했습니다.",
                        percent=100,
                        detail=str(explicit_path),
                        eta_seconds=0,
                    ))
                elif not deps["hf_model_ready"](parsed_model, "local_mlx"):
                    if not deps["_download_allowed"](allow_download):
                        deps["_download_block"](parsed_provider, parsed_model)
                    download_result = deps["download_hf_model"](parsed_model, "local_mlx", progress_emit=emit_progress)
                else:
                    download_result = {"model": parsed_model, "path": str(deps["hf_model_dir"](parsed_model)), "cached": True}
                    emit_progress(deps["model_download_progress_payload"](
                        "download",
                        "이미 다운로드된 모델을 확인했습니다.",
                        percent=100,
                        eta_seconds=0,
                    ))
            elif parsed_provider == "ollama":
                emit_progress(deps["model_download_progress_payload"](
                    "engine",
                    "Ollama 서버를 확인하는 중입니다.",
                    percent=12,
                    indeterminate=True,
                ))
                deps["ensure_ollama_server"]()
                if parsed_model not in deps["get_ollama_pulled_models"]():
                    if not deps["_download_allowed"](allow_download):
                        deps["_download_block"](parsed_provider, parsed_model)
                    download_result = deps["pull_ollama_model_with_progress"](parsed_model, progress_emit=emit_progress)
                else:
                    download_result = {"provider": "ollama", "model": parsed_model, "cached": True}
                    emit_progress(deps["model_download_progress_payload"](
                        "download",
                        "이미 다운로드된 Ollama 모델을 확인했습니다.",
                        percent=100,
                        detail=parsed_model,
                        eta_seconds=0,
                    ))
            elif parsed_provider == "vllm":
                if not deps["hf_model_ready"](parsed_model, "vllm"):
                    if not deps["_download_allowed"](allow_download):
                        deps["_download_block"](parsed_provider, parsed_model)
                    download_result = deps["download_hf_model"](parsed_model, "vllm", progress_emit=emit_progress)
                else:
                    download_result = {"provider": "vllm", "model": parsed_model, "cached": True}
                    emit_progress(deps["model_download_progress_payload"](
                        "download",
                        "이미 다운로드된 모델을 확인했습니다.",
                        percent=100,
                        detail=parsed_model,
                        eta_seconds=0,
                    ))
                emit_progress(deps["model_download_progress_payload"](
                    "server",
                    "vLLM 서버를 시작하는 중입니다.",
                    percent=92,
                    indeterminate=True,
                ))
                deps["ensure_vllm_server"](parsed_model)
                download_result = {**(download_result or {}), "provider": "vllm", "model": parsed_model, "server_ready": True}
            elif parsed_provider == "llamacpp":
                if not deps["hf_model_ready"](parsed_model, "llamacpp"):
                    if not deps["_download_allowed"](allow_download):
                        deps["_download_block"](parsed_provider, parsed_model)
                    download_result = deps["download_hf_model"](parsed_model, "llamacpp", progress_emit=emit_progress)
                else:
                    download_result = {"provider": "llamacpp", "model": parsed_model, "cached": True}
                    emit_progress(deps["model_download_progress_payload"](
                        "download",
                        "이미 다운로드된 GGUF 모델을 확인했습니다.",
                        percent=100,
                        detail=parsed_model,
                        eta_seconds=0,
                    ))
                emit_progress(deps["model_download_progress_payload"](
                    "server",
                    "llama.cpp 서버를 시작하는 중입니다.",
                    percent=92,
                    indeterminate=True,
                ))
                deps["ensure_llamacpp_server"](parsed_model)
                download_result = {**(download_result or {}), "provider": "llamacpp", "model": parsed_model, "server_ready": True}
            elif parsed_provider == "lmstudio":
                downloaded = {
                    str(item.get("key") or "").strip()
                    for item in deps["get_lmstudio_models"]()
                    if isinstance(item, dict)
                }
                if parsed_model not in downloaded and not deps["_download_allowed"](allow_download):
                    deps["_download_block"](parsed_provider, parsed_model)
                emit_progress(deps["model_download_progress_payload"](
                    "download",
                    "LM Studio 모델을 확인하는 중입니다.",
                    percent=35,
                    indeterminate=True,
                ))
                ensured = deps["ensure_lmstudio_model"](parsed_model)
                resolved_model = str(
                    ensured.get("instance_id")
                    or ensured.get("resolved_model")
                    or parsed_model
                ).strip()
                prepared_model_name = resolved_model
                prepared_model_id = f"lmstudio:{resolved_model}"
                download_result = ensured
            else:
                emit_progress(deps["model_download_progress_payload"](
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
                "detail": deps["_friendly_model_runtime_error"](exc, model_id=model_id, engine=parsed_provider),
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

    yield sse_event("progress", deps["model_download_progress_payload"](
        "load",
        "모델을 메모리에 로드하는 중입니다.",
        percent=96,
        indeterminate=True,
    ))

    effective_email = (user_email or deps["get_current_user"](request) or "").strip()
    user_api_key = deps["get_user_api_key"](effective_email, prepared_provider) if prepared_provider != "local_mlx" else None
    msg = await deps["router"].load_model(
        prepared_model_id,
        None,
        draft_model_id=None,
        api_key_override=user_api_key,
        owner=effective_email or None,
    )
    resolution_stream = deps["_ModelResolution"].from_request(
        prepared_model_id,
        engine=prepared_provider,
        user_email=effective_email or None,
        engine_aliases=deps["MODEL_ENGINE_ALIASES"],
    )
    resolution_stream.update_after_load(actual_current=deps["router"].current_model_id)
    yield sse_event("progress", deps["model_download_progress_payload"](
        "smoke_test",
        "채팅 호환성 테스트 중입니다.",
        percent=98,
        indeterminate=True,
    ))
    smoke_result: Dict[str, object] = {}
    ready_to_chat = True
    compat_status = "ok"
    try:
        smoke_result = await deps["_smoke_test_loaded_model"](resolution_stream, api_key_override=user_api_key)
        ready_to_chat = bool(smoke_result.get("ok"))
        compat_status = str(smoke_result.get("status") or ("ok" if ready_to_chat else "degraded"))
    except Exception as exc:
        logging.warning("smoke test (stream) failed for %s: %s", resolution_stream.load_id, exc)
        compat_status = "unknown"
    result = {
        "status": "ok",
        "message": msg,
        "model": prepared_model_id,
        "current": deps["router"].current_model_id,
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
    yield sse_event("progress", deps["model_download_progress_payload"](
        "done",
        "모델 준비가 완료되었습니다.",
        percent=100,
        eta_seconds=0,
    ))
    yield sse_event("done", result)


# To maintain exact public API, model_runtime will re-export these.
