from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, NoReturn, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from latticeai.core.messages import http_error, resolve_language
from latticeai.models.router.errors import MODEL_RUNTIME_BOUNDARY_ERRORS
from latticeai.services.model_errors import ModelRuntimeError


def _vision_capability(current_model_id: Optional[str], engines: Any) -> Dict[str, Any]:
    from latticeai.core.model_compat import get_model_profile

    current_vision = False
    if current_model_id:
        try:
            current_vision = bool(
                get_model_profile(current_model_id).get("supports_vision")
            )
        except (
            ImportError,
            RuntimeError,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
        ):
            current_vision = False
    engine_available = False
    try:
        for eng in engines or []:
            if (
                isinstance(eng, dict)
                and eng.get("id") in {"local_mlx", "mlx"}
                and eng.get("installed")
            ):
                engine_available = True
                break
    except (TypeError, AttributeError, RuntimeError):
        engine_available = False
    return {
        "current_model": current_model_id,
        "current_supports_vision": current_vision,
        "engine_available": engine_available,
        "enabled": bool(current_vision),
    }


class LoadModelRequest(BaseModel):
    model_id: str
    engine: Optional[str] = None
    user_email: Optional[str] = None
    adapter_path: Optional[str] = None
    draft_model_id: Optional[str] = None
    allow_download: bool = False


class PrepareModelRequest(BaseModel):
    model: str
    engine: Optional[str] = None
    user_email: Optional[str] = None
    allow_download: bool = False


_ENGINE_ORDER = ("local_mlx", "omlx", "ollama", "lmstudio", "llamacpp", "vllm")
_HARD_PRIMARY_STATUSES = {
    "runtime_update_needed",
    "unsupported_format",
    "repair_model",
    "incomplete_download",
}


@dataclass(frozen=True)
class _ModelsRouterDeps:
    model_router: Any
    require_user: Callable[[Request], str]
    require_admin: Callable[[Request], tuple]
    prepare_and_load_model: Callable[..., Any]
    prepare_and_load_model_stream: Callable[..., Any]
    sse_event: Callable[[str, Dict], str]
    engine_status: Callable[[], List[Dict]]
    filter_lower_family_versions: Callable[[List[Dict]], List[Dict]]
    list_compat_profiles: Callable[[], Any]
    engine_model_catalog: Dict
    model_engine_aliases: Dict
    is_public_mode: bool
    allow_local_models: bool
    require_auth: bool


def _normalized_identity(value: Optional[str]) -> str:
    return str(value or "").strip().lower()


def _authorize_model_admin(
    deps: _ModelsRouterDeps,
    request: Request,
    claimed_email: Optional[str] = None,
) -> str:
    current_user = deps.require_user(request)
    if deps.require_auth:
        if claimed_email and _normalized_identity(
            claimed_email
        ) != _normalized_identity(current_user):
            raise http_error(
                403, "models.other_user_credentials", resolve_language(request)
            )
        deps.require_admin(request)
    return current_user


def _effective_email(
    deps: _ModelsRouterDeps,
    current_user: str,
    claimed_email: Optional[str],
) -> Optional[str]:
    return current_user if deps.require_auth else claimed_email or current_user or None


def _raise_model_http(exc: ModelRuntimeError) -> NoReturn:
    raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def _option_rank(option: Dict[str, object]) -> tuple[int, int, int, int]:
    runtime_supported = bool(option.get("runtime_supported"))
    installed = bool(option.get("installed"))
    loaded_option = bool(option.get("loaded"))
    ready_without_download = installed and not bool(option.get("download_required"))
    eng_key = str(option.get("engine") or "vllm")
    return (
        0 if runtime_supported else 1,
        0 if loaded_option or ready_without_download else 1,
        0 if installed else 1,
        _ENGINE_ORDER.index(eng_key)
        if eng_key in _ENGINE_ORDER
        else len(_ENGINE_ORDER),
    )


def _build_engine_options(
    item: Dict[str, object],
    aliases: Dict[str, str],
    engine_lookup: Dict[str, Dict[str, Any]],
    model_lookup: Dict[str, Dict[str, Any]],
    loaded: set[str],
    current_id: Optional[str],
) -> List[Dict[str, Any]]:
    from latticeai.core.model_compat import model_runtime_compatibility

    options: List[Dict[str, Any]] = []
    for engine_name in _ENGINE_ORDER:
        real = aliases.get(engine_name)
        if not real:
            continue
        load_id = real if engine_name == "local_mlx" else f"{engine_name}:{real}"
        engine_info = engine_lookup.get(engine_name) or {}
        model_info = model_lookup.get(load_id) or model_lookup.get(real) or {}
        option_loaded = (
            load_id in loaded or real in loaded or current_id in {load_id, real}
        )
        option_runtime = model_runtime_compatibility(load_id, engine=engine_name)
        engine_supported = engine_info.get("supported") is not False
        option_supported = (
            option_runtime.get("supported") is not False
        ) and engine_supported
        option_pulled = bool(model_info.get("pulled"))
        option_download_required = bool(
            item.get("pullable", True) and not option_pulled and not option_loaded
        )
        options.append(
            {
                "engine": engine_name,
                "model_id": real,
                "load_id": load_id,
                "installed": bool(engine_info.get("installed")),
                "installable": bool(engine_info.get("installable", engine_supported)),
                "pulled": option_pulled,
                "loaded": option_loaded,
                "download_required": option_download_required,
                "runtime_compatibility": option_runtime,
                "runtime_supported": option_supported,
                "runtime_label": str(
                    option_runtime.get("preferred_runtime")
                    or engine_info.get("name")
                    or engine_name
                ),
            }
        )
    if options:
        return options
    raw_id = str(item["id"])
    engine_info = engine_lookup.get("local_mlx") or {}
    model_info = model_lookup.get(raw_id) or {}
    option_loaded = raw_id in loaded or current_id == raw_id
    option_pulled = bool(model_info.get("pulled"))
    runtime_compatibility = model_runtime_compatibility(raw_id, engine="local_mlx")
    engine_supported = engine_info.get("supported") is not False
    return [
        {
            "engine": "local_mlx",
            "model_id": item["id"],
            "load_id": item["id"],
            "installed": bool(engine_info.get("installed")),
            "installable": bool(engine_info.get("installable", engine_supported)),
            "pulled": option_pulled,
            "loaded": option_loaded,
            "download_required": bool(
                item.get("pullable", True) and not option_pulled and not option_loaded
            ),
            "runtime_compatibility": runtime_compatibility,
            "runtime_supported": (runtime_compatibility.get("supported") is not False)
            and engine_supported,
            "runtime_label": str(
                runtime_compatibility.get("preferred_runtime") or "MLX"
            ),
        }
    ]


def _resolve_load_status(
    *,
    is_loaded: bool,
    runtime_supported: bool,
    engine_installed: bool,
    download_required: bool,
    recommended_engine: str,
    runtime_compatibility: Dict[str, Any],
    engine_lookup: Dict[str, Dict[str, Any]],
) -> tuple[str, Optional[str]]:
    if is_loaded:
        return "loaded", None
    if not runtime_supported:
        return (
            str(runtime_compatibility.get("status") or "unsupported"),
            str(
                runtime_compatibility.get("user_message")
                or "This model is not supported by the installed runtime."
            ),
        )
    if not engine_installed:
        engine_name = (engine_lookup.get(recommended_engine) or {}).get(
            "name"
        ) or recommended_engine
        return "unavailable", f"{engine_name} runtime is not installed."
    if download_required:
        return (
            "download_required",
            "Model files are not present locally. Downloads are opt-in and never start from token/model presence alone.",
        )
    return "ready", None


def _recommended_with_engine_options(
    items: List[Dict[str, object]],
    model_engine_aliases: Dict[str, Any],
    engines: Optional[List[Dict[str, Any]]] = None,
    loaded_ids: Optional[List[str]] = None,
    current_id: Optional[str] = None,
) -> List[Dict[str, object]]:
    engine_lookup = {str(engine.get("id") or ""): engine for engine in engines or []}
    model_lookup: Dict[str, Dict[str, Any]] = {}
    for engine in engines or []:
        engine_id = str(engine.get("id") or "")
        for model in list(engine.get("models") or []):
            if isinstance(model, dict):
                model_lookup[str(model.get("id") or "")] = {
                    **model,
                    "_engine": engine_id,
                }
    loaded = set(loaded_ids or [])
    out: List[Dict[str, Any]] = []
    for item in items:
        short_id = str(item["id"]).lower()
        aliases = model_engine_aliases.get(short_id) or {}
        options = _build_engine_options(
            item, aliases, engine_lookup, model_lookup, loaded, current_id
        )
        primary_option = options[0]
        primary_compatibility = dict(primary_option.get("runtime_compatibility") or {})
        selected_option = (
            primary_option
            if primary_compatibility.get("supported") is False
            and primary_compatibility.get("status") in _HARD_PRIMARY_STATUSES
            else min(options, key=_option_rank)
        )
        recommended_engine = str(selected_option["engine"])
        load_id = str(selected_option["load_id"])
        model_info = (
            model_lookup.get(load_id)
            or model_lookup.get(str(selected_option.get("model_id") or ""))
            or {}
        )
        pulled = bool(selected_option.get("pulled") or model_info.get("pulled"))
        is_loaded = bool(selected_option.get("loaded"))
        engine_installed = bool(selected_option.get("installed"))
        engine_installable = bool(selected_option.get("installable"))
        pullable = bool(item.get("pullable", True))
        runtime_compatibility = dict(selected_option.get("runtime_compatibility") or {})
        runtime_supported = bool(selected_option.get("runtime_supported"))
        download_required = bool(
            selected_option.get("download_required") and pullable and not is_loaded
        )
        install_required = bool(
            runtime_supported and not engine_installed and engine_installable
        )
        load_status, unavailable_reason = _resolve_load_status(
            is_loaded=is_loaded,
            runtime_supported=runtime_supported,
            engine_installed=engine_installed,
            download_required=download_required,
            recommended_engine=recommended_engine,
            runtime_compatibility=runtime_compatibility,
            engine_lookup=engine_lookup,
        )
        base = {
            "id": item["id"],
            "name": item["name"],
            "model_name": item.get("model_name") or item.get("name"),
            "tag": item["tag"],
            "size": item["size"],
            "display_name": item.get("name") or item.get("id"),
            "modality": item.get("modality") or "multimodal",
            "category": item.get("category")
            or ("embedding" if item.get("task") == "embedding" else "chat"),
            "task": item.get("task")
            or ("embedding" if item.get("category") == "embedding" else "chat"),
            "role": item.get("role")
            or ("embedding" if item.get("category") == "embedding" else "chat"),
            "dimensions": item.get("dimensions"),
            "hf_repo_id": item.get("hf_repo_id") or item["id"],
            "source_country": item.get("source_country"),
            "source_company": item.get("source_company"),
            "execution_method": item.get("execution_method"),
            "run_location": item.get("run_location"),
            "internet_requirement": item.get("internet_requirement"),
            "source_display_order": item.get("source_display_order"),
            "pulled": pulled,
            "download_required": download_required,
            "install_required": install_required,
            "load_available": is_loaded
            or (runtime_supported and engine_installed and not download_required),
            "load_status": load_status,
            "unavailable_reason": unavailable_reason,
            "runtime_compatibility": runtime_compatibility,
            "recovery_guidance": runtime_compatibility.get("recovery_guidance") or [],
            "alternative_recommendations": runtime_compatibility.get("alternatives")
            or [],
            "runtime_label": selected_option.get("runtime_label"),
            "engine_options": options,
            "recommended_engine": recommended_engine,
            "recommended_load_id": load_id,
        }
        out.append(base)
    return out


def _register_engine_routes(router: APIRouter, deps: _ModelsRouterDeps) -> None:
    @router.post("/engines/prepare-model")
    async def engines_prepare_model(req: PrepareModelRequest, request: Request):
        current_user = _authorize_model_admin(deps, request, req.user_email)
        try:
            return await deps.prepare_and_load_model(
                req.model,
                request,
                engine=req.engine,
                user_email=_effective_email(deps, current_user, req.user_email),
                allow_download=req.allow_download,
            )
        except ModelRuntimeError as exc:
            _raise_model_http(exc)
        except HTTPException:
            raise
        except MODEL_RUNTIME_BOUNDARY_ERRORS as exc:
            from latticeai.core.model_compat import friendly_model_runtime_error

            raise HTTPException(
                status_code=500,
                detail=friendly_model_runtime_error(
                    exc, model_id=req.model, engine=req.engine
                ),
            )

    @router.post("/engines/prepare-model/stream")
    async def engines_prepare_model_stream(req: PrepareModelRequest, request: Request):
        current_user = _authorize_model_admin(deps, request, req.user_email)
        effective_email = _effective_email(deps, current_user, req.user_email)

        async def event_stream():
            try:
                async for chunk in deps.prepare_and_load_model_stream(
                    req.model,
                    request,
                    engine=req.engine,
                    user_email=effective_email,
                    allow_download=req.allow_download,
                ):
                    yield chunk
            except (HTTPException, ModelRuntimeError) as exc:
                yield deps.sse_event(
                    "error",
                    {
                        "status_code": exc.status_code,
                        "detail": exc.detail or "모델 준비에 실패했습니다.",
                    },
                )
            except Exception as exc:
                logging.exception("model prepare stream failed")
                from latticeai.core.model_compat import friendly_model_runtime_error

                yield deps.sse_event(
                    "error",
                    {
                        "status_code": 500,
                        "detail": friendly_model_runtime_error(
                            exc, model_id=req.model, engine=req.engine
                        ),
                    },
                )

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )


def _register_model_routes(router: APIRouter, deps: _ModelsRouterDeps) -> None:
    @router.get("/models")
    async def list_models(request: Request):
        _authorize_model_admin(deps, request)
        engines = await asyncio.to_thread(deps.engine_status)
        recommended = _recommended_with_engine_options(
            list(
                deps.filter_lower_family_versions(
                    deps.engine_model_catalog.get("local_mlx", [])
                )
            ),
            deps.model_engine_aliases,
            engines=engines,
            loaded_ids=deps.model_router.loaded_model_ids,
            current_id=deps.model_router.current_model_id,
        )
        try:
            from latticeai.services.model_catalog import (
                get_verified_models,
                recommend_embedding_for_hardware,
            )

            verified = get_verified_models()
            embedding_rec = recommend_embedding_for_hardware()
        except (
            ImportError,
            RuntimeError,
            OSError,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
        ):
            verified = []
            embedding_rec = {
                "id": "google/embeddinggemma-2",
                "hf_repo_id": "google/embeddinggemma-2",
                "name": "Google EmbeddingGemma 2",
                "category": "embedding",
                "task": "embedding",
                "role": "embedding",
                "dimensions": 768,
            }
        try:
            from latticeai.services.model_runtime.download import (
                get_download_progress_snapshot,
            )

            download_progress = get_download_progress_snapshot()
        except (
            ImportError,
            RuntimeError,
            OSError,
            ValueError,
            TypeError,
            AttributeError,
        ):
            download_progress = {}
        return {
            "recommended": recommended,
            "embedding_model": embedding_rec.get("id") or "google/embeddinggemma-2",
            "embedding_recommendation": embedding_rec,
            "download_progress": download_progress,
            "cloud": deps.model_router.detected_cloud_models(),
            "engines": engines,
            "loaded": deps.model_router.loaded_model_ids,
            "current": deps.model_router.current_model_id,
            "compat_profiles": deps.list_compat_profiles(),
            "vision": _vision_capability(deps.model_router.current_model_id, engines),
            "registry": {
                "version": "5.2.0",
                "verified_count": len(verified),
                "verified": verified[:12],
            },
        }

    @router.post("/models/load")
    async def load_model(req: LoadModelRequest, request: Request):
        current_user = _authorize_model_admin(deps, request, req.user_email)
        try:
            from latticeai.core.model_compat import (
                friendly_model_runtime_error,
                model_runtime_compatibility,
            )

            model_id = req.model_id
            requested_engine = req.engine or (
                model_id.split(":", 1)[0] if ":" in model_id else "local_mlx"
            )
            if (
                deps.is_public_mode
                and not deps.allow_local_models
                and requested_engine in {"local_mlx", "mlx"}
            ):
                raise http_error(
                    400, "models.public_mode_blocks_local", resolve_language(request)
                )
            compatibility = model_runtime_compatibility(
                model_id, engine=requested_engine
            )
            if compatibility.get("supported") is False:
                raise HTTPException(status_code=400, detail=compatibility)
            return await deps.prepare_and_load_model(
                model_id,
                request,
                engine=req.engine,
                user_email=_effective_email(deps, current_user, req.user_email),
                adapter_path=req.adapter_path,
                draft_model_id=req.draft_model_id,
                allow_download=req.allow_download,
            )
        except ModelRuntimeError as exc:
            _raise_model_http(exc)
        except HTTPException:
            raise
        except MODEL_RUNTIME_BOUNDARY_ERRORS as e:
            raise HTTPException(
                status_code=500,
                detail=friendly_model_runtime_error(
                    e, model_id=req.model_id, engine=req.engine
                ),
            )

    @router.delete("/models/unload/{model_id:path}")
    async def unload_model(model_id: str, request: Request):
        from latticeai.services.model_runtime.download import request_download_cancel

        _authorize_model_admin(deps, request)
        request_download_cancel(model_id)
        deps.model_router.unload_model(model_id)
        return {"status": "ok", "unloaded": model_id}


def create_models_router(
    *,
    model_router: Any,
    require_user: Callable[[Request], str],
    require_admin: Callable[[Request], tuple],
    prepare_and_load_model: Callable[..., Any],
    prepare_and_load_model_stream: Callable[..., Any],
    sse_event: Callable[[str, Dict], str],
    engine_status: Callable[[], List[Dict]],
    filter_lower_family_versions: Callable[[List[Dict]], List[Dict]],
    list_compat_profiles: Callable[[], Any],
    engine_model_catalog: Dict,
    model_engine_aliases: Dict,
    is_public_mode: bool,
    allow_local_models: bool,
    require_auth: bool,
) -> APIRouter:
    deps = _ModelsRouterDeps(
        model_router=model_router,
        require_user=require_user,
        require_admin=require_admin,
        prepare_and_load_model=prepare_and_load_model,
        prepare_and_load_model_stream=prepare_and_load_model_stream,
        sse_event=sse_event,
        engine_status=engine_status,
        filter_lower_family_versions=filter_lower_family_versions,
        list_compat_profiles=list_compat_profiles,
        engine_model_catalog=engine_model_catalog,
        model_engine_aliases=model_engine_aliases,
        is_public_mode=is_public_mode,
        allow_local_models=allow_local_models,
        require_auth=require_auth,
    )
    router = APIRouter()
    _register_engine_routes(router, deps)
    _register_model_routes(router, deps)
    return router
