//! Relationship explorer: what a node connects to, and the way between two.
//!
//! Port of `core/workspace_relationships.py`. Pure graph reading — it touches
//! no state file and records no events.
//!
//! The one subtlety worth keeping: `graph(limit=…)` returns a *window*, and the
//! node being explored may not be in it. Asking the store for that node's
//! neighbours directly is the difference between answering "not connected" and
//! answering "outside the page we happened to fetch".

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
use std::collections::{HashMap, HashSet, VecDeque};

use serde_json::{json, Value};

use super::deps::GraphReads;
use super::pyutil::listify;

/// `shortest_path` — fewest hops, treating every edge as undirected.
pub fn shortest_path(edges: &[Value], start: &str, target: Option<&str>) -> Vec<String> {
    let Some(target) = target.filter(|value| !value.is_empty()) else {
        return Vec::new();
    };
    if start.is_empty() {
        return Vec::new();
    }
    let mut adjacency: HashMap<&str, Vec<&str>> = HashMap::new();
    for edge in edges {
        let source = edge.get("from").and_then(Value::as_str).unwrap_or_default();
        let destination = edge.get("to").and_then(Value::as_str).unwrap_or_default();
        if source.is_empty() || destination.is_empty() {
            continue;
        }
        adjacency.entry(source).or_default().push(destination);
        adjacency.entry(destination).or_default().push(source);
    }
    let mut queue: VecDeque<Vec<&str>> = VecDeque::from([vec![start]]);
    let mut seen: HashSet<&str> = HashSet::from([start]);
    while let Some(path) = queue.pop_front() {
        let node = *path.last().expect("a path always has a tail");
        if node == target {
            return path.into_iter().map(str::to_string).collect();
        }
        for neighbour in adjacency.get(node).map(Vec::as_slice).unwrap_or_default() {
            if seen.insert(neighbour) {
                let mut extended = path.clone();
                extended.push(neighbour);
                queue.push_back(extended);
            }
        }
    }
    Vec::new()
}

/// The window size `relationship_explorer` asks the graph for.
pub const DEFAULT_LIMIT: usize = 500;

/// `relationship_explorer(graph, node_id, target_id)`.
pub fn explore(
    graph: Option<&dyn GraphReads>,
    node_id: &str,
    target_id: Option<&str>,
    limit: usize,
) -> Value {
    let Some(graph) = graph else {
        // No graph on this install: the answer has no `node` key at all, which
        // is what Python's early return produces.
        return json!({
            "node_id": node_id,
            "inbound": [],
            "outbound": [],
            "related_entities": [],
            "shortest_path": [],
        });
    };
    let data = graph.window(limit).unwrap_or_else(|| json!({}));
    let mut nodes: Vec<Value> = listify(data.get("nodes"))
        .into_iter()
        .filter(|node| {
            node.get("id")
                .and_then(Value::as_str)
                .is_some_and(|id| !id.is_empty())
        })
        .collect();
    let mut edges: Vec<Value> = listify(data.get("edges"));

    if !contains(&nodes, node_id) {
        if let Some(neighbours) = graph.neighbors(node_id) {
            nodes.extend(listify(neighbours.get("neighbors")));
            edges.extend(listify(neighbours.get("edges")));
        }
    }

    let inbound: Vec<Value> = edges
        .iter()
        .filter(|edge| edge.get("to").and_then(Value::as_str) == Some(node_id))
        .cloned()
        .collect();
    let outbound: Vec<Value> = edges
        .iter()
        .filter(|edge| edge.get("from").and_then(Value::as_str) == Some(node_id))
        .cloned()
        .collect();

    // `dict.fromkeys` — first appearance wins, duplicates dropped.
    let mut related_ids: Vec<String> = Vec::new();
    for edge in inbound.iter().chain(outbound.iter()) {
        let other = if edge.get("to").and_then(Value::as_str) == Some(node_id) {
            edge.get("from").and_then(Value::as_str)
        } else {
            edge.get("to").and_then(Value::as_str)
        };
        if let Some(other) = other.filter(|value| !value.is_empty()) {
            if !related_ids.iter().any(|seen| seen == other) {
                related_ids.push(other.to_string());
            }
        }
    }
    let related: Vec<Value> = related_ids
        .iter()
        .map(|id| find(&nodes, id).unwrap_or_else(|| json!({"id": id})))
        .collect();

    json!({
        "node_id": node_id,
        "node": find(&nodes, node_id).unwrap_or_else(|| json!({"id": node_id})),
        "inbound": inbound,
        "outbound": outbound,
        "related_entities": related,
        "shortest_path": shortest_path(&edges, node_id, target_id),
    })
}

fn contains(nodes: &[Value], node_id: &str) -> bool {
    nodes
        .iter()
        .any(|node| node.get("id").and_then(Value::as_str) == Some(node_id))
}

/// The **last** node with this id, matching `{node["id"]: node for …}`.
fn find(nodes: &[Value], node_id: &str) -> Option<Value> {
    nodes
        .iter()
        .rev()
        .find(|node| node.get("id").and_then(Value::as_str) == Some(node_id))
        .cloned()
}

#[cfg(test)]
mod tests {
    use super::*;

    struct Graph {
        window: Value,
        neighbours: Option<Value>,
    }

    impl GraphReads for Graph {
        fn stats(&self) -> Option<Value> {
            None
        }
        fn window(&self, _limit: usize) -> Option<Value> {
            Some(self.window.clone())
        }
        fn local_sources(&self) -> Option<Value> {
            None
        }
        fn neighbors(&self, _node_id: &str) -> Option<Value> {
            self.neighbours.clone()
        }
    }

    #[test]
    fn no_graph_answers_the_empty_shape_without_a_node_key() {
        let answer = explore(None, "node-1", None, DEFAULT_LIMIT);
        assert_eq!(answer["node_id"], json!("node-1"));
        assert!(answer.get("node").is_none());
        assert_eq!(answer["inbound"], json!([]));
        assert_eq!(answer["shortest_path"], json!([]));
    }

    #[test]
    fn an_unknown_node_answers_a_stub_record() {
        let graph = Graph {
            window: json!({"nodes": [], "edges": []}),
            neighbours: None,
        };
        let answer = explore(Some(&graph), "node-missing", None, DEFAULT_LIMIT);
        assert_eq!(answer["node"], json!({"id": "node-missing"}));
        assert_eq!(answer["related_entities"], json!([]));
    }

    #[test]
    fn inbound_outbound_and_related_are_derived_from_the_window() {
        let graph = Graph {
            window: json!({
                "nodes": [{"id": "a", "type": "Concept"}, {"id": "b"}, {"id": "c"}, {"id": ""}],
                "edges": [
                    {"from": "b", "to": "a", "type": "MENTIONS"},
                    {"from": "a", "to": "c", "type": "MENTIONS"},
                    {"from": "b", "to": "a", "type": "AGAIN"},
                    {"from": "", "to": ""},
                ],
            }),
            neighbours: None,
        };
        let answer = explore(Some(&graph), "a", None, DEFAULT_LIMIT);
        assert_eq!(answer["node"], json!({"id": "a", "type": "Concept"}));
        assert_eq!(answer["inbound"].as_array().unwrap().len(), 2);
        assert_eq!(answer["outbound"].as_array().unwrap().len(), 1);
        // `b` appears twice inbound but is listed once, and keeps its record.
        let related = answer["related_entities"].as_array().unwrap();
        assert_eq!(related.len(), 2);
        assert_eq!(related[0], json!({"id": "b"}));
        assert_eq!(related[1], json!({"id": "c"}));
    }

    #[test]
    fn a_node_outside_the_window_is_fetched_by_neighbours() {
        let graph = Graph {
            window: json!({"nodes": [{"id": "z"}], "edges": []}),
            neighbours: Some(json!({
                "neighbors": [{"id": "a", "type": "Concept"}, {"id": "b"}],
                "edges": [{"from": "a", "to": "b"}],
            })),
        };
        let answer = explore(Some(&graph), "a", None, DEFAULT_LIMIT);
        assert_eq!(answer["node"], json!({"id": "a", "type": "Concept"}));
        assert_eq!(answer["outbound"].as_array().unwrap().len(), 1);
        assert_eq!(answer["related_entities"][0], json!({"id": "b"}));
    }

    #[test]
    fn the_shortest_path_is_breadth_first_and_undirected() {
        let edges = vec![
            json!({"from": "a", "to": "b"}),
            json!({"from": "b", "to": "c"}),
            json!({"from": "a", "to": "d"}),
            json!({"from": "d", "to": "c"}),
        ];
        assert_eq!(shortest_path(&edges, "a", Some("c")), vec!["a", "b", "c"]);
        assert_eq!(shortest_path(&edges, "c", Some("a")), vec!["c", "b", "a"]);
        assert_eq!(shortest_path(&edges, "a", Some("a")), vec!["a"]);
        assert!(shortest_path(&edges, "a", Some("zzz")).is_empty());
        assert!(shortest_path(&edges, "a", None).is_empty());
        assert!(shortest_path(&edges, "", Some("a")).is_empty());
        assert!(shortest_path(&[], "a", Some("b")).is_empty());
    }

    #[test]
    fn a_target_produces_the_path_in_the_explore_answer() {
        let graph = Graph {
            window: json!({
                "nodes": [{"id": "a"}, {"id": "b"}],
                "edges": [{"from": "a", "to": "b"}],
            }),
            neighbours: None,
        };
        let answer = explore(Some(&graph), "a", Some("b"), DEFAULT_LIMIT);
        assert_eq!(answer["shortest_path"], json!(["a", "b"]));
    }
}
