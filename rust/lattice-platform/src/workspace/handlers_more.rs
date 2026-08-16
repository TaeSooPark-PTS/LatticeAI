//! Agents, workflows, skills, VS Code, computer memory, orgs.

use axum::body::Bytes;
use axum::extract::{Path, Query, RawQuery, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::Response;
use serde_json::{json, Map, Value};

use super::deps::IngestEvent;
use super::http::{
    admin, detail, email_of, gate_read, gate_write, internal_error, map_store, ok, query_i64,
    query_str, user, GRAPH_DISABLED,
};
use super::redact::redact_secret_text;
use super::reqbody::{self, field, Kind};
use super::routes::WorkspaceState;
use super::runs::{self, AgentRunRequest};
use super::store::StoreError;
use super::{computer, relationships, skills, timeline};
use crate::admin::{append_audit_event, load_audit_log};

const AGENT_RUN: &[reqbody::Field] = &[
    field("agent_id", Kind::StrDefault("agent:executor")),
    field("status", Kind::StrDefault("ok")),
    field("input", Kind::StrDefault("")),
    field("output", Kind::StrDefault("")),
    field("timeline", Kind::List),
    field("relationships", Kind::List),
];
const WORKFLOW: &[reqbody::Field] = &[
    field("name", Kind::RequiredStr),
    field("steps", Kind::List),
    field("metadata", Kind::Dict),
];
const WORKFLOW_EVENT: &[reqbody::Field] = &[
    field("event_type", Kind::RequiredStr),
    field("payload", Kind::Dict),
];
const COMPUTER: &[reqbody::Field] = &[
    field("enabled", Kind::Bool(false)),
    field("consent", Kind::Dict),
    field("scopes", Kind::List),
];
const ACTIVITY: &[reqbody::Field] = &[field("activity", Kind::Dict)];
const SKILL: &[reqbody::Field] = &[
    field("skill", Kind::RequiredStr),
    field("plugin", Kind::OptionalStr),
    field("enabled", Kind::OptionalBool),
    field("version", Kind::OptionalStr),
    field("metadata", Kind::Dict),
];
const VSCODE_STATUS: &[reqbody::Field] = &[
    field("status", Kind::StrDefault("connected")),
    field("index_status", Kind::StrDefault("unknown")),
    field("workspace_folder", Kind::StrDefault("")),
    field("extension_version", Kind::StrDefault("")),
    field("active_file", Kind::StrDefault("")),
    field("detail", Kind::StrDefault("")),
];
const VSCODE_SEND: &[reqbody::Field] = &[
    field("action", Kind::RequiredStr),
    field("file_path", Kind::OptionalStr),
    field("language", Kind::OptionalStr),
    field("content", Kind::StrDefault("")),
    field("selection", Kind::StrDefault("")),
    field("prompt", Kind::StrDefault("")),
    field("extension_version", Kind::StrDefault("")),
    field("workspace_folder", Kind::StrDefault("")),
];
const CREATE: &[reqbody::Field] = &[
    field("name", Kind::RequiredStr),
    field("settings", Kind::Dict),
];
const UPDATE: &[reqbody::Field] = &[
    field("name", Kind::OptionalStr),
    field("settings", Kind::OptionalDict),
];
const MEMBER: &[reqbody::Field] = &[
    field("user_id", Kind::RequiredStr),
    field("role", Kind::StrDefault("member")),
];
const MEMBER_ROLE: &[reqbody::Field] = &[field("role", Kind::RequiredStr)];
const ACTIVATE: &[reqbody::Field] = &[field("workspace_id", Kind::RequiredStr)];

fn audit_path(state: &WorkspaceState) -> std::path::PathBuf {
    state
        .data_dir
        .join(lattice_core::db::tables::state_files::AUDIT_LOG)
}

fn audit(state: &WorkspaceState, event_type: &str, fields: &[(&str, Value)]) {
    let mut payload = Map::new();
    for (key, value) in fields {
        payload.insert((*key).into(), value.clone());
    }
    append_audit_event(&audit_path(state), event_type, payload);
}

pub async fn agents_list(
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
    ok(&runs::list_agents(&state.store, Some(&scope)))
}

pub async fn agents_run(
    State(state): State<WorkspaceState>,
    headers: HeaderMap,
    RawQuery(query): RawQuery,
    body: Bytes,
) -> Response {
    let identity = match user(&state.auth, &headers) {
        Ok(identity) => identity,
        Err(refusal) => return refusal,
    };
    let parsed = match reqbody::parse(&body, AGENT_RUN) {
        Ok(parsed) => parsed,
        Err(refusal) => return refusal,
    };
    let scope = match gate_write(&state.resolver(), &headers, query.as_deref(), &identity) {
        Ok(scope) => scope,
        Err(refusal) => return refusal,
    };
    let request = AgentRunRequest {
        agent_id: parsed.str("agent_id").to_string(),
        status: parsed.str("status").to_string(),
        input: parsed.str("input").to_string(),
        output: parsed.str("output").to_string(),
        timeline: parsed.list("timeline"),
        relationships: parsed.list("relationships"),
        mode: "simulation".into(),
    };
    match runs::record_agent_run(
        &state.store,
        &request,
        email_of(&identity),
        Some(&scope),
        None,
    ) {
        Ok(run) => ok(&json!({"run": run})),
        Err(error) => map_store(error, "Not found"),
    }
}

pub async fn relationships(
    State(state): State<WorkspaceState>,
    headers: HeaderMap,
    Path(node_id): Path<String>,
    Query(pairs): Query<Vec<(String, String)>>,
) -> Response {
    if let Err(refusal) = user(&state.auth, &headers) {
        return refusal;
    }
    if state.deps.graph().is_none() {
        return detail(StatusCode::NOT_FOUND, GRAPH_DISABLED);
    }
    ok(&relationships::explore(
        state.deps.graph(),
        &node_id,
        query_str(&pairs, "target_id"),
        relationships::DEFAULT_LIMIT,
    ))
}

pub async fn computer_get(State(state): State<WorkspaceState>, headers: HeaderMap) -> Response {
    if let Err(refusal) = user(&state.auth, &headers) {
        return refusal;
    }
    ok(&computer::config(&state.store))
}

pub async fn computer_config(
    State(state): State<WorkspaceState>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let identity = match user(&state.auth, &headers) {
        Ok(identity) => identity,
        Err(refusal) => return refusal,
    };
    let parsed = match reqbody::parse(&body, COMPUTER) {
        Ok(parsed) => parsed,
        Err(refusal) => return refusal,
    };
    match computer::configure(
        &state.store,
        parsed.bool("enabled"),
        email_of(&identity),
        &parsed.dict("consent"),
        &parsed.list("scopes"),
    ) {
        Ok(config) => {
            audit(
                &state,
                "computer_memory_config",
                &[
                    ("user_email", json!(identity.email)),
                    ("enabled", json!(parsed.bool("enabled"))),
                ],
            );
            ok(&json!({"computer_memory": config}))
        }
        Err(StoreError::Permission(message)) => detail(StatusCode::FORBIDDEN, &message),
        Err(error) => map_store(error, "Not found"),
    }
}

pub async fn computer_activity(
    State(state): State<WorkspaceState>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    if let Err(refusal) = user(&state.auth, &headers) {
        return refusal;
    }
    let parsed = match reqbody::parse(&body, ACTIVITY) {
        Ok(parsed) => parsed,
        Err(refusal) => return refusal,
    };
    let activity = parsed.dict("activity");
    let record = computer::plan_activity(&activity);
    let graph = if state.deps.seam.is_available() {
        let event = IngestEvent {
            event_type: "ComputerActivity".into(),
            title: computer::activity_title(&activity),
            user_email: None,
            source: "workspace_os".into(),
            workspace_id: None,
            metadata: activity,
        };
        Some(state.deps.seam.ingest_event(&event).await)
    } else {
        None
    };
    match computer::record_activity(&state.store, record, graph) {
        Ok(result) => ok(&result),
        Err(error) => map_store(error, "Not found"),
    }
}

pub async fn workflows_list(
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
    ok(&runs::list_workflows(
        &state.store,
        query_str(&pairs, "q").unwrap_or(""),
        Some(&scope),
    ))
}

pub async fn workflows_create(
    State(state): State<WorkspaceState>,
    headers: HeaderMap,
    RawQuery(query): RawQuery,
    body: Bytes,
) -> Response {
    let identity = match user(&state.auth, &headers) {
        Ok(identity) => identity,
        Err(refusal) => return refusal,
    };
    let parsed = match reqbody::parse(&body, WORKFLOW) {
        Ok(parsed) => parsed,
        Err(refusal) => return refusal,
    };
    let scope = match gate_write(&state.resolver(), &headers, query.as_deref(), &identity) {
        Ok(scope) => scope,
        Err(refusal) => return refusal,
    };
    let planned = runs::plan_workflow(
        &state.store,
        parsed.str("name"),
        &parsed.list("steps"),
        &parsed.dict("metadata"),
        email_of(&identity),
        Some(&scope),
    );
    let graph = if state.deps.seam.is_available() {
        let event = IngestEvent {
            event_type: "Workflow".into(),
            title: planned.name().to_string(),
            user_email: email_of(&identity).map(str::to_string),
            source: "workspace_os".into(),
            workspace_id: planned.workspace_id().map(str::to_string),
            metadata: json!({"workflow_id": planned.id(), "steps": planned.steps()}),
        };
        Some(state.deps.seam.ingest_event(&event).await)
    } else {
        None
    };
    match runs::commit_workflow(&state.store, planned, graph) {
        Ok(workflow) => ok(&json!({"workflow": workflow})),
        Err(error) => map_store(error, "Workflow not found"),
    }
}

pub async fn workflows_event(
    State(state): State<WorkspaceState>,
    headers: HeaderMap,
    Path(workflow_id): Path<String>,
    body: Bytes,
) -> Response {
    if let Err(refusal) = user(&state.auth, &headers) {
        return refusal;
    }
    let parsed = match reqbody::parse(&body, WORKFLOW_EVENT) {
        Ok(parsed) => parsed,
        Err(refusal) => return refusal,
    };
    match runs::record_workflow_event(
        &state.store,
        &workflow_id,
        parsed.str("event_type"),
        &parsed.dict("payload"),
    ) {
        Ok(workflow) => ok(&json!({"workflow": workflow})),
        Err(error) => map_store(error, "Workflow not found"),
    }
}

pub async fn skills_list(State(state): State<WorkspaceState>, headers: HeaderMap) -> Response {
    if let Err(refusal) = user(&state.auth, &headers) {
        return refusal;
    }
    let marketplace = (state.deps.providers.skills_marketplace)().unwrap_or_default();
    match skills::list_skill_registry(
        &state.store,
        state.deps.providers.skills_dir.as_deref(),
        &marketplace,
    ) {
        Ok(listing) => ok(&listing),
        Err(error) => map_store(error, "Not found"),
    }
}

fn merge_metadata(result_key: &str, result: Value, extra: Value) -> Value {
    let mut merged = Map::new();
    merged.insert(result_key.into(), result);
    if let Value::Object(map) = extra {
        for (key, value) in map {
            merged.insert(key, value);
        }
    }
    Value::Object(merged)
}

pub async fn skills_install(
    State(state): State<WorkspaceState>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let identity = match admin(&state.auth, &headers) {
        Ok(identity) => identity,
        Err(refusal) => return refusal,
    };
    let parsed = match reqbody::parse(&body, SKILL) {
        Ok(parsed) => parsed,
        Err(refusal) => return refusal,
    };
    let result = if let Some(plugin) = parsed.opt_str("plugin") {
        match &state.deps.providers.install_skill {
            Some(install) => match install(plugin, parsed.str("skill")) {
                Ok(result) => result,
                Err(error) => return detail(StatusCode::BAD_REQUEST, &error),
            },
            None => json!({"status": "recorded", "skill": parsed.str("skill")}),
        }
    } else {
        json!({"status": "recorded", "skill": parsed.str("skill")})
    };
    let metadata = merge_metadata("install_result", result.clone(), parsed.dict("metadata"));
    match skills::mark_installed(
        &state.store,
        parsed.str("skill"),
        parsed.opt_str("version").unwrap_or("local"),
        &metadata,
    ) {
        Ok(entry) => {
            audit(
                &state,
                "skill_install",
                &[
                    ("user_email", json!(identity.email)),
                    ("plugin", json!(parsed.opt_str("plugin"))),
                    ("skill", json!(parsed.str("skill"))),
                    ("workspace_os", json!(true)),
                ],
            );
            ok(&json!({"skill": entry, "install": result}))
        }
        Err(error) => map_store(error, "Not found"),
    }
}

pub async fn skills_uninstall(
    State(state): State<WorkspaceState>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let identity = match admin(&state.auth, &headers) {
        Ok(identity) => identity,
        Err(refusal) => return refusal,
    };
    let parsed = match reqbody::parse(&body, SKILL) {
        Ok(parsed) => parsed,
        Err(refusal) => return refusal,
    };
    let Some(skills_dir) = state.deps.providers.skills_dir.as_deref() else {
        return internal_error();
    };
    let removal = match skills::remove_skill_directory(skills_dir, parsed.str("skill")) {
        Ok(removal) => removal,
        Err(_) => return internal_error(),
    };
    match skills::mark_uninstalled(&state.store, parsed.str("skill")) {
        Ok(entry) => {
            audit(
                &state,
                "skill_uninstall",
                &[
                    ("user_email", json!(identity.email)),
                    ("skill", json!(parsed.str("skill"))),
                    ("workspace_os", json!(true)),
                ],
            );
            ok(&json!({"skill": entry, "removal": removal}))
        }
        Err(error) => map_store(error, "Not found"),
    }
}

pub async fn skills_enable(
    State(state): State<WorkspaceState>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    if let Err(refusal) = user(&state.auth, &headers) {
        return refusal;
    }
    let parsed = match reqbody::parse(&body, SKILL) {
        Ok(parsed) => parsed,
        Err(refusal) => return refusal,
    };
    match skills::set_enabled(&state.store, parsed.str("skill"), true) {
        Ok(entry) => ok(&json!({"skill": entry})),
        Err(error) => map_store(error, "Not found"),
    }
}

pub async fn skills_disable(
    State(state): State<WorkspaceState>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    if let Err(refusal) = user(&state.auth, &headers) {
        return refusal;
    }
    let parsed = match reqbody::parse(&body, SKILL) {
        Ok(parsed) => parsed,
        Err(refusal) => return refusal,
    };
    match skills::set_enabled(&state.store, parsed.str("skill"), false) {
        Ok(entry) => ok(&json!({"skill": entry})),
        Err(error) => map_store(error, "Not found"),
    }
}

pub async fn skills_update(
    State(state): State<WorkspaceState>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let identity = match admin(&state.auth, &headers) {
        Ok(identity) => identity,
        Err(refusal) => return refusal,
    };
    let parsed = match reqbody::parse(&body, SKILL) {
        Ok(parsed) => parsed,
        Err(refusal) => return refusal,
    };
    let result = if let Some(plugin) = parsed.opt_str("plugin") {
        match &state.deps.providers.install_skill {
            Some(install) => match install(plugin, parsed.str("skill")) {
                Ok(result) => result,
                Err(error) => return detail(StatusCode::BAD_REQUEST, &error),
            },
            None => json!({"status": "version_recorded", "skill": parsed.str("skill")}),
        }
    } else {
        json!({"status": "version_recorded", "skill": parsed.str("skill")})
    };
    let metadata = merge_metadata("update_result", result.clone(), parsed.dict("metadata"));
    match skills::mark_installed(
        &state.store,
        parsed.str("skill"),
        parsed.opt_str("version").unwrap_or("latest"),
        &metadata,
    ) {
        Ok(entry) => {
            audit(
                &state,
                "skill_update",
                &[
                    ("user_email", json!(identity.email)),
                    ("plugin", json!(parsed.opt_str("plugin"))),
                    ("skill", json!(parsed.str("skill"))),
                    ("workspace_os", json!(true)),
                ],
            );
            ok(&json!({"skill": entry, "update": result}))
        }
        Err(error) => map_store(error, "Not found"),
    }
}

pub async fn audit_timeline(
    State(state): State<WorkspaceState>,
    headers: HeaderMap,
    Query(pairs): Query<Vec<(String, String)>>,
) -> Response {
    if let Err(refusal) = admin(&state.auth, &headers) {
        return refusal;
    }
    let filter = timeline::AuditFilter {
        user: query_str(&pairs, "user").map(str::to_string),
        event_type: query_str(&pairs, "event_type").map(str::to_string),
        model: query_str(&pairs, "model").map(str::to_string),
        since: query_str(&pairs, "since").map(str::to_string),
        until: query_str(&pairs, "until").map(str::to_string),
        limit: query_i64(&pairs, "limit", 100),
    };
    ok(&timeline::filter_audit_timeline(
        &load_audit_log(&audit_path(&state)),
        &filter,
    ))
}

pub async fn vscode_status(State(state): State<WorkspaceState>, headers: HeaderMap) -> Response {
    if let Err(refusal) = user(&state.auth, &headers) {
        return refusal;
    }
    ok(&state.vscode.status(computer::now_ms()))
}

pub async fn vscode_status_update(
    State(state): State<WorkspaceState>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let identity = match user(&state.auth, &headers) {
        Ok(identity) => identity,
        Err(refusal) => return refusal,
    };
    let parsed = match reqbody::parse(&body, VSCODE_STATUS) {
        Ok(parsed) => parsed,
        Err(refusal) => return refusal,
    };
    ok(&state.vscode.report(
        computer::now_ms(),
        parsed.str("status"),
        parsed.str("index_status"),
        parsed.str("workspace_folder"),
        parsed.str("extension_version"),
        parsed.str("active_file"),
        parsed.str("detail"),
        &identity.email,
    ))
}

pub async fn vscode_send(
    State(state): State<WorkspaceState>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let identity = match user(&state.auth, &headers) {
        Ok(identity) => identity,
        Err(refusal) => return refusal,
    };
    let parsed = match reqbody::parse(&body, VSCODE_SEND) {
        Ok(parsed) => parsed,
        Err(refusal) => return refusal,
    };
    state.vscode.synced(
        computer::now_ms(),
        parsed.str("workspace_folder"),
        parsed.str("extension_version"),
        parsed.opt_str("file_path").unwrap_or(""),
        &identity.email,
    );
    let content = if !parsed.str("selection").is_empty() {
        parsed.str("selection")
    } else if !parsed.str("content").is_empty() {
        parsed.str("content")
    } else {
        parsed.str("prompt")
    };
    let planned = runs::plan_workflow(
        &state.store,
        &format!("VS Code: {}", parsed.str("action")),
        &runs::vscode_steps(
            parsed.str("action"),
            parsed.opt_str("file_path"),
            parsed.opt_str("language"),
            content.chars().count(),
        ),
        &runs::vscode_metadata(
            parsed.opt_str("file_path"),
            parsed.opt_str("language"),
            &redact_secret_text(content),
        ),
        email_of(&identity),
        None,
    );
    let graph = if state.deps.seam.is_available() {
        let event = IngestEvent {
            event_type: "Workflow".into(),
            title: planned.name().to_string(),
            user_email: email_of(&identity).map(str::to_string),
            source: "workspace_os".into(),
            workspace_id: planned.workspace_id().map(str::to_string),
            metadata: json!({"workflow_id": planned.id()}),
        };
        Some(state.deps.seam.ingest_event(&event).await)
    } else {
        None
    };
    let workflow = match runs::commit_workflow(&state.store, planned, graph) {
        Ok(workflow) => workflow,
        Err(error) => return map_store(error, "Not found"),
    };
    if state.deps.seam.is_available() && !content.is_empty() {
        let event = IngestEvent {
            event_type: "VSCodeWorkflow".into(),
            title: parsed.str("action").to_string(),
            user_email: email_of(&identity).map(str::to_string),
            source: "vscode".into(),
            workspace_id: workflow
                .get("workspace_id")
                .and_then(Value::as_str)
                .map(str::to_string),
            metadata: json!({
                "file_path": parsed.opt_str("file_path"),
                "language": parsed.opt_str("language"),
                "chars": content.chars().count(),
                "workflow_id": workflow.get("id"),
            }),
        };
        let _ = state.deps.seam.ingest_event(&event).await;
    }
    ok(&json!({"status": "ok", "workflow": workflow}))
}

pub async fn registry(State(state): State<WorkspaceState>, headers: HeaderMap) -> Response {
    let identity = match user(&state.auth, &headers) {
        Ok(identity) => identity,
        Err(refusal) => return refusal,
    };
    ok(&state.resolver().list_workspaces(email_of(&identity)))
}

pub async fn editions(State(state): State<WorkspaceState>, headers: HeaderMap) -> Response {
    if let Err(refusal) = user(&state.auth, &headers) {
        return refusal;
    }
    ok(&state.deps.edition())
}

pub async fn activate(
    State(state): State<WorkspaceState>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let identity = match user(&state.auth, &headers) {
        Ok(identity) => identity,
        Err(refusal) => return refusal,
    };
    let parsed = match reqbody::parse(&body, ACTIVATE) {
        Ok(parsed) => parsed,
        Err(refusal) => return refusal,
    };
    match state
        .resolver()
        .set_active_workspace(parsed.str("workspace_id"), email_of(&identity))
    {
        Ok(workspace) => ok(&workspace),
        Err(error) => map_store(error, "Workspace not found"),
    }
}

pub async fn org_create(
    State(state): State<WorkspaceState>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let identity = match user(&state.auth, &headers) {
        Ok(identity) => identity,
        Err(refusal) => return refusal,
    };
    let parsed = match reqbody::parse(&body, CREATE) {
        Ok(parsed) => parsed,
        Err(refusal) => return refusal,
    };
    match state.resolver().create_organization_workspace(
        parsed.str("name"),
        email_of(&identity),
        Some(parsed.dict("settings")),
    ) {
        Ok(workspace) => {
            audit(
                &state,
                "workspace_created",
                &[
                    ("user_email", json!(identity.email)),
                    (
                        "workspace_id",
                        workspace
                            .get("workspace_id")
                            .cloned()
                            .unwrap_or(Value::Null),
                    ),
                ],
            );
            ok(&json!({"workspace": workspace}))
        }
        Err(error) => map_store(error, "Workspace not found"),
    }
}

pub async fn org_get(
    State(state): State<WorkspaceState>,
    headers: HeaderMap,
    Path(workspace_id): Path<String>,
) -> Response {
    let identity = match user(&state.auth, &headers) {
        Ok(identity) => identity,
        Err(refusal) => return refusal,
    };
    match state
        .resolver()
        .get_workspace(&workspace_id, email_of(&identity))
    {
        Ok(workspace) => ok(&json!({"workspace": workspace})),
        Err(StoreError::Permission(message)) => detail(StatusCode::FORBIDDEN, &message),
        Err(error) => map_store(error, "Workspace not found"),
    }
}

pub async fn org_summary(
    State(state): State<WorkspaceState>,
    headers: HeaderMap,
    Path(workspace_id): Path<String>,
) -> Response {
    let identity = match user(&state.auth, &headers) {
        Ok(identity) => identity,
        Err(refusal) => return refusal,
    };
    match state
        .resolver()
        .workspace_summary(&workspace_id, email_of(&identity))
    {
        Ok(summary) => ok(&summary),
        Err(StoreError::Permission(message)) => detail(StatusCode::FORBIDDEN, &message),
        Err(error) => map_store(error, "Workspace not found"),
    }
}

pub async fn org_update(
    State(state): State<WorkspaceState>,
    headers: HeaderMap,
    Path(workspace_id): Path<String>,
    body: Bytes,
) -> Response {
    let identity = match user(&state.auth, &headers) {
        Ok(identity) => identity,
        Err(refusal) => return refusal,
    };
    let parsed = match reqbody::parse(&body, UPDATE) {
        Ok(parsed) => parsed,
        Err(refusal) => return refusal,
    };
    let settings = parsed.present("settings").then(|| parsed.value("settings"));
    match state.resolver().update_workspace(
        &workspace_id,
        parsed.opt_str("name"),
        settings.as_ref(),
        email_of(&identity),
    ) {
        Ok(workspace) => {
            audit(
                &state,
                "workspace_updated",
                &[
                    ("user_email", json!(identity.email)),
                    ("workspace_id", json!(workspace_id)),
                ],
            );
            ok(&json!({"workspace": workspace}))
        }
        Err(error) => map_store(error, "Workspace not found"),
    }
}

pub async fn org_archive(
    State(state): State<WorkspaceState>,
    headers: HeaderMap,
    Path(workspace_id): Path<String>,
) -> Response {
    let identity = match user(&state.auth, &headers) {
        Ok(identity) => identity,
        Err(refusal) => return refusal,
    };
    match state
        .resolver()
        .archive_workspace(&workspace_id, email_of(&identity))
    {
        Ok(workspace) => {
            audit(
                &state,
                "workspace_archived",
                &[
                    ("user_email", json!(identity.email)),
                    ("workspace_id", json!(workspace_id)),
                ],
            );
            ok(&json!({"workspace": workspace}))
        }
        Err(error) => map_store(error, "Workspace not found"),
    }
}

pub async fn org_add_member(
    State(state): State<WorkspaceState>,
    headers: HeaderMap,
    Path(workspace_id): Path<String>,
    body: Bytes,
) -> Response {
    let identity = match user(&state.auth, &headers) {
        Ok(identity) => identity,
        Err(refusal) => return refusal,
    };
    let parsed = match reqbody::parse(&body, MEMBER) {
        Ok(parsed) => parsed,
        Err(refusal) => return refusal,
    };
    match state.resolver().add_member(
        &workspace_id,
        parsed.str("user_id"),
        parsed.str("role"),
        email_of(&identity),
    ) {
        Ok(workspace) => {
            audit(
                &state,
                "workspace_member_added",
                &[
                    ("user_email", json!(identity.email)),
                    ("workspace_id", json!(workspace_id)),
                    ("member", json!(parsed.str("user_id"))),
                    ("role", json!(parsed.str("role"))),
                ],
            );
            ok(&json!({"workspace": workspace}))
        }
        Err(error) => map_store(error, "Workspace not found"),
    }
}

pub async fn org_update_member(
    State(state): State<WorkspaceState>,
    headers: HeaderMap,
    Path((workspace_id, user_id)): Path<(String, String)>,
    body: Bytes,
) -> Response {
    let identity = match user(&state.auth, &headers) {
        Ok(identity) => identity,
        Err(refusal) => return refusal,
    };
    let parsed = match reqbody::parse(&body, MEMBER_ROLE) {
        Ok(parsed) => parsed,
        Err(refusal) => return refusal,
    };
    match state.resolver().update_member_role(
        &workspace_id,
        &user_id,
        parsed.str("role"),
        email_of(&identity),
    ) {
        Ok(workspace) => {
            audit(
                &state,
                "workspace_member_role_updated",
                &[
                    ("user_email", json!(identity.email)),
                    ("workspace_id", json!(workspace_id)),
                    ("member", json!(user_id)),
                    ("role", json!(parsed.str("role"))),
                ],
            );
            ok(&json!({"workspace": workspace}))
        }
        Err(error) => map_store(error, "Not found"),
    }
}

pub async fn org_remove_member(
    State(state): State<WorkspaceState>,
    headers: HeaderMap,
    Path((workspace_id, user_id)): Path<(String, String)>,
) -> Response {
    let identity = match user(&state.auth, &headers) {
        Ok(identity) => identity,
        Err(refusal) => return refusal,
    };
    match state
        .resolver()
        .remove_member(&workspace_id, &user_id, email_of(&identity))
    {
        Ok(workspace) => {
            audit(
                &state,
                "workspace_member_removed",
                &[
                    ("user_email", json!(identity.email)),
                    ("workspace_id", json!(workspace_id)),
                    ("member", json!(user_id)),
                ],
            );
            ok(&json!({"workspace": workspace}))
        }
        Err(error) => map_store(error, "Not found"),
    }
}
