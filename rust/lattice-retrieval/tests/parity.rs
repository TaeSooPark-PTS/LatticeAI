//! Python↔Rust retrieval parity, over the committed fixture.
//!
//! Same database, same queries, same golden files that
//! `tests/unit/test_rust_parity_contract.py` re-asserts against the Python
//! engines. Comparison is **exact**: `serde_json::Value` equality over the whole
//! response, so a drifting float, a missing honesty field, a reordered tie and a
//! renamed key all fail the same way.
//!
//! Regenerate with `.venv/bin/python scripts/generate_rust_parity_fixtures.py`.

use std::collections::BTreeSet;
use std::path::PathBuf;
use std::sync::OnceLock;

use lattice_core::{open_read_only, parse_iso, LocalEmbeddingModel};
use lattice_retrieval::hybrid::{hybrid_search, HybridOptions};
use lattice_retrieval::keyword::search;
use lattice_retrieval::vector::vector_search;
use rusqlite::Connection;
use serde_json::Value;

fn fixtures() -> PathBuf {
    [env!("CARGO_MANIFEST_DIR"), "..", "fixtures"]
        .iter()
        .collect()
}

fn read_json(path: PathBuf) -> Value {
    let raw = std::fs::read_to_string(&path)
        .unwrap_or_else(|err| panic!("missing fixture {}: {err}", path.display()));
    serde_json::from_str(&raw).expect("fixture must be valid JSON")
}

fn manifest() -> &'static Value {
    static MANIFEST: OnceLock<Value> = OnceLock::new();
    MANIFEST.get_or_init(|| read_json(fixtures().join("golden").join("manifest.json")))
}

/// Pin every environment knob to the configuration the goldens were built with.
///
/// A developer shell that already exports `LATTICEAI_VECTOR_INDEX=hnsw` would
/// otherwise fail this suite for a reason that has nothing to do with the port.
fn pin_environment() {
    static PINNED: OnceLock<()> = OnceLock::new();
    PINNED.get_or_init(|| {
        for (key, value) in manifest()["pinned_env"].as_object().unwrap() {
            std::env::set_var(key, value.as_str().unwrap());
        }
    });
}

/// Open the committed store read-only — from a copy, so a stray `-wal`/`-shm`
/// sidecar can never land next to a checked-in fixture.
fn open_store(dir: &std::path::Path) -> Connection {
    let source = fixtures().join(manifest()["store"].as_str().unwrap());
    let target = dir.join("parity_store.sqlite");
    std::fs::copy(&source, &target).expect("fixture database must exist");
    open_read_only(&target).expect("the fixture must open read-only")
}

fn allowed_of(params: &Value) -> Option<BTreeSet<String>> {
    params["allowed_workspaces"].as_array().map(|items| {
        items
            .iter()
            .map(|v| v.as_str().unwrap_or_default().to_string())
            .collect()
    })
}

fn run(conn: &Connection, model: &LocalEmbeddingModel, golden: &Value, now: f64) -> Value {
    let params = &golden["params"];
    let query = golden["query"].as_str().unwrap();
    match golden["engine"].as_str().unwrap() {
        "hybrid" => hybrid_search(
            conn,
            model,
            query,
            &HybridOptions {
                top_k: params["top_k"].as_i64().unwrap(),
                alpha: params["alpha"].as_f64(),
                allowed_workspaces: allowed_of(params),
                include_legacy_global: params["include_legacy_global"].as_bool().unwrap(),
                min_vector_score: params["min_vector_score"].as_f64().unwrap(),
                now_secs: now,
                ..HybridOptions::default()
            },
        )
        .expect("hybrid_search must not fail on the fixture"),
        "keyword" => search(
            conn,
            query,
            params["limit"].as_i64().unwrap(),
            allowed_of(params).as_ref(),
            params["include_legacy_global"].as_bool().unwrap(),
        )
        .expect("search must not fail on the fixture"),
        "vector" => vector_search(
            conn,
            model,
            query,
            params["limit"].as_i64().unwrap(),
            params["min_score"].as_f64().unwrap(),
        )
        .expect("vector_search must not fail on the fixture"),
        other => panic!("unknown engine {other}"),
    }
}

#[test]
fn every_query_matches_its_python_golden() {
    pin_environment();
    let dir = tempfile::tempdir().unwrap();
    let conn = open_store(dir.path());
    let model = LocalEmbeddingModel::new(manifest()["embedding_dim"].as_u64().unwrap() as usize);
    assert_eq!(
        model.model_id(),
        manifest()["embedding_model"].as_str().unwrap()
    );
    let now = parse_iso(manifest()["frozen_now"].as_str()).expect("frozen clock must parse");

    let queries = manifest()["queries"].as_array().unwrap();
    let engines = manifest()["engines"].as_array().unwrap();
    assert!(
        queries.len() >= 14,
        "the query set is the coverage — keep it wide"
    );

    let mut checked = 0usize;
    let mut failures: Vec<String> = Vec::new();
    for spec in queries {
        let key = spec["key"].as_str().unwrap();
        for engine in engines {
            let engine = engine.as_str().unwrap();
            let golden = read_json(
                fixtures()
                    .join("golden")
                    .join(format!("{engine}__{key}.json")),
            );
            let expected = &golden["result"];
            let got = run(&conn, &model, &golden, now);
            if &got != expected {
                failures.push(diff(engine, key, expected, &got));
            }
            checked += 1;
        }
    }
    assert!(
        failures.is_empty(),
        "{} of {checked} mismatched:\n{}",
        failures.len(),
        failures.join("\n")
    );
    assert_eq!(checked, queries.len() * engines.len());
}

/// The first differing path, so a failure names the field instead of dumping
/// two thousand lines of JSON.
fn diff(engine: &str, key: &str, expected: &Value, got: &Value) -> String {
    let mut trail = Vec::new();
    walk(expected, got, &mut String::new(), &mut trail);
    format!(
        "  {engine}/{key}: {}",
        trail.first().cloned().unwrap_or_else(|| "differs".into())
    )
}

fn walk(expected: &Value, got: &Value, path: &mut String, out: &mut Vec<String>) {
    if out.len() >= 3 || expected == got {
        return;
    }
    match (expected, got) {
        (Value::Object(a), Value::Object(b)) => {
            let keys: BTreeSet<&String> = a.keys().chain(b.keys()).collect();
            for key in keys {
                let mut next = format!("{path}.{key}");
                match (a.get(key), b.get(key)) {
                    (Some(x), Some(y)) => walk(x, y, &mut next, out),
                    (Some(_), None) => out.push(format!("{next} missing in rust")),
                    (None, Some(_)) => out.push(format!("{next} extra in rust")),
                    (None, None) => {}
                }
            }
        }
        (Value::Array(a), Value::Array(b)) if a.len() == b.len() => {
            for (index, (x, y)) in a.iter().zip(b.iter()).enumerate() {
                let mut next = format!("{path}[{index}]");
                walk(x, y, &mut next, out);
            }
        }
        _ => out.push(format!("{path}: python={expected} rust={got}")),
    }
}
