//! `CommandCenterService.briefing` — the seven sections and the quick actions.

use lattice_auth::OrderedMap;
use serde_json::Value;

use crate::history::{self, HistoryScope};
use crate::memory_api::kg;
use crate::memory_api::shared::BrainState;
use crate::memory_api::wsos;

use super::health;
use super::store::{self, clip};
use super::suggestions;

const RECENT_NODE_LIMIT: usize = 6;
const BRIEFING_HISTORY_LIMIT: i64 = 2000;
const HYGIENE_MIN_NODES: i64 = 200;
const HYGIENE_STALE_DAYS: i64 = 7;

/// One `GET /api/command/briefing` call.
pub async fn briefing(
    state: &BrainState,
    user_email: &str,
    workspace_id: Option<&str>,
) -> Result<OrderedMap, axum::response::Response> {
    let now = state.now();
    let doc = wsos::load(state.store(), state.data_dir());
    let email = user_email.to_string();
    let scope = workspace_id.map(str::to_string);
    let graph = state.graph_enabled();
    let db_path = state.store().path().display().to_string();
    // The instant the health and hygiene sections measure against, read once so
    // both agree and so a replay can freeze it (`BrainState::with_utc_clock`).
    let now_utc = state.now_utc();

    let snapshot = state
        .read(move |conn| {
            let knowledge = knowledge_section(conn, graph, scope.as_deref());
            let conversations = conversation_section(conn, &email, scope.as_deref());
            let sample = health::graph_sample(conn, scope.as_deref(), graph);
            let health = health::health_report(conn, &db_path, &sample, graph, now_utc);
            let hygiene = hygiene_section(conn, graph, &db_path, now_utc);
            Ok((knowledge, conversations, health, hygiene))
        })
        .await?;

    let (knowledge, conversations, health_report, hygiene) = snapshot;
    let automations = automation_section(&doc, workspace_id);
    let review = review_section(&doc, workspace_id, user_email, &now);
    let health = health_section(&health_report);
    let suggestion_items = {
        let request = suggestions::Request {
            user_email,
            workspace_id,
            state: &doc,
            enable_graph: graph,
        };
        // A read failure leaves the section empty rather than failing the whole
        // briefing — the other six sections are already in hand.
        state
            .read({
                let email = user_email.to_string();
                let scope = workspace_id.map(str::to_string);
                let doc = doc.clone();
                move |conn| {
                    Ok(suggestions::suggestions(
                        conn,
                        &suggestions::Request {
                            user_email: &email,
                            workspace_id: scope.as_deref(),
                            state: &doc,
                            enable_graph: request.enable_graph,
                        },
                    ))
                }
            })
            .await
            .unwrap_or_default()
    };
    let suggestion_section = suggestion_section(&suggestion_items);

    let mut sections = OrderedMap::new();
    sections.insert("knowledge", json(&knowledge));
    sections.insert("conversations", json(&conversations));
    sections.insert("automations", json(&automations));
    sections.insert("review", json(&review));
    sections.insert("health", json(&health));
    sections.insert("suggestions", json(&suggestion_section));
    sections.insert("hygiene", json(&hygiene));

    let actions = quick_actions(&sections);
    let mut out = OrderedMap::new();
    out.insert("generated_at", Value::String(now));
    out.insert("sections", json(&sections));
    out.insert("quick_actions", Value::Array(actions));
    Ok(out)
}

fn json(map: &OrderedMap) -> Value {
    serde_json::to_value(map).unwrap_or(Value::Null)
}

fn knowledge_section(
    conn: &rusqlite::Connection,
    graph: bool,
    workspace_id: Option<&str>,
) -> OrderedMap {
    let mut out = OrderedMap::new();
    if !graph {
        out.insert("available", Value::Bool(false));
        out.insert("recent", Value::Array(Vec::new()));
        return out;
    }
    let allowed = workspace_id.map(|id| {
        let mut set = std::collections::BTreeSet::new();
        set.insert(id.to_string());
        set
    });
    let Ok(slice) = kg::graph_slice(conn, 50, allowed.as_ref()) else {
        out.insert("available", Value::Bool(false));
        out.insert("recent", Value::Array(Vec::new()));
        return out;
    };
    let recent: Vec<Value> = slice
        .nodes
        .iter()
        .take(RECENT_NODE_LIMIT)
        .map(|node| {
            let mut item = OrderedMap::new();
            item.insert("id", node.get("id").cloned().unwrap_or(Value::Null));
            item.insert(
                "title",
                Value::String(clip(node.get("title").unwrap_or(&Value::Null), 120)),
            );
            item.insert("type", node.get("type").cloned().unwrap_or(Value::Null));
            item.insert(
                "updated_at",
                node.get("updated_at").cloned().unwrap_or(Value::Null),
            );
            serde_json::to_value(&item).unwrap_or(Value::Null)
        })
        .collect();
    out.insert("available", Value::Bool(true));
    out.insert("recent", Value::Array(recent));
    out.insert("sampled_nodes", Value::from(slice.nodes.len() as i64));
    out
}

fn conversation_section(
    conn: &rusqlite::Connection,
    user_email: &str,
    workspace_id: Option<&str>,
) -> OrderedMap {
    let scope = HistoryScope {
        user_email: Some(user_email.to_string()),
        allowed_workspaces: workspace_id.map(|id| vec![id.to_string()]),
        include_legacy_global: workspace_id.is_none(),
    };
    let items =
        history::history(conn, None, Some(BRIEFING_HISTORY_LIMIT), &scope).unwrap_or_default();
    let mut out = OrderedMap::new();
    if items.is_empty() {
        out.insert("available", Value::Bool(true));
        out.insert("messages", Value::from(0));
        out.insert("questions", Value::from(0));
        return out;
    }
    let user_items: Vec<&Value> = items
        .iter()
        .filter(|item| item.get("role").and_then(Value::as_str) == Some("user"))
        .collect();
    let last = items.last();
    let last_question = user_items
        .iter()
        .rev()
        .find_map(|item| item.get("content"))
        .map(|value| clip(value, 120))
        .unwrap_or_default();
    out.insert("available", Value::Bool(true));
    out.insert("messages", Value::from(items.len() as i64));
    out.insert("questions", Value::from(user_items.len() as i64));
    out.insert(
        "last_active",
        Value::String(
            last.and_then(|item| item.get("timestamp"))
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string(),
        ),
    );
    out.insert("last_question", Value::String(last_question));
    out
}

fn automation_section(state: &Value, workspace_id: Option<&str>) -> OrderedMap {
    let workflows = store::workflows(state, workspace_id);
    let mut enabled = 0i64;
    let mut drafts = 0i64;
    let mut last_execution: Option<Value> = None;
    let mut last_finished = String::new();
    for workflow in &workflows {
        let metadata = workflow.get("metadata").cloned().unwrap_or(Value::Null);
        match metadata.get("automation_state").and_then(Value::as_str) {
            Some("enabled") => enabled += 1,
            Some("draft_disabled") => drafts += 1,
            _ => {}
        }
        if let Some(stamped) = metadata.get("last_execution").filter(|v| v.is_object()) {
            let finished = stamped
                .get("finished_at")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            if finished > last_finished {
                last_finished = finished.clone();
                let mut item = OrderedMap::new();
                item.insert(
                    "workflow_id",
                    workflow.get("id").cloned().unwrap_or(Value::Null),
                );
                item.insert(
                    "name",
                    Value::String(clip(workflow.get("name").unwrap_or(&Value::Null), 120)),
                );
                item.insert("mode", stamped.get("mode").cloned().unwrap_or(Value::Null));
                item.insert(
                    "status",
                    stamped.get("status").cloned().unwrap_or(Value::Null),
                );
                item.insert(
                    "summary",
                    Value::String(clip(stamped.get("summary").unwrap_or(&Value::Null), 200)),
                );
                item.insert(
                    "finished_at",
                    stamped.get("finished_at").cloned().unwrap_or(Value::Null),
                );
                last_execution = Some(json(&item));
            }
        }
    }
    let mut out = OrderedMap::new();
    out.insert("available", Value::Bool(true));
    out.insert("total", Value::from(workflows.len() as i64));
    out.insert("enabled", Value::from(enabled));
    out.insert("drafts", Value::from(drafts));
    if let Some(last) = last_execution {
        out.insert("last_execution", last);
    }
    out
}

fn review_section(
    state: &Value,
    workspace_id: Option<&str>,
    user_email: &str,
    now: &str,
) -> OrderedMap {
    let now_secs = lattice_core::pytext::parse_iso(Some(now)).unwrap_or(0.0);
    let pending = store::pending_reviews(state, workspace_id, user_email, now_secs);
    let mut out = OrderedMap::new();
    out.insert("available", Value::Bool(true));
    out.insert("pending", Value::from(pending));
    out
}

fn health_section(report: &health::HealthReport) -> OrderedMap {
    let mut out = OrderedMap::new();
    out.insert("available", Value::Bool(report.overall_score.is_some()));
    out.insert(
        "grade",
        report
            .grade
            .map(|g| Value::String(g.to_string()))
            .unwrap_or(Value::Null),
    );
    out.insert(
        "score",
        report.overall_score.map(Value::from).unwrap_or(Value::Null),
    );
    out.insert(
        "recommended_actions",
        Value::Array(report.actions.iter().take(3).cloned().collect()),
    );
    out
}

fn suggestion_section(items: &[suggestions::Suggestion]) -> OrderedMap {
    let kept: Vec<&suggestions::Suggestion> = items.iter().filter(|item| !item.installed).collect();
    let top: Vec<Value> = kept
        .iter()
        .take(3)
        .map(|item| {
            let mut row = OrderedMap::new();
            row.insert("id", Value::String(item.id.clone()));
            row.insert("kind", Value::String(item.kind.to_string()));
            row.insert(
                "title",
                Value::String({
                    let text = Value::String(item.title.clone());
                    clip(&text, 120)
                }),
            );
            json(&row)
        })
        .collect();
    let mut out = OrderedMap::new();
    out.insert("available", Value::Bool(true));
    out.insert("count", Value::from(kept.len() as i64));
    out.insert("top", Value::Array(top));
    out
}

fn hygiene_section(
    conn: &rusqlite::Connection,
    graph: bool,
    db_path: &str,
    now_utc: f64,
) -> OrderedMap {
    let mut out = OrderedMap::new();
    out.insert("available", Value::Bool(false));
    out.insert("suggest_noise_curate", Value::Bool(false));
    out.insert("reason", Value::String(String::new()));
    out.insert("last_noise_curate_at", Value::Null);
    out.insert("node_count", Value::from(0));
    if !graph {
        return out;
    }
    let Ok(stats) = kg::stats(conn, db_path, None) else {
        return out;
    };
    let node_count: i64 = stats
        .get("nodes")
        .and_then(Value::as_object)
        .map(|map| map.values().filter_map(Value::as_i64).sum())
        .unwrap_or(0);
    let last = store::last_noise_curate_at(conn);
    out.insert("available", Value::Bool(true));
    out.insert("node_count", Value::from(node_count));
    out.insert(
        "last_noise_curate_at",
        last.clone().map(Value::String).unwrap_or(Value::Null),
    );
    if node_count < HYGIENE_MIN_NODES {
        return out;
    }
    if let Some(stamp) = last.as_deref() {
        if !store::older_than_days(stamp, HYGIENE_STALE_DAYS, now_utc) {
            return out;
        }
    }
    out.insert("suggest_noise_curate", Value::Bool(true));
    let reason = if last.is_some() {
        format!("{node_count} nodes and no noise curation in the last {HYGIENE_STALE_DAYS} days")
    } else {
        format!("{node_count} nodes and no noise curation recorded")
    };
    out.insert("reason", Value::String(reason));
    out
}

fn quick_actions(sections: &OrderedMap) -> Vec<Value> {
    let mut actions = Vec::new();
    if let Some(review) = sections.get("review") {
        if let Some(pending) = review.get("pending").and_then(Value::as_i64) {
            if pending != 0 {
                actions.push(action(
                    "review-pending",
                    "review",
                    pending,
                    "/act/review",
                    None,
                ));
            }
        }
    }
    if let Some(automations) = sections.get("automations") {
        if let Some(drafts) = automations.get("drafts").and_then(Value::as_i64) {
            if drafts != 0 {
                actions.push(action(
                    "enable-drafts",
                    "automation",
                    drafts,
                    "/act/workflows",
                    None,
                ));
            }
        }
    }
    if let Some(suggestions) = sections.get("suggestions") {
        if let Some(count) = suggestions.get("count").and_then(Value::as_i64) {
            if count != 0 {
                actions.push(action(
                    "install-suggestion",
                    "suggestion",
                    count,
                    "/act/workflows",
                    None,
                ));
            }
        }
    }
    if let Some(knowledge) = sections.get("knowledge") {
        let available = knowledge.get("available").and_then(Value::as_bool) == Some(true);
        let recent_empty = knowledge
            .get("recent")
            .and_then(Value::as_array)
            .map(Vec::is_empty)
            .unwrap_or(true);
        if available && recent_empty {
            actions.push(action(
                "connect-knowledge",
                "capture",
                0,
                "/capture/files",
                None,
            ));
        }
    }
    if let Some(health) = sections.get("health") {
        let available = health.get("available").and_then(Value::as_bool) == Some(true);
        let score = health.get("score").and_then(Value::as_i64);
        if available {
            if let Some(score) = score {
                if score < 70 {
                    actions.push(action("check-health", "health", 0, "/brain/graph", None));
                }
            }
        }
    }
    if let Some(hygiene) = sections.get("hygiene") {
        if hygiene.get("suggest_noise_curate").and_then(Value::as_bool) == Some(true) {
            let count = hygiene
                .get("node_count")
                .and_then(Value::as_i64)
                .unwrap_or(0);
            actions.push(action(
                "curate-noise",
                "hygiene",
                count,
                "/brain/graph",
                Some("/knowledge-graph/curate/noise"),
            ));
        }
    }
    if actions.is_empty() {
        actions.push(action("ask-brain", "chat", 0, "/brain", None));
    }
    actions
}

fn action(id: &str, kind: &str, count: i64, target: &str, endpoint: Option<&str>) -> Value {
    let mut out = OrderedMap::new();
    out.insert("id", Value::String(id.to_string()));
    out.insert("kind", Value::String(kind.to_string()));
    out.insert("count", Value::from(count));
    out.insert("target", Value::String(target.to_string()));
    if let Some(endpoint) = endpoint {
        out.insert("endpoint", Value::String(endpoint.to_string()));
    }
    json(&out)
}
