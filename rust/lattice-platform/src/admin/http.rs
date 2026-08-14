//! Shared HTTP helpers for the R2 families.

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

pub fn language_from(headers: &HeaderMap) -> &'static str {
    messages::resolve_language(
        headers.get(LANGUAGE_HEADER).and_then(|v| v.to_str().ok()),
        headers
            .get(axum::http::header::ACCEPT_LANGUAGE)
            .and_then(|v| v.to_str().ok()),
    )
}

pub fn json_ok(body: &OrderedMap) -> Response {
    json_response(
        StatusCode::OK,
        &serde_json::to_string(body).unwrap_or_else(|_| "{}".into()),
        None,
    )
}

pub fn json_ok_value(body: &Value) -> Response {
    json_response(
        StatusCode::OK,
        &serde_json::to_string(body).unwrap_or_else(|_| "null".into()),
        None,
    )
}

pub fn json_status(status: StatusCode, body: &OrderedMap) -> Response {
    json_response(
        status,
        &serde_json::to_string(body).unwrap_or_else(|_| "{}".into()),
        None,
    )
}

pub fn message_error(status: u16, id: &str, lang: &str, args: &[(&str, &str)]) -> Response {
    let err = messages::http_error(status, id, lang, args);
    let body = serde_json::to_string(&err.body).unwrap_or_else(|_| "{\"detail\":\"\"}".into());
    json_response(
        StatusCode::from_u16(err.status).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR),
        &body,
        None,
    )
}

pub fn detail_status(status: StatusCode, detail: &str) -> Response {
    let mut body = OrderedMap::new();
    body.insert("detail", json!(detail));
    json_status(status, &body)
}

pub fn workspace_from_headers(headers: &HeaderMap) -> Option<String> {
    lattice_auth::workspace_scope_from_request(headers, None)
}
