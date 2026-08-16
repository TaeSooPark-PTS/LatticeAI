//! Local template marketplace (v11.6.0, WP-R8).
//!
//! Port of `latticeai/api/marketplace.py` + `latticeai/core/marketplace.py`.

use std::path::Path;
use std::sync::Arc;

use crate::mcp::{
    detail, json_status, json_text, parse_json_object, requested_scope, require_user,
    value_to_ordered, PlatformStore, BUILTIN_TEMPLATES_JSON,
};
use axum::extract::{Path as AxumPath, Query, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::Response;
use axum::routing::{get, post};
use axum::Router;
use lattice_auth::{AuthState, OrderedMap};
use serde_json::{json, Value};

pub const MOUNTED: &[(&str, &str)] = &[
    ("GET", "/marketplace/templates"),
    ("GET", "/marketplace/templates/:kind/:template_id/export"),
    ("POST", "/marketplace/templates/import"),
    ("POST", "/marketplace/templates/install"),
    ("POST", "/marketplace/templates/:kind/:template_id/clone"),
    ("GET", "/marketplace/templates/registry"),
    ("GET", "/marketplace/interop/bridges"),
];

const MARKETPLACE_VERSION: &str = env!("CARGO_PKG_VERSION");
const TEMPLATE_KINDS: &[&str] = &["plugin", "workflow", "agent", "ingestion_bridge"];

#[derive(Clone)]
pub struct MarketplaceState {
    pub auth: Arc<AuthState>,
    pub(crate) store: PlatformStore,
}

impl MarketplaceState {
    pub fn new(auth: Arc<AuthState>, data_dir: impl AsRef<Path>) -> Self {
        Self {
            auth,
            store: PlatformStore::new(data_dir),
        }
    }
}

pub fn router(state: MarketplaceState) -> Router {
    Router::new()
        .route("/marketplace/templates", get(list_templates))
        .route(
            "/marketplace/templates/:kind/:template_id/export",
            get(export_template),
        )
        .route("/marketplace/templates/import", post(import_template))
        .route("/marketplace/templates/install", post(install_template))
        .route(
            "/marketplace/templates/:kind/:template_id/clone",
            post(clone_template),
        )
        .route("/marketplace/templates/registry", get(template_registry))
        .route("/marketplace/interop/bridges", get(interop_bridges))
        .with_state(state)
}

fn templates() -> Value {
    serde_json::from_str(BUILTIN_TEMPLATES_JSON).unwrap_or(json!({}))
}

fn normalize_kind(kind: &str) -> Result<String, Response> {
    let value = kind.trim().to_lowercase();
    if TEMPLATE_KINDS.contains(&value.as_str()) {
        Ok(value)
    } else {
        Err(detail(
            StatusCode::BAD_REQUEST,
            &format!("unknown template kind: {kind}"),
        ))
    }
}

fn get_template(kind: &str, template_id: &str) -> Result<Value, Response> {
    let kind = normalize_kind(kind)?;
    let catalog = templates();
    if let Some(list) = catalog.get(&kind).and_then(Value::as_array) {
        if let Some(found) = list
            .iter()
            .find(|t| t.get("id").and_then(Value::as_str) == Some(template_id))
        {
            return Ok(found.clone());
        }
    }
    Err(detail(
        StatusCode::NOT_FOUND,
        &format!("template not found: {kind}/{template_id}"),
    ))
}

fn import_payload(payload: &Value) -> Result<Value, Response> {
    if !payload.is_object() {
        return Err(detail(
            StatusCode::BAD_REQUEST,
            "template import payload must be an object",
        ));
    }
    let mut template = payload
        .get("template")
        .cloned()
        .unwrap_or_else(|| payload.clone());
    let kind_raw = template
        .get("kind")
        .and_then(Value::as_str)
        .or_else(|| payload.get("kind").and_then(Value::as_str))
        .unwrap_or("");
    let kind = normalize_kind(kind_raw)?;
    if template
        .get("id")
        .and_then(Value::as_str)
        .unwrap_or("")
        .is_empty()
    {
        return Err(detail(StatusCode::BAD_REQUEST, "template missing id"));
    }
    if template
        .get("name")
        .and_then(Value::as_str)
        .unwrap_or("")
        .is_empty()
    {
        return Err(detail(StatusCode::BAD_REQUEST, "template missing name"));
    }
    template["kind"] = json!(kind);
    if template.get("version").is_none() {
        template["version"] = json!("1.0.0");
    }
    let mut meta = template.get("metadata").cloned().unwrap_or(json!({}));
    if let Some(obj) = meta.as_object_mut() {
        obj.insert("imported".into(), json!(true));
    }
    template["metadata"] = meta;
    Ok(template)
}

#[derive(Debug, serde::Deserialize, Default)]
struct KindQuery {
    kind: Option<String>,
}

async fn list_templates(
    State(state): State<MarketplaceState>,
    headers: HeaderMap,
    Query(q): Query<KindQuery>,
) -> Response {
    if let Err(r) = require_user(&state.auth, &headers) {
        return r;
    }
    let kinds: Vec<String> = if let Some(kind) = q.kind.as_deref() {
        match normalize_kind(kind) {
            Ok(k) => vec![k],
            Err(r) => return r,
        }
    } else {
        TEMPLATE_KINDS.iter().map(|s| (*s).to_string()).collect()
    };
    let catalog = templates();
    let mut listed = Vec::new();
    for kind in &kinds {
        if let Some(arr) = catalog.get(kind).and_then(Value::as_array) {
            listed.extend(arr.iter().cloned());
        }
    }
    let items: Vec<String> = listed
        .iter()
        .map(|t| serde_json::to_string(&value_to_ordered(t)).unwrap_or_else(|_| "{}".into()))
        .collect();
    let text = format!(
        "{{\"marketplace_version\":\"{MARKETPLACE_VERSION}\",\"kinds\":{},\"templates\":[{}],\"total\":{}}}",
        serde_json::to_string(&TEMPLATE_KINDS).unwrap_or_else(|_| "[]".into()),
        items.join(","),
        listed.len()
    );
    json_text(StatusCode::OK, &text)
}

async fn export_template(
    State(state): State<MarketplaceState>,
    headers: HeaderMap,
    AxumPath((kind, template_id)): AxumPath<(String, String)>,
) -> Response {
    if let Err(r) = require_user(&state.auth, &headers) {
        return r;
    }
    let template = match get_template(&kind, &template_id) {
        Ok(t) => t,
        Err(r) => return r,
    };
    let mut body = OrderedMap::new();
    body.insert("lattice_template_export", json!(MARKETPLACE_VERSION));
    body.insert("kind", template.get("kind").cloned().unwrap_or(json!(kind)));
    body.insert("template", template.clone());
    body.insert(
        "metadata",
        json!({
            "exported_from": "local",
            "template_id": template_id,
            "template_version": template.get("version"),
        }),
    );
    json_status(StatusCode::OK, &body)
}

async fn import_template(
    State(state): State<MarketplaceState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    if let Err(r) = require_user(&state.auth, &headers) {
        return r;
    }
    let parsed = match parse_json_object(&body) {
        Ok(v) => v,
        Err(r) => return r,
    };
    let data = parsed.get("data").cloned().unwrap_or(json!({}));
    match import_payload(&data) {
        Ok(template) => {
            let text = format!(
                "{{\"template\":{}}}",
                serde_json::to_string(&value_to_ordered(&template)).unwrap_or_else(|_| "{}".into())
            );
            json_text(StatusCode::OK, &text)
        }
        Err(r) => r,
    }
}

async fn install_template(
    State(state): State<MarketplaceState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    let identity = match require_user(&state.auth, &headers) {
        Ok(id) => id,
        Err(r) => return r,
    };
    let scope = requested_scope(&headers, None);
    let parsed = match parse_json_object(&body) {
        Ok(v) => v,
        Err(r) => return r,
    };
    let data = parsed.get("data").cloned().unwrap_or(json!({}));
    let imported = match import_payload(&data) {
        Ok(t) => t,
        Err(r) => return r,
    };
    let kind = imported.get("kind").and_then(Value::as_str).unwrap_or("");
    let template_id = imported.get("id").and_then(Value::as_str).unwrap_or("");
    let name = imported.get("name").and_then(Value::as_str).unwrap_or("");
    let version = imported
        .get("version")
        .and_then(Value::as_str)
        .unwrap_or("1.0.0");
    let mut installed = OrderedMap::new();
    installed.insert("kind", json!(kind));
    installed.insert("template_id", json!(template_id));
    installed.insert("name", json!(name));
    installed.insert("version", json!(version));
    if kind == "workflow" {
        let definition = imported.get("definition").cloned().unwrap_or(json!({}));
        let nodes = definition
            .get("nodes")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        let steps: Vec<Value> = nodes
            .iter()
            .map(|node| {
                json!({
                    "action": node.get("type"),
                    "node": node.get("id"),
                })
            })
            .collect();
        let mut metadata = definition.get("metadata").cloned().unwrap_or(json!({}));
        if let Some(obj) = metadata.as_object_mut() {
            obj.insert("template_id".into(), json!(template_id));
        }
        let wf_name = definition
            .get("name")
            .and_then(Value::as_str)
            .unwrap_or(name);
        let workflow = state.store.create_workflow(
            wf_name,
            steps,
            nodes,
            metadata,
            if identity.email.is_empty() {
                None
            } else {
                Some(identity.email.as_str())
            },
            Some(&scope),
        );
        installed.insert(
            "workflow_id",
            workflow.get("id").cloned().unwrap_or(json!("")),
        );
    }
    let registry = state.store.mark_template_installed(
        kind,
        template_id,
        version,
        imported.get("metadata").cloned().unwrap_or(json!({})),
        Some(&scope),
    );
    installed.insert("registry", registry);
    let text = format!(
        "{{\"installed\":{}}}",
        serde_json::to_string(&installed).unwrap_or_else(|_| "{}".into())
    );
    json_text(StatusCode::OK, &text)
}

async fn clone_template(
    State(state): State<MarketplaceState>,
    headers: HeaderMap,
    AxumPath((kind, template_id)): AxumPath<(String, String)>,
    body: axum::body::Bytes,
) -> Response {
    if let Err(r) = require_user(&state.auth, &headers) {
        return r;
    }
    let parsed = if body.is_empty() {
        json!({})
    } else {
        match parse_json_object(&body) {
            Ok(v) => v,
            Err(r) => return r,
        }
    };
    let template = match get_template(&kind, &template_id) {
        Ok(t) => t,
        Err(r) => return r,
    };
    let new_name = parsed
        .get("name")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string)
        .unwrap_or_else(|| {
            format!(
                "{} (Copy)",
                template.get("name").and_then(Value::as_str).unwrap_or("")
            )
        });
    let slug = new_name
        .to_lowercase()
        .replace(' ', "-")
        .replace(['(', ')'], "");
    let mut clone = template.clone();
    let orig_id = template.get("id").and_then(Value::as_str).unwrap_or("");
    let id = format!("{orig_id}-copy-{slug}");
    clone["id"] = json!(&id[..id.len().min(80)]);
    clone["name"] = json!(new_name);
    clone["version"] = json!("1.0.0");
    let mut meta = template.get("metadata").cloned().unwrap_or(json!({}));
    if let Some(obj) = meta.as_object_mut() {
        obj.insert("cloned_from".into(), json!(template_id));
        obj.insert("editable".into(), json!(true));
    }
    clone["metadata"] = meta;
    let text = format!(
        "{{\"template\":{}}}",
        serde_json::to_string(&value_to_ordered(&clone)).unwrap_or_else(|_| "{}".into())
    );
    json_text(StatusCode::OK, &text)
}

async fn template_registry(State(state): State<MarketplaceState>, headers: HeaderMap) -> Response {
    if let Err(r) = require_user(&state.auth, &headers) {
        return r;
    }
    let scope = requested_scope(&headers, None);
    let registry = state.store.list_template_registry(Some(&scope));
    let text = format!(
        "{{\"registry\":{}}}",
        serde_json::to_string(&registry).unwrap_or_else(|_| "{}".into())
    );
    json_text(StatusCode::OK, &text)
}

async fn interop_bridges(State(state): State<MarketplaceState>, headers: HeaderMap) -> Response {
    if let Err(r) = require_user(&state.auth, &headers) {
        return r;
    }
    let catalog = templates();
    let bridges = catalog
        .get("ingestion_bridge")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let items: Vec<String> = bridges
        .iter()
        .map(|t| serde_json::to_string(&value_to_ordered(t)).unwrap_or_else(|_| "{}".into()))
        .collect();
    json_text(
        StatusCode::OK,
        &format!(
            "{{\"bridges\":[{}],\"total\":{},\"pipeline\":\"unified-ingestion\"}}",
            items.join(","),
            bridges.len()
        ),
    )
}
