//! Python↔Rust parity for the Phase-1 search engines, over the committed fixture.
//!
//! Same database, same queries, same golden files that
//! `tests/unit/test_rust_parity_contract.py` re-asserts against the Python
//! engines. Comparison is exact; the shared plumbing lives in `common`.

#![allow(dead_code, unused_imports, unused_variables)]
#![allow(clippy::all)]
mod common;

use common::{allowed_set, diff, golden, manifest, open_store, pin_environment};

use lattice_core::{parse_iso, LocalEmbeddingModel};
use lattice_retrieval::hybrid::{hybrid_search, HybridOptions};
use lattice_retrieval::keyword::search;
use lattice_retrieval::vector::vector_search;
use rusqlite::Connection;
use serde_json::Value;

fn run(conn: &Connection, model: &LocalEmbeddingModel, golden: &Value, now: f64) -> Value {
    let params = &golden["params"];
    let query = golden["query"].as_str().unwrap();
    let allowed = allowed_set(params.get("allowed_workspaces"));
    let legacy = params["include_legacy_global"].as_bool().unwrap();
    match golden["engine"].as_str().unwrap() {
        "hybrid" => hybrid_search(
            conn,
            model,
            query,
            &HybridOptions {
                top_k: params["top_k"].as_i64().unwrap(),
                alpha: params["alpha"].as_f64(),
                allowed_workspaces: allowed,
                include_legacy_global: legacy,
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
            allowed.as_ref(),
            legacy,
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
            let recorded = golden(&format!("{engine}__{key}"));
            let expected = &recorded["result"];
            let got = run(&conn, &model, &recorded, now);
            if &got != expected {
                failures.push(diff(&format!("{engine}/{key}"), expected, &got));
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
