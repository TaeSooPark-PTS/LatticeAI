//! Chat history, VPC, and SSO config helpers.

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

use super::internal::now_iso;

const DEFAULT_WORKSPACE_ID: &str = "personal";
const SSO_CALLBACK_PATH: &str = "/auth/sso/callback";

/// Read `chat_history.json` (array, or `{messages|items|history: [...]}`).
pub fn load_chat_history(data_dir: &Path) -> Vec<Value> {
    let path = data_dir.join(state_files::CHAT_HISTORY);
    let Ok(text) = std::fs::read_to_string(&path) else {
        return Vec::new();
    };
    match serde_json::from_str::<Value>(&text) {
        Ok(Value::Array(items)) => items,
        Ok(Value::Object(map)) => ["messages", "items", "history"]
            .iter()
            .find_map(|k| map.get(*k).and_then(Value::as_array).cloned())
            .unwrap_or_default(),
        _ => Vec::new(),
    }
}

pub fn matches_workspace_scope(item: &Value, workspace_id: Option<&str>) -> bool {
    let Some(scope) = workspace_id.filter(|s| !s.is_empty()) else {
        return true;
    };
    let item_scope = item
        .get("workspace_id")
        .and_then(Value::as_str)
        .unwrap_or("");
    if item_scope.is_empty() && scope == DEFAULT_WORKSPACE_ID {
        return true;
    }
    item_scope == scope
}

pub fn default_vpc_config() -> OrderedMap {
    let mut cfg = OrderedMap::new();
    cfg.insert("provider", json!("AWS"));
    cfg.insert("region", json!("ap-northeast-2"));
    cfg.insert("cidr_block", json!("10.42.0.0/16"));
    cfg.insert("private_subnets", json!(["10.42.10.0/24", "10.42.20.0/24"]));
    cfg.insert("endpoint", json!("ltcai-private.local"));
    cfg.insert("vpn_status", json!("standby"));
    cfg.insert("peering_status", json!("not_configured"));
    cfg.insert(
        "notes",
        json!("로컬 MLX 브릿지를 프라이빗 서브넷 또는 VPN 뒤에서 운영할 때 쓰는 네트워크 프로필입니다."),
    );
    cfg.insert("updated_at", Value::Null);
    cfg
}

pub fn load_vpc_config(data_dir: &Path) -> OrderedMap {
    let path = data_dir.join(state_files::VPC_CONFIG);
    let mut cfg = default_vpc_config();
    let Ok(text) = std::fs::read_to_string(&path) else {
        return cfg;
    };
    let Ok(stored) = serde_json::from_str::<Map<String, Value>>(&text) else {
        return cfg;
    };
    for (key, value) in stored {
        cfg.insert(key, value);
    }
    cfg
}

pub fn save_vpc_config(data_dir: &Path, mut cfg: OrderedMap) {
    cfg.insert("updated_at", json!(now_iso()));
    let path = data_dir.join(state_files::VPC_CONFIG);
    if let Ok(text) = lattice_auth::pyjson::dumps_indent2(&cfg) {
        lattice_auth::atomic::write_text(&path, &text);
    }
}

pub fn default_sso_redirect(port: u16) -> String {
    format!("http://localhost:{port}{SSO_CALLBACK_PATH}")
}

pub fn load_sso_config(data_dir: &Path, port: u16) -> OrderedMap {
    let mut cfg = OrderedMap::new();
    cfg.insert("enabled", json!(false));
    cfg.insert("provider_name", json!("SSO"));
    cfg.insert("discovery_url", json!(""));
    cfg.insert("client_id", json!(""));
    cfg.insert("client_secret", json!(""));
    cfg.insert("redirect_uri", json!(default_sso_redirect(port)));
    cfg.insert("scopes", json!("openid email profile"));

    let path = data_dir.join(state_files::SSO_CONFIG);
    if let Ok(text) = std::fs::read_to_string(&path) {
        if let Ok(stored) = serde_json::from_str::<Map<String, Value>>(&text) {
            for (key, value) in stored {
                if !value.is_null() {
                    cfg.insert(key, value);
                }
            }
        }
    }
    let provider = cfg
        .get("provider_name")
        .and_then(Value::as_str)
        .filter(|s| !s.is_empty())
        .unwrap_or("SSO")
        .to_string();
    cfg.insert("provider_name", json!(provider));
    for key in ["discovery_url", "client_id", "client_secret"] {
        let text = cfg
            .get(key)
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        cfg.insert(key, json!(text));
    }
    let redirect = cfg
        .get("redirect_uri")
        .and_then(Value::as_str)
        .filter(|s| !s.is_empty())
        .map(str::to_string)
        .unwrap_or_else(|| default_sso_redirect(port));
    cfg.insert("redirect_uri", json!(redirect));
    let scopes = cfg
        .get("scopes")
        .and_then(Value::as_str)
        .filter(|s| !s.is_empty())
        .unwrap_or("openid email profile")
        .to_string();
    cfg.insert("scopes", json!(scopes));
    let enabled = cfg.get("enabled").and_then(Value::as_bool).unwrap_or(false)
        && !cfg
            .get("discovery_url")
            .and_then(Value::as_str)
            .unwrap_or("")
            .is_empty()
        && !cfg
            .get("client_id")
            .and_then(Value::as_str)
            .unwrap_or("")
            .is_empty()
        && !cfg
            .get("client_secret")
            .and_then(Value::as_str)
            .unwrap_or("")
            .is_empty();
    cfg.insert("enabled", json!(enabled));
    cfg
}

pub fn public_sso_config(cfg: &OrderedMap, port: u16) -> OrderedMap {
    let mut out = OrderedMap::new();
    out.insert(
        "enabled",
        json!(cfg.get("enabled").and_then(Value::as_bool).unwrap_or(false)),
    );
    out.insert(
        "provider_name",
        json!(cfg
            .get("provider_name")
            .and_then(Value::as_str)
            .unwrap_or("")),
    );
    out.insert(
        "discovery_url",
        json!(cfg
            .get("discovery_url")
            .and_then(Value::as_str)
            .unwrap_or("")),
    );
    out.insert(
        "client_id",
        json!(cfg.get("client_id").and_then(Value::as_str).unwrap_or("")),
    );
    out.insert(
        "redirect_uri",
        json!(cfg
            .get("redirect_uri")
            .and_then(Value::as_str)
            .filter(|s| !s.is_empty())
            .map(str::to_string)
            .unwrap_or_else(|| default_sso_redirect(port))),
    );
    out.insert(
        "scopes",
        json!(cfg
            .get("scopes")
            .and_then(Value::as_str)
            .filter(|s| !s.is_empty())
            .unwrap_or("openid email profile")),
    );
    out.insert(
        "secret_configured",
        json!(!cfg
            .get("client_secret")
            .and_then(Value::as_str)
            .unwrap_or("")
            .is_empty()),
    );
    out
}

pub fn save_sso_config(data_dir: &Path, port: u16, update: Map<String, Value>) -> OrderedMap {
    let mut current = load_sso_config(data_dir, port);
    let mut update = update;
    if update.get("client_secret").and_then(Value::as_str) == Some("") {
        update.remove("client_secret");
    }
    for (key, value) in update {
        if !value.is_null() {
            current.insert(key, value);
        }
    }
    let enabled = current
        .get("enabled")
        .and_then(Value::as_bool)
        .unwrap_or(false)
        && !current
            .get("discovery_url")
            .and_then(Value::as_str)
            .unwrap_or("")
            .is_empty()
        && !current
            .get("client_id")
            .and_then(Value::as_str)
            .unwrap_or("")
            .is_empty()
        && !current
            .get("client_secret")
            .and_then(Value::as_str)
            .unwrap_or("")
            .is_empty();
    current.insert("enabled", json!(enabled));
    let path = data_dir.join(state_files::SSO_CONFIG);
    if let Ok(text) = lattice_auth::pyjson::dumps_indent2(&current) {
        lattice_auth::atomic::write_text(&path, &text);
    }
    current
}
