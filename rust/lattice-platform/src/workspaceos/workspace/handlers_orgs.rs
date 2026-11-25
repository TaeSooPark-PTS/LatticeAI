use axum::body::Bytes;
use axum::extract::{Path, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::Response;
use serde_json::{json, Value};

use super::super::http::{detail, email_of, map_store, ok, user};
use super::super::reqbody::{self, field, Kind};
use super::super::routes::WorkspaceState;
use super::super::store::StoreError;
use super::audit;

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
