//! `SelfModelService` — what the Brain believes about its owner.
//!
//! This module is the **read** half. Every write in
//! `lattice_brain/self_model.py` ends in `store._upsert_node` / `_upsert_edge`
//! / `DELETE FROM nodes`; until v11.7.0 the four write routes delegated those
//! over `POST /worker/graph/mutate`, a door the worker stopped serving in
//! v11.6.0 — so they answered 404 on every live install. They are native now,
//! in [`super::self_model_write`].
//!
//! The read reuses [`crate::self_model`] for the injected summary — the same
//! function the document-generation context already assembles prompts with, so
//! the profile a person sees and the profile a model is handed cannot drift.
//! What is added here is `list_self_model`'s grouped listing, which the Self
//! Model view renders.

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
use std::collections::BTreeSet;

use lattice_auth::OrderedMap;
use lattice_core::{safe_loads, CoreError};
use rusqlite::Connection;
use serde_json::Value;

use super::shared::{detail_response, message_response};

/// `self_model.KIND_ORDER` — the render order *is* the priority order.
pub const KIND_ORDER: [&str; 5] = ["trait", "preference", "habit", "decision", "relationship"];

/// `self_model.DEFAULT_SUMMARY_TOKENS`.
pub const SUMMARY_TOKENS: i64 = crate::self_model::DEFAULT_SUMMARY_TOKENS;

/// `SelfModelError.code` → the catalog id the router answers with.
///
/// Brain Core raises codes (it cannot import the catalog); which sentence a
/// person reads is chosen at the HTTP edge, in their language. An unknown code
/// degrades to the generic entry rather than leaking an English developer line.
pub fn message_id(code: &str) -> &'static str {
    match code {
        "invalid_kind" => "self_model.invalid_kind",
        "text_required" => "self_model.text_required",
        "not_found" => "self_model.not_found",
        "not_self_model" => "self_model.not_self_model",
        "not_a_proposal" => "self_model.not_a_proposal",
        "empty_proposal" => "self_model.empty_proposal",
        "graph_unavailable" => "self_model.graph_unavailable",
        "queue_unavailable" => "self_model.queue_unavailable",
        _ => "self_model.invalid",
    }
}

/// `_self_model_error` — `not_found` is a 404, everything else a 400.
pub fn error_response(code: &str, lang: &str) -> axum::response::Response {
    let status = if code == "not_found" { 404 } else { 400 };
    message_response(status, message_id(code), lang, &[])
}

/// A refusal the seam reported, mapped back onto the Self-Model contract.
///
/// The worker answers `500 worker_seam.graph_mutation_failed` with the store's
/// own reason embedded; the codes below are the ones `SelfModelError` raises,
/// so a caller keeps seeing the sentence the Python route gave it.
pub fn seam_error(detail: &str, lang: &str) -> axum::response::Response {
    for code in [
        "not_found",
        "not_self_model",
        "not_a_proposal",
        "empty_proposal",
        "invalid_kind",
        "text_required",
        "graph_unavailable",
        "queue_unavailable",
    ] {
        if detail.contains(code) {
            return error_response(code, lang);
        }
    }
    detail_response(500, detail)
}

/// One fact, in `_row_to_fact`'s key order.
fn row_to_fact(
    id: String,
    node_type: Value,
    title: Value,
    metadata: &serde_json::Map<String, Value>,
    updated_at: Value,
) -> Option<OrderedMap> {
    let kind = metadata
        .get("self_model_kind")
        .and_then(Value::as_str)
        .unwrap_or_default();
    if !KIND_ORDER.contains(&kind) {
        return None;
    }
    let text_of = |key: &str| {
        Value::String(
            metadata
                .get(key)
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_string(),
        )
    };
    let mut fact = OrderedMap::new();
    fact.insert("id", Value::String(id));
    fact.insert("kind", Value::String(kind.to_string()));
    fact.insert("type", node_type);
    fact.insert("text", title);
    fact.insert("origin", text_of("origin"));
    fact.insert(
        "confidence",
        metadata.get("confidence").cloned().unwrap_or(Value::Null),
    );
    fact.insert("signal", text_of("signal"));
    fact.insert(
        "workspace_id",
        metadata.get("workspace_id").cloned().unwrap_or(Value::Null),
    );
    fact.insert("updated_at", updated_at);
    Some(fact)
}

/// `_read_facts` — the legacy `nodes` table, deterministically ordered.
///
/// Deliberately *not* `read_tables`: the v4 write door maintains `nodes` as the
/// compatibility projection of `nodes_v2`, so this answers the same rows in
/// both read modes — the one reader in the crate that skips the view.
pub fn read_facts(
    conn: &Connection,
    allowed: Option<&BTreeSet<String>>,
) -> Result<Vec<OrderedMap>, CoreError> {
    let mut stmt = conn.prepare(
        "SELECT id, type, title, summary, metadata_json, updated_at \
         FROM nodes WHERE id LIKE ? AND id != ? ORDER BY id ASC",
    )?;
    let mut rows = stmt.query(rusqlite::params![
        format!("{}%", crate::self_model::SELF_ID_PREFIX),
        crate::self_model::SELF_ROOT_ID
    ])?;
    let mut facts: Vec<OrderedMap> = Vec::new();
    while let Some(row) = rows.next()? {
        let metadata = safe_loads(row.get::<_, Option<String>>("metadata_json")?.as_deref());
        let Some(fact) = row_to_fact(
            row.get("id")?,
            lattice_core::sql_json(row.get_ref("type")?),
            lattice_core::sql_json(row.get_ref("title")?),
            &metadata,
            lattice_core::sql_json(row.get_ref("updated_at")?),
        ) else {
            continue;
        };
        facts.push(fact);
    }
    if let Some(allowed) = allowed {
        // A fact with no workspace is personal-global and stays visible; this
        // is the Self-Model's own scoping, not the graph layer's opt-in.
        facts.retain(|fact| match fact.get("workspace_id") {
            Some(Value::String(workspace)) if !workspace.is_empty() => allowed.contains(workspace),
            _ => true,
        });
    }
    facts.sort_by(|left, right| {
        let rank = |fact: &OrderedMap| {
            KIND_ORDER
                .iter()
                .position(|kind| Some(*kind) == fact.get("kind").and_then(Value::as_str))
                .unwrap_or(KIND_ORDER.len())
        };
        let text = |fact: &OrderedMap| {
            fact.get("text")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_string()
        };
        rank(left)
            .cmp(&rank(right))
            .then_with(|| text(left).cmp(&text(right)))
    });
    Ok(facts)
}

/// `SelfModelService.profile` — the listing plus the injected summary.
pub fn profile(
    conn: &Connection,
    workspace_id: Option<&str>,
    user_email: &str,
    now: &str,
) -> Result<OrderedMap, CoreError> {
    let allowed: Option<BTreeSet<String>> =
        workspace_id.map(|workspace| [workspace.to_string()].into_iter().collect());
    let facts = read_facts(conn, allowed.as_ref())?;
    let mut counts = OrderedMap::new();
    for kind in KIND_ORDER {
        let count = facts
            .iter()
            .filter(|fact| fact.get("kind").and_then(Value::as_str) == Some(kind))
            .count() as i64;
        counts.insert(kind, Value::from(count));
    }
    let mut out = OrderedMap::new();
    out.insert("available", Value::Bool(true));
    out.insert(
        "facts",
        Value::Array(
            facts
                .iter()
                .map(|fact| serde_json::to_value(fact).unwrap_or(Value::Null))
                .collect(),
        ),
    );
    out.insert("count", Value::from(facts.len() as i64));
    out.insert(
        "counts",
        serde_json::to_value(&counts).unwrap_or(Value::Null),
    );
    out.insert(
        "kinds",
        Value::Array(
            KIND_ORDER
                .iter()
                .map(|k| Value::String(k.to_string()))
                .collect(),
        ),
    );
    out.insert("generated_at", Value::String(now.to_string()));
    out.insert(
        "summary",
        Value::String(crate::self_model::summary_for_prompt(
            conn,
            SUMMARY_TOKENS,
            allowed.as_ref(),
        )),
    );
    out.insert("summary_tokens", Value::from(SUMMARY_TOKENS));
    out.insert(
        "user_email",
        if user_email.is_empty() {
            Value::String(String::new())
        } else {
            Value::String(user_email.to_string())
        },
    );
    out.insert(
        "kind_options",
        Value::Array(
            KIND_ORDER
                .iter()
                .map(|k| Value::String(k.to_string()))
                .collect(),
        ),
    );
    Ok(out)
}

/// The answer a Brain with no graph gives — `available: false`, with a reason.
pub fn unavailable(detail: &str, now: &str, with_profile: bool) -> OrderedMap {
    let mut out = OrderedMap::new();
    out.insert("available", Value::Bool(false));
    out.insert("detail", Value::String(detail.to_string()));
    out.insert("generated_at", Value::String(now.to_string()));
    if with_profile {
        out.insert("facts", Value::Array(Vec::new()));
        out.insert("summary", Value::String(String::new()));
    }
    out
}

/// `SelfModelService.GRAPH_UNAVAILABLE`.
pub const GRAPH_UNAVAILABLE: &str = "self-model needs the knowledge graph";

#[cfg(test)]
mod tests {
    use super::*;

    fn store() -> (tempfile::TempDir, Connection) {
        let dir = tempfile::tempdir().expect("tempdir");
        let conn = Connection::open(dir.path().join("kg.sqlite")).expect("open");
        conn.execute_batch(
            "CREATE TABLE nodes(id TEXT PRIMARY KEY, type TEXT, title TEXT, summary TEXT,
                                metadata_json TEXT, updated_at TEXT);
             INSERT INTO nodes VALUES
               ('self:root','Self','나','root','{\"self_model\": true, \"self_model_kind\": \"root\"}','t'),
               ('self:pref:1','Preference','답변은 한국어로','x',
                '{\"self_model_kind\": \"preference\", \"origin\": \"user\", \"confidence\": 1.0,
                  \"signal\": \"user_edit\", \"workspace_id\": \"personal\"}','t'),
               ('self:trait:1','Self','개발자','x',
                '{\"self_model_kind\": \"trait\", \"origin\": \"user\", \"confidence\": 1.0,
                  \"signal\": \"user_edit\", \"workspace_id\": null}','t'),
               ('self:other:1','Decision','elsewhere','x',
                '{\"self_model_kind\": \"decision\", \"workspace_id\": \"org\"}','t'),
               ('self:junk:1','Note','not a fact','x','{}','t'),
               ('concept:a','Concept','not self at all','x','{}','t');",
        )
        .expect("schema");
        (dir, conn)
    }

    #[test]
    fn facts_are_kind_ordered_then_alphabetical_and_the_root_is_excluded() {
        let (_dir, conn) = store();
        let facts = read_facts(&conn, None).expect("facts");
        let kinds: Vec<&str> = facts
            .iter()
            .filter_map(|fact| fact.get("kind").and_then(Value::as_str))
            .collect();
        assert_eq!(kinds, vec!["trait", "preference", "decision"]);
        assert_eq!(
            facts.len(),
            3,
            "the root, a non-fact and a non-self node are all skipped"
        );
    }

    #[test]
    fn a_scope_hides_another_workspaces_fact_but_never_a_personal_one() {
        let (_dir, conn) = store();
        let allowed: BTreeSet<String> = ["personal".to_string()].into_iter().collect();
        let facts = read_facts(&conn, Some(&allowed)).expect("facts");
        let ids: Vec<&str> = facts
            .iter()
            .filter_map(|fact| fact.get("id").and_then(Value::as_str))
            .collect();
        assert_eq!(ids, vec!["self:trait:1", "self:pref:1"]);
    }

    #[test]
    fn the_profile_carries_the_listing_the_summary_and_the_kind_options() {
        let (_dir, conn) = store();
        let body = serde_json::to_value(
            profile(&conn, Some("personal"), "owner@x", "t").expect("profile"),
        )
        .expect("json");
        assert_eq!(body["available"], true);
        assert_eq!(body["count"], 2);
        assert_eq!(body["counts"]["preference"], 1);
        assert_eq!(body["counts"]["relationship"], 0);
        assert_eq!(body["kinds"][0], "trait");
        assert_eq!(body["summary_tokens"], 200);
        assert_eq!(body["user_email"], "owner@x");
        assert!(body["summary"]
            .as_str()
            .expect("summary")
            .contains("개발자"));
        assert_eq!(body["kind_options"].as_array().expect("options").len(), 5);
    }

    #[test]
    fn a_failure_code_picks_its_own_sentence() {
        assert_eq!(message_id("not_found"), "self_model.not_found");
        assert_eq!(message_id("who-knows"), "self_model.invalid");
        assert_eq!(error_response("not_found", "ko").status(), 404);
        assert_eq!(error_response("not_self_model", "ko").status(), 400);
        assert_eq!(seam_error("... not_self_model ...", "ko").status(), 400);
        assert_eq!(seam_error("something else entirely", "ko").status(), 500);
        let payload =
            serde_json::to_value(unavailable(GRAPH_UNAVAILABLE, "t", true)).expect("json");
        assert_eq!(payload["available"], false);
        assert_eq!(payload["facts"], serde_json::json!([]));
    }
}
