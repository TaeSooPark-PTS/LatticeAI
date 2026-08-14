//! Server-side message catalog — port of `latticeai.core.messages`.
//!
//! One id per message, both `ko` and `en`, language chosen by the request
//! ([`resolve_language`]), never by the call site. Unknown ids return the id
//! itself so a missing string cannot turn a 404 into a 500.
//!
//! `lattice-core` does not depend on axum. [`http_error`] returns the FastAPI
//! envelope (`{"detail": "..."}`) plus a status so a handler can wrap it as
//! `(StatusCode::from_u16(err.status).unwrap(), Json(err.body))`.

use serde_json::{json, Value};

use crate::pytext::strip;

/// `latticeai.core.messages.DEFAULT_LANGUAGE`.
pub const DEFAULT_LANGUAGE: &str = "ko";
/// `latticeai.core.messages.SUPPORTED_LANGUAGES` — same order as Python.
pub const SUPPORTED_LANGUAGES: &[&str] = &["ko", "en"];
/// `latticeai.core.messages.LANGUAGE_HEADER`. Preferred over `Accept-Language`.
pub const LANGUAGE_HEADER: &str = "x-lattice-language";

/// `(id, ko, en)` — sorted by `id`. Keep sorted when adding a message.
#[rustfmt::skip]
const MESSAGES: &[(&str, &str, &str)] = &[
    ("admin.cannot_delete_self", "자기 자신은 삭제할 수 없습니다.", "You cannot delete your own account."),
    ("admin.cannot_disable_self", "자기 자신은 비활성화할 수 없습니다.", "You cannot disable your own account."),
    ("admin.invalid_role", "role은 admin 또는 user만 가능합니다.", "Role must be either 'admin' or 'user'."),
    ("agent_seam.disabled", "워커 시임이 꺼져 있습니다. 호스트가 직접 띄운 워커에서만 열립니다.", "The worker seam is off. It opens only in a worker the host started itself."),
    ("agent_seam.max_tokens_out_of_range", "한 번에 만들 길이는 {min}에서 {max} 사이여야 합니다.", "Ask for between {min} and {max} tokens in one completion."),
    ("agent_seam.message_required", "모델에 보낼 내용을 적어주세요.", "Write the message to send to the model."),
    ("agent_seam.proposals_unavailable", "변경 제안 서비스가 연결되어 있지 않습니다.", "The change proposal service is not connected."),
    ("agent_seam.temperature_out_of_range", "온도는 {min}에서 {max} 사이여야 합니다.", "Temperature has to be between {min} and {max}."),
    ("agent_seam.tool_blocked", "'{tool}' 도구는 어떤 모드에서도 차단됩니다 ({reason}).", "'{tool}' is blocked in every mode ({reason})."),
    ("agent_seam.tool_fail_closed", "'{tool}' 도구는 기존 내용을 바꾸지만 검토할 수 있는 제안으로 만들 수 없어 차단했습니다 ({reason}).", "'{tool}' would change existing content but cannot be staged as a reviewable proposal, so it is blocked ({reason})."),
    ("agent_seam.tool_required", "실행할 도구 이름이 필요합니다.", "A tool name to run is required."),
    ("auth.account_disabled", "비활성화된 계정입니다.", "This account has been disabled."),
    ("auth.bad_credentials", "이메일 또는 비밀번호가 틀렸습니다.", "That email address or password is not correct."),
    ("auth.current_password_wrong", "현재 비밀번호가 틀렸습니다.", "Your current password is not correct."),
    ("auth.email_taken", "이미 존재하는 이메일입니다.", "That email address is already registered."),
    ("auth.invitation_required", "유효한 서명 초대 권한이 필요합니다.", "A valid signed invitation is required."),
    ("auth.login_required", "인증이 필요합니다.", "You need to sign in first."),
    ("auth.name_required", "이름을 입력해주세요.", "Please enter a name."),
    ("auth.nickname_required", "닉네임을 입력해주세요.", "Please enter a nickname."),
    ("auth.password_too_weak", "비밀번호는 8자 이상이며 영문자와 숫자를 모두 포함해야 합니다.", "Your password needs at least 8 characters, including letters and numbers."),
    ("auth.registration_disabled", "회원가입이 비활성화되어 있습니다. 관리자에게 문의하세요.", "Sign-up is turned off. Ask an administrator to enable it."),
    ("auth.user_not_found", "사용자를 찾을 수 없습니다.", "No such user."),
    ("boundary.policy_not_configured", "혼합 검색 정책 서비스가 설정되지 않았습니다.", "The hybrid policy service is not configured."),
    ("capture.ingestion_disabled", "지식 그래프 수집이 꺼져 있습니다.", "Knowledge Graph ingestion is turned off."),
    ("capture.nothing_to_capture", "저장할 내용이 없습니다. 텍스트나 페이지 내용을 함께 보내주세요.", "Nothing to capture — send text, html, or a selection."),
    ("capture.payload_too_large", "보낸 내용이 너무 큽니다.", "That capture is too large to accept."),
    ("chat.conversation_not_found", "대화를 찾을 수 없습니다.", "That conversation no longer exists."),
    ("chat.file_generation_failed", "파일 내용을 만들지 못했습니다.", "The file content could not be generated."),
    ("chat.file_name_collision", "'{name}' 이름의 파일이 너무 많습니다. 다른 이름을 지정해 주세요.", "Too many files are already named '{name}'. Please choose another name."),
    ("chat.model_not_loaded", "'{model}' 모델이 아직 준비되지 않았습니다.", "Model '{model}' is not loaded."),
    ("chat.no_model_loaded", "불러온 모델이 없습니다. 모델을 먼저 준비해주세요.", "No model is loaded. Prepare a model first."),
    ("chronicle.bad_date", "날짜를 읽을 수 없습니다. 2026-08-11 처럼 연-월-일로 적어 주세요.", "That date could not be read. Write it as year-month-day, like 2026-08-11."),
    ("chronicle.bad_timestamp", "시점을 읽을 수 없습니다. 2026-08-11T09:00:00 처럼 적어 주세요.", "That moment could not be read. Write it like 2026-08-11T09:00:00."),
    ("common.file_not_found", "파일을 찾을 수 없습니다.", "File not found."),
    ("common.graph_disabled", "지식 그래프가 꺼져 있습니다.", "The Knowledge Graph is turned off."),
    ("common.graph_unavailable", "지식 그래프를 사용할 수 없습니다.", "The Knowledge Graph is not available."),
    ("common.path_required", "경로를 입력해주세요.", "A path is required."),
    ("common.user_mismatch", "로그인한 사용자와 요청한 사용자가 다릅니다.", "user_email must match the authenticated user."),
    ("common.workspace_mismatch", "작업 공간 지정이 서로 다릅니다.", "Workspace selectors must match."),
    ("common.workspace_unreadable", "'{workspace}' 작업 공간을 읽을 권한이 없습니다.", "Workspace '{workspace}' is not readable."),
    ("features.allow_multimodal.label", "사진·녹음도 기억하기", "Remember pictures and recordings"),
    ("features.allow_multimodal.summary", "폴더를 읽을 때 글뿐 아니라 사진과 녹음도 함께 저장합니다.", "A folder scan stores pictures and recordings too, not just text."),
    ("features.auto_late_fusion.label", "글로 사진 찾기", "Find pictures by typing"),
    ("features.auto_late_fusion.summary", "글로 물어봐도 사진까지 함께 찾습니다. 사진을 읽는 모델이 있어야 켜집니다.", "A typed question also searches pictures — needs a vision model that shares the same space."),
    ("features.auto_vector_index.label", "넣자마자 검색 준비", "Make new material searchable at once"),
    ("features.auto_vector_index.summary", "새 자료를 넣으면 바로 의미 검색까지 준비합니다. 끄면 나중에 한 번에 만듭니다.", "New material is prepared for meaning-based search immediately; off means you rebuild later."),
    ("features.brain_network.caution", "이 기능만 기억을 이 컴퓨터 밖으로 내보냅니다. 받은 내용은 바로 합쳐지지 않고 검토함으로 갑니다.", "This is the one switch that sends memory off this computer. Anything received waits in the review inbox instead of merging."),
    ("features.brain_network.label", "골라서 나누기", "Share selected knowledge"),
    ("features.brain_network.summary", "내가 고른 기억 묶음만 다른 기기로 내보내고 받아올 수 있습니다.", "Lets you export a hand-picked slice of memory to another device, and receive one."),
    ("features.choice.install_required", "설치 필요 — {reason}", "Install required — {reason}"),
    ("features.fusion_rrf.label", "검색 결과 합치는 방식 바꾸기", "Blend search results by rank"),
    ("features.fusion_rrf.summary", "점수 대신 순위로 합칩니다. 검색 채널마다 점수 크기가 달라도 흔들리지 않습니다.", "Combines channels by position instead of score, so mismatched score scales stop skewing results."),
    ("features.graph_expansion.label", "옆에 있는 기억까지 보기", "Look at neighbouring memories"),
    ("features.graph_expansion.summary", "찾은 기억과 바로 이어진 기억도 후보로 넣습니다. 답은 넓어지고 조금 흐려집니다.", "Adds memories one link away from a hit as candidates — wider answers, slightly less focused."),
    ("features.invalid_value", "이 기능에 쓸 수 없는 값입니다: {value}", "That is not a value this feature can take: {value}"),
    ("features.note", "모두 지금 바로 적용됩니다. 다시 시작하지 않아도 됩니다.", "Every switch here takes effect right away — no restart needed."),
    ("features.synthesis.label", "스스로 정리하기", "Tidy up on its own"),
    ("features.synthesis.summary", "자료가 쌓이면 알아서 훑어보고, 고칠 거리를 검토함에 제안합니다.", "As material piles up, the Brain reviews it and proposes tidy-ups in the review inbox."),
    ("features.unknown", "그런 기능은 없습니다: {feature}", "There is no such feature: {feature}"),
    ("features.vault_watch.label", "노트 보관함 지켜보기", "Watch my notes vault"),
    ("features.vault_watch.summary", "밖에 있는 노트 보관함이 바뀌면 알아서 다시 읽어옵니다.", "When an outside notes vault changes, it is re-read on its own."),
    ("features.vector_backend.choice.brute", "전부 비교 (정확)", "Compare everything (exact)"),
    ("features.vector_backend.choice.hnsw", "근사 검색 (가장 빠름)", "Approximate search (fastest)"),
    ("features.vector_backend.choice.quantized", "간추려 비교 (빠름)", "Compare compressed (faster)"),
    ("features.vector_backend.label", "의미 검색 방식", "Meaning-search engine"),
    ("features.vector_backend.summary", "빠르기와 정확함 사이에서 고릅니다. 기본값은 전부 훑어보는 정확한 방식입니다.", "Trade speed against exactness. The default compares everything and is exact."),
    ("features.video_ingest.label", "영상도 함께", "Include videos"),
    ("features.video_ingest.summary", "사진·녹음을 켠 상태에서, 영상은 장면과 자막으로 저장합니다.", "With the switch above on, videos are stored as keyframes and subtitles."),
    ("graph.node_id_required", "노드 식별자가 필요합니다.", "A node id is required."),
    ("graph.node_not_found", "노드를 찾을 수 없습니다.", "That node was not found."),
    ("graph.unsupported_type", "지원하는 종류: message, ai_response, note", "Supported types are: message, ai_response, note."),
    ("index.limit_out_of_range", "한 번에 처리할 개수는 {min}에서 {max} 사이여야 합니다.", "Ask for between {min} and {max} items in one pass."),
    ("ingestion.interop_path_required", "불러올 파일이나 폴더 경로가 필요합니다.", "A file or folder path to read is required."),
    ("ingestion.interop_unknown_source", "알 수 없는 연동 종류입니다: {source}", "Unknown interop source: {source}"),
    ("ingestion.job_not_found", "수집 작업을 찾을 수 없습니다.", "That ingestion job was not found."),
    ("ingestion.vault_path_required", "Obsidian 보관함 폴더 경로가 필요합니다.", "An Obsidian vault folder path is required."),
    ("ingestion.vault_watch_disabled", "보관함 자동 확인은 기본으로 꺼져 있습니다. 설정에서 켜야 사용할 수 있습니다.", "Vault watch is off by default; turn it on in settings to use it."),
    ("ingestion.vault_watch_unavailable", "보관함 자동 확인 기능을 사용할 수 없습니다.", "Vault watch is unavailable."),
    ("ingestion.watch_enable_failed", "폴더 감시를 켜지 못했습니다.", "Turning folder watch on did not succeed."),
    ("ingestion.watch_not_found", "감시 중인 폴더를 찾을 수 없습니다.", "That watched folder was not found."),
    ("ingestion.watch_selector_required", "감시 항목 또는 경로가 필요합니다.", "A watch id or a path is required."),
    ("ingestion.watch_unavailable", "폴더 감시 기능을 사용할 수 없습니다.", "The folder watch service is unavailable."),
    ("mcp.connector_not_found", "커넥터를 찾을 수 없습니다.", "That connector was not found."),
    ("mcp.item_not_found", "항목을 찾을 수 없습니다.", "That item was not found."),
    ("mcp.name_required", "이름은 필수입니다.", "A name is required."),
    ("mcp.package_required", "패키지는 필수입니다.", "A package is required."),
    ("mcp.unknown_id", "알 수 없는 MCP입니다: {mcp_id}", "Unknown MCP: {mcp_id}"),
    ("memory.unknown_source", "'{source}'는 알 수 없는 기억 출처입니다.", "Unknown memory source: {source}."),
    ("models.api_key_empty", "API 키가 비어 있습니다.", "The API key is empty."),
    ("models.download_consent_required", "모델 내려받기는 사용자가 직접 동의해야 시작됩니다.", "Model downloads require explicit consent (allow_download=true)."),
    ("models.download_not_automated", "{provider} 엔진의 모델 내려받기는 아직 자동화되지 않았습니다.", "Model downloads for the {provider} engine are not automated yet."),
    ("models.download_timeout", "모델 내려받기 시간이 초과되었습니다.", "The model download timed out."),
    ("models.identifier_empty", "모델 식별자가 비어 있습니다.", "The model identifier is empty."),
    ("models.name_empty", "모델 이름이 비어 있습니다.", "The model name is empty."),
    ("models.ollama_missing", "Ollama가 설치되어 있지 않습니다.", "Ollama is not installed."),
    ("models.other_user_api_key", "다른 사용자의 API 키를 설정할 권한이 없습니다.", "You are not allowed to set another user's API key."),
    ("models.other_user_credentials", "다른 사용자의 모델 자격 증명을 사용할 수 없습니다.", "You cannot use another user's model credentials."),
    ("models.public_mode_blocks_local", "공개 모드에서는 로컬 MLX 모델을 불러올 수 없습니다. openai:, openrouter:, groq:, together: 모델을 쓰거나 LATTICEAI_ALLOW_LOCAL_MODELS=true 로 설정하세요.", "Public mode does not load local MLX models. Use an openai:, openrouter:, groq: or together: model, or set LATTICEAI_ALLOW_LOCAL_MODELS=true."),
    ("models.public_model_missing", "공개 모델이 준비되지 않았습니다. OPENAI_API_KEY 와 LATTICEAI_PUBLIC_MODEL={model} 을 설정하거나, OpenAI 호환 모델로 /models/load 를 호출하세요.", "No public model is loaded. Set OPENAI_API_KEY and LATTICEAI_PUBLIC_MODEL={model}, or call /models/load with an OpenAI-compatible model."),
    ("models.pull_failed", "모델 내려받기에 실패했습니다.", "The model download failed."),
    ("models.sign_in_required", "사용자 확인이 필요합니다. 로그인 후 다시 시도하세요.", "Sign in first, then try again."),
    ("models.unknown_provider", "알 수 없는 제공자입니다.", "Unknown provider."),
    ("portability.brain_network_disabled", "다른 Brain과의 공유 기능이 꺼져 있습니다. 기본값이 꺼짐이며, LATTICEAI_BRAIN_NETWORK=1 로 직접 켜야 사용할 수 있습니다.", "Sharing with another Brain is off. It is off by default; set LATTICEAI_BRAIN_NETWORK=1 to turn it on."),
    ("portability.review_queue_unavailable", "검토함이 연결되어 있지 않아 받은 지식을 제안으로 쌓을 수 없습니다.", "The review queue is not connected, so received knowledge cannot be queued as proposals."),
    ("portability.verification_failed", "보관 파일 검증에 실패했습니다.", "Archive verification failed."),
    ("project.not_found", "프로젝트를 찾을 수 없습니다.", "That project was not found."),
    ("review.bulk_ids_required", "한꺼번에 처리할 검토 항목을 하나 이상 골라 주세요.", "Choose at least one review item to act on."),
    ("review.bulk_too_many", "한 번에 최대 {cap}개까지 처리할 수 있습니다.", "At most {cap} items can be handled in one request."),
    ("review.cannot_approve_in_status", "'{status}' 상태의 검토 항목은 승인할 수 없습니다.", "A review item in status '{status}' cannot be approved."),
    ("review.item_not_found", "검토 항목을 찾을 수 없습니다.", "That review item was not found."),
    ("self_model.empty_proposal", "그 제안에는 저장할 내용이 없습니다.", "That proposal carries nothing to save."),
    ("self_model.graph_unavailable", "지식 그래프가 꺼져 있어 내 프로필을 쓸 수 없습니다.", "The knowledge graph is off, so the profile cannot be written."),
    ("self_model.invalid", "내 프로필을 바꾸지 못했습니다.", "The profile could not be changed."),
    ("self_model.invalid_kind", "나 / 선호 / 습관 / 결정 / 관계 중 하나를 골라주세요.", "Choose one of: trait, preference, habit, decision, relationship."),
    ("self_model.not_a_proposal", "그 검토 항목은 내 프로필 제안이 아닙니다.", "That review item is not a profile proposal."),
    ("self_model.not_found", "그 항목은 이미 없습니다.", "That entry is already gone."),
    ("self_model.not_self_model", "내 프로필 항목이 아니라서 지울 수 없습니다.", "That is not a profile entry, so it cannot be removed here."),
    ("self_model.queue_unavailable", "검토함이 연결되어 있지 않아 제안을 처리할 수 없습니다.", "The review queue is not connected, so proposals cannot be handled."),
    ("self_model.text_required", "저장할 내용을 적어주세요.", "Please write what should be remembered."),
    ("setup.unknown_permission", "알 수 없는 권한 설정입니다.", "Unknown permission setting."),
    ("sso.config_error", "SSO 설정 오류입니다.", "Single sign-on is misconfigured."),
    ("sso.email_unavailable", "이메일을 확인할 수 없습니다.", "The identity provider did not share an email address."),
    ("sso.invalid_state", "유효하지 않은 SSO 상태입니다.", "That sign-on request is no longer valid. Please start again."),
    ("sso.invitation_required", "신규 SSO 계정에는 유효한 서명 초대 권한이 필요합니다.", "New single sign-on accounts need a valid signed invitation."),
    ("sso.no_id_token", "ID 토큰을 받지 못했습니다.", "The identity provider did not return an ID token."),
    ("sso.not_configured", "SSO가 설정되지 않았습니다.", "Single sign-on is not set up."),
    ("sso.provider_verification_failed", "SSO 공급자 검증에 실패했습니다.", "The sign-on provider could not be verified."),
    ("sso.token_verification_failed", "SSO 토큰 검증에 실패했습니다.", "The sign-on token could not be verified."),
    ("tools.directory_not_found", "디렉터리를 찾을 수 없습니다.", "That directory was not found."),
    ("tools.path_outside_workspace", "경로가 작업 공간 밖입니다.", "That path is outside the workspace."),
    ("tools.pdf_render_failed", "PDF를 그리지 못했습니다: {reason}", "The PDF could not be rendered: {reason}"),
    ("worker_compute.audio_too_large", "오디오가 {size} bytes 입니다. 한도는 {limit} bytes 입니다.", "The audio is {size} bytes; the limit is {limit} bytes."),
    ("worker_compute.audio_unsupported", "'{suffix}' 은(는) 지원하는 오디오 형식이 아닙니다. {allowed} 만 가능합니다.", "'{suffix}' is not a supported audio container. Supported: {allowed}."),
    ("worker_compute.content_invalid", "본문을 base64로 읽지 못했습니다: {reason}", "The payload is not valid base64: {reason}"),
    ("worker_compute.embedder_unavailable", "임베딩 공급자가 연결되어 있지 않습니다.", "No embedding provider is connected to this worker."),
    ("worker_compute.extract_kind_invalid", "'{kind}' 은(는) 추출 종류가 아닙니다. {allowed} 중 하나여야 합니다.", "'{kind}' is not an extraction kind. Use one of {allowed}."),
    ("worker_compute.kind_invalid", "'{kind}' 은(는) 임베딩 종류가 아닙니다. {allowed} 중 하나여야 합니다.", "'{kind}' is not an embedding kind. Use one of {allowed}."),
    ("worker_compute.parse_failed", "문서를 읽지 못했습니다: {reason}", "The document could not be parsed: {reason}"),
    ("worker_compute.render_failed", "'{kind}' 문서 생성이 실패했습니다: {reason}", "Rendering the '{kind}' document failed: {reason}"),
    ("worker_compute.render_unavailable", "이 워커는 '{kind}' 문서를 만들 수 없습니다: {reason}", "This worker cannot render '{kind}' documents: {reason}"),
    ("worker_seam.arg_not_allowed", "'{op}' 은(는) '{arg}' 값을 받지 않습니다. {allowed} 만 보낼 수 있습니다.", "'{op}' does not take '{arg}'. Only {allowed} may be sent."),
    ("worker_seam.graph_mutation_failed", "'{op}' 그래프 쓰기가 실패했습니다: {reason}", "The '{op}' graph write failed: {reason}"),
    ("worker_seam.graph_unavailable", "지식 그래프가 꺼져 있어 쓰기를 위임할 수 없습니다.", "The knowledge graph is off, so there is nothing to delegate the write to."),
    ("worker_seam.history_unavailable", "대화 기록 저장소가 연결되어 있지 않습니다.", "The conversation history store is not connected."),
    ("worker_seam.op_not_allowed", "'{op}' 은(는) 위임할 수 있는 그래프 쓰기가 아닙니다. {allowed} 중 하나여야 합니다.", "'{op}' is not a graph write this seam delegates. Use one of {allowed}."),
    ("worker_seam.role_invalid", "'{role}' 은(는) 기록할 수 있는 역할이 아닙니다. {allowed} 중 하나여야 합니다.", "'{role}' is not a role a turn can be recorded under. Use one of {allowed}."),
];

/// FastAPI/Starlette `HTTPException` JSON body plus the status the route chose.
///
/// FastAPI serializes `HTTPException(detail=<str>)` as `{"detail": <str>}`.
/// Response headers that Python's `http_error(..., headers=...)` can set are
/// not part of that envelope and are not carried here.
#[derive(Debug, Clone, PartialEq)]
pub struct HttpError {
    pub status: u16,
    pub body: Value,
}

impl HttpError {
    /// The localized `detail` string inside the envelope.
    pub fn detail(&self) -> &str {
        self.body
            .get("detail")
            .and_then(Value::as_str)
            .unwrap_or("")
    }

    /// Pair an axum handler wraps as `(StatusCode::from_u16(status), Json(body))`.
    pub fn into_response_parts(self) -> (u16, Value) {
        (self.status, self.body)
    }
}

/// Language for this request: the product's choice, then the browser's.
///
/// `x_lattice_language` is the value of [`LANGUAGE_HEADER`] (already extracted;
/// Starlette/axum lookup is case-insensitive). `accept_language` is the
/// `Accept-Language` header value. Never returns an unsupported language.
///
/// Accept-Language is walked in send order; `q=` values are stripped and
/// ignored, matching Python (the two agree on every real header).
pub fn resolve_language(
    x_lattice_language: Option<&str>,
    accept_language: Option<&str>,
) -> &'static str {
    if let Some(lang) = normalize(x_lattice_language) {
        return lang;
    }
    for part in accept_language.unwrap_or("").split(',') {
        let tag = part.split_once(';').map(|(head, _)| head).unwrap_or(part);
        if let Some(lang) = normalize(Some(tag)) {
            return lang;
        }
    }
    DEFAULT_LANGUAGE
}

/// [`resolve_language`] over `(name, value)` pairs.
///
/// Names are compared ASCII-case-insensitively (Starlette `Headers`). The first
/// value of each of [`LANGUAGE_HEADER`] and `Accept-Language` wins.
pub fn resolve_language_from_headers<'a, I>(headers: I) -> &'static str
where
    I: IntoIterator<Item = (&'a str, &'a str)>,
{
    let mut explicit = None;
    let mut accept = None;
    for (name, value) in headers {
        if explicit.is_none() && name.eq_ignore_ascii_case(LANGUAGE_HEADER) {
            explicit = Some(value);
        } else if accept.is_none() && name.eq_ignore_ascii_case("accept-language") {
            accept = Some(value);
        }
    }
    resolve_language(explicit, accept)
}

/// Localized text for `id` — Python `translate`.
///
/// An unknown id returns the id itself. A language that is not `en` falls
/// back to [`DEFAULT_LANGUAGE`]. `{name}` placeholders are replaced by simple
/// string substitution, in `args` order, the same way Python walks `**params`.
pub fn text(id: &str, lang: &str, args: &[(&str, &str)]) -> String {
    let Some((_, ko, en)) = lookup(id) else {
        return id.to_owned();
    };
    // `entry.get(language) or entry.get(DEFAULT_LANGUAGE) or key`
    let primary = if lang == "en" {
        en
    } else if lang == "ko" {
        ko
    } else {
        ""
    };
    let mut out = if !primary.is_empty() {
        primary.to_owned()
    } else if !ko.is_empty() {
        ko.to_owned()
    } else {
        return id.to_owned();
    };
    for (name, value) in args {
        out = out.replace(&format!("{{{name}}}"), value);
    }
    out
}

/// FastAPI error envelope for this id, plus the status the route chose.
pub fn http_error(status: u16, id: &str, lang: &str, args: &[(&str, &str)]) -> HttpError {
    HttpError {
        status,
        body: json!({ "detail": text(id, lang, args) }),
    }
}

fn lookup(id: &str) -> Option<(&'static str, &'static str, &'static str)> {
    MESSAGES
        .binary_search_by_key(&id, |entry| entry.0)
        .ok()
        .map(|index| MESSAGES[index])
}

/// `"en-GB"` → `"en"` when supported, else `None`. Python `_normalize`.
fn normalize(value: Option<&str>) -> Option<&'static str> {
    let value = value?;
    if value.is_empty() {
        return None;
    }
    let tag = strip(value).to_lowercase().replace('_', "-");
    if tag.is_empty() {
        return None;
    }
    let base = tag
        .split_once('-')
        .map(|(head, _)| head)
        .unwrap_or(tag.as_str());
    match base {
        "ko" => Some("ko"),
        "en" => Some("en"),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn catalog_is_sorted_and_unique() {
        for pair in MESSAGES.windows(2) {
            assert!(
                pair[0].0 < pair[1].0,
                "catalog must stay sorted: {} before {}",
                pair[0].0,
                pair[1].0
            );
        }
    }

    #[test]
    fn unknown_key_returns_the_key() {
        assert_eq!(text("nope.not.a.key", "en", &[]), "nope.not.a.key");
        let err = http_error(404, "nope.not.a.key", "ko", &[]);
        assert_eq!(err.status, 404);
        assert_eq!(err.detail(), "nope.not.a.key");
        assert_eq!(err.body, json!({ "detail": "nope.not.a.key" }));
    }

    #[test]
    fn unsupported_lang_falls_back_to_default() {
        assert_eq!(
            text("auth.user_not_found", "fr", &[]),
            text("auth.user_not_found", DEFAULT_LANGUAGE, &[])
        );
        assert_eq!(
            text("auth.user_not_found", "EN", &[]),
            text("auth.user_not_found", "ko", &[])
        );
    }

    #[test]
    fn missing_arg_leaves_the_placeholder() {
        assert_eq!(
            text("chat.model_not_loaded", "en", &[]),
            "Model '{model}' is not loaded."
        );
    }

    #[test]
    fn extra_arg_is_ignored() {
        assert_eq!(
            text(
                "auth.user_not_found",
                "en",
                &[("model", "ignored"), ("unused", "x")]
            ),
            "No such user."
        );
    }

    #[test]
    fn header_map_is_case_insensitive() {
        assert_eq!(
            resolve_language_from_headers([
                ("Accept-Language", "ko-KR"),
                ("X-Lattice-Language", "en"),
            ]),
            "en"
        );
    }
}
