//! Brain Proof and Brain Brief — the home screen's evidence and its briefing.
//!
//! Both are backend-owned on purpose. The proof has to show the stores the
//! Brain can *actually* recall from, and the model-continuity claim has to be
//! independent of whichever LLM happens to be loaded — a UI-derived version of
//! either would be an assertion rather than a proof. `capability` is therefore
//! always true (the design is model-independent) while `proven` stays false
//! until there is durable evidence on disk.
//!
//! Everything the brief adds on top is a *descriptor list*, not copy: labels
//! are i18n keys the frontend resolves, so the wording stays in the frontend
//! and the ordering — which is a product judgement about priority — stays here.
//! `sorted(..., reverse=True)[:4]` is stable in Python and `sort_by` is stable
//! in Rust, so ties keep the order they were appended in.

use lattice_auth::OrderedMap;
use serde_json::Value;

use super::service::{nonempty_or, text_or, Snapshot};

fn json(map: &OrderedMap) -> Value {
    serde_json::to_value(map).unwrap_or(Value::Null)
}

fn count_of(sources: &Value, id: &str) -> i64 {
    sources
        .as_array()
        .and_then(|rows| {
            rows.iter()
                .find(|row| row.get("id").and_then(Value::as_str) == Some(id))
        })
        .and_then(|row| row.get("count"))
        .and_then(Value::as_i64)
        .unwrap_or(0)
}

/// `MemoryProofMixin._latest_recall_query` — the first thing worth recalling.
pub fn latest_recall_query(
    snapshot: &Snapshot,
    user_email: &str,
    workspace_id: Option<&str>,
) -> String {
    for memory in &snapshot.workspace_memories {
        let content = lattice_core::pytext::strip(text_or(memory, "content", ""));
        if !content.is_empty() {
            return lattice_core::truncate_chars(&content, 96);
        }
    }
    // Deliberately the *unscoped* conversation list, as Python has it: the
    // per-message filter below is the scoping, and it is not the same filter
    // `_scoped_conversations` applies (it keeps conversations, not messages).
    let target = workspace_id.unwrap_or(super::wsos::DEFAULT_WORKSPACE_ID);
    for conversation in &snapshot.conversations {
        let Some(messages) = conversation.get("messages").and_then(Value::as_array) else {
            continue;
        };
        let tail = messages.len().saturating_sub(8);
        for message in messages[tail..].iter().rev() {
            if !message.is_object() {
                continue;
            }
            if !user_email.is_empty()
                && message.get("user_email").and_then(Value::as_str) != Some(user_email)
            {
                continue;
            }
            let workspace = message
                .get("workspace_id")
                .and_then(Value::as_str)
                .filter(|value| !value.is_empty())
                .unwrap_or(super::wsos::DEFAULT_WORKSPACE_ID);
            if workspace != target {
                continue;
            }
            let content = lattice_core::pytext::strip(text_or(message, "content", ""));
            if !content.is_empty() {
                return lattice_core::truncate_chars(&content, 96);
            }
        }
    }
    String::new()
}

/// `MemoryProofMixin.brain_proof`, given an already-computed manager report.
pub fn brain_proof(
    manager: &OrderedMap,
    recall: &OrderedMap,
    query: &str,
    active_model: &str,
    limit: i64,
    now: &str,
) -> OrderedMap {
    let sources = manager.get("sources").cloned().unwrap_or(Value::Null);
    let readiness = manager
        .get("brain_readiness")
        .cloned()
        .unwrap_or(Value::Null);
    let conversation_count = count_of(&sources, "conversation");
    let workspace_count = count_of(&sources, "workspace");
    let graph_count = count_of(&sources, "graph");
    let vector_count = count_of(&sources, "vector");

    let cap = limit.clamp(1, 8) as usize;
    let items: Vec<Value> = recall
        .get("results")
        .and_then(Value::as_array)
        .map(|rows| rows.iter().take(cap).map(proof_item).collect())
        .unwrap_or_default();

    let durable_items = workspace_count + conversation_count + graph_count;
    let has_durable_evidence = durable_items > 0;

    let mut continuity = OrderedMap::new();
    continuity.insert("active_model", Value::String(active_model.to_string()));
    continuity.insert("brain_owner", Value::String("lattice_brain".to_string()));
    continuity.insert("capability", Value::Bool(true));
    continuity.insert("survives_model_switch", Value::Bool(has_durable_evidence));
    continuity.insert("proven", Value::Bool(has_durable_evidence));
    continuity.insert(
        "context_store",
        Value::String("workspace + conversation + graph + vector".to_string()),
    );

    let mut proofs = OrderedMap::new();
    proofs.insert("durable_items", Value::from(durable_items));
    proofs.insert("has_durable_evidence", Value::Bool(has_durable_evidence));
    proofs.insert("workspace_memories", Value::from(workspace_count));
    proofs.insert("conversations", Value::from(conversation_count));
    proofs.insert("graph_concepts", Value::from(graph_count));
    proofs.insert("vector_items", Value::from(vector_count));
    proofs.insert(
        "healthy_sources",
        readiness
            .get("signals")
            .and_then(|signals| signals.get("healthy_sources"))
            .cloned()
            .unwrap_or(Value::from(0)),
    );

    let mut recall_block = OrderedMap::new();
    recall_block.insert(
        "query",
        Value::String(match recall.get("query").and_then(Value::as_str) {
            Some(text) if !text.is_empty() => text.to_string(),
            _ => query.to_string(),
        }),
    );
    recall_block.insert("items", Value::Array(items.clone()));
    recall_block.insert(
        "count",
        recall.get("count").cloned().unwrap_or(Value::from(0)),
    );

    let mut claims = OrderedMap::new();
    claims.insert(
        "can_recall_user_context",
        Value::Bool(!items.is_empty() || durable_items > 0),
    );
    claims.insert(
        "keeps_context_across_models",
        Value::Bool(has_durable_evidence),
    );
    claims.insert(
        "is_knowledge_store",
        Value::Bool(
            graph_count != 0
                || vector_count != 0
                || workspace_count != 0
                || conversation_count != 0,
        ),
    );

    let mut out = OrderedMap::new();
    out.insert(
        "status",
        Value::String(
            readiness
                .get("state")
                .and_then(Value::as_str)
                .filter(|value| !value.is_empty())
                .unwrap_or("quiet")
                .to_string(),
        ),
    );
    out.insert("readiness", readiness);
    out.insert("model_continuity", json(&continuity));
    out.insert("proofs", json(&proofs));
    out.insert("recall", json(&recall_block));
    out.insert("claims", json(&claims));
    out.insert("generated_at", Value::String(now.to_string()));
    out
}

/// One recall row reduced to what the Evidence panel renders.
fn proof_item(item: &Value) -> Value {
    let mut row = OrderedMap::new();
    row.insert("id", item.get("id").cloned().unwrap_or(Value::Null));
    row.insert("source", item.get("source").cloned().unwrap_or(Value::Null));
    row.insert("title", item.get("title").cloned().unwrap_or(Value::Null));
    row.insert(
        "snippet",
        item.get("snippet").cloned().unwrap_or(Value::Null),
    );
    row.insert(
        "score",
        item.get("score").cloned().unwrap_or(Value::from(0)),
    );
    row.insert(
        "matched_terms",
        match item.get("matched_terms") {
            Some(Value::Array(terms)) if !terms.is_empty() => Value::Array(terms.clone()),
            _ => Value::Array(Vec::new()),
        },
    );
    row.insert(
        "confidence",
        Value::String(nonempty_or(item, "confidence", "low")),
    );
    row.insert("kind", Value::String(nonempty_or(item, "kind", "")));
    for key in ["caption", "thumbnail"] {
        if let Some(value) = item
            .get(key)
            .filter(|value| value.as_str().is_some_and(|text| !text.is_empty()))
        {
            row.insert(key, value.clone());
        }
    }
    json(&row)
}

/// `MemoryBriefMixin.brain_brief`.
// Eight parameters because the Python mixin method takes eight; the signature
// is the port's contract and a params struct would only rename them.
#[allow(clippy::too_many_arguments)]
pub fn brain_brief(
    snapshot: &Snapshot,
    manager: &OrderedMap,
    proof: &OrderedMap,
    user_email: &str,
    workspace_id: Option<&str>,
    recall_query: &str,
    limit: i64,
    now: &str,
) -> OrderedMap {
    let readiness = manager
        .get("brain_readiness")
        .cloned()
        .unwrap_or(Value::Null);
    let state = readiness
        .get("state")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .unwrap_or("quiet")
        .to_string();
    let proofs = proof.get("proofs").cloned().unwrap_or(Value::Null);
    let recall = proof.get("recall").cloned().unwrap_or(Value::Null);
    let recall_items: Vec<Value> = recall
        .get("items")
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter(|item| item.is_object())
                .cloned()
                .collect()
        })
        .unwrap_or_default();
    let number = |key: &str| proofs.get(key).and_then(Value::as_i64).unwrap_or(0);
    let durable_items = number("durable_items");
    let workspace_memories = number("workspace_memories");
    let conversations = number("conversations");
    let graph_concepts = number("graph_concepts");
    let vector_items = number("vector_items");
    let healthy_sources = number("healthy_sources");
    let has_durable_evidence = proofs
        .get("has_durable_evidence")
        .and_then(Value::as_bool)
        .unwrap_or(false);

    let query = match recall.get("query").and_then(Value::as_str) {
        Some(text) if !text.is_empty() => text.to_string(),
        _ => recall_query.to_string(),
    };
    let focus = focus(
        snapshot,
        user_email,
        workspace_id,
        &recall_items,
        durable_items,
        graph_concepts,
        &query,
    );
    let actions = next_actions(
        &state,
        has_durable_evidence,
        !recall_items.is_empty(),
        graph_concepts,
    );
    let questions = suggested_questions(
        &focus,
        has_durable_evidence,
        !recall_items.is_empty(),
        graph_concepts,
        conversations,
    );
    let proactive = proactive_actions(
        &focus,
        &state,
        has_durable_evidence,
        !recall_items.is_empty(),
        graph_concepts,
        vector_items,
        healthy_sources,
    );
    let known = matches!(state.as_str(), "quiet" | "forming" | "alive");
    let suffix = if known { state.as_str() } else { "quiet" };

    let mut evidence = Vec::new();
    for (id, label, value) in [
        ("durable", "brain.brief.evidence.durable", durable_items),
        ("graph", "brain.brief.evidence.graph", graph_concepts),
        ("sources", "brain.brief.evidence.sources", healthy_sources),
    ] {
        let mut row = OrderedMap::new();
        row.insert("id", Value::String(id.to_string()));
        row.insert("label_key", Value::String(label.to_string()));
        row.insert("value", Value::from(value));
        row.insert("detail_key", Value::String(format!("{label}.detail")));
        evidence.push(json(&row));
    }

    let mut signals = OrderedMap::new();
    signals.insert("workspace_memories", Value::from(workspace_memories));
    signals.insert("conversations", Value::from(conversations));
    signals.insert("graph_concepts", Value::from(graph_concepts));
    signals.insert("vector_items", Value::from(vector_items));
    signals.insert("healthy_sources", Value::from(healthy_sources));

    let cap = limit.clamp(1, 6) as usize;
    let mut proof_block = OrderedMap::new();
    proof_block.insert(
        "query",
        Value::String(
            recall
                .get("query")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_string(),
        ),
    );
    proof_block.insert(
        "items",
        Value::Array(recall_items.iter().take(cap).cloned().collect()),
    );
    proof_block.insert(
        "model_continuity",
        proof
            .get("model_continuity")
            .cloned()
            .unwrap_or(Value::Object(serde_json::Map::new())),
    );

    let mut out = OrderedMap::new();
    out.insert("status", Value::String(state.clone()));
    out.insert(
        "score",
        Value::from(readiness.get("score").and_then(Value::as_i64).unwrap_or(0)),
    );
    out.insert(
        "headline_key",
        Value::String(format!("brain.brief.headline.{suffix}")),
    );
    out.insert(
        "body_key",
        Value::String(format!("brain.brief.body.{suffix}")),
    );
    out.insert("focus", json(&focus));
    out.insert("next_actions", Value::Array(actions));
    out.insert("suggested_questions", Value::Array(questions));
    out.insert("proactive_actions", Value::Array(proactive));
    out.insert("evidence", Value::Array(evidence));
    out.insert("signals", json(&signals));
    out.insert("proof", json(&proof_block));
    out.insert("generated_at", Value::String(now.to_string()));
    out
}

/// `_brain_brief_focus` — a real recalled row, a real memory, a real
/// conversation or a real graph count, in that order, and an empty Brain says so.
fn focus(
    snapshot: &Snapshot,
    user_email: &str,
    workspace_id: Option<&str>,
    recall_items: &[Value],
    durable_items: i64,
    graph_concepts: i64,
    query: &str,
) -> OrderedMap {
    let mut out = OrderedMap::new();
    if let Some(first) = recall_items.first() {
        out.insert("kind", Value::String("recall".to_string()));
        out.insert(
            "title",
            Value::String(nonempty_or(first, "title", "Memory")),
        );
        out.insert(
            "detail",
            Value::String(match first.get("snippet").and_then(Value::as_str) {
                Some(text) if !text.is_empty() => text.to_string(),
                _ => query.to_string(),
            }),
        );
        out.insert(
            "source",
            Value::String(nonempty_or(first, "source", "memory")),
        );
        out.insert(
            "score",
            Value::from(first.get("score").and_then(Value::as_f64).unwrap_or(0.0)),
        );
        return out;
    }
    if let Some(memory) = snapshot.workspace_memories.first() {
        out.insert("kind", Value::String("memory".to_string()));
        out.insert(
            "title",
            Value::String(nonempty_or(memory, "kind", "memory")),
        );
        out.insert(
            "detail",
            Value::String(lattice_core::truncate_chars(
                text_or(memory, "content", ""),
                240,
            )),
        );
        out.insert("source", Value::String("workspace".to_string()));
        out.insert("score", Value::from(1.0));
        return out;
    }
    let _ = (user_email, workspace_id);
    if let Some(latest) = snapshot.scoped_conversations.last() {
        let last_message = latest
            .get("messages")
            .and_then(Value::as_array)
            .and_then(|messages| {
                messages.iter().rev().find(|message| {
                    message.is_object()
                        && !lattice_core::pytext::strip(text_or(message, "content", "")).is_empty()
                })
            })
            .cloned()
            .unwrap_or(Value::Object(serde_json::Map::new()));
        out.insert("kind", Value::String("conversation".to_string()));
        out.insert(
            "title",
            Value::String(match latest.get("title").and_then(Value::as_str) {
                Some(text) if !text.is_empty() => text.to_string(),
                _ => nonempty_or(latest, "id", "conversation"),
            }),
        );
        out.insert(
            "detail",
            Value::String(lattice_core::truncate_chars(
                text_or(&last_message, "content", ""),
                240,
            )),
        );
        out.insert("source", Value::String("conversation".to_string()));
        out.insert("score", Value::from(1.0));
        return out;
    }
    if graph_concepts > 0 {
        out.insert("kind", Value::String("graph".to_string()));
        out.insert("title", Value::String("Knowledge Graph".to_string()));
        out.insert(
            "detail",
            Value::String(format!(
                "{graph_concepts} graph concepts are ready to inspect."
            )),
        );
        out.insert("source", Value::String("graph".to_string()));
        out.insert("score", Value::from(1.0));
        return out;
    }
    out.insert("kind", Value::String("empty".to_string()));
    out.insert("title", Value::String(String::new()));
    out.insert("detail", Value::String(String::new()));
    out.insert("source", Value::String("none".to_string()));
    out.insert("score", Value::from(0));
    out.insert("empty", Value::Bool(durable_items <= 0));
    out
}

fn action(id: &str, label: &str, detail: &str, route: &str, priority: i64) -> OrderedMap {
    let mut row = OrderedMap::new();
    row.insert("id", Value::String(id.to_string()));
    row.insert("label_key", Value::String(label.to_string()));
    row.insert("detail_key", Value::String(detail.to_string()));
    row.insert("route", Value::String(route.to_string()));
    row.insert("priority", Value::from(priority));
    row
}

fn top4(rows: Vec<OrderedMap>) -> Vec<Value> {
    let mut rows = rows;
    rows.sort_by(|left, right| {
        let key = |row: &OrderedMap| row.get("priority").and_then(Value::as_i64).unwrap_or(0);
        key(right).cmp(&key(left))
    });
    rows.iter().take(4).map(json).collect()
}

/// `_brain_brief_actions`.
fn next_actions(
    state: &str,
    has_durable_evidence: bool,
    has_recall: bool,
    graph_concepts: i64,
) -> Vec<Value> {
    let mut rows = Vec::new();
    if !has_durable_evidence {
        rows.push(action(
            "add_source",
            "brain.brief.action.add",
            "brain.brief.action.add.detail",
            "/capture",
            10,
        ));
        rows.push(action(
            "ask_brain",
            "brain.brief.action.ask",
            "brain.brief.action.ask.detail",
            "",
            9,
        ));
        return top4(rows);
    }
    rows.push(action(
        "ask_brain",
        "brain.brief.action.ask",
        "brain.brief.action.ask.detail",
        "",
        10,
    ));
    if graph_concepts > 0 || state == "alive" {
        rows.push(action(
            "inspect_topics",
            "brain.brief.action.topics",
            "brain.brief.action.topics.detail",
            "/knowledge-graph",
            8,
        ));
    }
    if has_recall {
        rows.push(action(
            "verify_model",
            "brain.brief.action.verify",
            "brain.brief.action.verify.detail",
            "",
            7,
        ));
    }
    rows.push(action(
        "backup_brain",
        "brain.brief.action.backup",
        "brain.brief.action.backup.detail",
        "/settings",
        6,
    ));
    top4(rows)
}

/// `_brain_brief_suggested_questions`.
fn suggested_questions(
    focus: &OrderedMap,
    has_durable_evidence: bool,
    has_recall: bool,
    graph_concepts: i64,
    conversations: i64,
) -> Vec<Value> {
    let focus_title = lattice_core::pytext::strip(
        focus
            .get("title")
            .and_then(Value::as_str)
            .unwrap_or_default(),
    );
    let focus_kind = focus.get("kind").and_then(Value::as_str).unwrap_or("empty");
    let question = |id: &str, stem: &str, params: Value, priority: i64| {
        let mut row = OrderedMap::new();
        row.insert("id", Value::String(id.to_string()));
        row.insert("label_key", Value::String(format!("{stem}.label")));
        row.insert("detail_key", Value::String(format!("{stem}.detail")));
        row.insert("prompt_key", Value::String(format!("{stem}.prompt")));
        row.insert("params", params);
        row.insert("priority", Value::from(priority));
        row
    };
    let focus_param = |fallback: &str| {
        let value = if focus_title.is_empty() {
            fallback.to_string()
        } else {
            focus_title.clone()
        };
        serde_json::json!({"focus": value})
    };
    if !has_durable_evidence || focus_kind == "empty" {
        // Returned as built, *not* sorted or truncated — the Python early
        // return skips the `sorted(...)[:4]` the other branch ends with.
        return vec![
            json(&question(
                "start_brain",
                "brain.suggestion.start",
                serde_json::json!({}),
                10,
            )),
            json(&question(
                "add_context",
                "brain.suggestion.context",
                serde_json::json!({}),
                9,
            )),
        ];
    }
    let mut rows = vec![question(
        "focus_next",
        "brain.suggestion.focus",
        focus_param("Brain"),
        10,
    )];
    if has_recall {
        rows.push(question(
            "evidence_check",
            "brain.suggestion.evidence",
            focus_param("this topic"),
            9,
        ));
    }
    if graph_concepts > 0 {
        rows.push(question(
            "graph_connections",
            "brain.suggestion.graph",
            focus_param("Knowledge Graph"),
            8,
        ));
    }
    if conversations > 0 {
        rows.push(question(
            "conversation_followup",
            "brain.suggestion.history",
            focus_param("recent conversations"),
            7,
        ));
    }
    top4(rows)
}

/// `_brain_brief_proactive_actions` — concrete, one-click next steps.
fn proactive_actions(
    focus: &OrderedMap,
    state: &str,
    has_durable_evidence: bool,
    has_recall: bool,
    graph_concepts: i64,
    vector_items: i64,
    healthy_sources: i64,
) -> Vec<Value> {
    let raw_title = lattice_core::pytext::strip(
        focus
            .get("title")
            .and_then(Value::as_str)
            .unwrap_or_default(),
    );
    let focus_title = if raw_title.is_empty() {
        "Brain".to_string()
    } else {
        raw_title
    };
    let focus_detail = lattice_core::pytext::strip(
        focus
            .get("detail")
            .and_then(Value::as_str)
            .unwrap_or_default(),
    );
    let mut rows: Vec<OrderedMap> = Vec::new();
    let row = |id: &str,
               intent: &str,
               stem: &str,
               route: Option<&str>,
               prompt: String,
               priority: i64,
               context: Option<Value>| {
        let mut entry = OrderedMap::new();
        entry.insert("id", Value::String(id.to_string()));
        entry.insert("intent", Value::String(intent.to_string()));
        entry.insert("label_key", Value::String(format!("{stem}.label")));
        entry.insert("detail_key", Value::String(format!("{stem}.detail")));
        if let Some(route) = route {
            entry.insert("route", Value::String(route.to_string()));
        }
        entry.insert("prompt", Value::String(prompt));
        entry.insert("priority", Value::from(priority));
        if let Some(context) = context {
            entry.insert("context", context);
        }
        entry
    };
    if !has_durable_evidence {
        // Both returned unsorted, as the Python early return does.
        return vec![
            json(&row(
                "proactive_add_source",
                "route",
                "brain.proactive.addSource",
                Some("/capture"),
                "Add a useful source to my Brain and explain what it learned.".to_string(),
                100,
                None,
            )),
            json(&row(
                "proactive_seed_memory",
                "ask",
                "brain.proactive.seed",
                None,
                "Help me seed my Brain with the most useful personal context to remember."
                    .to_string(),
                90,
                None,
            )),
        ];
    }
    let context = serde_json::json!({"focus": focus_title, "detail": focus_detail});
    if has_recall {
        rows.push(row(
            "proactive_evidence_review",
            "ask",
            "brain.proactive.evidence",
            None,
            format!(
                "Review the evidence Brain has for {focus_title}. Separate confirmed facts, \
                 weak signals, contradictions, and next checks."
            ),
            100,
            Some(context.clone()),
        ));
        rows.push(row(
            "proactive_delegate",
            "delegate",
            "brain.proactive.delegate",
            None,
            format!(
                "Turn {focus_title} into an execution plan, verify the known context, \
                 and return concrete next steps with risks."
            ),
            95,
            Some(context.clone()),
        ));
        rows.push(row(
            "proactive_review_draft",
            "review",
            "brain.proactive.review",
            None,
            lattice_core::pytext::strip(&format!(
                "Create a reviewable task from Brain's current focus: {focus_title}. {}",
                lattice_core::truncate_chars(&focus_detail, 240)
            )),
            90,
            Some(context),
        ));
    }
    if graph_concepts > 0 {
        rows.push(row(
            "proactive_map_connections",
            "route",
            "brain.proactive.map",
            Some("/knowledge-graph"),
            format!("Map the strongest Knowledge Graph connections around {focus_title}."),
            82,
            Some(serde_json::json!({"focus": focus_title, "graph_concepts": graph_concepts})),
        ));
    }
    if state == "alive" && vector_items > 0 && healthy_sources > 0 {
        rows.push(row(
            "proactive_weekly_brief",
            "review",
            "brain.proactive.weekly",
            None,
            "Prepare a weekly Brain review: what changed, what decisions are pending, \
             what should be delegated, and what evidence is stale."
                .to_string(),
            78,
            Some(serde_json::json!({"vector_items": vector_items, "healthy_sources": healthy_sources})),
        ));
    }
    top4(rows)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn readiness(state: &str, score: i64, healthy: i64) -> Value {
        serde_json::json!({
            "score": score, "state": state,
            "signals": {"healthy_sources": healthy},
        })
    }

    fn manager_with(counts: [(&str, Value); 4], state: &str, score: i64) -> OrderedMap {
        let sources: Vec<Value> = counts
            .iter()
            .map(|(id, count)| serde_json::json!({"id": id, "count": count}))
            .collect();
        let mut manager = OrderedMap::new();
        manager.insert("sources", Value::Array(sources));
        manager.insert("brain_readiness", readiness(state, score, 6));
        manager
    }

    #[test]
    fn the_proof_separates_capability_from_evidence() {
        let manager = manager_with(
            [
                ("workspace", Value::from(0)),
                ("conversation", Value::from(2)),
                ("graph", Value::from(62)),
                ("vector", Value::Null),
            ],
            "alive",
            100,
        );
        let mut recall = OrderedMap::new();
        recall.insert("query", Value::String("ranking".into()));
        recall.insert(
            "results",
            serde_json::json!([{"id": "a", "score": 1.0, "kind": "Decision"}]),
        );
        recall.insert("count", Value::from(1));
        let proof = brain_proof(&manager, &recall, "ranking", "", 3, "2026-08-14T00:00:00");
        let body = serde_json::to_value(&proof).expect("json");
        assert_eq!(body["model_continuity"]["capability"], true);
        assert_eq!(body["model_continuity"]["proven"], true);
        assert_eq!(body["proofs"]["durable_items"], 64);
        assert_eq!(
            body["proofs"]["vector_items"], 0,
            "a null count reads as zero"
        );
        assert_eq!(body["recall"]["items"][0]["confidence"], "low");
        assert_eq!(
            body["recall"]["items"][0]["matched_terms"],
            serde_json::json!([])
        );
        assert_eq!(body["claims"]["is_knowledge_store"], true);
        assert_eq!(body["status"], "alive");
    }

    #[test]
    fn an_empty_brain_proves_nothing_and_says_so() {
        let manager = manager_with(
            [
                ("workspace", Value::from(0)),
                ("conversation", Value::from(0)),
                ("graph", Value::Null),
                ("vector", Value::Null),
            ],
            "quiet",
            12,
        );
        let mut recall = OrderedMap::new();
        recall.insert("query", Value::String(String::new()));
        recall.insert("results", Value::Array(Vec::new()));
        recall.insert("count", Value::from(0));
        let proof = brain_proof(&manager, &recall, "", "gpt", 3, "t");
        let body = serde_json::to_value(&proof).expect("json");
        assert_eq!(body["model_continuity"]["capability"], true);
        assert_eq!(body["model_continuity"]["proven"], false);
        assert_eq!(body["model_continuity"]["active_model"], "gpt");
        assert_eq!(body["claims"]["can_recall_user_context"], false);
        assert_eq!(body["claims"]["is_knowledge_store"], false);
    }

    #[test]
    fn the_action_lists_are_ordered_by_priority_and_capped_at_four() {
        let empty = next_actions("quiet", false, false, 0);
        assert_eq!(empty.len(), 2);
        assert_eq!(empty[0]["id"], "add_source");
        let full = next_actions("alive", true, true, 5);
        assert_eq!(full.len(), 4);
        assert_eq!(full[0]["id"], "ask_brain");
        assert_eq!(full[3]["id"], "backup_brain");
        let no_graph = next_actions("forming", true, false, 0);
        assert_eq!(no_graph.len(), 2, "no graph and no recall drops two rows");
    }

    #[test]
    fn the_suggestions_and_proactive_actions_follow_the_same_priorities() {
        let mut focus = OrderedMap::new();
        focus.insert("title", Value::String("Ranking".into()));
        focus.insert("kind", Value::String("recall".into()));
        focus.insert("detail", Value::String("alpha fusion".into()));
        let questions = suggested_questions(&focus, true, true, 4, 2);
        assert_eq!(questions.len(), 4);
        assert_eq!(questions[0]["id"], "focus_next");
        assert_eq!(questions[0]["params"]["focus"], "Ranking");
        let empty = suggested_questions(&focus, false, false, 0, 0);
        assert_eq!(empty.len(), 2);
        assert_eq!(empty[0]["id"], "start_brain");

        let proactive = proactive_actions(&focus, "alive", true, true, 4, 5, 6);
        assert_eq!(proactive.len(), 4);
        assert_eq!(proactive[0]["id"], "proactive_evidence_review");
        assert!(proactive[0]["prompt"]
            .as_str()
            .expect("prompt")
            .contains("Ranking"));
        assert_eq!(proactive[0]["context"]["focus"], "Ranking");
        let seeded = proactive_actions(&focus, "quiet", false, false, 0, 0, 0);
        assert_eq!(seeded.len(), 2);
        assert_eq!(seeded[0]["route"], "/capture");
        assert!(seeded[1].get("route").is_none(), "an ask has no route key");
    }
}
