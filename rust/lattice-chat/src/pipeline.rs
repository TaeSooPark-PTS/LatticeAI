//! The `POST /chat` pipeline: auth → intents → context → worker tokens → persist.
//!
//! Port of `latticeai/api/chat.py`'s handler. Token generation never moves;
//! this module asks the worker for them over `/worker/llm/stream` and reframes
//! the bytes. History writes are native as of Wave 2.5 §W3a — every
//! `persist_entry` here runs [`crate::turn::write_chat_turn`], which redacts
//! before anything else sees the text, so the stored copy is the redacted one.

use std::time::{SystemTime, UNIX_EPOCH};

use axum::body::Bytes;
use axum::extract::{RawQuery, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::Response;
use lattice_auth::requested_workspace;
use lattice_core::messages::{self, LANGUAGE_HEADER};
use lattice_retrieval::history::{history as read_history, HistoryScope};
use serde_json::json;

use crate::contracts::parse_chat_request;
use crate::helpers::{
    build_recent_chat_context, is_clear_command, is_current_url_request, is_file_action_request,
    is_network_status_request,
};
use crate::history::{error_body, history_scope, json_body};
use crate::intents::{self, current_url, direct_file_action, no_model_response, HistoryMeta};
use crate::state::ChatState;

mod answers;
mod model;

use model::run_model_turn;

/// `POST /chat`.
pub async fn chat(
    State(state): State<ChatState>,
    headers: HeaderMap,
    RawQuery(query): RawQuery,
    body: Bytes,
) -> Response {
    let req = match parse_chat_request(&body) {
        Ok(req) => req,
        Err(refusal) => return refusal,
    };
    let identity = match state.auth.require_user(&headers) {
        Ok(identity) => identity,
        Err(refusal) => return refusal,
    };
    if let Err(refusal) = state.auth.enforce_rate_limit(&identity.email, "chat") {
        return refusal;
    }
    let lang = language_of(&headers);
    let effective_email =
        match authenticated_identity(&identity.email, req.user_email.as_deref(), lang) {
            Ok(email) => email,
            Err(status) => return error_body(status, "common.user_mismatch", &headers, &[]),
        };
    let workspace_id = match write_workspace(&state, &headers, query.as_deref(), &identity.email) {
        Ok(workspace) => workspace,
        Err(refusal) => return refusal,
    };
    let (hist_email, hist_nick) =
        state.history_user(effective_email.as_deref(), req.user_nickname.as_deref());
    let source = req.source.clone();
    let source_ref = source.as_deref().or(Some("web"));
    let conversation = req.conversation_id.clone();
    let workspace = workspace_id.clone();
    let meta = HistoryMeta {
        email: hist_email.as_deref(),
        nickname: hist_nick.as_deref(),
        source: source_ref,
        conversation_id: conversation.as_deref(),
        workspace_id: workspace.as_deref(),
    };

    if is_network_status_request(&req.message) {
        return intents::network(&state, &req, &headers, &meta).await;
    }
    if is_clear_command(&req.message) {
        return intents::clear(
            &state,
            &req,
            &headers,
            effective_email.as_deref(),
            workspace.as_deref(),
        )
        .await;
    }
    if is_current_url_request(&req.message) && req.client_url.is_some() {
        return current_url(&state, &req, &meta).await;
    }

    let selected = match request_model(&state, req.model.as_deref(), lang).await {
        Ok(model) => model,
        Err(model) => {
            return error_body(404, "chat.model_not_loaded", &headers, &[("model", &model)]);
        }
    };
    let file_intent = is_file_action_request(&req.message);
    if file_intent {
        state.funnel_increment("file_requests");
        if let Some(response) = direct_file_action(
            &state,
            &req,
            &headers,
            selected.as_deref(),
            effective_email.as_deref(),
            workspace.as_deref(),
        )
        .await
        {
            return response;
        }
    }
    let Some(model_id) = selected else {
        return no_model_response(&state, &headers);
    };
    if file_intent {
        // The agent-loop fallback is lattice-agent's. Unreachable without a
        // model, and the with-model path has no fixtures.
        return error_body(400, "chat.file_generation_failed", &headers, &[]);
    }

    run_model_turn(
        state,
        req,
        headers,
        model_id,
        effective_email,
        workspace,
        hist_email,
        hist_nick,
    )
    .await
}

pub(crate) fn recent_context(
    state: &ChatState,
    user_email: Option<&str>,
    conversation_id: Option<&str>,
    workspace_id: Option<&str>,
    limit: usize,
    include_image_missing: bool,
) -> String {
    let Some(conn) = state.read_conn() else {
        return String::new();
    };
    let scope = if let Some(email) = user_email.filter(|email| !email.is_empty()) {
        HistoryScope {
            user_email: Some(email.to_string()),
            allowed_workspaces: workspace_id
                .filter(|id| !id.is_empty())
                .map(|id| vec![id.to_string()]),
            include_legacy_global: false,
        }
    } else {
        history_scope(state, "")
    };
    let rows = read_history(&conn, None, None, &scope).unwrap_or_default();
    build_recent_chat_context(
        &rows,
        limit,
        include_image_missing,
        user_email,
        conversation_id,
        workspace_id,
    )
}

fn authenticated_identity(
    current_user: &str,
    claimed: Option<&str>,
    _lang: &str,
) -> Result<Option<String>, u16> {
    if !current_user.is_empty() {
        if let Some(claimed) = claimed.filter(|value| !value.is_empty()) {
            if current_user.trim().to_lowercase() != claimed.trim().to_lowercase() {
                return Err(403);
            }
        }
    }
    let email = if current_user.is_empty() {
        claimed
            .filter(|value| !value.is_empty())
            .map(str::to_string)
    } else {
        Some(current_user.to_string())
    };
    Ok(email)
}

fn write_workspace(
    state: &ChatState,
    headers: &HeaderMap,
    query: Option<&str>,
    user: &str,
) -> Result<Option<String>, Response> {
    let requested = requested_workspace(headers, query, None)?;
    match state.workspace.as_ref() {
        Some(resolver) => resolver
            .resolve_write_scope(requested.as_deref(), Some(user).filter(|u| !u.is_empty()))
            .map_err(|error| json_body(StatusCode::FORBIDDEN, &json!({"detail": error}))),
        None => Ok(requested),
    }
}

async fn request_model(
    state: &ChatState,
    requested: Option<&str>,
    _lang: &str,
) -> Result<Option<String>, String> {
    let snapshot = match state.worker.as_ref() {
        Some(worker) => worker.models().await.unwrap_or_default(),
        None => crate::worker::ModelSnapshot::default(),
    };
    if let Some(model) = requested.filter(|id| !id.is_empty()) {
        if !snapshot.is_loaded(model) {
            return Err(model.to_string());
        }
        return Ok(Some(model.to_string()));
    }
    Ok(snapshot.current)
}

fn language_of(headers: &HeaderMap) -> &'static str {
    messages::resolve_language(
        headers
            .get(LANGUAGE_HEADER)
            .and_then(|value| value.to_str().ok()),
        headers
            .get(axum::http::header::ACCEPT_LANGUAGE)
            .and_then(|value| value.to_str().ok()),
    )
}

pub(crate) fn file_path_count(message: &str) -> usize {
    // Port of the auto-read detector: `(?:^|[\s\'"(])((~|/[\w.])[^\s\'")\]]*)`.
    let mut count = 0usize;
    let bytes = message.as_bytes();
    let mut index = 0usize;
    while index < bytes.len() {
        let start = bytes[index] == b'~'
            || (bytes[index] == b'/'
                && index + 1 < bytes.len()
                && (bytes[index + 1].is_ascii_alphanumeric()
                    || bytes[index + 1] == b'.'
                    || bytes[index + 1] == b'_'));
        let boundary = index == 0
            || bytes[index - 1].is_ascii_whitespace()
            || matches!(bytes[index - 1], b'\'' | b'"' | b'(');
        if start && boundary {
            count += 1;
            while index < bytes.len()
                && !bytes[index].is_ascii_whitespace()
                && !matches!(bytes[index], b'\'' | b'"' | b')' | b']')
            {
                index += 1;
            }
            continue;
        }
        index += 1;
    }
    count
}

pub(crate) fn now_secs() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs_f64())
        .unwrap_or(0.0)
}

pub(crate) fn utc_now() -> String {
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs())
        .unwrap_or(0);
    let days = secs / 86_400;
    let tod = secs % 86_400;
    let (year, month, day) = civil_from_days(days as i64);
    let hour = tod / 3600;
    let minute = (tod % 3600) / 60;
    let second = tod % 60;
    format!("{year:04}-{month:02}-{day:02}T{hour:02}:{minute:02}:{second:02}")
}

fn civil_from_days(days: i64) -> (i64, i64, i64) {
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = mp + if mp < 10 { 3 } else { -9 };
    let y = y + i64::from(m <= 2);
    (y, m, d)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn identity_mismatch_is_the_403_the_fixture_pins() {
        assert!(authenticated_identity("a@x", Some("b@x"), "ko").is_err());
        assert_eq!(
            authenticated_identity("a@x", Some("a@x"), "ko").unwrap(),
            Some("a@x".into())
        );
        assert_eq!(
            authenticated_identity("a@x", None, "ko").unwrap(),
            Some("a@x".into())
        );
    }

    #[test]
    fn utc_now_is_an_iso_timestamp() {
        let stamp = utc_now();
        assert!(stamp.contains('T'));
        assert_eq!(stamp.len(), 19);
    }
}
