use std::collections::{BTreeMap, BTreeSet};

use fancy_regex::Regex;
use lattice_core::graph_write::types::{EdgeSpec, NodeSpec};
use lattice_core::graph_write::GraphWriter;
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};

use crate::brain_api::desk;
use crate::memory_api::shared::{BrainState, SeamRefusal};
use crate::self_model::{kind_label, SELF_ID_PREFIX, SELF_ROOT_ID};

pub const SELF_MODEL_KIND: &str = "self_model_fact";

const MAX_FACT_CHARS: usize = 120;

const TRAILING: &str = " .。!?！？,、;:\n\t\"'\u{201c}\u{201d}\u{2018}\u{2019}()[]{}";

const KEEP_OLD: &str = "keep_old";
const REPLACE: &str = "replace";

pub fn node_type_for(kind: &str) -> Option<&'static str> {
    match kind {
        "preference" => Some("Preference"),
        "decision" => Some("Decision"),
        "habit" => Some("Habit"),
        "relationship" => Some("Relationship"),
        "trait" => Some("Self"),
        _ => None,
    }
}

pub fn normalize(text: &str) -> String {
    let collapsed = text.split_whitespace().collect::<Vec<_>>().join(" ");
    let trimmed = collapsed.trim_matches(|c| TRAILING.contains(c));
    trimmed.chars().take(MAX_FACT_CHARS).collect()
}

pub fn fact_id(kind: &str, text: &str) -> String {
    let digest = Sha256::digest(format!("{kind}|{}", text.to_lowercase()).as_bytes());
    let hex: String = digest.iter().map(|byte| format!("{byte:02x}")).collect();
    format!("{SELF_ID_PREFIX}{kind}:{}", &hex[..12])
}

fn patterns() -> &'static [(&'static str, Regex, f64, &'static str)] {
    static TABLE: std::sync::OnceLock<Vec<(&'static str, Regex, f64, &'static str)>> =
        std::sync::OnceLock::new();
    TABLE.get_or_init(|| {
        let rows: [(&'static str, &'static str, f64, &'static str); 18] = [
            (
                "decision",
                r"결정\s*[:：]\s*(?P<v>[^\n]+)",
                0.9,
                "ko_decision_marker",
            ),
            (
                "decision",
                r"(?P<v>[^\n.。]+?)\s*하기로\s*(?:했|결정했)",
                0.75,
                "ko_decided_to",
            ),
            (
                "decision",
                r"(?i)\bDecision\s*:\s*(?P<v>[^\n]+)",
                0.9,
                "en_decision_marker",
            ),
            (
                "decision",
                r"(?i)\b(?:I|we)\s+decided\s+to\s+(?P<v>[^\n.!?]+)",
                0.75,
                "en_decided_to",
            ),
            (
                "preference",
                r"(?:나는|저는|내가|제가)\s*(?P<v>[^\n.。]+?)\s*(?:을|를|이|가)?\s*(?:좋아|선호|싫어)(?:합니다|한다|해요|해|하고)",
                0.7,
                "ko_first_person_preference",
            ),
            (
                "preference",
                r"(?:답변은|설명은|코드는)\s*(?P<v>[^\n.。!?]+?)\s*(?:해\s*주세요|작성해\s*주세요|부탁합니다|좋겠습니다)",
                0.72,
                "ko_style_preference",
            ),
            (
                "preference",
                r"(?i)\bI\s+(?:prefer|like|love|hate|dislike|avoid|would\s+rather)\s+(?P<v>[^\n.!?]+)",
                0.7,
                "en_first_person_preference",
            ),
            (
                "preference",
                r"(?i)\bPlease\s+always\s+(?P<v>[^\n.!?]+)",
                0.72,
                "en_always_preference",
            ),
            (
                "habit",
                r"(?P<v>(?:매일|매주|매달|아침마다|항상|늘|평소)\s*[^\n.。!?]+)",
                0.65,
                "ko_routine",
            ),
            (
                "habit",
                r"(?:나는|저는)\s*(?P<v>주로\s*[^\n.。!?]+?(?:사용합니다|씁니다|활용합니다|일합니다))",
                0.68,
                "ko_primary_tool_habit",
            ),
            (
                "habit",
                r"(?i)\bI\s+(?P<v>(?:always|usually|normally|every\s+(?:morning|day|week))\s+[^\n.!?]+)",
                0.65,
                "en_routine",
            ),
            (
                "habit",
                r"(?i)\bI\s+(?P<v>primarily\s+(?:use|work\s+with)\s+[^\n.!?]+)",
                0.68,
                "en_primary_tool_habit",
            ),
            (
                "relationship",
                r"(?:내|제)\s*(?P<v>(?:동료|팀장|매니저|친구|파트너|상사|멘토|공동창업자)\s+\S+)",
                0.6,
                "ko_relationship",
            ),
            (
                "relationship",
                r"(?i)\bmy\s+(?P<v>(?:colleague|manager|teammate|friend|partner|mentor|boss|cofounder)\s+[^\n.!?,]+)",
                0.6,
                "en_relationship",
            ),
            (
                "trait",
                r"(?:나는|저는)\s*(?P<v>[^\n.。]*?(?:개발자|디자이너|엔지니어|연구원|학생|기획자|아키텍트|창업자|데이터\s*사이언티스트|교수))\s*(?:입니다|이다|다|예요)",
                0.6,
                "ko_role",
            ),
            (
                "trait",
                r"(?i)\bI\s+am\s+an?\s+(?P<v>[^\n.!?]+)",
                0.6,
                "en_role",
            ),
            (
                "trait",
                r"(?i)\bI\s+work\s+as\s+an?\s+(?P<v>[^\n.!?]+)",
                0.65,
                "en_work_as_role",
            ),
            (
                "decision",
                r"(?i)\bI\s+never\s+(?P<v>[^\n.!?]+)",
                0.68,
                "en_never_constraint",
            ),
        ];
        rows.into_iter()
            .map(|(kind, source, confidence, signal)| {
                (
                    kind,
                    Regex::new(source).expect("self-model pattern compiles"),
                    confidence,
                    signal,
                )
            })
            .collect()
    })
}

pub fn extract(text: &str, source: &str) -> Vec<Value> {
    let mut found: BTreeMap<String, Value> = BTreeMap::new();
    let mut order: Vec<String> = Vec::new();
    for (kind, pattern, confidence, signal) in patterns() {
        let mut cursor = 0usize;
        while cursor <= text.len() {
            let Ok(Some(captures)) = pattern.captures_from_pos(text, cursor) else {
                break;
            };
            let whole = captures.get(0).expect("group 0");
            cursor = if whole.end() > whole.start() {
                whole.end()
            } else {
                next_boundary(text, whole.end())
            };
            let Some(group) = captures.name("v") else {
                continue;
            };
            let value = normalize(group.as_str());
            if value.chars().count() < 2 {
                continue;
            }
            let identifier = fact_id(kind, &value);
            if found.contains_key(&identifier) {
                continue;
            }
            order.push(identifier.clone());
            found.insert(
                identifier.clone(),
                json!({
                    "id": identifier,
                    "kind": kind,
                    "text": value,
                    "confidence": confidence,
                    "signal": signal,
                    "source": source,
                }),
            );
        }
    }
    let mut candidates: Vec<Value> = order
        .iter()
        .filter_map(|id| found.get(id).cloned())
        .collect();
    candidates.sort_by(|left, right| {
        let key = |fact: &Value| {
            (
                fact.get("kind")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .to_string(),
                fact.get("text")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .to_string(),
            )
        };
        key(left).cmp(&key(right))
    });
    candidates
}

fn next_boundary(text: &str, from: usize) -> usize {
    let mut index = from + 1;
    while index < text.len() && !text.is_char_boundary(index) {
        index += 1;
    }
    index
}

#[allow(clippy::too_many_arguments)]
fn write_fact(
    graph: &GraphWriter,
    kind: &str,
    text: &str,
    workspace_id: Option<&str>,
    origin: &str,
    confidence: f64,
    signal: &str,
    item_id: Option<&str>,
) -> Result<Value, SeamRefusal> {
    let Some(node_type) = node_type_for(kind) else {
        return Err(refusal(400, "invalid_kind"));
    };
    let node_id = fact_id(kind, text);
    let metadata = json!({
        "self_model": true,
        "self_model_kind": kind,
        "origin": origin,
        "confidence": confidence,
        "signal": signal,
        "workspace_id": workspace_id,
        "review_item_id": item_id,
    });
    let root = NodeSpec {
        id: SELF_ROOT_ID.to_string(),
        node_type: "Self".to_string(),
        title: "나".to_string(),
        summary: "Brain이 사용자에 대해 알고 있는 사실의 뿌리입니다.".to_string(),
        metadata: object_of(json!({"self_model": true, "self_model_kind": "root"})),
        raw: Map::new(),
        owner: None,
        workspace_id: workspace_id.map(str::to_string),
        visibility: None,
    };
    let fact = NodeSpec {
        id: node_id.clone(),
        node_type: node_type.to_string(),
        title: text.to_string(),
        summary: text.to_string(),
        metadata: object_of(metadata),
        raw: Map::new(),
        owner: None,
        workspace_id: workspace_id.map(str::to_string),
        visibility: None,
    };
    graph.upsert_nodes(&[root, fact]).map_err(internal)?;
    graph
        .upsert_edges(&[EdgeSpec {
            from_node: node_id.clone(),
            to_node: SELF_ROOT_ID.to_string(),
            edge_type: "PART_OF".to_string(),
            weight: 1.0,
            metadata: Map::new(),
            legacy_label: None,
        }])
        .map_err(internal)?;
    Ok(json!({
        "id": node_id,
        "kind": kind,
        "type": node_type,
        "text": text,
        "origin": origin,
        "confidence": confidence,
        "signal": signal,
        "workspace_id": workspace_id,
    }))
}

fn object_of(value: Value) -> Map<String, Value> {
    value.as_object().cloned().unwrap_or_default()
}

fn refusal(status: u16, code: &str) -> SeamRefusal {
    SeamRefusal {
        status,
        detail: code.to_string(),
    }
}

fn internal(error: impl std::fmt::Display) -> SeamRefusal {
    SeamRefusal {
        status: 500,
        detail: format!("self_model write failed: {error}"),
    }
}

fn upsert(graph: &GraphWriter, args: &Value) -> Result<Value, SeamRefusal> {
    let kind = args
        .get("kind")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .trim()
        .to_lowercase();
    let value = normalize(args.get("text").and_then(Value::as_str).unwrap_or_default());
    if node_type_for(&kind).is_none() {
        return Err(refusal(400, "invalid_kind"));
    }
    if value.is_empty() {
        return Err(refusal(400, "text_required"));
    }
    write_fact(
        graph,
        &kind,
        &value,
        args.get("workspace_id").and_then(Value::as_str),
        "user",
        1.0,
        "user_edit",
        None,
    )
}

fn delete(graph: &GraphWriter, args: &Value, now: &str) -> Result<Value, SeamRefusal> {
    let node_id = args
        .get("node_id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .trim()
        .to_string();
    if !node_id.starts_with(SELF_ID_PREFIX) {
        return Err(refusal(400, "not_self_model"));
    }
    let deleted = graph.delete_node(&node_id).map_err(internal)?;
    if !deleted {
        return Err(refusal(404, "not_found"));
    }
    Ok(json!({"status": "ok", "id": node_id, "deleted_at": now}))
}

type TemporalEdit<'a> = (&'a str, Option<&'a str>, Option<&'a str>, Option<&'a str>);

fn stamp_contradiction(graph: &GraphWriter, args: &Value) -> Result<Value, SeamRefusal> {
    let older = args
        .get("older_id")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let newer = args
        .get("newer_id")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let moment = args.get("at").and_then(Value::as_str).unwrap_or_default();
    let resolution = args
        .get("resolution")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let plan: Vec<TemporalEdit<'_>> = match resolution {
        KEEP_OLD => vec![(newer, None, Some(moment), Some(older))],
        REPLACE => vec![
            (older, None, Some(moment), Some(newer)),
            (newer, Some(moment), None, None),
        ],
        _ => vec![
            (older, None, Some(moment), None),
            (newer, Some(moment), None, None),
        ],
    };
    let mut applied: Vec<Value> = Vec::new();
    for (node_id, valid_from, valid_to, superseded_by) in plan {
        let updated = graph
            .stamp_node_validity(node_id, valid_from, valid_to, superseded_by)
            .map_err(internal)?;
        let mut stamp = Map::new();
        stamp.insert("node_id".into(), json!(node_id));
        if let Some(value) = valid_to {
            stamp.insert("valid_to".into(), json!(value));
        }
        if let Some(value) = superseded_by {
            stamp.insert("superseded_by".into(), json!(value));
        }
        if let Some(value) = valid_from {
            stamp.insert("valid_from".into(), json!(value));
        }
        stamp.insert("updated".into(), json!(updated));
        applied.push(Value::Object(stamp));
    }
    Ok(Value::Array(applied))
}

async fn propose(state: &BrainState, args: &Value) -> Result<Value, SeamRefusal> {
    let text = args.get("text").and_then(Value::as_str).unwrap_or_default();
    let source = args
        .get("source")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let user_email = args
        .get("user_email")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string();
    let workspace_id = args
        .get("workspace_id")
        .and_then(Value::as_str)
        .map(str::to_string);
    let max_proposals = args
        .get("max_proposals")
        .and_then(Value::as_i64)
        .unwrap_or(5)
        .max(0) as usize;
    let candidates = extract(text, source);

    let scope: Option<BTreeSet<String>> = workspace_id
        .clone()
        .map(|workspace| [workspace].into_iter().collect());
    let known: BTreeSet<String> = state
        .read(move |conn| crate::memory_api::self_model::read_facts(conn, scope.as_ref()))
        .await
        .map(|facts| {
            facts
                .iter()
                .filter_map(|fact| fact.get("id").and_then(Value::as_str).map(str::to_string))
                .collect()
        })
        .unwrap_or_default();
    let fresh: Vec<&Value> = candidates
        .iter()
        .filter(|fact| !known.contains(fact.get("id").and_then(Value::as_str).unwrap_or_default()))
        .collect();

    let fresh_facts: Vec<Value> = fresh.iter().map(|fact| (*fact).clone()).collect();
    let already_known = candidates.len() - fresh.len();
    let desk_state = state.clone();
    let desk_workspace = workspace_id.clone();
    let (proposed, suppressed) = tokio::task::spawn_blocking(move || {
        let workspace = desk_workspace.as_deref();
        let mut open = desk::open_keys(&desk_state, workspace);
        let mut proposed: Vec<Value> = Vec::new();
        let mut suppressed = 0i64;
        for fact in fresh_facts.iter().take(max_proposals) {
            let key = fact
                .get("id")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_string();
            let value = fact.get("text").and_then(Value::as_str).unwrap_or_default();
            let kind = fact.get("kind").and_then(Value::as_str).unwrap_or_default();
            if open.contains(&key) {
                suppressed += 1;
                continue;
            }
            let summary = format!(
                "대화에서 '{value}'를 읽었습니다. 내 프로필({})에 추가할까요? \
                 승인하기 전에는 저장되지 않습니다.",
                kind_label(kind)
            );
            let payload = json!({
                "fact": fact.clone(),
                "node_type": node_type_for(kind).unwrap_or_default(),
            });
            match desk::create_review(
                &desk_state,
                &format!("나에 대한 새 사실: {value}"),
                &summary,
                SELF_MODEL_KIND,
                &key,
                payload,
                &user_email,
                workspace,
            ) {
                Some(item) => {
                    open.insert(key);
                    proposed.push(item);
                }
                None => suppressed += 1,
            }
        }
        (proposed, suppressed)
    })
    .await
    .map_err(|error| SeamRefusal {
        status: 500,
        detail: error.to_string(),
    })?;
    Ok(json!({
        "available": true,
        "candidates": candidates,
        "candidate_count": candidates.len(),
        "already_known": already_known,
        "proposed": proposed,
        "proposed_count": proposed.len(),
        "suppressed": suppressed,
        "generated_at": state.now(),
    }))
}

fn apply(state: &BrainState, graph: &GraphWriter, args: &Value) -> Result<Value, SeamRefusal> {
    let item_id = args
        .get("item_id")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let workspace_id = args.get("workspace_id").and_then(Value::as_str);
    let Some(item) = desk::load_review_item(state, item_id, workspace_id) else {
        return Err(SeamRefusal {
            status: 404,
            detail: item_id.to_string(),
        });
    };
    if item.get("kind").and_then(Value::as_str) != Some(SELF_MODEL_KIND) {
        return Err(refusal(400, "not_a_proposal"));
    }
    let fact = item
        .get("payload")
        .and_then(|payload| payload.get("fact"))
        .cloned()
        .unwrap_or(Value::Null);
    let kind = fact
        .get("kind")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string();
    let text = normalize(fact.get("text").and_then(Value::as_str).unwrap_or_default());
    if node_type_for(&kind).is_none() || text.is_empty() {
        return Err(refusal(400, "empty_proposal"));
    }
    let status = desk::approve_item(state, item_id);
    let workspace = workspace_id.or_else(|| item.get("workspace_id").and_then(Value::as_str));
    let node = write_fact(
        graph,
        &kind,
        &text,
        workspace,
        "proposal",
        fact.get("confidence")
            .and_then(Value::as_f64)
            .unwrap_or(0.0),
        fact.get("signal")
            .and_then(Value::as_str)
            .unwrap_or_default(),
        Some(item_id),
    )?;
    Ok(json!({
        "item_id": item_id,
        "status": status,
        "fact": node,
        "applied_at": state.now(),
    }))
}

pub async fn dispatch(
    state: &BrainState,
    graph: &GraphWriter,
    op: &str,
    args: Value,
) -> Result<Value, SeamRefusal> {
    let now = state.now();
    match op {
        "self_model_propose" => propose(state, &args).await,
        "self_model_apply" => {
            let state = state.clone();
            let graph = graph.clone();
            blocking(move || apply(&state, &graph, &args)).await
        }
        "self_model_upsert" => {
            let graph = graph.clone();
            blocking(move || upsert(&graph, &args)).await
        }
        "self_model_delete" => {
            let graph = graph.clone();
            blocking(move || delete(&graph, &args, &now)).await
        }
        "stamp_contradiction" => {
            let graph = graph.clone();
            blocking(move || stamp_contradiction(&graph, &args)).await
        }
        other => Err(SeamRefusal {
            status: 400,
            detail: format!("graph mutation op not allowed: {other}"),
        }),
    }
}

async fn blocking<F>(work: F) -> Result<Value, SeamRefusal>
where
    F: FnOnce() -> Result<Value, SeamRefusal> + Send + 'static,
{
    match tokio::task::spawn_blocking(work).await {
        Ok(result) => result,
        Err(error) => Err(SeamRefusal {
            status: 500,
            detail: error.to_string(),
        }),
    }
}

#[cfg(test)]
#[path = "self_model_write_tests.rs"]
mod tests;
