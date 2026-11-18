//! Bi-temporal knowledge representation, point-in-time queries, and evolution vs contradiction analysis.
//!
//! Tracks both **valid time** (`valid_from` .. `valid_until` in the real world)
//! and **transaction time** (`recorded_at`, `superseded_at` in the Brain store)
//! inside `nodes.metadata_json.temporal` and `nodes_v2` lifecycle columns,
//! preserving 100% legacy table compatibility and rollback safety.

use std::collections::{BTreeSet, HashMap};

use rusqlite::{params, Connection, Transaction};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use crate::db::CoreError;
use crate::entity_resolution::canonical_entity_key;
use crate::pytext::parse_iso;
use crate::read::{scoped_workspace_visible, workspace_is_default, DEFAULT_WORKSPACE_ID};

fn parse_temporal_iso(value: Option<&str>) -> Option<f64> {
    let raw = value?.trim();
    if raw.is_empty() {
        return None;
    }
    if let Some(ts) = parse_iso(Some(raw)) {
        return Some(ts);
    }
    if let Some(stripped) = raw.strip_suffix('Z').or_else(|| raw.strip_suffix('z')) {
        return parse_iso(Some(stripped));
    }
    // Handle trailing +HH:MM or -HH:MM offset after the date prefix.
    if raw.len() > 16 {
        let (date_prefix, tail) = raw.split_at(10);
        if let Some(pos) = tail.rfind(['+', '-']) {
            if pos > 0 {
                let clock = &tail[..pos];
                let offset_str = &tail[pos..];
                let sign = if offset_str.starts_with('-') {
                    -1.0
                } else {
                    1.0
                };
                let mut off_parts = offset_str[1..].split(':');
                let off_h: f64 = off_parts.next()?.parse().ok()?;
                let off_m: f64 = off_parts.next().unwrap_or("0").parse().ok()?;
                let local_naive = format!("{date_prefix}{clock}");
                let base = parse_iso(Some(&local_naive))?;
                return Some(base - sign * (off_h * 3600.0 + off_m * 60.0));
            }
        }
    }
    None
}

fn node_matches_workspace(row_ws: Option<&str>, meta: &Value, target_ws: &str) -> bool {
    let eff_ws = row_ws.map(str::trim).filter(|s| !s.is_empty()).or_else(|| {
        meta.get("workspace_id")
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|s| !s.is_empty())
    });
    let mut allowed = BTreeSet::new();
    allowed.insert(target_ws);
    scoped_workspace_visible(eff_ws, &allowed, workspace_is_default(Some(target_ws)))
}

/// Bi-temporal validity & transaction interval for a knowledge node.
#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq, Eq)]
pub struct TemporalInterval {
    pub valid_from: Option<String>,
    pub valid_until: Option<String>,
    pub recorded_at: String,
    pub superseded_at: Option<String>,
    pub superseded_by: Option<String>,
}

impl TemporalInterval {
    /// Whether this fact is active at `as_of_epoch` (seconds since Unix epoch).
    ///
    /// Evaluates valid-time bounds when present, falling back to transaction
    /// timestamps (`recorded_at` / `superseded_at`).
    pub fn is_active_at_epoch(&self, as_of_epoch: f64) -> bool {
        let start = parse_temporal_iso(self.valid_from.as_deref())
            .or_else(|| parse_temporal_iso(Some(self.recorded_at.as_str())));
        if let Some(s) = start {
            if as_of_epoch + 1e-6 < s {
                return false;
            }
        }
        let end = parse_temporal_iso(self.valid_until.as_deref())
            .or_else(|| parse_temporal_iso(self.superseded_at.as_deref()));
        if let Some(e) = end {
            if as_of_epoch + 1e-6 >= e {
                return false;
            }
        }
        true
    }

    /// Whether two intervals overlap in valid time.
    pub fn overlaps(&self, other: &Self) -> bool {
        let a_start = parse_temporal_iso(self.valid_from.as_deref())
            .or_else(|| parse_temporal_iso(Some(self.recorded_at.as_str())))
            .unwrap_or(f64::NEG_INFINITY);
        let a_end = parse_temporal_iso(self.valid_until.as_deref())
            .or_else(|| parse_temporal_iso(self.superseded_at.as_deref()))
            .unwrap_or(f64::INFINITY);
        let b_start = parse_temporal_iso(other.valid_from.as_deref())
            .or_else(|| parse_temporal_iso(Some(other.recorded_at.as_str())))
            .unwrap_or(f64::NEG_INFINITY);
        let b_end = parse_temporal_iso(other.valid_until.as_deref())
            .or_else(|| parse_temporal_iso(other.superseded_at.as_deref()))
            .unwrap_or(f64::INFINITY);
        a_start < b_end && b_start < a_end
    }
}

/// One temporally anchored knowledge statement.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct TemporalFactRecord {
    pub node_id: String,
    pub node_type: String,
    pub title: String,
    pub summary: String,
    pub subject_key: String,
    pub interval: TemporalInterval,
    pub confidence: f64,
    pub provenance_sources: Vec<String>,
}

/// Semantic distinction between historical change, refinement, and true conflict.
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum TemporalConflictKind {
    /// Earlier state superseded by a newer decision (`valid_until <= valid_from` or `SUPERSEDES`).
    TemporalEvolution,
    /// Coexisting refinement or scoped exception (`REFINES`, `EXTENDS`, `PART_OF`).
    ComplementaryRefinement,
    /// Overlapping active claims that contradict one another (`CONTRADICTS`).
    ActiveContradiction,
}

/// Pairwise temporal/conflict classification between two facts on the same subject.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct TemporalRelationClassification {
    pub from_node: String,
    pub to_node: String,
    pub kind: TemporalConflictKind,
    pub rationale: String,
}

/// Point-in-time or present-day synthesis over a subject's temporal evolution.
#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq)]
pub struct TemporalTimelineSummary {
    pub subject_query: String,
    pub as_of: Option<String>,
    pub active_facts: Vec<TemporalFactRecord>,
    pub historical_facts: Vec<TemporalFactRecord>,
    pub unreached_facts: Vec<TemporalFactRecord>,
    pub classifications: Vec<TemporalRelationClassification>,
}

impl TemporalTimelineSummary {
    /// Whether there is at least one unresolved active contradiction at the queried time.
    pub fn has_active_contradiction(&self) -> bool {
        let active_ids: BTreeSet<&str> = self
            .active_facts
            .iter()
            .map(|f| f.node_id.as_str())
            .collect();
        self.classifications.iter().any(|c| {
            c.kind == TemporalConflictKind::ActiveContradiction
                && active_ids.contains(c.from_node.as_str())
                && active_ids.contains(c.to_node.as_str())
        })
    }
}

fn read_temporal_interval(
    meta: &Value,
    created_at: &str,
    v2_superseded_by: Option<&str>,
) -> TemporalInterval {
    let block = meta.get("temporal").unwrap_or(&Value::Null);
    let valid_from = block
        .get("valid_from")
        .or_else(|| meta.get("valid_from"))
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string)
        .or_else(|| {
            let trimmed = created_at.trim();
            (!trimmed.is_empty()).then(|| trimmed.to_string())
        });
    let valid_until = block
        .get("valid_until")
        .or_else(|| meta.get("valid_until"))
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string);
    let recorded_at = block
        .get("recorded_at")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .unwrap_or(created_at)
        .to_string();
    let superseded_at = block
        .get("superseded_at")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string);
    let superseded_by = v2_superseded_by
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string)
        .or_else(|| {
            block
                .get("superseded_by")
                .or_else(|| meta.get("superseded_by"))
                .and_then(Value::as_str)
                .map(str::trim)
                .filter(|s| !s.is_empty())
                .map(str::to_string)
        });
    TemporalInterval {
        valid_from,
        valid_until,
        recorded_at,
        superseded_at,
        superseded_by,
    }
}

/// Stamp bi-temporal metadata onto `node_id`, optionally marking `supersedes_node_id`
/// as superseded as of `valid_from` (non-destructively preserving historical state).
pub fn stamp_temporal_fact(
    txn: &Transaction<'_>,
    node_id: &str,
    subject: &str,
    valid_from: &str,
    valid_until: Option<&str>,
    recorded_at: &str,
    supersedes_node_id: Option<&str>,
) -> Result<(), CoreError> {
    let raw_meta: String = txn
        .query_row(
            "SELECT metadata_json FROM nodes WHERE id = ?1",
            params![node_id],
            |r| r.get::<_, Option<String>>(0),
        )?
        .unwrap_or_else(|| "{}".to_string());
    let mut meta: Value = serde_json::from_str(&raw_meta).unwrap_or_else(|_| json!({}));
    if let Some(obj) = meta.as_object_mut() {
        obj.insert("subject".to_string(), json!(subject));
        obj.insert(
            "temporal".to_string(),
            json!({
                "valid_from": valid_from,
                "valid_until": valid_until,
                "recorded_at": recorded_at,
            }),
        );
    }
    txn.execute(
        "UPDATE nodes SET metadata_json = ?1 WHERE id = ?2",
        params![
            serde_json::to_string(&meta).unwrap_or_else(|_| "{}".to_string()),
            node_id
        ],
    )?;

    if let Some(old_id) = supersedes_node_id.map(str::trim).filter(|s| !s.is_empty()) {
        let old_raw: String = txn
            .query_row(
                "SELECT metadata_json, created_at FROM nodes WHERE id = ?1",
                params![old_id],
                |r| {
                    Ok((
                        r.get::<_, Option<String>>(0)?
                            .unwrap_or_else(|| "{}".to_string()),
                        r.get::<_, Option<String>>(1)?.unwrap_or_default(),
                    ))
                },
            )
            .map(|(m, _)| m)?;
        let mut old_meta: Value = serde_json::from_str(&old_raw).unwrap_or_else(|_| json!({}));
        if let Some(obj) = old_meta.as_object_mut() {
            let mut t = obj.get("temporal").cloned().unwrap_or_else(|| json!({}));
            if let Some(t_obj) = t.as_object_mut() {
                t_obj.insert("valid_until".to_string(), json!(valid_from));
                t_obj.insert("superseded_at".to_string(), json!(recorded_at));
                t_obj.insert("superseded_by".to_string(), json!(node_id));
            }
            obj.insert("temporal".to_string(), t);
            obj.insert("superseded_by".to_string(), json!(node_id));
        }
        txn.execute(
            "UPDATE nodes SET metadata_json = ?1 WHERE id = ?2",
            params![
                serde_json::to_string(&old_meta).unwrap_or_else(|_| "{}".to_string()),
                old_id
            ],
        )?;
        let has_v2: bool = txn
            .query_row(
                "SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type='table' AND name='nodes_v2')",
                [],
                |r| r.get(0),
            )
            .unwrap_or(false);
        if has_v2 {
            let v2_cols: Vec<String> = txn
                .prepare("PRAGMA table_info(nodes_v2)")?
                .query_map([], |r| r.get::<_, String>(1))?
                .filter_map(Result::ok)
                .collect();
            if v2_cols.iter().any(|c| c == "lifecycle_status") {
                let _ = txn.execute(
                    "UPDATE nodes_v2
                     SET superseded_by = ?1, lifecycle_status = 'superseded'
                     WHERE id = ?2",
                    params![node_id, old_id],
                );
            } else if v2_cols.iter().any(|c| c == "valid_to") {
                let _ = txn.execute(
                    "UPDATE nodes_v2
                     SET superseded_by = ?1, valid_to = COALESCE(valid_to, ?2)
                     WHERE id = ?3",
                    params![node_id, valid_from, old_id],
                );
            } else {
                let _ = txn.execute(
                    "UPDATE nodes_v2 SET superseded_by = ?1 WHERE id = ?2",
                    params![node_id, old_id],
                );
            }
        }
        let edge_id = format!("temporal_supersedes:{node_id}->{old_id}");
        let has_edge_meta: bool = txn
            .prepare("PRAGMA table_info(edges)")?
            .query_map([], |r| r.get::<_, String>(1))?
            .filter_map(Result::ok)
            .any(|col| col == "metadata_json");
        if has_edge_meta {
            txn.execute(
                "INSERT OR IGNORE INTO edges (id, from_node, to_node, type, weight, metadata_json, created_at)
                 VALUES (?1, ?2, ?3, 'SUPERSEDES', 1.0, '{}', ?4)",
                params![edge_id, node_id, old_id, recorded_at],
            )?;
        } else {
            txn.execute(
                "INSERT INTO edges (id, from_node, to_node, type, weight)
                 VALUES (?1, ?2, ?3, 'SUPERSEDES', 1.0)
                 ON CONFLICT(id) DO NOTHING",
                params![edge_id, node_id, old_id],
            )?;
        }
    }
    Ok(())
}

/// Query temporal facts matching `subject_query`, evaluated either at `as_of_iso`
/// (point-in-time travel) or at current latest state (`as_of_iso = None`).
pub fn query_temporal_timeline(
    conn: &Connection,
    workspace_id: Option<&str>,
    subject_query: &str,
    as_of_iso: Option<&str>,
) -> Result<TemporalTimelineSummary, CoreError> {
    let target_ws = workspace_id
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .unwrap_or(DEFAULT_WORKSPACE_ID);
    let query_key = canonical_entity_key(subject_query);
    let query_lower = subject_query.trim().to_lowercase();
    let as_of_epoch = parse_temporal_iso(as_of_iso);

    let has_v2: bool = conn
        .query_row(
            "SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type='table' AND name='nodes_v2')",
            [],
            |r| r.get(0),
        )
        .unwrap_or(false);
    let sql = if has_v2 {
        "SELECT n.id, n.type, n.title, n.summary, n.metadata_json, n.created_at,
                v.workspace_id, v.superseded_by, COALESCE(v.importance_score, 0.8)
         FROM nodes n LEFT JOIN nodes_v2 v ON v.id = n.id
         ORDER BY n.created_at ASC, n.id ASC"
    } else {
        "SELECT n.id, n.type, n.title, n.summary, n.metadata_json, n.created_at,
                NULL, NULL, 0.8
         FROM nodes n
         ORDER BY n.created_at ASC, n.id ASC"
    };

    let mut matched: Vec<TemporalFactRecord> = Vec::new();
    let mut stmt = conn.prepare(sql)?;
    let rows = stmt.query_map([], |r| {
        Ok((
            r.get::<_, String>(0)?,
            r.get::<_, String>(1)?,
            r.get::<_, String>(2)?,
            r.get::<_, String>(3)?,
            r.get::<_, Option<String>>(4)?.unwrap_or_default(),
            r.get::<_, Option<String>>(5)?.unwrap_or_default(),
            r.get::<_, Option<String>>(6)?,
            r.get::<_, Option<String>>(7)?,
            r.get::<_, f64>(8).unwrap_or(0.8),
        ))
    })?;

    for item in rows {
        let (id, ntype, title, summary, raw_meta, created_at, row_ws, v2_sup, importance) = item?;
        let meta: Value = serde_json::from_str(&raw_meta).unwrap_or_else(|_| json!({}));
        if !node_matches_workspace(row_ws.as_deref(), &meta, target_ws) {
            continue;
        }
        let subject_str = meta
            .get("subject")
            .and_then(Value::as_str)
            .unwrap_or(title.as_str());
        let subject_key = canonical_entity_key(subject_str);
        let title_key = canonical_entity_key(&title);
        let text_hit = !query_lower.is_empty()
            && (title.to_lowercase().contains(&query_lower)
                || summary.to_lowercase().contains(&query_lower)
                || subject_str.to_lowercase().contains(&query_lower));
        let key_hit = !query_key.is_empty()
            && (subject_key.contains(&query_key)
                || query_key.contains(&subject_key) && !subject_key.is_empty()
                || title_key.contains(&query_key));
        if !text_hit && !key_hit {
            continue;
        }

        let interval = read_temporal_interval(&meta, &created_at, v2_sup.as_deref());
        let confidence = meta
            .get("confidence")
            .and_then(Value::as_f64)
            .unwrap_or(importance)
            .clamp(0.0, 1.0);
        let mut prov = Vec::new();
        if let Some(src) = meta.get("source_node").and_then(Value::as_str) {
            if !src.trim().is_empty() {
                prov.push(src.trim().to_string());
            }
        }
        prov.push(id.clone());

        matched.push(TemporalFactRecord {
            node_id: id,
            node_type: ntype,
            title,
            summary,
            subject_key: if subject_key.is_empty() {
                title_key
            } else {
                subject_key
            },
            interval,
            confidence,
            provenance_sources: prov,
        });
    }

    let by_id: HashMap<String, &TemporalFactRecord> =
        matched.iter().map(|f| (f.node_id.clone(), f)).collect();

    let mut classifications: Vec<TemporalRelationClassification> = Vec::new();
    let has_edge_meta: bool = conn
        .prepare("PRAGMA table_info(edges)")?
        .query_map([], |r| r.get::<_, String>(1))?
        .filter_map(Result::ok)
        .any(|col| col == "metadata_json");
    let edge_sql = if has_edge_meta {
        "SELECT from_node, to_node, UPPER(COALESCE(NULLIF(json_extract(metadata_json, '$.legacy_label'), ''), type)) FROM edges"
    } else {
        "SELECT from_node, to_node, UPPER(type) FROM edges"
    };
    let mut edge_stmt = conn.prepare(edge_sql)?;
    let edge_rows = edge_stmt.query_map([], |r| {
        Ok((
            r.get::<_, String>(0)?,
            r.get::<_, String>(1)?,
            r.get::<_, String>(2)?,
        ))
    })?;
    for edge in edge_rows {
        let (from_id, to_id, etype) = edge?;
        let (Some(a), Some(b)) = (by_id.get(&from_id), by_id.get(&to_id)) else {
            continue;
        };
        match etype.as_str() {
            "SUPERSEDES" => classifications.push(TemporalRelationClassification {
                from_node: from_id,
                to_node: to_id,
                kind: TemporalConflictKind::TemporalEvolution,
                rationale: format!("{} supersedes earlier state {}", a.title, b.title),
            }),
            "REFINES" | "EXTENDS" | "PART_OF" | "COMPLEMENTS" => {
                classifications.push(TemporalRelationClassification {
                    from_node: from_id,
                    to_node: to_id,
                    kind: TemporalConflictKind::ComplementaryRefinement,
                    rationale: format!("{} refines/coexists with {}", a.title, b.title),
                });
            }
            "CONTRADICTS" => {
                let kind = if a.interval.overlaps(&b.interval)
                    && a.interval.superseded_by.is_none()
                    && b.interval.superseded_by.is_none()
                {
                    TemporalConflictKind::ActiveContradiction
                } else {
                    TemporalConflictKind::TemporalEvolution
                };
                classifications.push(TemporalRelationClassification {
                    from_node: from_id,
                    to_node: to_id,
                    kind,
                    rationale: format!(
                        "explicit conflict edge between {} and {}",
                        a.title, b.title
                    ),
                });
            }
            _ => {}
        }
    }

    let mut active_facts = Vec::new();
    let mut historical_facts = Vec::new();
    let mut unreached_facts = Vec::new();

    for fact in matched {
        if let Some(ts) = as_of_epoch {
            let start = parse_temporal_iso(fact.interval.valid_from.as_deref())
                .or_else(|| parse_temporal_iso(Some(fact.interval.recorded_at.as_str())));
            if start.is_some_and(|s| ts + 1e-6 < s) {
                unreached_facts.push(fact);
            } else if fact.interval.is_active_at_epoch(ts) {
                active_facts.push(fact);
            } else {
                historical_facts.push(fact);
            }
        } else {
            let is_retired =
                fact.interval.superseded_by.is_some() || fact.interval.valid_until.is_some();
            if is_retired {
                historical_facts.push(fact);
            } else {
                active_facts.push(fact);
            }
        }
    }

    Ok(TemporalTimelineSummary {
        subject_query: subject_query.to_string(),
        as_of: as_of_iso.map(str::to_string),
        active_facts,
        historical_facts,
        unreached_facts,
        classifications,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn project_a_jan_mar_may_evolution_and_point_in_time_queries() {
        let mut conn = Connection::open_in_memory().expect("mem");
        conn.execute_batch(
            "CREATE TABLE nodes (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                title TEXT NOT NULL,
                summary TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );
            CREATE TABLE edges (
                id TEXT PRIMARY KEY,
                from_node TEXT NOT NULL,
                to_node TEXT NOT NULL,
                type TEXT NOT NULL,
                weight REAL NOT NULL DEFAULT 1.0
            );",
        )
        .expect("ddl");

        conn.execute(
            "INSERT INTO nodes (id, type, title, summary, metadata_json, created_at) VALUES
             ('dec:jan', 'Decision', 'Project A Python Stack', 'Project A is developed in Python.', '{}', '2026-01-10T00:00:00Z'),
             ('dec:mar', 'Decision', 'Project A Rust Migration', 'Project A migrates core runtime to Rust.', '{}', '2026-03-15T00:00:00Z'),
             ('dec:may', 'Decision', 'Project A Hybrid Compute Seam', 'Selected Python ML modules remain alongside Rust core.', '{}', '2026-05-20T00:00:00Z')",
            [],
        )
        .expect("seed");

        let txn = conn.transaction().expect("txn");
        stamp_temporal_fact(
            &txn,
            "dec:jan",
            "Project A",
            "2026-01-10T00:00:00Z",
            None,
            "2026-01-10T00:00:00Z",
            None,
        )
        .expect("jan");
        stamp_temporal_fact(
            &txn,
            "dec:mar",
            "Project A",
            "2026-03-15T00:00:00Z",
            None,
            "2026-03-15T00:00:00Z",
            Some("dec:jan"),
        )
        .expect("mar supersedes jan");
        stamp_temporal_fact(
            &txn,
            "dec:may",
            "Project A",
            "2026-05-20T00:00:00Z",
            None,
            "2026-05-20T00:00:00Z",
            None,
        )
        .expect("may refines mar");
        txn.execute(
            "INSERT INTO edges (id, from_node, to_node, type, weight)
             VALUES ('e:may-refines-mar', 'dec:may', 'dec:mar', 'REFINES', 1.0)",
            [],
        )
        .expect("refines edge");
        txn.commit().expect("commit");

        // 1. Point-in-time February: only January Python decision is active!
        let feb = query_temporal_timeline(
            &conn,
            Some("personal"),
            "Project A",
            Some("2026-02-01T00:00:00Z"),
        )
        .expect("feb");
        assert_eq!(feb.active_facts.len(), 1);
        assert_eq!(feb.active_facts[0].node_id, "dec:jan");
        assert_eq!(feb.unreached_facts.len(), 2);

        // 2. Point-in-time April: March Rust migration is active, January is historical!
        let apr = query_temporal_timeline(
            &conn,
            Some("personal"),
            "Project A",
            Some("2026-04-01T00:00:00Z"),
        )
        .expect("apr");
        assert_eq!(apr.active_facts.len(), 1);
        assert_eq!(apr.active_facts[0].node_id, "dec:mar");
        assert_eq!(apr.historical_facts.len(), 1);
        assert_eq!(apr.historical_facts[0].node_id, "dec:jan");

        // 3. Present-day state: Rust core (Mar) + retained Python modules (May) coexist without contradiction!
        let now = query_temporal_timeline(&conn, Some("personal"), "Project A", None).expect("now");
        let active_ids: Vec<&str> = now
            .active_facts
            .iter()
            .map(|f| f.node_id.as_str())
            .collect();
        assert_eq!(active_ids, vec!["dec:mar", "dec:may"]);
        assert_eq!(now.historical_facts.len(), 1);
        assert!(!now.has_active_contradiction());
        assert!(now
            .classifications
            .iter()
            .any(|c| c.kind == TemporalConflictKind::TemporalEvolution));
        assert!(now
            .classifications
            .iter()
            .any(|c| c.kind == TemporalConflictKind::ComplementaryRefinement));
    }
}
