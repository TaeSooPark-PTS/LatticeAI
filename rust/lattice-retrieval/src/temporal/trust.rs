use std::collections::HashMap;

use lattice_core::correction_signals::CorrectionSignals;
use lattice_core::graph_write::frontmatter::FRONTMATTER_FIELD;
use lattice_core::{safe_loads, CoreError};
use rusqlite::{params_from_iter, Connection};
use serde_json::{json, Map, Value};

const ID_BATCH: usize = 400;
const VERIFIED_VALUES: [&str; 6] = [
    "verified",
    "confirmed",
    "approved",
    "final",
    "published",
    "trusted",
];
const DRAFT_VALUES: [&str; 7] = [
    "draft",
    "unverified",
    "wip",
    "proposed",
    "tentative",
    "untrusted",
    "unreviewed",
];
const USER_TRUST_KEYS: [&str; 2] = ["trust", "user_trust"];
const STATUS_KEYS: [&str; 5] = [
    "status",
    "trust",
    "verified",
    "review_status",
    "verification",
];
const METADATA_STATUS_KEYS: [&str; 2] = ["status", "review_status"];
const DRAFT_ORIGINS: [&str; 3] = ["draft", "agent_draft", "agent-draft"];

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Default)]
pub enum TrustTier {
    Verified,
    #[default]
    Normal,
    Draft,
}

impl TrustTier {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Verified => "verified",
            Self::Normal => "normal",
            Self::Draft => "draft",
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct TrustAssessment {
    pub tier: TrustTier,
    pub signal: &'static str,
    pub value: String,
}

impl TrustAssessment {
    fn new(tier: TrustTier, signal: &'static str, value: impl Into<String>) -> Self {
        Self {
            tier,
            signal,
            value: value.into(),
        }
    }

    pub fn as_json(&self) -> Value {
        json!({"tier": self.tier.as_str(), "signal": self.signal, "value": self.value})
    }
}

pub fn classify(raw: &Value) -> Option<(TrustTier, String)> {
    match raw {
        Value::Bool(true) => Some((TrustTier::Verified, "true".into())),
        Value::Bool(false) => Some((TrustTier::Draft, "false".into())),
        Value::String(text) => {
            let value = text.trim().to_ascii_lowercase();
            if VERIFIED_VALUES.contains(&value.as_str()) {
                Some((TrustTier::Verified, value))
            } else if DRAFT_VALUES.contains(&value.as_str()) {
                Some((TrustTier::Draft, value))
            } else {
                None
            }
        }
        _ => None,
    }
}

fn first_of(
    meta: &Map<String, Value>,
    keys: &[&str],
    signal: &'static str,
) -> Option<TrustAssessment> {
    keys.iter()
        .filter_map(|key| meta.get(*key))
        .find_map(classify)
        .map(|(tier, value)| TrustAssessment::new(tier, signal, value))
}

fn lifecycle_signal(meta: &Map<String, Value>) -> Option<TrustAssessment> {
    let lifecycle = meta.get("lifecycle")?.as_object()?;
    let state = lifecycle
        .get("state")
        .and_then(Value::as_str)
        .unwrap_or("active");
    if state == "pending_verification" {
        return Some(TrustAssessment::new(TrustTier::Draft, "lifecycle", state));
    }
    let approver = lifecycle
        .get("approver")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty());
    (state == "active" && approver.is_some())
        .then(|| TrustAssessment::new(TrustTier::Verified, "lifecycle", "approved"))
}

pub fn assess_metadata(meta: &Map<String, Value>) -> Option<TrustAssessment> {
    first_of(meta, &USER_TRUST_KEYS, "user_trust")
        .or_else(|| {
            meta.get(FRONTMATTER_FIELD)
                .and_then(Value::as_object)
                .and_then(|block| first_of(block, &STATUS_KEYS, "frontmatter"))
        })
        .or_else(|| first_of(meta, &METADATA_STATUS_KEYS, "metadata_status"))
        .or_else(|| lifecycle_signal(meta))
}

fn assess_provenance(
    source_type: &str,
    pipeline: &str,
    meta: &Map<String, Value>,
) -> Option<TrustAssessment> {
    first_of(meta, &STATUS_KEYS, "provenance").or_else(|| {
        [source_type, pipeline]
            .into_iter()
            .map(|origin| origin.trim().to_ascii_lowercase())
            .find(|origin| DRAFT_ORIGINS.contains(&origin.as_str()))
            .map(|origin| TrustAssessment::new(TrustTier::Draft, "provenance", origin))
    })
}

fn table_columns(
    conn: &Connection,
    table: &str,
) -> Result<std::collections::HashSet<String>, CoreError> {
    let mut stmt = conn.prepare(&format!("PRAGMA table_info({table})"))?;
    let mut cols = std::collections::HashSet::new();
    for row in stmt.query_map([], |row| row.get::<_, String>(1))? {
        cols.insert(row?);
    }
    Ok(cols)
}

fn table_exists(conn: &Connection, table: &str) -> Result<bool, CoreError> {
    Ok(conn.query_row(
        "SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type='table' AND name=?1)",
        [table],
        |row| row.get::<_, bool>(0),
    )?)
}

fn in_batches<T>(
    conn: &Connection,
    ids: &[String],
    sql: impl Fn(&str) -> String,
    row: impl Fn(&rusqlite::Row<'_>) -> rusqlite::Result<(String, T)>,
) -> Result<Vec<(String, T)>, CoreError> {
    let mut out = Vec::new();
    for batch in ids.chunks(ID_BATCH) {
        let marks = vec!["?"; batch.len()].join(",");
        let mut stmt = conn.prepare(&sql(&marks))?;
        for item in stmt.query_map(params_from_iter(batch.iter()), &row)? {
            out.push(item?);
        }
    }
    Ok(out)
}

pub fn node_trust(
    conn: &Connection,
    ids: &[String],
    corrections: &CorrectionSignals,
) -> Result<HashMap<String, TrustAssessment>, CoreError> {
    let mut metadata: HashMap<String, TrustAssessment> = HashMap::new();
    if !ids.is_empty() && table_exists(conn, "nodes")? {
        let rows = in_batches(
            conn,
            ids,
            |marks| format!("SELECT id, metadata_json FROM nodes WHERE id IN ({marks})"),
            |row| Ok((row.get(0)?, row.get::<_, Option<String>>(1)?)),
        )?;
        for (id, raw) in rows {
            if let Some(found) = assess_metadata(&safe_loads(raw.as_deref())) {
                metadata.insert(id, found);
            }
        }
    }
    let mut provenance: HashMap<String, TrustAssessment> = HashMap::new();
    if !ids.is_empty() && table_exists(conn, "ingestion_provenance")? {
        let cols = table_columns(conn, "ingestion_provenance")?;
        if cols.contains("node_id") {
            let source_expr = if cols.contains("source_type") {
                "source_type"
            } else {
                "''"
            };
            let pipeline_expr = if cols.contains("pipeline") {
                "pipeline"
            } else {
                "''"
            };
            let meta_expr = if cols.contains("metadata_json") {
                "metadata_json"
            } else {
                "NULL"
            };
            let order_expr = if cols.contains("created_at") {
                "created_at, rowid"
            } else {
                "rowid"
            };
            let rows = in_batches(
                conn,
                ids,
                |marks| {
                    format!(
                        "SELECT node_id, {source_expr}, {pipeline_expr}, {meta_expr} \
                         FROM ingestion_provenance WHERE node_id IN ({marks}) ORDER BY {order_expr}"
                    )
                },
                |row| {
                    Ok((
                        row.get(0)?,
                        (
                            row.get::<_, Option<String>>(1)?.unwrap_or_default(),
                            row.get::<_, Option<String>>(2)?.unwrap_or_default(),
                            row.get::<_, Option<String>>(3)?,
                        ),
                    ))
                },
            )?;
            for (id, (source_type, pipeline, raw)) in rows {
                match assess_provenance(&source_type, &pipeline, &safe_loads(raw.as_deref())) {
                    Some(found) => provenance.insert(id, found),
                    None => provenance.remove(&id),
                };
            }
        }
    }
    let mut out = HashMap::new();
    for id in ids {
        let user = metadata
            .get(id)
            .filter(|found| found.signal == "user_trust")
            .cloned();
        let confirmed = corrections.confirmed.get(id).map(|record| {
            TrustAssessment::new(
                TrustTier::Verified,
                "confirmed_correction",
                record.correction_id.clone(),
            )
        });
        let chosen = user
            .or(confirmed)
            .or_else(|| metadata.get(id).cloned())
            .or_else(|| provenance.get(id).cloned());
        if let Some(found) = chosen {
            out.insert(id.clone(), found);
        }
    }
    Ok(out)
}

#[cfg(test)]
#[path = "trust_tests.rs"]
mod tests;
