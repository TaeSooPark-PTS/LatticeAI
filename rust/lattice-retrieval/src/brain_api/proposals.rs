//! Synthesis and contradiction proposals. Review items are Workspace OS state.

use std::collections::BTreeSet;

use lattice_auth::OrderedMap;
use serde_json::{json, Value};

use crate::memory_api::shared::BrainState;
use crate::memory_api::wsos;

use super::desk::{approve_item, create_review, open_keys, pending_synthesis};
use super::proactive;
use super::pyutil;
use super::quality::content_signature;
use super::sampling;
const CONTRADICTION_KIND: &str = "contradiction";
const CONCEPT_KIND: &str = "concept_cluster";
const EDGE_KIND: &str = "missing_edge";
const CONSOLIDATION_KIND: &str = "consolidation";
const RESOLUTIONS: [&str; 3] = ["keep_old", "replace", "keep_both_temporal"];
const MIN_SHARED_TOKENS: usize = 3;
const MIN_CLUSTER_MEMBERS: usize = 3;
const EDGE_SIMILARITY: f64 = 0.35;
const MAX_PROPOSALS: usize = 5;
const COMMON_TOKEN_RATIO: f64 = 0.4;

/// `BrainIntelligenceService.synthesize`.
pub async fn synthesize(
    state: &BrainState,
    user_email: &str,
    workspace_id: Option<&str>,
) -> OrderedMap {
    let sample = sampling::graph_sample(state, workspace_id).await;
    if !sample.available {
        return no_queue(
            "synthesis needs both the knowledge graph and the review queue",
            &state.now(),
        );
    }
    let contradictions = propose_from_sample(state, user_email, workspace_id, &sample).await;
    let consolidation = propose_consolidation(state, user_email, workspace_id, &sample);
    let concepts = propose_concepts(state, user_email, workspace_id, &sample);
    let links = propose_links(state, user_email, workspace_id, &sample);

    let counts = serde_json::json!({
        "contradictions": contradictions.get("proposed_count").and_then(Value::as_i64).unwrap_or(0),
        "concepts": concepts.get("proposed").and_then(Value::as_array).map(Vec::len).unwrap_or(0) as i64,
        "links": links.get("proposed").and_then(Value::as_array).map(Vec::len).unwrap_or(0) as i64,
        "consolidation": consolidation.get("proposed_count").and_then(Value::as_i64).unwrap_or(0),
    });
    let proposed_total = ["contradictions", "concepts", "links", "consolidation"]
        .iter()
        .filter_map(|key| counts.get(*key).and_then(Value::as_i64))
        .sum::<i64>();
    let suppressed = contradictions
        .get("suppressed")
        .and_then(Value::as_i64)
        .unwrap_or(0)
        + consolidation
            .get("suppressed")
            .and_then(Value::as_i64)
            .unwrap_or(0)
        + concepts
            .get("suppressed")
            .and_then(Value::as_i64)
            .unwrap_or(0)
        + links.get("suppressed").and_then(Value::as_i64).unwrap_or(0);

    let brief = brief_section(
        &sample,
        &counts,
        proposed_total,
        &state.now(),
        state.now_utc(),
    );
    let (threshold, pending, due_in) = state.synthesis_trigger();
    let trigger = serde_json::json!({
        "threshold": threshold,
        "pending": pending,
        "runs": 0,
        "last_fired_at": null,
        "due_in": due_in,
    });

    let mut out = OrderedMap::new();
    out.insert("nodes_scanned", Value::from(sample.nodes.len() as i64));
    out.insert("edges_scanned", Value::from(sample.edges.len() as i64));
    out.insert("counts", counts.clone());
    out.insert("proposed_total", Value::from(proposed_total));
    out.insert("suppressed", Value::from(suppressed));
    out.insert(
        "contradictions",
        serde_json::to_value(&contradictions).unwrap_or(Value::Null),
    );
    out.insert(
        "concepts",
        serde_json::to_value(&concepts).unwrap_or(Value::Null),
    );
    out.insert("links", serde_json::to_value(&links).unwrap_or(Value::Null));
    out.insert(
        "consolidation",
        serde_json::to_value(&consolidation).unwrap_or(Value::Null),
    );
    out.insert("brief", serde_json::to_value(&brief).unwrap_or(Value::Null));
    out.insert("trigger", trigger);
    out.insert("generated_at", Value::String(state.now()));
    out.insert("available", Value::Bool(true));
    let _ = user_email;
    out
}

/// `BrainIntelligenceService.propose_contradictions`.
pub async fn propose_contradictions(
    state: &BrainState,
    user_email: &str,
    workspace_id: Option<&str>,
) -> OrderedMap {
    let sample = sampling::graph_sample(state, workspace_id).await;
    if !sample.available {
        return no_queue(
            "contradiction proposals need both the knowledge graph and the review queue",
            &state.now(),
        );
    }
    propose_from_sample(state, user_email, workspace_id, &sample).await
}

async fn propose_from_sample(
    state: &BrainState,
    user_email: &str,
    workspace_id: Option<&str>,
    sample: &sampling::Sample,
) -> OrderedMap {
    let found = proactive::detect_contradictions(&sample.nodes, &sample.edges);
    let pairs = found
        .get("node_pairs")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let mut proposed: Vec<Value> = Vec::new();
    let mut suppressed = 0i64;
    let mut open = open_keys(state, workspace_id);
    for pair in pairs.iter().take(MAX_PROPOSALS) {
        match propose_one_contradiction(state, user_email, workspace_id, pair, sample, &mut open) {
            Some(item) => proposed.push(item),
            None => suppressed += 1,
        }
    }
    let mut out = OrderedMap::new();
    out.insert("available", Value::Bool(true));
    out.insert("pairs_detected", Value::from(pairs.len() as i64));
    out.insert("proposed", Value::Array(proposed.clone()));
    out.insert("proposed_count", Value::from(proposed.len() as i64));
    out.insert("suppressed", Value::from(suppressed));
    out.insert("nodes_scanned", Value::from(sample.nodes.len() as i64));
    out.insert("generated_at", Value::String(state.now()));
    out
}

fn propose_one_contradiction(
    state: &BrainState,
    user_email: &str,
    workspace_id: Option<&str>,
    pair: &Value,
    sample: &sampling::Sample,
    open: &mut BTreeSet<String>,
) -> Option<Value> {
    let left_id = pyutil::text_of(pair.get("left_id"));
    let right_id = pyutil::text_of(pair.get("right_id"));
    if left_id.is_empty() || right_id.is_empty() || left_id == right_id {
        return None;
    }
    let by_id: std::collections::HashMap<String, &Value> = sample
        .nodes
        .iter()
        .filter_map(|node| {
            node.get("id")
                .and_then(Value::as_str)
                .map(|id| (id.to_string(), node))
        })
        .collect();
    let (older_id, newer_id) = order_by_age(&left_id, &right_id, &by_id);
    let older = by_id.get(&older_id).copied();
    let newer = by_id.get(&newer_id).copied();
    let older_title = older.map(title_of).unwrap_or_else(|| older_id.clone());
    let newer_title = newer.map(title_of).unwrap_or_else(|| newer_id.clone());
    let key = pair_key(CONTRADICTION_KIND, &older_id, &newer_id);
    if open.contains(&key) {
        return None;
    }
    let summary = format!(
        "'{older_title}'와(과) '{newer_title}'가 서로 어긋납니다. \
         예전 기억을 그대로 둘지, 새 기억으로 바꿀지, \
         둘 다 남기고 각각 언제 맞았는지 표시할지 골라주세요."
    );
    let payload = serde_json::json!({
        "older": memory_brief(&older_id, older, pair, "left"),
        "newer": memory_brief(&newer_id, newer, pair, "right"),
        "signal": pair.get("signal").cloned().unwrap_or(Value::Null),
        "options": [
            {"id": "keep_old", "label": "예전 기억을 유지"},
            {"id": "replace", "label": "새 기억으로 교체"},
            {"id": "keep_both_temporal", "label": "둘 다 유지하고 기간 표시"},
        ],
    });
    let item = create_review(
        state,
        &format!("모순된 기억: {older_title} ↔ {newer_title}"),
        &summary,
        CONTRADICTION_KIND,
        &key,
        payload,
        user_email,
        workspace_id,
    )?;
    open.insert(key);
    Some(item)
}

fn memory_brief(node_id: &str, node: Option<&Value>, pair: &Value, side: &str) -> Value {
    let mut content = pair
        .get(format!("{side}_content"))
        .cloned()
        .unwrap_or(Value::String(String::new()));
    if pyutil::text_of(pair.get(format!("{side}_id"))) != node_id {
        let other = if side == "left" { "right" } else { "left" };
        content = pair
            .get(format!("{other}_content"))
            .cloned()
            .unwrap_or(content);
    }
    serde_json::json!({
        "id": node_id,
        "title": node.map(title_of).unwrap_or_default(),
        "type": node.and_then(|n| n.get("type")).cloned().unwrap_or(Value::Null),
        "updated_at": node.and_then(|n| n.get("updated_at")).cloned().unwrap_or(Value::Null),
        "content": pyutil::head(&pyutil::text_of(Some(&content)), 200),
    })
}

fn order_by_age(
    left_id: &str,
    right_id: &str,
    by_id: &std::collections::HashMap<String, &Value>,
) -> (String, String) {
    let left_ts = by_id
        .get(left_id)
        .map(|node| pyutil::field_text(node, "updated_at"))
        .unwrap_or_default();
    let right_ts = by_id
        .get(right_id)
        .map(|node| pyutil::field_text(node, "updated_at"))
        .unwrap_or_default();
    if (&left_ts, left_id) <= (&right_ts, right_id) {
        (left_id.to_string(), right_id.to_string())
    } else {
        (right_id.to_string(), left_id.to_string())
    }
}

/// `BrainIntelligenceService.resolve_contradiction`.
pub async fn resolve_contradiction(
    state: &BrainState,
    item_id: &str,
    resolution: &str,
    workspace_id: Option<&str>,
) -> Result<OrderedMap, (u16, String)> {
    if !RESOLUTIONS.contains(&resolution) {
        return Err((
            400,
            "resolution must be one of ['keep_old', 'replace', 'keep_both_temporal']".to_string(),
        ));
    }
    let doc = wsos::load(state.store(), state.data_dir());
    let items = wsos::scoped(
        doc.get("review_items")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default(),
        workspace_id,
    );
    let item = items
        .iter()
        .find(|row| row.get("id").and_then(Value::as_str) == Some(item_id));
    let Some(item) = item else {
        return Err((404, item_id.to_string()));
    };
    if item.get("kind").and_then(Value::as_str) != Some(CONTRADICTION_KIND) {
        return Err((
            400,
            format!("review item {item_id} is not a contradiction proposal"),
        ));
    }
    let payload = item.get("payload").cloned().unwrap_or(Value::Null);
    let older_id = pyutil::text_of(payload.get("older").and_then(|v| v.get("id")));
    let newer_id = pyutil::text_of(payload.get("newer").and_then(|v| v.get("id")));
    if older_id.is_empty() || newer_id.is_empty() {
        return Err((
            400,
            format!("review item {item_id} carries no memory pair to resolve"),
        ));
    }
    // Python's order, and it matters: `review_queue.approve` must return
    // before a single column is stamped, so a queue that refuses leaves the
    // graph untouched. The status it answers with is the one reported
    // (`str(approved.get("status") or "")`), not a hard-coded "approved".
    let moment = state.now();
    let status = approve_item(state, item_id);
    let args = serde_json::json!({
        "older_id": older_id,
        "newer_id": newer_id,
        "resolution": resolution,
        "at": moment,
    });
    // Native since v11.7.0 (`GraphWriter::stamp_node_validity`). It used to be
    // a `POST /worker/graph/mutate` the worker stopped serving in v11.6.0, so
    // every resolution silently reported `stamps: []`.
    let stamps = match state.mutate("stamp_contradiction", args).await {
        Ok(value) => value,
        Err(_) => Value::Array(Vec::new()),
    };
    let mut out = OrderedMap::new();
    out.insert("item_id", Value::String(item_id.to_string()));
    out.insert("resolution", Value::String(resolution.to_string()));
    out.insert("status", Value::String(status));
    out.insert("applied_at", Value::String(moment));
    out.insert("stamps", stamps);
    out.insert("available", Value::Bool(true));
    Ok(out)
}

/// `BrainIntelligenceService.proactive_brief`.
pub async fn proactive_brief(
    state: &BrainState,
    _user_email: &str,
    workspace_id: Option<&str>,
) -> OrderedMap {
    let mut section = OrderedMap::new();
    section.insert("available", Value::Bool(false));
    section.insert("pending", serde_json::json!({"total": 0, "by_kind": {}}));
    section.insert("tidying", Value::Bool(false));
    section.insert("headline", Value::String(String::new()));
    section.insert("lines", Value::Array(Vec::new()));
    section.insert("generated_at", Value::String(state.now()));

    let doc = wsos::load(state.store(), state.data_dir());
    let pending = pending_synthesis(&doc, workspace_id);
    let mut by_kind: std::collections::BTreeMap<String, i64> = std::collections::BTreeMap::new();
    for item in &pending {
        let kind = item
            .get("kind")
            .and_then(Value::as_str)
            .unwrap_or("suggestion")
            .to_string();
        *by_kind.entry(kind).or_default() += 1;
    }
    let sample = sampling::graph_sample(state, workspace_id).await;
    let counts = serde_json::json!({
        "contradictions": by_kind.get(CONTRADICTION_KIND).copied().unwrap_or(0),
        "concepts": by_kind.get(CONCEPT_KIND).copied().unwrap_or(0),
        "links": by_kind.get(EDGE_KIND).copied().unwrap_or(0),
        "consolidation": by_kind.get(CONSOLIDATION_KIND).copied().unwrap_or(0),
    });
    let brief = brief_section(
        &sample,
        &counts,
        pending.len() as i64,
        &state.now(),
        state.now_utc(),
    );
    let items: Vec<Value> = pending
        .iter()
        .take(5)
        .map(|item| {
            serde_json::json!({
                "id": item.get("id").cloned().unwrap_or(Value::Null),
                "kind": item.get("kind").cloned().unwrap_or(Value::Null),
                "title": item.get("title").cloned().unwrap_or(Value::Null),
                "summary": item.get("summary").cloned().unwrap_or(Value::Null),
            })
        })
        .collect();

    section.insert("available", Value::Bool(true));
    section.insert(
        "pending",
        serde_json::json!({
            "total": pending.len() as i64,
            "by_kind": by_kind,
        }),
    );
    section.insert("items", Value::Array(items));
    section.insert(
        "tidying",
        Value::Bool(by_kind.get(CONSOLIDATION_KIND).copied().unwrap_or(0) > 0),
    );
    section.insert(
        "headline",
        brief
            .get("headline")
            .cloned()
            .unwrap_or(Value::String(String::new())),
    );
    section.insert(
        "lines",
        brief
            .get("lines")
            .cloned()
            .unwrap_or(Value::Array(Vec::new())),
    );
    section.insert(
        "recent_nodes",
        brief.get("recent_nodes").cloned().unwrap_or(Value::Null),
    );
    section
}

/// `now_utc` is UTC epoch seconds; the headline prints a 7-day count, which is
/// a threshold. See `BrainState::with_utc_clock`.
fn brief_section(
    sample: &sampling::Sample,
    counts: &Value,
    review_total: i64,
    now: &str,
    now_utc: f64,
) -> OrderedMap {
    let recent = recent_window(&sample.nodes, now_utc);
    // The four digest kinds are what the *lines* report. The headline's
    // "검토할 거리" is every pending review item (including self_model_fact).
    let headline = format!(
        "최근 7일 동안 새 기억 {}건이 쌓였고, Brain이 검토할 거리 {review_total}건을 찾았습니다.",
        recent
    );
    let lines = vec![
        format!(
            "모순되는 기억 {}쌍",
            counts
                .get("contradictions")
                .and_then(Value::as_i64)
                .unwrap_or(0)
        ),
        format!(
            "새 주제 후보 {}건",
            counts.get("concepts").and_then(Value::as_i64).unwrap_or(0)
        ),
        format!(
            "빠진 연결 {}건",
            counts.get("links").and_then(Value::as_i64).unwrap_or(0)
        ),
        format!(
            "정리할 오래된 기록 {}묶음",
            counts
                .get("consolidation")
                .and_then(Value::as_i64)
                .unwrap_or(0)
        ),
    ];
    let mut out = OrderedMap::new();
    out.insert("headline", Value::String(headline.clone()));
    out.insert("deterministic_headline", Value::String(headline));
    out.insert(
        "lines",
        Value::Array(lines.into_iter().map(Value::String).collect()),
    );
    out.insert("recent_nodes", Value::from(recent));
    out.insert("window_days", Value::from(7));
    out.insert("counts", counts.clone());
    out.insert("generated_at", Value::String(now.to_string()));
    out
}

fn recent_window(nodes: &[Value], now_utc: f64) -> i64 {
    let cutoff = now_utc - 7.0 * 86_400.0;
    nodes
        .iter()
        .filter(|node| {
            pyutil::parse_ts(node.get("updated_at"))
                .map(|stamp| stamp >= cutoff)
                .unwrap_or(false)
        })
        .count() as i64
}

fn propose_concepts(
    state: &BrainState,
    user_email: &str,
    workspace_id: Option<&str>,
    sample: &sampling::Sample,
) -> OrderedMap {
    let clusters = concept_clusters(&sample.nodes);
    let mut proposed = Vec::new();
    let mut suppressed = 0i64;
    let mut open = open_keys(state, workspace_id);
    for cluster in clusters.iter().take(MAX_PROPOSALS) {
        let token = pyutil::text_of(cluster.get("token"));
        let key = format!("{CONCEPT_KIND}:{token}");
        if open.contains(&key) {
            suppressed += 1;
            continue;
        }
        let size = cluster.get("size").and_then(Value::as_i64).unwrap_or(0);
        let titles = cluster
            .get("members")
            .and_then(Value::as_array)
            .map(|members| {
                members
                    .iter()
                    .take(3)
                    .map(|m| pyutil::text_of(m.get("title")))
                    .collect::<Vec<_>>()
                    .join(", ")
            })
            .unwrap_or_default();
        let mut payload = cluster.clone();
        if let Some(object) = payload.as_object_mut() {
            object
                .entry("node_type")
                .or_insert_with(|| json!("Concept"));
        }
        if let Some(item) = create_review(
            state,
            &format!("새 주제 후보: {token}"),
            &format!("'{token}'가 {size}개의 기억에 반복해서 나타납니다({titles} 등). 하나의 주제로 묶어둘까요?"),
            CONCEPT_KIND,
            &key,
            payload,
            user_email,
            workspace_id,
        ) {
            open.insert(key);
            proposed.push(item);
        } else {
            suppressed += 1;
        }
    }
    let mut out = OrderedMap::new();
    out.insert("clusters", Value::Array(clusters));
    out.insert("proposed", Value::Array(proposed));
    out.insert("suppressed", Value::from(suppressed));
    out
}

fn propose_links(
    state: &BrainState,
    user_email: &str,
    workspace_id: Option<&str>,
    sample: &sampling::Sample,
) -> OrderedMap {
    let pairs = unlinked_pairs(&sample.nodes, &sample.edges);
    let mut proposed = Vec::new();
    let mut suppressed = 0i64;
    let mut open = open_keys(state, workspace_id);
    for pair in pairs.iter().take(MAX_PROPOSALS) {
        let Some(left) = pair.get("left") else {
            continue;
        };
        let Some(right) = pair.get("right") else {
            continue;
        };
        let key = pair_key(
            EDGE_KIND,
            &pyutil::text_of(left.get("id")),
            &pyutil::text_of(right.get("id")),
        );
        if open.contains(&key) {
            suppressed += 1;
            continue;
        }
        let left_title = pyutil::text_of(left.get("title"));
        let right_title = pyutil::text_of(right.get("title"));
        let similarity = pair
            .get("similarity")
            .and_then(Value::as_f64)
            .unwrap_or(0.0);
        if let Some(item) = create_review(
            state,
            &format!("연결 제안: {left_title} ↔ {right_title}"),
            &format!(
                "'{left_title}'와(과) '{right_title}'는 자주 같이 등장하는데 아직 이어져 있지 않습니다(겹침 {}%). 연결할까요?",
                (similarity * 100.0) as i64
            ),
            EDGE_KIND,
            &key,
            pair.clone(),
            user_email,
            workspace_id,
        ) {
            open.insert(key);
            proposed.push(item);
        } else {
            suppressed += 1;
        }
    }
    let mut out = OrderedMap::new();
    out.insert("pairs", Value::Array(pairs));
    out.insert("proposed", Value::Array(proposed));
    out.insert("suppressed", Value::from(suppressed));
    out
}

fn propose_consolidation(
    state: &BrainState,
    user_email: &str,
    workspace_id: Option<&str>,
    sample: &sampling::Sample,
) -> OrderedMap {
    let stats = std::collections::HashMap::new();
    let report = proactive::importance_report(sample, &stats, &state.now(), state.now_utc());
    let candidates: Vec<Value> = report
        .get("candidates")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default()
        .into_iter()
        .take(MAX_PROPOSALS * 4)
        .collect();
    let mut proposed = Vec::new();
    let mut suppressed = 0i64;
    if candidates.len() >= 3 {
        let mut open = open_keys(state, workspace_id);
        let key = format!("consolidation:{}", {
            let mut ids: Vec<String> = candidates
                .iter()
                .map(|c| pyutil::text_of(c.get("id")))
                .collect();
            ids.sort();
            ids.join("|")
        });
        if open.contains(&key) {
            suppressed = 1;
        } else {
            let titles = candidates
                .iter()
                .take(3)
                .map(|c| {
                    let title = pyutil::text_of(c.get("title"));
                    if title.is_empty() {
                        pyutil::text_of(c.get("id"))
                    } else {
                        title
                    }
                })
                .collect::<Vec<_>>()
                .join(", ");
            if let Some(item) = create_review(
                state,
                &format!("오래된 기록 {}건 정리", candidates.len()),
                &format!(
                    "{titles} 등 {}건이 오랫동안 쓰이지 않았습니다. 하나의 요약으로 묶어둘까요? 원본은 그대로 남습니다.",
                    candidates.len()
                ),
                CONSOLIDATION_KIND,
                &key,
                serde_json::json!({
                    "candidates": candidates,
                    "half_life_days": report.get("half_life_days").cloned().unwrap_or(Value::Null),
                    "access_source": report.get("access_source").cloned().unwrap_or(Value::Null),
                }),
                user_email,
                workspace_id,
            ) {
                open.insert(key);
                proposed.push(item);
            }
        }
    }
    let mut out = OrderedMap::new();
    out.insert("available", Value::Bool(true));
    out.insert("candidate_count", Value::from(candidates.len() as i64));
    out.insert("proposed", Value::Array(proposed.clone()));
    out.insert("proposed_count", Value::from(proposed.len() as i64));
    out.insert("suppressed", Value::from(suppressed));
    out.insert(
        "report",
        serde_json::to_value(&report).unwrap_or(Value::Null),
    );
    out.insert("generated_at", Value::String(state.now()));
    out
}

fn concept_clusters(nodes: &[Value]) -> Vec<Value> {
    let existing: BTreeSet<String> = nodes
        .iter()
        .map(|node| pyutil::field_text(node, "title").trim().to_lowercase())
        .collect();
    let mut index: std::collections::BTreeMap<String, Vec<&Value>> =
        std::collections::BTreeMap::new();
    for node in nodes {
        let text = sampling::node_text(node);
        if text.chars().count() < 3 {
            continue;
        }
        for token in content_signature(&text) {
            index.entry(token).or_default().push(node);
        }
    }
    let ceiling = MIN_CLUSTER_MEMBERS.max((nodes.len() as f64 * COMMON_TOKEN_RATIO) as usize);
    let mut clusters = Vec::new();
    for (token, members) in index {
        if members.len() < MIN_CLUSTER_MEMBERS || members.len() > ceiling {
            continue;
        }
        if existing.contains(&token) {
            continue;
        }
        let listed: Vec<Value> = members
            .iter()
            .take(8)
            .map(|node| {
                serde_json::json!({
                    "id": pyutil::text_of(node.get("id")),
                    "title": title_of(node),
                })
            })
            .collect();
        clusters.push(serde_json::json!({
            "token": token,
            "size": members.len() as i64,
            "members": listed,
        }));
    }
    clusters.sort_by(|left, right| {
        let ls = left.get("size").and_then(Value::as_i64).unwrap_or(0);
        let rs = right.get("size").and_then(Value::as_i64).unwrap_or(0);
        rs.cmp(&ls).then_with(|| {
            pyutil::text_of(left.get("token")).cmp(&pyutil::text_of(right.get("token")))
        })
    });
    clusters
}

fn unlinked_pairs(nodes: &[Value], edges: &[Value]) -> Vec<Value> {
    let linked: BTreeSet<String> = edges
        .iter()
        .map(|edge| {
            pair_key(
                "e",
                &pyutil::text_of(edge.get("source")),
                &pyutil::text_of(edge.get("target")),
            )
        })
        .collect();
    let signatures: Vec<(&Value, BTreeSet<String>)> = nodes
        .iter()
        .filter(|node| sampling::node_text(node).chars().count() >= 3)
        .map(|node| (node, content_signature(&sampling::node_text(node))))
        .collect();
    let mut pairs = Vec::new();
    for (index, (left, left_sig)) in signatures.iter().enumerate() {
        for (right, right_sig) in signatures.iter().skip(index + 1) {
            let shared: BTreeSet<_> = left_sig.intersection(right_sig).cloned().collect();
            if shared.len() < MIN_SHARED_TOKENS {
                continue;
            }
            let left_id = pyutil::text_of(left.get("id"));
            let right_id = pyutil::text_of(right.get("id"));
            if linked.contains(&pair_key("e", &left_id, &right_id)) {
                continue;
            }
            let union = left_sig.union(right_sig).count();
            let similarity = shared.len() as f64 / union as f64;
            if similarity < EDGE_SIMILARITY {
                continue;
            }
            let mut shared_tokens: Vec<String> = shared.into_iter().collect();
            shared_tokens.sort();
            shared_tokens.truncate(8);
            pairs.push(serde_json::json!({
                "left": {"id": left_id, "title": title_of(left)},
                "right": {"id": right_id, "title": title_of(right)},
                "source": {"id": left_id, "title": title_of(left)},
                "target": {"id": right_id, "title": title_of(right)},
                "similarity": pyutil::round_to(similarity, 4),
                "shared_tokens": shared_tokens,
            }));
        }
    }
    pairs.sort_by(|left, right| {
        let ls = left
            .get("similarity")
            .and_then(Value::as_f64)
            .unwrap_or(0.0);
        let rs = right
            .get("similarity")
            .and_then(Value::as_f64)
            .unwrap_or(0.0);
        rs.partial_cmp(&ls)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| {
                pyutil::text_of(left.get("left").and_then(|v| v.get("id"))).cmp(&pyutil::text_of(
                    right.get("left").and_then(|v| v.get("id")),
                ))
            })
    });
    pairs
}

fn title_of(node: &Value) -> String {
    let title = lattice_core::pytext::strip(&pyutil::field_text(node, "title"));
    if title.is_empty() {
        pyutil::text_of(node.get("id"))
    } else {
        title
    }
}

fn pair_key(prefix: &str, left: &str, right: &str) -> String {
    let (a, b) = if left <= right {
        (left, right)
    } else {
        (right, left)
    };
    format!("{prefix}:{a}|{b}")
}

fn no_queue(detail: &str, now: &str) -> OrderedMap {
    let mut out = OrderedMap::new();
    out.insert("available", Value::Bool(false));
    out.insert("detail", Value::String(detail.to_string()));
    out.insert("generated_at", Value::String(now.to_string()));
    out
}
