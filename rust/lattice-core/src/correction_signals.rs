use std::collections::{BTreeSet, HashMap};

use rusqlite::{params_from_iter, Connection};
use serde::Serialize;
use serde_json::Value;

use crate::db::CoreError;
use crate::learning_loop::{node_effective_workspace, CorrectionTargetKind};

pub const FEEDBACK_FIELD: &str = "feedback";
const ID_BATCH: usize = 400;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum CitationVerdict {
    WrongSource,
    Useful,
}

impl CitationVerdict {
    pub fn parse(raw: &str) -> Option<Self> {
        let normalized = raw.trim().to_ascii_lowercase().replace(['-', ' '], "_");
        match normalized.as_str() {
            "wrong_source" | "not_a_good_source" | "bad_source" | "not_useful" => {
                Some(Self::WrongSource)
            }
            "useful" | "good_source" | "helpful" => Some(Self::Useful),
            _ => None,
        }
    }

    pub fn as_str(self) -> &'static str {
        match self {
            Self::WrongSource => "wrong_source",
            Self::Useful => "useful",
        }
    }

    pub fn from_snapshot(after: &Value) -> Option<Self> {
        after
            .get(FEEDBACK_FIELD)
            .and_then(Value::as_str)
            .and_then(Self::parse)
    }
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct ActiveCorrection {
    pub correction_id: String,
    pub workspace_id: String,
    pub target_kind: CorrectionTargetKind,
    pub target_id: String,
    pub verdict: Option<CitationVerdict>,
    pub updated_at: String,
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct CorrectionSignals {
    pub citation: HashMap<String, ActiveCorrection>,
    pub confirmed: HashMap<String, ActiveCorrection>,
}

impl CorrectionSignals {
    pub fn is_empty(&self) -> bool {
        self.citation.is_empty() && self.confirmed.is_empty()
    }
}

fn corrections_table_exists(conn: &Connection) -> Result<bool, CoreError> {
    Ok(conn.query_row(
        "SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type='table' AND name='knowledge_corrections')",
        [],
        |row| row.get::<_, bool>(0),
    )?)
}

type Row = (String, String, String, String, String, String);

fn read_rows(conn: &Connection, ids: &[String]) -> Result<Vec<Row>, CoreError> {
    let mut rows_out = Vec::new();
    for batch in ids.chunks(ID_BATCH) {
        let sql = format!(
            "SELECT correction_id, workspace_id, target_kind, target_id, after_json, updated_at
             FROM knowledge_corrections
             WHERE status = 'confirmed' AND reverted_at IS NULL
               AND target_kind IN ('citation', 'fact', 'entity_link')
               AND target_id IN ({})
             ORDER BY updated_at, rowid",
            vec!["?"; batch.len()].join(",")
        );
        let mut stmt = conn.prepare(&sql)?;
        let rows = stmt.query_map(params_from_iter(batch.iter()), |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, String>(3)?,
                row.get::<_, String>(4)?,
                row.get::<_, String>(5)?,
            ))
        })?;
        for row in rows {
            rows_out.push(row?);
        }
    }
    Ok(rows_out)
}

pub fn active_correction_signals(
    conn: &Connection,
    node_ids: &[String],
    allowed_workspaces: Option<&BTreeSet<String>>,
) -> Result<CorrectionSignals, CoreError> {
    let unique: Vec<String> = node_ids
        .iter()
        .map(|id| id.trim().to_string())
        .filter(|id| !id.is_empty())
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect();
    let mut signals = CorrectionSignals::default();
    if unique.is_empty() || !corrections_table_exists(conn)? {
        return Ok(signals);
    }
    let mut owners: HashMap<String, Option<String>> = HashMap::new();
    for (correction_id, workspace_id, kind_raw, target_id, after_raw, updated_at) in
        read_rows(conn, &unique)?
    {
        let workspace = workspace_id.trim().to_string();
        if allowed_workspaces.is_some_and(|allowed| !allowed.contains(&workspace)) {
            continue;
        }
        let owner = match owners.get(&target_id) {
            Some(found) => found.clone(),
            None => {
                let found = node_effective_workspace(conn, &target_id)?;
                owners.insert(target_id.clone(), found.clone());
                found
            }
        };
        if owner.as_deref() != Some(workspace.as_str()) {
            continue;
        }
        let Ok(target_kind) = CorrectionTargetKind::parse(&kind_raw) else {
            continue;
        };
        let after: Value = serde_json::from_str(&after_raw).unwrap_or(Value::Null);
        let verdict = CitationVerdict::from_snapshot(&after);
        let record = ActiveCorrection {
            correction_id,
            workspace_id: workspace,
            target_kind,
            target_id: target_id.clone(),
            verdict,
            updated_at,
        };
        match target_kind {
            CorrectionTargetKind::Citation if verdict.is_some() => {
                signals.citation.insert(target_id, record);
            }
            CorrectionTargetKind::Fact | CorrectionTargetKind::EntityLink => {
                signals.confirmed.insert(target_id, record);
            }
            _ => {}
        }
    }
    Ok(signals)
}

#[cfg(test)]
#[path = "correction_signals_tests.rs"]
mod tests;
