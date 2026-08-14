//! Python↔Rust parity for the v11.5.0 ports — the knowledge-graph relationship
//! and traversal reads, the service layer (graph channel + three-channel
//! fusion), the durable history reads and the context assembler — and the
//! v11.5.1 document-generation ports beside them: the hybrid document search,
//! the hop-labelled traversal, and the budgeted context builder (whose golden
//! carries the sources footnote alongside the contract dict, so both public
//! entry points are proven by one suite).
//!
//! Each suite in the manifest is a list of specs the Python generator ran; this
//! runs the Rust port on the same spec and compares the whole answer exactly.
//! The spec rides inside the golden, so there is nothing to keep in sync by hand
//! — adding a case to the generator makes this suite check it on the next run.

mod common;

use common::{allowed_set, diff, golden, manifest, open_store, pin_environment};

use lattice_core::{parse_iso, CoreError, LocalEmbeddingModel};
use lattice_retrieval::context::{assemble_context, ContextRequest};
use lattice_retrieval::docgen::{multi_hop_context, search_for_document_generation};
use lattice_retrieval::docgen_context::{
    format_sources_footnote, retrieve_context_for_generation, DocumentContextRequest,
};
use lattice_retrieval::graph_reads::{
    relationship_search, traverse, RelationshipQuery, TraverseOptions,
};
use lattice_retrieval::history::{
    conversation_messages, group_conversations, history, recent_chat, search_history, HistoryScope,
    RecentChatOptions,
};
use lattice_retrieval::service::{graph_search, GraphSearchOptions, Scope};
use lattice_retrieval::service_hybrid::{service_hybrid_search, ServiceHybridOptions};
use rusqlite::Connection;
use serde_json::{Map, Value};

fn text(spec: &Value, key: &str) -> String {
    spec.get(key)
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string()
}

fn opt_text(spec: &Value, key: &str) -> Option<String> {
    spec.get(key).and_then(Value::as_str).map(str::to_string)
}

fn int(spec: &Value, key: &str, default: i64) -> i64 {
    spec.get(key).and_then(Value::as_i64).unwrap_or(default)
}

fn flag(spec: &Value, key: &str, default: bool) -> bool {
    spec.get(key).and_then(Value::as_bool).unwrap_or(default)
}

/// The graph layer's scope: `allowed` absent means no scoping, `legacy` is off
/// by default (the opposite of the history layer's).
fn graph_scope(spec: &Value) -> Scope {
    Scope {
        allowed_workspaces: allowed_set(spec.get("allowed")),
        include_legacy_global: flag(spec, "legacy", false),
    }
}

/// The history layer's scope: `legacy` defaults **on**, as in Python.
fn history_scope(spec: &Value) -> HistoryScope {
    HistoryScope {
        user_email: opt_text(spec, "user_email"),
        allowed_workspaces: spec.get("allowed").and_then(Value::as_array).map(|items| {
            items
                .iter()
                .map(|item| item.as_str().unwrap_or_default().to_string())
                .collect()
        }),
        include_legacy_global: flag(spec, "legacy", true),
    }
}

fn scoped_history(conn: &Connection, spec: &Value) -> Vec<Value> {
    history(conn, None, None, &history_scope(spec)).expect("history must not fail on the fixture")
}

/// The document-generation seed list, as the spec spells it.
fn node_ids(spec: &Value) -> Vec<String> {
    spec.get("node_ids")
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .map(|item| item.as_str().unwrap_or_default().to_string())
                .collect()
        })
        .unwrap_or_default()
}

/// The recent-chat suite's spec, in the shape `build_recent_chat_context` takes.
fn recent_chat_options(spec: &Value) -> RecentChatOptions {
    RecentChatOptions {
        limit: int(spec, "limit", 10),
        include_image_missing_replies: flag(spec, "images", true),
        user_email: opt_text(spec, "user_email"),
        conversation_id: opt_text(spec, "conversation_id"),
        workspace_id: opt_text(spec, "workspace_id"),
    }
}

fn run(
    conn: &Connection,
    model: &LocalEmbeddingModel,
    suite: &str,
    spec: &Value,
    now: f64,
) -> Value {
    match suite {
        "relationship" => relationship_search(
            conn,
            &RelationshipQuery {
                query: text(spec, "query"),
                node_id: text(spec, "node_id"),
                relationship_type: text(spec, "relationship_type"),
                limit: int(spec, "limit", 30),
                allowed_workspaces: allowed_set(spec.get("allowed")),
                include_legacy_global: flag(spec, "legacy", false),
            },
        )
        .expect("relationship_search must not fail on the fixture"),
        "traverse" => match traverse(
            conn,
            &text(spec, "node_id"),
            &TraverseOptions {
                depth: int(spec, "depth", 1),
                limit: int(spec, "limit", 100),
                allowed_workspaces: allowed_set(spec.get("allowed")),
                include_legacy_global: flag(spec, "legacy", false),
            },
        ) {
            Ok(payload) => payload,
            // Python's two `ValueError`s are recorded, not skipped.
            Err(CoreError::InvalidRequest(message)) => serde_json::json!({"error": message}),
            Err(err) => panic!("traverse failed on the fixture: {err}"),
        },
        "graph_search" => graph_search(
            conn,
            &text(spec, "query"),
            &GraphSearchOptions {
                limit: int(spec, "limit", 30),
                expand_depth: int(spec, "expand_depth", 1),
                scope: graph_scope(spec),
            },
        )
        .expect("graph_search must not fail on the fixture"),
        "service_hybrid" => service_hybrid_search(
            conn,
            model,
            &text(spec, "query"),
            &ServiceHybridOptions {
                limit: int(spec, "limit", 30),
                keyword_limit: int(spec, "keyword_limit", 30),
                vector_limit: int(spec, "vector_limit", 30),
                graph_limit: int(spec, "graph_limit", 30),
                weights: spec
                    .get("weights")
                    .and_then(Value::as_object)
                    .cloned()
                    .map(Map::from_iter),
                scope: graph_scope(spec),
                now_secs: now,
            },
        )
        .expect("service hybrid must not fail on the fixture"),
        "history" => Value::Array(
            history(
                conn,
                spec.get("conversation_id").and_then(Value::as_str),
                spec.get("limit").and_then(Value::as_i64),
                &history_scope(spec),
            )
            .expect("history must not fail on the fixture"),
        ),
        "conversations" => Value::Array(group_conversations(&scoped_history(conn, spec))),
        "conversation_messages" => Value::Array(conversation_messages(
            &scoped_history(conn, spec),
            &text(spec, "conversation_id"),
        )),
        "recent_chat" => Value::String(
            recent_chat(conn, &recent_chat_options(spec))
                .expect("recent chat must not fail on the fixture"),
        ),
        "history_search" => Value::Array(search_history(
            &scoped_history(conn, spec),
            &text(spec, "query"),
            int(spec, "limit", 30),
        )),
        "docgen_search" => Value::Array(
            search_for_document_generation(
                conn,
                &text(spec, "query"),
                int(spec, "limit", 10),
                &graph_scope(spec),
                now,
            )
            .expect("document search must not fail on the fixture"),
        ),
        "multi_hop" => multi_hop_context(
            conn,
            &node_ids(spec),
            int(spec, "max_hops", 2),
            &graph_scope(spec),
        )
        .expect("multi-hop must not fail on the fixture"),
        "context_document" => {
            let context = retrieve_context_for_generation(
                conn,
                &DocumentContextRequest {
                    query: text(spec, "query"),
                    max_results: int(spec, "max_results", 10),
                    max_hops: int(spec, "max_hops", 2),
                    budget: int(spec, "budget", 2000),
                    include_self_model: flag(spec, "include_self_model", true),
                    self_model_tokens: int(spec, "self_model_tokens", 200),
                    scope: graph_scope(spec),
                    now_secs: now,
                },
            )
            .expect("document context must not fail on the fixture");
            let sources: Vec<Value> = context["sources"].as_array().cloned().unwrap_or_default();
            serde_json::json!({
                "context": context,
                "sources_footnote": format_sources_footnote(&sources),
            })
        }
        "context_assemble" => assemble_context(
            conn,
            model,
            &ContextRequest {
                query: text(spec, "query"),
                budget: int(spec, "budget", 2000),
                memory_limit: int(spec, "memory_limit", 5),
                knowledge_limit: int(spec, "knowledge_limit", 5),
                memories: spec.get("memories").cloned(),
                artifacts: spec.get("artifacts").cloned(),
                knowledge: flag(spec, "knowledge", true),
                notes: opt_text(spec, "notes"),
                // 11.5.2: no recent seam. The production assembler supplies
                // four seams and never this one, so a golden that pinned it
                // claimed agreement about a path the product does not run.
                // The transcript itself is pinned by the `recent_chat` suite.
                recent: None,
                user_email: opt_text(spec, "user_email"),
                conversation_id: opt_text(spec, "conversation_id"),
                workspace_id: opt_text(spec, "workspace_id"),
                now_secs: now,
            },
        )
        .expect("context assembly must not fail on the fixture"),
        other => panic!("unknown suite {other}"),
    }
}

#[test]
fn every_suite_spec_matches_its_python_golden() {
    pin_environment();
    let dir = tempfile::tempdir().unwrap();
    let conn = open_store(dir.path());
    let model = LocalEmbeddingModel::new(manifest()["embedding_dim"].as_u64().unwrap() as usize);
    let now = parse_iso(manifest()["frozen_now"].as_str()).expect("frozen clock must parse");
    let suites = manifest()["suites"]
        .as_object()
        .expect("the manifest must describe the v11.5.0 suites");
    assert_eq!(
        suites.len(),
        13,
        "thirteen ported entry points; a missing suite is a missing proof"
    );

    let mut checked = 0usize;
    let mut failures: Vec<String> = Vec::new();
    for (suite, specs) in suites {
        let specs = specs.as_array().expect("a suite is a list of specs");
        assert!(!specs.is_empty(), "{suite} has no specs");
        for spec in specs {
            let key = spec["key"].as_str().unwrap();
            let recorded = golden(&format!("{suite}__{key}"));
            assert_eq!(
                &recorded["spec"], spec,
                "{suite}/{key}: the golden was generated from a different spec"
            );
            let expected = &recorded["result"];
            let got = run(&conn, &model, suite, spec, now);
            if &got != expected {
                failures.push(diff(&format!("{suite}/{key}"), expected, &got));
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
    assert!(checked >= 150, "only {checked} suite cases — keep it wide");
}
