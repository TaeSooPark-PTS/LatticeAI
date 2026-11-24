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

use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};
use std::path::Path;

use rusqlite::{params, Connection};
use serde::{Deserialize, Serialize};

use crate::db::CoreError;
use crate::graph_write::schema::backup_database;

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

/// Run the full Knowledge Graph data-integrity audit against `conn`.
pub fn check_graph_integrity(conn: &Connection) -> Result<IntegrityReport, CoreError> {
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
    let foreign_key_violations = fk_violations.len();
    push_finding(
        &mut findings,
        IntegrityIssueKind::ForeignKeyViolation,
        IntegritySeverity::Critical,
        RepairSafety::SafeAuto,
        fk_violations,
        "Rows failed SQLite PRAGMA foreign_key_check.".to_string(),
        "Remove dangling child rows after snapshot backup.",
    );

    // 2. Dangling edges (`edges`, `edges_v2`, `edge_occurrences`).
    if table_exists(conn, "edges") && table_exists(conn, "nodes") {
        let dangling = collect_string_col(
            conn,
            "SELECT e.id FROM edges e
             LEFT JOIN nodes n1 ON n1.id = e.from_node
             LEFT JOIN nodes n2 ON n2.id = e.to_node
             WHERE n1.id IS NULL OR n2.id IS NULL",
        )?;
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
        let dangling_v2 = collect_string_col(
            conn,
            "SELECT e.id FROM edges_v2 e
             LEFT JOIN nodes_v2 n1 ON n1.id = e.source
             LEFT JOIN nodes_v2 n2 ON n2.id = e.target
             WHERE n1.id IS NULL OR n2.id IS NULL",
        )?;
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
    if table_exists(conn, "edge_occurrences") && table_exists(conn, "edges_v2") {
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
        let orphan_chunks = collect_string_col(
            conn,
            "SELECT c.id FROM chunks c
             LEFT JOIN nodes n ON n.id = c.source_node
             WHERE c.source_node IS NULL OR c.source_node = '' OR n.id IS NULL",
        )?;
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
        let orphan_vecs = collect_string_col(conn, sql)?;
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
        let orphan_prov = collect_string_col(
            conn,
            "SELECT CAST(p.id AS TEXT) FROM ingestion_provenance p
             LEFT JOIN nodes n ON n.id = p.node_id
             WHERE n.id IS NULL",
        )?;
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
        let missing_prov = collect_string_col(
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
        )?;
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
        let leaks = collect_string_col(
            conn,
            "SELECT e.id FROM edges e
             JOIN nodes_v2 a ON a.id = e.from_node
             JOIN nodes_v2 b ON b.id = e.to_node
             WHERE COALESCE(NULLIF(TRIM(a.workspace_id), ''), 'personal')
                != COALESCE(NULLIF(TRIM(b.workspace_id), ''), 'personal')",
        )?;
        push_finding(
            &mut findings,
            IntegrityIssueKind::CrossWorkspaceLeak,
            IntegritySeverity::Critical,
            RepairSafety::RequiresApproval,
            leaks,
            "Edges linking nodes that belong to different workspaces.".to_string(),
            "Sever cross-workspace edges after explicit review.",
        );
    }

    // 5. Duplicate natural keys (active source_uri collisions).
    if table_exists(conn, "nodes") && table_exists(conn, "nodes_v2") {
        let dup_uris = collect_string_col(
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
        )?;
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
        let mut stmt = conn.prepare(
            "SELECT id, file_path, COALESCE(graph_node_id, '') FROM local_file_index WHERE deleted = 0",
        )?;
        let missing_paths: Vec<String> = stmt
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
        let v2_missing = collect_string_col(
            conn,
            "SELECT n.id FROM nodes n LEFT JOIN nodes_v2 v ON v.id = n.id WHERE v.id IS NULL",
        )?;
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
        let fts_drift = collect_string_col(
            conn,
            "SELECT n.id FROM nodes n
             LEFT JOIN node_fts f ON f.node_id = n.id
             WHERE f.node_id IS NULL",
        )?;
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
        let missing_vecs = collect_string_col(
            conn,
            "SELECT n.id FROM nodes n
             LEFT JOIN vector_embeddings v ON v.item_id = n.id
             WHERE n.type IN ('Document', 'File', 'Chunk', 'Note', 'Fact')
               AND v.item_id IS NULL
               AND LENGTH(TRIM(COALESCE(n.title, '') || COALESCE(n.summary, ''))) > 0",
        )?;
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
    if table_exists(conn, "nodes_v2") {
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
        let will_execute = match finding.safety {
            RepairSafety::SafeAuto => true,
            RepairSafety::RequiresApproval => options.approved_actions.contains(action_id),
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
pub fn execute_integrity_repair(
    conn: &mut Connection,
    options: &RepairOptions,
) -> Result<RepairExecutionResult, CoreError> {
    let pre_report = check_graph_integrity(conn)?;
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
            IntegrityIssueKind::DanglingEdge => tx.execute(
                "DELETE FROM edges WHERE from_node NOT IN (SELECT id FROM nodes)
                 OR to_node NOT IN (SELECT id FROM nodes)",
                [],
            )?,
            IntegrityIssueKind::DanglingEdgeV2 => tx.execute(
                "DELETE FROM edges_v2 WHERE source NOT IN (SELECT id FROM nodes_v2)
                 OR target NOT IN (SELECT id FROM nodes_v2)",
                [],
            )?,
            IntegrityIssueKind::DanglingEdgeOccurrence => tx.execute(
                "DELETE FROM edge_occurrences WHERE edge_id NOT IN (SELECT id FROM edges_v2)",
                [],
            )?,
            IntegrityIssueKind::OrphanChunk => tx.execute(
                "DELETE FROM chunks WHERE source_node IS NULL OR source_node = ''
                 OR source_node NOT IN (SELECT id FROM nodes)",
                [],
            )?,
            IntegrityIssueKind::OrphanVector => {
                if table_exists(&tx, "chunks") {
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
            IntegrityIssueKind::OrphanProvenance => tx.execute(
                "DELETE FROM ingestion_provenance WHERE node_id NOT IN (SELECT id FROM nodes)",
                [],
            )?,
            IntegrityIssueKind::IndexDriftV2 => tx.execute(
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
            )?,
            IntegrityIssueKind::IndexDriftFts => tx.execute(
                "INSERT INTO node_fts(node_id, title, summary)
                 SELECT n.id, COALESCE(n.title, ''), COALESCE(n.summary, '')
                 FROM nodes n
                 LEFT JOIN node_fts f ON f.node_id = n.id
                 WHERE f.node_id IS NULL",
                [],
            )?,
            IntegrityIssueKind::CrossWorkspaceLeak => {
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
            IntegrityIssueKind::CyclicRelationship => {
                let mut broken = 0usize;
                let type_list = DAG_EDGE_TYPES
                    .iter()
                    .map(|t| format!("'{t}'"))
                    .collect::<Vec<_>>()
                    .join(",");
                let dag_edges: Vec<(String, String, String)> = {
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
                let stale_ids: Vec<String> = {
                    let mut stmt = tx.prepare(
                        "SELECT id, file_path FROM local_file_index WHERE deleted = 0",
                    )?;
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
                    pruned += tx.execute(
                        "DELETE FROM local_file_index WHERE id = ?1",
                        params![id],
                    )?;
                }
                pruned
            }
            _ => 0,
        };
        rows_repaired += changed;
        executed_actions.push(action.action_id.clone());
    }

    tx.commit()?;
    let post = check_graph_integrity(conn)?;

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
