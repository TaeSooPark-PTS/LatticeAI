from __future__ import annotations

import asyncio
import logging
import queue
import subprocess
import threading
from functools import partial
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Dict, Optional

from latticeai.core.sse import sse_frame
from latticeai.models.router.errors import MODEL_RUNTIME_BOUNDARY_ERRORS

from .model_errors import ModelRuntimeError

ProgressEmitter = Optional[Callable[[Dict[str, object]], None]]
_LOCAL_ENGINES = {"local_mlx", "omlx", "ollama", "vllm", "lmstudio", "llamacpp"}


def _get_model_runtime_deps(runtime_state: Any) -> Dict[str, Any]:
    from .model_runtime import (
        MODEL_ENGINE_ALIASES,
        _download_allowed,
        _download_block,
        _engine_install_block,
        _friendly_model_runtime_error,
        _model_runtime_compatibility,
        _ModelResolution,
        _smoke_test_loaded_model,
        download_hf_model,
        engine_installed,
        ensure_engine_ready,
        ensure_llamacpp_server,
        ensure_lmstudio_model,
        ensure_ollama_server,
        ensure_omlx_server,
        ensure_vllm_server,
        get_lmstudio_models,
        get_ollama_pulled_models,
        hf_model_dir,
        hf_model_ready,
        local_binary,
        model_download_progress_payload,
        normalize_local_model_request,
        parse_model_ref,
        pull_ollama_model_with_progress,
    )

    return {
        "_download_allowed": partial(_download_allowed, state=runtime_state),
        "_download_block": _download_block,
        "_engine_install_block": _engine_install_block,
        "_friendly_model_runtime_error": _friendly_model_runtime_error,
        "_ModelResolution": _ModelResolution,
        "_model_runtime_compatibility": _model_runtime_compatibility,
        "_smoke_test_loaded_model": partial(
            _smoke_test_loaded_model, state=runtime_state
        ),
        "download_hf_model": download_hf_model,
        "engine_installed": engine_installed,
        "ensure_engine_ready": partial(ensure_engine_ready, state=runtime_state),
        "ensure_llamacpp_server": ensure_llamacpp_server,
        "ensure_lmstudio_model": ensure_lmstudio_model,
        "ensure_ollama_server": ensure_ollama_server,
        "ensure_omlx_server": ensure_omlx_server,
        "ensure_vllm_server": ensure_vllm_server,
        "get_current_user": runtime_state.get_current_user,
        "get_lmstudio_models": get_lmstudio_models,
        "get_ollama_pulled_models": get_ollama_pulled_models,
        "get_user_api_key": runtime_state.get_user_api_key,
        "hf_model_dir": hf_model_dir,
        "hf_model_ready": hf_model_ready,
        "local_binary": local_binary,
        "MODEL_ENGINE_ALIASES": MODEL_ENGINE_ALIASES,
        "model_download_progress_payload": model_download_progress_payload,
        "normalize_local_model_request": normalize_local_model_request,
        "parse_model_ref": parse_model_ref,
        "pull_ollama_model_with_progress": pull_ollama_model_with_progress,
        "router": runtime_state.router,
    }


def _emit(
    deps: Any, emit_progress: ProgressEmitter, stage: str, message: str, **kwargs: Any
) -> None:
    if emit_progress is not None:
        emit_progress(deps["model_download_progress_payload"](stage, message, **kwargs))


def _apply_gated_fallback_result(
    download_result: Any,
    parsed_provider: str,
    current_model: str,
    current_model_id: str,
) -> tuple[str, str]:
    if isinstance(download_result, dict) and download_result.get("gated_fallback"):
        fallback_repo = str(download_result.get("fallback_repo") or "").strip()
        if fallback_repo:
            next_id = (
                fallback_repo
                if parsed_provider == "local_mlx"
                else f"{parsed_provider}:{fallback_repo}"
            )
            return fallback_repo, next_id
    return current_model, current_model_id


def _prepare_mlx_or_hf(
    deps: Any,
    parsed_provider: str,
    parsed_model: str,
    model_id: str,
    allow_download: bool,
    emit_progress: ProgressEmitter,
) -> tuple[str, str, Optional[Dict[str, object]]]:
    explicit_path = Path(parsed_model).expanduser()
    if explicit_path.exists():
        if emit_progress is None:
            return parsed_model, model_id, None
        _emit(
            deps,
            emit_progress,
            "download",
            "로컬 모델 경로를 확인했습니다.",
            percent=100,
            detail=str(explicit_path),
            eta_seconds=0,
        )
        return (
            parsed_model,
            model_id,
            {"model": parsed_model, "path": str(explicit_path), "cached": True},
        )
    if not deps["hf_model_ready"](parsed_model, "local_mlx"):
        if not deps["_download_allowed"](allow_download):
            deps["_download_block"](parsed_provider, parsed_model)
        dl_kwargs = (
            {"progress_emit": emit_progress} if emit_progress is not None else {}
        )
        download_result = deps["download_hf_model"](
            parsed_model, "local_mlx", **dl_kwargs
        )
        next_model, next_id = _apply_gated_fallback_result(
            download_result, parsed_provider, parsed_model, model_id
        )
        return next_model, next_id, download_result
    if emit_progress is None:
        return parsed_model, model_id, None
    _emit(
        deps,
        emit_progress,
        "download",
        "이미 다운로드된 모델을 확인했습니다.",
        percent=100,
        eta_seconds=0,
    )
    return (
        parsed_model,
        model_id,
        {
            "model": parsed_model,
            "path": str(deps["hf_model_dir"](parsed_model)),
            "cached": True,
        },
    )


def _prepare_ollama(
    deps: Any,
    parsed_model: str,
    model_id: str,
    allow_download: bool,
    emit_progress: ProgressEmitter,
) -> tuple[str, str, Optional[Dict[str, object]]]:
    _emit(
        deps,
        emit_progress,
        "engine",
        "Ollama 서버를 확인하는 중입니다.",
        percent=12,
        indeterminate=True,
    )
    deps["ensure_ollama_server"]()
    if emit_progress is None:
        ollama = deps["local_binary"]("ollama")
        if not ollama:
            raise ModelRuntimeError(
                status_code=400, detail="Ollama가 설치되지 않았습니다."
            )
        if parsed_model not in deps["get_ollama_pulled_models"]():
            if not deps["_download_allowed"](allow_download):
                deps["_download_block"]("ollama", parsed_model)
            completed = subprocess.run(
                [ollama, "pull", parsed_model],
                capture_output=True,
                text=True,
                timeout=900,
                check=False,
            )
            if completed.returncode != 0:
                raise ModelRuntimeError(
                    status_code=500,
                    detail=completed.stderr[-2000:] or "Ollama 모델 다운로드 실패",
                )
            return (
                parsed_model,
                model_id,
                {
                    "provider": "ollama",
                    "model": parsed_model,
                    "returncode": completed.returncode,
                },
            )
        return parsed_model, model_id, None

    if parsed_model not in deps["get_ollama_pulled_models"]():
        if not deps["_download_allowed"](allow_download):
            deps["_download_block"]("ollama", parsed_model)
        return (
            parsed_model,
            model_id,
            deps["pull_ollama_model_with_progress"](
                parsed_model, progress_emit=emit_progress
            ),
        )
    _emit(
        deps,
        emit_progress,
        "download",
        "이미 다운로드된 Ollama 모델을 확인했습니다.",
        percent=100,
        detail=parsed_model,
        eta_seconds=0,
    )
    return (
        parsed_model,
        model_id,
        {"provider": "ollama", "model": parsed_model, "cached": True},
    )


def _prepare_omlx(
    deps: Any,
    parsed_model: str,
    allow_download: bool,
    emit_progress: ProgressEmitter,
) -> tuple[str, str, Dict[str, object]]:
    download_result: Dict[str, object] = {}
    if not deps["hf_model_ready"](parsed_model, "omlx"):
        if not deps["_download_allowed"](allow_download):
            deps["_download_block"]("omlx", parsed_model)
        if emit_progress is not None:
            download_result = (
                deps["download_hf_model"](
                    parsed_model, "local_mlx", progress_emit=emit_progress
                )
                or {}
            )
    elif emit_progress is not None:
        download_result = {"provider": "omlx", "model": parsed_model, "cached": True}
        _emit(
            deps,
            emit_progress,
            "download",
            "이미 다운로드된 모델을 확인했습니다.",
            percent=100,
            detail=parsed_model,
            eta_seconds=0,
        )
    _emit(
        deps,
        emit_progress,
        "server",
        "oMLX 서버를 시작하는 중입니다.",
        percent=92,
        indeterminate=True,
    )
    served_model = deps["ensure_omlx_server"](parsed_model)
    return (
        served_model,
        f"omlx:{served_model}",
        {
            **download_result,
            "provider": "omlx",
            "model": served_model,
            "server_ready": True,
        },
    )


def _prepare_hf_server_engine(
    deps: Any,
    provider: str,
    ensure_key: str,
    cached_msg: str,
    server_msg: str,
    parsed_model: str,
    model_id: str,
    allow_download: bool,
    emit_progress: ProgressEmitter,
) -> tuple[str, str, Dict[str, object]]:
    download_result: Dict[str, object] = {}
    ready = deps["hf_model_ready"](parsed_model, provider)
    if not ready:
        if not deps["_download_allowed"](allow_download):
            deps["_download_block"](provider, parsed_model)
        if emit_progress is not None:
            download_result = (
                deps["download_hf_model"](
                    parsed_model, provider, progress_emit=emit_progress
                )
                or {}
            )
            parsed_model, model_id = _apply_gated_fallback_result(
                download_result, provider, parsed_model, model_id
            )
    elif emit_progress is not None:
        download_result = {"provider": provider, "model": parsed_model, "cached": True}
        _emit(
            deps,
            emit_progress,
            "download",
            cached_msg,
            percent=100,
            detail=parsed_model,
            eta_seconds=0,
        )
    _emit(deps, emit_progress, "server", server_msg, percent=92, indeterminate=True)
    deps[ensure_key](parsed_model)
    return (
        parsed_model,
        model_id,
        {
            **download_result,
            "provider": provider,
            "model": parsed_model,
            "server_ready": True,
        },
    )


def _prepare_lmstudio(
    deps: Any,
    parsed_model: str,
    allow_download: bool,
    emit_progress: ProgressEmitter,
) -> tuple[str, str, Dict[str, object]]:
    downloaded = {
        str(item.get("key") or "").strip()
        for item in deps["get_lmstudio_models"]()
        if isinstance(item, dict)
    }
    if parsed_model not in downloaded and not deps["_download_allowed"](allow_download):
        deps["_download_block"]("lmstudio", parsed_model)
    _emit(
        deps,
        emit_progress,
        "download",
        "LM Studio 모델을 확인하는 중입니다.",
        percent=35,
        indeterminate=True,
    )
    ensured = deps["ensure_lmstudio_model"](parsed_model)
    resolved_model = str(
        ensured.get("instance_id") or ensured.get("resolved_model") or parsed_model
    ).strip()
    return resolved_model, f"lmstudio:{resolved_model}", ensured


def _prepare_model_sources(
    deps: Any,
    parsed_provider: str,
    parsed_model: str,
    model_id: str,
    allow_download: bool,
    emit_progress: ProgressEmitter = None,
) -> Dict[str, Any]:
    install_result: Dict[str, object] = {}
    download_result: Optional[Dict[str, object]] = None

    if parsed_provider in _LOCAL_ENGINES:
        _emit(
            deps,
            emit_progress,
            "engine",
            "실행 엔진을 확인하는 중입니다.",
            percent=2,
            indeterminate=True,
        )
        if not deps["engine_installed"](parsed_provider) and not deps[
            "_download_allowed"
        ](allow_download):
            deps["_engine_install_block"](parsed_provider)
        install_result = deps["ensure_engine_ready"](parsed_provider)
        _emit(
            deps,
            emit_progress,
            "engine",
            "실행 엔진 준비가 완료되었습니다.",
            percent=10,
            indeterminate=False,
        )

    if parsed_provider in {"local_mlx", "huggingface"}:
        parsed_model, model_id, download_result = _prepare_mlx_or_hf(
            deps, parsed_provider, parsed_model, model_id, allow_download, emit_progress
        )
    elif parsed_provider == "ollama":
        parsed_model, model_id, download_result = _prepare_ollama(
            deps, parsed_model, model_id, allow_download, emit_progress
        )
    elif parsed_provider == "omlx":
        parsed_model, model_id, download_result = _prepare_omlx(
            deps, parsed_model, allow_download, emit_progress
        )
    elif parsed_provider == "vllm":
        parsed_model, model_id, download_result = _prepare_hf_server_engine(
            deps,
            "vllm",
            "ensure_vllm_server",
            "이미 다운로드된 모델을 확인했습니다.",
            "vLLM 서버를 시작하는 중입니다.",
            parsed_model,
            model_id,
            allow_download,
            emit_progress,
        )
    elif parsed_provider == "llamacpp":
        parsed_model, model_id, download_result = _prepare_hf_server_engine(
            deps,
            "llamacpp",
            "ensure_llamacpp_server",
            "이미 다운로드된 GGUF 모델을 확인했습니다.",
            "llama.cpp 서버를 시작하는 중입니다.",
            parsed_model,
            model_id,
            allow_download,
            emit_progress,
        )
    elif parsed_provider == "lmstudio":
        parsed_model, model_id, download_result = _prepare_lmstudio(
            deps, parsed_model, allow_download, emit_progress
        )
    else:
        _emit(
            deps,
            emit_progress,
            "engine",
            "모델 연결을 준비하는 중입니다.",
            percent=30,
            indeterminate=True,
        )

    return {
        "install_result": install_result,
        "download_result": download_result,
        "parsed_model": parsed_model,
        "model_id": model_id,
    }


async def prepare_and_load_model(
    model_id: str,
    request: Any,
    engine: Optional[str] = None,
    user_email: Optional[str] = None,
    adapter_path: Optional[str] = None,
    draft_model_id: Optional[str] = None,
    allow_download: bool = False,
    *,
    runtime_state: Any,
) -> Dict[str, object]:
    deps = _get_model_runtime_deps(runtime_state)
    model_id = deps["normalize_local_model_request"](model_id, engine)
    if not model_id:
        raise ModelRuntimeError(status_code=400, detail="모델 식별자가 비어 있습니다.")

    resolution = deps["_ModelResolution"].from_request(
        model_id,
        engine=engine,
        user_email=user_email or deps["get_current_user"](request),
        engine_aliases=deps["MODEL_ENGINE_ALIASES"],
    )

    parsed_provider, parsed_model = deps["parse_model_ref"](model_id)
    if parsed_provider == "mlx":
        parsed_provider = "local_mlx"
    compatibility = deps["_model_runtime_compatibility"](
        parsed_model, engine=parsed_provider
    )
    if compatibility.get("supported") is False:
        raise ModelRuntimeError(status_code=400, detail=compatibility)

    sources = await asyncio.to_thread(
        _prepare_model_sources,
        deps,
        parsed_provider,
        parsed_model,
        model_id,
        allow_download,
    )
    install_result = sources["install_result"]
    download_result = sources["download_result"]
    parsed_model = sources["parsed_model"]
    model_id = sources["model_id"]

    effective_email = (user_email or deps["get_current_user"](request) or "").strip()
    user_api_key = (
        deps["get_user_api_key"](effective_email, parsed_provider)
        if parsed_provider != "local_mlx"
        else None
    )
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
        smoke_result = await deps["_smoke_test_loaded_model"](
            resolution, api_key_override=user_api_key
        )
        ready_to_chat = bool(smoke_result.get("ok"))
        compat_status = str(
            smoke_result.get("status") or ("ok" if ready_to_chat else "degraded")
        )
    except MODEL_RUNTIME_BOUNDARY_ERRORS as exc:
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
        "downloaded": bool(
            download_result
            and not (
                isinstance(download_result, dict) and download_result.get("cached")
            )
        ),
        "loaded": True,
        "ready_to_chat": ready_to_chat,
        "compatibility_status": compat_status,
        "smoke_test": smoke_result,
    }


def sse_event(event: str, data: Dict[str, object]) -> str:
    return sse_frame(event, data)


async def prepare_and_load_model_stream(
    model_id: str,
    request: Any,
    engine: Optional[str] = None,
    user_email: Optional[str] = None,
    allow_download: bool = False,
    *,
    runtime_state: Any,
) -> AsyncIterator[str]:
    deps = _get_model_runtime_deps(runtime_state)
    model_id = deps["normalize_local_model_request"](model_id, engine)
    if not model_id:
        raise ModelRuntimeError(status_code=400, detail="모델 식별자가 비어 있습니다.")

    parsed_provider, parsed_model = deps["parse_model_ref"](model_id)
    if parsed_provider == "mlx":
        parsed_provider = "local_mlx"
    compatibility = deps["_model_runtime_compatibility"](
        parsed_model, engine=parsed_provider
    )
    if compatibility.get("supported") is False:
        raise ModelRuntimeError(status_code=400, detail=compatibility)

    work_queue: "queue.Queue[Dict[str, Any]]" = queue.Queue()
    work_result: Dict[str, object] = {}

    def emit_progress(payload: Dict[str, object]) -> None:
        work_queue.put({"kind": "progress", "data": payload})

    def blocking_prepare() -> None:
        try:
            sources = _prepare_model_sources(
                deps,
                parsed_provider,
                parsed_model,
                model_id,
                allow_download,
                emit_progress=emit_progress,
            )
            work_result.update(
                {
                    "model_id": sources["model_id"],
                    "parsed_provider": parsed_provider,
                    "parsed_model": sources["parsed_model"],
                    "install_result": sources["install_result"],
                    "download_result": sources["download_result"],
                }
            )
            work_queue.put({"kind": "done"})
        except ModelRuntimeError as exc:
            work_queue.put(
                {"kind": "error", "status_code": exc.status_code, "detail": exc.detail}
            )
        except Exception as exc:
            logging.exception("model prepare stream worker failed")
            work_queue.put(
                {
                    "kind": "error",
                    "status_code": 500,
                    "detail": deps["_friendly_model_runtime_error"](
                        exc, model_id=model_id, engine=parsed_provider
                    ),
                }
            )

    from latticeai.services.model_runtime.download import clear_download_cancel

    clear_download_cancel(model_id)
    clear_download_cancel(parsed_model)

    worker = threading.Thread(target=blocking_prepare, daemon=True)
    worker.start()

    cancelled = False
    async for ev in _iter_prepare_queue_events(
        work_queue, request, model_id, parsed_model
    ):
        if ev == "__cancelled__":
            cancelled = True
            break
        yield ev
    if cancelled:
        return

    prepared_model_id = str(work_result.get("model_id") or model_id)
    prepared_provider = str(work_result.get("parsed_provider") or parsed_provider)
    prepared_model_name = str(work_result.get("parsed_model") or parsed_model)
    install_result = work_result.get("install_result") or {}
    download_result = work_result.get("download_result")

    async for ev in _finalize_loaded_stream_events(
        deps,
        request=request,
        user_email=user_email,
        prepared_model_id=prepared_model_id,
        prepared_provider=prepared_provider,
        prepared_model_name=prepared_model_name,
        install_result=install_result,
        download_result=download_result,
    ):
        yield ev


async def _finalize_loaded_stream_events(
    deps: Dict[str, Any],
    *,
    request: Any,
    user_email: Optional[str],
    prepared_model_id: str,
    prepared_provider: str,
    prepared_model_name: str,
    install_result: Any,
    download_result: Any,
) -> AsyncIterator[str]:
    embed_done = _maybe_build_embedding_done_payload(
        deps,
        prepared_model_id=prepared_model_id,
        prepared_provider=prepared_provider,
        prepared_model_name=prepared_model_name,
        install_result=install_result,
        download_result=download_result,
    )
    if embed_done is not None:
        for ev in embed_done:
            yield ev
        return

    yield sse_event(
        "progress",
        deps["model_download_progress_payload"](
            "load",
            "모델을 메모리에 로드하는 중입니다.",
            percent=96,
            indeterminate=True,
        ),
    )

    effective_email = (user_email or deps["get_current_user"](request) or "").strip()
    user_api_key = (
        deps["get_user_api_key"](effective_email, prepared_provider)
        if prepared_provider != "local_mlx"
        else None
    )
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
    yield sse_event(
        "progress",
        deps["model_download_progress_payload"](
            "smoke_test",
            "채팅 호환성 테스트 중입니다.",
            percent=98,
            indeterminate=True,
        ),
    )
    smoke_result: Dict[str, object] = {}
    ready_to_chat = True
    compat_status = "ok"
    try:
        smoke_result = await deps["_smoke_test_loaded_model"](
            resolution_stream, api_key_override=user_api_key
        )
        ready_to_chat = bool(smoke_result.get("ok"))
        compat_status = str(
            smoke_result.get("status") or ("ok" if ready_to_chat else "degraded")
        )
    except MODEL_RUNTIME_BOUNDARY_ERRORS as exc:
        logging.warning(
            "smoke test (stream) failed for %s: %s", resolution_stream.load_id, exc
        )
        compat_status = "unknown"
    result = {
        "status": "ok",
        "message": msg,
        "model": prepared_model_id,
        "current": deps["router"].current_model_id,
        "engine": prepared_provider,
        "installed_now": bool(
            isinstance(install_result, dict) and install_result.get("installed_now")
        ),
        "download": download_result,
        "resolution": resolution_stream.to_dict(),
        "downloaded": bool(
            download_result
            and not (
                isinstance(download_result, dict) and download_result.get("cached")
            )
        ),
        "loaded": True,
        "ready_to_chat": ready_to_chat,
        "compatibility_status": compat_status,
        "smoke_test": smoke_result,
    }
    yield sse_event(
        "progress",
        deps["model_download_progress_payload"](
            "done",
            "모델 준비가 완료되었습니다.",
            percent=100,
            eta_seconds=0,
        ),
    )
    yield sse_event("done", result)


async def _iter_prepare_queue_events(
    work_queue: "queue.Queue[Dict[str, Any]]",
    request: Any,
    model_id: str,
    parsed_model: str,
) -> AsyncIterator[str]:
    from latticeai.services.model_runtime.download import request_download_cancel

    def _poll() -> Optional[Dict[str, Any]]:
        try:
            return work_queue.get(timeout=0.25)
        except queue.Empty:
            return None

    while True:
        item = await asyncio.to_thread(_poll)
        if item is None:
            if request is not None and hasattr(request, "is_disconnected"):
                if await request.is_disconnected():
                    request_download_cancel(model_id)
                    request_download_cancel(parsed_model)
                    yield "__cancelled__"
                    return
            continue
        kind = item.get("kind")
        if kind == "progress":
            yield sse_event("progress", item["data"])
        elif kind == "error":
            raise ModelRuntimeError(
                status_code=int(item.get("status_code") or 500),
                detail=item.get("detail") or "모델 준비에 실패했습니다.",
            )
        elif kind == "done":
            return


def _maybe_build_embedding_done_payload(
    deps: Dict[str, Any],
    *,
    prepared_model_id: str,
    prepared_provider: str,
    prepared_model_name: str,
    install_result: Any,
    download_result: Any,
) -> Optional[list[str]]:
    from latticeai.services.embedding_recommendation import (
        activate_downloaded_embedding_model,
        is_embedding_model_id,
    )

    if not is_embedding_model_id(prepared_model_name):
        return None
    transition = activate_downloaded_embedding_model(
        prepared_model_name, provider=prepared_provider
    )
    is_cached = isinstance(download_result, dict) and bool(
        download_result.get("cached")
    )
    result = {
        "status": "ok",
        "message": f"임베딩 모델 준비 및 시맨틱 전환 완료: {prepared_model_name}",
        "model": prepared_model_id,
        "current": deps["router"].current_model_id,
        "engine": prepared_provider,
        "installed_now": bool(
            isinstance(install_result, dict) and install_result.get("installed_now")
        ),
        "download": download_result,
        "downloaded": bool(download_result and not is_cached),
        "loaded": True,
        "ready_to_chat": False,
        "compatibility_status": "ok",
        "embedding_transition": transition,
    }
    return [
        sse_event(
            "progress",
            deps["model_download_progress_payload"](
                "done",
                "시맨틱 임베딩 전환이 준비되었습니다.",
                percent=100,
                eta_seconds=0,
            ),
        ),
        sse_event("done", result),
    ]
