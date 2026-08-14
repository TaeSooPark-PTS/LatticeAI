//! Workspace OS, onboarding, traces, indexing, snapshots, memories.

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
use axum::body::Bytes;
use axum::extract::{Path, Query, RawQuery, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::Response;
use serde_json::{json, Map, Value};

use super::deps::IngestEvent;
use super::http::{
    detail, email_of, gate_read, gate_write, internal_error, map_store, ok, query_i64, query_str,
    uncaught, user, GRAPH_DISABLED,
};
use super::onboarding::{self, Account};
use super::reqbody::{self, field, Kind};
use super::routes::WorkspaceState;
use super::store::StoreError;
use super::{indexing, memories, snapshots, timeline};
use crate::admin::{append_audit_event, load_audit_log, load_chat_history};

const STEP: &[reqbody::Field] = &[
    field("step", Kind::RequiredStr),
    field("status", Kind::StrDefault("complete")),
    field("data", Kind::Dict),
    field("error", Kind::StrDefault("")),
];
const COMPLETE: &[reqbody::Field] = &[field("data", Kind::Dict)];
const SNAPSHOT: &[reqbody::Field] = &[field("name", Kind::StrDefault("Workspace snapshot"))];
const COMPARE: &[reqbody::Field] = &[
    field("before_id", Kind::RequiredStr),
    field("after_id", Kind::RequiredStr),
];
const MEMORY: &[reqbody::Field] = &[
    field("kind", Kind::RequiredStr),
    field("content", Kind::RequiredStr),
    field("tags", Kind::List),
    field("memory_id", Kind::OptionalStr),
    field("metadata", Kind::Dict),
];

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

fn load_snapshot_authorized(
    state: &WorkspaceState,
    snapshot_id: &str,
    identity: &lattice_auth::Identity,
) -> Result<Value, Response> {
    let snapshot = snapshots::get_snapshot(&state.store, snapshot_id)
        .map_err(|error| map_store(error, "Snapshot not found"))?;
    state
        .resolver()
        .authorize_record_read(&snapshot, email_of(identity))
        .map_err(|error| detail(StatusCode::FORBIDDEN, &error.to_string()))?;
    Ok(snapshot)
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
        .unwrap_or_else(default_environment);
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
    let environment = state
        .deps
        .providers
        .scan_environment
        .as_ref()
        .map(|probe| probe())
        .unwrap_or_else(default_environment);
    let (recommendations, catalog) = match &state.deps.providers.model_recommendations {
        Some(recommend) => recommend(&environment),
        None => (default_recommendations(), default_catalog()),
    };
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

pub async fn snapshots_list(
    State(state): State<WorkspaceState>,
    headers: HeaderMap,
    RawQuery(query): RawQuery,
) -> Response {
    let identity = match user(&state.auth, &headers) {
        Ok(identity) => identity,
        Err(refusal) => return refusal,
    };
    let scope = match gate_read(&state.resolver(), &headers, query.as_deref(), &identity) {
        Ok(scope) => scope,
        Err(refusal) => return refusal,
    };
    ok(&snapshots::list_snapshots(&state.store, Some(&scope)))
}

pub async fn snapshots_create(
    State(state): State<WorkspaceState>,
    headers: HeaderMap,
    RawQuery(query): RawQuery,
    body: Bytes,
) -> Response {
    let identity = match user(&state.auth, &headers) {
        Ok(identity) => identity,
        Err(refusal) => return refusal,
    };
    let parsed = match reqbody::parse(&body, SNAPSHOT) {
        Ok(parsed) => parsed,
        Err(refusal) => return refusal,
    };
    let scope = match gate_write(&state.resolver(), &headers, query.as_deref(), &identity) {
        Ok(scope) => scope,
        Err(refusal) => return refusal,
    };
    let history = load_chat_history(&state.data_dir);
    let settings = (state.deps.providers.settings)();
    let models = (state.deps.providers.models)();
    match snapshots::create_snapshot(
        &state.store,
        parsed.str("name"),
        state.deps.graph(),
        &history,
        &settings,
        &models,
        Some(&scope),
    ) {
        Ok(result) => {
            let mut payload = Map::new();
            payload.insert("user_email".into(), json!(identity.email));
            payload.insert(
                "snapshot_id".into(),
                result
                    .get("snapshot")
                    .and_then(|item| item.get("id"))
                    .cloned()
                    .unwrap_or(Value::Null),
            );
            append_audit_event(
                &state
                    .data_dir
                    .join(lattice_core::db::tables::state_files::AUDIT_LOG),
                "workspace_snapshot",
                payload,
            );
            ok(&result)
        }
        Err(error) => map_store(error, "Snapshot not found"),
    }
}

pub async fn snapshots_compare(
    State(state): State<WorkspaceState>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let identity = match user(&state.auth, &headers) {
        Ok(identity) => identity,
        Err(refusal) => return refusal,
    };
    let parsed = match reqbody::parse(&body, COMPARE) {
        Ok(parsed) => parsed,
        Err(refusal) => return refusal,
    };
    if let Err(refusal) = load_snapshot_authorized(&state, parsed.str("before_id"), &identity) {
        return refusal;
    }
    if let Err(refusal) = load_snapshot_authorized(&state, parsed.str("after_id"), &identity) {
        return refusal;
    }
    match snapshots::compare_snapshots(
        &state.store,
        parsed.str("before_id"),
        parsed.str("after_id"),
    ) {
        Ok(result) => ok(&result),
        Err(error) => map_store(error, "Snapshot not found"),
    }
}

pub async fn snapshots_get(
    State(state): State<WorkspaceState>,
    headers: HeaderMap,
    Path(snapshot_id): Path<String>,
) -> Response {
    let identity = match user(&state.auth, &headers) {
        Ok(identity) => identity,
        Err(refusal) => return refusal,
    };
    match load_snapshot_authorized(&state, &snapshot_id, &identity) {
        Ok(snapshot) => ok(&snapshot),
        Err(refusal) => refusal,
    }
}

pub async fn snapshots_area(
    State(state): State<WorkspaceState>,
    headers: HeaderMap,
    Path((snapshot_id, area)): Path<(String, String)>,
) -> Response {
    let identity = match user(&state.auth, &headers) {
        Ok(identity) => identity,
        Err(refusal) => return refusal,
    };
    if let Err(refusal) = load_snapshot_authorized(&state, &snapshot_id, &identity) {
        return refusal;
    }
    match snapshots::snapshot_view(&state.store, &snapshot_id, &area) {
        Ok(view) => ok(&view),
        Err(error) => map_store(error, "Snapshot not found"),
    }
}

pub async fn snapshots_export(
    State(state): State<WorkspaceState>,
    headers: HeaderMap,
    Path(snapshot_id): Path<String>,
) -> Response {
    let identity = match user(&state.auth, &headers) {
        Ok(identity) => identity,
        Err(refusal) => return refusal,
    };
    if let Err(refusal) = load_snapshot_authorized(&state, &snapshot_id, &identity) {
        return refusal;
    }
    match snapshots::export_snapshot(&state.store, &snapshot_id) {
        Ok(result) => {
            let mut payload = Map::new();
            payload.insert("user_email".into(), json!(identity.email));
            payload.insert("snapshot_id".into(), json!(snapshot_id));
            payload.insert(
                "path".into(),
                result.get("export_path").cloned().unwrap_or(Value::Null),
            );
            append_audit_event(
                &state
                    .data_dir
                    .join(lattice_core::db::tables::state_files::AUDIT_LOG),
                "workspace_snapshot_export",
                payload,
            );
            ok(&result)
        }
        Err(error) => map_store(error, "Snapshot not found"),
    }
}

pub async fn snapshots_restore(
    State(state): State<WorkspaceState>,
    headers: HeaderMap,
    RawQuery(query): RawQuery,
    Path(snapshot_id): Path<String>,
) -> Response {
    let identity = match user(&state.auth, &headers) {
        Ok(identity) => identity,
        Err(refusal) => return refusal,
    };
    let snapshot = match load_snapshot_authorized(&state, &snapshot_id, &identity) {
        Ok(snapshot) => snapshot,
        Err(refusal) => return refusal,
    };
    let scope = match gate_write(&state.resolver(), &headers, query.as_deref(), &identity) {
        Ok(scope) => scope,
        Err(refusal) => return refusal,
    };
    if let Some(owner) = snapshot
        .get("workspace_id")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
    {
        if owner != scope {
            return detail(
                StatusCode::FORBIDDEN,
                "snapshot belongs to a different workspace",
            );
        }
    }
    match snapshots::restore_snapshot(&state.store, &snapshot_id, Some(&scope)) {
        Ok(result) => {
            let mut payload = Map::new();
            payload.insert("user_email".into(), json!(identity.email));
            payload.insert("snapshot_id".into(), json!(snapshot_id));
            payload.insert(
                "restore_id".into(),
                result
                    .get("restore")
                    .and_then(|item| item.get("id"))
                    .cloned()
                    .unwrap_or(Value::Null),
            );
            append_audit_event(
                &state
                    .data_dir
                    .join(lattice_core::db::tables::state_files::AUDIT_LOG),
                "workspace_snapshot_restore",
                payload,
            );
            ok(&result)
        }
        Err(StoreError::Value(message)) => detail(StatusCode::CONFLICT, &message),
        Err(error) => map_store(error, "Snapshot not found"),
    }
}

pub async fn time_machine(
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
    let audit = load_audit_log(
        &state
            .data_dir
            .join(lattice_core::db::tables::state_files::AUDIT_LOG),
    );
    ok(&timeline::timeline(
        &state.store,
        &audit,
        query_i64(&pairs, "limit", 100),
        Some(&scope),
    ))
}

pub async fn time_machine_view(
    State(state): State<WorkspaceState>,
    headers: HeaderMap,
    Path((snapshot_id, area)): Path<(String, String)>,
) -> Response {
    let identity = match user(&state.auth, &headers) {
        Ok(identity) => identity,
        Err(refusal) => return refusal,
    };
    if let Err(refusal) = load_snapshot_authorized(&state, &snapshot_id, &identity) {
        return refusal;
    }
    match snapshots::snapshot_view(&state.store, &snapshot_id, &area) {
        Ok(view) => ok(&view),
        Err(error) => map_store(error, "Snapshot not found"),
    }
}

pub async fn memories_list(
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
    ok(&memories::list_memories(
        &state.store,
        email_of(&identity),
        query_str(&pairs, "kind"),
        Some(&scope),
    ))
}

pub async fn memories_search(
    State(state): State<WorkspaceState>,
    headers: HeaderMap,
    RawQuery(query): RawQuery,
    Query(pairs): Query<Vec<(String, String)>>,
) -> Response {
    let identity = match user(&state.auth, &headers) {
        Ok(identity) => identity,
        Err(refusal) => return refusal,
    };
    let Some(q) = query_str(&pairs, "q") else {
        return reqbody::missing_query_parameter("q");
    };
    let scope = match gate_read(&state.resolver(), &headers, query.as_deref(), &identity) {
        Ok(scope) => scope,
        Err(refusal) => return refusal,
    };
    ok(&memories::search_memories(
        &state.store,
        q,
        email_of(&identity),
        query_i64(&pairs, "limit", 20),
        Some(&scope),
    ))
}

pub async fn memories_upsert(
    State(state): State<WorkspaceState>,
    headers: HeaderMap,
    RawQuery(query): RawQuery,
    body: Bytes,
) -> Response {
    let identity = match user(&state.auth, &headers) {
        Ok(identity) => identity,
        Err(refusal) => return refusal,
    };
    let parsed = match reqbody::parse(&body, MEMORY) {
        Ok(parsed) => parsed,
        Err(refusal) => return refusal,
    };
    let scope = match gate_write(&state.resolver(), &headers, query.as_deref(), &identity) {
        Ok(scope) => scope,
        Err(refusal) => return refusal,
    };
    let planned = match memories::plan(
        &state.store,
        parsed.str("kind"),
        parsed.str("content"),
        email_of(&identity),
        &parsed.list("tags"),
        parsed.opt_str("memory_id"),
        &parsed.dict("metadata"),
        Some(&scope),
    ) {
        Ok(planned) => planned,
        Err(StoreError::Value(message)) => return detail(StatusCode::BAD_REQUEST, &message),
        Err(error) => return map_store(error, "Memory not found"),
    };
    let graph = if state.deps.seam.is_available() {
        let event = IngestEvent {
            event_type: "Memory".into(),
            title: format!("{}: {}", planned.kind(), planned.content()),
            user_email: email_of(&identity).map(str::to_string),
            source: "workspace_os".into(),
            workspace_id: planned.workspace_id().map(str::to_string),
            metadata: json!({"memory_id": planned.memory_id(), "tags": planned.tags()}),
        };
        Some(state.deps.seam.ingest_event(&event).await)
    } else {
        None
    };
    match memories::commit(&state.store, planned, graph) {
        Ok(record) => ok(&json!({"memory": record})),
        Err(error) => map_store(error, "Memory not found"),
    }
}

pub async fn memories_delete(
    State(state): State<WorkspaceState>,
    headers: HeaderMap,
    Path(memory_id): Path<String>,
) -> Response {
    let identity = match user(&state.auth, &headers) {
        Ok(identity) => identity,
        Err(refusal) => return refusal,
    };
    let record = match memories::get_memory(&state.store, &memory_id) {
        Ok(record) => record,
        Err(error) => return map_store(error, "Memory not found"),
    };
    if let Err(error) = state
        .resolver()
        .authorize_memory_delete(&record, email_of(&identity))
    {
        return detail(StatusCode::FORBIDDEN, &error.to_string());
    }
    match memories::delete_memory(&state.store, &memory_id) {
        Ok(result) => ok(&result),
        Err(error) => map_store(error, "Memory not found"),
    }
}
