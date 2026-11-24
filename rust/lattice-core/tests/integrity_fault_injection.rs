//! Fault-injection test suite for P0.2 Knowledge Graph Data Integrity Checker.

use std::collections::{BTreeSet, HashSet};

use lattice_core::graph_write::schema::bootstrap;
use lattice_core::integrity::{
    check_graph_integrity, execute_integrity_repair, IntegrityIssueKind, RepairOptions,
};
use rusqlite::Connection;

fn init_db(conn: &mut Connection) {
    let tx = conn.transaction().expect("tx");
    bootstrap(&tx, "2026-01-01T00:00:00").expect("bootstrap");
    tx.commit().expect("commit");
}

#[test]
fn fault_injection_detects_all_corruption_patterns_and_repairs_safely() {
    let dir = tempfile::tempdir().expect("tempdir");
    let db_path = dir.path().join("brain.db");
    let backup_path = dir.path().join("pre_repair_backup.db");
    let mut conn = Connection::open(&db_path).expect("open db");
    init_db(&mut conn);

    // Disable FKs temporarily to inject real corruption patterns.
    conn.execute_batch("PRAGMA foreign_keys = OFF;").unwrap();

    // Valid nodes across two workspaces + one node missing from nodes_v2 & node_fts.
    conn.execute(
        "INSERT INTO nodes(id, type, title, summary, raw_json, metadata_json, created_at, updated_at)
         VALUES ('doc-a', 'Document', 'Alpha', 'Alpha text', '{}', '{\"workspace_id\":\"ws-a\",\"source_uri\":\"file:///a.md\"}', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
        [],
    )
    .unwrap();
    conn.execute(
        "INSERT INTO nodes(id, type, title, summary, raw_json, metadata_json, created_at, updated_at)
         VALUES ('doc-b', 'Document', 'Beta', 'Beta text', '{}', '{\"workspace_id\":\"ws-b\",\"source_uri\":\"file:///b.md\"}', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
        [],
    )
    .unwrap();
    conn.execute(
        "INSERT INTO nodes(id, type, title, summary, raw_json, metadata_json, created_at, updated_at)
         VALUES ('doc-drift', 'Note', 'Drift Note', 'Missing from v2 and fts', '{}', '{\"workspace_id\":\"ws-a\"}', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
        [],
    )
    .unwrap();
    conn.execute(
        "INSERT INTO nodes_v2(id, type, label, summary, attrs, workspace_id, importance_score, created_at, updated_at)
         VALUES ('doc-a', 'Document', 'Alpha', 'Alpha text', '{}', 'ws-a', 0.8, '2026-01-01', '2026-01-01'),
                ('doc-b', 'Document', 'Beta', 'Beta text', '{}', 'ws-b', 0.8, '2026-01-01', '2026-01-01')",
        [],
    )
    .unwrap();
    conn.execute(
        "INSERT INTO node_fts(node_id, title, summary) VALUES ('doc-a', 'Alpha', 'Alpha text'), ('doc-b', 'Beta', 'Beta text')",
        [],
    )
    .unwrap();

    conn.execute("DELETE FROM node_fts WHERE node_id = 'doc-drift'", [])
        .unwrap();

    // Inject: dangling edge, cross-workspace edge, cycle, orphan chunk, orphan vector, missing file.
    conn.execute(
        "INSERT INTO edges(id, from_node, to_node, type, weight, metadata_json, created_at)
         VALUES ('e-dangling', 'doc-a', 'ghost-node', 'RELATES_TO', 1.0, '{}', '2026-01-01'),
                ('e-cross-ws', 'doc-a', 'doc-b', 'RELATES_TO', 1.0, '{}', '2026-01-01'),
                ('e-cyc-1', 'doc-a', 'doc-drift', 'DEPENDS_ON', 1.0, '{}', '2026-01-01'),
                ('e-cyc-2', 'doc-drift', 'doc-a', 'DEPENDS_ON', 1.0, '{}', '2026-01-01')",
        [],
    )
    .unwrap();
    conn.execute(
        "INSERT INTO chunks(id, source_node, text, metadata_json, created_at)
         VALUES ('chunk-orphan', 'ghost-doc', 'orphan text', '{}', '2026-01-01')",
        [],
    )
    .unwrap();
    conn.execute(
        "INSERT INTO vector_embeddings(item_id, item_type, source_node, text_hash, embedding, embedding_dim, embedding_model, metadata_json, indexed_at)
         VALUES ('vec-orphan', 'node', 'ghost-doc', 'h', X'0000803F', 1, 'test', '{}', '2026-01-01')",
        [],
    )
    .unwrap();
    conn.execute(
        "INSERT INTO knowledge_sources(id, root_path, os_type, status, consent_json, created_at, updated_at)
         VALUES ('src-1', '/nonexistent', 'macos', 'active', '{}', '2026-01-01', '2026-01-01')",
        [],
    )
    .unwrap();
    conn.execute(
        "INSERT INTO local_file_index(id, source_id, os_type, root_path, file_path, relative_path, file_name, extension, status, graph_node_id, deleted, metadata_json)
         VALUES ('lfi-1', 'src-1', 'macos', '/nonexistent', '/nonexistent/lattice_missing_file_99999.md', 'lattice_missing_file_99999.md', 'lattice_missing_file_99999.md', '.md', 'indexed', 'doc-a', 0, '{}')",
        [],
    )
    .unwrap();

    let report = check_graph_integrity(&conn).expect("check");
    assert!(!report.healthy);
    let kinds: HashSet<IntegrityIssueKind> = report.findings.iter().map(|f| f.kind).collect();
    assert!(kinds.contains(&IntegrityIssueKind::DanglingEdge));
    assert!(kinds.contains(&IntegrityIssueKind::CrossWorkspaceLeak));
    assert!(kinds.contains(&IntegrityIssueKind::CyclicRelationship));
    assert!(kinds.contains(&IntegrityIssueKind::OrphanChunk));
    assert!(kinds.contains(&IntegrityIssueKind::OrphanVector));
    assert!(kinds.contains(&IntegrityIssueKind::MissingFilePath));
    assert!(kinds.contains(&IntegrityIssueKind::IndexDriftV2));
    assert!(kinds.contains(&IntegrityIssueKind::IndexDriftFts));

    // Dry-run preview must not mutate DB.
    let dry = execute_integrity_repair(
        &mut conn,
        &RepairOptions {
            dry_run: true,
            approved_actions: BTreeSet::new(),
            backup_path: None,
        },
    )
    .expect("dry run");
    assert!(dry.dry_run);
    assert!(dry.plan.blast_radius.safe_auto_actions >= 4);
    assert!(dry.plan.blast_radius.approval_gated_actions >= 3);
    assert_eq!(dry.rows_repaired, 0);

    // SafeAuto only: repairs safe issues, leaves approval-gated issues untouched, writes backup.
    let safe = execute_integrity_repair(
        &mut conn,
        &RepairOptions {
            dry_run: false,
            approved_actions: BTreeSet::new(),
            backup_path: Some(backup_path.to_string_lossy().to_string()),
        },
    )
    .expect("safe repair");
    assert!(backup_path.exists());
    assert!(safe
        .skipped_approval_actions
        .contains(&"sever_cross_workspace_edges".to_string()));
    assert!(safe
        .skipped_approval_actions
        .contains(&"break_relationship_cycles".to_string()));

    // Execute approved repairs.
    let approved: BTreeSet<String> = [
        "sever_cross_workspace_edges",
        "break_relationship_cycles",
        "prune_missing_local_file_index",
    ]
    .into_iter()
    .map(str::to_string)
    .collect();
    let full = execute_integrity_repair(
        &mut conn,
        &RepairOptions {
            dry_run: false,
            approved_actions: approved,
            backup_path: None,
        },
    )
    .expect("approved repair");
    let after = check_graph_integrity(&conn).expect("post check");
    assert!(after.healthy, "remaining findings: {:?}", after.findings);
    assert_eq!(full.post_check_total_issues, after.total_issues);
}
