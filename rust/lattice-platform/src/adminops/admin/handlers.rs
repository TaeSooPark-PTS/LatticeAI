//! Admin console route handlers.

use std::collections::BTreeMap;
use std::net::SocketAddr;

use axum::extract::{ConnectInfo, Path as AxumPath, Query, State};
use axum::http::{header, HeaderMap};
use axum::response::Response;
use lattice_auth::policy::capabilities_for_role;
use lattice_auth::{Identity, OrderedMap};
use serde_json::{json, Map, Value};

use super::audit::{
    append_audit_event, audit_log_path, build_admin_audit_report, build_sensitivity_report,
    load_audit_log,
};
use super::config::{
    load_chat_history, load_sso_config, load_vpc_config, matches_workspace_scope,
    public_sso_config, save_sso_config, save_vpc_config,
};
use super::enterprise::{poc_overview, siem_export_stub};
use super::http::{json_ok, json_ok_value, language_from, message_error, workspace_from_headers};
use super::internal::{
    external_origin, filter_audit_log, json_from_ordered, parse_object_body, parse_timestamp_unix,
    query_bound_error, unix_now,
};
use super::AdminState;

fn require_admin(
    state: &AdminState,
    headers: &HeaderMap,
) -> Result<(Identity, lattice_auth::Users), Response> {
    let identity = state.auth.require_admin(headers)?;
    let users = state.auth.users().load();
    Ok((identity, users))
}

fn require_user(state: &AdminState, headers: &HeaderMap) -> Result<Identity, Response> {
    state.auth.require_user(headers)
}

fn scoped_history(state: &AdminState, headers: &HeaderMap) -> Vec<Value> {
    let scope = workspace_from_headers(headers);
    load_chat_history(&state.data_dir)
        .into_iter()
        .filter(|item| matches_workspace_scope(item, scope.as_deref()))
        .collect()
}

fn scoped_audit(state: &AdminState, headers: &HeaderMap) -> Vec<Value> {
    let scope = workspace_from_headers(headers);
    load_audit_log(&audit_log_path(&state.data_dir))
        .into_iter()
        .filter(|item| matches_workspace_scope(item, scope.as_deref()))
        .collect()
}

pub(crate) async fn admin_summary(
    State(state): State<AdminState>,
    headers: HeaderMap,
) -> Result<Response, Response> {
    let (_, users) = require_admin(&state, &headers)?;
    let history = scoped_history(&state, &headers);
    let user_msgs = history
        .iter()
        .filter(|i| i.get("role").and_then(Value::as_str) == Some("user"))
        .count();
    let asst_msgs = history
        .iter()
        .filter(|i| i.get("role").and_then(Value::as_str) == Some("assistant"))
        .count();
    let admin_users = users
        .iter()
        .filter(|(email, _)| state.auth.get_user_role(email, &users) == "admin")
        .count();
    let active = users
        .iter()
        .filter(|(_, u)| {
            !u.get("disabled")
                .map(|v| v.as_bool().unwrap_or(!v.is_null()))
                .unwrap_or(false)
        })
        .count();
    let last = history
        .last()
        .and_then(|i| i.get("timestamp"))
        .cloned()
        .unwrap_or(Value::Null);
    let mut out = OrderedMap::new();
    out.insert("total_users", json!(users.len()));
    out.insert("active_users", json!(active));
    out.insert("admin_users", json!(admin_users));
    out.insert("total_messages", json!(history.len()));
    out.insert("user_messages", json!(user_msgs));
    out.insert("assistant_messages", json!(asst_msgs));
    out.insert("last_message_at", last);
    Ok(json_ok(&out))
}

pub(crate) async fn admin_health_summary(
    State(state): State<AdminState>,
    headers: HeaderMap,
) -> Result<Response, Response> {
    let (_, users) = require_admin(&state, &headers)?;
    let mut issues: Vec<Value> = Vec::new();
    let disabled = users
        .iter()
        .filter(|(_, u)| {
            u.get("disabled")
                .map(|v| v.as_bool().unwrap_or(!v.is_null()))
                .unwrap_or(false)
        })
        .count();
    if disabled > 0 {
        let mut issue = OrderedMap::new();
        issue.insert("area", json!("users"));
        issue.insert("severity", json!("warning"));
        issue.insert("message", json!(format!("{disabled} disabled user(s)")));
        issues.push(json_from_ordered(&issue));
    }
    let report = build_sensitivity_report(&scoped_history(&state, &headers));
    if let Some(high) = report
        .get("summary")
        .and_then(|s| s.get("severity_counts"))
        .and_then(|c| c.get("high"))
        .and_then(Value::as_u64)
    {
        if high > 0 {
            let mut issue = OrderedMap::new();
            issue.insert("area", json!("security"));
            issue.insert("severity", json!("high"));
            issue.insert("message", json!(format!("{high} high-risk event(s)")));
            issues.push(json_from_ordered(&issue));
        }
    }
    if state.enable_graph {
        match (state.graph_stats)() {
            Ok(stats) if stats.get("error").is_some() => {
                let mut issue = OrderedMap::new();
                issue.insert("area", json!("brain_ops"));
                issue.insert("severity", json!("warning"));
                issue.insert("message", json!("Knowledge graph unavailable"));
                issues.push(json_from_ordered(&issue));
            }
            Err(err) => {
                let mut issue = OrderedMap::new();
                issue.insert("area", json!("brain_ops"));
                issue.insert("severity", json!("warning"));
                let clipped: String = err.chars().take(160).collect();
                issue.insert("message", json!(clipped));
                issues.push(json_from_ordered(&issue));
            }
            _ => {}
        }
    }
    if let Some(hardening) = &state.hardening {
        let report = hardening();
        if report
            .get("startup")
            .and_then(|s| s.get("network_exposed"))
            .and_then(Value::as_bool)
            .unwrap_or(false)
        {
            let mut issue = OrderedMap::new();
            issue.insert("area", json!("runtime_trust"));
            issue.insert("severity", json!("warning"));
            issue.insert("message", json!("Server is network-exposed"));
            issues.push(json_from_ordered(&issue));
        }
    }
    let issue_count = issues.len();
    let mut out = OrderedMap::new();
    out.insert(
        "status",
        json!(if issue_count > 0 { "attention" } else { "ok" }),
    );
    out.insert("issue_count", json!(issue_count));
    out.insert("issues", json!(issues));
    Ok(json_ok(&out))
}

pub(crate) async fn admin_stats(
    State(state): State<AdminState>,
    headers: HeaderMap,
) -> Result<Response, Response> {
    require_admin(&state, &headers)?;
    let history = scoped_history(&state, &headers);
    let mut daily: BTreeMap<String, (u64, u64)> = BTreeMap::new();
    for item in &history {
        let ts = item.get("timestamp").and_then(Value::as_str).unwrap_or("");
        let day = if ts.is_empty() {
            "unknown".to_string()
        } else {
            ts.chars().take(10).collect()
        };
        match item.get("role").and_then(Value::as_str) {
            Some("user") => daily.entry(day).or_default().0 += 1,
            Some("assistant") => daily.entry(day).or_default().1 += 1,
            _ => {}
        }
    }
    let mut days: Vec<String> = daily.keys().cloned().collect();
    days.sort();
    let tail = if days.len() > 14 {
        days.split_off(days.len() - 14)
    } else {
        days
    };
    let rows: Vec<Value> = tail
        .into_iter()
        .map(|d| {
            let (u, a) = daily.get(&d).copied().unwrap_or((0, 0));
            let mut row = OrderedMap::new();
            row.insert("date", json!(d));
            row.insert("user", json!(u));
            row.insert("assistant", json!(a));
            json_from_ordered(&row)
        })
        .collect();
    let mut out = OrderedMap::new();
    out.insert("daily", json!(rows));
    Ok(json_ok(&out))
}

pub(crate) async fn admin_users(
    State(state): State<AdminState>,
    headers: HeaderMap,
) -> Result<Response, Response> {
    let (_, users) = require_admin(&state, &headers)?;
    let list: Vec<Value> = users
        .iter()
        .map(|(email, record)| json_from_ordered(&state.auth.public_user(email, record, &users)))
        .collect();
    Ok(json_ok_value(&Value::Array(list)))
}

pub(crate) async fn admin_sensitivity(
    State(state): State<AdminState>,
    headers: HeaderMap,
) -> Result<Response, Response> {
    require_admin(&state, &headers)?;
    Ok(json_ok(&build_sensitivity_report(&scoped_history(
        &state, &headers,
    ))))
}

pub(crate) async fn admin_audit(
    State(state): State<AdminState>,
    headers: HeaderMap,
    Query(query): Query<std::collections::HashMap<String, String>>,
) -> Result<Response, Response> {
    let (_, users) = require_admin(&state, &headers)?;
    if let Some(raw) = query.get("limit") {
        if let Ok(n) = raw.parse::<i64>() {
            if n < 1 {
                return Err(query_bound_error("greater_than_equal", "ge", 1, raw));
            }
            if n > 250 {
                return Err(query_bound_error("less_than_equal", "le", 250, raw));
            }
        }
    }
    let limit = query
        .get("limit")
        .and_then(|s| s.parse::<i64>().ok())
        .unwrap_or(50)
        .clamp(1, 250) as usize;
    let scoped = scoped_audit(&state, &headers);
    let filtered = filter_audit_log(
        &scoped,
        query.get("q").map(String::as_str),
        query.get("actor").map(String::as_str),
        query.get("action").map(String::as_str),
        query.get("severity").map(String::as_str),
        limit,
    );
    let graph = if state.enable_graph {
        match (state.graph_stats)() {
            Ok(v) => v,
            Err(e) => json!({"error": e}),
        }
    } else {
        json!({"disabled": true})
    };
    let mut report = build_admin_audit_report(&users, &state.auth, &filtered, Some(&graph));
    let mut filters = OrderedMap::new();
    filters.insert("q", json!(query.get("q").cloned().unwrap_or_default()));
    filters.insert(
        "actor",
        json!(query.get("actor").cloned().unwrap_or_default()),
    );
    filters.insert(
        "action",
        json!(query.get("action").cloned().unwrap_or_default()),
    );
    filters.insert(
        "severity",
        json!(query.get("severity").cloned().unwrap_or_default()),
    );
    filters.insert(
        "limit",
        json!(query
            .get("limit")
            .and_then(|s| s.parse::<i64>().ok())
            .unwrap_or(50)),
    );
    filters.insert("matched_events", json!(filtered.len()));
    filters.insert("scoped_events", json!(scoped.len()));
    report.insert("filters", json_from_ordered(&filters));
    report.insert("graph", graph);
    Ok(json_ok(&report))
}

pub(crate) async fn admin_roles(
    State(state): State<AdminState>,
    headers: HeaderMap,
) -> Result<Response, Response> {
    let (_, users) = require_admin(&state, &headers)?;
    let mut counts: BTreeMap<String, u64> = BTreeMap::new();
    for (email, _) in users.iter() {
        let role = state.auth.get_user_role(email, &users);
        *counts.entry(role).or_default() += 1;
    }
    for role in ["owner", "admin", "member", "user", "viewer"] {
        counts.entry(role.into()).or_insert(0);
    }
    let order = |r: &str| match r {
        "owner" => 0,
        "admin" => 1,
        "member" => 2,
        "user" => 3,
        "viewer" => 4,
        _ => 99,
    };
    let mut roles_list: Vec<String> = counts.keys().cloned().collect();
    roles_list.sort_by(|a, b| order(a).cmp(&order(b)).then(a.cmp(b)));
    let rows: Vec<Value> = roles_list
        .into_iter()
        .map(|role| {
            let mut row = OrderedMap::new();
            row.insert("role", json!(role.clone()));
            row.insert("members", json!(counts.get(&role).copied().unwrap_or(0)));
            row.insert("caps", json!(capabilities_for_role(&role)));
            json_from_ordered(&row)
        })
        .collect();
    let mut out = OrderedMap::new();
    out.insert("roles", json!(rows));
    Ok(json_ok(&out))
}

pub(crate) async fn admin_policies(
    State(state): State<AdminState>,
    headers: HeaderMap,
) -> Result<Response, Response> {
    require_admin(&state, &headers)?;
    let gate = if state.invite_gate_enabled {
        "Signed access gate"
    } else {
        "Disabled"
    };
    let policies = [
        (
            "local_file_access",
            "Local file access",
            "Approval-token gated (per path/user/action)",
            true,
        ),
        (
            "package_install",
            "Package install",
            "Admin-only with audit trail",
            true,
        ),
        (
            "data_residency",
            "Data residency",
            "Single-tenant local storage (~/.ltcai)",
            true,
        ),
        (
            "model_egress",
            "Model egress",
            "Local-only by default (no external inference in local mode)",
            true,
        ),
        (
            "invite_gate",
            "Invite gate",
            gate,
            state.invite_gate_enabled,
        ),
        (
            "log_retention",
            "Log retention",
            "90 day local audit window with manual export before pruning",
            true,
        ),
    ];
    let rows: Vec<Value> = policies
        .into_iter()
        .map(|(id, label, value, enforced)| {
            let mut row = OrderedMap::new();
            row.insert("id", json!(id));
            row.insert("label", json!(label));
            row.insert("value", json!(value));
            row.insert("enforced", json!(enforced));
            json_from_ordered(&row)
        })
        .collect();
    let mut out = OrderedMap::new();
    out.insert("policies", json!(rows));
    Ok(json_ok(&out))
}

pub(crate) async fn admin_log_retention(
    State(state): State<AdminState>,
    headers: HeaderMap,
) -> Result<Response, Response> {
    require_admin(&state, &headers)?;
    let events = scoped_audit(&state, &headers);
    let cutoff = unix_now().saturating_sub(90 * 24 * 3600);
    let mut retained = 0u64;
    let mut prune = 0u64;
    for event in &events {
        let ts = event
            .get("timestamp")
            .or_else(|| event.get("ts"))
            .and_then(Value::as_str);
        if let Some(parsed) = ts.and_then(parse_timestamp_unix) {
            if parsed < cutoff {
                prune += 1;
            } else {
                retained += 1;
            }
        } else {
            retained += 1;
        }
    }
    let mut out = OrderedMap::new();
    out.insert("mode", json!("local-first"));
    out.insert("retention_days", json!(90));
    out.insert("total_events", json!(events.len()));
    out.insert("retained_events", json!(retained));
    out.insert("prune_candidates", json!(prune));
    out.insert("export_before_prune", json!(true));
    out.insert("editable", json!(false));
    out.insert(
        "reason",
        json!("Retention is reported in Community mode; destructive pruning requires an explicit export workflow."),
    );
    Ok(json_ok(&out))
}

pub(crate) async fn admin_product_hardening(
    State(state): State<AdminState>,
    headers: HeaderMap,
) -> Result<Response, Response> {
    require_admin(&state, &headers)?;
    match &state.hardening {
        Some(h) => Ok(json_ok(&h())),
        None => {
            let mut out = OrderedMap::new();
            out.insert("available", json!(false));
            out.insert(
                "reason",
                json!("Product hardening status provider is not configured."),
            );
            Ok(json_ok(&out))
        }
    }
}

pub(crate) async fn vpc_status(
    State(state): State<AdminState>,
    headers: HeaderMap,
) -> Result<Response, Response> {
    require_user(&state, &headers)?;
    Ok(json_ok(&load_vpc_config(&state.data_dir)))
}

pub(crate) async fn admin_update_vpc(
    State(state): State<AdminState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Result<Response, Response> {
    require_admin(&state, &headers)?;
    let parsed = parse_object_body(&body)?;
    let mut cfg = load_vpc_config(&state.data_dir);
    for (key, value) in parsed {
        if key == "private_subnets" {
            if let Some(list) = value.as_array() {
                let cleaned: Vec<Value> = list
                    .iter()
                    .filter_map(|s| s.as_str())
                    .map(str::trim)
                    .filter(|s| !s.is_empty())
                    .map(|s| json!(s))
                    .collect();
                cfg.insert(key, json!(cleaned));
                continue;
            }
        }
        cfg.insert(key, value);
    }
    save_vpc_config(&state.data_dir, cfg.clone());
    Ok(json_ok(&load_vpc_config(&state.data_dir)))
}

pub(crate) async fn admin_update_user(
    State(state): State<AdminState>,
    AxumPath(email): AxumPath<String>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Result<Response, Response> {
    let lang = language_from(&headers);
    let (admin, users) = require_admin(&state, &headers)?;
    let email = email.trim_start_matches('/').to_string();
    let parsed = parse_object_body(&body)?;
    if users.get(&email).is_none() {
        return Err(message_error(404, "auth.user_not_found", lang, &[]));
    }
    let before = state
        .auth
        .public_user(&email, users.get(&email).unwrap(), &users);
    let mut record = users.get(&email).unwrap().clone();
    if let Some(role) = parsed.get("role") {
        if !role.is_null() {
            let role = role.as_str().unwrap_or("");
            if role != "admin" && role != "user" {
                return Err(message_error(400, "admin.invalid_role", lang, &[]));
            }
            record.insert("role".into(), json!(role));
        }
    }
    if let Some(disabled) = parsed.get("disabled") {
        if !disabled.is_null() {
            let flag = disabled.as_bool().unwrap_or(false);
            if email == admin.email && flag {
                return Err(message_error(400, "admin.cannot_disable_self", lang, &[]));
            }
            record.insert("disabled".into(), json!(flag));
        }
    }
    let mut next = users;
    next.insert(email.clone(), record);
    state.auth.users().save(&next);
    let after = state
        .auth
        .public_user(&email, next.get(&email).unwrap(), &next);
    let mut payload = Map::new();
    payload.insert("user_email".into(), json!(admin.email));
    payload.insert("target_email".into(), json!(email));
    payload.insert("before".into(), json_from_ordered(&before));
    payload.insert("after".into(), json_from_ordered(&after));
    append_audit_event(&audit_log_path(&state.data_dir), "user_update", payload);
    Ok(json_ok(&after))
}

pub(crate) async fn admin_delete_user(
    State(state): State<AdminState>,
    AxumPath(email): AxumPath<String>,
    headers: HeaderMap,
) -> Result<Response, Response> {
    let lang = language_from(&headers);
    let (admin, users) = require_admin(&state, &headers)?;
    let email = email.trim_start_matches('/').to_string();
    if email == admin.email {
        return Err(message_error(400, "admin.cannot_delete_self", lang, &[]));
    }
    let Some(record) = users.get(&email).cloned() else {
        return Err(message_error(404, "auth.user_not_found", lang, &[]));
    };
    let deleted = state.auth.public_user(&email, &record, &users);
    let mut payload = Map::new();
    payload.insert("user_email".into(), json!(admin.email));
    payload.insert("target_email".into(), json!(email));
    payload.insert("deleted_user".into(), json_from_ordered(&deleted));
    append_audit_event(&audit_log_path(&state.data_dir), "user_delete", payload);
    let mut next = lattice_auth::Users::new();
    for (e, rec) in users.iter() {
        if e != email {
            next.insert(e.to_string(), rec.clone());
        }
    }
    state.auth.users().save(&next);
    let mut out = OrderedMap::new();
    out.insert("status", json!("ok"));
    out.insert("deleted", json_from_ordered(&deleted));
    Ok(json_ok(&out))
}

pub(crate) async fn admin_invite_link(
    State(state): State<AdminState>,
    headers: HeaderMap,
    request: axum::extract::Request,
) -> Result<Response, Response> {
    require_admin(&state, &headers)?;
    let peer = request
        .extensions()
        .get::<ConnectInfo<SocketAddr>>()
        .map(|c| c.0.ip().to_string());
    let host = headers
        .get(header::HOST)
        .and_then(|v| v.to_str().ok())
        .unwrap_or("");
    let forwarded_host = headers
        .get("x-forwarded-host")
        .and_then(|v| v.to_str().ok());
    let forwarded_proto = headers
        .get("x-forwarded-proto")
        .and_then(|v| v.to_str().ok());
    let origin = external_origin(
        host,
        "http",
        forwarded_host,
        forwarded_proto,
        peer.as_deref(),
    )
    .unwrap_or_else(|| format!("http://localhost:{}", state.default_port));
    let url = if state.invite_gate_enabled {
        format!("{origin}/?code={}", state.invite_code)
    } else {
        format!("{origin}/")
    };
    let mut out = OrderedMap::new();
    out.insert("invite_url", json!(url));
    out.insert("invite_code", json!(state.invite_code));
    out.insert("gate_enabled", json!(state.invite_gate_enabled));
    Ok(json_ok(&out))
}

pub(crate) async fn admin_sso(
    State(state): State<AdminState>,
    headers: HeaderMap,
) -> Result<Response, Response> {
    require_admin(&state, &headers)?;
    let cfg = load_sso_config(&state.data_dir, state.default_port);
    Ok(json_ok(&public_sso_config(&cfg, state.default_port)))
}

pub(crate) async fn admin_update_sso(
    State(state): State<AdminState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Result<Response, Response> {
    let (admin, _) = require_admin(&state, &headers)?;
    let update = parse_object_body(&body)?;
    let saved = save_sso_config(&state.data_dir, state.default_port, update);
    let mut payload = Map::new();
    payload.insert("user_email".into(), json!(admin.email));
    payload.insert(
        "provider_name".into(),
        saved.get("provider_name").cloned().unwrap_or(Value::Null),
    );
    payload.insert(
        "discovery_url".into(),
        saved.get("discovery_url").cloned().unwrap_or(Value::Null),
    );
    payload.insert(
        "enabled".into(),
        json!(saved
            .get("enabled")
            .and_then(Value::as_bool)
            .unwrap_or(false)),
    );
    append_audit_event(
        &audit_log_path(&state.data_dir),
        "sso_config_update",
        payload,
    );
    Ok(json_ok(&public_sso_config(&saved, state.default_port)))
}

pub(crate) async fn admin_enterprise(
    State(state): State<AdminState>,
    headers: HeaderMap,
) -> Result<Response, Response> {
    require_admin(&state, &headers)?;
    Ok(json_ok(&poc_overview()))
}

pub(crate) async fn admin_enterprise_siem(
    State(state): State<AdminState>,
    headers: HeaderMap,
) -> Result<Response, Response> {
    require_admin(&state, &headers)?;
    Ok(json_ok(&siem_export_stub()))
}
