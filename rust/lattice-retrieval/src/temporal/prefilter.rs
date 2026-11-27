use std::collections::{HashMap, HashSet};

use fancy_regex::Regex;
use lattice_core::pytext::safe_loads;
use lattice_core::{parse_iso, CoreError};
use rusqlite::{params_from_iter, types::Value as SqlValue, Connection};
use serde_json::{Map, Value};

use super::intent::TimeWindow;
use crate::bm25::scoring::{build_node_scope_sql, detect_nodes_v2_schema};
use crate::service::Scope;

const FLOOR_ISO: &str = "0000-01-01T00:00:00";
const CEILING_ISO: &str = "9999-12-31T23:59:59";
const VERSION_SCAN_LIMIT: i64 = 2_000;
const NORMALISED_UPDATED_AT: &str = "substr(replace(n.updated_at, ' ', 'T'), 1, 19)";
const ID_BATCH: usize = 400;

fn nodes_table_exists(conn: &Connection) -> bool {
    conn.query_row(
        "SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type='table' AND name='nodes')",
        [],
        |row| row.get::<_, bool>(0),
    )
    .unwrap_or(false)
}

struct ScopeFilter {
    join: &'static str,
    condition: String,
    params: Vec<SqlValue>,
}

fn scope_filter(conn: &Connection, scope: &Scope) -> Result<ScopeFilter, CoreError> {
    let parts = build_node_scope_sql(detect_nodes_v2_schema(conn)?, "n", scope, true);
    let condition = parts
        .where_clause
        .strip_prefix("WHERE ")
        .map(|clause| format!(" AND ({clause})"))
        .unwrap_or_default();
    Ok(ScopeFilter {
        join: parts.join_clause,
        condition,
        params: parts.params,
    })
}

pub(super) fn window_node_ids(
    conn: &Connection,
    window: &TimeWindow,
    scope: &Scope,
) -> Result<HashSet<String>, CoreError> {
    if !nodes_table_exists(conn) {
        return Ok(HashSet::new());
    }
    let since = window.since_iso().unwrap_or_else(|| FLOOR_ISO.to_string());
    let until = window
        .until_iso()
        .unwrap_or_else(|| CEILING_ISO.to_string());
    let filter = scope_filter(conn, scope)?;
    let sql = format!(
        "SELECT n.id FROM nodes n {}
         WHERE COALESCE(n.updated_at, '') != ''
           AND {NORMALISED_UPDATED_AT} >= ?
           AND {NORMALISED_UPDATED_AT} < ?{}",
        filter.join, filter.condition
    );
    let mut bound = vec![SqlValue::Text(since), SqlValue::Text(until)];
    bound.extend(filter.params);
    let mut stmt = conn.prepare(&sql)?;
    let rows = stmt.query_map(params_from_iter(bound), |row| row.get::<_, String>(0))?;
    let mut ids = HashSet::new();
    for row in rows {
        ids.insert(row?);
    }
    Ok(ids)
}

fn version_pattern(version: &str) -> Option<Regex> {
    let escaped = version.replace('.', r"\.");
    Regex::new(&format!(r"(?i)(?:\bv|\bversion\s*)(?:{escaped})(?![\d])")).ok()
}

pub(super) fn version_anchor(
    conn: &Connection,
    version: &str,
    scope: &Scope,
) -> Result<Option<(f64, String)>, CoreError> {
    let Some(pattern) = version_pattern(version) else {
        return Ok(None);
    };
    if !nodes_table_exists(conn) {
        return Ok(None);
    }
    let like = format!("%{}%", version.to_lowercase());
    let filter = scope_filter(conn, scope)?;
    let sql = format!(
        "SELECT n.id, COALESCE(n.title, ''), COALESCE(n.summary, ''),
                COALESCE(NULLIF(n.created_at, ''), n.updated_at)
         FROM nodes n {}
         WHERE (lower(COALESCE(n.title, '')) LIKE ? OR lower(COALESCE(n.summary, '')) LIKE ?){}
         ORDER BY 4 ASC, n.id ASC
         LIMIT ?",
        filter.join, filter.condition
    );
    let mut bound = vec![SqlValue::Text(like.clone()), SqlValue::Text(like)];
    bound.extend(filter.params);
    bound.push(SqlValue::Integer(VERSION_SCAN_LIMIT));
    let mut stmt = conn.prepare(&sql)?;
    let rows = stmt.query_map(params_from_iter(bound), |row| {
        Ok((
            row.get::<_, String>(0)?,
            row.get::<_, String>(1)?,
            row.get::<_, String>(2)?,
            row.get::<_, Option<String>>(3)?,
        ))
    })?;
    for row in rows {
        let (id, title, summary, stamp) = row?;
        let mentions = pattern.is_match(&title).unwrap_or(false)
            || pattern.is_match(&summary).unwrap_or(false);
        if !mentions {
            continue;
        }
        if let Some(epoch) = parse_iso(stamp.as_deref()) {
            return Ok(Some((epoch, id)));
        }
    }
    Ok(None)
}

pub fn instant(raw: Option<&str>) -> Option<f64> {
    let text = raw?.trim();
    parse_iso(Some(text))
        .or_else(|| parse_iso(Some(text.trim_end_matches(['Z', 'z']))))
        .or_else(|| text.get(..19).and_then(|head| parse_iso(Some(head))))
        .or_else(|| text.get(..10).and_then(|head| parse_iso(Some(head))))
}

#[derive(Debug, Clone, Copy, Default, PartialEq)]
pub struct Validity {
    pub from: Option<f64>,
    pub until: Option<f64>,
}

fn meta_stamp(meta: &Map<String, Value>, keys: &[&str]) -> Option<f64> {
    let block = meta.get("temporal").and_then(Value::as_object);
    keys.iter().find_map(|key| {
        block
            .and_then(|temporal| temporal.get(*key))
            .or_else(|| meta.get(*key))
            .and_then(|value| instant(value.as_str()))
    })
}

fn v2_has(conn: &Connection, column: &str) -> bool {
    conn.prepare("SELECT name FROM pragma_table_info('nodes_v2') WHERE name = ?1")
        .and_then(|mut stmt| stmt.exists([column]))
        .unwrap_or(false)
}

pub(super) fn explicit_validity(
    conn: &Connection,
    ids: &[String],
) -> Result<HashMap<String, Validity>, CoreError> {
    let unique: Vec<&String> = ids
        .iter()
        .filter(|id| !id.is_empty())
        .collect::<HashSet<_>>()
        .into_iter()
        .collect();
    if unique.is_empty() || !nodes_table_exists(conn) {
        return Ok(HashMap::new());
    }
    let column = |name: &str| {
        if v2_has(conn, name) {
            format!("v2.{name}")
        } else {
            "NULL".to_string()
        }
    };
    let (from_col, to_col) = (column("valid_from"), column("valid_to"));
    let join = if from_col == "NULL" && to_col == "NULL" {
        ""
    } else {
        "LEFT JOIN nodes_v2 v2 ON v2.id = n.id"
    };
    let mut out = HashMap::new();
    for batch in unique.chunks(ID_BATCH) {
        let sql = format!(
            "SELECT n.id, n.metadata_json, {from_col}, {to_col} FROM nodes n {join} WHERE n.id IN ({})",
            vec!["?"; batch.len()].join(",")
        );
        let mut stmt = conn.prepare(&sql)?;
        let rows = stmt.query_map(params_from_iter(batch.iter()), |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, Option<String>>(1)?,
                row.get::<_, Option<String>>(2)?,
                row.get::<_, Option<String>>(3)?,
            ))
        })?;
        for row in rows {
            let (id, raw_meta, from, until) = row?;
            let meta = safe_loads(raw_meta.as_deref());
            out.insert(
                id,
                Validity {
                    from: instant(from.as_deref())
                        .or_else(|| meta_stamp(&meta, &["valid_from", "effective_from"])),
                    until: instant(until.as_deref()).or_else(|| {
                        meta_stamp(&meta, &["valid_until", "valid_to", "effective_until"])
                    }),
                },
            );
        }
    }
    Ok(out)
}
