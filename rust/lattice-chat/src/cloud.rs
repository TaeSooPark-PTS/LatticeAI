//! The hybrid cloud lane, behind the network-boundary dial.
//!
//! Ports `services/hybrid_context.py`, `cloud_token_guard.py`,
//! `cloud_egress_audit.py`, `openai_compatible_adapter.py`,
//! `cloud_extraction.py` and `cloud_streaming.py`. The lane is entered only
//! when [`crate::boundary::NetworkMode::CloudAllowed`] is resolved for this
//! turn *and* the graph is on — `local_only` never reaches this module's
//! network code at all.
//!
//! Three disciplines are the whole point of the lane and are ported exactly:
//!
//! 1. **The minimal extracted slice.** One retrieval, hard-blocked nodes
//!    dropped, preferred types first, at most 12 nodes, each rendered as one
//!    `- [Type] title: summary` line truncated at 400 characters, and the whole
//!    block run through [`crate::redact::redact_secret_text`] before it can
//!    leave. A retrieval that fails sends **nothing** — never a wider context.
//! 2. **The token guard.** Identical limits, identical refusal text, and the
//!    refusal is audited too: "nothing left the machine, and here is why".
//! 3. **The egress record is written before the call**, not after: if the
//!    provider hangs or the process dies mid-stream, the record of what was
//!    about to leave already exists.

/// `LATTICEAI_CLOUD_API_KEY`.
pub const CLOUD_API_KEY_ENV: &str = "LATTICEAI_CLOUD_API_KEY";
/// `LATTICEAI_CLOUD_BASE_URL`.
pub const CLOUD_BASE_URL_ENV: &str = "LATTICEAI_CLOUD_BASE_URL";
/// `LATTICEAI_CLOUD_MODEL`.
pub const CLOUD_MODEL_ENV: &str = "LATTICEAI_CLOUD_MODEL";
/// The adapter's default when the environment names none.
pub const DEFAULT_CLOUD_MODEL: &str = "gpt-4o-mini";
/// Where an OpenAI-compatible provider lives when none is configured.
pub const DEFAULT_CLOUD_BASE_URL: &str = "https://api.openai.com/v1";

/// The system prompt every hybrid turn sends, verbatim.
pub const HYBRID_SYSTEM_PROMPT: &str =
    "You are assisting a user whose private Knowledge Graph lives on their machine. \
     Use only the provided context. If the context is insufficient, say so honestly.";

mod adapter;
mod budget;
mod context;
mod expansion;

pub use adapter::{cloud_egress_event, CloudTurnResult, EgressAudit, OpenAiCompatibleAdapter};
pub use budget::{budget_for, record_budget, reset_budget, scope_key, TokenBudget};
pub use context::{build_minimal_context, extract_keywords, rough_token_estimate, MinimalContext};
pub use expansion::{
    extract_candidates, ingest_expansion, plan_kg_expansion, plan_kg_expansion_rich, ExpansionPlan,
    ReviewSink,
};

#[cfg(test)]
mod tests {
    use super::*;
    use crate::boundary::NetworkMode;
    use serde_json::{json, Value};

    #[test]
    fn the_token_estimate_counts_characters_and_never_answers_zero_for_text() {
        assert_eq!(rough_token_estimate(""), 0);
        assert_eq!(rough_token_estimate("a"), 1);
        assert_eq!(rough_token_estimate("abcd"), 1);
        assert_eq!(rough_token_estimate("abcde"), 2);
        assert_eq!(rough_token_estimate("가나다라마"), 2);
    }

    #[test]
    fn keywords_are_deduplicated_case_insensitively_and_capped() {
        assert_eq!(
            extract_keywords("Rust rust 검색 엔진 a", 12),
            vec!["Rust", "검색", "엔진"]
        );
        assert!(extract_keywords("   ", 12).is_empty());
        assert_eq!(extract_keywords("aa bb cc", 2), vec!["aa", "bb"]);
    }

    #[test]
    fn the_budget_refuses_a_fat_turn_and_a_spent_session() {
        let budget = TokenBudget {
            max_tokens_per_turn: 10,
            max_tokens_per_session: 20,
            session_used: 0,
        };
        assert_eq!(budget.check_turn(5), None);
        assert!(budget
            .check_turn(11)
            .unwrap()
            .contains("exceed per-turn limit 10"));
        let spent = TokenBudget {
            session_used: 18,
            ..budget.clone()
        };
        assert!(spent
            .check_turn(5)
            .unwrap()
            .contains("session budget would exceed 20"));
        let mut budget = budget;
        budget.record(-4);
        assert_eq!(budget.session_used, 0);
        budget.record(6);
        assert_eq!(budget.snapshot()["session_remaining"], 14);
    }

    #[test]
    fn budgets_are_process_local_and_keyed_by_scope() {
        let key = scope_key(Some("owner@x"), Some("team"));
        assert_eq!(key, "owner@x|team");
        assert_eq!(scope_key(None, None), "anon|global");
        reset_budget(&key);
        assert_eq!(budget_for(&key).session_used, 0);
        assert_eq!(record_budget(&key, 7).session_used, 7);
        assert_eq!(budget_for(&key).session_used, 7);
        reset_budget(&key);
        assert_eq!(budget_for(&key).session_used, 0);
        // The empty key is the "global" bucket, not a key of its own.
        reset_budget("");
        assert_eq!(record_budget("", 1).session_used, 1);
        reset_budget("global");
        assert_eq!(budget_for("").session_used, 0);
    }

    #[test]
    fn the_egress_event_records_shape_and_never_content() {
        let event = cloud_egress_event(
            &["n1".into()],
            42,
            NetworkMode::CloudAllowed,
            "openai_compatible",
            Some("gpt"),
            Some("owner@x"),
            None,
            "sent",
            None,
        );
        assert_eq!(event["event"], "cloud_egress");
        assert_eq!(event["node_count"], 1);
        assert_eq!(event["workspace_id"], Value::Null);
        assert!(event.get("detail").is_none());
        assert!(
            !event.to_string().contains("compact"),
            "the payload text is never recorded"
        );
        let refused = cloud_egress_event(
            &[],
            0,
            NetworkMode::LocalOnly,
            "(refused)",
            None,
            None,
            None,
            "refused_token_guard",
            Some("over budget"),
        );
        assert_eq!(refused["detail"], "over budget");
    }

    #[test]
    fn the_adapter_builds_the_messages_array_python_builds() {
        let messages = OpenAiCompatibleAdapter::messages("sys", "hello", "ctx");
        assert_eq!(messages.as_array().unwrap().len(), 3);
        assert!(messages[1]["content"]
            .as_str()
            .unwrap()
            .starts_with("Local Knowledge Graph context"));
        let bare = OpenAiCompatibleAdapter::messages("sys", "hello", "");
        assert_eq!(bare.as_array().unwrap().len(), 2);
        assert_eq!(bare[1]["role"], "user");
    }

    #[test]
    fn a_chat_completions_delta_yields_its_text() {
        let payload = json!({"choices": [{"delta": {"content": "hi"}}]});
        assert_eq!(OpenAiCompatibleAdapter::delta_text(&payload), "hi");
        assert_eq!(OpenAiCompatibleAdapter::delta_text(&json!({})), "");
        assert_eq!(
            OpenAiCompatibleAdapter::delta_text(&json!({"choices": [{"delta": {}}]})),
            ""
        );
    }

    #[tokio::test]
    async fn an_unconfigured_adapter_refuses_before_it_dials() {
        let adapter = OpenAiCompatibleAdapter::from_env(reqwest::Client::new()).with_api_key("");
        assert!(!adapter.configured());
        let mut pieces: Vec<String> = Vec::new();
        let error = adapter
            .stream("s", "u", "c", None, &mut |piece| {
                pieces.push(piece.to_string());
                true
            })
            .await
            .unwrap_err();
        assert!(error.contains("LATTICEAI_CLOUD_API_KEY"));
        assert!(pieces.is_empty());
    }

    #[test]
    fn the_expansion_plan_grounds_the_turn_on_every_sent_node() {
        let result = CloudTurnResult {
            user_message: "질문".into(),
            answer_text: "답변".into(),
            sent_node_ids: vec!["n1".into(), "n2".into()],
            provider: "openai_compatible".into(),
            model: "gpt".into(),
        };
        let plan = plan_kg_expansion(&result);
        assert_eq!(plan.new_nodes.len(), 1);
        assert_eq!(plan.new_edges.len(), 2);
        assert_eq!(plan.conversation_title, "질문");
        assert_eq!(plan.new_edges[0]["type"], "grounded_on");
        assert!(!plan.auto_commit);
        // Deterministic, unlike the salted Python original.
        assert_eq!(
            plan.new_nodes[0]["id"],
            plan_kg_expansion(&result).new_nodes[0]["id"]
        );
        let empty = plan_kg_expansion(&CloudTurnResult::default());
        assert_eq!(empty.conversation_title, "Cloud turn");
    }

    #[test]
    fn candidates_are_extracted_by_type_and_deduplicated() {
        let answer =
            "Decision: ship it\n- [ ] write the test\n1. first step\n**개념어**\nDecision: ship it";
        let candidates = extract_candidates(answer, 8);
        let types: Vec<&str> = candidates
            .iter()
            .map(|c| c["type"].as_str().unwrap())
            .collect();
        assert_eq!(types, ["Decision", "Task", "Task", "Concept"]);
        assert_eq!(candidates[0]["title"], "ship it");
        assert_eq!(candidates[0]["metadata"]["confidence"], 0.55);
        assert!(extract_candidates("nothing structured", 8).is_empty());
        assert_eq!(extract_candidates(answer, 1).len(), 1, "the limit stops it");
    }

    #[test]
    fn the_rich_plan_links_every_candidate_to_the_turn_and_its_sources() {
        let result = CloudTurnResult {
            user_message: "q".into(),
            answer_text: "Decision: ship it".into(),
            sent_node_ids: vec!["n1".into()],
            ..Default::default()
        };
        let plan = plan_kg_expansion_rich(&result);
        assert_eq!(plan.new_nodes.len(), 2);
        assert_eq!(plan.provenance["candidate_count"], 1);
        assert_eq!(plan.provenance["extraction"], "heuristic_v1");
        // turn → grounded_on n1, turn → implies cand, cand → grounded_on n1.
        assert_eq!(plan.new_edges.len(), 3);
        let turn = plan.new_nodes[0]["id"].as_str().unwrap().to_string();
        assert_eq!(plan.new_nodes[1]["id"], format!("{turn}:cand:0"));
        assert_eq!(plan.to_value()["new_edges"].as_array().unwrap().len(), 3);
    }

    struct RecordingReview {
        fail: bool,
        items: std::sync::Mutex<Vec<Value>>,
    }

    impl ReviewSink for RecordingReview {
        fn create(&self, item: &Value) -> Result<String, String> {
            self.items.lock().unwrap().push(item.clone());
            if self.fail {
                Err("queue is full".into())
            } else {
                Ok("review-1".into())
            }
        }
    }

    #[test]
    fn ingestion_stages_for_review_and_refuses_to_write_the_graph() {
        let plan = plan_kg_expansion(&CloudTurnResult {
            user_message: "q".into(),
            answer_text: "a".into(),
            sent_node_ids: vec!["n1".into()],
            ..Default::default()
        });
        let unbound = ingest_expansion(&plan, None, None, None);
        assert_eq!(unbound["status"], "staged");
        assert_eq!(unbound["reason"], "no store or review_queue bound");

        let sink = RecordingReview {
            fail: false,
            items: std::sync::Mutex::new(Vec::new()),
        };
        let staged = ingest_expansion(&plan, Some(&sink), Some("owner@x"), Some("team"));
        assert_eq!(staged["status"], "queued_for_review");
        assert_eq!(staged["review_item_id"], "review-1");
        let items = sink.items.lock().unwrap();
        assert_eq!(items[0]["kind"], "kg_cloud_expansion");
        assert_eq!(items[0]["provenance"]["source"], "hybrid_cloud");
        assert!(items[0]["summary"]
            .as_str()
            .unwrap()
            .contains("auto_commit=False"));

        let failing = RecordingReview {
            fail: true,
            items: std::sync::Mutex::new(Vec::new()),
        };
        let errored = ingest_expansion(&plan, Some(&failing), None, None);
        assert_eq!(errored["status"], "staged");
        assert_eq!(errored["review_error"], "queue is full");

        let mut committing = plan.clone();
        committing.auto_commit = true;
        let refused = ingest_expansion(&committing, None, None, None);
        assert!(refused["write_error"]
            .as_str()
            .unwrap()
            .contains("GRAPH_MUTATION_OPS"));
        assert_eq!(refused["written_nodes"], 0);
    }
}
