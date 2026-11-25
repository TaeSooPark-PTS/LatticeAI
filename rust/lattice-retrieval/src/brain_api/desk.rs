use std::collections::BTreeSet;

use serde_json::{json, Value};

use crate::memory_api::shared::BrainState;
use crate::memory_api::wsos;

use super::pyutil;

pub const SYNTHESIS_SOURCE: &str = "kg_change_digest";

pub fn open_keys(state: &BrainState, workspace_id: Option<&str>) -> BTreeSet<String> {
    let doc = wsos::load(state.store(), state.data_dir());
    wsos::scoped(
        doc.get("review_items")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default(),
        workspace_id,
    )
    .into_iter()
    .filter(|item| item.get("source").and_then(Value::as_str) == Some(SYNTHESIS_SOURCE))
    .filter(|item| {
        let status = item
            .get("effective_status")
            .or_else(|| item.get("status"))
            .and_then(Value::as_str)
            .unwrap_or("pending");
        matches!(status, "pending" | "snoozed")
    })
    .filter_map(|item| {
        item.get("payload")
            .and_then(|p| p.get("proposal_key").or_else(|| p.get("key")))
            .and_then(Value::as_str)
            .map(str::to_string)
            .or_else(|| {
                item.get("kind")
                    .and_then(Value::as_str)
                    .map(|kind| format!("{kind}:{}", pyutil::text_of(item.get("title"))))
            })
    })
    .collect()
}

pub fn pending_synthesis(state: &Value, workspace_id: Option<&str>) -> Vec<Value> {
    wsos::scoped(
        state
            .get("review_items")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default(),
        workspace_id,
    )
    .into_iter()
    .filter(|item| item.get("source").and_then(Value::as_str) == Some(SYNTHESIS_SOURCE))
    .filter(|item| {
        let status = item
            .get("effective_status")
            .or_else(|| item.get("status"))
            .and_then(Value::as_str)
            .unwrap_or("pending");
        status == "pending"
    })
    .collect()
}

#[allow(clippy::too_many_arguments)]
pub fn create_review(
    state: &BrainState,
    title: &str,
    summary: &str,
    kind: &str,
    key: &str,
    mut payload: Value,
    user_email: &str,
    workspace_id: Option<&str>,
) -> Option<Value> {
    if title.trim().is_empty() {
        return None;
    }
    if let Some(object) = payload.as_object_mut() {
        object.insert("proposal_key".into(), json!(key));
        object.insert("summary_ko".into(), json!(summary));
    }
    let now = state.now();
    let workspace = workspace_id
        .filter(|v| !v.is_empty())
        .unwrap_or(wsos::DEFAULT_WORKSPACE_ID)
        .to_string();
    let seed = json!([title, SYNTHESIS_SOURCE, kind, user_email, now]);
    let digest = {
        use sha2::{Digest, Sha256};
        let mut hasher = Sha256::new();
        hasher.update(seed.to_string().as_bytes());
        let hex: String = hasher
            .finalize()
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect();
        hex.chars().take(16).collect::<String>()
    };
    let mut created: Option<Value> = None;
    let write = wsos::mutate_state(state.store(), state.data_dir(), |doc| {
        let existing: BTreeSet<String> = doc
            .get("review_items")
            .and_then(Value::as_array)
            .map(|items| {
                items
                    .iter()
                    .filter_map(|item| item.get("id").and_then(Value::as_str).map(str::to_string))
                    .collect()
            })
            .unwrap_or_default();
        let mut item_id = format!("review-{digest}");
        let mut seq = 0u32;
        while existing.contains(&item_id) {
            seq += 1;
            item_id = format!("review-{digest}{seq}");
        }
        let item = json!({
            "id": item_id,
            "status": "pending",
            "title": title,
            "summary": summary,
            "source": SYNTHESIS_SOURCE,
            "kind": kind,
            "payload": payload,
            "provenance": {"pipeline": "brain-synthesis", "proposal_key": key},
            "effective_status": "pending",
            "snoozed_until": null,
            "user_email": if user_email.is_empty() { Value::Null } else { Value::String(user_email.to_string()) },
            "workspace_id": workspace,
            "created_at": now,
            "updated_at": now,
        });
        if let Some(object) = doc.as_object_mut() {
            let items = object
                .entry("review_items")
                .or_insert_with(|| Value::Array(Vec::new()));
            if let Some(list) = items.as_array_mut() {
                list.push(item.clone());
                created = Some(item);
            }
        }
    });
    if write.is_err() {
        return None;
    }
    let item = created?;
    wsos::record_review_event(&item, "create");
    Some(item)
}

pub fn approve_item(state: &BrainState, item_id: &str) -> String {
    let now = state.now();
    let mut approved: Option<Value> = None;
    let write = wsos::mutate_state(state.store(), state.data_dir(), |doc| {
        let Some(items) = doc.get_mut("review_items").and_then(Value::as_array_mut) else {
            return;
        };
        for item in items {
            if item.get("id").and_then(Value::as_str) != Some(item_id) {
                continue;
            }
            if let Some(object) = item.as_object_mut() {
                object.insert("status".into(), Value::String("approved".into()));
                object.insert("updated_at".into(), Value::String(now.clone()));
            }
            approved = Some(item.clone());
        }
    });
    if write.is_err() {
        return String::new();
    }
    match approved {
        Some(item) => {
            wsos::record_review_event(&item, "approve");
            item.get("status")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_string()
        }
        None => String::new(),
    }
}

pub fn load_review_item(
    state: &BrainState,
    item_id: &str,
    workspace_id: Option<&str>,
) -> Option<Value> {
    let doc = wsos::load(state.store(), state.data_dir());
    wsos::scoped(
        doc.get("review_items")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default(),
        workspace_id,
    )
    .into_iter()
    .find(|row| row.get("id").and_then(Value::as_str) == Some(item_id))
}
