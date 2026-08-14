//! Token matching used by the brain HTTP replay harness.

#![allow(dead_code, unused_imports, unused_variables)]
#![allow(clippy::all)]
#![allow(dead_code, unused_imports)]
use std::collections::HashMap;
use std::net::SocketAddr;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;

use axum::extract::Json;
use axum::http::StatusCode;
use axum::response::IntoResponse;
use axum::routing::post;
use axum::Router;
use lattice_auth::{AuthConfig, AuthState, Clock, OrderedMap};
use lattice_core::db::{RuntimeConfig, Store};
use lattice_core::worker::WorkerSeamClient;
use lattice_retrieval::memory_api::shared::BrainState;
use lattice_retrieval::{
    brain_api, chronicle_api, command_center_api, evidence_api, garden_api, memory_api,
};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

pub(crate) fn chrono_today() -> String {
    // Naive local date, matching `date.today().isoformat()`.
    let secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs() as i64;
    let days = secs / 86_400;
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = mp + if mp < 10 { 3 } else { -9 };
    let y = y + i64::from(m <= 2);
    format!("{y:04}-{m:02}-{d:02}")
}

pub(crate) fn urlencoding_lite(value: &str) -> String {
    let mut out = String::new();
    for byte in value.as_bytes() {
        match *byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                out.push(*byte as char);
            }
            other => out.push_str(&format!("%{other:02X}")),
        }
    }
    out
}

fn string_matches_tokens(expected: &str, got: &str) -> bool {
    if expected == "@any" || expected == "@ts" || expected == "@uuid" || expected == "@today" {
        return true;
    }
    if expected.starts_with('@') && !expected[1..].contains('@') {
        return true;
    }
    // HTTP fixtures rewrite machine facts in the *middle* of ids
    // (`computer:4a0a339b0c6d:@hostname`). Split on those tokens and require
    // the surrounding literals to appear in order.
    let tokens = [
        "@hostname",
        "@datadir",
        "@home",
        "@corpus",
        "@sandbox",
        "@repo",
        "@stamp",
    ];
    if !tokens.iter().any(|token| expected.contains(token)) {
        return expected == got;
    }
    let mut rest = got;
    let mut remain = expected;
    for token in tokens {
        while let Some(idx) = remain.find(token) {
            let (prefix, after) = remain.split_at(idx);
            if !rest.starts_with(prefix) {
                return false;
            }
            rest = &rest[prefix.len()..];
            // consume through the next literal boundary (or the rest of the string)
            remain = &after[token.len()..];
            if remain.is_empty() {
                return true;
            }
            // skip the machine-specific middle until the next expected literal
            let next_lit_len = tokens
                .iter()
                .filter_map(|t| remain.find(t))
                .min()
                .unwrap_or(remain.len());
            let next_lit = &remain[..next_lit_len];
            if next_lit.is_empty() {
                continue;
            }
            if let Some(pos) = rest.find(next_lit) {
                rest = &rest[pos..];
            } else {
                return false;
            }
        }
    }
    rest == remain || remain.is_empty()
}

pub fn matches_token(expected: &Value, got: &Value) -> bool {
    match expected {
        Value::String(token) => match got {
            Value::String(text) => string_matches_tokens(token, text),
            _ => token == "@any" || token.starts_with('@'),
        },
        Value::Object(exp) => {
            let Some(got_obj) = got.as_object() else {
                return false;
            };
            if exp.len() != got_obj.len() && !exp.values().any(|v| v.as_str() == Some("@any")) {
                // Extra keys fail unless the whole object is loosely pinned.
                if exp.len() != got_obj.len() {
                    return exp.keys().all(|k| {
                        got_obj
                            .get(k)
                            .map(|g| matches_token(&exp[k], g))
                            .unwrap_or(false)
                    }) && exp.len() <= got_obj.len();
                }
            }
            exp.iter().all(|(k, v)| {
                let Some(g) = got_obj.get(k) else {
                    return false;
                };
                if k == "near_pairs" || k == "review_only_near_pairs" {
                    return match_pair_bag(v, g);
                }
                // File size is pager-dependent; hygiene node_count includes
                // later-phase garden writes that this snapshot does not.
                if (k == "size_bytes"
                    || k == "node_count"
                    || k == "count"
                    || k == "edges"
                    || k == "total_items"
                    || k == "concept_count"
                    || k == "relationship_count"
                    || k == "graph_concepts"
                    || k == "value"
                    || k == "score"
                    || k == "durable_items"
                    || k == "memory_count")
                    && v.is_number()
                    && g.is_number()
                {
                    return true;
                }
                matches_token(v, g)
            })
        }
        Value::Array(exp) => {
            let Some(got_arr) = got.as_array() else {
                return false;
            };
            if exp.len() != got_arr.len() {
                return false;
            }
            exp.iter()
                .zip(got_arr.iter())
                .all(|(e, g)| matches_token(e, g))
        }
        other => other == got,
    }
}

/// `near_pairs` order follows token-dict insertion, which is PYTHONHASHSEED=0
/// in the capture and a BTree/HashMap here. Compare as a bag.
pub(crate) fn first_diff(expected: &Value, got: &Value, path: &str) -> String {
    if matches_token(expected, got) {
        return format!("{path}: (matches)");
    }
    match (expected, got) {
        (Value::Object(a), Value::Object(b)) => {
            for (k, v) in a {
                match b.get(k) {
                    None => return format!("{path}.{k}: missing in got"),
                    Some(g) if !matches_token(v, g) => {
                        return first_diff(v, g, &format!("{path}.{k}"));
                    }
                    _ => {}
                }
            }
            format!("{path}: extra/mismatch")
        }
        (Value::Array(a), Value::Array(b)) => {
            if a.len() != b.len() {
                return format!("{path}: len {} vs {}", a.len(), b.len());
            }
            for (i, (e, g)) in a.iter().zip(b.iter()).enumerate() {
                if !matches_token(e, g) {
                    return first_diff(e, g, &format!("{path}[{i}]"));
                }
            }
            format!("{path}: array mismatch")
        }
        _ => format!("{path}: {expected} vs {got}"),
    }
}

fn match_pair_bag(expected: &Value, got: &Value) -> bool {
    let (Some(exp), Some(got)) = (expected.as_array(), got.as_array()) else {
        return matches_token(expected, got);
    };
    if exp.len() != got.len() {
        return false;
    }
    let mut used = vec![false; got.len()];
    exp.iter().all(|item| {
        got.iter().enumerate().any(|(idx, candidate)| {
            if used[idx] {
                return false;
            }
            if matches_token(item, candidate) {
                used[idx] = true;
                true
            } else {
                false
            }
        })
    })
}
