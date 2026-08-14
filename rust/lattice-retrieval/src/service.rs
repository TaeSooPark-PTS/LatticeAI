//! Port of the service layer's single channels and graph channel
//! (`latticeai/services/search_service.py`).
//!
//! The graph layer answers "what matches"; this layer answers "what does the
//! product show". It re-shapes every lane into one match contract
//! (`id`/`node_id`/`item_type`/`sources`/`source_scores`) and adds the graph
//! channel, which is not a search at all: it is a keyword search whose hits are
//! then *expanded* through the graph, scored by how they were reached.
//!
//! Two behaviours here are easy to "improve" and must not be. The final sort is
//! Python's stable `sorted(..., reverse=True)` with **no** id tie-break, so
//! equally scored nodes keep the order they were discovered in — the insertion
//! order of a dict. And a traversal that fails is an empty neighbourhood, not an
//! error: an expansion is an enrichment, and a broken one may not cost the user
//! the answer they asked for.

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
use std::collections::{BTreeSet, HashMap};

use lattice_core::pytext::{clean_text, round6, truncate_chars};
use lattice_core::read::filter_scoped_nodes;
use lattice_core::{CoreError, LocalEmbeddingModel};
use rusqlite::Connection;
use serde_json::{Map, Value};

use crate::graph_reads::{relationship_search, traverse, RelationshipQuery, TraverseOptions};
use crate::keyword::search as graph_keyword_search;
use crate::shape::{py_str, truthy};
use crate::vector::vector_search as graph_vector_search;

/// Who is asking, in the only terms the read path cares about.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Scope {
    /// `None` is the unscoped local-owner path; a set is a membership filter.
    pub allowed_workspaces: Option<BTreeSet<String>>,
    /// Whether NULL-workspace legacy rows count as visible.
    pub include_legacy_global: bool,
}

impl Scope {
    fn allowed(&self) -> Option<&BTreeSet<String>> {
        self.allowed_workspaces.as_ref()
    }
}

/// `search_service._clean` — collapse whitespace, then cut to 1,000 characters.
pub fn clean(value: Option<&Value>) -> String {
    let text = match value {
        Some(Value::String(text)) => text.clone(),
        Some(other) if truthy(other) => py_str(other),
        _ => String::new(),
    };
    truncate_chars(&clean_text(&text), 1000)
}

/// `node.get("metadata") or {}` — a falsy metadata blob becomes an empty object.
fn metadata_of(item: &Value) -> Value {
    match item.get("metadata") {
        Some(value) if truthy(value) => value.clone(),
        _ => Value::Object(Map::new()),
    }
}

fn field(item: &Value, key: &str) -> Value {
    item.get(key).cloned().unwrap_or(Value::Null)
}

/// `SearchService._scope` — drop matches the caller may not read, by `id`.
pub(crate) fn scope_matches(
    conn: &Connection,
    matches: Vec<Value>,
    scope: &Scope,
) -> Result<Vec<Value>, CoreError> {
    filter_scoped_nodes(
        conn,
        matches,
        scope.allowed(),
        scope.include_legacy_global,
        |item| {
            item.get("id")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_string()
        },
    )
}

/// `SearchService.keyword_search` — the lexical lane in the service contract.
pub fn keyword_search(
    conn: &Connection,
    query: &str,
    limit: i64,
    scope: &Scope,
) -> Result<Value, CoreError> {
    let payload = graph_keyword_search(
        conn,
        query,
        limit,
        scope.allowed(),
        scope.include_legacy_global,
    )?;
    let mut matches = Vec::new();
    for (index, item) in payload["matches"]
        .as_array()
        .into_iter()
        .flatten()
        .enumerate()
    {
        let rank = index + 1;
        let score = round6(1.0 / rank as f64);
        let mut out = Map::new();
        out.insert("id".into(), field(item, "id"));
        out.insert("node_id".into(), field(item, "id"));
        out.insert("item_type".into(), Value::String("node".into()));
        out.insert("type".into(), field(item, "type"));
        out.insert("title".into(), field(item, "title"));
        out.insert("summary".into(), Value::String(clean(item.get("summary"))));
        out.insert("score".into(), Value::from(score));
        out.insert("rank".into(), Value::from(rank));
        out.insert("sources".into(), Value::Array(vec!["keyword".into()]));
        let mut source_scores = Map::new();
        source_scores.insert("keyword".into(), Value::from(score));
        out.insert("source_scores".into(), Value::Object(source_scores));
        out.insert("metadata".into(), metadata_of(item));
        out.insert("updated_at".into(), field(item, "updated_at"));
        matches.push(Value::Object(out));
    }
    let mut result = Map::new();
    result.insert("query".into(), Value::String(query.to_string()));
    result.insert("mode".into(), Value::String("keyword".into()));
    result.insert(
        "matches".into(),
        Value::Array(scope_matches(conn, matches, scope)?),
    );
    Ok(Value::Object(result))
}

/// `SearchService.vector_search` — the vector lane in the service contract.
pub fn vector_search(
    conn: &Connection,
    model: &LocalEmbeddingModel,
    query: &str,
    limit: i64,
    min_score: f64,
    scope: &Scope,
) -> Result<Value, CoreError> {
    let payload = graph_vector_search(conn, model, query, limit, min_score)?;
    let mut matches = Vec::new();
    for (index, item) in payload["matches"]
        .as_array()
        .into_iter()
        .flatten()
        .enumerate()
    {
        let raw = item.get("score").and_then(Value::as_f64).unwrap_or(0.0);
        let score = round6(raw);
        let mut out = Map::new();
        out.insert("id".into(), field(item, "id"));
        out.insert("node_id".into(), field(item, "node_id"));
        out.insert("item_type".into(), field(item, "item_type"));
        out.insert("type".into(), field(item, "type"));
        out.insert("title".into(), field(item, "title"));
        out.insert("summary".into(), Value::String(clean(item.get("summary"))));
        out.insert("score".into(), Value::from(score));
        out.insert("rank".into(), Value::from(index + 1));
        out.insert("sources".into(), Value::Array(vec!["vector".into()]));
        let mut source_scores = Map::new();
        source_scores.insert("vector".into(), Value::from(score));
        out.insert("source_scores".into(), Value::Object(source_scores));
        out.insert("metadata".into(), metadata_of(item));
        out.insert("updated_at".into(), field(item, "updated_at"));
        matches.push(Value::Object(out));
    }
    let mut result = Map::new();
    result.insert("query".into(), Value::String(query.to_string()));
    result.insert("mode".into(), Value::String("vector".into()));
    result.insert("embedding_model".into(), field(&payload, "embedding_model"));
    result.insert("embedding_dim".into(), field(&payload, "embedding_dim"));
    result.insert(
        "matches".into(),
        Value::Array(scope_matches(conn, matches, scope)?),
    );
    // Honest recall passthrough: the graph layer scores a capped slice of a large
    // index and says so. Dropping it would turn "partial recall" into "these are
    // all the matches" at the API boundary.
    if let Some(recall) = payload.get("recall").filter(|value| value.is_object()) {
        result.insert("recall".into(), recall.clone());
    }
    Ok(Value::Object(result))
}

/// Everything `graph_search` accepts beyond the query.
#[derive(Debug, Clone)]
pub struct GraphSearchOptions {
    /// Result cap; clamps to `1..=100` (`0` means Python's default of 30).
    pub limit: i64,
    /// How far to expand around each direct hit; clamps to `0..=3`. **`0` means
    /// the default of 1** (Python spells it `int(expand_depth or 1)`); pass a
    /// negative depth to turn expansion off.
    pub expand_depth: i64,
    /// Who is asking.
    pub scope: Scope,
}

impl Default for GraphSearchOptions {
    fn default() -> Self {
        Self {
            limit: 30,
            expand_depth: 1,
            scope: Scope::default(),
        }
    }
}

/// One accumulating candidate — the value side of Python's `by_id` dict.
struct Candidate {
    id: String,
    node_type: Value,
    title: Value,
    summary: String,
    metadata: Value,
    updated_at: Value,
    score: f64,
    graph_score: f64,
    graph_context: Vec<Value>,
}

struct Accumulator {
    order: Vec<Candidate>,
    index: HashMap<String, usize>,
}

impl Accumulator {
    fn new() -> Self {
        Self {
            order: Vec::new(),
            index: HashMap::new(),
        }
    }

    /// `add_node` — first sighting creates the row, every sighting raises the
    /// score to its maximum and appends one more reason it is here.
    fn add(&mut self, node: &Value, score: f64, reason: &str, edge: Option<&Value>) {
        let node_id = node
            .get("id")
            .filter(|value| truthy(value))
            .map(py_str)
            .unwrap_or_default();
        if node_id.is_empty() {
            return;
        }
        let position = match self.index.get(&node_id) {
            Some(position) => *position,
            None => {
                self.order.push(Candidate {
                    id: node_id.clone(),
                    node_type: field(node, "type"),
                    title: field(node, "title"),
                    summary: clean(node.get("summary")),
                    metadata: metadata_of(node),
                    updated_at: field(node, "updated_at"),
                    score: 0.0,
                    graph_score: 0.0,
                    graph_context: Vec::new(),
                });
                let position = self.order.len() - 1;
                self.index.insert(node_id, position);
                position
            }
        };
        let candidate = &mut self.order[position];
        candidate.score = candidate.score.max(score);
        candidate.graph_score = candidate.graph_score.max(score);
        let mut context = Map::new();
        context.insert("reason".into(), Value::String(reason.to_string()));
        if let Some(edge) = edge {
            let mut relationship = Map::new();
            relationship.insert("id".into(), field(edge, "id"));
            relationship.insert("type".into(), field(edge, "type"));
            relationship.insert("weight".into(), field(edge, "weight"));
            relationship.insert("from".into(), edge_end(edge, "from", "source"));
            relationship.insert("to".into(), edge_end(edge, "to", "target"));
            context.insert("relationship".into(), Value::Object(relationship));
        }
        candidate.graph_context.push(Value::Object(context));
    }
}

/// `edge.get("from") or (edge.get("source") or {}).get("id")`.
///
/// A traversal edge carries flat `from`/`to`; a relationship carries nested
/// `source`/`target` objects. One accessor reads both, and the `or` chain means
/// an explicitly empty `from` falls through to the nested id.
fn edge_end(edge: &Value, flat: &str, nested: &str) -> Value {
    if let Some(value) = edge.get(flat).filter(|value| truthy(value)) {
        return value.clone();
    }
    edge.get(nested)
        .and_then(|side| side.get("id"))
        .cloned()
        .unwrap_or(Value::Null)
}

/// `SearchService.graph_search` — direct hits, their relationships, and the
/// neighbourhood around each, merged by maximum score.
pub fn graph_search(
    conn: &Connection,
    query: &str,
    options: &GraphSearchOptions,
) -> Result<Value, CoreError> {
    let limit = if options.limit == 0 {
        30
    } else {
        options.limit
    }
    .clamp(1, 100);
    // `max(0, min(int(expand_depth or 1), 3))`: as in `traverse`, a zero is
    // Python-falsy and therefore means "one hop", not "no expansion".
    let expand_depth = if options.expand_depth == 0 {
        1
    } else {
        options.expand_depth
    }
    .clamp(0, 3);
    let scope = &options.scope;

    let direct = graph_keyword_search(
        conn,
        query,
        limit.max(10),
        scope.allowed(),
        scope.include_legacy_global,
    )?;
    let relationships = relationship_search(
        conn,
        &RelationshipQuery {
            query: query.to_string(),
            limit,
            allowed_workspaces: scope.allowed_workspaces.clone(),
            include_legacy_global: scope.include_legacy_global,
            ..RelationshipQuery::default()
        },
    )?;

    let mut accumulator = Accumulator::new();
    let empty: Vec<Value> = Vec::new();
    let direct_matches = direct["matches"].as_array().unwrap_or(&empty).clone();
    for (index, item) in direct_matches.iter().enumerate() {
        let rank = index as f64 + 1.0;
        accumulator.add(item, 1.0 / rank, "direct_match", None);
        if expand_depth <= 0 {
            continue;
        }
        let seed = item.get("id").and_then(Value::as_str).unwrap_or_default();
        // An expansion that cannot run costs the enrichment, never the answer.
        let neighbourhood = traverse(
            conn,
            seed,
            &TraverseOptions {
                depth: expand_depth,
                limit: limit * 3,
                allowed_workspaces: scope.allowed_workspaces.clone(),
                include_legacy_global: scope.include_legacy_global,
            },
        )
        .unwrap_or_else(|_| serde_json::json!({"nodes": [], "edges": []}));
        let edges = neighbourhood["edges"].as_array().unwrap_or(&empty).clone();
        for node in neighbourhood["nodes"].as_array().unwrap_or(&empty) {
            let node_id = node.get("id").and_then(Value::as_str).unwrap_or_default();
            if node_id == seed {
                continue;
            }
            let related = edges.iter().find(|edge| {
                let ends = [
                    edge.get("from").and_then(Value::as_str).unwrap_or_default(),
                    edge.get("to").and_then(Value::as_str).unwrap_or_default(),
                ];
                ends.contains(&seed) && ends.contains(&node_id)
            });
            accumulator.add(node, 0.45 / rank, "neighbor_expansion", related);
        }
    }
    for (index, relationship) in relationships["relationships"]
        .as_array()
        .unwrap_or(&empty)
        .iter()
        .enumerate()
    {
        let score = 0.75 / (index as f64 + 1.0);
        for side in ["source", "target"] {
            let node = relationship.get(side).cloned().unwrap_or(Value::Null);
            accumulator.add(&node, score, "relationship_match", Some(relationship));
        }
    }

    // Stable, score-descending, no id tie-break: discovery order survives a tie.
    let mut candidates = accumulator.order;
    candidates.sort_by(|a, b| {
        b.score
            .partial_cmp(&a.score)
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    candidates.truncate(limit as usize);

    let matches: Vec<Value> = candidates
        .into_iter()
        .enumerate()
        .map(|(index, candidate)| {
            let mut source_scores = Map::new();
            source_scores.insert("graph".into(), Value::from(round6(candidate.graph_score)));
            let mut item = Map::new();
            item.insert("id".into(), Value::String(candidate.id.clone()));
            item.insert("node_id".into(), Value::String(candidate.id));
            item.insert("item_type".into(), Value::String("node".into()));
            item.insert("type".into(), candidate.node_type);
            item.insert("title".into(), candidate.title);
            item.insert("summary".into(), Value::String(candidate.summary));
            item.insert("score".into(), Value::from(round6(candidate.score)));
            item.insert("sources".into(), Value::Array(vec!["graph".into()]));
            item.insert("source_scores".into(), Value::Object(source_scores));
            item.insert("metadata".into(), candidate.metadata);
            item.insert("updated_at".into(), candidate.updated_at);
            item.insert(
                "graph_context".into(),
                Value::Array(candidate.graph_context),
            );
            item.insert("rank".into(), Value::from(index + 1));
            Value::Object(item)
        })
        .collect();

    let mut result = Map::new();
    result.insert("query".into(), Value::String(query.to_string()));
    result.insert("mode".into(), Value::String("graph".into()));
    result.insert("expand_depth".into(), Value::from(expand_depth));
    result.insert(
        "matches".into(),
        Value::Array(scope_matches(conn, matches, scope)?),
    );
    Ok(Value::Object(result))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn graph() -> (tempfile::TempDir, Connection) {
        let dir = tempfile::tempdir().unwrap();
        let conn = Connection::open(dir.path().join("g.sqlite")).unwrap();
        conn.execute_batch(
            "CREATE TABLE nodes(id TEXT PRIMARY KEY, type TEXT, title TEXT, summary TEXT,
                                metadata_json TEXT, updated_at TEXT);
             CREATE TABLE edges(id TEXT PRIMARY KEY, from_node TEXT, to_node TEXT, type TEXT,
                                weight REAL, metadata_json TEXT, created_at TEXT);
             CREATE TABLE nodes_v2(id TEXT PRIMARY KEY, workspace_id TEXT);
             INSERT INTO nodes VALUES
               ('a','Decision','Alpha ranking','  about   ranking  ','{}','2026-01-02T00:00:00'),
               ('b','Concept','Beta ranking',NULL,'{\"k\":1}','2026-01-03T00:00:00'),
               ('c','Concept','Gamma','unrelated','{}','2026-01-01T00:00:00');
             INSERT INTO nodes_v2 VALUES ('a','w1'),('b','w1'),('c','w2');
             INSERT INTO edges VALUES
               ('e1','a','b','MENTIONS',0.9,'{}','2026-02-01T00:00:00');",
        )
        .unwrap();
        (dir, conn)
    }

    #[test]
    fn clean_collapses_and_truncates_like_python() {
        assert_eq!(clean(Some(&json!("  a \n b  "))), "a b");
        assert_eq!(clean(None), "");
        assert_eq!(clean(Some(&Value::Null)), "");
        assert_eq!(clean(Some(&json!(""))), "");
        assert_eq!(clean(Some(&json!(0))), "", "0 is falsy in Python's `or`");
        assert_eq!(clean(Some(&json!(7))), "7");
        let long = "가".repeat(1200);
        assert_eq!(clean(Some(&json!(long))).chars().count(), 1000);
    }

    #[test]
    fn the_single_lanes_wear_the_service_contract() {
        let (_dir, conn) = graph();
        let out = keyword_search(&conn, "ranking", 30, &Scope::default()).unwrap();
        assert_eq!(out["mode"], "keyword");
        let first = &out["matches"][0];
        assert_eq!(first["item_type"], "node");
        assert_eq!(first["score"], json!(1.0));
        assert_eq!(first["rank"], json!(1));
        assert_eq!(first["sources"], json!(["keyword"]));
        assert_eq!(first["source_scores"]["keyword"], json!(1.0));
        assert_eq!(first["node_id"], first["id"]);
        assert_eq!(first["summary"], "about ranking");
        assert_eq!(out["matches"][1]["score"], json!(0.5));
        // A NULL summary becomes "", never null.
        assert_eq!(out["matches"][1]["summary"], "");
        // Scoping drops the rows the caller may not read.
        let w2: BTreeSet<String> = ["w2".to_string()].into_iter().collect();
        let scoped = keyword_search(
            &conn,
            "ranking",
            30,
            &Scope {
                allowed_workspaces: Some(w2),
                include_legacy_global: false,
            },
        )
        .unwrap();
        assert!(scoped["matches"].as_array().unwrap().is_empty());
    }

    #[test]
    fn edge_ends_read_flat_and_nested_shapes() {
        assert_eq!(edge_end(&json!({"from": "a"}), "from", "source"), "a");
        assert_eq!(
            edge_end(&json!({"source": {"id": "s"}}), "from", "source"),
            "s"
        );
        assert_eq!(
            edge_end(
                &json!({"from": "", "source": {"id": "s"}}),
                "from",
                "source"
            ),
            "s",
            "an empty string is falsy, so the `or` chain falls through"
        );
        assert_eq!(edge_end(&json!({}), "from", "source"), Value::Null);
    }

    #[test]
    fn graph_search_scores_by_how_a_node_was_reached() {
        let (_dir, conn) = graph();
        let out = graph_search(&conn, "ranking", &GraphSearchOptions::default()).unwrap();
        assert_eq!(out["mode"], "graph");
        assert_eq!(out["expand_depth"], 1);
        let first = &out["matches"][0];
        assert_eq!(first["score"], json!(1.0));
        assert_eq!(first["source_scores"]["graph"], json!(1.0));
        assert_eq!(first["graph_context"][0]["reason"], "direct_match");
        // The neighbour arrives through the edge that connects it.
        let reasons: Vec<&str> = out["matches"]
            .as_array()
            .unwrap()
            .iter()
            .flat_map(|item| item["graph_context"].as_array().unwrap())
            .map(|context| context["reason"].as_str().unwrap())
            .collect();
        assert!(reasons.contains(&"neighbor_expansion"));
        assert!(reasons.contains(&"relationship_match"));
    }

    #[test]
    fn expansion_depth_and_limits_clamp_rather_than_fail() {
        let (_dir, conn) = graph();
        // Negative turns expansion off; zero is Python-falsy and means "one hop".
        let flat = graph_search(
            &conn,
            "ranking",
            &GraphSearchOptions {
                expand_depth: -1,
                ..Default::default()
            },
        )
        .unwrap();
        assert_eq!(flat["expand_depth"], 0);
        let reasons: BTreeSet<&str> = flat["matches"]
            .as_array()
            .unwrap()
            .iter()
            .flat_map(|item| item["graph_context"].as_array().unwrap())
            .map(|context| context["reason"].as_str().unwrap())
            .collect();
        assert!(!reasons.contains("neighbor_expansion"));
        for (asked, expect) in [(9, 3), (-1, 0), (0, 1)] {
            let out = graph_search(
                &conn,
                "ranking",
                &GraphSearchOptions {
                    expand_depth: asked,
                    ..Default::default()
                },
            )
            .unwrap();
            assert_eq!(out["expand_depth"], expect);
        }
        let one = graph_search(
            &conn,
            "ranking",
            &GraphSearchOptions {
                limit: 1,
                ..Default::default()
            },
        )
        .unwrap();
        assert_eq!(one["matches"].as_array().unwrap().len(), 1);
        let defaulted = graph_search(
            &conn,
            "ranking",
            &GraphSearchOptions {
                limit: 0,
                ..Default::default()
            },
        )
        .unwrap();
        assert!(!defaulted["matches"].as_array().unwrap().is_empty());
        assert!(format!("{:?}", GraphSearchOptions::default()).contains("expand_depth"));
        assert_eq!(Scope::default(), Scope::default().clone());
    }

    #[test]
    fn a_blank_id_never_becomes_a_candidate() {
        let mut accumulator = Accumulator::new();
        accumulator.add(&json!({}), 1.0, "direct_match", None);
        accumulator.add(&json!({"id": ""}), 1.0, "direct_match", None);
        assert!(accumulator.order.is_empty());
        accumulator.add(&json!({"id": "x"}), 0.4, "direct_match", None);
        accumulator.add(&json!({"id": "x"}), 0.9, "relationship_match", None);
        assert_eq!(accumulator.order.len(), 1);
        assert_eq!(accumulator.order[0].score, 0.9);
        assert_eq!(accumulator.order[0].graph_context.len(), 2);
        // A later, lower score never lowers the maximum.
        accumulator.add(&json!({"id": "x"}), 0.1, "neighbor_expansion", None);
        assert_eq!(accumulator.order[0].score, 0.9);
    }

    #[test]
    fn the_vector_lane_passes_recall_through() {
        let (_dir, conn) = graph();
        conn.execute_batch(
            "CREATE TABLE chunks(id TEXT PRIMARY KEY, source_node TEXT, text TEXT,
                                 metadata_json TEXT);
             CREATE TABLE vector_embeddings(item_id TEXT PRIMARY KEY, item_type TEXT,
               source_node TEXT, embedding BLOB, embedding_dim INT, embedding_model TEXT,
               metadata_json TEXT, indexed_at TEXT);",
        )
        .unwrap();
        let model = LocalEmbeddingModel::new(384);
        conn.execute(
            "INSERT INTO vector_embeddings VALUES ('a','node','a',?,384,?,'{}','2026-03-01')",
            rusqlite::params![
                model.encode(&model.embed("alpha ranking")),
                model.model_id()
            ],
        )
        .unwrap();
        let out = vector_search(&conn, &model, "ranking", 10, 0.0, &Scope::default()).unwrap();
        assert_eq!(out["mode"], "vector");
        assert_eq!(out["embedding_dim"], 384);
        assert!(out["recall"].is_object());
        assert_eq!(out["matches"][0]["sources"], json!(["vector"]));
        assert_eq!(out["matches"][0]["rank"], json!(1));
    }
}
