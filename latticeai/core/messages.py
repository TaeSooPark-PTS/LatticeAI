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
    # ── agent_seam ────────────────────────────────────────────────────────
    "agent_seam.disabled": {
        "ko": '워커 시임이 꺼져 있습니다. 호스트가 직접 띄운 워커에서만 열립니다.',
        "en": 'The worker seam is off. It opens only in a worker the host started itself.',
    },
    "agent_seam.max_tokens_out_of_range": {
        "ko": '한 번에 만들 길이는 {min}에서 {max} 사이여야 합니다.',
        "en": 'Ask for between {min} and {max} tokens in one completion.',
    },
    "agent_seam.message_required": {
        "ko": '모델에 보낼 내용을 적어주세요.',
        "en": 'Write the message to send to the model.',
    },
    "agent_seam.temperature_out_of_range": {
        "ko": '온도는 {min}에서 {max} 사이여야 합니다.',
        "en": 'Temperature has to be between {min} and {max}.',
    },
    "agent_seam.tool_blocked": {
        "ko": "'{tool}' 도구는 어떤 모드에서도 차단됩니다 ({reason}).",
        "en": "'{tool}' is blocked in every mode ({reason}).",
    },
    "agent_seam.tool_fail_closed": {
        "ko": "'{tool}' 도구는 기존 내용을 바꾸지만 검토할 수 있는 제안으로 만들 수 없어 차단했습니다 ({reason}).",
        "en": "'{tool}' would change existing content but cannot be staged as a reviewable proposal, so it is blocked ({reason}).",
    },
    "agent_seam.tool_required": {
        "ko": '실행할 도구 이름이 필요합니다.',
        "en": 'A tool name to run is required.',
    },
    # ── auth ────────────────────────────────────────────────────────
    "auth.user_not_found": {
        "ko": '사용자를 찾을 수 없습니다.',
        "en": 'No such user.',
    },
    # ── chat ────────────────────────────────────────────────────────
    "chat.model_not_loaded": {
        "ko": "'{model}' 모델이 아직 준비되지 않았습니다.",
        "en": "Model '{model}' is not loaded.",
    },
    # ── common ────────────────────────────────────────────────────────
    "common.file_not_found": {
        "ko": '파일을 찾을 수 없습니다.',
        "en": 'File not found.',
    },
    # ── models ────────────────────────────────────────────────────────
    "models.download_consent_required": {
        "ko": '모델 내려받기는 사용자가 직접 동의해야 시작됩니다.',
        "en": 'Model downloads require explicit consent (allow_download=true).',
    },
    "models.download_not_automated": {
        "ko": '{provider} 엔진의 모델 내려받기는 아직 자동화되지 않았습니다.',
        "en": 'Model downloads for the {provider} engine are not automated yet.',
    },
    "models.download_timeout": {
        "ko": '모델 내려받기 시간이 초과되었습니다.',
        "en": 'The model download timed out.',
    },
    "models.identifier_empty": {
        "ko": '모델 식별자가 비어 있습니다.',
        "en": 'The model identifier is empty.',
    },
    "models.name_empty": {
        "ko": '모델 이름이 비어 있습니다.',
        "en": 'The model name is empty.',
    },
    "models.ollama_missing": {
        "ko": 'Ollama가 설치되어 있지 않습니다.',
        "en": 'Ollama is not installed.',
    },
    "models.other_user_credentials": {
        "ko": '다른 사용자의 모델 자격 증명을 사용할 수 없습니다.',
        "en": "You cannot use another user's model credentials.",
    },
    "models.public_mode_blocks_local": {
        "ko": '공개 모드에서는 로컬 MLX 모델을 불러올 수 없습니다. openai:, openrouter:, groq:, together: 모델을 쓰거나 LATTICEAI_ALLOW_LOCAL_MODELS=true 로 설정하세요.',
        "en": 'Public mode does not load local MLX models. Use an openai:, openrouter:, groq: or together: model, or set LATTICEAI_ALLOW_LOCAL_MODELS=true.',
    },
    "models.pull_failed": {
        "ko": '모델 내려받기에 실패했습니다.',
        "en": 'The model download failed.',
    },
    # ── tools ────────────────────────────────────────────────────────
    "tools.path_outside_workspace": {
        "ko": '경로가 작업 공간 밖입니다.',
        "en": 'That path is outside the workspace.',
    },
    "tools.pdf_render_failed": {
        "ko": 'PDF를 그리지 못했습니다: {reason}',
        "en": 'The PDF could not be rendered: {reason}',
    },
    # ── worker_compute ────────────────────────────────────────────────────────
    "worker_compute.audio_too_large": {
        "ko": '오디오가 {size} bytes 입니다. 한도는 {limit} bytes 입니다.',
        "en": 'The audio is {size} bytes; the limit is {limit} bytes.',
    },
    "worker_compute.audio_unsupported": {
        "ko": "'{suffix}' 은(는) 지원하는 오디오 형식이 아닙니다. {allowed} 만 가능합니다.",
        "en": "'{suffix}' is not a supported audio container. Supported: {allowed}.",
    },
    "worker_compute.content_invalid": {
        "ko": '본문을 base64로 읽지 못했습니다: {reason}',
        "en": 'The payload is not valid base64: {reason}',
    },
    "worker_compute.embedder_unavailable": {
        "ko": '임베딩 공급자가 연결되어 있지 않습니다.',
        "en": 'No embedding provider is connected to this worker.',
    },
    "worker_compute.extract_kind_invalid": {
        "ko": "'{kind}' 은(는) 추출 종류가 아닙니다. {allowed} 중 하나여야 합니다.",
        "en": "'{kind}' is not an extraction kind. Use one of {allowed}.",
    },
    "worker_compute.kind_invalid": {
        "ko": "'{kind}' 은(는) 임베딩 종류가 아닙니다. {allowed} 중 하나여야 합니다.",
        "en": "'{kind}' is not an embedding kind. Use one of {allowed}.",
    },
    "worker_compute.parse_failed": {
        "ko": '문서를 읽지 못했습니다: {reason}',
        "en": 'The document could not be parsed: {reason}',
    },
    "worker_compute.render_failed": {
        "ko": "'{kind}' 문서 생성이 실패했습니다: {reason}",
        "en": "Rendering the '{kind}' document failed: {reason}",
    },
    "worker_compute.render_unavailable": {
        "ko": "이 워커는 '{kind}' 문서를 만들 수 없습니다: {reason}",
        "en": "This worker cannot render '{kind}' documents: {reason}",
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
