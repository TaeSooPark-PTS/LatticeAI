//! `LATTICEAI_FUSION_RRF=1` — rank fusion, switched on.
//!
//! Its own binary for the same reason as `expansion_gated.rs`: the gate is
//! process-wide, so the only race-free way to assert an "on" behaviour is to
//! own the process.
//!
//! What this pins is not "RRF ranks differently" — it is that the *reported*
//! strategy stops lying (`fusion_strategy` was the literal `"alpha"` no matter
//! what the switch said) and that a row both channels agree on outranks a row
//! only one of them found, which is the entire reason to fuse by position.

use lattice_core::LocalEmbeddingModel;
use lattice_retrieval::hybrid::{hybrid_search, HybridOptions};
use rusqlite::Connection;

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
           ('both','Document','retrieval ranking','retrieval ranking notes','{}',
            '2026-08-11T09:00:00'),
           ('lex','Document','retrieval only','retrieval appears here too','{}',
            '2026-08-11T09:00:00');
         INSERT INTO nodes_v2 VALUES ('both', NULL), ('lex', NULL);",
    )
    .unwrap();
    // Only `both` carries a vector, so only it is in two channels.
    let vector = model.encode(&model.embed("retrieval ranking notes"));
    conn.execute(
        "INSERT INTO vector_embeddings VALUES ('both','node','both',?,384,?,'{}',
         '2026-08-11T09:00:00')",
        rusqlite::params![vector, model.model_id()],
    )
    .unwrap();
    (dir, conn, model)
}

#[test]
fn rank_fusion_is_reported_and_rewards_channel_agreement() {
    // Safety: one test in this binary; nothing else can read the variable.
    std::env::set_var("LATTICEAI_FUSION_RRF", "1");
    let (_dir, conn, model) = seeded();

    let payload = hybrid_search(&conn, &model, "retrieval", &HybridOptions::default()).unwrap();

    assert_eq!(
        payload["fusion_strategy"], "rrf",
        "the switch used to be reported as `alpha` regardless"
    );
    let matches = payload["matches"].as_array().unwrap();
    assert!(matches.len() >= 2);
    let both = matches
        .iter()
        .find(|item| item["node_id"] == "both")
        .expect("the two-channel row");
    let lex = matches
        .iter()
        .find(|item| item["node_id"] == "lex")
        .expect("the one-channel row");
    assert_eq!(both["fusion"], "both");
    assert_eq!(lex["fusion"], "lexical");
    assert!(
        both["score"].as_f64().unwrap() > lex["score"].as_f64().unwrap(),
        "a row both channels found must outrank a row only one found"
    );
    // RRF sums 1/(60 + rank) over the channels a row appeared in. `lex` is
    // second in the lexical channel and absent from the vector one, so its
    // whole score is a single 1/62 — no zero term, because RRF has no zero.
    assert_eq!(
        lex["score"].as_f64().unwrap(),
        (1.0f64 / 62.0 * 1_000_000.0).round() / 1_000_000.0
    );
    // The channel scores themselves are still reported — RRF replaces the
    // *fusion*, not the evidence a reader uses to judge it.
    assert!(both["scores"]["lexical"].as_f64().unwrap() > 0.0);
    assert!(both["scores"]["vector"].as_f64().unwrap() > 0.0);
    // Expansion is a separate switch and stays off.
    assert_eq!(payload["graph_expansion"]["enabled"], false);
}
