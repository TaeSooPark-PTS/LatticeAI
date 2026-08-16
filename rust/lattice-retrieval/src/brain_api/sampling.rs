//! One graph slice and one memory read, shared by every surface in the family.
//!
//! `latticeai/services/brain_intelligence/sampling.py` takes a single
//! workspace-scoped slice and hands it to the health report, the insights
//! digest, the garden and both consistency scans, which is what makes their
//! numbers comparable. It also does the one normalisation the whole family
//! depends on: the store emits edges keyed `from`/`to`, the quality layer wants
//! `source`/`target`, and `setdefault` adds the second pair **while keeping the
//! first**. `health_report`'s connectivity dimension then reads
//! `edge.get("source") or edge.get("from_node")` — so an edge that arrived
//! without a `from` key silently scores every node an orphan. That is the
//! product's behaviour; it is reproduced, not repaired.
//!
//! Both readers degrade rather than raise: an unreadable graph reports
//! `available: false` and an unreadable memory tier reads as empty.

use std::collections::BTreeSet;

use serde_json::Value;

use crate::memory_api::shared::BrainState;
use crate::memory_api::{kg, wsos};

use super::pyutil;

/// `_STALE_DAYS` — untouched for longer than this and a node is stale.
pub const STALE_DAYS: i64 = 45;
/// `_RECENT_DAYS` — the window "recent" means everywhere in this family.
pub const RECENT_DAYS: i64 = 7;
/// `_GRAPH_SAMPLE_LIMIT` — how much of the graph one pass looks at.
pub const GRAPH_SAMPLE_LIMIT: i64 = 800;
/// `MemoryService.inspect("workspace", limit=500)`.
pub const MEMORY_LIMIT: usize = 500;

/// The `{"nodes", "edges", "available"}` reading every surface starts from.
///
/// The `error` key Python adds on the failure branch is not carried: no surface
/// in this family reads it, and a field nothing reads is a field that stops
/// being true.
#[derive(Debug, Clone, Default)]
pub struct Sample {
    /// Visible nodes, newest first, capped at [`GRAPH_SAMPLE_LIMIT`].
    pub nodes: Vec<Value>,
    /// Edges keyed `from`/`to` **and** `source`/`target`.
    pub edges: Vec<Value>,
    /// Whether the graph could be read at all.
    pub available: bool,
}

/// `_graph_sample` — the scoped slice, normalised, never raising.
pub async fn graph_sample(state: &BrainState, workspace_id: Option<&str>) -> Sample {
    if !state.graph_enabled() {
        return Sample::default();
    }
    let allowed: Option<BTreeSet<String>> = workspace_id.map(|id| BTreeSet::from([id.to_string()]));
    let slice = state
        .read(move |conn| Ok(kg::graph_slice(conn, GRAPH_SAMPLE_LIMIT, allowed.as_ref()).ok()))
        .await
        .unwrap_or(None);
    let Some(slice) = slice else {
        return Sample::default();
    };
    Sample {
        nodes: slice.nodes,
        edges: slice.edges.into_iter().map(normalise_edge).collect(),
        available: true,
    }
}

/// `normalized.setdefault("source", edge.get("from"))` — both spellings survive.
fn normalise_edge(edge: Value) -> Value {
    let Value::Object(mut map) = edge else {
        return edge;
    };
    let from = map.get("from").cloned().unwrap_or(Value::Null);
    let to = map.get("to").cloned().unwrap_or(Value::Null);
    map.entry("source").or_insert(from);
    map.entry("target").or_insert(to);
    Value::Object(map)
}

/// `edge.get("source") or edge.get("from_node")` — the connectivity reading.
///
/// `or`, not `??`: an edge whose `source` is `None` (a slice that lost its
/// `from` key) falls through to a `from_node` that modern rows do not carry, so
/// the endpoint reads as the empty string and nothing counts as connected.
pub fn endpoint(edge: &Value, primary: &str, legacy: &str) -> String {
    let value = edge.get(primary).filter(|v| pyutil::truthy(v));
    match value {
        Some(value) => pyutil::py_str(value),
        None => pyutil::text_of(edge.get(legacy)),
    }
}

/// `_workspace_memories` — the workspace tier, scoped and capped.
pub fn workspace_memories(
    state: &BrainState,
    user_email: &str,
    workspace_id: Option<&str>,
) -> Vec<Value> {
    let scope = workspace_id
        .filter(|value| !value.is_empty())
        .unwrap_or(wsos::DEFAULT_WORKSPACE_ID);
    let document = wsos::load(state.store(), state.data_dir());
    let mut items = wsos::list_memories(&document, Some(user_email), None, Some(scope));
    items.truncate(MEMORY_LIMIT);
    items
}

/// `_no_graph_reason` — "nothing saved yet" is not "could not be read".
pub fn no_graph_reason(graph_available: bool) -> &'static str {
    if graph_available {
        "no knowledge saved yet"
    } else {
        "the knowledge graph could not be read"
    }
}

/// `_slim(node)` — the four fields every list of nodes is rendered down to.
pub fn slim(node: &Value) -> lattice_auth::OrderedMap {
    let mut out = lattice_auth::OrderedMap::new();
    out.insert("id", node.get("id").cloned().unwrap_or(Value::Null));
    out.insert("type", node.get("type").cloned().unwrap_or(Value::Null));
    out.insert(
        "title",
        Value::String(pyutil::head(&pyutil::field_text(node, "title"), 120)),
    );
    out.insert(
        "updated_at",
        node.get("updated_at").cloned().unwrap_or(Value::Null),
    );
    out
}

/// `_node_text(node)` — `f"{title} {summary}".strip()`.
pub fn node_text(node: &Value) -> String {
    let title = lattice_core::pytext::strip(&pyutil::field_text(node, "title"));
    let summary = lattice_core::pytext::strip(&pyutil::field_text(node, "summary"));
    lattice_core::pytext::strip(&format!("{title} {summary}"))
}

/// The current instant as `datetime.now(timezone.utc).timestamp()`.
pub fn now_utc_secs() -> f64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|value| value.as_secs_f64())
        .unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn an_edge_keeps_both_spellings_of_its_endpoints() {
        let edge = normalise_edge(serde_json::json!({"id": "e", "from": "a", "to": "b"}));
        assert_eq!(edge["from"], "a");
        assert_eq!(edge["source"], "a");
        assert_eq!(edge["target"], "b");
        assert_eq!(endpoint(&edge, "source", "from_node"), "a");
        let kept = normalise_edge(serde_json::json!({"source": "x", "from": "a", "to": "b"}));
        assert_eq!(kept["source"], "x", "setdefault does not overwrite");
        assert_eq!(normalise_edge(Value::Null), Value::Null);
    }

    #[test]
    fn an_edge_that_lost_its_from_key_reads_as_no_endpoint_at_all() {
        let edge = normalise_edge(serde_json::json!({"id": "e", "to": "b"}));
        assert_eq!(
            endpoint(&edge, "source", "from_node"),
            "",
            "the orphan trap: a missing `from` makes every node an orphan"
        );
        assert_eq!(endpoint(&edge, "target", "to_node"), "b");
    }

    #[test]
    fn slim_truncates_the_title_and_keeps_the_raw_id() {
        let node = serde_json::json!({
            "id": 7, "type": "Concept", "title": "가".repeat(200), "updated_at": null,
        });
        let slimmed = slim(&node);
        assert_eq!(slimmed.get("id"), Some(&Value::from(7)));
        assert_eq!(
            slimmed
                .get("title")
                .and_then(Value::as_str)
                .map(|t| t.chars().count()),
            Some(120)
        );
        assert_eq!(slimmed.get("updated_at"), Some(&Value::Null));
        assert_eq!(
            node_text(&serde_json::json!({"title": " a ", "summary": " b "})),
            "a b"
        );
        assert_eq!(node_text(&serde_json::json!({"title": "a"})), "a");
    }

    #[test]
    fn the_two_unavailable_reasons_are_told_apart() {
        assert_eq!(no_graph_reason(true), "no knowledge saved yet");
        assert_eq!(
            no_graph_reason(false),
            "the knowledge graph could not be read"
        );
        assert!(now_utc_secs() > 1_700_000_000.0);
        assert!(!Sample::default().available);
    }
}
