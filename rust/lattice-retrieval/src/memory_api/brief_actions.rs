use lattice_auth::OrderedMap;
use serde_json::Value;

use super::json;

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

pub(super) fn next_actions(
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

pub(super) fn suggested_questions(
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

pub(super) fn proactive_actions(
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
