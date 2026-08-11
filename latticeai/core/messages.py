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
    # ── shared ──────────────────────────────────────────────────────────
    "common.user_mismatch": {
        "ko": "로그인한 사용자와 요청한 사용자가 다릅니다.",
        "en": "user_email must match the authenticated user.",
    },
    "common.workspace_mismatch": {
        "ko": "작업 공간 지정이 서로 다릅니다.",
        "en": "Workspace selectors must match.",
    },
    "common.workspace_unreadable": {
        "ko": "'{workspace}' 작업 공간을 읽을 권한이 없습니다.",
        "en": "Workspace '{workspace}' is not readable.",
    },
    "common.file_not_found": {
        "ko": "파일을 찾을 수 없습니다.",
        "en": "File not found.",
    },
    "common.path_required": {
        "ko": "경로를 입력해주세요.",
        "en": "A path is required.",
    },
    "common.graph_disabled": {
        "ko": "지식 그래프가 꺼져 있습니다.",
        "en": "The Knowledge Graph is turned off.",
    },
    "common.graph_unavailable": {
        "ko": "지식 그래프를 사용할 수 없습니다.",
        "en": "The Knowledge Graph is not available.",
    },
    # ── chat ────────────────────────────────────────────────────────────
    "chat.model_not_loaded": {
        "ko": "'{model}' 모델이 아직 준비되지 않았습니다.",
        "en": "Model '{model}' is not loaded.",
    },
    "chat.no_model_loaded": {
        "ko": "불러온 모델이 없습니다. 모델을 먼저 준비해주세요.",
        "en": "No model is loaded. Prepare a model first.",
    },
    "chat.conversation_not_found": {
        "ko": "대화를 찾을 수 없습니다.",
        "en": "That conversation no longer exists.",
    },
    "chat.file_name_collision": {
        "ko": "'{name}' 이름의 파일이 너무 많습니다. 다른 이름을 지정해 주세요.",
        "en": "Too many files are already named '{name}'. Please choose another name.",
    },
    "chat.file_generation_failed": {
        "ko": "파일 내용을 만들지 못했습니다.",
        "en": "The file content could not be generated.",
    },
    # ── memory / graph ──────────────────────────────────────────────────
    "memory.unknown_source": {
        "ko": "'{source}'는 알 수 없는 기억 출처입니다.",
        "en": "Unknown memory source: {source}.",
    },
    # ── self-model (personal ontology) ──────────────────────────────────
    "self_model.invalid_kind": {
        "ko": "나 / 선호 / 습관 / 결정 / 관계 중 하나를 골라주세요.",
        "en": "Choose one of: trait, preference, habit, decision, relationship.",
    },
    "self_model.text_required": {
        "ko": "저장할 내용을 적어주세요.",
        "en": "Please write what should be remembered.",
    },
    "self_model.not_found": {
        "ko": "그 항목은 이미 없습니다.",
        "en": "That entry is already gone.",
    },
    "self_model.not_self_model": {
        "ko": "내 프로필 항목이 아니라서 지울 수 없습니다.",
        "en": "That is not a profile entry, so it cannot be removed here.",
    },
    "self_model.not_a_proposal": {
        "ko": "그 검토 항목은 내 프로필 제안이 아닙니다.",
        "en": "That review item is not a profile proposal.",
    },
    "self_model.empty_proposal": {
        "ko": "그 제안에는 저장할 내용이 없습니다.",
        "en": "That proposal carries nothing to save.",
    },
    "self_model.graph_unavailable": {
        "ko": "지식 그래프가 꺼져 있어 내 프로필을 쓸 수 없습니다.",
        "en": "The knowledge graph is off, so the profile cannot be written.",
    },
    "self_model.queue_unavailable": {
        "ko": "검토함이 연결되어 있지 않아 제안을 처리할 수 없습니다.",
        "en": "The review queue is not connected, so proposals cannot be handled.",
    },
    "self_model.invalid": {
        "ko": "내 프로필을 바꾸지 못했습니다.",
        "en": "The profile could not be changed.",
    },
    "graph.node_id_required": {
        "ko": "노드 식별자가 필요합니다.",
        "en": "A node id is required.",
    },
    "graph.node_not_found": {
        "ko": "노드를 찾을 수 없습니다.",
        "en": "That node was not found.",
    },
    "graph.unsupported_type": {
        "ko": "지원하는 종류: message, ai_response, note",
        "en": "Supported types are: message, ai_response, note.",
    },
    # ── ingestion / folders ─────────────────────────────────────────────
    "ingestion.job_not_found": {
        "ko": "수집 작업을 찾을 수 없습니다.",
        "en": "That ingestion job was not found.",
    },
    "ingestion.watch_unavailable": {
        "ko": "폴더 감시 기능을 사용할 수 없습니다.",
        "en": "The folder watch service is unavailable.",
    },
    "ingestion.watch_enable_failed": {
        "ko": "폴더 감시를 켜지 못했습니다.",
        "en": "Turning folder watch on did not succeed.",
    },
    "ingestion.watch_selector_required": {
        "ko": "감시 항목 또는 경로가 필요합니다.",
        "en": "A watch id or a path is required.",
    },
    "ingestion.watch_not_found": {
        "ko": "감시 중인 폴더를 찾을 수 없습니다.",
        "en": "That watched folder was not found.",
    },
    "portability.verification_failed": {
        "ko": "보관 파일 검증에 실패했습니다.",
        "en": "Archive verification failed.",
    },
    "portability.brain_network_disabled": {
        "ko": "다른 Brain과의 공유 기능이 꺼져 있습니다. 기본값이 꺼짐이며, "
              "LATTICEAI_BRAIN_NETWORK=1 로 직접 켜야 사용할 수 있습니다.",
        "en": "Sharing with another Brain is off. It is off by default; set "
              "LATTICEAI_BRAIN_NETWORK=1 to turn it on.",
    },
    "portability.review_queue_unavailable": {
        "ko": "검토함이 연결되어 있지 않아 받은 지식을 제안으로 쌓을 수 없습니다.",
        "en": "The review queue is not connected, so received knowledge cannot be queued as proposals.",
    },
    "ingestion.vault_path_required": {
        "ko": "Obsidian 보관함 폴더 경로가 필요합니다.",
        "en": "An Obsidian vault folder path is required.",
    },
    # ── review queue ────────────────────────────────────────────────────
    "review.item_not_found": {
        "ko": "검토 항목을 찾을 수 없습니다.",
        "en": "That review item was not found.",
    },
    "review.cannot_approve_in_status": {
        "ko": "'{status}' 상태의 검토 항목은 승인할 수 없습니다.",
        "en": "A review item in status '{status}' cannot be approved.",
    },
    "review.bulk_ids_required": {
        "ko": "한꺼번에 처리할 검토 항목을 하나 이상 골라 주세요.",
        "en": "Choose at least one review item to act on.",
    },
    "review.bulk_too_many": {
        "ko": "한 번에 최대 {cap}개까지 처리할 수 있습니다.",
        "en": "At most {cap} items can be handled in one request.",
    },
    # ── interop bridges (Notion export / git / mail / calendar) ──────────
    "ingestion.interop_path_required": {
        "ko": "불러올 파일이나 폴더 경로가 필요합니다.",
        "en": "A file or folder path to read is required.",
    },
    "ingestion.interop_unknown_source": {
        "ko": "알 수 없는 연동 종류입니다: {source}",
        "en": "Unknown interop source: {source}",
    },
    "ingestion.vault_watch_unavailable": {
        "ko": "보관함 자동 확인 기능을 사용할 수 없습니다.",
        "en": "Vault watch is unavailable.",
    },
    "ingestion.vault_watch_disabled": {
        "ko": "보관함 자동 확인은 기본으로 꺼져 있습니다. 설정에서 켜야 사용할 수 있습니다.",
        "en": "Vault watch is off by default; turn it on in settings to use it.",
    },
    # ── projects ────────────────────────────────────────────────────────
    "project.not_found": {
        "ko": "프로젝트를 찾을 수 없습니다.",
        "en": "That project was not found.",
    },
    # ── network boundary ────────────────────────────────────────────────
    "boundary.policy_not_configured": {
        "ko": "혼합 검색 정책 서비스가 설정되지 않았습니다.",
        "en": "The hybrid policy service is not configured.",
    },
    # ── feature toggles ─────────────────────────────────────────────────
    # The switchboard renders from the server (see
    # ``latticeai/services/feature_toggles.py``), so every label and one-line
    # explanation a person reads lives here. The rule for the summaries: say
    # what turning it *on* does, in the words someone who never read the
    # environment-variable docs would use.
    "features.note": {
        "ko": "모두 지금 바로 적용됩니다. 다시 시작하지 않아도 됩니다.",
        "en": "Every switch here takes effect right away — no restart needed.",
    },
    "features.unknown": {
        "ko": "그런 기능은 없습니다: {feature}",
        "en": "There is no such feature: {feature}",
    },
    "features.invalid_value": {
        "ko": "이 기능에 쓸 수 없는 값입니다: {value}",
        "en": "That is not a value this feature can take: {value}",
    },
    "features.choice.install_required": {
        "ko": "설치 필요 — {reason}",
        "en": "Install required — {reason}",
    },
    "features.allow_multimodal.label": {
        "ko": "사진·녹음도 기억하기",
        "en": "Remember pictures and recordings",
    },
    "features.allow_multimodal.summary": {
        "ko": "폴더를 읽을 때 글뿐 아니라 사진과 녹음도 함께 저장합니다.",
        "en": "A folder scan stores pictures and recordings too, not just text.",
    },
    "features.video_ingest.label": {
        "ko": "영상도 함께",
        "en": "Include videos",
    },
    "features.video_ingest.summary": {
        "ko": "사진·녹음을 켠 상태에서, 영상은 장면과 자막으로 저장합니다.",
        "en": "With the switch above on, videos are stored as keyframes and subtitles.",
    },
    "features.vault_watch.label": {
        "ko": "노트 보관함 지켜보기",
        "en": "Watch my notes vault",
    },
    "features.vault_watch.summary": {
        "ko": "밖에 있는 노트 보관함이 바뀌면 알아서 다시 읽어옵니다.",
        "en": "When an outside notes vault changes, it is re-read on its own.",
    },
    "features.brain_network.label": {
        "ko": "골라서 나누기",
        "en": "Share selected knowledge",
    },
    "features.brain_network.summary": {
        "ko": "내가 고른 기억 묶음만 다른 기기로 내보내고 받아올 수 있습니다.",
        "en": "Lets you export a hand-picked slice of memory to another device, and receive one.",
    },
    "features.brain_network.caution": {
        "ko": "이 기능만 기억을 이 컴퓨터 밖으로 내보냅니다. 받은 내용은 바로 합쳐지지 않고 검토함으로 갑니다.",
        "en": "This is the one switch that sends memory off this computer. Anything received waits in the review inbox instead of merging.",
    },
    "features.synthesis.label": {
        "ko": "스스로 정리하기",
        "en": "Tidy up on its own",
    },
    "features.synthesis.summary": {
        "ko": "자료가 쌓이면 알아서 훑어보고, 고칠 거리를 검토함에 제안합니다.",
        "en": "As material piles up, the Brain reviews it and proposes tidy-ups in the review inbox.",
    },
    "features.auto_vector_index.label": {
        "ko": "넣자마자 검색 준비",
        "en": "Make new material searchable at once",
    },
    "features.auto_vector_index.summary": {
        "ko": "새 자료를 넣으면 바로 의미 검색까지 준비합니다. 끄면 나중에 한 번에 만듭니다.",
        "en": "New material is prepared for meaning-based search immediately; off means you rebuild later.",
    },
    "features.auto_late_fusion.label": {
        "ko": "글로 사진 찾기",
        "en": "Find pictures by typing",
    },
    "features.auto_late_fusion.summary": {
        "ko": "글로 물어봐도 사진까지 함께 찾습니다. 사진을 읽는 모델이 있어야 켜집니다.",
        "en": "A typed question also searches pictures — needs a vision model that shares the same space.",
    },
    "features.fusion_rrf.label": {
        "ko": "검색 결과 합치는 방식 바꾸기",
        "en": "Blend search results by rank",
    },
    "features.fusion_rrf.summary": {
        "ko": "점수 대신 순위로 합칩니다. 검색 채널마다 점수 크기가 달라도 흔들리지 않습니다.",
        "en": "Combines channels by position instead of score, so mismatched score scales stop skewing results.",
    },
    "features.graph_expansion.label": {
        "ko": "옆에 있는 기억까지 보기",
        "en": "Look at neighbouring memories",
    },
    "features.graph_expansion.summary": {
        "ko": "찾은 기억과 바로 이어진 기억도 후보로 넣습니다. 답은 넓어지고 조금 흐려집니다.",
        "en": "Adds memories one link away from a hit as candidates — wider answers, slightly less focused.",
    },
    "features.vector_backend.label": {
        "ko": "의미 검색 방식",
        "en": "Meaning-search engine",
    },
    "features.vector_backend.summary": {
        "ko": "빠르기와 정확함 사이에서 고릅니다. 기본값은 전부 훑어보는 정확한 방식입니다.",
        "en": "Trade speed against exactness. The default compares everything and is exact.",
    },
    "features.vector_backend.choice.brute": {
        "ko": "전부 비교 (정확)",
        "en": "Compare everything (exact)",
    },
    "features.vector_backend.choice.quantized": {
        "ko": "간추려 비교 (빠름)",
        "en": "Compare compressed (faster)",
    },
    "features.vector_backend.choice.hnsw": {
        "ko": "근사 검색 (가장 빠름)",
        "en": "Approximate search (fastest)",
    },
    # ── models ──────────────────────────────────────────────────────────
    "models.other_user_credentials": {
        "ko": "다른 사용자의 모델 자격 증명을 사용할 수 없습니다.",
        "en": "You cannot use another user's model credentials.",
    },
    "models.download_consent_required": {
        "ko": "모델 내려받기는 사용자가 직접 동의해야 시작됩니다.",
        "en": "Model downloads require explicit consent (allow_download=true).",
    },
    "models.identifier_empty": {
        "ko": "모델 식별자가 비어 있습니다.",
        "en": "The model identifier is empty.",
    },
    "models.name_empty": {
        "ko": "모델 이름이 비어 있습니다.",
        "en": "The model name is empty.",
    },
    "models.ollama_missing": {
        "ko": "Ollama가 설치되어 있지 않습니다.",
        "en": "Ollama is not installed.",
    },
    "models.download_timeout": {
        "ko": "모델 내려받기 시간이 초과되었습니다.",
        "en": "The model download timed out.",
    },
    "models.pull_failed": {
        "ko": "모델 내려받기에 실패했습니다.",
        "en": "The model download failed.",
    },
    "models.download_not_automated": {
        "ko": "{provider} 엔진의 모델 내려받기는 아직 자동화되지 않았습니다.",
        "en": "Model downloads for the {provider} engine are not automated yet.",
    },
    "models.unknown_provider": {
        "ko": "알 수 없는 제공자입니다.",
        "en": "Unknown provider.",
    },
    "models.api_key_empty": {
        "ko": "API 키가 비어 있습니다.",
        "en": "The API key is empty.",
    },
    "models.other_user_api_key": {
        "ko": "다른 사용자의 API 키를 설정할 권한이 없습니다.",
        "en": "You are not allowed to set another user's API key.",
    },
    "models.sign_in_required": {
        "ko": "사용자 확인이 필요합니다. 로그인 후 다시 시도하세요.",
        "en": "Sign in first, then try again.",
    },
    # ── tools / files ───────────────────────────────────────────────────
    "tools.pdf_render_failed": {
        "ko": "PDF를 그리지 못했습니다: {reason}",
        "en": "The PDF could not be rendered: {reason}",
    },
    "tools.path_outside_workspace": {
        "ko": "경로가 작업 공간 밖입니다.",
        "en": "That path is outside the workspace.",
    },
    "tools.directory_not_found": {
        "ko": "디렉터리를 찾을 수 없습니다.",
        "en": "That directory was not found.",
    },
    # ── chronicle ───────────────────────────────────────────────────────
    "chronicle.bad_date": {
        "ko": "날짜를 읽을 수 없습니다. 2026-08-11 처럼 연-월-일로 적어 주세요.",
        "en": "That date could not be read. Write it as year-month-day, like 2026-08-11.",
    },
    "chronicle.bad_timestamp": {
        "ko": "시점을 읽을 수 없습니다. 2026-08-11T09:00:00 처럼 적어 주세요.",
        "en": "That moment could not be read. Write it like 2026-08-11T09:00:00.",
    },
    # ── index jobs ──────────────────────────────────────────────────────
    "index.limit_out_of_range": {
        "ko": "한 번에 처리할 개수는 {min}에서 {max} 사이여야 합니다.",
        "en": "Ask for between {min} and {max} items in one pass.",
    },
    # ── MCP / setup ─────────────────────────────────────────────────────
    "mcp.connector_not_found": {
        "ko": "커넥터를 찾을 수 없습니다.",
        "en": "That connector was not found.",
    },
    "mcp.name_required": {
        "ko": "이름은 필수입니다.",
        "en": "A name is required.",
    },
    "mcp.package_required": {
        "ko": "패키지는 필수입니다.",
        "en": "A package is required.",
    },
    "mcp.item_not_found": {
        "ko": "항목을 찾을 수 없습니다.",
        "en": "That item was not found.",
    },
    "mcp.unknown_id": {
        "ko": "알 수 없는 MCP입니다: {mcp_id}",
        "en": "Unknown MCP: {mcp_id}",
    },
    "setup.unknown_permission": {
        "ko": "알 수 없는 권한 설정입니다.",
        "en": "Unknown permission setting.",
    },
    "models.public_mode_blocks_local": {
        "ko": (
            "공개 모드에서는 로컬 MLX 모델을 불러올 수 없습니다. "
            "openai:, openrouter:, groq:, together: 모델을 쓰거나 "
            "LATTICEAI_ALLOW_LOCAL_MODELS=true 로 설정하세요."
        ),
        "en": (
            "Public mode does not load local MLX models. Use an openai:, "
            "openrouter:, groq: or together: model, or set "
            "LATTICEAI_ALLOW_LOCAL_MODELS=true."
        ),
    },
    "models.public_model_missing": {
        "ko": (
            "공개 모델이 준비되지 않았습니다. OPENAI_API_KEY 와 "
            "LATTICEAI_PUBLIC_MODEL={model} 을 설정하거나, OpenAI 호환 모델로 "
            "/models/load 를 호출하세요."
        ),
        "en": (
            "No public model is loaded. Set OPENAI_API_KEY and "
            "LATTICEAI_PUBLIC_MODEL={model}, or call /models/load with an "
            "OpenAI-compatible model."
        ),
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
