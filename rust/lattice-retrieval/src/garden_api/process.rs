//! The two garden HTTP handlers and the `process()` write.

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
use std::path::Path;

use axum::extract::{Request, State};
use axum::response::Response;
use axum::routing::{get, post};
use axum::Router;
use lattice_auth::OrderedMap;
use serde_json::Value;

use crate::memory_api::shared::{body_opt_str, body_required_str, json_body, ok_json, BrainState};

use super::vault;

/// The (method, path) table this family mounts, in axum spelling.
pub const MOUNTED: &[(&str, &str)] = &[("POST", "/garden"), ("GET", "/garden/tree")];

/// The mountable router for `latticeai/api/garden.py`.
pub fn router(state: BrainState) -> Router {
    Router::new()
        .route("/garden", post(garden_put))
        .route("/garden/tree", get(garden_tree))
        .with_state(state)
}

async fn garden_put(State(state): State<BrainState>, request: Request) -> Response {
    let (parts, body) = request.into_parts();
    let payload = match json_body(body).await {
        Ok(payload) => payload,
        Err(refusal) => return refusal,
    };
    let raw = match body_required_str(&payload, "raw_data") {
        Ok(raw) => raw,
        Err(refusal) => return refusal,
    };
    let category = body_opt_str(&payload, "category");
    let request = Request::from_parts(parts, axum::body::Body::empty());
    if let Err(refusal) = state.require_user(request.headers()) {
        return refusal;
    }
    let dir = state.brain_dir().to_path_buf();
    let now = state.now();
    let category = category.clone();
    let saved =
        match tokio::task::spawn_blocking(move || save_note(&dir, &raw, category.as_deref(), &now))
            .await
        {
            Ok(Ok(saved)) => saved,
            Ok(Err(error)) => {
                return crate::memory_api::shared::detail_response(500, &error);
            }
            Err(error) => {
                return crate::memory_api::shared::detail_response(
                    500,
                    &format!("the task did not finish: {error}"),
                );
            }
        };
    let ingest = ingest_note(&state, &saved).await;
    let mut out = OrderedMap::new();
    out.insert("status", Value::String("saved".to_string()));
    out.insert("folder", Value::String(saved.folder.to_string()));
    out.insert("filename", Value::String(saved.filename.clone()));
    out.insert("path", Value::String(saved.path.clone()));
    out.insert("classified_as", Value::String(saved.folder.to_string()));
    out.insert(
        "description",
        Value::String(vault::description(saved.folder).to_string()),
    );
    for (key, value) in ingest {
        out.insert(key, value);
    }
    ok_json(&out)
}

async fn garden_tree(State(state): State<BrainState>, request: Request) -> Response {
    if let Err(refusal) = state.require_user(request.headers()) {
        return refusal;
    }
    let dir = state.brain_dir().to_path_buf();
    match tokio::task::spawn_blocking(move || {
        vault::ensure_structure(
            &dir,
            &vault::stamps(&crate::memory_api::shared::now_iso()).minute,
        );
        vault::get_tree(&dir)
    })
    .await
    {
        Ok(tree) => ok_json(&tree),
        Err(error) => crate::memory_api::shared::detail_response(
            500,
            &format!("the task did not finish: {error}"),
        ),
    }
}

struct Saved {
    folder: &'static str,
    filename: String,
    path: String,
    title: String,
    text: String,
}

fn save_note(dir: &Path, raw: &str, category: Option<&str>, now: &str) -> Result<Saved, String> {
    let stamps = vault::stamps(now);
    vault::ensure_structure(dir, &stamps.minute);
    let folder = vault::folder_for(category, raw);
    let filename = vault::make_filename(raw, &stamps.file);
    let path = dir.join(folder).join(&filename);
    let content = vault::wrap_markdown(raw, folder, &stamps.minute);
    std::fs::write(&path, content.as_bytes()).map_err(|error| error.to_string())?;
    let preview: String = raw.chars().take(200).collect();
    vault::append_log(dir, &preview, folder, &filename, &stamps.date, &stamps.hhmm)
        .map_err(|error| error.to_string())?;
    Ok(Saved {
        folder,
        filename,
        path: path.display().to_string(),
        title: vault::first_line_of(raw).chars().take(80).collect(),
        text: raw.to_string(),
    })
}

/// `_ingest_note` — delegated to the worker's ingest door.
async fn ingest_note(state: &BrainState, saved: &Saved) -> Vec<(&'static str, Value)> {
    let Some(seam) = state.seam() else {
        return vec![
            ("graph", Value::String("unavailable".to_string())),
            (
                "graph_detail",
                Value::String("ingestion pipeline not wired".to_string()),
            ),
        ];
    };
    let payload = serde_json::json!({
        "type": "note",
        "title": saved.title,
        "content": saved.text,
        "source": saved.path,
        "metadata": {
            "garden_folder": saved.folder,
            "pipeline": "p-reinforce",
        },
    });
    match seam.post_json("/knowledge-graph/ingest", &payload).await {
        Ok(value) => {
            let status = value.get("status").and_then(Value::as_str).unwrap_or("ok");
            if status != "ok" {
                return vec![
                    ("graph", Value::String(status.to_string())),
                    (
                        "graph_detail",
                        value
                            .get("detail")
                            .cloned()
                            .unwrap_or(Value::String(String::new())),
                    ),
                ];
            }
            vec![
                ("graph", Value::String("ok".to_string())),
                (
                    "graph_node_id",
                    value
                        .get("node_id")
                        .cloned()
                        .unwrap_or(Value::String(String::new())),
                ),
                (
                    "provenance_id",
                    value
                        .get("provenance_id")
                        .cloned()
                        .unwrap_or(Value::String(String::new())),
                ),
                (
                    "duplicate",
                    value
                        .get("duplicate")
                        .cloned()
                        .unwrap_or(Value::Bool(false)),
                ),
            ]
        }
        Err(error) => vec![
            ("graph", Value::String("failed".to_string())),
            ("graph_detail", Value::String(error.to_string())),
        ],
    }
}
