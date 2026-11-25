use rusqlite::{params, Connection, OptionalExtension};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use crate::db::CoreError;
use crate::graph_write::clock::{Clock, SystemClock};
use crate::graph_write::pyaux::sha256_text;

fn utc_now_iso() -> String {
    SystemClock.now_iso()
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum CorrectionTargetKind {
    EntityLink,
    Fact,
    Relationship,
    Citation,
}

impl CorrectionTargetKind {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::EntityLink => "entity_link",
            Self::Fact => "fact",
            Self::Relationship => "relationship",
            Self::Citation => "citation",
        }
    }

    pub fn parse(raw: &str) -> Result<Self, CoreError> {
        match raw.trim().to_ascii_lowercase().as_str() {
            "entity_link" => Ok(Self::EntityLink),
            "fact" => Ok(Self::Fact),
            "relationship" => Ok(Self::Relationship),
            "citation" => Ok(Self::Citation),
            other => Err(CoreError::InvalidRequest(format!(
                "unsupported correction target kind: {other}"
            ))),
        }
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum CorrectionStatus {
    Pending,
    Confirmed,
    Rejected,
}

impl CorrectionStatus {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Pending => "pending",
            Self::Confirmed => "confirmed",
            Self::Rejected => "rejected",
        }
    }

    pub fn parse(raw: &str) -> Result<Self, CoreError> {
        match raw.trim().to_ascii_lowercase().as_str() {
            "pending" => Ok(Self::Pending),
            "confirmed" => Ok(Self::Confirmed),
            "rejected" => Ok(Self::Rejected),
            other => Err(CoreError::InvalidRequest(format!(
                "unsupported correction status: {other}"
            ))),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RecordCorrectionRequest {
    pub workspace_id: String,
    pub target_kind: CorrectionTargetKind,
    pub target_id: String,
    pub status: CorrectionStatus,
    pub actor: String,
    pub reason: String,
    pub before_snapshot: Value,
    pub after_snapshot: Value,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct KnowledgeCorrectionRecord {
    pub correction_id: String,
    pub workspace_id: String,
    pub target_kind: CorrectionTargetKind,
    pub target_id: String,
    pub status: CorrectionStatus,
    pub actor: String,
    pub reason: String,
    pub before_snapshot: Value,
    pub after_snapshot: Value,
    pub reindex_triggered: bool,
    pub local_only: bool,
    pub external_training_upload_allowed: bool,
    pub created_at: String,
    pub updated_at: String,
    pub reverted_at: Option<String>,
}

pub fn ensure_learning_loop_schema(conn: &Connection) -> Result<(), CoreError> {
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS knowledge_corrections (
            correction_id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            target_kind TEXT NOT NULL,
            target_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            actor TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT '',
            before_json TEXT NOT NULL DEFAULT '{}',
            after_json TEXT NOT NULL DEFAULT '{}',
            reindex_triggered INTEGER NOT NULL DEFAULT 0,
            local_only INTEGER NOT NULL DEFAULT 1 CHECK (local_only = 1),
            external_training_upload_allowed INTEGER NOT NULL DEFAULT 0 CHECK (external_training_upload_allowed = 0),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            reverted_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_kc_ws_status ON knowledge_corrections(workspace_id, status);
        CREATE INDEX IF NOT EXISTS idx_kc_target ON knowledge_corrections(target_kind, target_id);",
    )?;
    Ok(())
}

pub fn assert_external_training_upload_forbidden(
    record: &KnowledgeCorrectionRecord,
) -> Result<(), CoreError> {
    if !record.local_only || record.external_training_upload_allowed {
        return Err(CoreError::InvalidRequest(
            "policy violation: user knowledge corrections may never be uploaded for external model training"
                .to_string(),
        ));
    }
    Ok(())
}

fn node_effective_workspace(conn: &Connection, node_id: &str) -> Result<Option<String>, CoreError> {
    let has_v2: bool = conn
        .query_row(
            "SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type='table' AND name='nodes_v2')",
            [],
            |r| r.get(0),
        )
        .unwrap_or(false);
    let row: Option<(Option<String>, Option<String>)> = if has_v2 {
        conn.query_row(
            "SELECT v.workspace_id, json_extract(n.metadata_json, '$.workspace_id')
             FROM nodes n LEFT JOIN nodes_v2 v ON v.id = n.id
             WHERE n.id = ?1",
            params![node_id],
            |r| Ok((r.get(0)?, r.get(1)?)),
        )
        .optional()?
    } else {
        conn.query_row(
            "SELECT NULL, json_extract(n.metadata_json, '$.workspace_id')
             FROM nodes n
             WHERE n.id = ?1",
            params![node_id],
            |r| Ok((r.get(0)?, r.get(1)?)),
        )
        .optional()?
    };
    Ok(row.map(|(v_ws, m_ws)| {
        v_ws.or(m_ws)
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty())
            .unwrap_or_else(|| "personal".to_string())
    }))
}

pub fn verify_correction_target_workspace(
    conn: &Connection,
    workspace_id: &str,
    kind: CorrectionTargetKind,
    target_id: &str,
) -> Result<(), CoreError> {
    let ws = workspace_id.trim();
    let tid = target_id.trim();
    if tid.is_empty() {
        return Err(CoreError::InvalidRequest(
            "target_id is required".to_string(),
        ));
    }
    match kind {
        CorrectionTargetKind::Fact
        | CorrectionTargetKind::EntityLink
        | CorrectionTargetKind::Citation => {
            let Some(owner_ws) = node_effective_workspace(conn, tid)? else {
                return Err(CoreError::InvalidRequest(format!(
                    "target node '{tid}' not found in workspace '{ws}'"
                )));
            };
            if owner_ws != ws {
                return Err(CoreError::InvalidRequest(format!(
                    "cross-workspace mutation forbidden: target node '{tid}' does not belong to workspace '{ws}'"
                )));
            }
            Ok(())
        }
        CorrectionTargetKind::Relationship => {
            let endpoints: Option<(String, String)> = conn
                .query_row(
                    "SELECT source, target FROM edges_v2 WHERE id = ?1",
                    params![tid],
                    |r| Ok((r.get(0)?, r.get(1)?)),
                )
                .optional()
                .ok()
                .flatten()
                .or_else(|| {
                    conn.query_row(
                        "SELECT from_node, to_node FROM edges WHERE id = ?1",
                        params![tid],
                        |r| Ok((r.get(0)?, r.get(1)?)),
                    )
                    .optional()
                    .ok()
                    .flatten()
                });
            let Some((src, dst)) = endpoints else {
                return Err(CoreError::InvalidRequest(format!(
                    "target edge '{tid}' not found in workspace '{ws}'"
                )));
            };
            let src_ws = node_effective_workspace(conn, &src)?;
            let dst_ws = node_effective_workspace(conn, &dst)?;
            if src_ws.as_deref() != Some(ws) || dst_ws.as_deref() != Some(ws) {
                return Err(CoreError::InvalidRequest(format!(
                    "cross-workspace mutation forbidden: target edge '{tid}' does not belong to workspace '{ws}'"
                )));
            }
            Ok(())
        }
    }
}

pub fn record_knowledge_correction(
    conn: &mut Connection,
    req: &RecordCorrectionRequest,
) -> Result<KnowledgeCorrectionRecord, CoreError> {
    ensure_learning_loop_schema(conn)?;
    let now = utc_now_iso();
    let ws = if req.workspace_id.trim().is_empty() {
        "personal".to_string()
    } else {
        req.workspace_id.trim().to_string()
    };
    verify_correction_target_workspace(conn, &ws, req.target_kind, &req.target_id)?;
    let correction_id = format!(
        "corr:{}",
        &sha256_text(&format!(
            "{}|{}|{}|{}|{}",
            ws,
            req.target_kind.as_str(),
            req.target_id,
            req.actor,
            now
        ))[..20]
    );
    let before_json = serde_json::to_string(&req.before_snapshot).unwrap_or_else(|_| "{}".into());
    let after_json = serde_json::to_string(&req.after_snapshot).unwrap_or_else(|_| "{}".into());

    let tx = conn.transaction()?;
    let mut reindex_triggered = false;

    if req.status == CorrectionStatus::Confirmed {
        reindex_triggered = apply_confirmed_correction_and_selective_reindex(
            &tx,
            req.target_kind,
            &req.target_id,
            &req.after_snapshot,
            &correction_id,
            &now,
        )?;
    }

    tx.execute(
        "INSERT INTO knowledge_corrections(
            correction_id, workspace_id, target_kind, target_id, status, actor, reason,
            before_json, after_json, reindex_triggered, local_only,
            external_training_upload_allowed, created_at, updated_at
         ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, 1, 0, ?11, ?11)",
        params![
            correction_id,
            ws,
            req.target_kind.as_str(),
            req.target_id,
            req.status.as_str(),
            req.actor,
            req.reason,
            before_json,
            after_json,
            if reindex_triggered { 1 } else { 0 },
            now,
        ],
    )?;
    tx.commit()?;

    load_correction(conn, &correction_id)?
        .ok_or_else(|| CoreError::InvalidRequest("correction missing after insert".to_string()))
}

fn apply_confirmed_correction_and_selective_reindex(
    tx: &rusqlite::Transaction<'_>,
    kind: CorrectionTargetKind,
    target_id: &str,
    after: &Value,
    correction_id: &str,
    now: &str,
) -> Result<bool, CoreError> {
    match kind {
        CorrectionTargetKind::Fact | CorrectionTargetKind::EntityLink => {
            let existing: Option<(String, String, String)> = tx
                .query_row(
                    "SELECT title, summary, metadata_json FROM nodes WHERE id = ?1",
                    params![target_id],
                    |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
                )
                .optional()?;
            if let Some((old_title, old_summary, old_meta_raw)) = existing {
                let new_title = after
                    .get("title")
                    .and_then(Value::as_str)
                    .unwrap_or(&old_title);
                let new_summary = after
                    .get("summary")
                    .and_then(Value::as_str)
                    .unwrap_or(&old_summary);
                let mut meta: Value =
                    serde_json::from_str(&old_meta_raw).unwrap_or_else(|_| json!({}));
                if let Some(obj) = meta.as_object_mut() {
                    obj.insert("verification_status".to_string(), json!("confirmed"));
                    obj.insert("last_correction_id".to_string(), json!(correction_id));
                    obj.insert("last_corrected_at".to_string(), json!(now));
                }
                let meta_str = serde_json::to_string(&meta).unwrap_or(old_meta_raw);
                tx.execute(
                    "UPDATE nodes SET title = ?2, summary = ?3, metadata_json = ?4 WHERE id = ?1",
                    params![target_id, new_title, new_summary, meta_str],
                )?;
                let _ = tx.execute(
                    "UPDATE nodes_v2 SET label = ?2, summary = ?3, attrs = ?4, updated_at = ?5 WHERE id = ?1",
                    params![target_id, new_title, new_summary, meta_str, now],
                );
                let _ = tx.execute(
                    "DELETE FROM node_fts WHERE node_id = ?1",
                    params![target_id],
                );
                let _ = tx.execute(
                    "INSERT INTO node_fts(node_id, title, summary) VALUES (?1, ?2, ?3)",
                    params![target_id, new_title, new_summary],
                );
                return Ok(true);
            }
            Ok(false)
        }
        CorrectionTargetKind::Relationship => {
            let new_conf = after.get("confidence").and_then(Value::as_f64);
            let new_status = after
                .get("relation_status")
                .and_then(Value::as_str)
                .unwrap_or("user_verified");
            let updated = tx.execute(
                "UPDATE edges_v2
                 SET confidence = COALESCE(?2, confidence),
                     metadata = json_set(COALESCE(NULLIF(metadata, ''), '{}'), '$.relation_status', ?3, '$.last_correction_id', ?4)
                 WHERE id = ?1",
                params![target_id, new_conf, new_status, correction_id],
            )?;
            Ok(updated > 0)
        }
        CorrectionTargetKind::Citation => Ok(false),
    }
}

pub fn update_correction_status(
    conn: &mut Connection,
    correction_id: &str,
    new_status: CorrectionStatus,
) -> Result<KnowledgeCorrectionRecord, CoreError> {
    update_correction_status_inner(conn, None, correction_id, new_status)
}

pub fn update_correction_status_in_workspace(
    conn: &mut Connection,
    workspace_id: &str,
    correction_id: &str,
    new_status: CorrectionStatus,
) -> Result<KnowledgeCorrectionRecord, CoreError> {
    let ws = workspace_id.trim();
    if ws.is_empty() {
        return Err(CoreError::InvalidRequest(
            "workspace_id is required".to_string(),
        ));
    }
    update_correction_status_inner(conn, Some(ws), correction_id, new_status)
}

fn update_correction_status_inner(
    conn: &mut Connection,
    expected_workspace: Option<&str>,
    correction_id: &str,
    new_status: CorrectionStatus,
) -> Result<KnowledgeCorrectionRecord, CoreError> {
    let existing = load_correction(conn, correction_id)?
        .ok_or_else(|| CoreError::InvalidRequest(format!("unknown correction: {correction_id}")))?;
    if let Some(ws) = expected_workspace {
        if existing.workspace_id.trim() != ws {
            return Err(CoreError::InvalidRequest(format!(
                "cross-workspace mutation forbidden: correction '{correction_id}' does not belong to workspace '{ws}'"
            )));
        }
        verify_correction_target_workspace(conn, ws, existing.target_kind, &existing.target_id)?;
    }
    let now = utc_now_iso();
    let tx = conn.transaction()?;
    let mut reindex = existing.reindex_triggered;
    if new_status == CorrectionStatus::Confirmed && existing.status != CorrectionStatus::Confirmed {
        reindex = apply_confirmed_correction_and_selective_reindex(
            &tx,
            existing.target_kind,
            &existing.target_id,
            &existing.after_snapshot,
            correction_id,
            &now,
        )?;
    }
    tx.execute(
        "UPDATE knowledge_corrections
         SET status = ?2, reindex_triggered = ?3, updated_at = ?4
         WHERE correction_id = ?1",
        params![
            correction_id,
            new_status.as_str(),
            if reindex { 1 } else { 0 },
            now
        ],
    )?;
    tx.commit()?;
    load_correction(conn, correction_id)?
        .ok_or_else(|| CoreError::InvalidRequest("correction missing after update".to_string()))
}

pub fn revert_knowledge_correction(
    conn: &mut Connection,
    correction_id: &str,
) -> Result<KnowledgeCorrectionRecord, CoreError> {
    revert_knowledge_correction_inner(conn, None, correction_id)
}

pub fn revert_knowledge_correction_in_workspace(
    conn: &mut Connection,
    workspace_id: &str,
    correction_id: &str,
) -> Result<KnowledgeCorrectionRecord, CoreError> {
    let ws = workspace_id.trim();
    if ws.is_empty() {
        return Err(CoreError::InvalidRequest(
            "workspace_id is required".to_string(),
        ));
    }
    revert_knowledge_correction_inner(conn, Some(ws), correction_id)
}

fn revert_knowledge_correction_inner(
    conn: &mut Connection,
    expected_workspace: Option<&str>,
    correction_id: &str,
) -> Result<KnowledgeCorrectionRecord, CoreError> {
    let existing = load_correction(conn, correction_id)?
        .ok_or_else(|| CoreError::InvalidRequest(format!("unknown correction: {correction_id}")))?;
    if let Some(ws) = expected_workspace {
        if existing.workspace_id.trim() != ws {
            return Err(CoreError::InvalidRequest(format!(
                "cross-workspace mutation forbidden: correction '{correction_id}' does not belong to workspace '{ws}'"
            )));
        }
        verify_correction_target_workspace(conn, ws, existing.target_kind, &existing.target_id)?;
    }
    let now = utc_now_iso();
    let tx = conn.transaction()?;
    let _ = apply_confirmed_correction_and_selective_reindex(
        &tx,
        existing.target_kind,
        &existing.target_id,
        &existing.before_snapshot,
        correction_id,
        &now,
    )?;
    tx.execute(
        "UPDATE knowledge_corrections
         SET status = 'rejected', reverted_at = ?2, updated_at = ?2
         WHERE correction_id = ?1",
        params![correction_id, now],
    )?;
    tx.commit()?;
    load_correction(conn, correction_id)?
        .ok_or_else(|| CoreError::InvalidRequest("correction missing after revert".to_string()))
}

pub fn load_correction(
    conn: &Connection,
    correction_id: &str,
) -> Result<Option<KnowledgeCorrectionRecord>, CoreError> {
    ensure_learning_loop_schema(conn)?;
    conn.query_row(
        "SELECT correction_id, workspace_id, target_kind, target_id, status, actor, reason,
                before_json, after_json, reindex_triggered, local_only,
                external_training_upload_allowed, created_at, updated_at, reverted_at
         FROM knowledge_corrections
         WHERE correction_id = ?1",
        params![correction_id],
        row_to_correction,
    )
    .optional()
    .map_err(Into::into)
}

pub fn list_corrections(
    conn: &Connection,
    workspace_id: &str,
    status_filter: Option<CorrectionStatus>,
) -> Result<Vec<KnowledgeCorrectionRecord>, CoreError> {
    ensure_learning_loop_schema(conn)?;
    let mut rows_out = Vec::new();
    if let Some(st) = status_filter {
        let mut stmt = conn.prepare(
            "SELECT correction_id, workspace_id, target_kind, target_id, status, actor, reason,
                    before_json, after_json, reindex_triggered, local_only,
                    external_training_upload_allowed, created_at, updated_at, reverted_at
             FROM knowledge_corrections
             WHERE workspace_id = ?1 AND status = ?2
             ORDER BY created_at DESC",
        )?;
        for rec in stmt.query_map(params![workspace_id, st.as_str()], row_to_correction)? {
            rows_out.push(rec?);
        }
    } else {
        let mut stmt = conn.prepare(
            "SELECT correction_id, workspace_id, target_kind, target_id, status, actor, reason,
                    before_json, after_json, reindex_triggered, local_only,
                    external_training_upload_allowed, created_at, updated_at, reverted_at
             FROM knowledge_corrections
             WHERE workspace_id = ?1
             ORDER BY created_at DESC",
        )?;
        for rec in stmt.query_map(params![workspace_id], row_to_correction)? {
            rows_out.push(rec?);
        }
    }
    Ok(rows_out)
}

fn row_to_correction(r: &rusqlite::Row<'_>) -> rusqlite::Result<KnowledgeCorrectionRecord> {
    let kind_raw: String = r.get(2)?;
    let status_raw: String = r.get(4)?;
    let before_raw: String = r.get(7)?;
    let after_raw: String = r.get(8)?;
    Ok(KnowledgeCorrectionRecord {
        correction_id: r.get(0)?,
        workspace_id: r.get(1)?,
        target_kind: CorrectionTargetKind::parse(&kind_raw).unwrap_or(CorrectionTargetKind::Fact),
        target_id: r.get(3)?,
        status: CorrectionStatus::parse(&status_raw).unwrap_or(CorrectionStatus::Pending),
        actor: r.get(5)?,
        reason: r.get(6)?,
        before_snapshot: serde_json::from_str(&before_raw).unwrap_or_else(|_| json!({})),
        after_snapshot: serde_json::from_str(&after_raw).unwrap_or_else(|_| json!({})),
        reindex_triggered: r.get::<_, i64>(9)? != 0,
        local_only: r.get::<_, i64>(10)? != 0,
        external_training_upload_allowed: r.get::<_, i64>(11)? != 0,
        created_at: r.get(12)?,
        updated_at: r.get(13)?,
        reverted_at: r.get(14)?,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph_write::schema::bootstrap;

    #[test]
    fn corrections_preserve_provenance_trigger_selective_reindex_and_block_external_training() {
        let mut conn = Connection::open_in_memory().unwrap();
        {
            let tx = conn.transaction().unwrap();
            bootstrap(&tx, "2026-01-01T00:00:00").unwrap();
            tx.commit().unwrap();
        }
        conn.execute(
            "INSERT INTO nodes(id, type, title, summary, raw_json, metadata_json, created_at, updated_at)
             VALUES ('fact-1', 'Fact', 'Old Owner', 'Alice leads Project X', '{}',
                     '{\"source_uri\":\"file:///orig.md\",\"workspace_id\":\"ws-1\"}', '2026-01-01', '2026-01-01')",
            [],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO node_fts(node_id, title, summary) VALUES ('fact-1', 'Old Owner', 'Alice leads Project X')",
            [],
        )
        .unwrap();

        let rec = record_knowledge_correction(
            &mut conn,
            &RecordCorrectionRequest {
                workspace_id: "ws-1".to_string(),
                target_kind: CorrectionTargetKind::Fact,
                target_id: "fact-1".to_string(),
                status: CorrectionStatus::Confirmed,
                actor: "user@example.com".to_string(),
                reason: "Updated project lead after Q2 handoff".to_string(),
                before_snapshot: json!({"title": "Old Owner", "summary": "Alice leads Project X"}),
                after_snapshot: json!({"title": "Current Owner", "summary": "Bob leads Project X"}),
            },
        )
        .unwrap();

        assert!(rec.reindex_triggered);
        assert!(rec.local_only);
        assert!(!rec.external_training_upload_allowed);
        assert!(assert_external_training_upload_forbidden(&rec).is_ok());

        let meta_raw: String = conn
            .query_row(
                "SELECT metadata_json FROM nodes WHERE id = 'fact-1'",
                [],
                |r| r.get(0),
            )
            .unwrap();
        let meta: Value = serde_json::from_str(&meta_raw).unwrap();
        assert_eq!(meta["source_uri"], "file:///orig.md");
        assert_eq!(meta["verification_status"], "confirmed");

        let fts_hits: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM node_fts WHERE node_fts MATCH 'Bob'",
                [],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(fts_hits, 1);

        let reverted = revert_knowledge_correction(&mut conn, &rec.correction_id).unwrap();
        assert_eq!(reverted.status, CorrectionStatus::Rejected);
        assert!(reverted.reverted_at.is_some());
        let restored_summary: String = conn
            .query_row("SELECT summary FROM nodes WHERE id = 'fact-1'", [], |r| {
                r.get(0)
            })
            .unwrap();
        assert_eq!(restored_summary, "Alice leads Project X");

        let cross_create = record_knowledge_correction(
            &mut conn,
            &RecordCorrectionRequest {
                workspace_id: "ws-other".to_string(),
                target_kind: CorrectionTargetKind::Fact,
                target_id: "fact-1".to_string(),
                status: CorrectionStatus::Pending,
                actor: "attacker@example.com".to_string(),
                reason: "cross-ws".to_string(),
                before_snapshot: json!({}),
                after_snapshot: json!({"summary": "hacked"}),
            },
        );
        assert!(cross_create.is_err());
        assert!(update_correction_status_in_workspace(
            &mut conn,
            "ws-other",
            &rec.correction_id,
            CorrectionStatus::Confirmed,
        )
        .is_err());
        assert!(revert_knowledge_correction_in_workspace(
            &mut conn,
            "ws-other",
            &rec.correction_id,
        )
        .is_err());
    }
}
