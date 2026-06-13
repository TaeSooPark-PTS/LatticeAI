"""Model / engine API router.

Extracted from ``server_app.py`` in v1.3.0. Paths and schemas unchanged:
``/models*``, ``/engines*`` (install/verify-cloud/pull-model/prepare-model[/stream]),
``/setup/set-api-key``.

Mirrors the established router-factory convention: the heavy provider/runtime
helpers (engine_status, prepare_and_load_model, download_hf_model,
verify_cloud_models, …) remain owned by server_app for now and are injected here
as callables, so this module has no import cycle and adds no import-time
side effects.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel


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


class InstallEngineRequest(BaseModel):
    engine: str


class SetApiKeyRequest(BaseModel):
    provider: str
    key: str
    user_email: Optional[str] = None


class PullModelRequest(BaseModel):
    model: str


class PrepareModelRequest(BaseModel):
    model: str
    engine: Optional[str] = None
    user_email: Optional[str] = None
    allow_download: bool = False


class VerifyCloudRequest(BaseModel):
    force: bool = False
    provider: Optional[str] = None


def create_models_router(
    *,
    model_router: Any,
    require_user: Callable[[Request], str],
    get_current_user: Callable[[Request], Optional[str]],
    load_users: Callable[[], Dict],
    get_user_role: Callable[..., str],
    install_engine: Callable[[str], Dict],
    verify_cloud_models: Callable[..., Any],
    normalize_local_model_request: Callable[..., str],
    download_hf_model: Callable[..., Dict],
    prepare_and_load_model: Callable[..., Any],
    prepare_and_load_model_stream: Callable[..., Any],
    sse_event: Callable[[str, Dict], str],
    ensure_ollama_server: Callable[[], None],
    local_binary: Callable[[str], Optional[str]],
    engine_status: Callable[[], List[Dict]],
    filter_lower_family_versions: Callable[[List[Dict]], List[Dict]],
    list_compat_profiles: Callable[[], Any],
    set_user_api_key: Callable[..., None],
    engine_model_catalog: Dict,
    model_engine_aliases: Dict,
    cloud_verify_ttl_seconds: int,
    is_public_mode: bool,
    allow_local_models: bool,
    require_auth: bool,
) -> APIRouter:
    router = APIRouter()
    # Bind injected deps to the names the moved handler bodies expect.
    _router = model_router
    ENGINE_MODEL_CATALOG = engine_model_catalog
    MODEL_ENGINE_ALIASES = model_engine_aliases
    CLOUD_VERIFY_TTL_SECONDS = cloud_verify_ttl_seconds
    IS_PUBLIC_MODE = is_public_mode
    ALLOW_LOCAL_MODELS = allow_local_models
    REQUIRE_AUTH = require_auth
    _list_compat_profiles = list_compat_profiles

    def _recommended_with_engine_options(
        items: List[Dict[str, object]],
        engines: Optional[List[Dict[str, object]]] = None,
        loaded_ids: Optional[List[str]] = None,
        current_id: Optional[str] = None,
    ) -> List[Dict[str, object]]:
        from latticeai.core.model_compat import model_runtime_compatibility

        engine_lookup = {str(engine.get("id") or ""): engine for engine in engines or []}
        model_lookup: Dict[str, Dict[str, object]] = {}
        for engine in engines or []:
            engine_id = str(engine.get("id") or "")
            for model in engine.get("models") or []:
                if isinstance(model, dict):
                    model_lookup[str(model.get("id") or "")] = {**model, "_engine": engine_id}
        loaded = set(loaded_ids or [])
        out: List[Dict[str, object]] = []
        for item in items:
            short_id = str(item["id"]).lower()
            aliases = MODEL_ENGINE_ALIASES.get(short_id) or {}
            options: List[Dict[str, object]] = []
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

    @router.post("/engines/install")
    async def engines_install(req: InstallEngineRequest, request: Request):
        require_user(request)
        return install_engine(req.engine)

    @router.post("/engines/verify-cloud")
    async def engines_verify_cloud(req: VerifyCloudRequest, request: Request):
        require_user(request)
        results = await verify_cloud_models(force=req.force, provider_filter=req.provider)
        return {"verified": results, "ttl_seconds": CLOUD_VERIFY_TTL_SECONDS}

    @router.post("/engines/pull-model")
    async def pull_ollama_model(req: PullModelRequest, request: Request):
        require_user(request)
        model_ref = normalize_local_model_request(req.model, None)
        if not model_ref:
            raise HTTPException(status_code=400, detail="모델 식별자가 비어 있습니다.")

        if ":" in model_ref and model_ref.split(":", 1)[0].strip().lower() in {"ollama", "vllm", "lmstudio", "llamacpp", "local_mlx", "mlx"}:
            provider, model_name = model_ref.split(":", 1)
            provider = provider.strip().lower()
            model_name = model_name.strip()
        else:
            provider, model_name = "local_mlx", model_ref

        if not model_name:
            raise HTTPException(status_code=400, detail="모델 이름이 비어 있습니다.")

        if provider == "ollama":
            ensure_ollama_server()
            ollama = local_binary("ollama")
            if not ollama:
                raise HTTPException(status_code=400, detail="Ollama가 설치되지 않았습니다.")
            try:
                completed = subprocess.run(
                    [ollama, "pull", model_name],
                    capture_output=True, text=True, timeout=900, check=False,
                )
            except subprocess.TimeoutExpired:
                raise HTTPException(status_code=408, detail="모델 다운로드 시간이 초과되었습니다.")
            if completed.returncode != 0:
                raise HTTPException(status_code=500, detail=completed.stderr[-2000:] or "pull 실패")
            return {"provider": provider, "model": model_name, "returncode": completed.returncode}

        if provider == "lmstudio":
            raise HTTPException(
                status_code=400,
                detail=(
                    "LM Studio 모델은 Lattice에서 Hugging Face로 pull하지 않습니다. "
                    "LM Studio 앱에서 모델을 다운로드하고 Local Server를 켠 뒤 모델을 로드하세요. "
                    "그러면 모델 선택창에 실제 /v1/models 항목이 표시됩니다."
                ),
            )

        if provider in {"vllm", "llamacpp", "local_mlx", "mlx"}:
            download_provider = "local_mlx" if provider == "mlx" else provider
            result = download_hf_model(model_name, download_provider)
            return {"provider": provider, "model": model_name, "returncode": 0, **result}

        raise HTTPException(status_code=400, detail=f"{provider} 엔진 모델 다운로드는 아직 자동화되지 않았습니다.")

    @router.post("/engines/prepare-model")
    async def engines_prepare_model(req: PrepareModelRequest, request: Request):
        require_user(request)
        try:
            return await prepare_and_load_model(
                req.model, request, engine=req.engine, user_email=req.user_email,
                allow_download=req.allow_download,
            )
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
        require_user(request)

        async def event_stream():
            try:
                async for chunk in prepare_and_load_model_stream(
                    req.model, request, engine=req.engine, user_email=req.user_email,
                    allow_download=req.allow_download,
                ):
                    yield chunk
            except HTTPException as exc:
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

    @router.post("/setup/set-api-key")
    async def set_api_key(req: SetApiKeyRequest, request: Request):
        from latticeai.models.router import OPENAI_COMPATIBLE_PROVIDERS
        config = OPENAI_COMPATIBLE_PROVIDERS.get(req.provider)
        if not config:
            raise HTTPException(status_code=400, detail="알 수 없는 프로바이더입니다.")
        if not req.key.strip():
            raise HTTPException(status_code=400, detail="API 키가 비어있습니다.")
        current_user = get_current_user(request)
        if REQUIRE_AUTH and not current_user:
            raise HTTPException(status_code=401, detail="인증이 필요합니다.")
        if req.user_email and req.user_email != current_user:
            users = load_users()
            if get_user_role(current_user or "", users) != "admin":
                raise HTTPException(status_code=403, detail="다른 사용자의 API 키를 설정할 권한이 없습니다.")
        target_email = (req.user_email or current_user or "").strip()
        if not target_email:
            raise HTTPException(status_code=400, detail="사용자 식별이 필요합니다. 로그인 후 다시 시도하세요.")
        set_user_api_key(target_email, req.provider, req.key.strip())
        return {"ok": True, "provider": req.provider, "user_email": target_email, "scope": "user"}

    # ── Models ────────────────────────────────────────────────────────────

    @router.get("/models")
    async def list_models():
        engines = await asyncio.to_thread(engine_status)
        recommended = _recommended_with_engine_options(
            list(filter_lower_family_versions(ENGINE_MODEL_CATALOG.get("local_mlx", []))),
            engines=engines,
            loaded_ids=_router.loaded_model_ids,
            current_id=_router.current_model_id,
        )
        return {
            "recommended": recommended,
            "cloud": _router.detected_cloud_models(),
            "engines": engines,
            "loaded": _router.loaded_model_ids,
            "current": _router.current_model_id,
            "compat_profiles": _list_compat_profiles(),
            "vision": _vision_capability(_router.current_model_id, engines),
        }

    @router.get("/models/compat-profiles")
    async def list_model_compat_profiles(request: Request):
        require_user(request)
        return {"profiles": _list_compat_profiles()}

    @router.post("/models/load")
    async def load_model(req: LoadModelRequest, request: Request):
        try:
            from latticeai.core.model_compat import friendly_model_runtime_error, model_runtime_compatibility

            model_id = req.model_id
            requested_engine = req.engine or (model_id.split(":", 1)[0] if ":" in model_id else "local_mlx")
            if IS_PUBLIC_MODE and not ALLOW_LOCAL_MODELS and requested_engine in {"local_mlx", "mlx"}:
                raise HTTPException(
                    status_code=400,
                    detail="Public mode blocks local MLX model loading. Use openai:, openrouter:, groq:, together:, or set LATTICEAI_ALLOW_LOCAL_MODELS=true.",
                )
            compatibility = model_runtime_compatibility(model_id, engine=requested_engine)
            if compatibility.get("supported") is False:
                raise HTTPException(status_code=400, detail=compatibility)
            return await prepare_and_load_model(
                model_id, request, engine=req.engine, user_email=req.user_email,
                adapter_path=req.adapter_path, draft_model_id=req.draft_model_id,
                allow_download=req.allow_download,
            )
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=friendly_model_runtime_error(e, model_id=req.model_id, engine=req.engine),
            )

    @router.post("/models/switch/{model_id:path}")
    async def switch_model(model_id: str, request: Request):
        require_user(request)
        try:
            _router.switch_model(model_id)
            return {"status": "ok", "current": _router.current_model_id}
        except KeyError:
            raise HTTPException(status_code=404, detail=f"Model '{model_id}' not loaded. Call /models/load first.")

    @router.delete("/models/unload/{model_id:path}")
    async def unload_model(model_id: str, request: Request):
        require_user(request)
        _router.unload_model(model_id)
        return {"status": "ok", "unloaded": model_id}

    @router.delete("/models/unload-all")
    async def unload_all_models(request: Request):
        require_user(request)
        unloaded = _router.loaded_model_ids
        _router.unload_all()
        return {"status": "ok", "unloaded": unloaded}

    @router.get("/models/recommendations")
    async def model_recommendations(request: Request, engine: str = "local_mlx"):
        """Hardware-aware tri-state model recommendation for this machine.

        Detects the system profile (OS/RAM/CPU/GPU/disk) and classifies the
        ``engine`` catalog into recommended / compatible / not_recommended,
        grouped by family. Used by the onboarding and model-picker UIs.
        """
        require_user(request)
        from auto_setup import probe as auto_setup_probe
        from latticeai.services.model_recommendation import recommend_catalog

        profile = await asyncio.to_thread(lambda: auto_setup_probe().to_json())
        catalog = recommend_catalog(profile, engine=engine)
        return {"profile": profile, "recommendations": catalog}

    return router
