use std::collections::HashMap;

use lattice_core::correction_signals::{ActiveCorrection, CorrectionSignals};
use lattice_core::learning_loop::CorrectionTargetKind;
use rusqlite::Connection;
use serde_json::{json, Map, Value};

use super::*;

fn meta(value: Value) -> Map<String, Value> {
    value.as_object().cloned().unwrap()
}

fn tier(value: Value) -> Option<(TrustTier, &'static str)> {
    assess_metadata(&meta(value)).map(|found| (found.tier, found.signal))
}

#[test]
fn tiers_order_verified_before_normal_before_draft() {
    assert!(TrustTier::Verified < TrustTier::Normal);
    assert!(TrustTier::Normal < TrustTier::Draft);
    assert_eq!(TrustTier::default(), TrustTier::Normal);
    assert_eq!(TrustTier::Verified.as_str(), "verified");
    assert_eq!(TrustTier::Normal.as_str(), "normal");
    assert_eq!(TrustTier::Draft.as_str(), "draft");
}

#[test]
fn classify_reads_strings_and_booleans_and_ignores_the_rest() {
    assert_eq!(
        classify(&json!(" Draft ")),
        Some((TrustTier::Draft, "draft".into()))
    );
    assert_eq!(
        classify(&json!("approved")),
        Some((TrustTier::Verified, "approved".into()))
    );
    assert_eq!(
        classify(&json!(true)),
        Some((TrustTier::Verified, "true".into()))
    );
    assert_eq!(
        classify(&json!(false)),
        Some((TrustTier::Draft, "false".into()))
    );
    assert_eq!(classify(&json!("active")), None);
    assert_eq!(classify(&json!(3)), None);
}

#[test]
fn metadata_signals_follow_user_then_frontmatter_then_status_then_lifecycle() {
    assert_eq!(tier(json!({})), None);
    assert_eq!(tier(json!({"status": "superseded"})), None);
    assert_eq!(
        tier(json!({"trust": "verified", "frontmatter": {"status": "draft"}})),
        Some((TrustTier::Verified, "user_trust"))
    );
    assert_eq!(
        tier(json!({"frontmatter": {"status": "draft"}, "status": "final"})),
        Some((TrustTier::Draft, "frontmatter"))
    );
    assert_eq!(
        tier(json!({"review_status": "unverified"})),
        Some((TrustTier::Draft, "metadata_status"))
    );
    assert_eq!(
        tier(json!({"lifecycle": {"state": "pending_verification"}})),
        Some((TrustTier::Draft, "lifecycle"))
    );
    assert_eq!(
        tier(json!({"lifecycle": {"state": "active", "approver": "lead@example.com"}})),
        Some((TrustTier::Verified, "lifecycle"))
    );
    assert_eq!(
        tier(json!({"lifecycle": {"state": "active", "approver": " "}})),
        None
    );
    assert_eq!(tier(json!({"lifecycle": "broken"})), None);
}

#[test]
fn provenance_reads_status_metadata_or_a_draft_pipeline() {
    let empty = Map::new();
    assert_eq!(
        assess_provenance("note", "agent_draft", &empty).map(|found| found.tier),
        Some(TrustTier::Draft)
    );
    assert_eq!(
        assess_provenance(
            "note",
            "unified-ingestion",
            &meta(json!({"trust": "verified"}))
        )
        .map(|found| found.signal),
        Some("provenance")
    );
    assert_eq!(assess_provenance("file", "unified-ingestion", &empty), None);
}

fn correction(id: &str, kind: CorrectionTargetKind) -> ActiveCorrection {
    ActiveCorrection {
        correction_id: format!("corr:{id}"),
        workspace_id: "personal".into(),
        target_kind: kind,
        target_id: id.into(),
        verdict: None,
        updated_at: "2026-01-01T00:00:00".into(),
    }
}

#[test]
fn node_trust_merges_metadata_provenance_and_confirmed_corrections() {
    let conn = Connection::open_in_memory().unwrap();
    let ids: Vec<String> = ["plain", "draft", "fixed", "user", "generated", "missing"]
        .iter()
        .map(|id| id.to_string())
        .collect();
    assert!(node_trust(&conn, &ids, &CorrectionSignals::default())
        .unwrap()
        .is_empty());
    conn.execute_batch(
        "CREATE TABLE nodes (id TEXT PRIMARY KEY, metadata_json TEXT);
         CREATE TABLE ingestion_provenance (
            node_id TEXT, source_type TEXT, pipeline TEXT, metadata_json TEXT, created_at TEXT);
         INSERT INTO nodes VALUES
            ('plain', '{}'),
            ('draft', '{\"frontmatter\":{\"status\":\"draft\"}}'),
            ('fixed', '{\"status\":\"draft\"}'),
            ('user', '{\"trust\":\"draft\"}'),
            ('generated', NULL);
         INSERT INTO ingestion_provenance VALUES
            ('generated', 'note', 'unified-ingestion', '{}', '2026-01-01'),
            ('generated', 'note', 'agent_draft', '{}', '2026-01-02'),
            ('plain', 'note', 'agent_draft', '{}', '2026-01-01'),
            ('plain', 'note', 'unified-ingestion', '{}', '2026-01-02');",
    )
    .unwrap();
    let mut corrections = CorrectionSignals::default();
    corrections.confirmed.insert(
        "fixed".into(),
        correction("fixed", CorrectionTargetKind::Fact),
    );
    corrections.confirmed.insert(
        "user".into(),
        correction("user", CorrectionTargetKind::Fact),
    );
    let found = node_trust(&conn, &ids, &corrections).unwrap();
    let summary: HashMap<&str, (TrustTier, &str)> = found
        .iter()
        .map(|(id, item)| (id.as_str(), (item.tier, item.signal)))
        .collect();
    assert_eq!(summary.get("plain"), None);
    assert_eq!(summary["draft"], (TrustTier::Draft, "frontmatter"));
    assert_eq!(
        summary["fixed"],
        (TrustTier::Verified, "confirmed_correction")
    );
    assert_eq!(summary["user"], (TrustTier::Draft, "user_trust"));
    assert_eq!(summary["generated"], (TrustTier::Draft, "provenance"));
    assert_eq!(summary.get("missing"), None);
    assert_eq!(
        found["fixed"].as_json(),
        json!({"tier": "verified", "signal": "confirmed_correction", "value": "corr:fixed"})
    );
}
