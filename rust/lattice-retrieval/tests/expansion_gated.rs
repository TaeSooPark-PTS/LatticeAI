//! `LATTICEAI_GRAPH_EXPANSION=1` — the one-hop path, switched on.
//!
//! Its own test binary, and deliberately so: the gate is a process-wide
//! environment variable, and a `set_var` inside a shared binary races every
//! other test in it. One process, one setting, no mutex to forget.
//!
//! The off path is asserted by `tests/parity.rs` and `tests/suites.rs` against
//! the frozen goldens — those run without this variable and must never see a
//! difference, which is the other half of this feature's contract.

use lattice_core::LocalEmbeddingModel;
use lattice_retrieval::hybrid::{hybrid_search, HybridOptions};
use lattice_retrieval::service_hybrid::{service_hybrid_search, ServiceHybridOptions};
use rusqlite::Connection;
use serde_json::Value;

/// Two documents about the same launch, joined by an extracted relation. Only
/// one of them says "phoenix"; the other is reachable **only** across the edge.
fn seeded() -> (tempfile::TempDir, Connection, LocalEmbeddingModel) {
    let model = LocalEmbeddingModel::new(384);
    let dir = tempfile::tempdir().unwrap();
    let conn = Connection::open(dir.path().join("g.sqlite")).unwrap();
    conn.execute_batch(
        "CREATE TABLE nodes(id TEXT PRIMARY KEY, type TEXT, title TEXT, summary TEXT,
                            metadata_json TEXT, updated_at TEXT);
         CREATE TABLE edges(id TEXT PRIMARY KEY, from_node TEXT, to_node TEXT, type TEXT,
                            weight REAL, metadata_json TEXT, created_at TEXT);
         CREATE TABLE nodes_v2(id TEXT PRIMARY KEY, workspace_id TEXT);
         CREATE TABLE chunks(id TEXT PRIMARY KEY, source_node TEXT, text TEXT,
                             metadata_json TEXT);
         CREATE TABLE vector_embeddings(item_id TEXT PRIMARY KEY, item_type TEXT,
           source_node TEXT, embedding BLOB, embedding_dim INTEGER,
           embedding_model TEXT, metadata_json TEXT, indexed_at TEXT);
         INSERT INTO nodes VALUES
           ('doc','Document','Phoenix launch','phoenix ships in March','{}',
            '2026-08-11T09:00:00'),
           ('owner','Concept','배포담당팀','이 노드에는 그 단어가 어디에도 없다','{}',
            '2026-08-11T09:00:00'),
           ('deputy','Concept','릴리스매니저','두 홉 떨어져 있는 노드','{}',
            '2026-08-11T09:00:00');
         INSERT INTO nodes_v2 VALUES ('doc', NULL), ('owner', NULL), ('deputy', NULL);
         INSERT INTO edges VALUES
           ('e1','doc','owner','의존함',1.0,
            '{\"context\":\"[출시 > 담당] Phoenix는 배포담당팀에 의존한다.\",\"evidence\":\"verb\"}',
            '2026-08-11'),
           ('e2','owner','deputy','구성요소',0.9,
            '{\"context\":\"[출시 > 담당] 배포담당팀은 릴리스매니저의 일부이다.\",\"evidence\":\"structure\"}',
            '2026-08-11');",
    )
    .unwrap();
    (dir, conn, model)
}

fn ids(payload: &Value) -> Vec<String> {
    payload["matches"]
        .as_array()
        .unwrap()
        .iter()
        .map(|item| item["node_id"].as_str().unwrap_or_default().to_string())
        .collect()
}

#[test]
fn a_neighbour_of_a_hit_becomes_reachable_and_says_how() {
    // Safety: this binary contains exactly one test, so nothing else in the
    // process can observe the variable mid-flight.
    std::env::set_var("LATTICEAI_GRAPH_EXPANSION", "1");

    // The *product's* search surface first (`GET/POST /api/search/hybrid`,
    // `assemble_context` and the plan route all call this one). The graph
    // layer's `hybrid_search` below has no production caller, so a feature
    // that only reached it would be off everywhere that matters.
    {
        let (_dir, conn, model) = seeded();
        let payload =
            service_hybrid_search(&conn, &model, "phoenix", &ServiceHybridOptions::default())
                .unwrap();
        assert_eq!(payload["graph_expansion"]["enabled"], Value::Bool(true));
        // The service's own `graph` channel already walks one hop out of the
        // *lexical* seeds, so `owner` is not new. What expansion adds is a hop
        // out of the **fused** top hits — one step further than any channel.
        let neighbour = payload["matches"]
            .as_array()
            .unwrap()
            .iter()
            .find(|item| item["node_id"] == "deputy")
            .expect("two hops out is reachable only through expansion");
        assert_eq!(payload["graph_expansion"]["added"], 1);
        assert_eq!(neighbour["via"]["seed_node_id"], "owner");
        assert_eq!(neighbour["via"]["edge_type"], "구성요소");
        assert_eq!(neighbour["sources"], serde_json::json!(["graph_expansion"]));
        // No channel found it, so it claims no channel score.
        assert_eq!(neighbour["source_scores"], serde_json::json!({}));
    }

    let (_dir, conn, model) = seeded();

    let payload = hybrid_search(&conn, &model, "phoenix", &HybridOptions::default()).unwrap();

    let found = ids(&payload);
    assert_eq!(
        found,
        vec!["doc".to_string(), "owner".to_string()],
        "the neighbour lands *under* the hit it was reached from"
    );
    // The graph layer has no graph channel at all, so one hop is all it reaches.

    let expansion = &payload["graph_expansion"];
    assert_eq!(expansion["enabled"], Value::Bool(true));
    assert_eq!(expansion["seeds"], 1);
    assert_eq!(expansion["added"], 1);
    assert_eq!(expansion["cap"], 5);
    assert_eq!(expansion["truncated"], Value::Bool(false));
    assert_eq!(expansion["failed_seeds"], 0);

    let neighbour = &payload["matches"][1];
    assert_eq!(neighbour["fusion"], "graph");
    assert_eq!(neighbour["title"], "배포담당팀");
    // Half its seed's score: a lead, never a match.
    let seed_score = payload["matches"][0]["score"].as_f64().unwrap();
    assert!(seed_score > 0.0);
    assert_eq!(neighbour["score"].as_f64().unwrap(), seed_score * 0.5);

    // The whole point: the answer can say *why* this row is here.
    let via = &neighbour["via"];
    assert_eq!(via["seed_node_id"], "doc");
    assert_eq!(via["edge_type"], "의존함");
    assert_eq!(via["direction"], "outgoing");
    assert_eq!(via["evidence"], "verb");
    assert_eq!(
        via["context"],
        "[출시 > 담당] Phoenix는 배포담당팀에 의존한다."
    );
    // A row that stood on its own claims no path in.
    assert!(payload["matches"][0].get("via").is_none());
}
