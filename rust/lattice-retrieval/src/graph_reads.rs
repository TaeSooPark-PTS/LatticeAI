//! Port of the knowledge graph's relationship and traversal reads
//! (`lattice_brain/graph/retrieval_reads.py`).
//!
//! Both functions run the **same SQL** against the same SQLite, because both are
//! defined by their SQL: `relationship_search` is a six-way LIKE with a fixed
//! `ORDER BY weight DESC, created_at DESC, id ASC`, and `traverse` is a
//! breadth-first walk whose every round re-runs a capped
//! `ORDER BY weight DESC, id ASC LIMIT limit*3`.
//!
//! That per-round cap is the part worth staring at. It is *not* a global cap: a
//! round with a wide frontier can lose edges the same walk would have kept had
//! the frontier been narrower, so the set of edges a traversal returns depends
//! on how the rounds happened to split. Reproduced exactly — an implementation
//! that hoisted the cap out of the loop would be a better graph walk and a
//! worse port.

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

use lattice_core::pytext::safe_loads;
use lattice_core::read::{column_json, filter_scoped_nodes, read_tables};
use lattice_core::CoreError;
use rusqlite::Connection;
use serde_json::{Map, Value};

/// Everything `relationship_search` accepts.
#[derive(Debug, Clone, Default)]
pub struct RelationshipQuery {
    /// Free text matched against six columns with `LIKE %…%`.
    pub query: String,
    /// Restrict to edges touching this node (either endpoint).
    pub node_id: String,
    /// Restrict to edge types containing this substring.
    pub relationship_type: String,
    /// Row cap; `0` means Python's default of 30, and the result clamps to 200.
    pub limit: i64,
    /// `None` is the unscoped local-owner path; a set is a membership filter.
    pub allowed_workspaces: Option<BTreeSet<String>>,
    /// Whether NULL-workspace legacy rows count as visible.
    pub include_legacy_global: bool,
}

const RELATIONSHIP_COLUMNS: &str = "
                      e.id, e.from_node, e.to_node, e.type, e.weight, e.metadata_json, e.created_at,
                      src.type AS source_type, src.title AS source_title, src.summary AS source_summary,
                      src.metadata_json AS source_metadata,
                      dst.type AS target_type, dst.title AS target_title, dst.summary AS target_summary,
                      dst.metadata_json AS target_metadata";

const TRAVERSE_NODE_COLUMNS: &str = "id, type, title, summary, metadata_json, updated_at";

/// `max(1, min(int(limit or default), ceiling))` — a 0 means "the default".
fn clamp_limit(limit: i64, default: i64, ceiling: i64) -> i64 {
    let requested = if limit == 0 { default } else { limit };
    requested.clamp(1, ceiling)
}

fn endpoint(row: &rusqlite::Row<'_>, id: &str, prefix: &str) -> rusqlite::Result<Value> {
    let mut node = Map::new();
    node.insert("id".into(), column_json(row, id)?);
    node.insert("type".into(), column_json(row, &format!("{prefix}_type"))?);
    node.insert(
        "title".into(),
        column_json(row, &format!("{prefix}_title"))?,
    );
    node.insert(
        "summary".into(),
        column_json(row, &format!("{prefix}_summary"))?,
    );
    let metadata: Option<String> = row.get(format!("{prefix}_metadata").as_str())?;
    node.insert(
        "metadata".into(),
        Value::Object(safe_loads(metadata.as_deref())),
    );
    Ok(Value::Object(node))
}

/// `KnowledgeGraphReadsMixin.relationship_search`.
pub fn relationship_search(
    conn: &Connection,
    request: &RelationshipQuery,
) -> Result<Value, CoreError> {
    let query = request.query.trim().to_string();
    let node_id = request.node_id.trim().to_string();
    let relationship_type = request.relationship_type.trim().to_string();
    let limit = clamp_limit(request.limit, 30, 200);
    let (nodes_table, edges_table) = read_tables(conn);

    let mut where_clauses: Vec<&str> = Vec::new();
    let mut params: Vec<String> = Vec::new();
    if !node_id.is_empty() {
        where_clauses.push("(e.from_node=? OR e.to_node=?)");
        params.push(node_id.clone());
        params.push(node_id.clone());
    }
    if !relationship_type.is_empty() {
        where_clauses.push("e.type LIKE ?");
        params.push(format!("%{relationship_type}%"));
    }
    if !query.is_empty() {
        where_clauses.push(
            "(e.type LIKE ? OR e.metadata_json LIKE ? OR src.title LIKE ? \
             OR dst.title LIKE ? OR src.summary LIKE ? OR dst.summary LIKE ?)",
        );
        for _ in 0..6 {
            params.push(format!("%{query}%"));
        }
    }
    let where_sql = if where_clauses.is_empty() {
        String::new()
    } else {
        format!("WHERE {}", where_clauses.join(" AND "))
    };
    let sql = format!(
        "SELECT {RELATIONSHIP_COLUMNS} FROM {edges_table} e \
         JOIN {nodes_table} src ON src.id=e.from_node \
         JOIN {nodes_table} dst ON dst.id=e.to_node \
         {where_sql} ORDER BY e.weight DESC, e.created_at DESC, e.id ASC LIMIT ?"
    );

    let mut statement = conn.prepare(&sql)?;
    let bound: Vec<&dyn rusqlite::ToSql> = params
        .iter()
        .map(|value| value as &dyn rusqlite::ToSql)
        .chain(std::iter::once(&limit as &dyn rusqlite::ToSql))
        .collect();
    let rows = statement.query_map(bound.as_slice(), |row| {
        let metadata: Option<String> = row.get("metadata_json")?;
        let mut item = Map::new();
        item.insert("id".into(), column_json(row, "id")?);
        item.insert("type".into(), column_json(row, "type")?);
        item.insert("weight".into(), column_json(row, "weight")?);
        item.insert(
            "metadata".into(),
            Value::Object(safe_loads(metadata.as_deref())),
        );
        item.insert("created_at".into(), column_json(row, "created_at")?);
        item.insert("source".into(), endpoint(row, "from_node", "source")?);
        item.insert("target".into(), endpoint(row, "to_node", "target")?);
        Ok(Value::Object(item))
    })?;
    let mut relationships: Vec<Value> = rows.filter_map(Result::ok).collect();

    if let Some(allowed) = request.allowed_workspaces.as_ref() {
        // Both endpoints or neither: an edge whose far end the caller may not
        // read is not a relationship the caller has.
        let mut kept = Vec::new();
        for rel in relationships {
            let endpoints: Vec<String> = ["source", "target"]
                .iter()
                .map(|side| endpoint_id(&rel, side))
                .collect();
            let visible = filter_scoped_nodes(
                conn,
                endpoints,
                Some(allowed),
                request.include_legacy_global,
                |id| id.clone(),
            )?;
            if visible.len() == 2 {
                kept.push(rel);
            }
        }
        relationships = kept;
    }

    let mut out = Map::new();
    out.insert("query".into(), Value::String(query));
    out.insert("node_id".into(), Value::String(node_id));
    out.insert("relationship_type".into(), Value::String(relationship_type));
    out.insert("relationships".into(), Value::Array(relationships));
    Ok(Value::Object(out))
}

fn endpoint_id(rel: &Value, side: &str) -> String {
    rel.get(side)
        .and_then(|value| value.get("id"))
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string()
}

/// Everything `traverse` accepts beyond the seed.
#[derive(Debug, Clone)]
pub struct TraverseOptions {
    /// BFS rounds; clamps to `0..=4`. **`0` means the default of 1** (Python
    /// spells it `int(depth or 1)`); pass a negative depth for "the seed alone".
    pub depth: i64,
    /// Node budget; `0` means Python's default of 100, and it clamps to 500.
    pub limit: i64,
    /// `None` is the unscoped local-owner path.
    pub allowed_workspaces: Option<BTreeSet<String>>,
    /// Whether NULL-workspace legacy rows count as visible.
    pub include_legacy_global: bool,
}

impl Default for TraverseOptions {
    fn default() -> Self {
        Self {
            depth: 1,
            limit: 100,
            allowed_workspaces: None,
            include_legacy_global: false,
        }
    }
}

/// `KnowledgeGraphReadsMixin.traverse`.
///
/// Returns [`CoreError::InvalidRequest`] where Python raises `ValueError`: a
/// blank seed, and a seed the caller's scope cannot see (which is reported as
/// "not found" rather than "forbidden", so the endpoint never confirms the
/// existence of another workspace's node).
pub fn traverse(
    conn: &Connection,
    node_id: &str,
    options: &TraverseOptions,
) -> Result<Value, CoreError> {
    let node_id = node_id.trim().to_string();
    if node_id.is_empty() {
        return Err(CoreError::InvalidRequest("node_id required".into()));
    }
    if let Some(allowed) = options.allowed_workspaces.as_ref() {
        let seed = filter_scoped_nodes(
            conn,
            vec![node_id.clone()],
            Some(allowed),
            options.include_legacy_global,
            |id| id.clone(),
        )?;
        if seed.is_empty() {
            return Err(CoreError::InvalidRequest(format!(
                "graph node not found: {node_id}"
            )));
        }
    }
    // `max(0, min(int(depth or 1), 4))`: a zero is Python-falsy, so it means
    // "the default of one round", and only a negative depth reaches zero. Not a
    // typo on either side — reproduced because callers already live with it.
    let depth = if options.depth == 0 { 1 } else { options.depth }.clamp(0, 4);
    let limit = clamp_limit(options.limit, 100, 500);
    let (nodes_table, edges_table) = read_tables(conn);

    let mut visited: BTreeSet<String> = [node_id.clone()].into_iter().collect();
    let mut frontier: BTreeSet<String> = [node_id.clone()].into_iter().collect();
    // Insertion-ordered `edges_by_id`: a repeated edge keeps its first position
    // and takes the newest value, which is what assigning into a Python dict does.
    let mut edge_order: Vec<String> = Vec::new();
    let mut edges_by_id: HashMap<String, Value> = HashMap::new();

    for _ in 0..depth {
        if frontier.is_empty() || visited.len() as i64 >= limit {
            break;
        }
        let placeholders = vec!["?"; frontier.len()].join(",");
        let sql = format!(
            "SELECT id, from_node, to_node, type, weight, metadata_json FROM {edges_table} \
             WHERE from_node IN ({placeholders}) OR to_node IN ({placeholders}) \
             ORDER BY weight DESC, id ASC LIMIT ?"
        );
        let round_cap = limit * 3;
        let mut statement = conn.prepare(&sql)?;
        let bound: Vec<&dyn rusqlite::ToSql> = frontier
            .iter()
            .chain(frontier.iter())
            .map(|value| value as &dyn rusqlite::ToSql)
            .chain(std::iter::once(&round_cap as &dyn rusqlite::ToSql))
            .collect();
        let rows = statement.query_map(bound.as_slice(), |row| {
            let metadata: Option<String> = row.get("metadata_json")?;
            let mut edge = Map::new();
            edge.insert("id".into(), column_json(row, "id")?);
            edge.insert("from".into(), column_json(row, "from_node")?);
            edge.insert("to".into(), column_json(row, "to_node")?);
            edge.insert("type".into(), column_json(row, "type")?);
            edge.insert("weight".into(), column_json(row, "weight")?);
            edge.insert(
                "metadata".into(),
                Value::Object(safe_loads(metadata.as_deref())),
            );
            let from: String = row.get("from_node")?;
            let to: String = row.get("to_node")?;
            let id: String = row.get("id")?;
            Ok((id, from, to, Value::Object(edge)))
        })?;
        let mut next_frontier: BTreeSet<String> = BTreeSet::new();
        for (id, from, to, edge) in rows.filter_map(Result::ok) {
            if edges_by_id.insert(id.clone(), edge).is_none() {
                edge_order.push(id);
            }
            for candidate in [from, to] {
                if !visited.contains(&candidate) && (visited.len() as i64) < limit {
                    visited.insert(candidate.clone());
                    next_frontier.insert(candidate);
                }
            }
        }
        frontier = next_frontier;
    }

    let placeholders = vec!["?"; visited.len()].join(",");
    let sql = format!(
        "SELECT {TRAVERSE_NODE_COLUMNS} FROM {nodes_table} WHERE id IN ({placeholders}) \
         ORDER BY updated_at DESC, id ASC"
    );
    let mut statement = conn.prepare(&sql)?;
    let bound = rusqlite::params_from_iter(visited.iter());
    let rows = statement.query_map(bound, |row| {
        let metadata: Option<String> = row.get("metadata_json")?;
        let mut node = Map::new();
        node.insert("id".into(), column_json(row, "id")?);
        node.insert("type".into(), column_json(row, "type")?);
        node.insert("title".into(), column_json(row, "title")?);
        node.insert("summary".into(), column_json(row, "summary")?);
        node.insert(
            "metadata".into(),
            Value::Object(safe_loads(metadata.as_deref())),
        );
        node.insert("updated_at".into(), column_json(row, "updated_at")?);
        Ok(Value::Object(node))
    })?;
    let mut nodes: Vec<Value> = rows.filter_map(Result::ok).collect();
    let mut edges: Vec<Value> = edge_order
        .iter()
        .filter_map(|id| edges_by_id.get(id).cloned())
        .collect();

    if let Some(allowed) = options.allowed_workspaces.as_ref() {
        nodes = filter_scoped_nodes(
            conn,
            nodes,
            Some(allowed),
            options.include_legacy_global,
            |node| {
                node.get("id")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .to_string()
            },
        )?;
        let kept: BTreeSet<&str> = nodes
            .iter()
            .filter_map(|node| node.get("id").and_then(Value::as_str))
            .collect();
        edges.retain(|edge| {
            let end = |key: &str| edge.get(key).and_then(Value::as_str).unwrap_or_default();
            kept.contains(end("from")) && kept.contains(end("to"))
        });
    }

    let mut out = Map::new();
    out.insert("root".into(), Value::String(node_id));
    out.insert("depth".into(), Value::from(depth));
    out.insert("nodes".into(), Value::Array(nodes));
    out.insert("edges".into(), Value::Array(edges));
    Ok(Value::Object(out))
}

#[cfg(test)]
mod tests {
    use super::*;

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
               ('a','Decision','Alpha','about ranking','{\"k\":1}','2026-01-02T00:00:00'),
               ('b','Concept','Beta','about ranking','{}','2026-01-03T00:00:00'),
               ('c','Concept','Gamma',NULL,NULL,'2026-01-01T00:00:00');
             INSERT INTO nodes_v2 VALUES ('a','w1'),('b',NULL),('c','w2');
             INSERT INTO edges VALUES
               ('e1','a','b','MENTIONS',0.9,'{}','2026-02-01T00:00:00'),
               ('e2','b','c','CONTAINS',0.5,'{\"note\":\"x\"}','2026-02-02T00:00:00');",
        )
        .unwrap();
        (dir, conn)
    }

    fn ids(payload: &Value, key: &str, field: &str) -> Vec<String> {
        payload[key]
            .as_array()
            .unwrap()
            .iter()
            .map(|item| item[field].as_str().unwrap().to_string())
            .collect()
    }

    #[test]
    fn relationship_search_orders_and_filters_like_python() {
        let (_dir, conn) = graph();
        let all = relationship_search(&conn, &RelationshipQuery::default()).unwrap();
        assert_eq!(ids(&all, "relationships", "id"), vec!["e1", "e2"]);
        assert_eq!(all["query"], "");
        assert_eq!(all["relationships"][0]["source"]["id"], "a");
        assert_eq!(all["relationships"][0]["target"]["title"], "Beta");
        assert_eq!(
            all["relationships"][1]["metadata"],
            serde_json::json!({"note": "x"})
        );
        // A REAL weight stays a float; a NULL summary stays null.
        assert_eq!(all["relationships"][0]["weight"], serde_json::json!(0.9));
        assert_eq!(all["relationships"][1]["target"]["summary"], Value::Null);

        let by_type = relationship_search(
            &conn,
            &RelationshipQuery {
                relationship_type: " contain ".into(),
                ..Default::default()
            },
        )
        .unwrap();
        assert_eq!(ids(&by_type, "relationships", "id"), vec!["e2"]);
        assert_eq!(by_type["relationship_type"], "contain");

        let by_node = relationship_search(
            &conn,
            &RelationshipQuery {
                node_id: "c".into(),
                ..Default::default()
            },
        )
        .unwrap();
        assert_eq!(ids(&by_node, "relationships", "id"), vec!["e2"]);

        let by_query = relationship_search(
            &conn,
            &RelationshipQuery {
                query: "ranking".into(),
                ..Default::default()
            },
        )
        .unwrap();
        assert_eq!(ids(&by_query, "relationships", "id"), vec!["e1", "e2"]);
    }

    #[test]
    fn relationship_limits_and_scoping_follow_the_contract() {
        let (_dir, conn) = graph();
        let one = relationship_search(
            &conn,
            &RelationshipQuery {
                limit: 1,
                ..Default::default()
            },
        )
        .unwrap();
        assert_eq!(one["relationships"].as_array().unwrap().len(), 1);
        // 0 means "the default 30", and a huge limit clamps rather than failing.
        assert_eq!(clamp_limit(0, 30, 200), 30);
        assert_eq!(clamp_limit(500, 30, 200), 200);
        assert_eq!(clamp_limit(-4, 30, 200), 1);

        let w1: BTreeSet<String> = ["w1".to_string()].into_iter().collect();
        let scoped = relationship_search(
            &conn,
            &RelationshipQuery {
                allowed_workspaces: Some(w1.clone()),
                ..Default::default()
            },
        )
        .unwrap();
        // e1 spans w1 → NULL: without the legacy opt-in the far end is invisible.
        assert!(scoped["relationships"].as_array().unwrap().is_empty());
        let scoped = relationship_search(
            &conn,
            &RelationshipQuery {
                allowed_workspaces: Some(w1),
                include_legacy_global: true,
                ..Default::default()
            },
        )
        .unwrap();
        assert_eq!(ids(&scoped, "relationships", "id"), vec!["e1"]);
    }

    #[test]
    fn traverse_walks_undirected_and_hydrates_by_recency() {
        let (_dir, conn) = graph();
        let one = traverse(&conn, " a ", &TraverseOptions::default()).unwrap();
        assert_eq!(one["root"], "a");
        assert_eq!(one["depth"], 1);
        assert_eq!(ids(&one, "nodes", "id"), vec!["b", "a"]);
        assert_eq!(ids(&one, "edges", "id"), vec!["e1"]);

        let two = traverse(
            &conn,
            "a",
            &TraverseOptions {
                depth: 2,
                ..Default::default()
            },
        )
        .unwrap();
        assert_eq!(ids(&two, "nodes", "id"), vec!["b", "a", "c"]);
        assert_eq!(ids(&two, "edges", "id"), vec!["e1", "e2"]);

        // A negative depth is how "the seed alone" is spelled; a zero is falsy
        // in Python and therefore means the default of one round.
        let zero = traverse(
            &conn,
            "a",
            &TraverseOptions {
                depth: -1,
                ..Default::default()
            },
        )
        .unwrap();
        assert_eq!(ids(&zero, "nodes", "id"), vec!["a"]);
        assert!(zero["edges"].as_array().unwrap().is_empty());
        // Out-of-range depths clamp instead of failing.
        for (asked, expect) in [(9, 4), (-1, 0), (0, 1)] {
            let out = traverse(
                &conn,
                "a",
                &TraverseOptions {
                    depth: asked,
                    ..Default::default()
                },
            )
            .unwrap();
            assert_eq!(out["depth"], expect);
        }
    }

    #[test]
    fn traverse_admits_nodes_only_while_under_the_limit() {
        let (_dir, conn) = graph();
        let capped = traverse(
            &conn,
            "a",
            &TraverseOptions {
                depth: 3,
                limit: 2,
                ..Default::default()
            },
        )
        .unwrap();
        assert_eq!(capped["nodes"].as_array().unwrap().len(), 2);
        // e2's far endpoint never joined, but the edge was seen, so it stays —
        // the honest record of what the walk touched.
        assert_eq!(ids(&capped, "edges", "id"), vec!["e1"]);
        let missing = traverse(&conn, "nope", &TraverseOptions::default()).unwrap();
        assert!(missing["nodes"].as_array().unwrap().is_empty());
        assert_eq!(missing["root"], "nope");
    }

    #[test]
    fn traverse_refuses_a_blank_or_invisible_seed() {
        let (_dir, conn) = graph();
        assert!(matches!(
            traverse(&conn, "   ", &TraverseOptions::default()),
            Err(CoreError::InvalidRequest(message)) if message == "node_id required"
        ));
        let w2: BTreeSet<String> = ["w2".to_string()].into_iter().collect();
        let err = traverse(
            &conn,
            "a",
            &TraverseOptions {
                allowed_workspaces: Some(w2),
                ..Default::default()
            },
        )
        .unwrap_err();
        assert_eq!(format!("{err}"), "graph node not found: a");
        // An empty allowed set may read nothing at all.
        let none = BTreeSet::new();
        assert!(traverse(
            &conn,
            "a",
            &TraverseOptions {
                allowed_workspaces: Some(none),
                ..Default::default()
            },
        )
        .is_err());
    }

    #[test]
    fn traverse_drops_scoped_out_nodes_and_their_edges() {
        let (_dir, conn) = graph();
        let w1: BTreeSet<String> = ["w1".to_string()].into_iter().collect();
        let scoped = traverse(
            &conn,
            "a",
            &TraverseOptions {
                depth: 2,
                allowed_workspaces: Some(w1.clone()),
                ..Default::default()
            },
        )
        .unwrap();
        assert_eq!(ids(&scoped, "nodes", "id"), vec!["a"]);
        assert!(scoped["edges"].as_array().unwrap().is_empty());
        let legacy = traverse(
            &conn,
            "a",
            &TraverseOptions {
                depth: 2,
                limit: 100,
                allowed_workspaces: Some(w1),
                include_legacy_global: true,
            },
        )
        .unwrap();
        assert_eq!(ids(&legacy, "nodes", "id"), vec!["b", "a"]);
        assert_eq!(ids(&legacy, "edges", "id"), vec!["e1"]);
        assert!(format!("{:?}", TraverseOptions::default()).contains("depth"));
        assert!(format!("{:?}", RelationshipQuery::default()).contains("limit"));
    }
}
