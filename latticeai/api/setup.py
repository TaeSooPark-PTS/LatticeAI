"""Setup wizard and OS permission routes."""

from __future__ import annotations

from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from latticeai.services.process_audit import command_plan
from auto_setup import (
    plan as auto_setup_plan,
    preset as auto_setup_preset,
    probe as auto_setup_probe,
    recommend as auto_setup_recommend,
    verify as auto_setup_verify,
)
from latticeai.models.router import parse_model_ref
from setup_wizard import get_recommendations, install_stream, open_url, scan_environment


class SetupInstallRequest(BaseModel):
    items: List[Dict]
    confirmation_token: Optional[str] = None


def create_setup_router(*, model_router, require_user) -> APIRouter:
    api_router = APIRouter()
    router = model_router

    # ── Setup Wizard ─────────────────────────────────────────────────────────────

    def setup_auto_state() -> Dict[str, object]:
        """Return the PPT-aligned zero-config setup state used by setup UI/API."""
        profile = auto_setup_probe()
        recommendation = auto_setup_recommend(profile)
        install_plan = auto_setup_plan(profile, recommendation)
        return {
            "probe": profile.to_json(),
            "recommend": recommendation.to_json(),
            "plan": install_plan.to_json(),
            "verify": auto_setup_verify(profile, recommendation),
            "preset": auto_setup_preset(profile, recommendation),
        }
    
    
    def primary_setup_model(recs: Dict[str, object]) -> Optional[Dict[str, object]]:
        models = recs.get("models") if isinstance(recs, dict) else None
        if not isinstance(models, list):
            return None
        candidates = [
            item for item in models
            if isinstance(item, dict) and not item.get("disabled") and (item.get("model_id") or (item.get("action") or {}).get("model_id"))
        ]
        if not candidates:
            return None
        return next((item for item in candidates if item.get("checked")), candidates[0])
    
    
    @api_router.get("/setup/scan")
    async def setup_scan(request: Request):
        """환경 감지 및 맞춤 추천 반환."""
        require_user(request)
        env  = scan_environment()
        recs = get_recommendations(env)
        zero_config = setup_auto_state()
        primary_model = primary_setup_model(recs)
        if primary_model:
            model_id = primary_model.get("model_id") or (primary_model.get("action") or {}).get("model_id")
            model_provider, provider_model = parse_model_ref(str(model_id))
            primary_runtime = "mlx" if model_provider == "local_mlx" else model_provider
            zero_config.setdefault("recommend", {})["model_id"] = model_id
            zero_config["recommend"]["runtime"] = primary_runtime
            rationale = [
                item for item in zero_config["recommend"].get("rationale", [])
                if not (isinstance(item, str) and item.startswith("RAM ") and "→" in item)
            ]
            rationale.append(f"실제 다운로드 및 로드 가능한 {primary_runtime} 모델 → {model_id}")
            zero_config["recommend"]["rationale"] = rationale
            if isinstance(zero_config.get("plan"), dict):
                if model_provider == "ollama":
                    command = ["ollama", "pull", provider_model]
                elif model_provider in {"vllm", "lmstudio", "llamacpp"}:
                    command = ["lattice-ai", "models", "load", str(model_id)]
                else:
                    command = ["huggingface-cli", "download", str(model_id), "--quiet"]
                step_plan = command_plan(
                    command,
                    name=f"weights:{model_id}",
                    purpose="auto_setup_install",
                    metadata={"model_id": str(model_id)},
                )
                zero_config["plan"]["steps"] = [{
                    "name": f"weights:{model_id}",
                    "why": "추론에 사용할 모델 가중치",
                    "command": command,
                    "requires_admin": False,
                    "command_plan": step_plan,
                    "confirmation_token": step_plan["confirmation_token"],
                }]
            if isinstance(zero_config.get("preset"), dict):
                zero_config["preset"].setdefault("model", {})["id"] = model_id
                zero_config["preset"]["model"]["runtime"] = primary_runtime
        env["zero_config"] = zero_config
        recs.setdefault("summary", {})["zero_config"] = zero_config["recommend"]
        recs["install_plan"] = zero_config["plan"]
        recs["preset"] = zero_config["preset"]
        return {"environment": env, "recommendations": recs, "zero_config": zero_config}
    
    @api_router.get("/setup/auto")
    async def setup_auto(request: Request):
        """PPT-aligned zero-config setup pipeline: probe → recommend → plan → verify → preset."""
        require_user(request)
        return setup_auto_state()
    
    @api_router.post("/setup/install")
    async def setup_install(req: SetupInstallRequest, request: Request):
        """선택된 항목을 순서대로 설치 · 로드하는 SSE 스트림."""
        user_email = require_user(request)
        async def _gen():
            async for chunk in install_stream(
                req.items,
                router,
                confirmation_token=req.confirmation_token,
                user_email=user_email,
            ):
                yield chunk
        return StreamingResponse(_gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    
    @api_router.post("/setup/open-auth/{mcp_id}")
    async def setup_open_auth(mcp_id: str, request: Request):
        require_user(request)
        """MCP 인증 페이지를 브라우저에서 자동으로 엽니다."""
        auth_urls: Dict[str, str] = {
            "github":      "https://github.com/apps",
            "google-drive": "https://chatgpt.com/connectors",
            "slack":       "https://chatgpt.com/connectors",
            "chrome":      "https://chatgpt.com/connectors",
            "computer-use": "https://chatgpt.com/connectors",
            "figma":       "https://chatgpt.com/connectors",
            "notion":      "https://chatgpt.com/connectors",
            "linear":      "https://chatgpt.com/connectors",
            "gmail":       "https://chatgpt.com/connectors",
            "google-calendar": "https://chatgpt.com/connectors",
            "outlook-email": "https://chatgpt.com/connectors",
            "outlook-calendar": "https://chatgpt.com/connectors",
            "teams":       "https://chatgpt.com/connectors",
            "sharepoint":  "https://chatgpt.com/connectors",
            "canva":       "https://chatgpt.com/connectors",
        }
        url = auth_urls.get(mcp_id)
        if not url:
            raise HTTPException(status_code=404, detail=f"알 수 없는 MCP: {mcp_id}")
        open_url(url)
        return {"status": "ok", "opened": url, "mcp_id": mcp_id}
    
    
    @api_router.post("/permissions/open/{permission_id}")
    async def open_permission_settings(permission_id: str, request: Request):
        require_user(request)
        """macOS 권한 설정 화면을 엽니다."""
        urls = {
            "accessibility": "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
            "automation": "x-apple.systempreferences:com.apple.preference.security?Privacy_Automation",
            "screen": "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture",
        }
        url = urls.get(permission_id)
        if not url:
            raise HTTPException(status_code=404, detail="알 수 없는 권한 설정입니다.")
        open_url(url)
        return {"status": "ok", "opened": url, "permission": permission_id}
    return api_router
