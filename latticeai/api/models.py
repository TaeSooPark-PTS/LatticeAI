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


class LoadModelRequest(BaseModel):
    model_id: str
    engine: Optional[str] = None
    user_email: Optional[str] = None
    adapter_path: Optional[str] = None
    draft_model_id: Optional[str] = None


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

    def _recommended_with_engine_options(items: List[Dict[str, object]]) -> List[Dict[str, object]]:
        out: List[Dict[str, object]] = []
        for item in items:
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
            }
            short_id = str(item["id"]).lower()
            aliases = MODEL_ENGINE_ALIASES.get(short_id) or {}
            options: List[Dict[str, str]] = []
            for engine_name in ("local_mlx", "ollama", "lmstudio", "llamacpp", "vllm"):
                real = aliases.get(engine_name)
                if not real:
                    continue
                options.append({
                    "engine": engine_name,
                    "model_id": real,
                    "load_id": real if engine_name == "local_mlx" else f"{engine_name}:{real}",
                })
            if not options:
                options.append({"engine": "local_mlx", "model_id": item["id"], "load_id": item["id"]})
            base["engine_options"] = options
            base["recommended_engine"] = options[0]["engine"]
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
        return await prepare_and_load_model(
            req.model, request, engine=req.engine, user_email=req.user_email,
        )

    @router.post("/engines/prepare-model/stream")
    async def engines_prepare_model_stream(req: PrepareModelRequest, request: Request):
        require_user(request)

        async def event_stream():
            try:
                async for chunk in prepare_and_load_model_stream(
                    req.model, request, engine=req.engine, user_email=req.user_email,
                ):
                    yield chunk
            except HTTPException as exc:
                yield sse_event("error", {
                    "status_code": exc.status_code,
                    "detail": exc.detail or "모델 준비에 실패했습니다.",
                })
            except Exception as exc:
                logging.exception("model prepare stream failed")
                yield sse_event("error", {
                    "status_code": 500,
                    "detail": str(exc)[-1000:] or "모델 준비에 실패했습니다.",
                })

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.post("/setup/set-api-key")
    async def set_api_key(req: SetApiKeyRequest, request: Request):
        from llm_router import OPENAI_COMPATIBLE_PROVIDERS
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
        recommended = _recommended_with_engine_options(
            list(filter_lower_family_versions(ENGINE_MODEL_CATALOG.get("local_mlx", [])))
        )
        return {
            "recommended": recommended,
            "cloud": _router.detected_cloud_models(),
            "engines": await asyncio.to_thread(engine_status),
            "loaded": _router.loaded_model_ids,
            "current": _router.current_model_id,
            "compat_profiles": _list_compat_profiles(),
        }

    @router.get("/models/compat-profiles")
    async def list_model_compat_profiles(request: Request):
        require_user(request)
        return {"profiles": _list_compat_profiles()}

    @router.post("/models/load")
    async def load_model(req: LoadModelRequest, request: Request):
        try:
            model_id = req.model_id
            requested_engine = req.engine or (model_id.split(":", 1)[0] if ":" in model_id else "local_mlx")
            if IS_PUBLIC_MODE and not ALLOW_LOCAL_MODELS and requested_engine in {"local_mlx", "mlx"}:
                raise HTTPException(
                    status_code=400,
                    detail="Public mode blocks local MLX model loading. Use openai:, openrouter:, groq:, together:, or set LATTICEAI_ALLOW_LOCAL_MODELS=true.",
                )
            return await prepare_and_load_model(
                model_id, request, engine=req.engine, user_email=req.user_email,
                adapter_path=req.adapter_path, draft_model_id=req.draft_model_id,
            )
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

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
