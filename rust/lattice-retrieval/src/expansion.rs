//! The two opt-in ranking paths: graph candidate expansion and RRF fusion.
//!
//! Both were product features with switches in the home dock's 기능 drawer and
//! rows in `lattice-platform`'s feature catalog marked `live: true`. Neither
//! existed in this crate. `hybrid_search` hard-coded `fusion_strategy =
//! "alpha"` and emitted a `graph_expansion` block whose `enabled` was the
//! literal `false`, so flipping either switch changed nothing at all. They
//! were Python (`lattice_brain/graph/fusion.py`), and that module went with
//! the write path.
//!
//! This is the port, gated exactly as the catalog says it is gated:
//!
//! * `LATTICEAI_GRAPH_EXPANSION=1` — after ranking, walk one hop out from the
//!   strongest hits and add neighbours that were never candidates. A node one
//!   edge away from a match is otherwise **unreachable**, no matter how
//!   relevant: neither the keyword index nor the vector index knows it exists.
//! * `LATTICEAI_FUSION_RRF=1` — fuse the two channels by *position* instead of
//!   by score. Lexical scores are `1/rank` and vector scores are a normalized
//!   cosine; a weighted sum of two scales that do not mean the same thing is a
//!   number, not a ranking.
//!
//! ## Off is byte-identical
//!
//! Both gates are read once and default to off, and every function here is
//! called only inside `if enabled`. With neither variable set, `hybrid_search`
//! executes exactly the statements it executed before — which is what keeps
//! `rust/fixtures/golden`'s 75 parity cases and 13 suites unchanged.

use std::collections::{BTreeMap, BTreeSet};

use lattice_core::pytext::{round6, safe_loads};
use lattice_core::read::read_tables;
use lattice_core::CoreError;
use rusqlite::Connection;
use serde_json::{Map, Value};

/// `LATTICEAI_GRAPH_EXPANSION` — one-hop candidate expansion.
pub const GRAPH_EXPANSION_ENV: &str = "LATTICEAI_GRAPH_EXPANSION";
/// `LATTICEAI_FUSION_RRF` — rank fusion instead of score fusion.
pub const FUSION_RRF_ENV: &str = "LATTICEAI_FUSION_RRF";

/// How many of the top hits are walked out from.
pub const EXPANSION_SEEDS: usize = 3;
/// A neighbour enters at this fraction of the seed's score. It is a lead, not
/// a match: something *near* an answer must never outrank the answer.
pub const EXPANSION_SCORE_FACTOR: f64 = 0.5;
/// RRF's smoothing constant, from Cormack et al. 2009. 60 is the published
/// default and the one every comparable implementation uses; a different value
/// here would make our ranking incomparable to everyone else's for no gain.
pub const RRF_K: f64 = 60.0;

fn flag(name: &str) -> bool {
    matches!(
        std::env::var(name)
            .unwrap_or_default()
            .trim()
            .to_ascii_lowercase()
            .as_str(),
        "1" | "true" | "yes" | "on"
    )
}

/// Whether one-hop expansion is switched on for this process.
pub fn expansion_enabled() -> bool {
    flag(GRAPH_EXPANSION_ENV)
}

/// Whether rank fusion replaces alpha fusion for this process.
pub fn rrf_enabled() -> bool {
    flag(FUSION_RRF_ENV)
}

/// One node reached by walking a single edge out from a hit.
#[derive(Debug, Clone, PartialEq)]
pub struct Neighbour {
    pub node_id: String,
    pub node_type: Value,
    pub title: Value,
    pub summary: Value,
    pub metadata: Value,
    pub updated_at: Value,
    /// Which hit it hangs off, so the answer can say *why* this is here.
    pub seed_id: String,
    /// The edge's canonical taxonomy value (`USES`, `PART_OF`, …) — that is
    /// what `edges.type` holds once the writer has normalized it.
    pub edge_type: String,
    /// The label the writer was handed (`사용함`, `구성요소`, …), when it
    /// differs from the canonical value and the writer kept it. This is the
    /// word a Korean-speaking reader recognises.
    pub edge_label: String,
    /// The edge's own weight.
    pub edge_weight: f64,
    /// The evidence sentence the extractor stored on the edge, if any.
    pub edge_context: String,
    /// `verb` | `definition` | `structure` | `contrast` | `cooccurrence`.
    pub edge_evidence: String,
    /// Whether the edge points seed → neighbour (`true`) or the other way.
    pub outgoing: bool,
}

impl Neighbour {
    /// The `via` block a match carries so a reader can see the path in.
    pub fn provenance(&self) -> Value {
        let mut via = Map::new();
        via.insert("seed_node_id".into(), Value::String(self.seed_id.clone()));
        via.insert("edge_type".into(), Value::String(self.edge_type.clone()));
        via.insert("edge_weight".into(), Value::from(round6(self.edge_weight)));
        via.insert(
            "direction".into(),
            Value::String(
                if self.outgoing {
                    "outgoing"
                } else {
                    "incoming"
                }
                .into(),
            ),
        );
        if !self.edge_label.is_empty() && self.edge_label != self.edge_type {
            via.insert("edge_label".into(), Value::String(self.edge_label.clone()));
        }
        if !self.edge_context.is_empty() {
            via.insert("context".into(), Value::String(self.edge_context.clone()));
        }
        if !self.edge_evidence.is_empty() {
            via.insert("evidence".into(), Value::String(self.edge_evidence.clone()));
        }
        Value::Object(via)
    }
}

/// What the walk did, for the result's `graph_expansion` block.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct ExpansionReport {
    pub seeds: usize,
    pub added: usize,
    pub truncated: bool,
    pub failed_seeds: usize,
}

impl ExpansionReport {
    /// The `graph_expansion` block, in the shape the off path already emits.
    pub fn as_json(&self, cap: i64) -> Value {
        let mut map = Map::new();
        map.insert("enabled".into(), Value::Bool(true));
        map.insert("seeds".into(), Value::from(self.seeds));
        map.insert("added".into(), Value::from(self.added));
        map.insert("cap".into(), Value::from(cap));
        map.insert("truncated".into(), Value::Bool(self.truncated));
        map.insert("failed_seeds".into(), Value::from(self.failed_seeds));
        Value::Object(map)
    }
}

/// One-hop neighbours of `seeds`, excluding anything already in the result.
///
/// Ordered by edge weight then node id — deterministic, because a ranking that
/// depends on SQLite's row order is a ranking that changes under VACUUM.
pub fn one_hop(
    conn: &Connection,
    seeds: &[String],
    exclude: &BTreeSet<String>,
    cap: usize,
) -> Result<(Vec<Neighbour>, ExpansionReport), CoreError> {
    let mut report = ExpansionReport {
        seeds: seeds.len(),
        ..ExpansionReport::default()
    };
    if seeds.is_empty() || cap == 0 {
        return Ok((Vec::new(), report));
    }
    let (nodes_table, edges_table) = read_tables(conn);
    let mut taken: BTreeSet<String> = exclude.clone();
    let mut out: Vec<Neighbour> = Vec::new();

    for seed in seeds {
        if out.len() >= cap {
            report.truncated = true;
            break;
        }
        let rows = match edges_of(conn, edges_table, seed.as_str()) {
            Ok(rows) => rows,
            Err(_) => {
                // A seed whose edges cannot be read costs that seed, not the
                // search. The count is reported rather than swallowed.
                report.failed_seeds += 1;
                continue;
            }
        };
        for (other, outgoing, edge_type, weight, metadata) in rows {
            if out.len() >= cap {
                report.truncated = true;
                break;
            }
            if other.is_empty() || !taken.insert(other.clone()) {
                continue;
            }
            let Some((node_type, title, summary, node_meta, updated_at)) =
                load_node(conn, nodes_table, other.as_str())?
            else {
                // An edge to a node that is not there is a dangling edge, not
                // a candidate. Silently skipping it is right; inventing a
                // titleless row for it is not.
                continue;
            };
            out.push(Neighbour {
                node_id: other,
                node_type,
                title,
                summary,
                metadata: node_meta,
                updated_at,
                seed_id: seed.clone(),
                edge_type,
                edge_label: string_field(&metadata, "legacy_label"),
                edge_weight: weight,
                edge_context: string_field(&metadata, "context"),
                edge_evidence: string_field(&metadata, "evidence"),
                outgoing,
            });
            report.added += 1;
        }
    }
    Ok((out, report))
}

type EdgeRow = (String, bool, String, f64, Map<String, Value>);

fn edges_of(conn: &Connection, edges: &str, seed: &str) -> Result<Vec<EdgeRow>, CoreError> {
    let sql = format!(
        "SELECT from_node, to_node, type, weight, metadata_json FROM {edges} \
         WHERE from_node=?1 OR to_node=?1 ORDER BY weight DESC, to_node ASC, from_node ASC"
    );
    let mut statement = conn.prepare(&sql)?;
    let rows = statement.query_map([seed], |row| {
        let from: String = row.get(0).unwrap_or_default();
        let to: String = row.get(1).unwrap_or_default();
        let edge_type: String = row.get(2).unwrap_or_default();
        let weight: f64 = row.get(3).unwrap_or(1.0);
        let metadata: Option<String> = row.get(4)?;
        let outgoing = from == seed;
        let other = if outgoing { to } else { from };
        Ok((
            other,
            outgoing,
            edge_type,
            weight,
            safe_loads(metadata.as_deref()),
        ))
    })?;
    Ok(rows.filter_map(Result::ok).collect())
}

type NodeRow = (Value, Value, Value, Value, Value);

fn load_node(conn: &Connection, nodes: &str, node_id: &str) -> Result<Option<NodeRow>, CoreError> {
    let sql =
        format!("SELECT type, title, summary, metadata_json, updated_at FROM {nodes} WHERE id=?");
    let mut statement = conn.prepare(&sql)?;
    let mut rows = statement.query([node_id])?;
    let Some(row) = rows.next()? else {
        return Ok(None);
    };
    let metadata: Option<String> = row.get(3)?;
    Ok(Some((
        opt_string(row.get::<_, Option<String>>(0)?),
        opt_string(row.get::<_, Option<String>>(1)?),
        opt_string(row.get::<_, Option<String>>(2)?),
        Value::Object(safe_loads(metadata.as_deref())),
        opt_string(row.get::<_, Option<String>>(4)?),
    )))
}

fn opt_string(value: Option<String>) -> Value {
    value.map(Value::String).unwrap_or(Value::Null)
}

fn string_field(metadata: &Map<String, Value>, key: &str) -> String {
    metadata
        .get(key)
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string()
}

/// Reciprocal Rank Fusion over per-channel positions.
///
/// `ranks` maps node id → its 1-based position in each channel it appeared in.
/// A node missing from a channel simply contributes nothing for it — RRF has
/// no notion of "zero score", which is exactly why it does not care that the
/// two channels measure different things.
pub fn rrf_scores(ranks: &BTreeMap<String, Vec<usize>>) -> BTreeMap<String, f64> {
    ranks
        .iter()
        .map(|(node_id, positions)| {
            let total: f64 = positions
                .iter()
                .map(|rank| 1.0 / (RRF_K + *rank as f64))
                .sum();
            (node_id.clone(), round6(total))
        })
        .collect()
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
             INSERT INTO nodes VALUES
               ('hit','Document','Hit','the match','{}','2026-01-01T00:00:00'),
               ('near','Concept','Near','one edge away','{\"a\":1}','2026-01-02T00:00:00'),
               ('far','Concept','Far','two edges away','{}','2026-01-03T00:00:00'),
               ('back','Concept','Back','points at the hit','{}','2026-01-04T00:00:00');
             INSERT INTO edges VALUES
               ('e1','hit','near','USES',1.0,
                '{\"context\":\"[아키텍처] 히트는 니어를 사용한다.\",\"evidence\":\"verb\",\"legacy_label\":\"사용함\"}','2026-01-01'),
               ('e2','back','hit','PART_OF',0.9,'{\"evidence\":\"structure\"}','2026-01-01'),
               ('e3','near','far','MENTIONS',0.35,'{}','2026-01-01'),
               ('e4','hit','ghost','MENTIONS',0.5,'{}','2026-01-01');",
        )
        .unwrap();
        (dir, conn)
    }

    #[test]
    fn both_gates_are_off_unless_the_environment_says_otherwise() {
        // The suite runs with neither variable set; that is the shipped state.
        assert!(!expansion_enabled());
        assert!(!rrf_enabled());
        assert_eq!(EXPANSION_SEEDS, 3);
        assert_eq!(RRF_K, 60.0);
    }

    #[test]
    fn one_hop_reaches_both_directions_and_carries_the_edge() {
        let (_dir, conn) = graph();
        let exclude: BTreeSet<String> = ["hit".to_string()].into_iter().collect();
        let (found, report) = one_hop(&conn, &["hit".into()], &exclude, 5).unwrap();
        let ids: Vec<&str> = found.iter().map(|n| n.node_id.as_str()).collect();
        assert_eq!(ids, ["near", "back"], "weight order, ghost row dropped");
        assert_eq!(report.added, 2);
        assert_eq!(report.seeds, 1);
        assert!(!report.truncated);
        assert_eq!(report.failed_seeds, 0);

        let via = found[0].provenance();
        assert_eq!(via["seed_node_id"], "hit");
        assert_eq!(via["edge_type"], "USES");
        assert_eq!(
            via["edge_label"], "사용함",
            "the Korean word a reader knows"
        );
        assert_eq!(via["direction"], "outgoing");
        assert_eq!(via["evidence"], "verb");
        assert_eq!(via["context"], "[아키텍처] 히트는 니어를 사용한다.");
        assert_eq!(found[0].title, json!("Near"));
        assert_eq!(found[0].metadata["a"], json!(1));
        assert_eq!(found[1].provenance()["direction"], "incoming");
        // An edge with no context claims none rather than an empty string.
        assert!(found[1].provenance().get("context").is_none());
    }

    #[test]
    fn a_node_already_in_the_result_is_never_added_twice() {
        let (_dir, conn) = graph();
        let exclude: BTreeSet<String> = ["hit".into(), "near".into()].into_iter().collect();
        let (found, report) = one_hop(&conn, &["hit".into()], &exclude, 5).unwrap();
        assert_eq!(
            found.iter().map(|n| n.node_id.as_str()).collect::<Vec<_>>(),
            ["back"]
        );
        assert_eq!(report.added, 1);
    }

    #[test]
    fn the_cap_bites_and_says_so() {
        let (_dir, conn) = graph();
        let exclude: BTreeSet<String> = ["hit".to_string()].into_iter().collect();
        let (found, report) = one_hop(&conn, &["hit".into()], &exclude, 1).unwrap();
        assert_eq!(found.len(), 1);
        assert!(report.truncated);
        assert_eq!(report.as_json(1)["truncated"], json!(true));
        assert_eq!(report.as_json(1)["enabled"], json!(true));
        assert_eq!(report.as_json(1)["cap"], json!(1));
    }

    #[test]
    fn no_seeds_and_no_room_are_both_no_work() {
        let (_dir, conn) = graph();
        let empty = BTreeSet::new();
        assert_eq!(one_hop(&conn, &[], &empty, 5).unwrap().0, Vec::new());
        assert_eq!(
            one_hop(&conn, &["hit".into()], &empty, 0).unwrap().0,
            Vec::new()
        );
    }

    #[test]
    fn an_unreadable_seed_costs_that_seed_and_is_counted() {
        let dir = tempfile::tempdir().unwrap();
        let conn = Connection::open(dir.path().join("g.sqlite")).unwrap();
        conn.execute_batch(
            "CREATE TABLE nodes(id TEXT PRIMARY KEY, type TEXT, title TEXT, summary TEXT,
                                metadata_json TEXT, updated_at TEXT);
             CREATE TABLE edges(id TEXT PRIMARY KEY);",
        )
        .unwrap();
        let (found, report) = one_hop(&conn, &["hit".into()], &BTreeSet::new(), 5).unwrap();
        assert!(found.is_empty());
        assert_eq!(report.failed_seeds, 1);
        assert_eq!(report.as_json(5)["failed_seeds"], json!(1));
    }

    #[test]
    fn rrf_rewards_agreement_between_channels() {
        let mut ranks: BTreeMap<String, Vec<usize>> = BTreeMap::new();
        ranks.insert("both".into(), vec![2, 2]);
        ranks.insert("lexical_only".into(), vec![1]);
        ranks.insert("vector_only".into(), vec![1]);
        let scored = rrf_scores(&ranks);
        assert!(scored["both"] > scored["lexical_only"]);
        assert_eq!(scored["lexical_only"], scored["vector_only"]);
        assert_eq!(scored["both"], round6(2.0 / 62.0));
        assert!(rrf_scores(&BTreeMap::new()).is_empty());
    }

    #[test]
    fn a_neighbour_is_worth_half_its_seed() {
        // Stated as arithmetic rather than as a bare inequality so the
        // intent survives a refactor: a lead must never outrank the match it
        // was reached from, and must not be free either.
        let seed_score = 0.8_f64;
        let lead = seed_score * EXPANSION_SCORE_FACTOR;
        assert!(lead < seed_score, "a neighbour cannot outrank its seed");
        assert!(lead > 0.0, "a neighbour still carries some of its seed");
        let report = ExpansionReport::default();
        assert_eq!(report.as_json(5)["added"], json!(0));
        assert!(format!("{report:?}").contains("failed_seeds"));
    }
}
