use super::*;
use serde_json::json;

#[test]
fn open_keys_in_treats_pending_and_active_snoozed_as_open_and_suppresses_duplicates() {
    let now = wsos::review_snooze_expiry_secs("2026-11-28T10:00:00Z").expect("now");
    let doc = json!({
        "review_items": [
            {
                "id": "rev-pending",
                "status": "pending",
                "source": SYNTHESIS_SOURCE,
                "kind": "self_model_fact",
                "workspace_id": "ws-a",
                "snoozed_until": null,
                "payload": {"proposal_key": "self_model:habit:daily-review"}
            },
            {
                "id": "rev-snoozed-active",
                "status": "snoozed",
                "source": SYNTHESIS_SOURCE,
                "kind": "self_model_fact",
                "workspace_id": "ws-a",
                "snoozed_until": "2026-11-28T11:00:00Z",
                "payload": {"proposal_key": "self_model:preference:dark-mode"}
            },
            {
                "id": "rev-snoozed-expired",
                "status": "snoozed",
                "source": SYNTHESIS_SOURCE,
                "kind": "self_model_fact",
                "workspace_id": "ws-a",
                "snoozed_until": "2026-11-28T09:59:00Z",
                "payload": {"proposal_key": "self_model:decision:use-rust"}
            },
            {
                "id": "rev-approved",
                "status": "approved",
                "source": SYNTHESIS_SOURCE,
                "kind": "self_model_fact",
                "workspace_id": "ws-a",
                "payload": {"proposal_key": "self_model:trait:curious"}
            },
            {
                "id": "rev-dismissed",
                "status": "dismissed",
                "source": SYNTHESIS_SOURCE,
                "kind": "self_model_fact",
                "workspace_id": "ws-a",
                "payload": {"proposal_key": "self_model:habit:skip-lunch"}
            },
            {
                "id": "rev-other-ws",
                "status": "snoozed",
                "source": SYNTHESIS_SOURCE,
                "kind": "self_model_fact",
                "workspace_id": "ws-b",
                "snoozed_until": "2026-11-28T11:00:00Z",
                "payload": {"proposal_key": "self_model:trait:other-ws"}
            }
        ]
    });

    let keys_a = open_keys_in(&doc, Some("ws-a"), now);
    assert!(keys_a.contains("self_model:habit:daily-review"));
    assert!(keys_a.contains("self_model:preference:dark-mode"));
    assert!(keys_a.contains("self_model:decision:use-rust"));
    assert!(!keys_a.contains("self_model:trait:curious"));
    assert!(!keys_a.contains("self_model:habit:skip-lunch"));
    assert!(!keys_a.contains("self_model:trait:other-ws"));

    let pending_a = pending_synthesis(&doc, Some("ws-a"), now);
    let pending_ids: Vec<&str> = pending_a
        .iter()
        .filter_map(|item| item.get("id").and_then(Value::as_str))
        .collect();
    assert_eq!(pending_ids, vec!["rev-pending", "rev-snoozed-expired"]);
}
