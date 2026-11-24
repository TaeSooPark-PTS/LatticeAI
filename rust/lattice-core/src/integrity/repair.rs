//! Transactional workspace-scoped and global integrity repair planner & executor.

use std::collections::HashSet;
use std::path::Path;

use rusqlite::{params, Connection};

use crate::db::CoreError;
use crate::graph_write::schema::backup_database;

use super::{
    check_graph_integrity_scoped, detect_cycles, table_exists, IntegrityIssueKind, IntegrityReport,
    PlannedRepairAction, RepairBlastRadius, RepairExecutionResult, RepairOptions, RepairPlan,
    RepairSafety, DAG_EDGE_TYPES,
};

fn action_id_for(kind: IntegrityIssueKind) -> &'static str {
    match kind {
        IntegrityIssueKind::DanglingEdge => "delete_dangling_edges",
        IntegrityIssueKind::DanglingEdgeV2 => "delete_dangling_edges_v2",
        IntegrityIssueKind::DanglingEdgeOccurrence => "delete_dangling_edge_occurrences",
        IntegrityIssueKind::OrphanChunk => "delete_orphan_chunks",
        IntegrityIssueKind::OrphanVector => "delete_orphan_vectors",
        IntegrityIssueKind::OrphanProvenance => "delete_orphan_provenance",
        IntegrityIssueKind::IndexDriftV2 => "backfill_nodes_v2",
        IntegrityIssueKind::IndexDriftFts => "backfill_node_fts",
        IntegrityIssueKind::CrossWorkspaceLeak => "sever_cross_workspace_edges",
        IntegrityIssueKind::CyclicRelationship => "break_relationship_cycles",
        IntegrityIssueKind::MissingFilePath => "prune_missing_local_file_index",
        _ => "manual_review",
    }
}

/// Build a dry-run repair plan with blast-radius summary from an [`IntegrityReport`].
pub fn plan_integrity_repair(report: &IntegrityReport, options: &RepairOptions) -> RepairPlan {
    let mut actions = Vec::new();
    let mut blast = RepairBlastRadius::default();
    let mut tables = HashSet::new();

    for finding in &report.findings {
        let action_id = action_id_for(finding.kind);
        if action_id == "manual_review" {
            continue;
        }
        let is_scoped = options
            .workspace_id
            .as_deref()
            .map(str::trim)
            .is_some_and(|s| !s.is_empty());
        let will_execute = match finding.safety {
            RepairSafety::SafeAuto => true,
            RepairSafety::RequiresApproval => {
                // Cross-workspace edge severing touches another workspace and
                // requires explicit global/admin repair authorization.
                if is_scoped && finding.kind == IntegrityIssueKind::CrossWorkspaceLeak {
                    false
                } else {
                    options.approved_actions.contains(action_id)
                }
            }
        };
        match finding.safety {
            RepairSafety::SafeAuto => blast.safe_auto_actions += 1,
            RepairSafety::RequiresApproval => blast.approval_gated_actions += 1,
        }
        if will_execute {
            match finding.kind {
                IntegrityIssueKind::IndexDriftV2 => {
                    blast.rows_updated += finding.count;
                    tables.insert("nodes_v2".to_string());
                }
                IntegrityIssueKind::IndexDriftFts => {
                    blast.rows_updated += finding.count;
                    tables.insert("node_fts".to_string());
                }
                IntegrityIssueKind::DanglingEdge
                | IntegrityIssueKind::CrossWorkspaceLeak
                | IntegrityIssueKind::CyclicRelationship => {
                    blast.rows_deleted += finding.count;
                    tables.insert("edges".to_string());
                }
                IntegrityIssueKind::DanglingEdgeV2 => {
                    blast.rows_deleted += finding.count;
                    tables.insert("edges_v2".to_string());
                }
                IntegrityIssueKind::DanglingEdgeOccurrence => {
                    blast.rows_deleted += finding.count;
                    tables.insert("edge_occurrences".to_string());
                }
                IntegrityIssueKind::OrphanChunk => {
                    blast.rows_deleted += finding.count;
                    tables.insert("chunks".to_string());
                }
                IntegrityIssueKind::OrphanVector => {
                    blast.rows_deleted += finding.count;
                    tables.insert("vector_embeddings".to_string());
                }
                IntegrityIssueKind::OrphanProvenance => {
                    blast.rows_deleted += finding.count;
                    tables.insert("ingestion_provenance".to_string());
                }
                IntegrityIssueKind::MissingFilePath => {
                    blast.rows_deleted += finding.count;
                    tables.insert("local_file_index".to_string());
                }
                _ => {}
            }
        }
        actions.push(PlannedRepairAction {
            action_id: action_id.to_string(),
            kind: finding.kind,
            safety: finding.safety,
            estimated_rows: finding.count,
            description: finding.recommended_action.clone(),
            will_execute,
        });
    }

    let mut tables_touched: Vec<String> = tables.into_iter().collect();
    tables_touched.sort();
    blast.tables_touched = tables_touched;

    RepairPlan {
        dry_run: options.dry_run,
        blast_radius: blast,
        actions,
    }
}

/// Execute planned integrity repairs transactionally, creating a pre-repair backup when requested.
///
/// When `options.workspace_id` is `Some(ws)`, both inspection and every SQL mutation
/// are strictly restricted to rows owned by `ws`; cross-workspace mutations are refused
/// unless `workspace_id` is `None` (global admin repair).
pub fn execute_integrity_repair(
    conn: &mut Connection,
    options: &RepairOptions,
) -> Result<RepairExecutionResult, CoreError> {
    let ws_scope = options
        .workspace_id
        .as_deref()
        .map(str::trim)
        .filter(|s| !s.is_empty());
    let pre_report = check_graph_integrity_scoped(conn, ws_scope)?;
    let plan = plan_integrity_repair(&pre_report, options);

    let skipped_approval_actions: Vec<String> = plan
        .actions
        .iter()
        .filter(|a| a.safety == RepairSafety::RequiresApproval && !a.will_execute)
        .map(|a| a.action_id.clone())
        .collect();

    if options.dry_run {
        return Ok(RepairExecutionResult {
            dry_run: true,
            backup_created: None,
            plan,
            executed_actions: Vec::new(),
            skipped_approval_actions,
            rows_repaired: 0,
            post_check_total_issues: pre_report.total_issues,
        });
    }

    let mut backup_created = None;
    if let Some(path_str) = options.backup_path.as_deref() {
        let p = Path::new(path_str);
        backup_database(conn, p)?;
        backup_created = Some(path_str.to_string());
    }

    let mut executed_actions = Vec::new();
    let mut rows_repaired = 0usize;
    let tx = conn.transaction()?;

    for action in &plan.actions {
        if !action.will_execute {
            continue;
        }
        let changed = match action.kind {
            IntegrityIssueKind::DanglingEdge => {
                if let Some(w) = ws_scope {
                    let sql = if table_exists(&tx, "nodes_v2") {
                        "DELETE FROM edges WHERE id IN (
                            SELECT e.id FROM edges e
                            LEFT JOIN nodes n1 ON n1.id = e.from_node
                            LEFT JOIN nodes_v2 v1 ON v1.id = n1.id
                            LEFT JOIN nodes n2 ON n2.id = e.to_node
                            LEFT JOIN nodes_v2 v2 ON v2.id = n2.id
                            WHERE (n1.id IS NULL AND n2.id IS NOT NULL
                                   AND COALESCE(NULLIF(TRIM(v2.workspace_id), ''), NULLIF(TRIM(json_extract(n2.metadata_json, '$.workspace_id')), ''), 'personal') = ?1)
                               OR (n2.id IS NULL AND n1.id IS NOT NULL
                                   AND COALESCE(NULLIF(TRIM(v1.workspace_id), ''), NULLIF(TRIM(json_extract(n1.metadata_json, '$.workspace_id')), ''), 'personal') = ?1)
                        )"
                    } else {
                        "DELETE FROM edges WHERE id IN (
                            SELECT e.id FROM edges e
                            LEFT JOIN nodes n1 ON n1.id = e.from_node
                            LEFT JOIN nodes n2 ON n2.id = e.to_node
                            WHERE (n1.id IS NULL AND n2.id IS NOT NULL
                                   AND COALESCE(NULLIF(TRIM(json_extract(n2.metadata_json, '$.workspace_id')), ''), 'personal') = ?1)
                               OR (n2.id IS NULL AND n1.id IS NOT NULL
                                   AND COALESCE(NULLIF(TRIM(json_extract(n1.metadata_json, '$.workspace_id')), ''), 'personal') = ?1)
                        )"
                    };
                    tx.execute(sql, params![w])?
                } else {
                    tx.execute(
                        "DELETE FROM edges WHERE from_node NOT IN (SELECT id FROM nodes)
                         OR to_node NOT IN (SELECT id FROM nodes)",
                        [],
                    )?
                }
            }
            IntegrityIssueKind::DanglingEdgeV2 => {
                if let Some(w) = ws_scope {
                    tx.execute(
                        "DELETE FROM edges_v2 WHERE id IN (
                            SELECT e.id FROM edges_v2 e
                            LEFT JOIN nodes_v2 n1 ON n1.id = e.source
                            LEFT JOIN nodes_v2 n2 ON n2.id = e.target
                            WHERE (n1.id IS NULL AND n2.id IS NOT NULL
                                   AND COALESCE(NULLIF(TRIM(n2.workspace_id), ''), 'personal') = ?1)
                               OR (n2.id IS NULL AND n1.id IS NOT NULL
                                   AND COALESCE(NULLIF(TRIM(n1.workspace_id), ''), 'personal') = ?1)
                        )",
                        params![w],
                    )?
                } else {
                    tx.execute(
                        "DELETE FROM edges_v2 WHERE source NOT IN (SELECT id FROM nodes_v2)
                         OR target NOT IN (SELECT id FROM nodes_v2)",
                        [],
                    )?
                }
            }
            IntegrityIssueKind::DanglingEdgeOccurrence => {
                if ws_scope.is_some() {
                    0
                } else {
                    tx.execute(
                        "DELETE FROM edge_occurrences WHERE edge_id NOT IN (SELECT id FROM edges_v2)",
                        [],
                    )?
                }
            }
            IntegrityIssueKind::OrphanChunk => {
                if let Some(w) = ws_scope {
                    tx.execute(
                        "DELETE FROM chunks
                         WHERE (source_node IS NULL OR source_node = ''
                                OR source_node NOT IN (SELECT id FROM nodes))
                           AND COALESCE(NULLIF(TRIM(json_extract(metadata_json, '$.workspace_id')), ''), 'personal') = ?1",
                        params![w],
                    )?
                } else {
                    tx.execute(
                        "DELETE FROM chunks WHERE source_node IS NULL OR source_node = ''
                         OR source_node NOT IN (SELECT id FROM nodes)",
                        [],
                    )?
                }
            }
            IntegrityIssueKind::OrphanVector => {
                if let Some(w) = ws_scope {
                    if table_exists(&tx, "chunks") {
                        tx.execute(
                            "DELETE FROM vector_embeddings
                             WHERE item_id NOT IN (SELECT id FROM nodes)
                               AND item_id NOT IN (SELECT id FROM chunks)
                               AND COALESCE(NULLIF(TRIM(json_extract(metadata_json, '$.workspace_id')), ''), 'personal') = ?1",
                            params![w],
                        )?
                    } else {
                        tx.execute(
                            "DELETE FROM vector_embeddings
                             WHERE item_id NOT IN (SELECT id FROM nodes)
                               AND COALESCE(NULLIF(TRIM(json_extract(metadata_json, '$.workspace_id')), ''), 'personal') = ?1",
                            params![w],
                        )?
                    }
                } else if table_exists(&tx, "chunks") {
                    tx.execute(
                        "DELETE FROM vector_embeddings
                         WHERE item_id NOT IN (SELECT id FROM nodes)
                           AND item_id NOT IN (SELECT id FROM chunks)",
                        [],
                    )?
                } else {
                    tx.execute(
                        "DELETE FROM vector_embeddings WHERE item_id NOT IN (SELECT id FROM nodes)",
                        [],
                    )?
                }
            }
            IntegrityIssueKind::OrphanProvenance => {
                if let Some(w) = ws_scope {
                    tx.execute(
                        "DELETE FROM ingestion_provenance
                         WHERE node_id NOT IN (SELECT id FROM nodes)
                           AND COALESCE(NULLIF(TRIM(workspace_id), ''), 'personal') = ?1",
                        params![w],
                    )?
                } else {
                    tx.execute(
                        "DELETE FROM ingestion_provenance WHERE node_id NOT IN (SELECT id FROM nodes)",
                        [],
                    )?
                }
            }
            IntegrityIssueKind::IndexDriftV2 => {
                if let Some(w) = ws_scope {
                    tx.execute(
                        "INSERT OR IGNORE INTO nodes_v2(
                            id, type, label, summary, attrs, workspace_id,
                            importance_score, created_at, updated_at
                         )
                         SELECT n.id, n.type, COALESCE(n.title, ''), COALESCE(n.summary, ''), n.metadata_json,
                                COALESCE(NULLIF(TRIM(json_extract(n.metadata_json, '$.workspace_id')), ''), 'personal'),
                                0.5, COALESCE(n.created_at, ''), COALESCE(n.updated_at, n.created_at, '')
                         FROM nodes n
                         LEFT JOIN nodes_v2 v ON v.id = n.id
                         WHERE v.id IS NULL
                           AND COALESCE(NULLIF(TRIM(json_extract(n.metadata_json, '$.workspace_id')), ''), 'personal') = ?1",
                        params![w],
                    )?
                } else {
                    tx.execute(
                        "INSERT OR IGNORE INTO nodes_v2(
                            id, type, label, summary, attrs, workspace_id,
                            importance_score, created_at, updated_at
                         )
                         SELECT n.id, n.type, COALESCE(n.title, ''), COALESCE(n.summary, ''), n.metadata_json,
                                COALESCE(NULLIF(TRIM(json_extract(n.metadata_json, '$.workspace_id')), ''), 'personal'),
                                0.5, COALESCE(n.created_at, ''), COALESCE(n.updated_at, n.created_at, '')
                         FROM nodes n
                         LEFT JOIN nodes_v2 v ON v.id = n.id
                         WHERE v.id IS NULL",
                        [],
                    )?
                }
            }
            IntegrityIssueKind::IndexDriftFts => {
                if let Some(w) = ws_scope {
                    let sql = if table_exists(&tx, "nodes_v2") {
                        "INSERT INTO node_fts(node_id, title, summary)
                         SELECT n.id, COALESCE(n.title, ''), COALESCE(n.summary, '')
                         FROM nodes n
                         LEFT JOIN nodes_v2 v ON v.id = n.id
                         LEFT JOIN node_fts f ON f.node_id = n.id
                         WHERE f.node_id IS NULL
                           AND COALESCE(NULLIF(TRIM(v.workspace_id), ''), NULLIF(TRIM(json_extract(n.metadata_json, '$.workspace_id')), ''), 'personal') = ?1"
                    } else {
                        "INSERT INTO node_fts(node_id, title, summary)
                         SELECT n.id, COALESCE(n.title, ''), COALESCE(n.summary, '')
                         FROM nodes n
                         LEFT JOIN node_fts f ON f.node_id = n.id
                         WHERE f.node_id IS NULL
                           AND COALESCE(NULLIF(TRIM(json_extract(n.metadata_json, '$.workspace_id')), ''), 'personal') = ?1"
                    };
                    tx.execute(sql, params![w])?
                } else {
                    tx.execute(
                        "INSERT INTO node_fts(node_id, title, summary)
                         SELECT n.id, COALESCE(n.title, ''), COALESCE(n.summary, '')
                         FROM nodes n
                         LEFT JOIN node_fts f ON f.node_id = n.id
                         WHERE f.node_id IS NULL",
                        [],
                    )?
                }
            }
            IntegrityIssueKind::CrossWorkspaceLeak => {
                if ws_scope.is_some() {
                    // Prohibited in workspace-scoped repairs; requires global admin authorization.
                    0
                } else {
                    let n1 = tx.execute(
                        "DELETE FROM edges WHERE id IN (
                            SELECT e.id FROM edges e
                            JOIN nodes_v2 a ON a.id = e.from_node
                            JOIN nodes_v2 b ON b.id = e.to_node
                            WHERE COALESCE(NULLIF(TRIM(a.workspace_id), ''), 'personal')
                               != COALESCE(NULLIF(TRIM(b.workspace_id), ''), 'personal')
                        )",
                        [],
                    )?;
                    let n2 = if table_exists(&tx, "edges_v2") {
                        tx.execute(
                            "DELETE FROM edges_v2 WHERE id IN (
                                SELECT e.id FROM edges_v2 e
                                JOIN nodes_v2 a ON a.id = e.source
                                JOIN nodes_v2 b ON b.id = e.target
                                WHERE COALESCE(NULLIF(TRIM(a.workspace_id), ''), 'personal')
                                   != COALESCE(NULLIF(TRIM(b.workspace_id), ''), 'personal')
                            )",
                            [],
                        )?
                    } else {
                        0
                    };
                    n1 + n2
                }
            }
            IntegrityIssueKind::CyclicRelationship => {
                let mut broken = 0usize;
                let type_list = DAG_EDGE_TYPES
                    .iter()
                    .map(|t| format!("'{t}'"))
                    .collect::<Vec<_>>()
                    .join(",");
                let dag_edges: Vec<(String, String, String)> = if let Some(w) = ws_scope {
                    let sql = if table_exists(&tx, "nodes_v2") {
                        format!(
                            "SELECT e.id, e.from_node, e.to_node FROM edges e
                             JOIN nodes n1 ON n1.id = e.from_node
                             LEFT JOIN nodes_v2 v1 ON v1.id = n1.id
                             JOIN nodes n2 ON n2.id = e.to_node
                             LEFT JOIN nodes_v2 v2 ON v2.id = n2.id
                             WHERE UPPER(e.type) IN ({type_list})
                               AND COALESCE(NULLIF(TRIM(v1.workspace_id), ''), NULLIF(TRIM(json_extract(n1.metadata_json, '$.workspace_id')), ''), 'personal') = ?1
                               AND COALESCE(NULLIF(TRIM(v2.workspace_id), ''), NULLIF(TRIM(json_extract(n2.metadata_json, '$.workspace_id')), ''), 'personal') = ?1"
                        )
                    } else {
                        format!(
                            "SELECT e.id, e.from_node, e.to_node FROM edges e
                             JOIN nodes n1 ON n1.id = e.from_node
                             JOIN nodes n2 ON n2.id = e.to_node
                             WHERE UPPER(e.type) IN ({type_list})
                               AND COALESCE(NULLIF(TRIM(json_extract(n1.metadata_json, '$.workspace_id')), ''), 'personal') = ?1
                               AND COALESCE(NULLIF(TRIM(json_extract(n2.metadata_json, '$.workspace_id')), ''), 'personal') = ?1"
                        )
                    };
                    let mut stmt = tx.prepare(&sql)?;
                    let out = stmt
                        .query_map(params![w], |r| {
                            Ok((
                                r.get::<_, String>(0)?,
                                r.get::<_, String>(1)?,
                                r.get::<_, String>(2)?,
                            ))
                        })?
                        .filter_map(Result::ok)
                        .collect();
                    out
                } else {
                    let mut stmt = tx.prepare(&format!(
                        "SELECT id, from_node, to_node FROM edges WHERE UPPER(type) IN ({type_list})"
                    ))?;
                    let out = stmt
                        .query_map([], |r| {
                            Ok((
                                r.get::<_, String>(0)?,
                                r.get::<_, String>(1)?,
                                r.get::<_, String>(2)?,
                            ))
                        })?
                        .filter_map(Result::ok)
                        .collect();
                    out
                };
                for eid in detect_cycles(&dag_edges) {
                    broken += tx.execute("DELETE FROM edges WHERE id = ?1", params![eid])?;
                }
                broken
            }
            IntegrityIssueKind::MissingFilePath => {
                let stale_ids: Vec<String> = if let Some(w) = ws_scope {
                    let sql = if table_exists(&tx, "nodes_v2") {
                        "SELECT f.id, f.file_path FROM local_file_index f
                         JOIN nodes n ON n.id = f.graph_node_id
                         LEFT JOIN nodes_v2 v ON v.id = n.id
                         WHERE f.deleted = 0
                           AND COALESCE(NULLIF(TRIM(v.workspace_id), ''), NULLIF(TRIM(json_extract(n.metadata_json, '$.workspace_id')), ''), 'personal') = ?1"
                    } else {
                        "SELECT f.id, f.file_path FROM local_file_index f
                         JOIN nodes n ON n.id = f.graph_node_id
                         WHERE f.deleted = 0
                           AND COALESCE(NULLIF(TRIM(json_extract(n.metadata_json, '$.workspace_id')), ''), 'personal') = ?1"
                    };
                    let mut stmt = tx.prepare(sql)?;
                    let out = stmt
                        .query_map(params![w], |r| {
                            Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?))
                        })?
                        .filter_map(Result::ok)
                        .filter(|(_, p)| !p.trim().is_empty() && !Path::new(p).exists())
                        .map(|(id, _)| id)
                        .collect();
                    out
                } else {
                    let mut stmt =
                        tx.prepare("SELECT id, file_path FROM local_file_index WHERE deleted = 0")?;
                    let out = stmt
                        .query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?)))?
                        .filter_map(Result::ok)
                        .filter(|(_, p)| !p.trim().is_empty() && !Path::new(p).exists())
                        .map(|(id, _)| id)
                        .collect();
                    out
                };
                let mut pruned = 0usize;
                for id in stale_ids {
                    pruned +=
                        tx.execute("DELETE FROM local_file_index WHERE id = ?1", params![id])?;
                }
                pruned
            }
            _ => 0,
        };
        rows_repaired += changed;
        executed_actions.push(action.action_id.clone());
    }

    tx.commit()?;
    let post = check_graph_integrity_scoped(conn, ws_scope)?;

    Ok(RepairExecutionResult {
        dry_run: false,
        backup_created,
        plan,
        executed_actions,
        skipped_approval_actions,
        rows_repaired,
        post_check_total_issues: post.total_issues,
    })
}
