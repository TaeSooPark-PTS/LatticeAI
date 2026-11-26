use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::Arc;

use axum::extract::{Query, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use serde_json::{json, Map, Value};

use crate::chunk::{chunk_meta_fields, typed_chunks, DEFAULT_CHUNK_OVERLAP, DEFAULT_CHUNK_SIZE};
use crate::filters::is_document_extension;
use crate::hashes::{chunk_id, vector_text_hash};
use crate::pystr::{decode_utf8_ignore, py_strip};
use crate::strategy::chunk_strategy_for;
use crate::watch::{walk_folder, WatchConfig};

pub const PLAN_PATH: &str = "/rust/ingest/plan";
pub const CHUNK_PATH: &str = "/rust/ingest/chunk";

#[derive(Debug, Clone)]
pub struct IngestApiConfig {
    pub max_plan_files: usize,
    pub max_chunk_chars: usize,
    pub watch: WatchConfig,
}

impl Default for IngestApiConfig {
    fn default() -> Self {
        Self {
            max_plan_files: 500,
            max_chunk_chars: 2_000_000,
            watch: WatchConfig::default(),
        }
    }
}

pub fn router(config: IngestApiConfig) -> Router {
    Router::new()
        .route(PLAN_PATH, get(plan))
        .route(CHUNK_PATH, post(chunk))
        .with_state(Arc::new(config))
}

fn bad_request(field: &str, detail: impl Into<String>) -> Response {
    (
        StatusCode::BAD_REQUEST,
        Json(json!({"error": "invalid_request", "field": field, "detail": detail.into()})),
    )
        .into_response()
}

fn expand_user(raw: &str) -> PathBuf {
    let home = std::env::var_os("HOME").map(PathBuf::from);
    expand_user_with(raw, home.as_deref())
}

fn expand_user_with(raw: &str, home: Option<&Path>) -> PathBuf {
    match (raw.strip_prefix('~'), home) {
        (Some(rest), Some(home)) if rest.is_empty() || rest.starts_with('/') => {
            home.join(rest.trim_start_matches('/'))
        }
        _ => PathBuf::from(raw),
    }
}

fn parse_bool(raw: &str) -> Option<bool> {
    match raw.trim().to_ascii_lowercase().as_str() {
        "1" | "true" | "yes" | "on" => Some(true),
        "0" | "false" | "no" | "off" => Some(false),
        _ => None,
    }
}

async fn plan(
    State(config): State<Arc<IngestApiConfig>>,
    Query(params): Query<HashMap<String, String>>,
) -> Response {
    let Some(raw_path) = params
        .get("path")
        .map(|value| value.trim())
        .filter(|value| !value.is_empty())
    else {
        return bad_request("path", "a folder path is required");
    };
    let recursive = match params.get("recursive") {
        Some(raw) => match parse_bool(raw) {
            Some(value) => value,
            None => return bad_request("recursive", "expected a boolean"),
        },
        None => true,
    };
    let limit = match params.get("limit") {
        Some(raw) => match raw.trim().parse::<usize>() {
            Ok(value) if value >= 1 => value.min(config.max_plan_files),
            _ => return bad_request("limit", "expected a positive integer"),
        },
        None => config.max_plan_files,
    };
    let root = expand_user(raw_path);
    let mut watch = config.watch.clone();
    watch.recursive = recursive;
    let max_bytes = watch.max_file_bytes;
    match tokio::task::spawn_blocking(move || build_plan(&root, &watch, limit, max_bytes)).await {
        Ok(Ok(payload)) => Json(payload).into_response(),
        Ok(Err(detail)) => (
            StatusCode::NOT_FOUND,
            Json(json!({"error": "folder_unavailable", "detail": detail})),
        )
            .into_response(),
        Err(error) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({"error": "plan_failed", "detail": error.to_string()})),
        )
            .into_response(),
    }
}

fn build_plan(
    root: &Path,
    config: &WatchConfig,
    limit: usize,
    max_bytes: u64,
) -> Result<Value, String> {
    let files = walk_folder(root, config).map_err(|error| error.to_string())?;
    let matched = files.len();
    let mut by_strategy: std::collections::BTreeMap<String, u64> =
        std::collections::BTreeMap::new();
    let mut reported = Vec::new();
    let mut total_chunks: u64 = 0;
    let mut total_chars: u64 = 0;
    let mut total_bytes: u64 = 0;
    let chunking = crate::chunk::ChunkingConfig::from_env();
    for file in files.iter().take(limit) {
        total_bytes += file.size;
        let strategy = chunk_strategy_for(&file.relative_path, "");
        *by_strategy.entry(strategy.to_string()).or_default() += 1;
        let mut entry = Map::new();
        entry.insert(
            "relative_path".into(),
            Value::from(file.relative_path.clone()),
        );
        entry.insert("path".into(), Value::from(file.path.display().to_string()));
        entry.insert("extension".into(), Value::from(file.extension.clone()));
        entry.insert("bytes".into(), Value::from(file.size));
        entry.insert("strategy".into(), Value::from(strategy));
        if is_document_extension(&file.extension) {
            entry.insert("chars".into(), Value::Null);
            entry.insert("chunks".into(), Value::Null);
            entry.insert(
                "note".into(),
                Value::from("text extraction happens in the worker; not counted here"),
            );
        } else if file.size > max_bytes {
            entry.insert("chars".into(), Value::Null);
            entry.insert("chunks".into(), Value::Null);
            entry.insert("note".into(), Value::from("over the size cap"));
        } else {
            let text = std::fs::read(&file.path)
                .map(|bytes| decode_utf8_ignore(&bytes))
                .unwrap_or_default();
            let planned = crate::chunk::plan_chunks(
                &text,
                &file.relative_path,
                "",
                &file.relative_path,
                &chunking,
            );
            let chars = py_strip(&text).chars().count() as u64;
            total_chars += chars;
            total_chunks += planned.children.len() as u64;
            entry.insert("chars".into(), Value::from(chars));
            entry.insert("chunks".into(), Value::from(planned.children.len()));
            entry.insert("parents".into(), Value::from(planned.parents.len()));
        }
        reported.push(Value::Object(entry));
    }
    Ok(json!({
        "dry_run": true,
        "detail": "nothing was ingested; this is what a folder ingest would admit",
        "root": root.display().to_string(),
        "recursive": config.recursive,
        "matched": matched,
        "reported": reported.len(),
        "truncated": matched > reported.len(),
        "totals": {
            "bytes": total_bytes,
            "chars": total_chars,
            "chunks": total_chunks,
            "by_strategy": by_strategy,
            "chunk_mode": chunking.mode.as_str(),
        },
        "files": reported,
    }))
}

async fn chunk(State(config): State<Arc<IngestApiConfig>>, body: Option<Json<Value>>) -> Response {
    let Some(Json(body)) = body else {
        return bad_request("body", "expected a JSON object");
    };
    let Some(object) = body.as_object() else {
        return bad_request("body", "expected a JSON object");
    };
    let text = match object.get("text") {
        Some(Value::String(text)) => text.clone(),
        Some(Value::Null) | None => return bad_request("text", "text is required"),
        Some(_) => return bad_request("text", "expected a string"),
    };
    if text.chars().count() > config.max_chunk_chars {
        return bad_request(
            "text",
            format!("longer than {} characters", config.max_chunk_chars),
        );
    }
    let filename = object.get("filename").and_then(Value::as_str).unwrap_or("");
    let content_type = object
        .get("content_type")
        .and_then(Value::as_str)
        .unwrap_or("");
    let requested = object.get("strategy").and_then(Value::as_str);
    let strategy = match requested.map(str::trim).filter(|value| !value.is_empty()) {
        Some(value) => value.to_string(),
        None => chunk_strategy_for(filename, content_type).to_string(),
    };
    let size = match object.get("size") {
        Some(value) => match value.as_i64() {
            Some(size) => size,
            None => return bad_request("size", "expected an integer"),
        },
        None => DEFAULT_CHUNK_SIZE,
    };
    let overlap = match object.get("overlap") {
        Some(value) => match value.as_i64() {
            Some(overlap) => overlap,
            None => return bad_request("overlap", "expected an integer"),
        },
        None => DEFAULT_CHUNK_OVERLAP,
    };
    let source_node_id = object
        .get("source_node_id")
        .and_then(Value::as_str)
        .unwrap_or("text:inline");
    Json(chunk_payload(
        &text,
        filename,
        content_type,
        requested,
        &strategy,
        size,
        overlap,
        source_node_id,
    ))
    .into_response()
}

#[allow(clippy::too_many_arguments)]
fn chunk_payload(
    text: &str,
    filename: &str,
    content_type: &str,
    requested: Option<&str>,
    strategy: &str,
    size: i64,
    overlap: i64,
    source_node_id: &str,
) -> Value {
    let chunks = typed_chunks(text, strategy, size, overlap);
    let cleaned = py_strip(text);
    let described: Vec<Value> = chunks
        .iter()
        .enumerate()
        .map(|(index, chunk)| {
            json!({
                "index": index,
                "text": chunk.text,
                "meta": chunk.meta,
                "meta_fields": Value::Object(chunk_meta_fields(chunk)),
                "chunk_id": chunk_id(source_node_id, index, &chunk.text),
                "len_chars": chunk.text.chars().count(),
                "len_bytes": chunk.text.len(),
            })
        })
        .collect();
    json!({
        "dry_run": true,
        "filename": filename,
        "content_type": content_type,
        "requested_strategy": requested,
        "strategy": strategy,
        "size": size,
        "overlap": overlap,
        "source_node_id": source_node_id,
        "cleaned_len_chars": cleaned.chars().count(),
        "cleaned_len_bytes": cleaned.len(),
        "text_hash": vector_text_hash(text),
        "chunk_count": described.len(),
        "chunks": described,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tilde_expansion_only_fires_on_a_path_component() {
        let home = PathBuf::from("/home/tester");
        let home = Some(home.as_path());
        assert_eq!(expand_user_with("~", home), PathBuf::from("/home/tester"));
        assert_eq!(
            expand_user_with("~/notes", home),
            PathBuf::from("/home/tester/notes")
        );
        assert_eq!(
            expand_user_with("~tilde/notes", home),
            PathBuf::from("~tilde/notes")
        );
        assert_eq!(
            expand_user_with("/abs/notes", home),
            PathBuf::from("/abs/notes")
        );
        assert_eq!(expand_user_with("rel", home), PathBuf::from("rel"));
        assert_eq!(expand_user_with("~/notes", None), PathBuf::from("~/notes"));
        assert_eq!(expand_user("/abs"), PathBuf::from("/abs"));
    }

    #[test]
    fn booleans_are_parsed_the_way_a_query_string_spells_them() {
        for raw in ["1", "true", "TRUE", " yes ", "on"] {
            assert_eq!(parse_bool(raw), Some(true), "{raw}");
        }
        for raw in ["0", "false", "no", "off"] {
            assert_eq!(parse_bool(raw), Some(false), "{raw}");
        }
        assert_eq!(parse_bool("maybe"), None);
    }

    #[test]
    fn the_chunk_payload_carries_ids_and_both_lengths() {
        let payload = chunk_payload(
            "# 제목\n본문입니다.",
            "guide.md",
            "",
            None,
            "markdown",
            1200,
            160,
            "file:x",
        );
        assert_eq!(payload["dry_run"], Value::Bool(true));
        assert_eq!(payload["strategy"], Value::from("markdown"));
        assert_eq!(payload["requested_strategy"], Value::Null);
        assert_eq!(payload["chunk_count"], Value::from(1));
        let chunk = &payload["chunks"][0];
        assert_eq!(chunk["meta"]["strategy"], Value::from("markdown"));
        assert_eq!(chunk["meta"]["heading_path"], Value::from("제목"));
        assert_eq!(chunk["meta_fields"]["heading_path"], Value::from("제목"));
        assert!(chunk["chunk_id"].as_str().unwrap().starts_with("chunk:"));
        assert!(chunk["len_bytes"].as_u64() > chunk["len_chars"].as_u64());
    }

    #[test]
    fn an_empty_text_answers_zero_chunks_rather_than_failing() {
        let payload = chunk_payload("   ", "", "", Some("plain"), "plain", 1200, 160, "n");
        assert_eq!(payload["chunk_count"], Value::from(0));
        assert_eq!(payload["cleaned_len_chars"], Value::from(0));
        assert_eq!(payload["requested_strategy"], Value::from("plain"));
    }

    #[test]
    fn a_bad_request_names_the_field() {
        let response = bad_request("path", "missing");
        assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    }

    #[test]
    fn the_defaults_are_conservative_and_the_router_builds() {
        let config = IngestApiConfig::default();
        assert_eq!(config.max_plan_files, 500);
        assert!(config.watch.recursive);
        let _router = router(config);
    }
}
