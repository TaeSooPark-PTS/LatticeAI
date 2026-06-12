"""Static UI and lightweight status routes."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from fastapi import APIRouter, Cookie, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse

from latticeai.api.ui_redirects import app_redirect

def ui_file_response(path: Path) -> FileResponse:
    response = FileResponse(path)
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

@dataclass(frozen=True)
class StaticRoutesBundle:
    router: APIRouter
    ui_file_response: Callable[[Path], FileResponse]
    local_sysinfo: Callable[[Request], object]


def create_static_routes_router(
    *,
    static_dir: Path,
    invite_gate_enabled: bool,
    invite_code: str,
    app_mode: str,
    model_router,
    require_user,
) -> StaticRoutesBundle:
    api_router = APIRouter()
    STATIC_DIR = static_dir
    INVITE_GATE_ENABLED = invite_gate_enabled
    INVITE_CODE = invite_code
    APP_MODE = app_mode
    router = model_router

    @api_router.get("/")
    async def root(request: Request, code: Optional[str] = None, authorized: Optional[str] = Cookie(None)):
        """로그인/회원가입 페이지. 초대 게이트 활성화 시 코드 검증 후 진입."""
        if not INVITE_GATE_ENABLED:
            return app_redirect("account", request)
    
        # 1. 이미 쿠키로 인증된 경우
        if authorized == "true":
            return app_redirect("account", request)
    
        # 2. 초대 코드가 일치하는 경우 (최초 진입)
        if code == INVITE_CODE:
            response = app_redirect("account", request)
            response.set_cookie(key="authorized", value="true", httponly=True, samesite="lax", max_age=60*60*24*7)
            return response
    
        # 3. 인증 실패 시 차단 화면
        return HTMLResponse(content="""
            <body style="background:#0f1115; color:white; display:flex; flex-direction:column; align-items:center; justify-content:center; height:100vh; font-family:sans-serif;">
                <div style="background:#16191f; padding:40px; border-radius:24px; border:1px solid rgba(255,255,255,0.1); text-align:center; box-shadow: 0 20px 40px rgba(0,0,0,0.5);">
                    <div style="font-size:48px; margin-bottom:20px;">🔒</div>
                    <h1 style="color:#378ADD; margin:0; font-size:24px;">Invitation Required</h1>
                    <p style="color:#94a3b8; margin:20px 0; line-height:1.6;">이 서비스는 비공개로 운영되고 있습니다.<br>선생님께 받은 <b>초대용 전용 링크</b>를 통해 접속해 주세요.</p>
                    <div style="margin-top:30px; padding-top:20px; border-top:1px solid rgba(255,255,255,0.05); font-size:11px; color:rgba(255,255,255,0.2); letter-spacing:1px;">LATTICE AI</div>
                </div>
            </body>
        """, status_code=403)
    
    
    @api_router.get("/account")
    async def account_page():
        """Direct login/register page route used by logout and manual navigation."""
        return app_redirect("account")
    
    
    @api_router.get("/manifest.json")
    async def manifest():
        p = STATIC_DIR / "manifest.json"
        if not p.exists():
            raise HTTPException(status_code=404)
        return FileResponse(str(p), media_type="application/manifest+json")

    @api_router.api_route("/favicon.ico", methods=["GET", "HEAD"], include_in_schema=False)
    async def favicon():
        ico = STATIC_DIR / "favicon.ico"
        png = STATIC_DIR / "icons" / "favicon-32.png"
        if ico.exists():
            return FileResponse(str(ico), media_type="image/x-icon")
        if png.exists():
            return FileResponse(str(png), media_type="image/png")
        raise HTTPException(status_code=404)
    
    
    @api_router.get("/sw.js")
    async def service_worker():
        p = STATIC_DIR / "sw.js"
        if not p.exists():
            raise HTTPException(status_code=404)
        resp = FileResponse(str(p), media_type="application/javascript")
        resp.headers["Service-Worker-Allowed"] = "/"
        return resp
    
    
    @api_router.get("/chat")
    async def chat_page(request: Request):
        return app_redirect("chat", request)


    @api_router.get("/app")
    async def app_shell(request: Request):
        """React desktop single-page workspace shell."""
        page = STATIC_DIR / "app" / "index.html"
        if not page.exists():
            raise HTTPException(status_code=404, detail="React shell not found.")
        return ui_file_response(page)


    @api_router.get("/admin")
    async def admin_page(request: Request):
        return app_redirect("admin/users", request)
    
    # /workspace and /onboarding UI pages are served by the workspace router
    # (latticeai.api.workspace), included below after its dependencies are defined.
    
    @api_router.get("/status")
    async def status():
        """서버 상태 및 현재 로드된 모델 정보를 반환합니다."""
        return {
            "message": "🧠 Lattice AI MLX Server is running!",
            "status": "online",
            "mode": APP_MODE,
            "loaded_model": router._current or "None"
        }
    
    
    @api_router.get("/local/sysinfo")
    async def local_sysinfo(request: Request):
        """CPU / RAM / GPU(MLX) 사용량을 반환합니다."""
        require_user(request)
        import re as _re
        result = {"cpu_pct": 0.0, "ram_pct": 0.0, "gpu_mem_pct": 0.0, "gpu_mem_gb": 0.0}
        try:
            # CPU
            top_out = subprocess.run(["top", "-l", "1", "-n", "0"], capture_output=True, text=True, timeout=4).stdout
            for line in top_out.splitlines():
                if "CPU usage" in line:
                    m = _re.search(r"([\d.]+)% user.*?([\d.]+)% sys", line)
                    if m:
                        result["cpu_pct"] = round(float(m.group(1)) + float(m.group(2)), 1)
            # RAM
            vm_out = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=4).stdout
            pages: dict = {}
            for line in vm_out.splitlines():
                for key in ["Pages free", "Pages active", "Pages inactive", "Pages wired down", "Pages occupied by compressor"]:
                    if line.startswith(key):
                        m = _re.search(r"(\d+)", line)
                        if m:
                            pages[key] = int(m.group(1))
            total = sum(pages.values())
            used  = total - pages.get("Pages free", 0)
            result["ram_pct"] = round(used / total * 100, 1) if total else 0.0
            # GPU (MLX / Apple Silicon unified memory)
            try:
                import mlx.core as _mx
                hw_out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=2).stdout
                total_bytes = int(hw_out.strip())
                gpu_bytes = _mx.get_active_memory() + _mx.get_cache_memory()
                result["gpu_mem_gb"]  = round(gpu_bytes / (1024 ** 3), 2)
                result["gpu_mem_pct"] = round(gpu_bytes / total_bytes * 100, 1) if total_bytes else 0.0
            except Exception:
                pass
        except Exception as e:
            result["error"] = str(e)
        return result

    return StaticRoutesBundle(api_router, ui_file_response, local_sysinfo)
