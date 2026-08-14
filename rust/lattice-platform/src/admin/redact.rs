//! Secret redaction.

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
    clippy::useless_format,
    clippy::collapsible_str_replace,
    clippy::manual_repeat_n,
    clippy::module_inception
)]
use std::collections::BTreeMap;
use std::net::SocketAddr;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};

use axum::extract::{ConnectInfo, Path as AxumPath, Query, State};
use axum::http::{header, HeaderMap, StatusCode};
use axum::response::Response;
use axum::routing::{get, patch};
use axum::Router;
use fancy_regex::Regex;
use lattice_auth::policy::capabilities_for_role;
use lattice_auth::response::json_response;
use lattice_auth::{AuthState, Identity, OrderedMap};
use lattice_core::db::tables::state_files;
use lattice_core::messages::{self, LANGUAGE_HEADER};
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};

/// `core.security.redact_secret_text`.
pub fn redact_secret_text(text: &str) -> String {
    if text.is_empty() {
        return String::new();
    }
    let mut redacted = text.to_string();
    if let Ok(re) = Regex::new(r"\bbot(\d{5,20}):[A-Za-z0-9_-]{8,}\b") {
        redacted = re.replace_all(&redacted, "bot${1}:REDACTED").into_owned();
    }
    if let Ok(re) = Regex::new(r"(?<![A-Za-z0-9_:-])(\d{5,20}):[A-Za-z0-9_-]{8,}(?![A-Za-z0-9_-])")
    {
        redacted = re.replace_all(&redacted, "bot${1}:REDACTED").into_owned();
    }
    let patterns = [
        r#"(?i)\b(api[_ -]?key|secret|token|password|passwd|authorization|bearer|client[_ -]?secret|webhook|dsn)\s*[:=]\s*['\"]?([^\s'\",;]{8,})['\"]?"#,
        r"\b(sk-[A-Za-z0-9_\-]{16,})\b",
        r"\b(xai-[A-Za-z0-9_\-]{16,})\b",
        r"\b(gsk_[A-Za-z0-9_\-]{16,})\b",
        r"\b(ghp_[A-Za-z0-9_]{30,})\b",
        r"\b(xox[baprs]-[A-Za-z0-9-]{10,})\b",
        r"\b(AKIA[0-9A-Z]{16})\b",
        r"(?i)\b(postgres(?:ql)?://[^@\s]+:[^@\s]+@[^\s]+)",
        r"-----BEGIN [A-Z ]+PRIVATE KEY-----[\s\S]+?-----END [A-Z ]+PRIVATE KEY-----",
    ];
    for pat in patterns {
        let Ok(re) = Regex::new(pat) else {
            continue;
        };
        redacted = re
            .replace_all(&redacted, |caps: &fancy_regex::Captures| {
                if caps.get(2).is_some() {
                    format!(
                        "{}=[REDACTED_SECRET]",
                        caps.get(1).map(|m| m.as_str()).unwrap_or("")
                    )
                } else {
                    "[REDACTED_SECRET]".into()
                }
            })
            .into_owned();
    }
    redacted
}

/// Recursively redact string leaves. Keys named like secrets become
/// `[REDACTED_SECRET]` (Python `redact_secrets`).
pub fn redact_secrets(value: &Value) -> Value {
    match value {
        Value::String(text) => json!(redact_secret_text(text)),
        Value::Object(map) => {
            let mut out = Map::new();
            for (key, item) in map {
                if is_secret_key(key) {
                    out.insert(key.clone(), json!("[REDACTED_SECRET]"));
                } else {
                    out.insert(key.clone(), redact_secrets(item));
                }
            }
            Value::Object(out)
        }
        Value::Array(items) => Value::Array(items.iter().map(redact_secrets).collect()),
        other => other.clone(),
    }
}

/// Walk values only — keys stay. Used by the security dashboard (not
/// `redact_secrets`, which blanks secret-*named* fields).
pub fn redact_structure(value: &Value) -> Value {
    match value {
        Value::String(text) => json!(redact_secret_text(text)),
        Value::Object(map) => {
            let mut out = Map::new();
            for (key, item) in map {
                out.insert(key.clone(), redact_structure(item));
            }
            Value::Object(out)
        }
        Value::Array(items) => Value::Array(items.iter().map(redact_structure).collect()),
        other => other.clone(),
    }
}

fn is_secret_key(key: &str) -> bool {
    let lowered = key.to_lowercase().replace('-', "_");
    [
        "password",
        "passwd",
        "secret",
        "token",
        "api_key",
        "apikey",
        "authorization",
        "bearer",
        "private_key",
        "client_secret",
        "webhook",
        "dsn",
        "credential",
    ]
    .iter()
    .any(|hint| lowered.contains(hint))
}
