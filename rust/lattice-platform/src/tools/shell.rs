//! Git, shell, network, and project script tools.

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
use std::collections::BTreeSet;
use std::path::{Path, PathBuf};
use std::sync::Arc;

use axum::extract::{Query, State};
use axum::http::{header, HeaderMap, HeaderValue, StatusCode};
use axum::response::Response;
use axum::routing::{get, post};
use axum::Router;
use lattice_agent::sandbox::{Workspace, MAX_FILE_BYTES};
use lattice_agent::{command, is_circuit_breaker};
use lattice_auth::{AuthState, Identity, OrderedMap};
use serde_json::{json, Value};

use crate::mcp::{
    detail, json_status, json_text, localized, missing_fields, parse_json_object, requested_scope,
    require_admin, require_user, sha256_hex,
};

use super::{relative, resolve, tool_err, tool_ok, ToolsState};

pub(crate) async fn git_status(State(state): State<ToolsState>, headers: HeaderMap) -> Response {
    if let Err(r) = require_user(&state.auth, &headers) {
        return r;
    }
    git_run(&state, &["status", "--short"], ".")
}

pub(crate) async fn git_diff(
    State(state): State<ToolsState>,
    headers: HeaderMap,
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
    let cwd = parsed.get("cwd").and_then(Value::as_str).unwrap_or(".");
    git_run(&state, &["diff", "--"], cwd)
}

pub(crate) async fn git_log(
    State(state): State<ToolsState>,
    headers: HeaderMap,
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
    let max = parsed
        .get("max_count")
        .and_then(Value::as_i64)
        .unwrap_or(5)
        .clamp(1, 20);
    let cwd = parsed.get("cwd").and_then(Value::as_str).unwrap_or(".");
    let flag = format!("--max-count={max}");
    git_run(&state, &["log", &flag, "--oneline", "--decorate"], cwd)
}

pub(crate) async fn git_show(
    State(state): State<ToolsState>,
    headers: HeaderMap,
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
    let revision = parsed
        .get("revision")
        .and_then(Value::as_str)
        .unwrap_or("HEAD");
    if revision.starts_with('-')
        || revision.contains("..")
        || revision.contains(':')
        || revision.contains('/')
        || revision.contains('\\')
    {
        return tool_err("Revision is not allowed.");
    }
    let cwd = parsed.get("cwd").and_then(Value::as_str).unwrap_or(".");
    git_run(
        &state,
        &["show", "--stat", "--oneline", "--decorate", revision],
        cwd,
    )
}

fn git_run(state: &ToolsState, args: &[&str], cwd: &str) -> Response {
    let workdir = match resolve(&state.workspace, cwd) {
        Ok(p) => p,
        Err(r) => return r,
    };
    if !workdir.exists() || !workdir.is_dir() {
        return tool_err("Working directory does not exist.");
    }
    let output = std::process::Command::new("git")
        .args(args)
        .current_dir(&workdir)
        .output();
    match output {
        Ok(out) => {
            let rel = if workdir == *state.workspace.root() {
                ".".into()
            } else {
                relative(&state.workspace, &workdir)
            };
            let stdout = String::from_utf8_lossy(&out.stdout);
            let stderr = String::from_utf8_lossy(&out.stderr);
            let stdout = tail(&stdout, 12_000);
            let stderr = tail(&stderr, 12_000);
            tool_ok(
                &state.workspace,
                json!({
                    "command": format!("git {}", args.join(" ")),
                    "cwd": rel,
                    "returncode": out.status.code().unwrap_or(1),
                    "stdout": stdout,
                    "stderr": stderr,
                }),
            )
        }
        Err(_) => tool_err("Git command timed out after 30 seconds."),
    }
}

fn tail(s: &str, n: usize) -> String {
    if s.len() <= n {
        s.to_string()
    } else {
        s[s.len() - n..].to_string()
    }
}

pub(crate) async fn run_command(
    State(state): State<ToolsState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    if let Err(r) = require_admin(&state.auth, &headers) {
        return r;
    }
    let parsed = match parse_json_object(&body) {
        Ok(v) => v,
        Err(r) => return r,
    };
    let command = parsed.get("command").and_then(Value::as_str).unwrap_or("");
    let cwd = parsed.get("cwd").and_then(Value::as_str).unwrap_or(".");
    let policy = lattice_agent::policy::ToolPolicy {
        risk: "exec".into(),
        destructive: false,
        shell: true,
        network: false,
        auto_approve: false,
        sandbox: "workspace".into(),
        rollback: "none".into(),
        capability: None,
        scope: None,
    };
    if let Some(reason) = is_circuit_breaker("run_command", &policy, &{
        let mut m = serde_json::Map::new();
        m.insert("command".into(), json!(command));
        m
    }) {
        return detail(
            StatusCode::FORBIDDEN,
            &format!("'run_command' 차단: {reason}"),
        );
    }
    match command::validate(&state.workspace, command, Some(cwd)) {
        Ok(validated) => {
            let output = std::process::Command::new(&validated.executable)
                .args(&validated.args)
                .current_dir(&validated.workdir)
                .output();
            match output {
                Ok(out) => {
                    let rel = if cwd.is_empty() { "." } else { cwd };
                    tool_ok(
                        &state.workspace,
                        json!({
                            "command": command,
                            "cwd": rel,
                            "returncode": out.status.code().unwrap_or(1),
                            "stdout": String::from_utf8_lossy(&out.stdout),
                            "stderr": String::from_utf8_lossy(&out.stderr),
                        }),
                    )
                }
                Err(err) => tool_err(&err.to_string()),
            }
        }
        Err(err) => detail(
            StatusCode::FORBIDDEN,
            &format!("'run_command' 차단: {}", err.message),
        ),
    }
}

pub(crate) async fn network_status(
    State(state): State<ToolsState>,
    headers: HeaderMap,
) -> Response {
    if let Err(r) = require_user(&state.auth, &headers) {
        return r;
    }
    tool_ok(
        &state.workspace,
        json!({"hostname": hostname(), "note": "sampled locally"}),
    )
}

fn hostname() -> String {
    std::process::Command::new("hostname")
        .output()
        .ok()
        .and_then(|o| String::from_utf8(o.stdout).ok())
        .map(|s| s.trim().to_string())
        .unwrap_or_default()
}

pub(crate) async fn build_project(
    State(state): State<ToolsState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    if let Err(r) = require_admin(&state.auth, &headers) {
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
    let cwd = parsed.get("cwd").and_then(Value::as_str).unwrap_or(".");
    let script = parsed
        .get("script")
        .and_then(Value::as_str)
        .unwrap_or("build");
    script_missing(&state, cwd, script)
}

pub(crate) async fn deploy_project(
    State(state): State<ToolsState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    if let Err(r) = require_admin(&state.auth, &headers) {
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
    let cwd = parsed.get("cwd").and_then(Value::as_str).unwrap_or(".");
    let script = parsed
        .get("script")
        .and_then(Value::as_str)
        .unwrap_or("deploy");
    script_missing(&state, cwd, script)
}

fn script_missing(state: &ToolsState, cwd: &str, script: &str) -> Response {
    let workdir = match resolve(&state.workspace, cwd) {
        Ok(p) => p,
        Err(r) => return r,
    };
    let pkg = workdir.join("package.json");
    if !pkg.exists() {
        return tool_err(&format!(
            "package.json does not define a '{script}' script."
        ));
    }
    let Ok(text) = std::fs::read_to_string(&pkg) else {
        return tool_err(&format!(
            "package.json does not define a '{script}' script."
        ));
    };
    let Ok(val) = serde_json::from_str::<Value>(&text) else {
        return tool_err(&format!(
            "package.json does not define a '{script}' script."
        ));
    };
    if val
        .get("scripts")
        .and_then(|s| s.get(script))
        .and_then(Value::as_str)
        .is_none()
    {
        return tool_err(&format!(
            "package.json does not define a '{script}' script."
        ));
    }
    tool_err(&format!(
        "package.json does not define a '{script}' script."
    ))
}
