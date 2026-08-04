"""Server-side message catalog.

Every string the API hands a person — an ``HTTPException`` detail, a status
line, a refusal — used to be a literal at the raise site, and the literals were
written in whichever language the author happened to be thinking in. The result
was one API that answered ``"사용자를 찾을 수 없습니다."`` and, two files over,
``"Knowledge Graph ingestion is disabled."``, so *every* user read half the
product in a language they had not chosen.

The rules here are the same three the frontend catalog follows:

1. **One id per message.** The id is what code refers to; the wording is data.
2. **Both languages or neither.** ``test_messages.py`` fails on a key present
   in one language and missing from the other, so a new message cannot ship
   half-translated.
3. **The caller never picks the language.** It comes from the request, via
   :func:`resolve_language`, so a message cannot be localized to whoever wrote
   the endpoint.

Usage::

    from latticeai.core.messages import http_error, resolve_language

    lang = resolve_language(request)
    raise http_error(404, "auth.user_not_found", lang)
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from fastapi import HTTPException

__all__ = [
    "DEFAULT_LANGUAGE",
    "SUPPORTED_LANGUAGES",
    "LANGUAGE_HEADER",
    "MESSAGES",
    "http_error",
    "resolve_language",
    "translate",
]

DEFAULT_LANGUAGE = "ko"
SUPPORTED_LANGUAGES = ("ko", "en")

#: Explicit override the frontend sends. Preferred over ``Accept-Language``
#: because the person picked this language *in the product*, whereas
#: ``Accept-Language`` is whatever their browser was installed with.
LANGUAGE_HEADER = "x-lattice-language"


MESSAGES: Dict[str, Dict[str, str]] = {
    # ── auth ────────────────────────────────────────────────────────────
    "auth.password_too_weak": {
        "ko": "비밀번호는 8자 이상이며 영문자와 숫자를 모두 포함해야 합니다.",
        "en": "Your password needs at least 8 characters, including letters and numbers.",
    },
    "auth.invitation_required": {
        "ko": "유효한 서명 초대 권한이 필요합니다.",
        "en": "A valid signed invitation is required.",
    },
    "auth.registration_disabled": {
        "ko": "회원가입이 비활성화되어 있습니다. 관리자에게 문의하세요.",
        "en": "Sign-up is turned off. Ask an administrator to enable it.",
    },
    "auth.email_taken": {
        "ko": "이미 존재하는 이메일입니다.",
        "en": "That email address is already registered.",
    },
    "auth.bad_credentials": {
        "ko": "이메일 또는 비밀번호가 틀렸습니다.",
        "en": "That email address or password is not correct.",
    },
    "auth.account_disabled": {
        "ko": "비활성화된 계정입니다.",
        "en": "This account has been disabled.",
    },
    "auth.login_required": {
        "ko": "인증이 필요합니다.",
        "en": "You need to sign in first.",
    },
    "auth.user_not_found": {
        "ko": "사용자를 찾을 수 없습니다.",
        "en": "No such user.",
    },
    "auth.current_password_wrong": {
        "ko": "현재 비밀번호가 틀렸습니다.",
        "en": "Your current password is not correct.",
    },
    "auth.name_required": {
        "ko": "이름을 입력해주세요.",
        "en": "Please enter a name.",
    },
    "auth.nickname_required": {
        "ko": "닉네임을 입력해주세요.",
        "en": "Please enter a nickname.",
    },
    # ── SSO ─────────────────────────────────────────────────────────────
    "sso.not_configured": {
        "ko": "SSO가 설정되지 않았습니다.",
        "en": "Single sign-on is not set up.",
    },
    "sso.invalid_state": {
        "ko": "유효하지 않은 SSO 상태입니다.",
        "en": "That sign-on request is no longer valid. Please start again.",
    },
    "sso.config_error": {
        "ko": "SSO 설정 오류입니다.",
        "en": "Single sign-on is misconfigured.",
    },
    "sso.no_id_token": {
        "ko": "ID 토큰을 받지 못했습니다.",
        "en": "The identity provider did not return an ID token.",
    },
    "sso.token_verification_failed": {
        "ko": "SSO 토큰 검증에 실패했습니다.",
        "en": "The sign-on token could not be verified.",
    },
    "sso.provider_verification_failed": {
        "ko": "SSO 공급자 검증에 실패했습니다.",
        "en": "The sign-on provider could not be verified.",
    },
    "sso.email_unavailable": {
        "ko": "이메일을 확인할 수 없습니다.",
        "en": "The identity provider did not share an email address.",
    },
    "sso.invitation_required": {
        "ko": "신규 SSO 계정에는 유효한 서명 초대 권한이 필요합니다.",
        "en": "New single sign-on accounts need a valid signed invitation.",
    },
    # ── admin ───────────────────────────────────────────────────────────
    "admin.invalid_role": {
        "ko": "role은 admin 또는 user만 가능합니다.",
        "en": "Role must be either 'admin' or 'user'.",
    },
    "admin.cannot_disable_self": {
        "ko": "자기 자신은 비활성화할 수 없습니다.",
        "en": "You cannot disable your own account.",
    },
    "admin.cannot_delete_self": {
        "ko": "자기 자신은 삭제할 수 없습니다.",
        "en": "You cannot delete your own account.",
    },
    # ── capture / ingestion ─────────────────────────────────────────────
    "capture.ingestion_disabled": {
        "ko": "지식 그래프 수집이 꺼져 있습니다.",
        "en": "Knowledge Graph ingestion is turned off.",
    },
    "capture.payload_too_large": {
        "ko": "보낸 내용이 너무 큽니다.",
        "en": "That capture is too large to accept.",
    },
    "capture.nothing_to_capture": {
        "ko": "저장할 내용이 없습니다. 텍스트나 페이지 내용을 함께 보내주세요.",
        "en": "Nothing to capture — send text, html, or a selection.",
    },
}


def resolve_language(request: Any, default: str = DEFAULT_LANGUAGE) -> str:
    """Language for this request: the product's choice, then the browser's.

    Never raises and never returns an unsupported language — an endpoint that
    cannot resolve a language must still be able to answer.
    """
    headers: Optional[Mapping[str, str]] = getattr(request, "headers", None)
    if headers is None:
        return default

    explicit = _normalize(headers.get(LANGUAGE_HEADER))
    if explicit:
        return explicit

    # `Accept-Language: en-GB,en;q=0.9,ko;q=0.8` — take the first tag we
    # support, honouring the order the browser sent (which is the priority
    # order) rather than re-sorting by q-value, since the two agree in every
    # real header and disagreeing ones are malformed anyway.
    for part in (headers.get("accept-language") or "").split(","):
        candidate = _normalize(part.split(";", 1)[0])
        if candidate:
            return candidate
    return default


def _normalize(value: Optional[str]) -> Optional[str]:
    """``"en-GB"`` → ``"en"`` when supported, else None."""
    if not value:
        return None
    tag = value.strip().lower().replace("_", "-")
    if not tag:
        return None
    base = tag.split("-", 1)[0]
    return base if base in SUPPORTED_LANGUAGES else None


def translate(key: str, language: str = DEFAULT_LANGUAGE, **params: Any) -> str:
    """Localized text for ``key``.

    An unknown key returns the key itself rather than raising: a missing
    message must not turn a 404 into a 500. The namespaced key is also
    recognisable in a bug report, which a generic "an error occurred" is not.
    """
    entry = MESSAGES.get(key)
    if entry is None:
        return key
    text = entry.get(language) or entry.get(DEFAULT_LANGUAGE) or key
    for name, value in params.items():
        text = text.replace("{" + name + "}", str(value))
    return text


def http_error(
    status_code: int,
    key: str,
    language: str = DEFAULT_LANGUAGE,
    *,
    headers: Optional[Dict[str, str]] = None,
    **params: Any,
) -> HTTPException:
    """An ``HTTPException`` whose detail is localized for this request.

    Returned, not raised, so call sites keep reading ``raise http_error(...)``
    and the traceback still points at the endpoint.
    """
    return HTTPException(
        status_code=status_code,
        detail=translate(key, language, **params),
        headers=headers,
    )
