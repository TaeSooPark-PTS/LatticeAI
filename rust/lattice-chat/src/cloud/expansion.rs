use std::collections::BTreeSet;
use std::sync::OnceLock;

use fancy_regex::Regex;
use serde_json::{json, Map, Value};

use crate::pyvalue::field;

use super::adapter::CloudTurnResult;

// ── expansion planning ──────────────────────────────────────────────────────

/// `KGExpansionPlan`.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct ExpansionPlan {
    pub conversation_title: String,
    pub new_nodes: Vec<Value>,
    pub new_edges: Vec<Value>,
    pub provenance: Value,
    pub auto_commit: bool,
}

impl ExpansionPlan {
    /// `KGExpansionPlan.to_dict`.
    pub fn to_value(&self) -> Value {
        json!({
            "conversation_title": self.conversation_title,
            "new_nodes": self.new_nodes,
            "new_edges": self.new_edges,
            "provenance": self.provenance,
            "auto_commit": self.auto_commit,
        })
    }
}

/// The turn node's id.
///
/// **Stated divergence.** Python builds it from
/// `abs(hash((user_message, answer_text))) % 10**12`, and CPython's `str`
/// hashing is salted per process (`PYTHONHASHSEED`), so the Python value is not
/// reproducible even between two Python runs. Nothing can depend on it, so this
/// uses a stable digest of the same two strings instead — same shape, same
/// 12-digit width, and now actually deterministic.
fn turn_id(user_message: &str, answer_text: &str) -> String {
    let mut hash: u64 = 1_469_598_103_934_665_603;
    for byte in user_message.bytes().chain([0u8]).chain(answer_text.bytes()) {
        hash ^= u64::from(byte);
        hash = hash.wrapping_mul(1_099_511_628_211);
    }
    format!("cloud_turn:{}", hash % 1_000_000_000_000)
}

fn clipped(text: &str, limit: usize) -> String {
    text.chars().take(limit).collect()
}

/// `plan_kg_expansion` — the conversation node plus its grounding edges.
pub fn plan_kg_expansion(result: &CloudTurnResult) -> ExpansionPlan {
    let turn_id = turn_id(&result.user_message, &result.answer_text);
    let title = if result.user_message.is_empty() {
        "Cloud turn".to_string()
    } else {
        clipped(&result.user_message, 120)
    };
    let conversation_node = json!({
        "id": turn_id,
        "type": "Chat",
        "title": title,
        "summary": clipped(&result.answer_text, 800),
        "metadata": {
            "source": "cloud_llm",
            "provider": result.provider,
            "model": result.model,
            "sent_node_ids": result.sent_node_ids,
            "derived_from_cloud": true,
        },
    });
    let edges: Vec<Value> = result
        .sent_node_ids
        .iter()
        .map(|node_id| {
            json!({
                "from": turn_id, "to": node_id, "type": "grounded_on",
                "weight": 1.0, "metadata": {"provenance": "cloud_turn"},
            })
        })
        .collect();
    ExpansionPlan {
        conversation_title: title_of(&conversation_node),
        new_nodes: vec![conversation_node],
        new_edges: edges,
        provenance: json!({
            "kind": "derived_from_cloud",
            "sent_node_ids": result.sent_node_ids,
            "provider": result.provider,
            "model": result.model,
        }),
        auto_commit: false,
    }
}

fn title_of(node: &Value) -> String {
    field(node, "title")
}

fn candidate_patterns() -> &'static Vec<(&'static str, Vec<Regex>)> {
    static SET: OnceLock<Vec<(&'static str, Vec<Regex>)>> = OnceLock::new();
    SET.get_or_init(|| {
        let compile = |patterns: &[&str]| -> Vec<Regex> {
            patterns
                .iter()
                .map(|pattern| Regex::new(pattern).expect("ported pattern must compile"))
                .collect()
        };
        vec![
            (
                "Decision",
                compile(&[
                    r"(?im)^(?:decision|결정)\s*[:：-]\s*(.+)$",
                    r"(?im)we (?:decided|agreed|chose) to (.+?)(?:\.|$)",
                    r"(?im)(?:결정(?:했|하)|합의)(?:습니다|다)?\s*[:：]?\s*(.+)",
                ]),
            ),
            (
                "Task",
                compile(&[
                    r"(?im)^(?:todo|task|할\s*일|다음)\s*[:：-]\s*(.+)$",
                    r"(?im)^[-*]\s+\[(?: |x|X)\]\s*(.+)$",
                    r"(?im)^\d+[.)]\s+(.+)$",
                ]),
            ),
            (
                "Concept",
                compile(&[
                    r"(?im)^(?:concept|개념|용어)\s*[:：-]\s*(.+)$",
                    r"\*\*([^*]{2,80})\*\*",
                ]),
            ),
        ]
    })
}

/// `_clean` — collapse whitespace, then clip.
fn clean(text: &str, limit: usize) -> String {
    static RE: OnceLock<Regex> = OnceLock::new();
    let pattern = RE.get_or_init(|| Regex::new(r"\s+").expect("pattern compiles"));
    clipped(&pattern.replace_all(text.trim(), " "), limit)
}

/// `extract_candidates` — heuristic Concept / Decision / Task nodes.
pub fn extract_candidates(answer: &str, limit: usize) -> Vec<Value> {
    let mut out: Vec<Value> = Vec::new();
    let mut seen: BTreeSet<String> = BTreeSet::new();
    for (node_type, patterns) in candidate_patterns() {
        for pattern in patterns {
            let mut position = 0usize;
            while let Ok(Some(captures)) = pattern.captures_from_pos(answer, position) {
                let whole = captures.get(0).expect("group 0 always exists");
                position = whole.end().max(whole.start() + 1);
                let title = clean(captures.get(1).map(|m| m.as_str()).unwrap_or(""), 120);
                if title.is_empty() {
                    continue;
                }
                let key = format!("{node_type}:{}", title.to_lowercase());
                if !seen.insert(key) {
                    continue;
                }
                out.push(json!({
                    "type": node_type,
                    "title": title,
                    "summary": clean(&title, 400),
                    "metadata": {
                        "derived_from_cloud": true,
                        "extraction": "heuristic_v1",
                        "confidence": 0.55,
                    },
                }));
                if out.len() >= limit {
                    return out;
                }
            }
        }
    }
    out
}

/// `plan_kg_expansion_rich` — base plan plus the heuristic candidates.
pub fn plan_kg_expansion_rich(result: &CloudTurnResult) -> ExpansionPlan {
    let mut plan = plan_kg_expansion(result);
    let candidates = extract_candidates(&result.answer_text, 8);
    let turn_id = plan
        .new_nodes
        .first()
        .map(|node| field(node, "id"))
        .filter(|id| !id.is_empty())
        .unwrap_or_else(|| "cloud_turn:unknown".to_string());
    for (index, candidate) in candidates.iter().enumerate() {
        let node_id = format!("{turn_id}:cand:{index}");
        let mut node = candidate.as_object().cloned().unwrap_or_default();
        // `{"id": node_id, **cand}` — the id goes first, then the candidate's
        // own keys, and a candidate carrying an `id` would win. None does.
        let mut with_id = Map::new();
        with_id.insert("id".into(), json!(node_id));
        with_id.append(&mut node);
        plan.new_nodes.push(Value::Object(with_id));
        plan.new_edges.push(json!({
            "from": turn_id, "to": node_id, "type": "implies",
            "weight": 0.6, "metadata": {"provenance": "cloud_extraction"},
        }));
        for source in &result.sent_node_ids {
            plan.new_edges.push(json!({
                "from": node_id, "to": source, "type": "grounded_on",
                "weight": 0.5, "metadata": {"provenance": "cloud_extraction"},
            }));
        }
    }
    if let Some(provenance) = plan.provenance.as_object_mut() {
        provenance.insert("candidate_count".into(), json!(candidates.len()));
        provenance.insert("extraction".into(), json!("heuristic_v1"));
    }
    plan.auto_commit = false;
    plan
}

/// Where cloud-derived knowledge waits for a human.
///
/// The Review Center queue is `lattice-platform`'s (WP-R7), so chat takes it as
/// a sink. Unbound, the plan is reported as `staged` and nothing is written —
/// which is the honest answer for an install with no Review Center.
pub trait ReviewSink: Send + Sync {
    /// Stage one `change_proposal`; return its id.
    fn create(&self, item: &Value) -> Result<String, String>;
}

/// `CloudResponseIngestor.ingest` — stage, and write only if allowed to.
///
/// **Stated gap.** Python's `auto_commit` branch calls `store.upsert_nodes`
/// directly. That is a graph write, and plan §2 keeps the Python worker the
/// single writer, so this port refuses it rather than opening a native write
/// path: `upsert_nodes` is not in WP-I6's `GRAPH_MUTATION_OPS` whitelist yet.
/// The default is `auto_commit = false`, so the shipped behaviour is unchanged;
/// an install that turns it on gets `write_error` naming the missing seam op
/// instead of a silent no-op.
pub fn ingest_expansion(
    plan: &ExpansionPlan,
    review: Option<&dyn ReviewSink>,
    user_email: Option<&str>,
    workspace_id: Option<&str>,
) -> Value {
    let mut result = Map::new();
    result.insert("status".into(), json!("staged"));
    result.insert("plan".into(), plan.to_value());
    result.insert("review_item_id".into(), Value::Null);
    result.insert("written_nodes".into(), json!(0));
    result.insert("written_edges".into(), json!(0));

    if let Some(review) = review {
        let item = json!({
            "title": format!(
                "Cloud KG expansion: {}",
                clipped(&plan.conversation_title, 80)
            ),
            "summary": format!(
                "{} node(s), {} edge(s) derived from cloud LLM (auto_commit={})",
                plan.new_nodes.len(),
                plan.new_edges.len(),
                if plan.auto_commit { "True" } else { "False" },
            ),
            "source": "change_proposal",
            "kind": "kg_cloud_expansion",
            "payload": {"plan": plan.to_value(), "auto_commit": plan.auto_commit},
            "provenance": {
                "kind": plan.provenance.get("kind").cloned().unwrap_or(Value::Null),
                "sent_node_ids": plan.provenance.get("sent_node_ids").cloned().unwrap_or(Value::Null),
                "provider": plan.provenance.get("provider").cloned().unwrap_or(Value::Null),
                "model": plan.provenance.get("model").cloned().unwrap_or(Value::Null),
                "candidate_count": plan.provenance.get("candidate_count").cloned().unwrap_or(Value::Null),
                "extraction": plan.provenance.get("extraction").cloned().unwrap_or(Value::Null),
                "source": "hybrid_cloud",
            },
            "user_email": user_email,
            "workspace_id": workspace_id,
        });
        match review.create(&item) {
            Ok(id) => {
                result.insert("review_item_id".into(), json!(id));
                result.insert("status".into(), json!("queued_for_review"));
            }
            Err(error) => {
                result.insert("review_error".into(), json!(error));
            }
        }
    }

    if plan.auto_commit {
        result.insert(
            "write_error".into(),
            json!(
                "auto_commit is not delegated yet: 'upsert_nodes' is not in the \
                 worker seam's GRAPH_MUTATION_OPS whitelist, and the Brain keeps \
                 one writer. The plan is staged for review instead."
            ),
        );
    }

    if result.get("status") == Some(&json!("staged")) && review.is_none() {
        result.insert("reason".into(), json!("no store or review_queue bound"));
    }
    Value::Object(result)
}
