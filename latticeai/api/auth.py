"""Authentication API router: register, login, logout, SSO, profile."""

import base64
import hashlib
import logging
import secrets
import time
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

from latticeai.core.users import normalize_email
from latticeai.core.oidc import (
    OIDCValidationError,
    fetch_jwks as _default_fetch_jwks,
    verify_id_token as _default_verify_id_token,
)


class UserRegister(BaseModel):
    email: str
    password: str
    name: str
    nickname: str


class UserLogin(BaseModel):
    email: str
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class UpdateProfileRequest(BaseModel):
    name: Optional[str] = None
    nickname: Optional[str] = None


# state → (issued_at, nonce). The nonce binds the eventual ID token to *this*
# login attempt (replay / token-injection defence); the timestamp expires it.
_sso_states: Dict[str, Tuple[float, str]] = {}


def create_auth_router(
    *,
    load_users: Callable[[], Dict],
    save_users: Callable[[Dict], None],
    hash_password: Callable[[str], str],
    verify_and_migrate: Callable[[str, str, str, Dict], bool],
    create_session: Callable[[str], str],
    get_session_email: Callable[[str], Optional[str]],
    invalidate_session: Callable[[str], None],
    extract_bearer_token: Callable[[Request], Optional[str]],
    get_user_role: Callable[[str, Optional[Dict]], str],
    require_user: Callable[[Request], str],
    check_ip_rate_limit: Callable[..., None],
    client_ip: Callable[[Request], str],
    get_sso_settings: Callable[[], Dict],
    get_sso_discovery: Callable[[], Any],
    public_sso_config: Callable[..., Dict],
    open_registration: bool,
    session_ttl: int,
    require_auth: bool = True,
    ensure_identity: Optional[Callable[[str, Dict], None]] = None,
    verify_id_token: Callable[..., Dict] = _default_verify_id_token,
    fetch_jwks: Callable[[str], Awaitable[Dict]] = _default_fetch_jwks,
) -> APIRouter:
    router = APIRouter()

    def _enforce_password_policy(password: str) -> None:
        # Real policy (v4): length >= 8 with letters AND digits. A 4-char
        # minimum was not a policy.
        pw = str(password or "")
        if len(pw) < 8 or not any(c.isalpha() for c in pw) or not any(c.isdigit() for c in pw):
            raise HTTPException(
                status_code=400,
                detail="비밀번호는 8자 이상이며 영문자와 숫자를 모두 포함해야 합니다.",
            )

    @router.post("/register")
    async def register(req: UserRegister, request: Request):
        check_ip_rate_limit(client_ip(request), "register", max_calls=5, window_secs=3600)
        if not open_registration:
            raise HTTPException(status_code=403, detail="회원가입이 비활성화되어 있습니다. 관리자에게 문의하세요.")
        _enforce_password_policy(req.password)
        email = normalize_email(req.email)
        users = load_users()
        if email in users:
            raise HTTPException(status_code=400, detail="이미 존재하는 이메일입니다.")
        role = "admin" if not users else "user"
        users[email] = {
            "password": hash_password(req.password),
            "name": req.name,
            "nickname": req.nickname,
            "role": role,
            "disabled": False,
        }
        if ensure_identity is not None:
            ensure_identity(email, users[email])
        save_users(users)
        msg = "회원가입 성공! 첫 번째 사용자로 관리자 권한이 부여되었습니다." if role == "admin" else "회원가입 성공!"
        return {"status": "ok", "message": msg, "role": role}

    @router.post("/login")
    async def login(req: UserLogin, request: Request):
        check_ip_rate_limit(client_ip(request), "login", max_calls=10, window_secs=300)
        email = normalize_email(req.email)
        users = load_users()
        user = users.get(email)
        if not user or not verify_and_migrate(email, req.password, user.get("password", ""), users):
            raise HTTPException(status_code=401, detail="이메일 또는 비밀번호가 틀렸습니다.")
        if user.get("disabled"):
            raise HTTPException(status_code=403, detail="비활성화된 계정입니다.")
        role = get_user_role(email, users)
        token = create_session(email)
        response = JSONResponse(content={
            "status": "ok",
            "nickname": user["nickname"],
            "name": user["name"],
            "email": email,
            "role": role,
            "is_admin": role == "admin",
        })
        response.set_cookie(key="session_token", value=token, httponly=True, samesite="lax", max_age=session_ttl)
        return response

    @router.get("/auth/sso/config")
    async def sso_config_endpoint():
        return public_sso_config()

    @router.get("/auth/sso/login")
    async def sso_login():
        settings = get_sso_settings()
        discovery = await get_sso_discovery()
        if not settings.get("enabled") or not discovery:
            raise HTTPException(status_code=503, detail="SSO가 설정되지 않았습니다.")
        state = secrets.token_urlsafe(16)
        nonce = secrets.token_urlsafe(16)
        # PKCE (S256): bind the token exchange to this login, so an
        # intercepted authorization code is useless without the verifier.
        code_verifier = secrets.token_urlsafe(48)
        code_challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode("ascii")).digest())
            .rstrip(b"=")
            .decode("ascii")
        )
        _sso_states[state] = (time.time(), nonce, code_verifier)
        params = urlencode({
            "client_id": settings["client_id"],
            "response_type": "code",
            "redirect_uri": settings["redirect_uri"],
            "scope": settings.get("scopes") or "openid email profile",
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        })
        return RedirectResponse(f"{discovery['authorization_endpoint']}?{params}")

    @router.get("/auth/sso/callback")
    async def sso_callback(code: str = "", state: str = "", error: str = ""):
        if error:
            return RedirectResponse(f"/?sso_error={error}")
        entry = _sso_states.pop(state, None)
        if entry is None or time.time() - entry[0] > 300:
            raise HTTPException(status_code=400, detail="유효하지 않은 SSO 상태입니다.")
        _, nonce, code_verifier = entry
        settings = get_sso_settings()
        discovery = await get_sso_discovery()
        if not settings.get("enabled") or not discovery:
            raise HTTPException(status_code=503, detail="SSO 설정 오류입니다.")
        import httpx as _httpx
        async with _httpx.AsyncClient() as c:
            r = await c.post(discovery["token_endpoint"], data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings["redirect_uri"],
                "client_id": settings["client_id"],
                "client_secret": settings["client_secret"],
                "code_verifier": code_verifier,
            }, headers={"Accept": "application/json"}, timeout=15)
            tokens = r.json()
        id_token = tokens.get("id_token")
        if not id_token:
            raise HTTPException(status_code=400, detail="ID 토큰을 받지 못했습니다.")
        # Never trust a decoded JWT payload: verify signature (against the
        # provider JWKS), issuer, audience, expiry and the login nonce before
        # using any claim. Any failure is fail-closed (401).
        issuer = discovery.get("issuer") or ""
        try:
            jwks = await fetch_jwks(discovery.get("jwks_uri", ""))
            payload = verify_id_token(
                id_token,
                jwks=jwks,
                issuer=issuer,
                audience=settings["client_id"],
                nonce=nonce,
            )
        except OIDCValidationError as exc:
            logging.warning("SSO ID token rejected: %s", exc)
            raise HTTPException(status_code=401, detail="SSO 토큰 검증에 실패했습니다.")
        except Exception as exc:  # discovery/JWKS fetch failure → fail closed
            logging.warning("SSO token validation error: %s", exc)
            raise HTTPException(status_code=502, detail="SSO 공급자 검증에 실패했습니다.")
        email = normalize_email(payload.get("email") or payload.get("preferred_username") or payload.get("upn") or "")
        if not email:
            raise HTTPException(status_code=400, detail="이메일을 확인할 수 없습니다.")
        users = load_users()
        if email not in users:
            is_first = len(users) == 0
            users[email] = {
                "password": "",
                "name": payload.get("name", email.split("@")[0]),
                "nickname": payload.get("given_name", email.split("@")[0]),
                "role": "admin" if is_first else "user",
                "disabled": False,
                "sso": True,
            }
            if ensure_identity is not None:
                ensure_identity(email, users[email])
            save_users(users)
        if users[email].get("disabled"):
            raise HTTPException(status_code=403, detail="비활성화된 계정입니다.")
        token = create_session(email)
        resp = RedirectResponse("/app", status_code=302)
        resp.set_cookie("session_token", token, httponly=True, samesite="lax", max_age=session_ttl)
        return resp

    @router.post("/logout")
    async def logout(request: Request):
        token = extract_bearer_token(request)
        if token:
            invalidate_session(token)
        response = JSONResponse(content={"status": "ok"})
        response.delete_cookie("session_token")
        return response

    @router.post("/account/change-password")
    async def change_password(req: ChangePasswordRequest, request: Request):
        email = normalize_email(require_user(request))
        if not email:
            raise HTTPException(status_code=401, detail="인증이 필요합니다.")
        _enforce_password_policy(req.new_password)
        users = load_users()
        user = users.get(email)
        if not user:
            raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")
        if not verify_and_migrate(email, req.current_password, user.get("password", ""), users):
            raise HTTPException(status_code=401, detail="현재 비밀번호가 틀렸습니다.")
        users[email]["password"] = hash_password(req.new_password)
        save_users(users)
        return {"status": "ok", "message": "비밀번호가 변경되었습니다."}

    @router.patch("/account/profile")
    async def update_profile(req: UpdateProfileRequest, request: Request):
        email = normalize_email(require_user(request))
        if not email:
            raise HTTPException(status_code=401, detail="인증이 필요합니다.")
        if req.name is not None and not req.name.strip():
            raise HTTPException(status_code=400, detail="이름을 입력해주세요.")
        if req.nickname is not None and not req.nickname.strip():
            raise HTTPException(status_code=400, detail="닉네임을 입력해주세요.")
        users = load_users()
        user = users.get(email)
        if not user:
            raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")
        if req.name is not None:
            users[email]["name"] = req.name.strip()
        if req.nickname is not None:
            users[email]["nickname"] = req.nickname.strip()
        save_users(users)
        return {"status": "ok", "name": users[email]["name"], "nickname": users[email]["nickname"]}

    @router.get("/account/profile")
    async def get_profile(request: Request):
        email = normalize_email(require_user(request))
        if not email:
            if require_auth:
                raise HTTPException(status_code=401, detail="인증이 필요합니다.")
            return {"email": "", "name": "Local User", "nickname": "You", "role": "admin", "is_admin": True}
        users = load_users()
        user = users.get(email)
        if not user:
            raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")
        role = get_user_role(email, users)
        return {"email": email, "name": user.get("name", ""), "nickname": user.get("nickname", ""),
                "role": role, "is_admin": role == "admin"}

    return router
