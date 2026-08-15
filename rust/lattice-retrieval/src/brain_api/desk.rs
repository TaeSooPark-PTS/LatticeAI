#![allow(
    dead_code,
    unused_imports,
    unused_variables,
    unused_assignments,
    unused_mut,
    private_interfaces,
    clippy::result_large_err,
    clippy::needless_lifetimes,
    clippy::too_many_arguments,
    clippy::type_complexity,
    clippy::collapsible_if,
    clippy::needless_as_bytes,
    clippy::redundant_closure,
    clippy::needless_return,
    clippy::manual_clamp,
    clippy::ptr_arg,
    clippy::unnecessary_sort_by,
    clippy::result_unit_err,
    clippy::useless_vec,
    clippy::uninlined_format_args,
    clippy::manual_contains,
    clippy::needless_borrows_for_generic_args,
    clippy::implicit_clone,
    clippy::unnecessary_map_or,
    clippy::match_like_matches_macro,
    clippy::manual_range_contains,
    clippy::derivable_impls,
    clippy::needless_pass_by_ref_mut,
    clippy::redundant_guards,
    clippy::map_identity,
    clippy::iter_overeager_cloned,
    clippy::explicit_auto_deref,
    clippy::bool_comparison,
    clippy::nonminimal_bool,
    clippy::if_same_then_else,
    clippy::question_mark,
    clippy::single_char_pattern,
    clippy::manual_pattern_char_comparison,
    clippy::manual_is_ascii_check,
    clippy::repeat_once,
    clippy::unused_self,
    clippy::module_inception
)]

//! The proposal desk: the one writer of `review_items` in this crate.
//!
//! Port of `lattice_brain.synthesis.ProposalDesk` plus the two review-queue
//! calls it wraps (`create` and `approve`). It lives beside `proposals.rs`
//! rather than inside it because the Self-Model write path
//! (`memory_api::self_model_write`) raises proposals through the same door —
//! two desks over one document would be two id generators and two funnels.
//!
//! Three properties this module is responsible for:
//!
//! * **Atomic.** Every write is one [`wsos::mutate_state`] closure, so the
//!   load and the save happen under the owner's lock. The earlier shape
//!   (`load` → mutate → `save_state`) took the lock only for the write half,
//!   which is exactly wide enough to lose a concurrent review action.
//! * **Funnelled.** Every write records a `review_item_created` /
//!   `review_item_updated` timeline event afterwards, the way the Review
//!   Center's own writers do (`review_queue::store`), so a synthesis or
//!   Self-Model proposal is visible to the same readers as a hand-raised one.
//! * **Silent when nothing changed.** A refused create and an approve of an
//!   item that is not there write nothing and record nothing.

use std::collections::BTreeSet;

use serde_json::{json, Value};

use crate::memory_api::shared::BrainState;
use crate::memory_api::wsos;

use super::pyutil;

/// `SYNTHESIS_REVIEW_SOURCE` — every proposal this crate raises carries it.
pub const SYNTHESIS_SOURCE: &str = "kg_change_digest";

/// `ProposalDesk.open_keys` — proposal keys still awaiting a decision.
pub fn open_keys(state: &BrainState, workspace_id: Option<&str>) -> BTreeSet<String> {
    let doc = wsos::load(state.store(), state.data_dir());
    pending_synthesis(&doc, workspace_id)
        .iter()
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

/// Pending proposals this crate raised, scoped to one workspace.
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

/// `ProposalDesk.propose` → `review_queue.create` — one new review item.
///
/// The id is `review-<16 hex of sha256([title, source, kind, user, now])>`,
/// with a numeric suffix when that collides; the uniqueness scan and the
/// append happen inside one `mutate_state` closure, so two proposals raised
/// concurrently cannot be handed the same id or erase one another.
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

/// `review_queue.approve` — mark one item approved, and say so on the timeline.
///
/// Returns the item's status afterwards (`""` when there was no such item, the
/// way Python's `str(approved.get("status") or "")` reads an absent answer).
/// An id nothing matches writes nothing and records nothing.
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

/// One review item by id, scoped — `review_queue.get`.
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
