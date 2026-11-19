//! Workspace snapshot, time-machine, and memory handlers.

use axum::body::Bytes;
use axum::extract::{Path, Query, RawQuery, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::Response;
use serde_json::{json, Map, Value};

use super::super::deps::IngestEvent;
use super::super::http::{
    detail, email_of, gate_read, gate_write, map_store, ok, query_i64, query_str, user,
};
use super::super::reqbody::{self, field, Kind};
use super::super::routes::WorkspaceState;
use super::super::store::StoreError;
use super::super::{memories, snapshots, timeline};
use crate::adminops::admin::{append_audit_event, load_audit_log, load_chat_history};

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
