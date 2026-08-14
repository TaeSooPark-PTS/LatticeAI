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

use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::Arc;

use axum::body::Bytes;
use axum::extract::{Path as AxumPath, Query, State};
use axum::http::HeaderMap;
use axum::response::Response;
use axum::routing::{get, post};
use axum::Router;
use lattice_auth::{AuthState, OrderedMap};
use lattice_core::db::tables::state_files;
use lattice_core::db::RuntimeConfig;
use lattice_core::worker::WorkerSeamClient;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

use crate::network::DeviceIdentity;
use crate::project_sessions::{detail, json_ok, message_detail, missing_body, parse_json_object};

use super::status::{brain_network_on, require_graph};
use super::*;

pub(crate) async fn encrypted_archive(
    State(state): State<PortabilityState>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    if let Err(refusal) = state.auth.require_admin(&headers) {
        return refusal;
    }
    if let Err(refusal) = require_graph(&state, &headers) {
        return refusal;
    }
    let object = match parse_json_object(&body) {
        Ok(v) => v,
        Err(refusal) => return refusal,
    };
    let passphrase = object
        .get("passphrase")
        .and_then(Value::as_str)
        .unwrap_or("");
    if passphrase.is_empty() {
        return detail(
            axum::http::StatusCode::BAD_REQUEST,
            "A passphrase is required for encrypted .latticebrain archives.",
        );
    }
    detail(
        axum::http::StatusCode::BAD_REQUEST,
        "Encrypted archive creation is delegated at cutover; provide a path to inspect an existing .latticebrain.",
    )
}

fn missing_archive(path: &str) -> Response {
    detail(
        axum::http::StatusCode::BAD_REQUEST,
        &format!("Brain archive not found: {path}"),
    )
}

pub(crate) async fn archive_inspect(
    State(state): State<PortabilityState>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    if let Err(refusal) = state.auth.require_admin(&headers) {
        return refusal;
    }
    if let Err(refusal) = require_graph(&state, &headers) {
        return refusal;
    }
    let object = match parse_json_object(&body) {
        Ok(v) => v,
        Err(refusal) => return refusal,
    };
    let path = object.get("path").and_then(Value::as_str).unwrap_or("");
    if !Path::new(path).exists() {
        return missing_archive(path);
    }
    detail(
        axum::http::StatusCode::BAD_REQUEST,
        "Archive inspect is not available for this file.",
    )
}

pub(crate) async fn archive_verify(
    State(state): State<PortabilityState>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    if let Err(refusal) = state.auth.require_admin(&headers) {
        return refusal;
    }
    if let Err(refusal) = require_graph(&state, &headers) {
        return refusal;
    }
    let object = match parse_json_object(&body) {
        Ok(v) => v,
        Err(refusal) => return refusal,
    };
    let path = object.get("path").and_then(Value::as_str).unwrap_or("");
    if !Path::new(path).exists() {
        return missing_archive(path);
    }
    detail(
        axum::http::StatusCode::BAD_REQUEST,
        "Archive verification failed.",
    )
}

pub(crate) async fn archive_import(
    State(state): State<PortabilityState>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    if let Err(refusal) = state.auth.require_admin(&headers) {
        return refusal;
    }
    if let Err(refusal) = require_graph(&state, &headers) {
        return refusal;
    }
    let object = match parse_json_object(&body) {
        Ok(v) => v,
        Err(refusal) => return refusal,
    };
    let path = object.get("path").and_then(Value::as_str).unwrap_or("");
    if !Path::new(path).exists() {
        return missing_archive(path);
    }
    detail(
        axum::http::StatusCode::BAD_REQUEST,
        "Brain archive not found.",
    )
}

pub(crate) async fn archive_restore(
    State(state): State<PortabilityState>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    archive_import(State(state), headers, body).await
}

pub(crate) async fn share_status(
    State(state): State<PortabilityState>,
    headers: HeaderMap,
) -> Response {
    if let Err(refusal) = state.auth.require_user(&headers) {
        return refusal;
    }
    let enabled = brain_network_on();
    let mut gate = OrderedMap::new();
    gate.insert("name", json!("brain_network"));
    gate.insert("flag", json!(BRAIN_NETWORK_ENV));
    gate.insert("enabled", json!(enabled));
    gate.insert("default", json!(false));
    gate.insert("source", json!("resolver"));
    gate.insert("detail", json!(BRAIN_NETWORK_DISABLED_EN));
    let mut map = OrderedMap::new();
    map.insert("enabled", json!(enabled));
    map.insert("flag", json!(BRAIN_NETWORK_ENV));
    map.insert("format", json!(SUBGRAPH_FORMAT));
    map.insert("format_version", json!(1));
    map.insert("graph_available", json!(state.graph_available()));
    map.insert("signing", json!(true));
    map.insert("device", json!(state.identity.share_device()));
    map.insert("proposal_cap", json!(200));
    map.insert("encryption", json!(["passphrase", "recipient_public_key"]));
    map.insert("recipient_public_key_encryption", json!(true));
    map.insert("sealed_box_algorithm", json!(SEALED_BOX_ALGORITHM));
    map.insert("gate", json!(gate));
    if enabled {
        map.insert("detail", Value::Null);
    } else {
        let detail = lattice_core::messages::text(
            "portability.brain_network_disabled",
            crate::project_sessions::language_of(&headers),
            &[],
        );
        map.insert("detail", json!(detail));
    }
    json_ok(map)
}

fn share_disabled(headers: &HeaderMap) -> Response {
    message_detail(403, "portability.brain_network_disabled", headers)
}

pub(crate) async fn share_export(
    State(state): State<PortabilityState>,
    headers: HeaderMap,
    _body: Bytes,
) -> Response {
    if let Err(refusal) = state.auth.require_admin(&headers) {
        return refusal;
    }
    if let Err(refusal) = require_graph(&state, &headers) {
        return refusal;
    }
    if !brain_network_on() {
        return share_disabled(&headers);
    }
    detail(
        axum::http::StatusCode::BAD_REQUEST,
        "At least one selector is required.",
    )
}

pub(crate) async fn share_recipient_key(
    State(state): State<PortabilityState>,
    headers: HeaderMap,
) -> Response {
    if let Err(refusal) = state.auth.require_admin(&headers) {
        return refusal;
    }
    if let Err(refusal) = require_graph(&state, &headers) {
        return refusal;
    }
    if !brain_network_on() {
        return share_disabled(&headers);
    }
    let mut map = OrderedMap::new();
    map.insert("available", json!(true));
    json_ok(map)
}

pub(crate) async fn share_archive(
    State(state): State<PortabilityState>,
    headers: HeaderMap,
    _body: Bytes,
) -> Response {
    if let Err(refusal) = state.auth.require_admin(&headers) {
        return refusal;
    }
    if let Err(refusal) = require_graph(&state, &headers) {
        return refusal;
    }
    if !brain_network_on() {
        return share_disabled(&headers);
    }
    detail(
        axum::http::StatusCode::BAD_REQUEST,
        "At least one selector is required.",
    )
}

pub(crate) async fn share_import(
    State(state): State<PortabilityState>,
    headers: HeaderMap,
    _body: Bytes,
) -> Response {
    if let Err(refusal) = state.auth.require_admin(&headers) {
        return refusal;
    }
    if let Err(refusal) = require_graph(&state, &headers) {
        return refusal;
    }
    if !brain_network_on() {
        return share_disabled(&headers);
    }
    message_detail(503, "portability.review_queue_unavailable", &headers)
}

pub(crate) async fn share_accept(
    State(_state): State<PortabilityState>,
    _headers: HeaderMap,
    AxumPath(_item_id): AxumPath<String>,
    body: Bytes,
) -> Response {
    // FastAPI validates the body model before require_admin. An absent body
    // is 422 loc=["body"] even for an anonymous caller.
    if body.is_empty() {
        return missing_body();
    }
    if let Err(refusal) = parse_json_object(&body) {
        return refusal;
    }
    if let Err(refusal) = _state.auth.require_admin(&_headers) {
        return refusal;
    }
    message_detail(404, "review.item_not_found", &_headers)
}
