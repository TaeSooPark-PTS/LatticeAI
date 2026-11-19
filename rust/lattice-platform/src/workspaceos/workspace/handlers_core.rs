//! Workspace OS, onboarding, traces, indexing, snapshots, memories.

use axum::body::Bytes;
use axum::extract::{Path, Query, RawQuery, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::Response;
use serde_json::{json, Map, Value};

use super::http::{
    detail, email_of, gate_read, internal_error, ok, query_i64, query_str, uncaught, user,
    GRAPH_DISABLED,
};
use super::onboarding::{self, Account};
use super::reqbody::{self, field, Kind};
use super::routes::WorkspaceState;
use super::{indexing, timeline};
use crate::adminops::admin::{append_audit_event, json_from_ordered};
use crate::modelops::models_catalog;
use crate::modelops::models_catalog::{fetch_worker_catalog, probe_host, recommend_from_catalog};
use crate::modelops::setup;

const STEP: &[reqbody::Field] = &[
    field("step", Kind::RequiredStr),
    field("status", Kind::StrDefault("complete")),
    field("data", Kind::Dict),
    field("error", Kind::StrDefault("")),
];
const COMPLETE: &[reqbody::Field] = &[field("data", Kind::Dict)];

#[path = "handlers_snapshots.rs"]
mod handlers_snapshots;
pub use handlers_snapshots::*;

fn accounts(state: &WorkspaceState) -> Vec<(String, String)> {
    let users = state.auth.users().load();
    users
        .iter()
        .map(|(email, _record)| {
            let role = state.auth.get_user_role(email, &users);
            (email.to_string(), role)
        })
        .collect()
}

fn account_refs(accounts: &[(String, String)]) -> Vec<Account<'_>> {
    accounts
        .iter()
        .map(|(email, role)| (email.as_str(), role.as_str()))
        .collect()
}

fn require_graph(state: &WorkspaceState) -> Result<(), Response> {
    if state.deps.graph().is_none() {
        return Err(detail(StatusCode::NOT_FOUND, GRAPH_DISABLED));
    }
    Ok(())
}

pub async fn os_summary(State(state): State<WorkspaceState>, headers: HeaderMap) -> Response {
    let identity = match user(&state.auth, &headers) {
        Ok(identity) => identity,
        Err(refusal) => return refusal,
    };
    let mut summary = state.resolver().summary(email_of(&identity));
    summary["graph"] = state.deps.graph_stats_safe();
    summary["models"] = (state.deps.providers.models)();
    summary["edition"] = state.deps.edition();
    ok(&summary)
}

pub async fn onboarding_status(
    State(state): State<WorkspaceState>,
    headers: HeaderMap,
) -> Response {
    if let Err(refusal) = user(&state.auth, &headers) {
        return refusal;
    }
    let held = accounts(&state);
    let refs = account_refs(&held);
    ok(&onboarding::status(
        &state.store,
        &refs,
        &state.deps.graph_stats_safe(),
    ))
}

pub async fn onboarding_step(
    State(state): State<WorkspaceState>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let identity = match user(&state.auth, &headers) {
        Ok(identity) => identity,
        Err(refusal) => return refusal,
    };
    let parsed = match reqbody::parse(&body, STEP) {
        Ok(parsed) => parsed,
        Err(refusal) => return refusal,
    };
    let held = accounts(&state);
    let refs = account_refs(&held);
    match onboarding::update_step(
        &state.store,
        parsed.str("step"),
        parsed.str("status"),
        Some(&parsed.dict("data")),
        parsed.str("error"),
        email_of(&identity),
        &refs,
        &state.deps.graph_stats_safe(),
    ) {
        Ok(payload) => ok(&payload),
        Err(error) => uncaught(error),
    }
}

pub async fn onboarding_complete(
    State(state): State<WorkspaceState>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let identity = match user(&state.auth, &headers) {
        Ok(identity) => identity,
        Err(refusal) => return refusal,
    };
    let parsed = match reqbody::parse(&body, COMPLETE) {
        Ok(parsed) => parsed,
        Err(refusal) => return refusal,
    };
    let mut payload = Map::new();
    payload.insert("user_email".into(), json!(identity.email));
    payload.insert("platform".into(), json!("AI Workspace OS"));
    append_audit_event(
        &state
            .data_dir
            .join(lattice_core::db::tables::state_files::AUDIT_LOG),
        "onboarding_complete",
        payload,
    );
    let held = accounts(&state);
    let refs = account_refs(&held);
    match onboarding::complete(
        &state.store,
        Some(&parsed.dict("data")),
        email_of(&identity),
        &refs,
        &state.deps.graph_stats_safe(),
    ) {
        Ok(payload) => ok(&payload),
        Err(error) => uncaught(error),
    }
}

fn hardware_payload(state: &WorkspaceState) -> Value {
    let environment = state
        .deps
        .providers
        .scan_environment
        .as_ref()
        .map(|probe| probe())
        .unwrap_or_else(|| host_environment(state, None));
    let sysinfo = state
        .deps
        .providers
        .local_sysinfo
        .as_ref()
        .map(|probe| probe())
        .unwrap_or_else(default_sysinfo);
    json!({
        "environment": environment,
        "sysinfo": sysinfo,
        "scanned_at": super::pyutil::now_iso(),
    })
}

fn host_environment(
    state: &WorkspaceState,
    catalog: Option<&models_catalog::WorkerCatalog>,
) -> Value {
    let probe = probe_host(Some(&state.data_dir));
    let fallback = models_catalog::WorkerCatalog {
        reachable: false,
        reason: Some("onboarding hardware probe is host-only".into()),
        models: json!({}),
        sysinfo: json!({}),
    };
    let env = setup::scan_environment(&probe, catalog.unwrap_or(&fallback));
    json_from_ordered(&env)
}

fn default_environment() -> Value {
    json!({
        "os": "unknown", "os_version": "", "chip": "", "cpu": "", "gpu": "",
        "cuda": false, "wsl": false, "ram_gb": 0, "disk_free_gb": 0,
        "tools": {}, "components": {}, "path": [], "mlx": false, "api_keys": {},
    })
}

fn default_sysinfo() -> Value {
    json!({
        "cpu_pct": 0, "ram_pct": 0, "gpu_mem_pct": 0, "gpu_mem_gb": 0, "readiness": "unknown",
    })
}

pub async fn onboarding_hardware(
    State(state): State<WorkspaceState>,
    headers: HeaderMap,
) -> Response {
    let identity = match user(&state.auth, &headers) {
        Ok(identity) => identity,
        Err(refusal) => return refusal,
    };
    let payload = hardware_payload(&state);
    let held = accounts(&state);
    let refs = account_refs(&held);
    let _ = onboarding::update_step(
        &state.store,
        "hardware",
        "complete",
        Some(&payload),
        "",
        email_of(&identity),
        &refs,
        &state.deps.graph_stats_safe(),
    );
    ok(&payload)
}

pub async fn onboarding_models(
    State(state): State<WorkspaceState>,
    headers: HeaderMap,
) -> Response {
    let identity = match user(&state.auth, &headers) {
        Ok(identity) => identity,
        Err(refusal) => return refusal,
    };
    let (environment, recommendations, catalog) = onboarding_models_payload(&state).await;
    let payload = json!({
        "environment": environment,
        "recommendations": recommendations,
        "catalog": catalog,
        "default_local_model": state.deps.providers.local_model,
        "default_public_model": state.deps.providers.public_model,
    });
    let held = accounts(&state);
    let refs = account_refs(&held);
    let _ = onboarding::update_step(
        &state.store,
        "model_recommendation",
        "complete",
        Some(&payload),
        "",
        email_of(&identity),
        &refs,
        &state.deps.graph_stats_safe(),
    );
    ok(&payload)
}

fn default_recommendations() -> Value {
    json!({"components": [], "engines": [], "models": [], "mcps": [], "summary": {}})
}

fn default_catalog() -> Value {
    json!({
        "engine": "local_mlx", "engine_available": false, "apple_silicon": false,
        "ram_gb": 0, "counts": {}, "top_pick": null, "families": {}, "models": [],
    })
}

async fn onboarding_models_payload(state: &WorkspaceState) -> (Value, Value, Value) {
    let injected_scan = state.deps.providers.scan_environment.as_ref();
    let injected_recs = state.deps.providers.model_recommendations.as_ref();
    if injected_scan.is_some() || injected_recs.is_some() {
        let environment = injected_scan
            .map(|probe| probe())
            .unwrap_or_else(default_environment);
        let (recommendations, catalog) = match injected_recs {
            Some(recommend) => recommend(&environment),
            None => (default_recommendations(), default_catalog()),
        };
        return (environment, recommendations, catalog);
    }

    let probe = probe_host(Some(&state.data_dir));
    let worker_catalog = fetch_worker_catalog(state.worker.as_ref()).await;
    let environment = json_from_ordered(&setup::scan_environment(&probe, &worker_catalog));
    let (recs, _) = recommend_from_catalog(&probe, "local_mlx", &worker_catalog);
    let catalog = json_from_ordered(&recs);
    let mut recommendations = default_recommendations();
    if let Some(object) = recommendations.as_object_mut() {
        if let Some(models) = recs.get("models") {
            object.insert("models".into(), models.clone());
        }
        if let Some(top) = recs.get("top_pick") {
            object.insert("top_pick".into(), top.clone());
        }
    }
    (environment, recommendations, catalog)
}

pub async fn traces(
    State(state): State<WorkspaceState>,
    headers: HeaderMap,
    RawQuery(query): RawQuery,
    Query(pairs): Query<Vec<(String, String)>>,
) -> Response {
    let identity = match user(&state.auth, &headers) {
        Ok(identity) => identity,
        Err(refusal) => return refusal,
    };
    let scope = match gate_read(&state.resolver(), &headers, query.as_deref(), &identity) {
        Ok(scope) => scope,
        Err(refusal) => return refusal,
    };
    ok(&timeline::list_traces(
        &state.store,
        query_str(&pairs, "conversation_id"),
        query_i64(&pairs, "limit", 50),
        Some(&scope),
    ))
}

pub async fn indexing(State(state): State<WorkspaceState>, headers: HeaderMap) -> Response {
    if let Err(refusal) = user(&state.auth, &headers) {
        return refusal;
    }
    let watcher = state
        .deps
        .providers
        .watcher_status
        .as_ref()
        .map(|probe| probe());
    ok(&indexing::build_dashboard(state.deps.graph(), watcher))
}

pub async fn indexing_pause(
    State(state): State<WorkspaceState>,
    headers: HeaderMap,
    Path(source_id): Path<String>,
) -> Response {
    if let Err(refusal) = user(&state.auth, &headers) {
        return refusal;
    }
    if let Err(refusal) = require_graph(&state) {
        return refusal;
    }
    match state
        .deps
        .seam
        .set_local_source_watch(&source_id, false)
        .await
    {
        Ok(source) => {
            state.store.record_timeline_event(
                "graph",
                "indexing_paused",
                json!({"source_id": source_id}),
                None,
            );
            ok(&indexing::pause_answer(
                source,
                indexing::stopped_without_watcher(&source_id),
            ))
        }
        Err(_) => internal_error(),
    }
}

pub async fn indexing_resume(
    State(state): State<WorkspaceState>,
    headers: HeaderMap,
    Path(source_id): Path<String>,
) -> Response {
    if let Err(refusal) = user(&state.auth, &headers) {
        return refusal;
    }
    if let Err(refusal) = require_graph(&state) {
        return refusal;
    }
    match state
        .deps
        .seam
        .set_local_source_watch(&source_id, true)
        .await
    {
        Ok(source) => {
            state.store.record_timeline_event(
                "graph",
                "indexing_resumed",
                json!({"source_id": source_id}),
                None,
            );
            ok(&indexing::pause_answer(
                source,
                indexing::not_watching(&source_id),
            ))
        }
        Err(_) => internal_error(),
    }
}

pub async fn indexing_remove(
    State(state): State<WorkspaceState>,
    headers: HeaderMap,
    Path(source_id): Path<String>,
) -> Response {
    if let Err(refusal) = user(&state.auth, &headers) {
        return refusal;
    }
    if let Err(refusal) = require_graph(&state) {
        return refusal;
    }
    match state.deps.seam.remove_local_source(&source_id).await {
        Ok(result) => {
            state.store.record_timeline_event(
                "graph",
                "indexing_removed",
                json!({"source_id": source_id}),
                None,
            );
            ok(&indexing::remove_answer(result))
        }
        Err(_) => internal_error(),
    }
}
