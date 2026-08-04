"""Static UI and lightweight status routes."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, Cookie, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse

from latticeai.api.ui_redirects import app_redirect
from latticeai.core.quiet import quiet

PRODUCTION_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob: http://127.0.0.1:*; "
    "font-src 'self' data:; "
    "connect-src 'self' http://127.0.0.1:* ws://127.0.0.1:*; "
    "frame-src 'none'; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)

INVITE_COOKIE_NAME = "lattice_invite"
INVITE_COOKIE_TTL_SECONDS = 60 * 60 * 24 * 7

# Host-capacity plain-language judgment for System basic mode (layout rebuild).
# Thresholds live here so the frontend never re-derives copy from raw percents.
SYSINFO_READINESS_ROOMY_MAX = 55.0
SYSINFO_READINESS_TIGHT_MAX = 80.0


def host_capacity_readiness(
    *,
    cpu_pct: float = 0.0,
    ram_pct: float = 0.0,
    gpu_mem_pct: float = 0.0,
) -> str:
    """Map host telemetry to a single plain-language readiness bucket.

    Returns one of ``roomy`` / ``tight`` / ``low``. The product uses this so
    basic-mode System copy and advanced-mode numbers share one judgment.
    """
    load = max(float(cpu_pct or 0.0), float(ram_pct or 0.0), float(gpu_mem_pct or 0.0))
    if load <= SYSINFO_READINESS_ROOMY_MAX:
        return "roomy"
    if load <= SYSINFO_READINESS_TIGHT_MAX:
        return "tight"
    return "low"


def _sign_invite_cookie(secret: str, *, now: Optional[int] = None) -> str:
    """Create an expiring, nonce-bearing invite-gate capability cookie."""

    issued_at = int(time.time() if now is None else now)
    expires_at = issued_at + INVITE_COOKIE_TTL_SECONDS
    nonce = secrets.token_urlsafe(24)
    payload = f"{expires_at}.{nonce}"
    signature = hmac.new(
        str(secret).encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"v1.{payload}.{signature}"


def _verify_invite_cookie(
    value: Optional[str],
    secret: str,
    *,
    now: Optional[int] = None,
) -> bool:
    """Verify version, expiry and HMAC without trusting client claims."""

    if not value or not secret:
        return False
    try:
        version, raw_expiry, nonce, supplied_signature = value.split(".", 3)
        expires_at = int(raw_expiry)
    except (TypeError, ValueError):
        return False
    if version != "v1" or not nonce or expires_at <= int(time.time() if now is None else now):
        return False
    payload = f"{expires_at}.{nonce}"
    expected_signature = hmac.new(
        str(secret).encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(supplied_signature, expected_signature)


def _invite_denied_response() -> HTMLResponse:
    return HTMLResponse(
        content="""
            <body style="background:#0f1115; color:white; display:flex; flex-direction:column; align-items:center; justify-content:center; height:100vh; font-family:sans-serif;">
                <div style="background:#16191f; padding:40px; border-radius:24px; border:1px solid rgba(255,255,255,0.1); text-align:center; box-shadow: 0 20px 40px rgba(0,0,0,0.5);">
                    <div style="font-size:48px; margin-bottom:20px;">🔒</div>
                    <h1 style="color:#378ADD; margin:0; font-size:24px;">Invitation Required</h1>
                    <p style="color:#94a3b8; margin:20px 0; line-height:1.6;">이 서비스는 비공개로 운영되고 있습니다.<br>선생님께 받은 <b>초대용 전용 링크</b>를 통해 접속해 주세요.</p>
                    <div style="margin-top:30px; padding-top:20px; border-top:1px solid rgba(255,255,255,0.05); font-size:11px; color:rgba(255,255,255,0.2); letter-spacing:1px;">LATTICE AI</div>
                </div>
            </body>
        """,
        status_code=403,
    )


def ui_file_response(path: Path) -> FileResponse:
    response = FileResponse(path)
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    response.headers["Content-Security-Policy"] = PRODUCTION_CSP
    return response

@dataclass(frozen=True)
class StaticRoutesBundle:
    router: APIRouter
    ui_file_response: Callable[[Path], FileResponse]
    local_sysinfo: Callable[[Request], object]
    invite_authorized: Callable[[Request], bool]


def create_static_routes_router(
    *,
    static_dir: Path,
    invite_gate_enabled: bool,
    invite_code: str,
    app_mode: str,
    model_router,
    require_user,
    invite_cookie_secret: str = "",
    secure_cookies: bool = False,
) -> StaticRoutesBundle:
    api_router = APIRouter()
    STATIC_DIR = static_dir
    INVITE_GATE_ENABLED = invite_gate_enabled
    INVITE_CODE = invite_code
    INVITE_COOKIE_SECRET = invite_cookie_secret or secrets.token_urlsafe(48)
    SECURE_COOKIES = bool(secure_cookies)
    APP_MODE = app_mode
    router = model_router

    def invite_authorized(request: Request) -> bool:
        """Verify the request's signed invite claim at every gated boundary."""

        if not INVITE_GATE_ENABLED:
            return True
        return _verify_invite_cookie(
            request.cookies.get(INVITE_COOKIE_NAME),
            INVITE_COOKIE_SECRET,
        )

    @api_router.get("/")
    async def root(
        request: Request,
        code: Optional[str] = None,
        invite_cookie: Optional[str] = Cookie(None, alias=INVITE_COOKIE_NAME),
    ):
        """로그인/회원가입 페이지. 초대 게이트 활성화 시 코드 검증 후 진입."""
        if not INVITE_GATE_ENABLED:
            return app_redirect("account", request)

        # 1. 유효한 서버 서명 쿠키가 있는 경우
        if invite_authorized(request):
            return app_redirect("account")

        # 2. 초대 코드가 일치하는 경우 (최초 진입)
        if INVITE_CODE and code and secrets.compare_digest(code, INVITE_CODE):
            # Do not retain the invitation code in the redirect URL/history.
            response = app_redirect("account")
            response.set_cookie(
                key=INVITE_COOKIE_NAME,
                value=_sign_invite_cookie(INVITE_COOKIE_SECRET),
                httponly=True,
                secure=SECURE_COOKIES,
                samesite="lax",
                max_age=INVITE_COOKIE_TTL_SECONDS,
                path="/",
            )
            return response

        # 3. 인증 실패 시 차단 화면
        return _invite_denied_response()
    
    
    @api_router.get("/account")
    async def account_page(
        request: Request,
        invite_cookie: Optional[str] = Cookie(None, alias=INVITE_COOKIE_NAME),
    ):
        """Direct login/register page route used by logout and manual navigation."""
        if INVITE_GATE_ENABLED and not invite_authorized(request):
            return _invite_denied_response()
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
    async def app_shell(
        request: Request,
        invite_cookie: Optional[str] = Cookie(None, alias=INVITE_COOKIE_NAME),
    ):
        """React desktop single-page workspace shell."""
        if INVITE_GATE_ENABLED and not invite_authorized(request):
            return _invite_denied_response()
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
    async def status(request: Request):
        """서버 상태 및 현재 로드된 모델 정보를 반환합니다."""
        require_user(request)
        return {
            "message": "🧠 Lattice AI MLX Server is running!",
            "status": "online",
            "mode": APP_MODE,
            "loaded_model": router._current or "None"
        }
    
    
    @api_router.get("/local/sysinfo")
    async def local_sysinfo(request: Request):
        """CPU / RAM / GPU(MLX) usage plus a plain-language readiness bucket.

        ``readiness`` is ``roomy`` | ``tight`` | ``low`` so basic System copy
        does not re-interpret raw percents on the client.
        """
        require_user(request)
        import re as _re
        result: Dict[str, Any] = {
            "cpu_pct": 0.0,
            "ram_pct": 0.0,
            "gpu_mem_pct": 0.0,
            "gpu_mem_gb": 0.0,
            "readiness": "roomy",
        }
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
                quiet()
        except Exception as e:
            result["error"] = str(e)
        result["readiness"] = host_capacity_readiness(
            cpu_pct=float(result.get("cpu_pct") or 0.0),
            ram_pct=float(result.get("ram_pct") or 0.0),
            gpu_mem_pct=float(result.get("gpu_mem_pct") or 0.0),
        )
        return result

    return StaticRoutesBundle(
        api_router,
        ui_file_response,
        local_sysinfo,
        invite_authorized,
    )
