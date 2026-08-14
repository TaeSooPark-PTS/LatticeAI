//! Invitation API: create, list, and accept workspace invitations.
//!
//! Port of `latticeai/api/invitations.py` over `latticeai/core/invitations.py`.
//! State is `<data_dir>/invitations.json`, written atomically, same shape.

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
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

use axum::body::Bytes;
use axum::extract::{Path as AxumPath, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::Response;
use axum::routing::{get, post};
use axum::Router;
use lattice_auth::atomic;
use lattice_auth::messages::detail_error;
use lattice_auth::pyjson::dumps_indent2;
use lattice_auth::response::json_response;
use lattice_auth::AuthState;
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};

use crate::admin::append_audit_event;
use crate::workspace::pyutil::{iso_seconds_for, now_iso};
use crate::workspace::reqbody::{self, field, Kind};
use crate::workspace::store::StoreError;
use crate::workspace::{WorkspaceService, WorkspaceState};

/// Mounted (method, path) pairs.
pub const MOUNTED: &[(&str, &str)] = &[
    ("GET", "/invitations"),
    ("POST", "/invitations"),
    ("POST", "/invitations/:token/accept"),
];

const CREATE: &[reqbody::Field] = &[
    field("email", Kind::OptionalStr),
    field("workspace_id", Kind::OptionalStr),
    field("role", Kind::StrDefault("member")),
    field("expires_hours", Kind::Int(168)),
];

const ROLES: &[&str] = &["owner", "admin", "member", "viewer"];

/// In-process invitation store over `invitations.json`.
pub struct InvitationStore {
    path: PathBuf,
    lock: Mutex<()>,
}

impl InvitationStore {
    /// Open (or create) the store at `<data_dir>/invitations.json`.
    pub fn open(data_dir: &Path) -> Self {
        Self {
            path: data_dir.join(lattice_core::db::tables::state_files::INVITATIONS),
            lock: Mutex::new(()),
        }
    }

    fn load(&self) -> Value {
        let Ok(text) = std::fs::read_to_string(&self.path) else {
            return json!({"version": 1, "invitations": []});
        };
        match serde_json::from_str::<Value>(&text) {
            Ok(Value::Object(mut map)) => {
                map.entry("invitations").or_insert_with(|| json!([]));
                Value::Object(map)
            }
            _ => json!({"version": 1, "invitations": []}),
        }
    }

    fn save(&self, mut data: Value) {
        data["version"] = json!(1);
        if let Ok(text) = dumps_indent2(&data) {
            atomic::write_text(&self.path, &text);
        }
    }

    /// `InvitationStore.list`.
    pub fn list(&self) -> Vec<Value> {
        let _guard = self.lock.lock().unwrap_or_else(|error| error.into_inner());
        let mut data = self.load();
        let mut changed = false;
        let mut records = Vec::new();
        if let Some(items) = data.get_mut("invitations").and_then(Value::as_array_mut) {
            for record in items.iter_mut() {
                if expire_if_needed(record) {
                    changed = true;
                }
                records.push(public(record));
            }
        }
        if changed {
            self.save(data);
        }
        records
    }

    /// `InvitationStore.create`.
    pub fn create(
        &self,
        email: Option<&str>,
        workspace_id: Option<&str>,
        role: &str,
        created_by: Option<&str>,
        expires_hours: i64,
    ) -> Value {
        let token = token_urlsafe(32);
        let now = now_iso();
        let hours = expires_hours.clamp(1, 24 * 30);
        let expires_at = add_hours(&now, hours);
        let email = email
            .map(str::trim)
            .map(str::to_lowercase)
            .filter(|value| !value.is_empty());
        let record = json!({
            "id": format!("invite-{}", token_hex(8)),
            "token_hash": sha256_hex(&token),
            "email": email,
            "workspace_id": workspace_id.filter(|value| !value.is_empty()),
            "role": role,
            "created_by": created_by,
            "created_at": now,
            "expires_at": expires_at,
            "status": "pending",
            "accepted_by": Value::Null,
            "accepted_at": Value::Null,
        });
        let _guard = self.lock.lock().unwrap_or_else(|error| error.into_inner());
        let mut data = self.load();
        if let Some(items) = data.get_mut("invitations").and_then(Value::as_array_mut) {
            items.push(record.clone());
        }
        self.save(data);
        let mut published = public(&record);
        published["token"] = json!(token);
        published
    }

    /// `InvitationStore.accept`.
    pub fn accept(
        &self,
        token: &str,
        accepted_by: &str,
        email: Option<&str>,
    ) -> Result<Value, StoreError> {
        let token_hash = sha256_hex(token);
        let _guard = self.lock.lock().unwrap_or_else(|error| error.into_inner());
        let mut data = self.load();
        let items = data
            .get_mut("invitations")
            .and_then(Value::as_array_mut)
            .cloned()
            .unwrap_or_default();
        let mut found = None;
        let mut updated = Vec::new();
        for mut record in items {
            if record.get("token_hash").and_then(Value::as_str) == Some(token_hash.as_str()) {
                if expire_if_needed(&mut record) {
                    updated.push(record);
                    data["invitations"] = Value::Array(updated);
                    self.save(data);
                    return Err(StoreError::Permission("invitation expired".into()));
                }
                let status = record
                    .get("status")
                    .and_then(Value::as_str)
                    .unwrap_or_default();
                if status != "pending" {
                    return Err(StoreError::Permission(format!("invitation is {status}")));
                }
                let invited = record
                    .get("email")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_lowercase();
                if !invited.is_empty()
                    && email.map(str::to_lowercase).as_deref() != Some(invited.as_str())
                {
                    return Err(StoreError::Permission(
                        "invitation was issued for a different email".into(),
                    ));
                }
                record["status"] = json!("accepted");
                record["accepted_by"] = json!(accepted_by);
                record["accepted_at"] = json!(now_iso());
                found = Some(public(&record));
            }
            updated.push(record);
        }
        let Some(accepted) = found else {
            return Err(StoreError::NotFound("invitation not found".into()));
        };
        data["invitations"] = Value::Array(updated);
        self.save(data);
        Ok(accepted)
    }
}

fn public(record: &Value) -> Value {
    let Value::Object(map) = record else {
        return json!({});
    };
    let mut out = Map::new();
    for (key, value) in map {
        if key != "token_hash" {
            out.insert(key.clone(), value.clone());
        }
    }
    Value::Object(out)
}

fn expire_if_needed(record: &mut Value) -> bool {
    if record.get("status").and_then(Value::as_str) != Some("pending") {
        return false;
    }
    let expires = record
        .get("expires_at")
        .and_then(Value::as_str)
        .unwrap_or_default();
    if !expires.is_empty() && expires >= now_iso().as_str() {
        return false;
    }
    record["status"] = json!("expired");
    record["expired_at"] = json!(now_iso());
    true
}

fn add_hours(stamp: &str, hours: i64) -> String {
    // `stamp` is naive-local `YYYY-MM-DDTHH:MM:SS`. Add hours via the civil clock.
    let Ok(parsed) = chrono_naive_secs(stamp) else {
        return stamp.to_string();
    };
    iso_seconds_for(parsed + hours * 3600)
}

fn chrono_naive_secs(stamp: &str) -> Result<i64, ()> {
    let (date, time) = stamp.split_once('T').ok_or(())?;
    let mut d = date.split('-');
    let year: i64 = d.next().ok_or(())?.parse().map_err(|_| ())?;
    let month: i64 = d.next().ok_or(())?.parse().map_err(|_| ())?;
    let day: i64 = d.next().ok_or(())?.parse().map_err(|_| ())?;
    let mut t = time.split(':');
    let hour: i64 = t.next().ok_or(())?.parse().map_err(|_| ())?;
    let minute: i64 = t.next().ok_or(())?.parse().map_err(|_| ())?;
    let second: i64 = t
        .next()
        .unwrap_or("0")
        .chars()
        .take(2)
        .collect::<String>()
        .parse()
        .map_err(|_| ())?;
    Ok(days_from_civil(year, month, day) * 86_400 + hour * 3600 + minute * 60 + second)
}

fn days_from_civil(year: i64, month: i64, day: i64) -> i64 {
    let y = if month <= 2 { year - 1 } else { year };
    let era = y.div_euclid(400);
    let yoe = y.rem_euclid(400);
    let mp = if month > 2 { month - 3 } else { month + 9 };
    let doy = (153 * mp + 2) / 5 + day - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146_097 + doe - 719_468
}

fn token_urlsafe(nbytes: usize) -> String {
    let mut bytes = vec![0u8; nbytes];
    let _ = getrandom::fill(&mut bytes);
    base64::Engine::encode(&base64::engine::general_purpose::URL_SAFE_NO_PAD, &bytes)
}

fn token_hex(nbytes: usize) -> String {
    let mut bytes = vec![0u8; nbytes];
    let _ = getrandom::fill(&mut bytes);
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

fn sha256_hex(text: &str) -> String {
    let digest = Sha256::digest(text.as_bytes());
    digest.iter().map(|byte| format!("{byte:02x}")).collect()
}

/// State the invitations router needs.
#[derive(Clone)]
pub struct InvitationsState {
    /// Process-wide auth.
    pub auth: Arc<AuthState>,
    /// Invitation store.
    pub store: Arc<InvitationStore>,
    /// Workspace service (membership on accept).
    pub workspace: WorkspaceService,
    /// Data directory for the audit log.
    pub data_dir: PathBuf,
}

impl InvitationsState {
    /// Build from a workspace state (shares the registry).
    pub fn from_workspace(workspace: &WorkspaceState) -> Self {
        Self {
            auth: Arc::clone(&workspace.auth),
            store: Arc::new(InvitationStore::open(&workspace.data_dir)),
            workspace: workspace.resolver(),
            data_dir: workspace.data_dir.clone(),
        }
    }
}

impl axum::extract::FromRef<InvitationsState> for Arc<AuthState> {
    fn from_ref(state: &InvitationsState) -> Self {
        Arc::clone(&state.auth)
    }
}

/// Router factory.
pub fn router(state: InvitationsState) -> Router {
    Router::new()
        .route(
            "/invitations",
            get(list_invitations).post(create_invitation),
        )
        .route("/invitations/:token/accept", post(accept_invitation))
        .with_state(state)
}

fn ok(value: &Value) -> Response {
    json_response(
        StatusCode::OK,
        &serde_json::to_string(value).unwrap_or_else(|_| "{}".into()),
        None,
    )
}

async fn list_invitations(State(state): State<InvitationsState>, headers: HeaderMap) -> Response {
    if let Err(refusal) = state.auth.require_admin(&headers) {
        return refusal;
    }
    ok(&json!({"invitations": state.store.list()}))
}

async fn create_invitation(
    State(state): State<InvitationsState>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let identity = match state.auth.require_admin(&headers) {
        Ok(identity) => identity,
        Err(refusal) => return refusal,
    };
    let parsed = match reqbody::parse(&body, CREATE) {
        Ok(parsed) => parsed,
        Err(refusal) => return refusal,
    };
    let actor_id = state
        .auth
        .users()
        .load()
        .user_id_for_email(if identity.email.is_empty() {
            None
        } else {
            Some(identity.email.as_str())
        });
    if let Some(workspace_id) = parsed.opt_str("workspace_id") {
        match crate::workspace::orgs::require_permission(
            match state
                .workspace
                .store()
                .load_state()
                .get("workspaces")
                .and_then(Value::as_object)
                .and_then(|map| map.get(workspace_id))
            {
                Some(workspace)
                    if workspace.get("type").and_then(Value::as_str) == Some("organization") =>
                {
                    workspace
                }
                Some(_) => {
                    return detail_error(
                        StatusCode::BAD_REQUEST,
                        "operation only valid for organization workspaces",
                    );
                }
                None => {
                    return detail_error(
                        StatusCode::NOT_FOUND,
                        &format!("Workspace not found: {workspace_id}"),
                    );
                }
            },
            actor_id.as_deref(),
            "manage_members",
        ) {
            Ok(()) => {}
            Err(StoreError::Permission(message)) => {
                return detail_error(StatusCode::FORBIDDEN, &message);
            }
            Err(StoreError::Value(message)) => {
                return detail_error(StatusCode::BAD_REQUEST, &message);
            }
            Err(StoreError::NotFound(_)) => {
                return detail_error(
                    StatusCode::NOT_FOUND,
                    &format!("Workspace not found: {workspace_id}"),
                );
            }
        }
    }
    if !ROLES.contains(&parsed.str("role")) {
        return detail_error(StatusCode::BAD_REQUEST, "unknown invitation role");
    }
    let invitation = state.store.create(
        parsed.opt_str("email"),
        parsed.opt_str("workspace_id"),
        parsed.str("role"),
        actor_id.as_deref(),
        parsed.int("expires_hours"),
    );
    let mut payload = Map::new();
    payload.insert("user_email".into(), json!(identity.email));
    payload.insert(
        "invitation_id".into(),
        invitation.get("id").cloned().unwrap_or(Value::Null),
    );
    payload.insert("workspace_id".into(), json!(parsed.opt_str("workspace_id")));
    payload.insert("role".into(), json!(parsed.str("role")));
    append_audit_event(
        &state
            .data_dir
            .join(lattice_core::db::tables::state_files::AUDIT_LOG),
        "invitation_created",
        payload,
    );
    ok(&json!({"invitation": invitation}))
}

async fn accept_invitation(
    State(state): State<InvitationsState>,
    headers: HeaderMap,
    AxumPath(token): AxumPath<String>,
) -> Response {
    let identity = match state.auth.require_user(&headers) {
        Ok(identity) => identity,
        Err(refusal) => return refusal,
    };
    let user_id = state
        .auth
        .users()
        .load()
        .user_id_for_email(if identity.email.is_empty() {
            None
        } else {
            Some(identity.email.as_str())
        });
    let Some(user_id) = user_id else {
        return detail_error(StatusCode::UNAUTHORIZED, "Authentication required");
    };
    let invitation = match state.store.accept(
        &token,
        &user_id,
        if identity.email.is_empty() {
            None
        } else {
            Some(identity.email.as_str())
        },
    ) {
        Ok(invitation) => invitation,
        Err(StoreError::NotFound(_)) => {
            return detail_error(StatusCode::NOT_FOUND, "Invitation not found");
        }
        Err(StoreError::Permission(message)) => {
            return detail_error(StatusCode::FORBIDDEN, &message);
        }
        Err(StoreError::Value(message)) => {
            return detail_error(StatusCode::BAD_REQUEST, &message);
        }
    };
    if let Some(workspace_id) = invitation
        .get("workspace_id")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
    {
        let role = invitation
            .get("role")
            .and_then(Value::as_str)
            .unwrap_or("member");
        let actor = invitation.get("created_by").and_then(Value::as_str);
        if let Err(error) = state
            .workspace
            .add_member(workspace_id, &user_id, role, actor)
        {
            return detail_error(StatusCode::CONFLICT, &error.to_string());
        }
    }
    let mut payload = Map::new();
    payload.insert("user_email".into(), json!(identity.email));
    payload.insert(
        "invitation_id".into(),
        invitation.get("id").cloned().unwrap_or(Value::Null),
    );
    payload.insert(
        "workspace_id".into(),
        invitation
            .get("workspace_id")
            .cloned()
            .unwrap_or(Value::Null),
    );
    append_audit_event(
        &state
            .data_dir
            .join(lattice_core::db::tables::state_files::AUDIT_LOG),
        "invitation_accepted",
        payload,
    );
    ok(&json!({"invitation": invitation}))
}
