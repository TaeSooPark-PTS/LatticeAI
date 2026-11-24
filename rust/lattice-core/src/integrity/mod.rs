//! Unified Knowledge Graph Data Integrity Checker & Safe Repair Engine (P0.2).
//!
//! Verifies referential integrity, provenance coverage, orphan chunks/vectors,
//! duplicate natural keys, cross-workspace reference leaks, missing local file
//! paths, primary-vs-secondary index drift (`nodes_v2`, `edges_v2`, `node_fts`,
//! `bm25_node_stats`, `vector_embeddings`), and DAG-invariant relationship cycles.
//!
//! Repair guarantees:
//! - Dry-run preview with explicit blast-radius summary before any mutation.
//! - Automatic `VACUUM INTO` snapshot backup prior to executing repairs when a
//!   `backup_path` is provided.
//! - Strict split between provably safe auto-repairs (dangling edge/occurrence
//!   cleanup, orphan vector cleanup, FTS5/v2 mirror resync) and approval-gated
//!   repairs (breaking cycles, removing cross-workspace leaked edges, pruning
//!   missing local file records).

use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::path::Path;

use rusqlite::{params, Connection};
use serde::{Deserialize, Serialize};

use crate::db::CoreError;

/// Severity of an integrity finding.
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum IntegritySeverity {
    Critical,
    Warning,
    Info,
}

/// Whether a repair action is provably safe for automatic execution or requires explicit user approval.
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum RepairSafety {
    SafeAuto,
    RequiresApproval,
}

/// Canonical categories matching the P0.2 Mandatory Integrity Checks.
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq, Hash, PartialOrd, Ord)]
#[serde(rename_all = "snake_case")]
pub enum IntegrityIssueKind {
    SqliteCorruption,
    ForeignKeyViolation,
    DanglingEdge,
    DanglingEdgeV2,
    DanglingEdgeOccurrence,
    MissingProvenance,
    OrphanChunk,
    OrphanVector,
    OrphanProvenance,
    DuplicateNaturalKey,
    CrossWorkspaceLeak,
    MissingFilePath,
    IndexDriftV2,
    IndexDriftFts,
    IndexDriftVector,
    CyclicRelationship,
}

/// One detected integrity issue with affected record IDs and repair classification.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct IntegrityFinding {
    pub kind: IntegrityIssueKind,
    pub severity: IntegritySeverity,
    pub safety: RepairSafety,
    pub count: usize,
    pub sample_ids: Vec<String>,
    pub detail: String,
    pub recommended_action: String,
}

/// Full diagnostic report produced by [`check_graph_integrity`].
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct IntegrityReport {
    pub healthy: bool,
    pub sqlite_integrity_ok: bool,
    pub foreign_key_violations: usize,
    pub total_issues: usize,
    pub critical_issues: usize,
    pub warning_issues: usize,
    pub findings: Vec<IntegrityFinding>,
    pub counts_by_kind: BTreeMap<String, usize>,
}

/// Blast-radius estimate for a planned repair operation.
#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq, Eq)]
pub struct RepairBlastRadius {
    pub rows_deleted: usize,
    pub rows_updated: usize,
    pub tables_touched: Vec<String>,
    pub safe_auto_actions: usize,
    pub approval_gated_actions: usize,
}

/// One discrete repair action included in a [`RepairPlan`].
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct PlannedRepairAction {
    pub action_id: String,
    pub kind: IntegrityIssueKind,
    pub safety: RepairSafety,
    pub estimated_rows: usize,
    pub description: String,
    pub will_execute: bool,
}

/// Dry-run preview plan returned before any mutation occurs.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct RepairPlan {
    pub dry_run: bool,
    pub blast_radius: RepairBlastRadius,
    pub actions: Vec<PlannedRepairAction>,
}

/// Policy controlling which repair actions may run.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct RepairOptions {
    /// When true, compute blast-radius preview without writing.
    pub dry_run: bool,
    /// Explicit set of `action_id`s approved by the user for `RequiresApproval` repairs.
    pub approved_actions: BTreeSet<String>,
    /// Optional path where an atomic SQLite snapshot backup is written before mutating.
    pub backup_path: Option<String>,
    /// Strict workspace scope. When `Some(ws)`, both inspection and repair are
    /// strictly limited to rows owned by `ws` and cross-workspace mutations are
    /// prohibited. `None` runs global DB-wide repair (requires explicit admin
    /// authorization at the API layer).
    #[serde(default)]
    pub workspace_id: Option<String>,
}

/// Outcome of executing [`execute_integrity_repair`].
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct RepairExecutionResult {
    pub dry_run: bool,
    pub backup_created: Option<String>,
    pub plan: RepairPlan,
    pub executed_actions: Vec<String>,
    pub skipped_approval_actions: Vec<String>,
    pub rows_repaired: usize,
    pub post_check_total_issues: usize,
}

const DAG_EDGE_TYPES: &[&str] = &[
    "SUPERSEDES",
    "PART_OF",
    "DEPENDS_ON",
    "VERSION_OF",
    "DERIVED_FROM",
];

const PROVENANCE_REQUIRED_TYPES: &[&str] = &["Document", "File", "Fact", "Decision", "Claim"];

fn table_exists(conn: &Connection, table: &str) -> bool {
    conn.query_row(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name = ?1 LIMIT 1",
        params![table],
        |_| Ok(()),
    )
    .is_ok()
}

fn collect_string_col(conn: &Connection, sql: &str) -> Result<Vec<String>, CoreError> {
    let mut stmt = conn.prepare(sql)?;
    let rows = stmt
        .query_map([], |r| r.get::<_, String>(0))?
        .filter_map(Result::ok)
        .collect();
    Ok(rows)
}

fn collect_string_col_param(
    conn: &Connection,
    sql: &str,
    param: &str,
) -> Result<Vec<String>, CoreError> {
    let mut stmt = conn.prepare(sql)?;
    let rows = stmt
        .query_map(params![param], |r| r.get::<_, String>(0))?
        .filter_map(Result::ok)
        .collect();
    Ok(rows)
}

fn push_finding(
    findings: &mut Vec<IntegrityFinding>,
    kind: IntegrityIssueKind,
    severity: IntegritySeverity,
    safety: RepairSafety,
    ids: Vec<String>,
    detail: String,
    recommended_action: &str,
) {
    if ids.is_empty() {
        return;
    }
    let count = ids.len();
    let sample_ids = ids.into_iter().take(12).collect();
    findings.push(IntegrityFinding {
        kind,
        severity,
        safety,
        count,
        sample_ids,
        detail,
        recommended_action: recommended_action.to_string(),
    });
}

fn detect_cycles(edges: &[(String, String, String)]) -> Vec<String> {
    let mut adj: HashMap<&str, Vec<(&str, &str)>> = HashMap::new();
    for (edge_id, u, v) in edges {
        adj.entry(u.as_str())
            .or_default()
            .push((v.as_str(), edge_id.as_str()));
    }
    let mut state: HashMap<&str, u8> = HashMap::new(); // 0=unvisited, 1=visiting, 2=done
    let mut cycle_edges: BTreeSet<String> = BTreeSet::new();

    fn dfs<'a>(
        node: &'a str,
        adj: &HashMap<&'a str, Vec<(&'a str, &'a str)>>,
        state: &mut HashMap<&'a str, u8>,
        cycle_edges: &mut BTreeSet<String>,
    ) {
        state.insert(node, 1);
        if let Some(neighbors) = adj.get(node) {
            for &(next, eid) in neighbors {
                match state.get(next).copied().unwrap_or(0) {
                    1 => {
                        cycle_edges.insert(eid.to_string());
                    }
                    0 => dfs(next, adj, state, cycle_edges),
                    _ => {}
                }
            }
        }
        state.insert(node, 2);
    }

    let nodes: Vec<&str> = adj.keys().copied().collect();
    for node in nodes {
        if state.get(node).copied().unwrap_or(0) == 0 {
            dfs(node, &adj, &mut state, &mut cycle_edges);
        }
    }
    cycle_edges.into_iter().collect()
}

/// Run the full Knowledge Graph data-integrity audit against `conn` (global scope).
pub fn check_graph_integrity(conn: &Connection) -> Result<IntegrityReport, CoreError> {
    check_graph_integrity_scoped(conn, None)
}

/// Run the Knowledge Graph data-integrity audit against `conn`, optionally
/// restricted to a single `workspace_id`.
///
/// When `workspace_id` is `Some(ws)`, all row-level checks are strictly filtered
/// to records owned by `ws` so callers cannot inspect or repair data belonging
/// to other workspaces.
pub fn check_graph_integrity_scoped(
    conn: &Connection,
    workspace_id: Option<&str>,
) -> Result<IntegrityReport, CoreError> {
    let ws = workspace_id.map(str::trim).filter(|s| !s.is_empty());
    let mut findings = Vec::new();

    // 1. SQLite low-level integrity & foreign-key checks.
    let integrity_rows = collect_string_col(conn, "PRAGMA integrity_check")?;
    let sqlite_integrity_ok =
        integrity_rows.len() == 1 && integrity_rows[0].eq_ignore_ascii_case("ok");
    if !sqlite_integrity_ok {
        push_finding(
            &mut findings,
            IntegrityIssueKind::SqliteCorruption,
            IntegritySeverity::Critical,
            RepairSafety::RequiresApproval,
            integrity_rows,
            "SQLite PRAGMA integrity_check reported B-tree or page errors.".to_string(),
            "Restore from the latest verified backup snapshot.",
        );
    }

    let foreign_key_violations = if ws.is_none() {
        let fk_violations: Vec<String> = {
            let mut stmt = conn.prepare("PRAGMA foreign_key_check")?;
            let out = stmt
                .query_map([], |r| {
                    let tbl: String = r.get(0)?;
                    let rowid: i64 = r.get(1)?;
                    let parent: String = r.get(2)?;
                    Ok(format!("{tbl}:{rowid}->{parent}"))
                })?
                .filter_map(Result::ok)
                .collect();
            out
        };
        let count = fk_violations.len();
        push_finding(
            &mut findings,
            IntegrityIssueKind::ForeignKeyViolation,
            IntegritySeverity::Critical,
            RepairSafety::SafeAuto,
            fk_violations,
            "Rows failed SQLite PRAGMA foreign_key_check.".to_string(),
            "Remove dangling child rows after snapshot backup.",
        );
        count
    } else {
        0
    };

    // 2. Dangling edges (`edges`, `edges_v2`, `edge_occurrences`).
    if table_exists(conn, "edges") && table_exists(conn, "nodes") {
        let dangling = if let Some(w) = ws {
            if table_exists(conn, "nodes_v2") {
                collect_string_col_param(
                    conn,
                    "SELECT e.id FROM edges e
                     LEFT JOIN nodes n1 ON n1.id = e.from_node
                     LEFT JOIN nodes_v2 v1 ON v1.id = n1.id
                     LEFT JOIN nodes n2 ON n2.id = e.to_node
                     LEFT JOIN nodes_v2 v2 ON v2.id = n2.id
                     WHERE (n1.id IS NULL AND n2.id IS NOT NULL
                            AND COALESCE(NULLIF(TRIM(v2.workspace_id), ''), NULLIF(TRIM(json_extract(n2.metadata_json, '$.workspace_id')), ''), 'personal') = ?1)
                        OR (n2.id IS NULL AND n1.id IS NOT NULL
                            AND COALESCE(NULLIF(TRIM(v1.workspace_id), ''), NULLIF(TRIM(json_extract(n1.metadata_json, '$.workspace_id')), ''), 'personal') = ?1)",
                    w,
                )?
            } else {
                collect_string_col_param(
                    conn,
                    "SELECT e.id FROM edges e
                     LEFT JOIN nodes n1 ON n1.id = e.from_node
                     LEFT JOIN nodes n2 ON n2.id = e.to_node
                     WHERE (n1.id IS NULL AND n2.id IS NOT NULL
                            AND COALESCE(NULLIF(TRIM(json_extract(n2.metadata_json, '$.workspace_id')), ''), 'personal') = ?1)
                        OR (n2.id IS NULL AND n1.id IS NOT NULL
                            AND COALESCE(NULLIF(TRIM(json_extract(n1.metadata_json, '$.workspace_id')), ''), 'personal') = ?1)",
                    w,
                )?
            }
        } else {
            collect_string_col(
                conn,
                "SELECT e.id FROM edges e
                 LEFT JOIN nodes n1 ON n1.id = e.from_node
                 LEFT JOIN nodes n2 ON n2.id = e.to_node
                 WHERE n1.id IS NULL OR n2.id IS NULL",
            )?
        };
        push_finding(
            &mut findings,
            IntegrityIssueKind::DanglingEdge,
            IntegritySeverity::Critical,
            RepairSafety::SafeAuto,
            dangling,
            "Edges referencing non-existent source or target nodes.".to_string(),
            "Delete dangling rows from edges.",
        );
    }
    if table_exists(conn, "edges_v2") && table_exists(conn, "nodes_v2") {
        let dangling_v2 = if let Some(w) = ws {
            collect_string_col_param(
                conn,
                "SELECT e.id FROM edges_v2 e
                 LEFT JOIN nodes_v2 n1 ON n1.id = e.source
                 LEFT JOIN nodes_v2 n2 ON n2.id = e.target
                 WHERE (n1.id IS NULL AND n2.id IS NOT NULL
                        AND COALESCE(NULLIF(TRIM(n2.workspace_id), ''), 'personal') = ?1)
                    OR (n2.id IS NULL AND n1.id IS NOT NULL
                        AND COALESCE(NULLIF(TRIM(n1.workspace_id), ''), 'personal') = ?1)",
                w,
            )?
        } else {
            collect_string_col(
                conn,
                "SELECT e.id FROM edges_v2 e
                 LEFT JOIN nodes_v2 n1 ON n1.id = e.source
                 LEFT JOIN nodes_v2 n2 ON n2.id = e.target
                 WHERE n1.id IS NULL OR n2.id IS NULL",
            )?
        };
        push_finding(
            &mut findings,
            IntegrityIssueKind::DanglingEdgeV2,
            IntegritySeverity::Critical,
            RepairSafety::SafeAuto,
            dangling_v2,
            "edges_v2 rows referencing missing nodes_v2 endpoints.".to_string(),
            "Delete dangling rows from edges_v2.",
        );
    }
    if ws.is_none() && table_exists(conn, "edge_occurrences") && table_exists(conn, "edges_v2") {
        let dangling_occ = collect_string_col(
            conn,
            "SELECT CAST(o.id AS TEXT) FROM edge_occurrences o
             LEFT JOIN edges_v2 e ON e.id = o.edge_id
             WHERE e.id IS NULL",
        )?;
        push_finding(
            &mut findings,
            IntegrityIssueKind::DanglingEdgeOccurrence,
            IntegritySeverity::Warning,
            RepairSafety::SafeAuto,
            dangling_occ,
            "edge_occurrences rows detached from any parent edge.".to_string(),
            "Delete orphaned edge_occurrences rows.",
        );
    }

    // 3. Orphan chunks, vectors, and provenance rows.
    if table_exists(conn, "chunks") && table_exists(conn, "nodes") {
        let orphan_chunks = if let Some(w) = ws {
            collect_string_col_param(
                conn,
                "SELECT c.id FROM chunks c
                 LEFT JOIN nodes n ON n.id = c.source_node
                 WHERE (c.source_node IS NULL OR c.source_node = '' OR n.id IS NULL)
                   AND COALESCE(NULLIF(TRIM(json_extract(c.metadata_json, '$.workspace_id')), ''), 'personal') = ?1",
                w,
            )?
        } else {
            collect_string_col(
                conn,
                "SELECT c.id FROM chunks c
                 LEFT JOIN nodes n ON n.id = c.source_node
                 WHERE c.source_node IS NULL OR c.source_node = '' OR n.id IS NULL",
            )?
        };
        push_finding(
            &mut findings,
            IntegrityIssueKind::OrphanChunk,
            IntegritySeverity::Critical,
            RepairSafety::SafeAuto,
            orphan_chunks,
            "Chunks detached from a valid parent document/source node.".to_string(),
            "Delete orphan chunk rows and cascade their vector entries.",
        );
    }
    if table_exists(conn, "vector_embeddings") && table_exists(conn, "nodes") {
        let has_chunks = table_exists(conn, "chunks");
        let orphan_vecs = if let Some(w) = ws {
            let sql = if has_chunks {
                "SELECT v.item_id FROM vector_embeddings v
                 LEFT JOIN nodes n ON n.id = v.item_id
                 LEFT JOIN chunks c ON c.id = v.item_id
                 WHERE n.id IS NULL AND c.id IS NULL
                   AND COALESCE(NULLIF(TRIM(json_extract(v.metadata_json, '$.workspace_id')), ''), 'personal') = ?1"
            } else {
                "SELECT v.item_id FROM vector_embeddings v
                 LEFT JOIN nodes n ON n.id = v.item_id
                 WHERE n.id IS NULL
                   AND COALESCE(NULLIF(TRIM(json_extract(v.metadata_json, '$.workspace_id')), ''), 'personal') = ?1"
            };
            collect_string_col_param(conn, sql, w)?
        } else {
            let sql = if has_chunks {
                "SELECT v.item_id FROM vector_embeddings v
                 LEFT JOIN nodes n ON n.id = v.item_id
                 LEFT JOIN chunks c ON c.id = v.item_id
                 WHERE n.id IS NULL AND c.id IS NULL"
            } else {
                "SELECT v.item_id FROM vector_embeddings v
                 LEFT JOIN nodes n ON n.id = v.item_id
                 WHERE n.id IS NULL"
            };
            collect_string_col(conn, sql)?
        };
        push_finding(
            &mut findings,
            IntegrityIssueKind::OrphanVector,
            IntegritySeverity::Warning,
            RepairSafety::SafeAuto,
            orphan_vecs,
            "Vector embeddings whose parent node/chunk no longer exists.".to_string(),
            "Delete orphaned vector_embeddings rows.",
        );
    }
    if table_exists(conn, "ingestion_provenance") && table_exists(conn, "nodes") {
        let orphan_prov = if let Some(w) = ws {
            collect_string_col_param(
                conn,
                "SELECT CAST(p.id AS TEXT) FROM ingestion_provenance p
                 LEFT JOIN nodes n ON n.id = p.node_id
                 WHERE n.id IS NULL
                   AND COALESCE(NULLIF(TRIM(p.workspace_id), ''), 'personal') = ?1",
                w,
            )?
        } else {
            collect_string_col(
                conn,
                "SELECT CAST(p.id AS TEXT) FROM ingestion_provenance p
                 LEFT JOIN nodes n ON n.id = p.node_id
                 WHERE n.id IS NULL",
            )?
        };
        push_finding(
            &mut findings,
            IntegrityIssueKind::OrphanProvenance,
            IntegritySeverity::Warning,
            RepairSafety::SafeAuto,
            orphan_prov,
            "ingestion_provenance records referencing deleted nodes.".to_string(),
            "Delete orphaned ingestion_provenance rows.",
        );

        let placeholders = PROVENANCE_REQUIRED_TYPES
            .iter()
            .map(|t| format!("'{t}'"))
            .collect::<Vec<_>>()
            .join(",");
        let missing_prov = if let Some(w) = ws {
            let sql = if table_exists(conn, "nodes_v2") {
                format!(
                    "SELECT n.id FROM nodes n
                     LEFT JOIN nodes_v2 v ON v.id = n.id
                     LEFT JOIN ingestion_provenance p ON p.node_id = n.id
                     WHERE n.type IN ({placeholders})
                       AND p.node_id IS NULL
                       AND COALESCE(NULLIF(TRIM(v.workspace_id), ''), NULLIF(TRIM(json_extract(n.metadata_json, '$.workspace_id')), ''), 'personal') = ?1
                       AND COALESCE(json_extract(n.metadata_json, '$.source_uri'), '') = ''
                       AND COALESCE(json_extract(n.metadata_json, '$.source_path'), '') = ''
                       AND COALESCE(json_extract(n.metadata_json, '$.provenance_source'), '') = ''"
                )
            } else {
                format!(
                    "SELECT n.id FROM nodes n
                     LEFT JOIN ingestion_provenance p ON p.node_id = n.id
                     WHERE n.type IN ({placeholders})
                       AND p.node_id IS NULL
                       AND COALESCE(NULLIF(TRIM(json_extract(n.metadata_json, '$.workspace_id')), ''), 'personal') = ?1
                       AND COALESCE(json_extract(n.metadata_json, '$.source_uri'), '') = ''
                       AND COALESCE(json_extract(n.metadata_json, '$.source_path'), '') = ''
                       AND COALESCE(json_extract(n.metadata_json, '$.provenance_source'), '') = ''"
                )
            };
            collect_string_col_param(conn, &sql, w)?
        } else {
            collect_string_col(
                conn,
                &format!(
                    "SELECT n.id FROM nodes n
                     LEFT JOIN ingestion_provenance p ON p.node_id = n.id
                     WHERE n.type IN ({placeholders})
                       AND p.node_id IS NULL
                       AND COALESCE(json_extract(n.metadata_json, '$.source_uri'), '') = ''
                       AND COALESCE(json_extract(n.metadata_json, '$.source_path'), '') = ''
                       AND COALESCE(json_extract(n.metadata_json, '$.provenance_source'), '') = ''"
                ),
            )?
        };
        push_finding(
            &mut findings,
            IntegrityIssueKind::MissingProvenance,
            IntegritySeverity::Warning,
            RepairSafety::RequiresApproval,
            missing_prov,
            "Core Document/File/Fact/Decision nodes lacking provenance metadata or ingestion_provenance row."
                .to_string(),
            "Attach provenance source metadata or quarantine ungrounded nodes.",
        );
    }

    // 4. Cross-workspace reference leaks.
    if table_exists(conn, "edges") && table_exists(conn, "nodes_v2") {
        let leaks = if let Some(w) = ws {
            collect_string_col_param(
                conn,
                "SELECT e.id FROM edges e
                 JOIN nodes_v2 a ON a.id = e.from_node
                 JOIN nodes_v2 b ON b.id = e.to_node
                 WHERE COALESCE(NULLIF(TRIM(a.workspace_id), ''), 'personal')
                    != COALESCE(NULLIF(TRIM(b.workspace_id), ''), 'personal')
                   AND (COALESCE(NULLIF(TRIM(a.workspace_id), ''), 'personal') = ?1
                     OR COALESCE(NULLIF(TRIM(b.workspace_id), ''), 'personal') = ?1)",
                w,
            )?
        } else {
            collect_string_col(
                conn,
                "SELECT e.id FROM edges e
                 JOIN nodes_v2 a ON a.id = e.from_node
                 JOIN nodes_v2 b ON b.id = e.to_node
                 WHERE COALESCE(NULLIF(TRIM(a.workspace_id), ''), 'personal')
                    != COALESCE(NULLIF(TRIM(b.workspace_id), ''), 'personal')",
            )?
        };
        push_finding(
            &mut findings,
            IntegrityIssueKind::CrossWorkspaceLeak,
            IntegritySeverity::Critical,
            RepairSafety::RequiresApproval,
            leaks,
            "Edges linking nodes that belong to different workspaces.".to_string(),
            "Sever cross-workspace edges after explicit admin review.",
        );
    }

    // 5. Duplicate natural keys (active source_uri collisions).
    if table_exists(conn, "nodes") && table_exists(conn, "nodes_v2") {
        let dup_uris = if let Some(w) = ws {
            collect_string_col_param(
                conn,
                "SELECT json_extract(n.metadata_json, '$.source_uri') || ' (x' || COUNT(*) || ')'
                 FROM nodes n
                 LEFT JOIN nodes_v2 v ON v.id = n.id
                 WHERE n.type IN ('Document', 'File')
                   AND COALESCE(v.superseded_by, '') = ''
                   AND COALESCE(json_extract(n.metadata_json, '$.source_uri'), '') != ''
                   AND COALESCE(NULLIF(TRIM(v.workspace_id), ''), 'personal') = ?1
                 GROUP BY COALESCE(NULLIF(TRIM(v.workspace_id), ''), 'personal'),
                          json_extract(n.metadata_json, '$.source_uri')
                 HAVING COUNT(*) > 1",
                w,
            )?
        } else {
            collect_string_col(
                conn,
                "SELECT json_extract(n.metadata_json, '$.source_uri') || ' (x' || COUNT(*) || ')'
                 FROM nodes n
                 LEFT JOIN nodes_v2 v ON v.id = n.id
                 WHERE n.type IN ('Document', 'File')
                   AND COALESCE(v.superseded_by, '') = ''
                   AND COALESCE(json_extract(n.metadata_json, '$.source_uri'), '') != ''
                 GROUP BY COALESCE(NULLIF(TRIM(v.workspace_id), ''), 'personal'),
                          json_extract(n.metadata_json, '$.source_uri')
                 HAVING COUNT(*) > 1",
            )?
        };
        push_finding(
            &mut findings,
            IntegrityIssueKind::DuplicateNaturalKey,
            IntegritySeverity::Warning,
            RepairSafety::RequiresApproval,
            dup_uris,
            "Multiple active Document/File nodes share the same source_uri in a workspace."
                .to_string(),
            "Supersede older duplicate revisions after diff review.",
        );
    }

    // 6. Missing / deleted file paths in `local_file_index`.
    if table_exists(conn, "local_file_index") {
        let missing_paths: Vec<String> = if let Some(w) = ws {
            if table_exists(conn, "nodes") {
                let sql = if table_exists(conn, "nodes_v2") {
                    "SELECT f.id, f.file_path, COALESCE(f.graph_node_id, '')
                     FROM local_file_index f
                     JOIN nodes n ON n.id = f.graph_node_id
                     LEFT JOIN nodes_v2 v ON v.id = n.id
                     WHERE f.deleted = 0
                       AND COALESCE(NULLIF(TRIM(v.workspace_id), ''), NULLIF(TRIM(json_extract(n.metadata_json, '$.workspace_id')), ''), 'personal') = ?1"
                } else {
                    "SELECT f.id, f.file_path, COALESCE(f.graph_node_id, '')
                     FROM local_file_index f
                     JOIN nodes n ON n.id = f.graph_node_id
                     WHERE f.deleted = 0
                       AND COALESCE(NULLIF(TRIM(json_extract(n.metadata_json, '$.workspace_id')), ''), 'personal') = ?1"
                };
                let mut stmt = conn.prepare(sql)?;
                let out = stmt
                    .query_map(params![w], |r| {
                        Ok((
                            r.get::<_, String>(0)?,
                            r.get::<_, String>(1)?,
                            r.get::<_, String>(2)?,
                        ))
                    })?
                    .filter_map(Result::ok)
                    .filter(|(_, path, _)| !path.trim().is_empty() && !Path::new(path).exists())
                    .map(|(id, path, node_id)| format!("{id}:{node_id}:{path}"))
                    .collect();
                out
            } else {
                Vec::new()
            }
        } else {
            let mut stmt = conn.prepare(
                "SELECT id, file_path, COALESCE(graph_node_id, '') FROM local_file_index WHERE deleted = 0",
            )?;
            let out = stmt
                .query_map([], |r| {
                    Ok((
                        r.get::<_, String>(0)?,
                        r.get::<_, String>(1)?,
                        r.get::<_, String>(2)?,
                    ))
                })?
                .filter_map(Result::ok)
                .filter(|(_, path, _)| !path.trim().is_empty() && !Path::new(path).exists())
                .map(|(id, path, node_id)| format!("{id}:{node_id}:{path}"))
                .collect();
            out
        };
        push_finding(
            &mut findings,
            IntegrityIssueKind::MissingFilePath,
            IntegritySeverity::Warning,
            RepairSafety::RequiresApproval,
            missing_paths,
            "Indexed local file paths no longer exist on disk.".to_string(),
            "Approve pruning of deleted local file records or relocate moved files.",
        );
    }

    // 7. Primary DB vs secondary index drift (`nodes_v2`, `node_fts`, `vector_embeddings`).
    if table_exists(conn, "nodes") && table_exists(conn, "nodes_v2") {
        let v2_missing = if let Some(w) = ws {
            collect_string_col_param(
                conn,
                "SELECT n.id FROM nodes n
                 LEFT JOIN nodes_v2 v ON v.id = n.id
                 WHERE v.id IS NULL
                   AND COALESCE(NULLIF(TRIM(json_extract(n.metadata_json, '$.workspace_id')), ''), 'personal') = ?1",
                w,
            )?
        } else {
            collect_string_col(
                conn,
                "SELECT n.id FROM nodes n LEFT JOIN nodes_v2 v ON v.id = n.id WHERE v.id IS NULL",
            )?
        };
        push_finding(
            &mut findings,
            IntegrityIssueKind::IndexDriftV2,
            IntegritySeverity::Warning,
            RepairSafety::SafeAuto,
            v2_missing,
            "Nodes present in primary nodes table missing from nodes_v2 projection.".to_string(),
            "Backfill missing nodes_v2 rows from nodes.",
        );
    }
    if table_exists(conn, "nodes") && table_exists(conn, "node_fts") {
        let fts_drift = if let Some(w) = ws {
            if table_exists(conn, "nodes_v2") {
                collect_string_col_param(
                    conn,
                    "SELECT n.id FROM nodes n
                     LEFT JOIN nodes_v2 v ON v.id = n.id
                     LEFT JOIN node_fts f ON f.node_id = n.id
                     WHERE f.node_id IS NULL
                       AND COALESCE(NULLIF(TRIM(v.workspace_id), ''), NULLIF(TRIM(json_extract(n.metadata_json, '$.workspace_id')), ''), 'personal') = ?1",
                    w,
                )?
            } else {
                collect_string_col_param(
                    conn,
                    "SELECT n.id FROM nodes n
                     LEFT JOIN node_fts f ON f.node_id = n.id
                     WHERE f.node_id IS NULL
                       AND COALESCE(NULLIF(TRIM(json_extract(n.metadata_json, '$.workspace_id')), ''), 'personal') = ?1",
                    w,
                )?
            }
        } else {
            collect_string_col(
                conn,
                "SELECT n.id FROM nodes n
                 LEFT JOIN node_fts f ON f.node_id = n.id
                 WHERE f.node_id IS NULL",
            )?
        };
        push_finding(
            &mut findings,
            IntegrityIssueKind::IndexDriftFts,
            IntegritySeverity::Warning,
            RepairSafety::SafeAuto,
            fts_drift,
            "Nodes missing from node_fts full-text search index.".to_string(),
            "Backfill missing node_fts entries.",
        );
    }
    if table_exists(conn, "nodes") && table_exists(conn, "vector_embeddings") {
        let missing_vecs = if let Some(w) = ws {
            if table_exists(conn, "nodes_v2") {
                collect_string_col_param(
                    conn,
                    "SELECT n.id FROM nodes n
                     LEFT JOIN nodes_v2 nv ON nv.id = n.id
                     LEFT JOIN vector_embeddings v ON v.item_id = n.id
                     WHERE n.type IN ('Document', 'File', 'Chunk', 'Note', 'Fact')
                       AND v.item_id IS NULL
                       AND COALESCE(NULLIF(TRIM(nv.workspace_id), ''), NULLIF(TRIM(json_extract(n.metadata_json, '$.workspace_id')), ''), 'personal') = ?1
                       AND LENGTH(TRIM(COALESCE(n.title, '') || COALESCE(n.summary, ''))) > 0",
                    w,
                )?
            } else {
                collect_string_col_param(
                    conn,
                    "SELECT n.id FROM nodes n
                     LEFT JOIN vector_embeddings v ON v.item_id = n.id
                     WHERE n.type IN ('Document', 'File', 'Chunk', 'Note', 'Fact')
                       AND v.item_id IS NULL
                       AND COALESCE(NULLIF(TRIM(json_extract(n.metadata_json, '$.workspace_id')), ''), 'personal') = ?1
                       AND LENGTH(TRIM(COALESCE(n.title, '') || COALESCE(n.summary, ''))) > 0",
                    w,
                )?
            }
        } else {
            collect_string_col(
                conn,
                "SELECT n.id FROM nodes n
                 LEFT JOIN vector_embeddings v ON v.item_id = n.id
                 WHERE n.type IN ('Document', 'File', 'Chunk', 'Note', 'Fact')
                   AND v.item_id IS NULL
                   AND LENGTH(TRIM(COALESCE(n.title, '') || COALESCE(n.summary, ''))) > 0",
            )?
        };
        push_finding(
            &mut findings,
            IntegrityIssueKind::IndexDriftVector,
            IntegritySeverity::Info,
            RepairSafety::RequiresApproval,
            missing_vecs,
            "Searchable nodes lacking corresponding vector_embeddings entries.".to_string(),
            "Enqueue background vector indexing for missing nodes.",
        );
    }

    // 8. Cyclic DAG-invariant relationships & `superseded_by` chains.
    let mut dag_edges: Vec<(String, String, String)> = Vec::new();
    if table_exists(conn, "edges") {
        let type_list = DAG_EDGE_TYPES
            .iter()
            .map(|t| format!("'{t}'"))
            .collect::<Vec<_>>()
            .join(",");
        if let Some(w) = ws {
            let sql = if table_exists(conn, "nodes_v2") {
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
            let mut stmt = conn.prepare(&sql)?;
            for row in stmt
                .query_map(params![w], |r| {
                    Ok((
                        r.get::<_, String>(0)?,
                        r.get::<_, String>(1)?,
                        r.get::<_, String>(2)?,
                    ))
                })?
                .filter_map(Result::ok)
            {
                dag_edges.push(row);
            }
        } else {
            let mut stmt = conn.prepare(&format!(
                "SELECT id, from_node, to_node FROM edges WHERE UPPER(type) IN ({type_list})"
            ))?;
            for row in stmt
                .query_map([], |r| {
                    Ok((
                        r.get::<_, String>(0)?,
                        r.get::<_, String>(1)?,
                        r.get::<_, String>(2)?,
                    ))
                })?
                .filter_map(Result::ok)
            {
                dag_edges.push(row);
            }
        }
    }
    if table_exists(conn, "nodes_v2") {
        if let Some(w) = ws {
            let mut stmt = conn.prepare(
                "SELECT 'superseded_by:' || id, id, superseded_by
                 FROM nodes_v2
                 WHERE superseded_by IS NOT NULL AND TRIM(superseded_by) != ''
                   AND COALESCE(NULLIF(TRIM(workspace_id), ''), 'personal') = ?1",
            )?;
            for row in stmt
                .query_map(params![w], |r| {
                    Ok((
                        r.get::<_, String>(0)?,
                        r.get::<_, String>(1)?,
                        r.get::<_, String>(2)?,
                    ))
                })?
                .filter_map(Result::ok)
            {
                dag_edges.push(row);
            }
        } else {
            let mut stmt = conn.prepare(
                "SELECT 'superseded_by:' || id, id, superseded_by
                 FROM nodes_v2
                 WHERE superseded_by IS NOT NULL AND TRIM(superseded_by) != ''",
            )?;
            for row in stmt
                .query_map([], |r| {
                    Ok((
                        r.get::<_, String>(0)?,
                        r.get::<_, String>(1)?,
                        r.get::<_, String>(2)?,
                    ))
                })?
                .filter_map(Result::ok)
            {
                dag_edges.push(row);
            }
        }
    }
    let cyclic_ids = detect_cycles(&dag_edges);
    push_finding(
        &mut findings,
        IntegrityIssueKind::CyclicRelationship,
        IntegritySeverity::Critical,
        RepairSafety::RequiresApproval,
        cyclic_ids,
        "Cycle detected in DAG-invariant relationships (SUPERSEDES/PART_OF/DEPENDS_ON/VERSION_OF/DERIVED_FROM)."
            .to_string(),
        "Break cycle edges after manual inspection.",
    );

    let mut counts_by_kind = BTreeMap::new();
    let mut critical_issues = 0usize;
    let mut warning_issues = 0usize;
    let mut total_issues = 0usize;
    for f in &findings {
        let key = serde_json::to_value(f.kind)
            .ok()
            .and_then(|v| v.as_str().map(str::to_string))
            .unwrap_or_else(|| format!("{:?}", f.kind));
        *counts_by_kind.entry(key).or_insert(0) += f.count;
        total_issues += f.count;
        match f.severity {
            IntegritySeverity::Critical => critical_issues += f.count,
            IntegritySeverity::Warning => warning_issues += f.count,
            IntegritySeverity::Info => {}
        }
    }

    Ok(IntegrityReport {
        healthy: critical_issues == 0 && warning_issues == 0 && sqlite_integrity_ok,
        sqlite_integrity_ok,
        foreign_key_violations,
        total_issues,
        critical_issues,
        warning_issues,
        findings,
        counts_by_kind,
    })
}

mod repair;
pub use repair::{execute_integrity_repair, plan_integrity_repair};
