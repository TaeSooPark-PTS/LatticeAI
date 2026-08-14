//! `CommandCenterService.search` — the Cmd+K answer, across three surfaces.
//!
//! ## The knowledge group can never fill, and that is the captured behaviour
//!
//! `_search_knowledge` calls `SearchService.keyword_search(...)` and then reads
//! `payload.get("results")`. That service answers `{"query", "mode",
//! "matches"}` — there is no `results` key and there never has been — so the
//! knowledge group is `[]` on every query, and `search()` drops it because it
//! only keeps groups whose `items` are non-empty. `memory_brain.json`'s
//! `search` and `search_korean` cases record exactly that: `groups: []` for a
//! query the graph *does* know about.
//!
//! This port answers the same empty list, and does **not** perform the discarded
//! keyword search: the call has no side effect, its result is thrown away on
//! every branch (including the `except`), so running it would only spend a graph
//! read to produce `[]`. If Python ever fixes the key, this comment is where the
//! port has to change — reinstate `crate::service::keyword_search` here and take
//! its `matches`, and regenerate the two fixture cases, because the answer they
//! pin will change.

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
use lattice_auth::OrderedMap;
use rusqlite::Connection;
use serde_json::Value;

use super::store::{clip, py_text};
use crate::history::{self, HistoryScope};

/// `_SEARCH_HISTORY_LIMIT`.
const SEARCH_HISTORY_LIMIT: i64 = 2000;

/// One `GET /api/command/search` call, after query validation.
pub(crate) struct SearchRequest<'a> {
    /// `str(query or "").strip()`, already applied.
    pub(crate) query: &'a str,
    pub(crate) user_email: &'a str,
    pub(crate) workspace_id: Option<&'a str>,
    /// The Workspace OS state document, already loaded.
    pub(crate) state: &'a Value,
    /// `max(1, min(int(limit or 8), 20))`, already applied.
    pub(crate) limit: i64,
}

/// The three groups, and the total across all of them.
///
/// `total` sums every group including the ones `search()` drops for being
/// empty — which, since an empty group contributes zero, is the same number.
pub(crate) fn groups(conn: &Connection, request: &SearchRequest<'_>) -> (Vec<Value>, i64) {
    let built = [
        ("knowledge", knowledge()),
        ("conversation", conversations(conn, request)),
        ("automation", automations(request)),
    ];
    let total: i64 = built.iter().map(|(_, items)| items.len() as i64).sum();
    let kept = built
        .into_iter()
        .filter(|(_, items)| !items.is_empty())
        .map(|(kind, items)| {
            let mut group = OrderedMap::new();
            group.insert("kind", Value::String(kind.to_string()));
            group.insert("items", Value::Array(items));
            serde_json::to_value(&group).unwrap_or(Value::Null)
        })
        .collect();
    (kept, total)
}

/// `_search_knowledge` — see this module's header for why it is always empty.
fn knowledge() -> Vec<Value> {
    Vec::new()
}

/// `_search_conversations` — newest first, one hit per conversation.
fn conversations(conn: &Connection, request: &SearchRequest<'_>) -> Vec<Value> {
    let needle = request.query.to_lowercase();
    let scope = HistoryScope {
        user_email: Some(request.user_email.to_string()),
        allowed_workspaces: request.workspace_id.map(|id| vec![id.to_string()]),
        include_legacy_global: request.workspace_id.is_none(),
    };
    let Ok(items) = history::history(conn, None, Some(SEARCH_HISTORY_LIMIT), &scope) else {
        // `_history` logs and answers `[]`; the briefing never breaks on a
        // conversation store that cannot be read.
        return Vec::new();
    };
    let mut seen: Vec<String> = Vec::new();
    let mut matches: Vec<Value> = Vec::new();
    for item in items.iter().rev() {
        let content = py_text(item.get("content"));
        if !content.to_lowercase().contains(&needle) {
            continue;
        }
        let conversation_id = py_text(item.get("conversation_id"));
        if !conversation_id.is_empty() {
            if seen.contains(&conversation_id) {
                continue;
            }
            seen.push(conversation_id.clone());
        }
        let mut hit = OrderedMap::new();
        hit.insert("conversation_id", Value::String(conversation_id));
        hit.insert("role", item.get("role").cloned().unwrap_or(Value::Null));
        hit.insert("snippet", Value::String(clip(&Value::String(content), 140)));
        hit.insert("timestamp", Value::String(py_text(item.get("timestamp"))));
        matches.push(serde_json::to_value(&hit).unwrap_or(Value::Null));
        if matches.len() as i64 >= request.limit {
            break;
        }
    }
    matches
}

/// `_search_automations` — a substring scan over installed workflow names.
fn automations(request: &SearchRequest<'_>) -> Vec<Value> {
    let needle = request.query.to_lowercase();
    let mut matches: Vec<Value> = Vec::new();
    for workflow in super::store::workflows(request.state, request.workspace_id) {
        let name = py_text(workflow.get("name"));
        if !name.to_lowercase().contains(&needle) {
            continue;
        }
        let state = workflow
            .get("metadata")
            .and_then(|metadata| metadata.get("automation_state"))
            .and_then(Value::as_str);
        let mut hit = OrderedMap::new();
        hit.insert("id", workflow.get("id").cloned().unwrap_or(Value::Null));
        hit.insert("name", Value::String(clip(&Value::String(name), 120)));
        hit.insert("enabled", Value::Bool(state == Some("enabled")));
        matches.push(serde_json::to_value(&hit).unwrap_or(Value::Null));
        if matches.len() as i64 >= request.limit {
            break;
        }
    }
    matches
}

/// The whole answer body, in Python's key order.
pub(crate) fn body(
    request: &SearchRequest<'_>,
    kept: Vec<Value>,
    total: i64,
    now: &str,
) -> OrderedMap {
    let mut body = OrderedMap::new();
    body.insert("query", Value::String(request.query.to_string()));
    body.insert("groups", Value::Array(kept));
    body.insert("total", Value::from(total));
    body.insert("generated_at", Value::String(now.to_string()));
    body
}

/// The short answer an empty query gets: no `total` key at all.
pub(crate) fn empty_body(now: &str) -> OrderedMap {
    let mut body = OrderedMap::new();
    body.insert("query", Value::String(String::new()));
    body.insert("groups", Value::Array(Vec::new()));
    body.insert("generated_at", Value::String(now.to_string()));
    body
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn state() -> Value {
        json!({"workflows": [
            {"id": "w1", "name": "Daily digest", "metadata": {"automation_state": "enabled"}},
            {"id": "w2", "name": "Weekly DIGEST review", "metadata": {"automation_state": "draft_disabled"}},
            {"id": "w3", "name": "Unrelated"},
        ]})
    }

    fn request<'a>(query: &'a str, doc: &'a Value, limit: i64) -> SearchRequest<'a> {
        SearchRequest {
            query,
            user_email: "",
            workspace_id: None,
            state: doc,
            limit,
        }
    }

    #[test]
    fn the_automation_scan_is_case_insensitive_and_respects_the_limit() {
        let doc = state();
        let hits = automations(&request("digest", &doc, 8));
        assert_eq!(hits.len(), 2);
        // `list_workflows` reverses, so w2 comes first.
        assert_eq!(hits[0]["id"], "w2");
        assert_eq!(hits[0]["enabled"], false);
        assert_eq!(hits[1]["id"], "w1");
        assert_eq!(hits[1]["enabled"], true);
        assert_eq!(automations(&request("digest", &doc, 1)).len(), 1);
    }

    #[test]
    fn an_empty_query_matches_every_name_because_python_substrings_do() {
        let doc = state();
        assert_eq!(
            automations(&request("", &doc, 8)).len(),
            3,
            "\"\" in name is True; the caller is what stops an empty query earlier"
        );
    }

    #[test]
    fn the_knowledge_group_is_empty_by_record() {
        assert!(knowledge().is_empty());
    }

    #[test]
    fn the_empty_query_body_carries_no_total() {
        let body = empty_body("2026-08-14T00:00:00");
        assert_eq!(body.get("query"), Some(&json!("")));
        assert_eq!(body.get("groups"), Some(&json!([])));
        assert!(
            !body.contains_key("total"),
            "search() returns three keys here"
        );
        let keys: Vec<&str> = body.iter().map(|(key, _)| key).collect();
        assert_eq!(keys, vec!["query", "groups", "generated_at"]);
    }

    #[test]
    fn a_full_body_keeps_pythons_key_order() {
        let doc = state();
        let body = body(
            &request("digest", &doc, 8),
            Vec::new(),
            2,
            "2026-08-14T00:00:00",
        );
        let keys: Vec<&str> = body.iter().map(|(key, _)| key).collect();
        assert_eq!(keys, vec!["query", "groups", "total", "generated_at"]);
    }
}
