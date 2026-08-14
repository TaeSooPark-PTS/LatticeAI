#![allow(dead_code, unused_imports, unused_variables)]
#![allow(clippy::all)]
#![allow(dead_code, unused_imports)]
#![allow(clippy::field_reassign_with_default, clippy::unnecessary_sort_by)]

use std::collections::HashMap;
use std::net::SocketAddr;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use std::time::Duration;

use axum::extract::RawQuery;
use axum::http::HeaderMap;
use axum::routing::get;
use axum::Router;
use lattice_auth::{AuthConfig, AuthState, Clock, OrderedMap};
use lattice_platform::invitations::{self, InvitationsState};
use lattice_platform::permissions::{self, PermissionGateway, PermissionsState};
use lattice_platform::ui_redirects;
use lattice_platform::workspace::{
    self, GraphReads, GraphSeam, WorkspaceDeps, WorkspaceProviders, WorkspaceState,
};
use serde_json::{json, Value};

use super::*;

pub(crate) fn substitute_tokens(value: &mut Value, symbols: &HashMap<String, String>) {
    match value {
        Value::String(text) => {
            let mut out = text.clone();
            substitute_symbol_text(&mut out, symbols);
            *text = out;
        }
        Value::Array(items) => {
            for item in items {
                substitute_tokens(item, symbols);
            }
        }
        Value::Object(map) => {
            for item in map.values_mut() {
                substitute_tokens(item, symbols);
            }
        }
        _ => {}
    }
}

pub(crate) fn substitute_symbols(value: &mut Value, symbols: &HashMap<String, String>) {
    substitute_tokens(value, symbols);
}

pub(crate) fn substitute_symbol_text(text: &mut String, symbols: &HashMap<String, String>) {
    let mut pairs: Vec<_> = symbols.iter().collect();
    pairs.sort_by(|a, b| b.0.len().cmp(&a.0.len()));
    for (symbol, replacement) in pairs {
        *text = replace_symbol(text, symbol, replacement);
    }
}

/// Replace `$name` only when it is not a prefix of a longer `$name_suffix`.
pub(crate) fn replace_symbol(haystack: &str, symbol: &str, replacement: &str) -> String {
    let mut out = String::with_capacity(haystack.len());
    let mut rest = haystack;
    while let Some(index) = rest.find(symbol) {
        out.push_str(&rest[..index]);
        let after = &rest[index + symbol.len()..];
        let continues = after
            .chars()
            .next()
            .is_some_and(|ch| ch == '_' || ch.is_ascii_alphanumeric());
        if continues {
            out.push_str(symbol);
        } else {
            out.push_str(replacement);
        }
        rest = after;
    }
    out.push_str(rest);
    out
}

pub(crate) fn rewrite_path_tokens(value: &mut Value, data_dir: &Path) {
    let data = data_dir.to_string_lossy().into_owned();
    // `/private<HOME>` exists in the fixtures because the Python oracle ran on
    // macOS, where the per-test temp dir is reached through the `/private`
    // symlink and every path the handler resolves comes back carrying that
    // prefix. Prepending the literal string reproduced that only on macOS: on
    // Linux, where `/tmp` is already canonical, it turned `/tmp/.tmpXXXX` into
    // `/private/tmp/.tmpXXXX` and four `permissions.py` cases failed on the
    // machine rather than on the behaviour. Ask the filesystem for the
    // resolved form instead — byte-identical to the old value on macOS,
    // and the plain path everywhere else.
    let private_home = std::fs::canonicalize(data_dir)
        .map(|resolved| resolved.to_string_lossy().into_owned())
        .unwrap_or_else(|_| format!("/private{data}"));
    match value {
        Value::String(text) => {
            *text = text
                .replace("<DATA_DIR>", &data)
                .replace("/private<HOME>", &private_home)
                .replace("<HOME>", &data)
                .replace("<REPO>", "/repo")
                .replace("<SANDBOX>", &data);
        }
        Value::Array(items) => {
            for item in items {
                rewrite_path_tokens(item, data_dir);
            }
        }
        Value::Object(map) => {
            for item in map.values_mut() {
                rewrite_path_tokens(item, data_dir);
            }
        }
        _ => {}
    }
}

pub(crate) fn values_match(expected: &Value, actual: &Value) -> bool {
    match expected {
        Value::String(token)
            if token == "@any"
                || token == "@ts"
                || token == "@uuid"
                || token == "@id"
                || token == "@version"
                || token.starts_with('$') =>
        {
            true
        }
        Value::String(exp)
            if exp.contains("@id") || exp.contains("@any") || exp.contains("@ts") =>
        {
            let Some(act) = actual.as_str() else {
                return false;
            };
            wildcard_match(exp, act)
        }
        Value::String(exp) => actual.as_str() == Some(exp.as_str()),
        Value::Array(exp) => {
            let Some(act) = actual.as_array() else {
                return false;
            };
            if exp.len() != act.len() {
                return false;
            }
            exp.iter().zip(act.iter()).all(|(e, a)| values_match(e, a))
        }
        Value::Object(exp) => {
            let Some(act) = actual.as_object() else {
                return false;
            };
            // Expected keys must match; extra actual keys (audit `contract`, …)
            // are allowed so a richer native helper does not fail a slimmer
            // captured envelope.
            exp.iter().all(|(k, ev)| {
                if k.starts_with('$') {
                    act.values().any(|av| values_match(ev, av))
                } else {
                    act.get(k).is_some_and(|av| values_match(ev, av))
                }
            })
        }
        other => other == actual,
    }
}

/// `@id` / `@any` / `@ts` as a substring wildcard inside an otherwise pinned string.
pub(crate) fn wildcard_match(pattern: &str, actual: &str) -> bool {
    let mut pat = pattern;
    let mut act = actual;
    loop {
        let next = ["@id", "@any", "@ts"]
            .iter()
            .filter_map(|token| pat.find(token).map(|index| (index, token.len())))
            .min_by_key(|(index, _)| *index);
        let Some((index, token_len)) = next else {
            return act == pat;
        };
        let prefix = &pat[..index];
        if !act.starts_with(prefix) {
            return false;
        }
        act = &act[prefix.len()..];
        pat = &pat[index + token_len..];
        if pat.is_empty() {
            return true;
        }
        let next_wild = ["@id", "@any", "@ts"]
            .iter()
            .filter_map(|token| pat.find(token))
            .min();
        let literal = match next_wild {
            Some(0) => continue,
            Some(at) => &pat[..at],
            None => pat,
        };
        if literal.is_empty() {
            continue;
        }
        match act.find(literal) {
            Some(found) => {
                act = &act[found + literal.len()..];
                pat = &pat[literal.len()..];
            }
            None => return false,
        }
    }
}
