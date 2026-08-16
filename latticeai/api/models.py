"""Model / engine API router — the MLX lifecycle this process owns.

Extracted from ``server_app.py`` in v1.3.0 with the whole ``/models*`` +
``/engines*`` + ``/setup/set-api-key`` surface. v11.6.0 kept the eight routes
that are *this interpreter's* business and moved the rest to ``lattice-host``:
engine installation and cloud-key verification are host operations,
``/setup/set-api-key`` is account state, and the two catalogue reads
(``/models/compat-profiles``, ``/models/recommendations``) are product views.

v11.8.0 took three of those eight away, because nothing called them — not the
Rust surface, not the SPA client, not either extension:

* ``POST /engines/pull-model`` — every caller reaches a download through
  ``/engines/prepare-model``, which resolves, downloads on consent, loads and
  smoke-tests in one step. The bare pull was the older door, and with it went
  this module's only use of ``huggingface_hub`` / ``ollama`` (both still run
  here, under ``services/model_loading.py``, for the prepare flow).
* ``POST /models/switch/{model_id:path}`` — ``/models/load`` is what every
  surface sends, and it switches as part of loading.
* ``DELETE /models/unload-all`` — unloading is per-model everywhere.

What is left is list, load, unload-one and the two prepare flows.

Mirrors the established router-factory convention: the heavy provider/runtime
helpers are injected as bound service callables. This module owns the sole
translation from transport-neutral ``ModelRuntimeError`` failures to HTTP.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Dict, List, NoReturn, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from latticeai.core.messages import http_error, resolve_language
from latticeai.services.model_errors import ModelRuntimeError


def _vision_capability(current_model_id: Optional[str], engines: Any) -> Dict[str, Any]:
    """Whether the active model can accept image input (VLM).

    Honest, derived signal for the Chat 'Vision Enabled / Disabled' badge: a
    model is vision-capable only when its compat profile reports
    ``supports_vision``. The MLX-VLM engine availability is reported too so the
    UI can explain a disabled badge ("load a vision model" vs "install MLX-VLM").
    """
    from latticeai.core.model_compat import get_model_profile

    current_vision = False
    if current_model_id:
        try:
            current_vision = bool(get_model_profile(current_model_id).get("supports_vision"))
        except Exception:
            current_vision = False
    engine_available = False
    try:
        for eng in (engines or []):
            if isinstance(eng, dict) and eng.get("id") in {"local_mlx", "mlx"} and eng.get("installed"):
                engine_available = True
                break
    except Exception:
        engine_available = False
    return {
        "current_model": current_model_id,
        "current_supports_vision": current_vision,
        "engine_available": engine_available,
        # The badge is "enabled" only when a vision-capable model is active.
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
    router = APIRouter()
    # Bind injected deps to the names the moved handler bodies expect.
    _router = model_router
    ENGINE_MODEL_CATALOG = engine_model_catalog
    MODEL_ENGINE_ALIASES = model_engine_aliases
    IS_PUBLIC_MODE = is_public_mode
    ALLOW_LOCAL_MODELS = allow_local_models
    REQUIRE_AUTH = require_auth
    _list_compat_profiles = list_compat_profiles

    def _normalized_identity(value: Optional[str]) -> str:
        return str(value or "").strip().lower()

    def _authorize_model_admin(request: Request, claimed_email: Optional[str] = None) -> str:
        """Authenticate model operations and gate host-global state to admins."""
        current_user = require_user(request)
        if REQUIRE_AUTH:
            if claimed_email and _normalized_identity(claimed_email) != _normalized_identity(current_user):
                raise http_error(403, "models.other_user_credentials", resolve_language(request))
            require_admin(request)
        return current_user

    def _effective_email(current_user: str, claimed_email: Optional[str]) -> Optional[str]:
        # Authenticated callers may only act as their session identity. The
        # legacy body field remains usable solely in explicit no-auth/local
        # mode for backward compatibility.
        return current_user if REQUIRE_AUTH else claimed_email or current_user or None

    def _raise_model_http(exc: ModelRuntimeError) -> NoReturn:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    def _recommended_with_engine_options(
        items: List[Dict[str, object]],
        engines: Optional[List[Dict[str, Any]]] = None,
        loaded_ids: Optional[List[str]] = None,
        current_id: Optional[str] = None,
    ) -> List[Dict[str, object]]:
        from latticeai.core.model_compat import model_runtime_compatibility

        engine_lookup = {str(engine.get("id") or ""): engine for engine in engines or []}
        model_lookup: Dict[str, Dict[str, Any]] = {}
        for engine in engines or []:
            engine_id = str(engine.get("id") or "")
            for model in list(engine.get("models") or []):
                if isinstance(model, dict):
                    model_lookup[str(model.get("id") or "")] = {**model, "_engine": engine_id}
        loaded = set(loaded_ids or [])
        out: List[Dict[str, Any]] = []
        for item in items:
            short_id = str(item["id"]).lower()
            aliases = MODEL_ENGINE_ALIASES.get(short_id) or {}
            options: List[Dict[str, Any]] = []
            for engine_name in ("local_mlx", "ollama", "lmstudio", "llamacpp", "vllm"):
                real = aliases.get(engine_name)
                if not real:
                    continue
                load_id = real if engine_name == "local_mlx" else f"{engine_name}:{real}"
                engine_info = engine_lookup.get(engine_name) or {}
                model_info = model_lookup.get(load_id) or model_lookup.get(real) or {}
                option_loaded = load_id in loaded or real in loaded or current_id in {load_id, real}
                option_runtime = model_runtime_compatibility(load_id, engine=engine_name)
                option_supported = option_runtime.get("supported") is not False
                option_pulled = bool(model_info.get("pulled"))
                option_download_required = bool(item.get("pullable", True) and not option_pulled and not option_loaded)
                options.append({
                    "engine": engine_name,
                    "model_id": real,
                    "load_id": load_id,
                    "installed": bool(engine_info.get("installed")),
                    "pulled": option_pulled,
                    "loaded": option_loaded,
                    "download_required": option_download_required,
                    "runtime_compatibility": option_runtime,
                    "runtime_supported": option_supported,
                    "runtime_label": str(option_runtime.get("preferred_runtime") or engine_info.get("name") or engine_name),
                })
            if not options:
                raw_id = str(item["id"])
                engine_info = engine_lookup.get("local_mlx") or {}
                model_info = model_lookup.get(raw_id) or {}
                option_loaded = raw_id in loaded or current_id == raw_id
                option_pulled = bool(model_info.get("pulled"))
                runtime_compatibility = model_runtime_compatibility(str(item["id"]), engine="local_mlx")
                options.append({
                    "engine": "local_mlx",
                    "model_id": item["id"],
                    "load_id": item["id"],
                    "installed": bool(engine_info.get("installed")),
                    "pulled": option_pulled,
                    "loaded": option_loaded,
                    "download_required": bool(item.get("pullable", True) and not option_pulled and not option_loaded),
                    "runtime_compatibility": runtime_compatibility,
                    "runtime_supported": runtime_compatibility.get("supported") is not False,
                    "runtime_label": str(runtime_compatibility.get("preferred_runtime") or "MLX"),
                })

            def option_rank(option: Dict[str, object]) -> tuple[int, int, int, int]:
                runtime_supported = bool(option.get("runtime_supported"))
                installed = bool(option.get("installed"))
                loaded_option = bool(option.get("loaded"))
                ready_without_download = installed and not bool(option.get("download_required"))
                return (
                    0 if runtime_supported else 1,
                    0 if loaded_option or ready_without_download else 1,
                    0 if installed else 1,
                    ["local_mlx", "ollama", "lmstudio", "llamacpp", "vllm"].index(str(option.get("engine") or "vllm")),
                )

            primary_option = options[0]
            primary_compatibility = dict(primary_option.get("runtime_compatibility") or {})
            hard_primary_statuses = {
                "runtime_update_needed",
                "unsupported_format",
                "repair_model",
                "incomplete_download",
            }
            selected_option = (
                primary_option
                if primary_compatibility.get("supported") is False
                and primary_compatibility.get("status") in hard_primary_statuses
                else min(options, key=option_rank)
            )
            recommended_engine = str(selected_option["engine"])
            load_id = str(selected_option["load_id"])
            model_info = model_lookup.get(load_id) or model_lookup.get(str(selected_option.get("model_id") or "")) or {}
            pulled = bool(selected_option.get("pulled") or model_info.get("pulled"))
            is_loaded = bool(selected_option.get("loaded"))
            engine_installed = bool(selected_option.get("installed"))
            pullable = bool(item.get("pullable", True))
            runtime_compatibility = dict(selected_option.get("runtime_compatibility") or {})
            runtime_supported = runtime_compatibility.get("supported") is not False
            download_required = bool(selected_option.get("download_required") and pullable and not is_loaded)
            if is_loaded:
                load_status = "loaded"
                unavailable_reason = None
            elif not runtime_supported:
                load_status = str(runtime_compatibility.get("status") or "unsupported")
                unavailable_reason = str(runtime_compatibility.get("user_message") or "This model is not supported by the installed runtime.")
            elif not engine_installed:
                load_status = "unavailable"
                unavailable_reason = f"{engine_info.get('name') or recommended_engine} runtime is not installed."
            elif download_required:
                load_status = "download_required"
                unavailable_reason = "Model files are not present locally. Downloads are opt-in and never start from token/model presence alone."
            else:
                load_status = "ready"
                unavailable_reason = None
            base = {
                "id": item["id"],
                "name": item["name"],
                "model_name": item.get("model_name") or item.get("name"),
                "tag": item["tag"],
                "size": item["size"],
                "display_name": item.get("name") or item.get("id"),
                "modality": item.get("modality") or "multimodal",
                "source_country": item.get("source_country"),
                "source_company": item.get("source_company"),
                "execution_method": item.get("execution_method"),
                "run_location": item.get("run_location"),
                "internet_requirement": item.get("internet_requirement"),
                "source_display_order": item.get("source_display_order"),
                "pulled": pulled,
                "download_required": download_required,
                "load_available": is_loaded or (runtime_supported and engine_installed and not download_required),
                "load_status": load_status,
                "unavailable_reason": unavailable_reason,
                "runtime_compatibility": runtime_compatibility,
                "recovery_guidance": runtime_compatibility.get("recovery_guidance") or [],
                "alternative_recommendations": runtime_compatibility.get("alternatives") or [],
                "runtime_label": selected_option.get("runtime_label"),
            }
            base["engine_options"] = options
            base["recommended_engine"] = recommended_engine
            base["recommended_load_id"] = load_id
            out.append(base)
        return out

    # ── Engines ───────────────────────────────────────────────────────────

    @router.post("/engines/prepare-model")
    async def engines_prepare_model(req: PrepareModelRequest, request: Request):
        current_user = _authorize_model_admin(request, req.user_email)
        try:
            return await prepare_and_load_model(
                req.model, request, engine=req.engine,
                user_email=_effective_email(current_user, req.user_email),
                allow_download=req.allow_download,
            )
        except ModelRuntimeError as exc:
            _raise_model_http(exc)
        except HTTPException:
            raise
        except Exception as exc:
            from latticeai.core.model_compat import friendly_model_runtime_error

            raise HTTPException(
                status_code=500,
                detail=friendly_model_runtime_error(exc, model_id=req.model, engine=req.engine),
            )

    @router.post("/engines/prepare-model/stream")
    async def engines_prepare_model_stream(req: PrepareModelRequest, request: Request):
        current_user = _authorize_model_admin(request, req.user_email)
        effective_email = _effective_email(current_user, req.user_email)

        async def event_stream():
            try:
                async for chunk in prepare_and_load_model_stream(
                    req.model, request, engine=req.engine, user_email=effective_email,
                    allow_download=req.allow_download,
                ):
                    yield chunk
            except (HTTPException, ModelRuntimeError) as exc:
                yield sse_event("error", {
                    "status_code": exc.status_code,
                    "detail": exc.detail or "모델 준비에 실패했습니다.",
                })
            except Exception as exc:
                logging.exception("model prepare stream failed")
                from latticeai.core.model_compat import friendly_model_runtime_error

                yield sse_event("error", {
                    "status_code": 500,
                    "detail": friendly_model_runtime_error(exc, model_id=req.model, engine=req.engine),
                })

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )


    # ── Models ────────────────────────────────────────────────────────────

    @router.get("/models")
    async def list_models(request: Request):
        _authorize_model_admin(request)
        engines = await asyncio.to_thread(engine_status)
        recommended = _recommended_with_engine_options(
            list(filter_lower_family_versions(ENGINE_MODEL_CATALOG.get("local_mlx", []))),
            engines=engines,
            loaded_ids=_router.loaded_model_ids,
            current_id=_router.current_model_id,
        )
        # 5.2.0: surface structured registry info (verified status, hf, hardware, strategies) for UX
        try:
            from latticeai.services.model_catalog import get_verified_models
            verified = get_verified_models()
        except Exception:
            verified = []
        return {
            "recommended": recommended,
            "cloud": _router.detected_cloud_models(),
            "engines": engines,
            "loaded": _router.loaded_model_ids,
            "current": _router.current_model_id,
            "compat_profiles": _list_compat_profiles(),
            "vision": _vision_capability(_router.current_model_id, engines),
            # 5.2+ transparent model capability registry
            "registry": {
                "version": "5.2.0",
                "verified_count": len(verified),
                "verified": verified[:12],  # compact; full via /models/recommendations or future dedicated
            },
        }


    @router.post("/models/load")
    async def load_model(req: LoadModelRequest, request: Request):
        current_user = _authorize_model_admin(request, req.user_email)
        try:
            from latticeai.core.model_compat import (
                friendly_model_runtime_error,
                model_runtime_compatibility,
            )

            model_id = req.model_id
            requested_engine = req.engine or (model_id.split(":", 1)[0] if ":" in model_id else "local_mlx")
            if IS_PUBLIC_MODE and not ALLOW_LOCAL_MODELS and requested_engine in {"local_mlx", "mlx"}:
                raise http_error(400, "models.public_mode_blocks_local", resolve_language(request))
            compatibility = model_runtime_compatibility(model_id, engine=requested_engine)
            if compatibility.get("supported") is False:
                raise HTTPException(status_code=400, detail=compatibility)
            return await prepare_and_load_model(
                model_id, request, engine=req.engine,
                user_email=_effective_email(current_user, req.user_email),
                adapter_path=req.adapter_path, draft_model_id=req.draft_model_id,
                allow_download=req.allow_download,
            )
        except ModelRuntimeError as exc:
            _raise_model_http(exc)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=friendly_model_runtime_error(e, model_id=req.model_id, engine=req.engine),
            )

    @router.delete("/models/unload/{model_id:path}")
    async def unload_model(model_id: str, request: Request):
        _authorize_model_admin(request)
        _router.unload_model(model_id)
        return {"status": "ok", "unloaded": model_id}

    return router
