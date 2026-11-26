use std::collections::HashSet;

use fancy_regex::Regex;
use lattice_core::{parse_iso, CoreError};
use rusqlite::{params, Connection};

use super::intent::TimeWindow;

const FLOOR_ISO: &str = "0000-01-01T00:00:00";
const CEILING_ISO: &str = "9999-12-31T23:59:59";
const VERSION_SCAN_LIMIT: i64 = 2_000;
const NORMALISED_UPDATED_AT: &str = "substr(replace(updated_at, ' ', 'T'), 1, 19)";

fn nodes_table_exists(conn: &Connection) -> bool {
    conn.query_row(
        "SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type='table' AND name='nodes')",
        [],
        |row| row.get::<_, bool>(0),
    )
    .unwrap_or(false)
}

pub(super) fn window_node_ids(
    conn: &Connection,
    window: &TimeWindow,
) -> Result<HashSet<String>, CoreError> {
    if !nodes_table_exists(conn) {
        return Ok(HashSet::new());
    }
    let since = window.since_iso().unwrap_or_else(|| FLOOR_ISO.to_string());
    let until = window
        .until_iso()
        .unwrap_or_else(|| CEILING_ISO.to_string());
    let sql = format!(
        "SELECT id FROM nodes
         WHERE COALESCE(updated_at, '') != ''
           AND {NORMALISED_UPDATED_AT} >= ?1
           AND {NORMALISED_UPDATED_AT} < ?2"
    );
    let mut stmt = conn.prepare(&sql)?;
    let rows = stmt.query_map(params![since, until], |row| row.get::<_, String>(0))?;
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
) -> Result<Option<(f64, String)>, CoreError> {
    let Some(pattern) = version_pattern(version) else {
        return Ok(None);
    };
    if !nodes_table_exists(conn) {
        return Ok(None);
    }
    let like = format!("%{}%", version.to_lowercase());
    let mut stmt = conn.prepare(
        "SELECT id, COALESCE(title, ''), COALESCE(summary, ''),
                COALESCE(NULLIF(created_at, ''), updated_at)
         FROM nodes
         WHERE lower(COALESCE(title, '')) LIKE ?1 OR lower(COALESCE(summary, '')) LIKE ?1
         ORDER BY 4 ASC, id ASC
         LIMIT ?2",
    )?;
    let rows = stmt.query_map(params![like, VERSION_SCAN_LIMIT], |row| {
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
