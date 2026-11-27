use std::collections::BTreeMap;

use lattice_core::embeddings::LocalEmbeddingModel;
use lattice_retrieval::history::{history as read_history, HistoryScope};
use lattice_retrieval::mmr::{diversify, DiversityConfig};
use lattice_retrieval::query_plan::QueryPlan;
use lattice_retrieval::self_rag::{assess, is_evidence, Sufficiency};
use lattice_retrieval::ContextRequest;
use serde_json::{json, Value};

use crate::boundary::strip_cloud_prefix;
use crate::pyvalue::field;
use crate::state::ChatState;
use crate::worker::ChatWorker;

pub(crate) const CHAT_KNOWLEDGE_LIMIT: i64 = 5;
const HISTORY_MESSAGES: usize = 6;
const HISTORY_MESSAGE_CHARS: usize = 500;
pub(crate) const IMAGE_MARKER: &str = "\n[Image attached]";

pub(crate) fn prior_user_messages(
    state: &ChatState,
    user_email: Option<&str>,
    conversation_id: Option<&str>,
    workspace_id: Option<&str>,
) -> Vec<String> {
    let Some(conversation_id) = conversation_id.filter(|id| !id.is_empty()) else {
        return Vec::new();
    };
    let Some(conn) = state.read_conn() else {
        return Vec::new();
    };
    let scope = HistoryScope {
        user_email: user_email
            .filter(|email| !email.is_empty())
            .map(str::to_string),
        allowed_workspaces: workspace_id
            .filter(|id| !id.is_empty())
            .map(|id| vec![id.to_string()]),
        include_legacy_global: false,
    };
    let rows = read_history(&conn, Some(conversation_id), None, &scope).unwrap_or_default();
    let mut recent: Vec<String> = rows
        .iter()
        .rev()
        .filter(|row| field(row, "role") == "user")
        .map(|row| spoken_text(&field(row, "content")))
        .filter(|content| !content.trim().is_empty())
        .take(HISTORY_MESSAGES)
        .collect();
    recent.reverse();
    recent
}

fn spoken_text(content: &str) -> String {
    let content = content.strip_suffix(IMAGE_MARKER).unwrap_or(content);
    strip_cloud_prefix(content)
        .chars()
        .take(HISTORY_MESSAGE_CHARS)
        .collect()
}

pub(crate) struct Retrieved {
    pub payload: Value,
    pub historical: bool,
    pub diversity: Value,
}

impl Retrieved {
    pub fn matches(&self) -> &[Value] {
        self.payload
            .get("matches")
            .and_then(Value::as_array)
            .map(Vec::as_slice)
            .unwrap_or_default()
    }
}

pub(crate) async fn retrieve(
    state: &ChatState,
    request: &ContextRequest,
    evidence_only: bool,
) -> Option<Retrieved> {
    let (mut payload, plan) = state.read_conn().and_then(|conn| {
        lattice_retrieval::context::knowledge_search_ranked(
            &conn,
            &LocalEmbeddingModel::default(),
            request,
        )
        .ok()
    })?;
    if evidence_only {
        if let Some(matches) = payload.get_mut("matches").and_then(Value::as_array_mut) {
            matches.retain(is_evidence);
        }
    }
    let rerank_query = plan
        .as_ref()
        .map(|plan| plan.search_query().to_string())
        .unwrap_or_else(|| request.query.clone());
    lattice_retrieval::rerank::rerank_report(
        state.worker.as_ref().map(ChatWorker::client),
        &rerank_query,
        &mut payload,
        &lattice_retrieval::rerank::RerankConfig::from_env(),
    )
    .await;
    let mut historical = false;
    if let Some(mut plan) = plan {
        historical = plan.include_historical();
        let pool = lattice_retrieval::context::knowledge_fetch_limit(request);
        lattice_retrieval::context::finalize_knowledge(&mut plan, &mut payload, request, pool);
    }
    let mut diversity = Value::Null;
    if let Some(matches) = payload.get_mut("matches").and_then(Value::as_array_mut) {
        let (selected, report) = diversify(
            std::mem::take(matches),
            CHAT_KNOWLEDGE_LIMIT as usize,
            &DiversityConfig::from_env(),
        );
        *matches = selected;
        diversity = report;
    }
    Some(Retrieved {
        payload,
        historical,
        diversity,
    })
}

fn sufficiency(
    state: &ChatState,
    plan: &QueryPlan,
    request: &ContextRequest,
    found: &Retrieved,
) -> Sufficiency {
    let parents = state
        .read_conn()
        .map(|conn| {
            lattice_retrieval::context_parents::parents_for_payload(
                &conn,
                &found.payload,
                found.matches().len() as i64,
            )
        })
        .unwrap_or_default();
    let semantic = request
        .query_embedding
        .as_ref()
        .is_some_and(|embedding| !embedding.is_hash_fallback);
    assess(
        &plan.standalone,
        found.matches(),
        &parents,
        found.historical,
        semantic,
    )
}

pub(crate) struct Gated {
    pub found: Option<Retrieved>,
    pub abstention: Option<Sufficiency>,
    pub report: Value,
    pub expanded: Option<Vec<String>>,
}

impl Gated {
    pub fn plan_for(&self, plan: &QueryPlan) -> QueryPlan {
        QueryPlan {
            sub_queries: self
                .expanded
                .clone()
                .unwrap_or_else(|| plan.sub_queries.clone()),
            ..plan.clone()
        }
    }
}

fn unavailable(plan: &QueryPlan) -> Sufficiency {
    assess(&plan.standalone, &[], &BTreeMap::new(), false, false)
}

pub(crate) async fn retrieve_gated(
    state: &ChatState,
    plan: &QueryPlan,
    request: &ContextRequest,
    required: bool,
) -> Gated {
    let ungated = |found| Gated {
        found,
        abstention: None,
        report: json!({"required": required, "retried": false}),
        expanded: None,
    };
    let first = retrieve(state, request, required).await;
    if !required {
        return ungated(first);
    }
    let Some(found) = first else {
        let verdict = unavailable(plan);
        return Gated {
            found: None,
            report: json!({"required": true, "retried": false, "verdict": verdict.as_json()}),
            abstention: Some(verdict),
            expanded: None,
        };
    };
    let verdict = sufficiency(state, plan, request, &found);
    if verdict.sufficient {
        return Gated {
            report: json!({"required": true, "retried": false, "verdict": verdict.as_json()}),
            found: Some(found),
            abstention: None,
            expanded: None,
        };
    }
    let expanded = ContextRequest {
        sub_queries: plan.expanded_queries(),
        ..request.clone()
    };
    let (found, verdict) = match retrieve(state, &expanded, true).await {
        Some(retried) => {
            let verdict = sufficiency(state, plan, &expanded, &retried);
            (retried, verdict)
        }
        None => (found, verdict),
    };
    Gated {
        report: json!({"required": true, "retried": true, "verdict": verdict.as_json()}),
        abstention: (!verdict.sufficient).then(|| verdict.clone()),
        found: Some(found),
        expanded: Some(expanded.sub_queries),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stored_turns_lose_routing_and_attachment_markers() {
        assert_eq!(
            spoken_text("/cloud Phoenix plan\n[Image attached]"),
            "Phoenix plan"
        );
        assert_eq!(spoken_text("Phoenix plan"), "Phoenix plan");
    }

    #[test]
    fn a_retried_gate_hands_its_expanded_queries_to_the_provider_lane() {
        let plan = lattice_retrieval::query_plan::plan_query("compare Postgres and SQLite", &[]);
        let mut gated = Gated {
            found: None,
            abstention: None,
            report: Value::Null,
            expanded: None,
        };
        assert_eq!(gated.plan_for(&plan), plan);
        gated.expanded = Some(plan.expanded_queries());
        assert_eq!(gated.plan_for(&plan).sub_queries, plan.expanded_queries());
    }
}
