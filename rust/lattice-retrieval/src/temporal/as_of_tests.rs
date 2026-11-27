use std::collections::BTreeSet;

use lattice_core::parse_iso;
use rusqlite::Connection;
use serde_json::{json, Value};

use super::*;
use crate::service::Scope;

fn now() -> f64 {
    parse_iso(Some("2026-11-26T15:30:00")).unwrap()
}

fn as_of_of(query: &str) -> Option<(String, Option<String>, Option<String>)> {
    parse_time_intent(query, now())
        .as_of
        .map(|w| (w.label.clone(), w.since_iso(), w.until_iso()))
}

fn span(label: &str, since: &str, until: &str) -> Option<(String, Option<String>, Option<String>)> {
    Some((label.into(), Some(since.into()), Some(until.into())))
}

#[test]
fn as_of_and_year_phrases_become_point_in_time_spans_not_updated_at_windows() {
    assert_eq!(
        as_of_of("Who owned Atlas as of 2024-06-01?"),
        span("as_of", "2024-06-01T00:00:00", "2024-06-02T00:00:00")
    );
    let parsed = parse_time_intent("Who owned Atlas as of 2024-06-01?", now());
    assert!(parsed.window.is_none());
    assert!(!parsed.has_time_signal());
    assert_eq!(parsed.search_query, "Who owned Atlas ?");
    assert_eq!(
        as_of_of("What was the legacy port in 2024 before the migration?"),
        span("as_of_year", "2024-01-01T00:00:00", "2025-01-01T00:00:00")
    );
    assert_eq!(
        as_of_of("2024년 3월 기준 담당자"),
        span("as_of", "2024-03-01T00:00:00", "2024-04-01T00:00:00")
    );
    assert_eq!(
        as_of_of("2025-02-03 기준 담당자"),
        span("as_of", "2025-02-03T00:00:00", "2025-02-04T00:00:00")
    );
    assert_eq!(
        as_of_of("2024년 구 포트"),
        span("as_of_year", "2024-01-01T00:00:00", "2025-01-01T00:00:00")
    );
    assert_eq!(
        as_of_of("as of 2023"),
        span("as_of", "2023-01-01T00:00:00", "2024-01-01T00:00:00")
    );
    for plain in [
        "Who is the current 2026 owner of Atlas?",
        "port 4825 기준",
        "2024년 3월 회의",
        "2024년 이후 변경",
        "in 2024-06 meeting",
    ] {
        assert_eq!(as_of_of(plain), None, "{plain}");
    }
    assert!(parse_time_intent("in 2024-06 meeting", now())
        .window
        .is_some());
}

fn store() -> Connection {
    let conn = Connection::open_in_memory().unwrap();
    conn.execute_batch(
        "CREATE TABLE nodes(id TEXT PRIMARY KEY, type TEXT, title TEXT, summary TEXT,
                            metadata_json TEXT, created_at TEXT, updated_at TEXT);
         CREATE TABLE nodes_v2(id TEXT PRIMARY KEY, workspace_id TEXT, owner_id TEXT,
                               superseded_by TEXT, valid_from TEXT, valid_to TEXT);
         INSERT INTO nodes VALUES ('old', 'Document', 'Owner 2024', 'Kim owns it', '{\"superseded_by\":\"new\"}', '2026-04-01T00:00:00', '2026-04-01T00:00:00');
         INSERT INTO nodes VALUES ('new', 'Document', 'Owner 2026', 'Lee owns it', '{}', '2026-04-01T00:00:00', '2026-04-01T00:00:00');
         INSERT INTO nodes VALUES ('plain', 'Document', 'Owner notes', 'notes', '{\"valid_from\":\"2023-01-01T00:00:00Z\"}', '2026-04-01T00:00:00', '2026-04-01T00:00:00');
         INSERT INTO nodes_v2 VALUES ('old', 'w', NULL, 'new', '2024-01-01T00:00:00Z', '2026-01-15T00:00:00Z');
         INSERT INTO nodes_v2 VALUES ('new', 'w', NULL, NULL, '2026-01-15T00:00:00Z', NULL);
         INSERT INTO nodes_v2 VALUES ('plain', 'w', NULL, NULL, NULL, NULL);",
    )
    .unwrap();
    conn
}

fn hit(id: &str, score: f64) -> Value {
    json!({"id": id, "node_id": id, "type": "Document", "score": score, "updated_at": "2026-04-01T00:00:00"})
}

fn ids(matches: &[Value]) -> Vec<&str> {
    matches
        .iter()
        .map(|m| m["node_id"].as_str().unwrap())
        .collect()
}

#[test]
fn validity_is_filtered_and_supersession_judged_at_as_of_with_relevance_order() {
    let conn = store();
    let ranking = TemporalRanking::default();
    let mut past =
        TemporalPlan::resolve(&conn, "who owned it as of 2024-06-01", now(), &ranking).unwrap();
    assert_eq!(past.sort, SortBy::Relevance);
    assert!(past.engine_includes_historical());
    assert!(!past.include_historical());
    assert!(past.fetch_limit(10) > 10);
    let kept = past
        .pre_rerank(
            &conn,
            vec![hit("new", 0.9), hit("old", 0.5), hit("plain", 0.4)],
            true,
            false,
        )
        .unwrap();
    assert_eq!(ids(&kept), ["old", "plain"]);
    assert_eq!(kept[0]["status"], "active");
    assert_eq!(past.report["validity"]["valid"], 2);
    let report = past.report_json(false);
    assert_eq!(report["as_of"]["source"], "query");
    assert_eq!(report["as_of"]["evaluated_at"], "2024-06-01T23:59:59");

    let mut current = TemporalPlan::resolve(&conn, "2026년 담당자", now(), &ranking).unwrap();
    let kept = current
        .pre_rerank(&conn, vec![hit("old", 0.9), hit("new", 0.5)], true, false)
        .unwrap();
    assert_eq!(ids(&kept), ["new"]);
    assert_eq!(current.report["superseded"]["excluded"], 1);

    let pinned = ranking
        .clone()
        .with_as_of(Some("2024-06-01T00:00:00Z".into()));
    let mut explicit = TemporalPlan::resolve(&conn, "2026년 담당자", now(), &pinned).unwrap();
    assert_eq!(explicit.as_of_source, Some("explicit"));
    let kept = explicit
        .pre_rerank(&conn, vec![hit("new", 0.9), hit("old", 0.5)], true, false)
        .unwrap();
    assert_eq!(ids(&kept), ["old"]);

    let mut ancient = TemporalPlan::resolve(&conn, "as of 2001", now(), &ranking).unwrap();
    let kept = ancient
        .pre_rerank(&conn, vec![hit("new", 0.9), hit("old", 0.5)], true, false)
        .unwrap();
    assert_eq!(ids(&kept), ["new", "old"]);
    assert_eq!(
        ancient.report["validity"]["fallback"],
        "no_candidates_valid_at_as_of"
    );
    assert_eq!(
        TemporalRanking::default()
            .with_as_of(Some("  ".into()))
            .as_of,
        None
    );
}

fn tenants() -> Connection {
    let conn = Connection::open_in_memory().unwrap();
    conn.execute_batch(
        "CREATE TABLE nodes(id TEXT PRIMARY KEY, type TEXT, title TEXT, summary TEXT,
                            metadata_json TEXT, created_at TEXT, updated_at TEXT);
         CREATE TABLE nodes_v2(id TEXT PRIMARY KEY, workspace_id TEXT, owner_id TEXT, summary TEXT,
                               created_at TEXT, updated_at TEXT);
         INSERT INTO nodes VALUES ('message:b', 'Message', 'chat', 'we shipped v7 last week', '{}', '2026-11-20T09:00:00', '2026-11-20T09:00:00');
         INSERT INTO nodes VALUES ('foreign', 'Document', 'Plan', 'v7 rollout', '{}', '2026-11-19T09:00:00', '2026-11-19T09:00:00');
         INSERT INTO nodes VALUES ('mine', 'Document', 'Refund', 'refund notes', '{}', '2026-11-21T09:00:00', '2026-11-21T09:00:00');
         INSERT INTO nodes_v2 VALUES ('message:b', 'team', 'b@example.com', 'x', '2026-11-20T09:00:00', '2026-11-20T09:00:00');
         INSERT INTO nodes_v2 VALUES ('foreign', 'other', NULL, 'x', '2026-11-19T09:00:00', '2026-11-19T09:00:00');
         INSERT INTO nodes_v2 VALUES ('mine', 'team', 'a@example.com', 'x', '2026-11-21T09:00:00', '2026-11-21T09:00:00');",
    )
    .unwrap();
    conn
}

fn viewer_a() -> Scope {
    Scope {
        allowed_workspaces: Some(lattice_core::with_viewer(
            BTreeSet::from(["team".to_string()]),
            Some("a@example.com"),
        )),
        include_legacy_global: false,
    }
}

#[test]
fn the_prefilter_never_reads_another_tenants_or_users_private_nodes() {
    let conn = tenants();
    let scoped = TemporalRanking::default().with_scope(viewer_a());
    let plan = TemporalPlan::resolve(&conn, "since v7 refund", now(), &scoped).unwrap();
    assert_eq!(plan.report["version_anchor"]["status"], "not_found");
    assert_eq!(plan.report["version_anchor"]["node_id"], Value::Null);
    let windowed = TemporalPlan::resolve(&conn, "past week refund", now(), &scoped).unwrap();
    let visible: Vec<&str> = windowed
        .window_ids
        .as_ref()
        .unwrap()
        .iter()
        .map(String::as_str)
        .collect();
    assert_eq!(visible, ["mine"]);

    let unscoped =
        TemporalPlan::resolve(&conn, "since v7 refund", now(), &TemporalRanking::default())
            .unwrap();
    assert_eq!(unscoped.report["version_anchor"]["node_id"], "foreign");
    let team_only = TemporalRanking::default().with_scope(Scope::for_workspace("team"));
    let shared = TemporalPlan::resolve(&conn, "since v7 refund", now(), &team_only).unwrap();
    assert_eq!(shared.report["version_anchor"]["node_id"], "message:b");
}
