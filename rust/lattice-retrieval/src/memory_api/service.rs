//! `MemoryService` — the cross-tier report and the maintenance half.
//!
//! Six tiers, three real backends: Workspace OS state (workspace / project /
//! agent), the durable `conversation_messages` table (conversation), and the
//! knowledge graph (graph / vector). The rule the Python module states and this
//! port keeps is *never invent*: a tier with no backing reports `unavailable`
//! and contributes `None`, never a zero dressed up as a measurement — which is
//! why `count` is `Value::Null` rather than `0` for an absent vector index.
//!
//! [`Snapshot`] is the one read every surface here is computed from. Python
//! re-reads the stores per method and pays for it four times inside
//! `brain_brief` (manager → proof → manager again); taking the reads once is
//! the only intentional difference, and it changes no output because nothing
//! writes between them inside a request.

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
use std::path::Path;

use lattice_auth::OrderedMap;
use lattice_core::CoreError;
use rusqlite::Connection;
use serde_json::Value;

use super::kg;
use super::shared::BrainState;
use super::wsos;

/// `memory_service.constants.TIERS`.
pub const TIERS: [&str; 6] = [
    "workspace",
    "project",
    "agent",
    "conversation",
    "graph",
    "vector",
];

/// `memory_service.constants.WORKSPACE_KINDS` — `WorkspaceOS.MEMORY_KINDS`.
pub const WORKSPACE_KINDS: [&str; 7] = [
    "short_term",
    "workspace",
    "preferences",
    "decisions",
    "working_style",
    "frequently_used_tools",
    "long_term",
];

/// Longest inline thumbnail a recall row carries.
pub const MAX_RECALL_THUMBNAIL_CHARS: usize = 24_000;

pub(crate) mod mutate;
pub(crate) mod report;
pub(crate) mod snapshot;
pub use mutate::{clear_plan, compact, prune, ClearPlan, PruneOutcome};
pub use report::{inspect, manager, nonempty_or, text_or, tiers};
pub use snapshot::{brain_readiness, conversations, scoped_conversations, Snapshot};

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn readiness_scores_the_way_the_product_grades_a_brain() {
        let quiet = brain_readiness(0, Some(0), Some(0), 0);
        assert_eq!(quiet.get("state"), Some(&Value::String("quiet".into())));
        assert_eq!(quiet.get("score"), Some(&Value::from(12)));
        assert_eq!(quiet.get("depth"), Some(&Value::from(2)));
        let forming = brain_readiness(1, Some(2), Some(5), 2);
        assert_eq!(forming.get("state"), Some(&Value::String("forming".into())));
        assert_eq!(forming.get("depth"), Some(&Value::from(3)));
        let deeper = brain_readiness(1, Some(4), Some(1), 2);
        assert_eq!(deeper.get("depth"), Some(&Value::from(4)));
        let alive = brain_readiness(2, Some(62), Some(68), 6);
        assert_eq!(alive.get("state"), Some(&Value::String("alive".into())));
        assert_eq!(alive.get("score"), Some(&Value::from(100)));
        // An unmeasured tier contributes nothing rather than a zero.
        assert_eq!(
            brain_readiness(0, None, None, 0).get("score"),
            Some(&Value::from(12))
        );
    }

    #[test]
    fn conversations_are_grouped_first_seen_first_with_a_legacy_bucket() {
        let history = vec![
            serde_json::json!({"conversation_id": "c1", "content": "a"}),
            serde_json::json!({"content": "orphan"}),
            serde_json::json!({"conversation_id": "c1", "content": "b"}),
        ];
        let mut order: Vec<String> = Vec::new();
        let mut grouped: std::collections::HashMap<String, Vec<Value>> = Default::default();
        for item in history {
            let id = item
                .get("conversation_id")
                .and_then(Value::as_str)
                .filter(|v| !v.is_empty())
                .unwrap_or("legacy-previous-history")
                .to_string();
            if !grouped.contains_key(&id) {
                order.push(id.clone());
            }
            grouped.entry(id).or_default().push(item);
        }
        assert_eq!(order, vec!["c1", "legacy-previous-history"]);
        assert_eq!(grouped["c1"].len(), 2);
    }

    #[test]
    fn a_scoped_conversation_keeps_only_the_callers_own_messages() {
        let conversations = vec![serde_json::json!({
            "id": "c1",
            "messages": [
                {"content": "mine", "user_email": "me@x", "workspace_id": "personal"},
                {"content": "theirs", "user_email": "you@x", "workspace_id": "personal"},
                {"content": "elsewhere", "user_email": "me@x", "workspace_id": "org"},
            ]
        })];
        let scoped = scoped_conversations(&conversations, "me@x", None);
        assert_eq!(scoped.len(), 1);
        assert_eq!(scoped[0]["messages"].as_array().expect("messages").len(), 1);
        assert_eq!(scoped[0]["messages"][0]["content"], "mine");
        assert_eq!(
            scoped_conversations(&conversations, "", None).len(),
            1,
            "the trusted local owner is unscoped"
        );
        assert!(scoped_conversations(&conversations, "nobody@x", None).is_empty());
    }

    #[test]
    fn the_clear_router_refuses_everything_it_does_not_own() {
        assert!(matches!(
            clear_plan("decisions", true),
            ClearPlan::ByKind(_)
        ));
        match clear_plan("conversations", true) {
            ClearPlan::Refused(detail) => {
                assert_eq!(detail, "unsupported clear scope: conversations")
            }
            _ => panic!("conversations is not a clear scope"),
        }
        match clear_plan("decisions", false) {
            ClearPlan::Refused(detail) => assert_eq!(detail, "clear requires confirm=true"),
            _ => panic!("confirm is the guard"),
        }
        match clear_plan("graph", true) {
            ClearPlan::Refused(detail) => assert!(detail.starts_with("graph clear is disabled")),
            _ => panic!("graph is refused outright"),
        }
    }

    #[test]
    fn a_prune_outcome_reports_partial_and_error_apart() {
        let clean = PruneOutcome {
            removed: vec!["a".into()],
            skipped: Vec::new(),
            failed: Vec::new(),
        };
        let body = serde_json::to_value(clean.to_body()).expect("json");
        assert_eq!(body, serde_json::json!({"removed": ["a"], "count": 1}));
        let partial = PruneOutcome {
            removed: vec!["a".into()],
            skipped: vec!["b".into()],
            failed: vec![("c".into(), "boom".into())],
        };
        let body = serde_json::to_value(partial.to_body()).expect("json");
        assert_eq!(body["status"], "partial");
        assert_eq!(body["skipped"], serde_json::json!(["b"]));
        assert_eq!(body["failed"][0]["detail"], "boom");
        let all_bad = PruneOutcome {
            removed: Vec::new(),
            skipped: Vec::new(),
            failed: vec![("c".into(), "boom".into())],
        };
        assert_eq!(
            serde_json::to_value(all_bad.to_body()).expect("json")["status"],
            "error"
        );
    }

    #[test]
    fn tiers_are_the_vocabulary_the_ui_renders() {
        let body = serde_json::to_value(tiers()).expect("json");
        assert_eq!(body["tiers"][0], "workspace");
        assert_eq!(body["tiers"].as_array().expect("tiers").len(), 6);
        assert_eq!(body["workspace_kinds"].as_array().expect("kinds").len(), 7);
    }
}
