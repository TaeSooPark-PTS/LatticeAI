use serde_json::{json, Value};

use super::*;

fn digest_item(id: &str, status: &str, snoozed_until: Value) -> Value {
    json!({
        "id": id,
        "status": status,
        "title": format!("title {id}"),
        "source": SYNTHESIS_SOURCE,
        "kind": "concept",
        "payload": {"proposal_key": format!("concept:{id}")},
        "effective_status": "pending",
        "snoozed_until": snoozed_until,
        "workspace_id": "personal",
    })
}

fn doc_of(items: Vec<Value>) -> Value {
    json!({"review_items": items})
}

#[test]
fn a_dismissed_or_approved_proposal_with_a_stale_stored_status_is_not_pending() {
    let doc = doc_of(vec![
        digest_item("a", "dismissed", Value::Null),
        digest_item("b", "approved", Value::Null),
        digest_item("c", "pending", Value::Null),
    ]);
    let pending = pending_synthesis(&doc, Some("personal"), 0.0);
    let ids: Vec<&str> = pending.iter().filter_map(|i| i["id"].as_str()).collect();
    assert_eq!(ids, vec!["c"]);
}
