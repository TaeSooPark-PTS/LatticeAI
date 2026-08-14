//! Unit coverage for the native `audit_log.json` append helper.

#![allow(dead_code, unused_imports, unused_variables)]
#![allow(clippy::all)]
use lattice_platform::admin::{append_audit_event, load_audit_log};
use serde_json::{json, Map};

#[test]
fn append_writes_event_id_timestamp_and_contract() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("audit_log.json");
    let mut payload = Map::new();
    payload.insert("user_email".into(), json!("owner@lattice.test"));
    payload.insert("token".into(), json!("sk-supersecretfixturekey"));
    append_audit_event(&path, "user_update", payload);
    let events = load_audit_log(&path);
    assert_eq!(events.len(), 1);
    let ev = &events[0];
    assert!(ev["event_id"].as_str().unwrap().starts_with("audit-"));
    assert_eq!(ev["event_type"], "user_update");
    assert!(ev["timestamp"].as_str().unwrap().contains('T'));
    assert_eq!(ev["user_email"], "owner@lattice.test");
    // secret-shaped values are redacted before persist
    assert_eq!(ev["token"], "[REDACTED_SECRET]");
    assert_eq!(ev["contract"]["family"], "agent-run-contract/v1");
    assert_eq!(ev["contract"]["kind"], "audit_event");
}

#[test]
fn missing_file_is_empty() {
    let dir = tempfile::tempdir().unwrap();
    assert!(load_audit_log(&dir.path().join("audit_log.json")).is_empty());
}
