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

//! The Self-Model's write side — native (v11.7.0).
//!
//! Port of the write half of `lattice_brain/self_model.py`, plus
//! `synthesis._stamp_resolution`. Until v11.7.0 these five operations were
//! posted to `POST /worker/graph/mutate`; the Python worker stopped serving
//! that door in v11.6.0, so **every one of them answered 404 on a live
//! install** — a Self-Model fact could not be added, corrected, proposed,
//! approved or deleted, and a resolved contradiction always reported
//! `stamps: []`. Nothing was lost, because nothing was written.
//!
//! The governance shape is Python's, unchanged:
//!
//! * **Extraction never writes.** [`propose`] reads text and raises one review
//!   proposal per candidate through the same desk synthesis uses
//!   ([`crate::brain_api::desk`]).
//! * **[`apply`] is the only path from a proposal to a node**, and it writes
//!   nothing until the approve has returned.
//! * **The user writes directly.** [`upsert`] and [`delete`] are
//!   user-initiated edits: ownership means a person can correct their own
//!   profile without asking a queue for permission.
//! * **Deterministic.** The extractor is a table of first-person patterns; the
//!   same text always yields the same candidates in the same order.
//!
//! Refusals carry `SelfModelError.code` in the detail, which is what
//! `self_model::seam_error` reads to pick the catalog sentence — the codes are
//! the contract, not the English text beside them.

use std::collections::{BTreeMap, BTreeSet};

use fancy_regex::Regex;
use lattice_core::graph_write::types::{EdgeSpec, NodeSpec};
use lattice_core::graph_write::GraphWriter;
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};

use crate::brain_api::desk;
use crate::memory_api::shared::{BrainState, SeamRefusal};
use crate::self_model::{kind_label, SELF_ID_PREFIX, SELF_ROOT_ID};

/// `SELF_MODEL_KIND` — the review-queue kind a candidate fact is filed under.
pub const SELF_MODEL_KIND: &str = "self_model_fact";

/// `_MAX_FACT_CHARS`.
const MAX_FACT_CHARS: usize = 120;

/// `_TRAILING` — stripped from **both** ends, as Python's `str.strip(chars)` does.
const TRAILING: &str = " .。!?！？,、;:\n\t\"'\u{201c}\u{201d}\u{2018}\u{2019}()[]{}";

/// `CONTRADICTION_RESOLUTIONS`, in `_stamp_resolution`'s branch order.
const KEEP_OLD: &str = "keep_old";
const REPLACE: &str = "replace";

/// `FACT_NODE_TYPES` — fact kind → the graph node type it becomes.
///
/// `Decision` is the existing node type, deliberately reused.
pub fn node_type_for(kind: &str) -> Option<&'static str> {
    match kind {
        "preference" => Some("Preference"),
        "decision" => Some("Decision"),
        "habit" => Some("Habit"),
        "relationship" => Some("Relationship"),
        "trait" => Some("Self"),
        _ => None,
    }
}

/// `_normalize` — collapse whitespace, strip the trailing set, cap at 120
/// **characters** (Python slices code points, not bytes).
pub fn normalize(text: &str) -> String {
    let collapsed = text.split_whitespace().collect::<Vec<_>>().join(" ");
    let trimmed = collapsed.trim_matches(|c| TRAILING.contains(c));
    trimmed.chars().take(MAX_FACT_CHARS).collect()
}

/// `fact_id` — the same statement always lands on the same node.
pub fn fact_id(kind: &str, text: &str) -> String {
    let digest = Sha256::digest(format!("{kind}|{}", text.to_lowercase()).as_bytes());
    let hex: String = digest.iter().map(|byte| format!("{byte:02x}")).collect();
    format!("{SELF_ID_PREFIX}{kind}:{}", &hex[..12])
}

/// `_PATTERNS` — `(kind, pattern, confidence, signal)`.
///
/// The order fixes the order candidates are produced in, and every capture is
/// the named group `v`. `(?i)` is Python's `re.IGNORECASE`.
fn patterns() -> &'static [(&'static str, Regex, f64, &'static str)] {
    static TABLE: std::sync::OnceLock<Vec<(&'static str, Regex, f64, &'static str)>> =
        std::sync::OnceLock::new();
    TABLE.get_or_init(|| {
        let rows: [(&'static str, &'static str, f64, &'static str); 12] = [
            (
                "decision",
                r"결정\s*[:：]\s*(?P<v>[^\n]+)",
                0.9,
                "ko_decision_marker",
            ),
            (
                "decision",
                r"(?P<v>[^\n.。]+?)\s*하기로\s*(?:했|결정했)",
                0.75,
                "ko_decided_to",
            ),
            (
                "decision",
                r"(?i)\bDecision\s*:\s*(?P<v>[^\n]+)",
                0.9,
                "en_decision_marker",
            ),
            (
                "decision",
                r"(?i)\b(?:I|we)\s+decided\s+to\s+(?P<v>[^\n.!?]+)",
                0.75,
                "en_decided_to",
            ),
            (
                "preference",
                r"(?:나는|저는|내가|제가)\s*(?P<v>[^\n.。]+?)\s*(?:을|를|이|가)?\s*(?:좋아|선호|싫어)(?:합니다|한다|해요|해|하고)",
                0.7,
                "ko_first_person_preference",
            ),
            (
                "preference",
                r"(?i)\bI\s+(?:prefer|like|love|hate|dislike|avoid)\s+(?P<v>[^\n.!?]+)",
                0.7,
                "en_first_person_preference",
            ),
            (
                // The frequency word stays inside the capture: "회고를 씁니다"
                // is a sentence, "매일 회고를 씁니다" is a habit.
                "habit",
                r"(?P<v>(?:매일|매주|매달|아침마다|항상|늘)\s*[^\n.。!?]+)",
                0.65,
                "ko_routine",
            ),
            (
                "habit",
                r"(?i)\bI\s+(?P<v>(?:always|usually|every\s+(?:morning|day|week))\s+[^\n.!?]+)",
                0.65,
                "en_routine",
            ),
            (
                "relationship",
                r"(?:내|제)\s*(?P<v>(?:동료|팀장|매니저|친구|파트너|상사|멘토)\s+\S+)",
                0.6,
                "ko_relationship",
            ),
            (
                "relationship",
                r"(?i)\bmy\s+(?P<v>(?:colleague|manager|teammate|friend|partner|mentor|boss)\s+[^\n.!?,]+)",
                0.6,
                "en_relationship",
            ),
            (
                "trait",
                r"(?:나는|저는)\s*(?P<v>[^\n.。]*?(?:개발자|디자이너|엔지니어|연구원|학생|기획자))\s*(?:입니다|이다|다|예요)",
                0.6,
                "ko_role",
            ),
            (
                "trait",
                r"(?i)\bI\s+am\s+an?\s+(?P<v>[^\n.!?]+)",
                0.6,
                "en_role",
            ),
        ];
        rows.into_iter()
            .map(|(kind, source, confidence, signal)| {
                (
                    kind,
                    Regex::new(source).expect("self-model pattern compiles"),
                    confidence,
                    signal,
                )
            })
            .collect()
    })
}

/// `extract_self_model` — candidate facts found in `text`.
///
/// Pure and deterministic: deduplicated on the fact id (which is
/// `(kind, lowercased text)`), then ordered by `(kind, text)`. There is no
/// `refiner` here: the optional model pass Python allowed had no caller in the
/// Rust port and a model that can rewrite a candidate's wording is a feature,
/// not a port detail.
pub fn extract(text: &str, source: &str) -> Vec<Value> {
    let mut found: BTreeMap<String, Value> = BTreeMap::new();
    let mut order: Vec<String> = Vec::new();
    for (kind, pattern, confidence, signal) in patterns() {
        let mut cursor = 0usize;
        while cursor <= text.len() {
            let Ok(Some(captures)) = pattern.captures_from_pos(text, cursor) else {
                break;
            };
            let whole = captures.get(0).expect("group 0");
            cursor = if whole.end() > whole.start() {
                whole.end()
            } else {
                // A zero-width match cannot advance on its own; step one
                // character, the way `finditer` does.
                next_boundary(text, whole.end())
            };
            let Some(group) = captures.name("v") else {
                continue;
            };
            let value = normalize(group.as_str());
            if value.chars().count() < 2 {
                continue;
            }
            let identifier = fact_id(kind, &value);
            if found.contains_key(&identifier) {
                continue;
            }
            order.push(identifier.clone());
            found.insert(
                identifier.clone(),
                json!({
                    "id": identifier,
                    "kind": kind,
                    "text": value,
                    "confidence": confidence,
                    "signal": signal,
                    "source": source,
                }),
            );
        }
    }
    let mut candidates: Vec<Value> = order
        .iter()
        .filter_map(|id| found.get(id).cloned())
        .collect();
    candidates.sort_by(|left, right| {
        let key = |fact: &Value| {
            (
                fact.get("kind")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .to_string(),
                fact.get("text")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .to_string(),
            )
        };
        key(left).cmp(&key(right))
    });
    candidates
}

fn next_boundary(text: &str, from: usize) -> usize {
    let mut index = from + 1;
    while index < text.len() && !text.is_char_boundary(index) {
        index += 1;
    }
    index
}

/// `_write_fact` — the root (if needed), the fact node, and the `PART_OF` edge.
fn write_fact(
    graph: &GraphWriter,
    kind: &str,
    text: &str,
    workspace_id: Option<&str>,
    origin: &str,
    confidence: f64,
    signal: &str,
    item_id: Option<&str>,
) -> Result<Value, SeamRefusal> {
    let Some(node_type) = node_type_for(kind) else {
        return Err(refusal(400, "invalid_kind"));
    };
    let node_id = fact_id(kind, text);
    let metadata = json!({
        "self_model": true,
        "self_model_kind": kind,
        "origin": origin,
        "confidence": confidence,
        "signal": signal,
        "workspace_id": workspace_id,
        "review_item_id": item_id,
    });
    let root = NodeSpec {
        id: SELF_ROOT_ID.to_string(),
        node_type: "Self".to_string(),
        title: "나".to_string(),
        summary: "Brain이 사용자에 대해 알고 있는 사실의 뿌리입니다.".to_string(),
        metadata: object_of(json!({"self_model": true, "self_model_kind": "root"})),
        raw: Map::new(),
        owner: None,
        workspace_id: workspace_id.map(str::to_string),
        visibility: None,
    };
    let fact = NodeSpec {
        id: node_id.clone(),
        node_type: node_type.to_string(),
        title: text.to_string(),
        summary: text.to_string(),
        metadata: object_of(metadata),
        raw: Map::new(),
        owner: None,
        workspace_id: workspace_id.map(str::to_string),
        visibility: None,
    };
    graph.upsert_nodes(&[root, fact]).map_err(internal)?;
    graph
        .upsert_edges(&[EdgeSpec {
            from_node: node_id.clone(),
            to_node: SELF_ROOT_ID.to_string(),
            edge_type: "PART_OF".to_string(),
            weight: 1.0,
            metadata: Map::new(),
            legacy_label: None,
        }])
        .map_err(internal)?;
    Ok(json!({
        "id": node_id,
        "kind": kind,
        "type": node_type,
        "text": text,
        "origin": origin,
        "confidence": confidence,
        "signal": signal,
        "workspace_id": workspace_id,
    }))
}

fn object_of(value: Value) -> Map<String, Value> {
    value.as_object().cloned().unwrap_or_default()
}

/// A refusal whose detail **is** `SelfModelError.code`, and nothing else.
///
/// Two readers, and they disagree about how much slack they allow:
/// `self_model::seam_error` looks for a code *inside* the detail, but
/// `routes::self_model_apply` calls `self_model::message_id` on the whole
/// string — an exact match — to tell "this item is not a proposal" from "there
/// is no such item". A detail of `"not_a_proposal: review item … is not a
/// Self-Model proposal"` satisfies the first and fails the second, and the
/// route then answers 404 about a review item that exists. So the code travels
/// alone; the sentence a person reads comes from the message catalog, in their
/// language, which is why Brain Core raised codes rather than prose.
fn refusal(status: u16, code: &str) -> SeamRefusal {
    SeamRefusal {
        status,
        detail: code.to_string(),
    }
}

/// A storage failure, which is not one of the codes.
///
/// Deliberately prefixed with `self_model`: `routes::self_model_apply` reads a
/// code-less detail as "the review item is not there" *unless* it names the
/// family, and reporting a disk error as a missing item would send someone
/// looking in the wrong place.
fn internal(error: impl std::fmt::Display) -> SeamRefusal {
    SeamRefusal {
        status: 500,
        detail: format!("self_model write failed: {error}"),
    }
}

/// `upsert_self_model_fact` — add or correct one fact directly.
fn upsert(graph: &GraphWriter, args: &Value) -> Result<Value, SeamRefusal> {
    let kind = args
        .get("kind")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .trim()
        .to_lowercase();
    let value = normalize(args.get("text").and_then(Value::as_str).unwrap_or_default());
    if node_type_for(&kind).is_none() {
        return Err(refusal(400, "invalid_kind"));
    }
    if value.is_empty() {
        return Err(refusal(400, "text_required"));
    }
    write_fact(
        graph,
        &kind,
        &value,
        args.get("workspace_id").and_then(Value::as_str),
        "user",
        1.0,
        "user_edit",
        None,
    )
}

/// `delete_self_model_fact` — remove one fact permanently.
fn delete(graph: &GraphWriter, args: &Value, now: &str) -> Result<Value, SeamRefusal> {
    let node_id = args
        .get("node_id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .trim()
        .to_string();
    if !node_id.starts_with(SELF_ID_PREFIX) {
        return Err(refusal(400, "not_self_model"));
    }
    let deleted = graph.delete_node(&node_id).map_err(internal)?;
    if !deleted {
        return Err(refusal(404, "not_found"));
    }
    Ok(json!({"status": "ok", "id": node_id, "deleted_at": now}))
}

/// `_stamp_resolution` — the validity stamps one contradiction resolution writes.
fn stamp_contradiction(graph: &GraphWriter, args: &Value) -> Result<Value, SeamRefusal> {
    let older = args
        .get("older_id")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let newer = args
        .get("newer_id")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let moment = args.get("at").and_then(Value::as_str).unwrap_or_default();
    let resolution = args
        .get("resolution")
        .and_then(Value::as_str)
        .unwrap_or_default();
    // `(node_id, valid_from, valid_to, superseded_by)` — the caller validated
    // `resolution`, so the third arm is `keep_both_temporal`.
    let plan: Vec<(&str, Option<&str>, Option<&str>, Option<&str>)> = match resolution {
        KEEP_OLD => vec![(newer, None, Some(moment), Some(older))],
        REPLACE => vec![
            (older, None, Some(moment), Some(newer)),
            (newer, Some(moment), None, None),
        ],
        _ => vec![
            (older, None, Some(moment), None),
            (newer, Some(moment), None, None),
        ],
    };
    let mut applied: Vec<Value> = Vec::new();
    for (node_id, valid_from, valid_to, superseded_by) in plan {
        let updated = graph
            .stamp_node_validity(node_id, valid_from, valid_to, superseded_by)
            .map_err(internal)?;
        // Key order is Python's: `node_id`, then the fields this arm supplied,
        // then `updated`.
        let mut stamp = Map::new();
        stamp.insert("node_id".into(), json!(node_id));
        if let Some(value) = valid_to {
            stamp.insert("valid_to".into(), json!(value));
        }
        if let Some(value) = superseded_by {
            stamp.insert("superseded_by".into(), json!(value));
        }
        if let Some(value) = valid_from {
            stamp.insert("valid_from".into(), json!(value));
        }
        stamp.insert("updated".into(), json!(updated));
        applied.push(Value::Object(stamp));
    }
    Ok(Value::Array(applied))
}

/// `propose_self_model` — one review proposal per newly-noticed fact.
async fn propose(state: &BrainState, args: &Value) -> Result<Value, SeamRefusal> {
    let text = args.get("text").and_then(Value::as_str).unwrap_or_default();
    let source = args
        .get("source")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let user_email = args
        .get("user_email")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string();
    let workspace_id = args
        .get("workspace_id")
        .and_then(Value::as_str)
        .map(str::to_string);
    let max_proposals = args
        .get("max_proposals")
        .and_then(Value::as_i64)
        .unwrap_or(5)
        .max(0) as usize;
    let candidates = extract(text, source);

    // Facts already in the subgraph are never proposed again.
    let scope: Option<BTreeSet<String>> = workspace_id
        .clone()
        .map(|workspace| [workspace].into_iter().collect());
    let known: BTreeSet<String> = state
        .read(move |conn| crate::memory_api::self_model::read_facts(conn, scope.as_ref()))
        .await
        .map(|facts| {
            facts
                .iter()
                .filter_map(|fact| fact.get("id").and_then(Value::as_str).map(str::to_string))
                .collect()
        })
        .unwrap_or_default();
    let fresh: Vec<&Value> = candidates
        .iter()
        .filter(|fact| !known.contains(fact.get("id").and_then(Value::as_str).unwrap_or_default()))
        .collect();

    // The desk reads and writes `workspace_os.json` (and, with an owner
    // installed, its SQLite row), so the whole `ProposalDesk` loop runs off the
    // reactor — a proposal raised on the request thread would stall every other
    // request behind a file write (v10.9.0).
    let fresh_facts: Vec<Value> = fresh.iter().map(|fact| (*fact).clone()).collect();
    let already_known = candidates.len() - fresh.len();
    let desk_state = state.clone();
    let desk_workspace = workspace_id.clone();
    let (proposed, suppressed) = tokio::task::spawn_blocking(move || {
        let workspace = desk_workspace.as_deref();
        let mut open = desk::open_keys(&desk_state, workspace);
        let mut proposed: Vec<Value> = Vec::new();
        let mut suppressed = 0i64;
        for fact in fresh_facts.iter().take(max_proposals) {
            let key = fact
                .get("id")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_string();
            let value = fact.get("text").and_then(Value::as_str).unwrap_or_default();
            let kind = fact.get("kind").and_then(Value::as_str).unwrap_or_default();
            if open.contains(&key) {
                suppressed += 1;
                continue;
            }
            let summary = format!(
                "대화에서 '{value}'를 읽었습니다. 내 프로필({})에 추가할까요? \
                 승인하기 전에는 저장되지 않습니다.",
                kind_label(kind)
            );
            let payload = json!({
                "fact": fact.clone(),
                "node_type": node_type_for(kind).unwrap_or_default(),
            });
            match desk::create_review(
                &desk_state,
                &format!("나에 대한 새 사실: {value}"),
                &summary,
                SELF_MODEL_KIND,
                &key,
                payload,
                &user_email,
                workspace,
            ) {
                Some(item) => {
                    open.insert(key);
                    proposed.push(item);
                }
                None => suppressed += 1,
            }
        }
        (proposed, suppressed)
    })
    .await
    .map_err(|error| SeamRefusal {
        status: 500,
        detail: error.to_string(),
    })?;
    Ok(json!({
        "available": true,
        "candidates": candidates,
        "candidate_count": candidates.len(),
        "already_known": already_known,
        "proposed": proposed,
        "proposed_count": proposed.len(),
        "suppressed": suppressed,
        "generated_at": state.now(),
    }))
}

/// `apply_self_model_proposal` — approve one proposal, then write its fact.
fn apply(state: &BrainState, graph: &GraphWriter, args: &Value) -> Result<Value, SeamRefusal> {
    let item_id = args
        .get("item_id")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let workspace_id = args.get("workspace_id").and_then(Value::as_str);
    // `except (KeyError, FileNotFoundError)` in the router turns a detail that
    // names no code into "review item not found", so an absent item reports
    // only its id.
    let Some(item) = desk::load_review_item(state, item_id, workspace_id) else {
        return Err(SeamRefusal {
            status: 404,
            detail: item_id.to_string(),
        });
    };
    if item.get("kind").and_then(Value::as_str) != Some(SELF_MODEL_KIND) {
        return Err(refusal(400, "not_a_proposal"));
    }
    let fact = item
        .get("payload")
        .and_then(|payload| payload.get("fact"))
        .cloned()
        .unwrap_or(Value::Null);
    let kind = fact
        .get("kind")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string();
    let text = normalize(fact.get("text").and_then(Value::as_str).unwrap_or_default());
    if node_type_for(&kind).is_none() || text.is_empty() {
        return Err(refusal(400, "empty_proposal"));
    }
    // Gated twice, the way `resolve_contradiction` is: nothing is written
    // until the approve has returned.
    let status = desk::approve_item(state, item_id);
    let workspace = workspace_id.or_else(|| item.get("workspace_id").and_then(Value::as_str));
    let node = write_fact(
        graph,
        &kind,
        &text,
        workspace,
        "proposal",
        fact.get("confidence")
            .and_then(Value::as_f64)
            .unwrap_or(0.0),
        fact.get("signal")
            .and_then(Value::as_str)
            .unwrap_or_default(),
        Some(item_id),
    )?;
    Ok(json!({
        "item_id": item_id,
        "status": status,
        "fact": node,
        "applied_at": state.now(),
    }))
}

/// Run one Self-Model / contradiction write on the native engine.
///
/// The SQLite halves run on `spawn_blocking`; `propose` is already async
/// because it reads the subgraph through [`BrainState::read`] and writes review
/// items through the desk.
pub async fn dispatch(
    state: &BrainState,
    graph: &GraphWriter,
    op: &str,
    args: Value,
) -> Result<Value, SeamRefusal> {
    let now = state.now();
    match op {
        "self_model_propose" => propose(state, &args).await,
        "self_model_apply" => {
            let state = state.clone();
            let graph = graph.clone();
            blocking(move || apply(&state, &graph, &args)).await
        }
        "self_model_upsert" => {
            let graph = graph.clone();
            blocking(move || upsert(&graph, &args)).await
        }
        "self_model_delete" => {
            let graph = graph.clone();
            blocking(move || delete(&graph, &args, &now)).await
        }
        "stamp_contradiction" => {
            let graph = graph.clone();
            blocking(move || stamp_contradiction(&graph, &args)).await
        }
        other => Err(SeamRefusal {
            status: 400,
            detail: format!("graph mutation op not allowed: {other}"),
        }),
    }
}

async fn blocking<F>(work: F) -> Result<Value, SeamRefusal>
where
    F: FnOnce() -> Result<Value, SeamRefusal> + Send + 'static,
{
    match tokio::task::spawn_blocking(work).await {
        Ok(result) => result,
        Err(error) => Err(SeamRefusal {
            status: 500,
            detail: error.to_string(),
        }),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalize_is_pythons_two_strips_and_a_character_cap() {
        // `_WHITESPACE.sub(" ", …).strip().strip(_TRAILING)`: the trailing set
        // is stripped from **both** ends, and it contains a space, so the two
        // strips compose.
        assert_eq!(
            normalize("  파이썬을   좋아합니다.  "),
            "파이썬을 좋아합니다"
        );
        assert_eq!(normalize("(quoted)"), "quoted");
        assert_eq!(normalize("...."), "");
        assert_eq!(normalize("a\n\tb"), "a b");
        // The cap counts code points, not bytes: 200 Korean characters are 600
        // bytes, and a byte slice would land mid-character.
        let long = "가".repeat(200);
        assert_eq!(normalize(&long).chars().count(), MAX_FACT_CHARS);
    }

    #[test]
    fn a_fact_id_is_the_statement_not_the_wording() {
        // `sha256(f"{kind}|{text.lower()}")[:12]`, so case does not matter and
        // the same statement under two kinds is two facts.
        assert_eq!(
            fact_id("preference", "답변은 한국어로 받고 싶습니다"),
            "self:preference:e28d88323aff"
        );
        assert_eq!(
            fact_id("decision", "Keep Alpha Fusion"),
            fact_id("decision", "keep alpha fusion")
        );
        assert_ne!(fact_id("decision", "x"), fact_id("habit", "x"));
    }

    #[test]
    fn extraction_is_deterministic_deduplicated_and_ordered() {
        let text = "저는 파이썬을 좋아합니다. 매일 아침 회의록을 정리합니다. \
                    I decided to keep alpha fusion.";
        let first = extract(text, "fixture");
        assert_eq!(first, extract(text, "fixture"), "the same text twice");
        let seen: Vec<(&str, &str)> = first
            .iter()
            .map(|fact| {
                (
                    fact["kind"].as_str().unwrap_or_default(),
                    fact["signal"].as_str().unwrap_or_default(),
                )
            })
            .collect();
        // Sorted by (kind, text), which is Python's `sorted(...)` key.
        assert_eq!(
            seen,
            vec![
                ("decision", "en_decided_to"),
                ("habit", "ko_routine"),
                ("preference", "ko_first_person_preference"),
            ]
        );
        assert_eq!(first[2]["text"], json!("파이썬"));
        assert_eq!(first[2]["source"], json!("fixture"));
        // One statement said twice is one candidate.
        let twice = extract("I decided to ship it. I decided to ship it.", "");
        assert_eq!(twice.len(), 1, "{twice:?}");
        // Nothing first-person is nothing at all — no fabricated profile.
        assert!(extract("The build finished.", "").is_empty());
        // A capture under two characters is dropped (`len(value) < 2`).
        assert!(extract("Decision: x", "").is_empty());
    }

    #[test]
    fn every_kind_has_a_node_type_and_nothing_else_does() {
        for kind in crate::self_model::KIND_ORDER {
            assert!(node_type_for(kind).is_some(), "{kind}");
            assert!(!kind_label(kind).is_empty(), "{kind}");
        }
        assert!(node_type_for("Preference").is_none(), "kinds are lowercase");
        assert!(node_type_for("").is_none());
    }

    #[test]
    fn a_refusal_carries_the_code_the_router_reads() {
        // `self_model::seam_error` picks the catalog sentence by looking for
        // the code *inside* the detail, which is how the Python worker
        // reported `SelfModelError.code`. If this format changes, every
        // Self-Model refusal silently becomes a 500.
        let refused = refusal(400, "invalid_kind");
        assert_eq!(refused.detail, "invalid_kind", "the code travels alone");
        assert_eq!(
            crate::memory_api::self_model::seam_error(&refused.detail, "ko").status(),
            400
        );
        let missing = refusal(404, "not_found");
        assert_eq!(
            crate::memory_api::self_model::seam_error(&missing.detail, "ko").status(),
            404
        );
    }
}
