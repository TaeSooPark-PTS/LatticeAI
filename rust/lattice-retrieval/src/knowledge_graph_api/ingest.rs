//! Native `POST /knowledge-graph/ingest` (W3b).

#![allow(
    dead_code,
    unused_imports,
    unused_variables,
    unused_assignments,
    unused_mut,
    private_interfaces,
    clippy::result_large_err,
    clippy::needless_lifetimes,
    clippy::too_many_arguments,
    clippy::type_complexity,
    clippy::collapsible_if,
    clippy::needless_as_bytes,
    clippy::redundant_closure,
    clippy::needless_return,
    clippy::manual_clamp,
    clippy::ptr_arg,
    clippy::unnecessary_sort_by,
    clippy::result_unit_err,
    clippy::useless_vec,
    clippy::uninlined_format_args,
    clippy::manual_contains,
    clippy::needless_borrows_for_generic_args,
    clippy::implicit_clone,
    clippy::unnecessary_map_or,
    clippy::match_like_matches_macro,
    clippy::manual_range_contains,
    clippy::derivable_impls,
    clippy::needless_pass_by_ref_mut,
    clippy::redundant_guards,
    clippy::map_identity,
    clippy::iter_overeager_cloned,
    clippy::explicit_auto_deref,
    clippy::bool_comparison,
    clippy::nonminimal_bool,
    clippy::if_same_then_else,
    clippy::question_mark,
    clippy::single_char_pattern,
    clippy::manual_pattern_char_comparison,
    clippy::manual_is_ascii_check,
    clippy::repeat_once,
    clippy::unused_self,
    clippy::module_inception
)]
use std::sync::Arc;

use axum::body::Bytes;
use axum::extract::{Path as AxumPath, RawQuery, State};
use axum::http::HeaderMap;
use axum::response::Response;
use axum::routing::{get, post};
use axum::Router;
use lattice_auth::OrderedMap;
use lattice_core::worker::{WorkerSeamClient, WorkerSeamError};
use lattice_core::CoreError;

use crate::memory_api::graph_native;
use rusqlite::Connection;
use serde_json::{json, Value};

use crate::search_api::{
    engine_error, graph_disabled, http_error, language, ok, optional, Kind, Model, Query,
    RetrievalApiState,
};

// ── native ingest (W3b) ─────────────────────────────────────────────────────

const INGEST_REQUEST: &[crate::search_api::FieldSpec] = &[
    crate::search_api::required("type", Kind::Str(1)),
    crate::search_api::optional("content", Kind::Str(0)),
    crate::search_api::optional("role", Kind::Str(0)),
    crate::search_api::optional("title", Kind::Str(0)),
    crate::search_api::optional("source", Kind::Str(0)),
    crate::search_api::optional("conversation_id", Kind::Str(0)),
    crate::search_api::optional("user_email", Kind::Str(0)),
    crate::search_api::optional("user_nickname", Kind::Str(0)),
    crate::search_api::optional("metadata", Kind::Object),
];

const EXTRACT_PATH: &str = "/worker/extract";
const EMBED_PATH: &str = "/worker/embed";

async fn extract_via_seam(
    seam: Option<&WorkerSeamClient>,
    text: &str,
    kind: &str,
) -> lattice_core::graph_write::types::ExtractReply {
    use lattice_core::graph_write::types::ExtractReply;
    if text.trim().is_empty() {
        return ExtractReply::default();
    }
    let Some(seam) = seam else {
        return ExtractReply::default();
    };
    match seam
        .post_json(EXTRACT_PATH, &json!({"text": text, "kind": kind}))
        .await
    {
        Ok(payload) => ExtractReply::from_json(&payload),
        Err(_) => ExtractReply::default(),
    }
}

async fn embed_via_seam(
    seam: Option<&WorkerSeamClient>,
    text: &str,
) -> Option<lattice_core::graph_write::types::SuppliedVector> {
    if text.trim().is_empty() {
        return None;
    }
    let seam = seam?;
    let payload = seam
        .post_json(EMBED_PATH, &json!({"texts": [text], "kind": "passage"}))
        .await
        .ok()?;
    let values = payload
        .get("vectors")
        .and_then(Value::as_array)
        .and_then(|rows| rows.first())
        .and_then(Value::as_array)
        .map(|row| row.iter().filter_map(Value::as_f64).collect::<Vec<f64>>())?;
    if values.is_empty() {
        return None;
    }
    Some(lattice_core::graph_write::types::SuppliedVector {
        model_id: payload
            .get("model_id")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string(),
        dim: payload
            .get("dim")
            .and_then(Value::as_u64)
            .unwrap_or(values.len() as u64) as usize,
        values,
    })
}

pub(crate) async fn ingest(
    State(state): State<Arc<RetrievalApiState>>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let identity = match state.auth().require_user(&headers) {
        Ok(identity) => identity,
        Err(refusal) => return refusal,
    };
    let model = match Model::parse(&body, INGEST_REQUEST) {
        Ok(model) => model,
        Err(refusal) => return refusal,
    };
    let lang = language(&headers);
    let claimed = model.str("user_email");
    if !identity.email.is_empty()
        && !claimed.is_empty()
        && !identity.email.eq_ignore_ascii_case(claimed)
    {
        return crate::search_api::http_error(403, "common.user_mismatch", lang);
    }
    let event_type = model.str("type").trim().to_ascii_lowercase();
    if !matches!(event_type.as_str(), "message" | "ai_response" | "note") {
        return crate::search_api::http_error(400, "graph.unsupported_type", lang);
    }
    let Some(graph) = state.graph().cloned() else {
        return crate::search_api::http_error(503, "capture.ingestion_disabled", lang);
    };
    let effective_user = if identity.email.is_empty() {
        if claimed.is_empty() {
            None
        } else {
            Some(claimed.to_string())
        }
    } else {
        Some(identity.email.clone())
    };
    let workspace = state
        .scope_for(&identity)
        .allowed_workspaces
        .as_ref()
        .and_then(|set| set.iter().next().cloned());
    let role = {
        let given = model.str("role");
        if given.is_empty() {
            if event_type == "ai_response" {
                "assistant".into()
            } else {
                "user".into()
            }
        } else {
            given.to_string()
        }
    };
    let title = model.str("title").to_string();
    let content = model.str("content").to_string();
    let source = {
        let given = model.str("source");
        if given.is_empty() {
            "mcp".into()
        } else {
            given.to_string()
        }
    };
    let conversation_id = {
        let given = model.str("conversation_id");
        if given.is_empty() {
            None
        } else {
            Some(given.to_string())
        }
    };
    let nickname = {
        let given = model.str("user_nickname");
        if given.is_empty() {
            None
        } else {
            Some(given.to_string())
        }
    };
    let metadata = model
        .get("metadata")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    let is_note = event_type == "note";
    let extract_text = if is_note {
        if title.is_empty() {
            content.clone()
        } else {
            format!("{title}\n{content}")
        }
    } else {
        content.clone()
    };
    let extract_kind = if is_note { "document" } else { "message" };
    let extracted = extract_via_seam(state.seam(), &extract_text, extract_kind).await;
    let embedding = embed_via_seam(state.seam(), &extract_text).await;
    let native_agrees = match (embedding.as_ref(), state.graph()) {
        (Some(vector), Some(graph)) => {
            vector.model_id == graph.embedder().model_id() && vector.dim == graph.embedder().dim()
        }
        (None, _) => true,
        (Some(_), None) => true,
    };
    let outcome = match tokio::task::spawn_blocking(move || {
        if is_note {
            let mut request = lattice_core::graph_write::types::IngestContentRequest {
                source_type: "note".into(),
                title,
                text: content,
                source_uri: Some(source),
                owner: effective_user,
                workspace_id: workspace,
                conversation_id,
                metadata,
                concepts: extracted.concepts.clone(),
                triples: extracted.triples.clone(),
                semantic: extracted.semantic.clone(),
                embedding: embedding.clone(),
                ..Default::default()
            };
            request.node_type = Some("Document".into());
            graph.ingest_content(&request)
        } else {
            let mut raw = metadata.clone();
            raw.insert("type".into(), json!(event_type));
            raw.insert("title".into(), json!(title));
            raw.insert("content".into(), json!(content));
            let request = lattice_core::graph_write::types::IngestMessageRequest {
                role,
                content,
                user_email: effective_user,
                user_nickname: nickname,
                source: Some(source),
                conversation_id,
                workspace_id: workspace,
                raw: Some(raw),
                concepts: extracted.concepts,
                triples: extracted.triples,
                semantic: extracted.semantic,
                embedding,
                ..Default::default()
            };
            graph.ingest_message(&request)
        }
    })
    .await
    {
        Ok(Ok(outcome)) => outcome,
        Ok(Err(error)) => return crate::search_api::detail(500, &error.to_string()),
        Err(error) => return crate::search_api::detail(500, &error.to_string()),
    };
    if native_agrees {
        if let Some(graph) = state.graph().cloned() {
            let node_id = outcome
                .to_json()
                .get("node_id")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_string();
            if !node_id.is_empty() {
                let _ = tokio::task::spawn_blocking(move || graph.write_vectors(&node_id)).await;
            }
        }
    }
    ok(&outcome.to_json())
}
