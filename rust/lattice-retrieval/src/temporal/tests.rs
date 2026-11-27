use lattice_core::parse_iso;
use rusqlite::Connection;
use serde_json::{json, Value};

use super::*;

fn now() -> f64 {
    parse_iso(Some("2026-11-26T15:30:00")).unwrap()
}

fn window_of(query: &str) -> Option<(String, Option<String>, Option<String>)> {
    parse_time_intent(query, now())
        .window
        .map(|w| (w.label.clone(), w.since_iso(), w.until_iso()))
}

fn some(
    label: &str,
    since: Option<&str>,
    until: Option<&str>,
) -> Option<(String, Option<String>, Option<String>)> {
    Some((
        label.to_string(),
        since.map(str::to_string),
        until.map(str::to_string),
    ))
}

#[test]
fn sort_by_accepts_the_three_modes_and_refuses_anything_else() {
    assert_eq!(SortBy::parse(None), Ok(SortBy::Auto));
    assert_eq!(SortBy::parse(Some(" ")), Ok(SortBy::Auto));
    assert_eq!(SortBy::parse(Some("Relevance")), Ok(SortBy::Relevance));
    assert_eq!(SortBy::parse(Some("newest")), Ok(SortBy::Newest));
    assert_eq!(SortBy::parse(Some("updated_at")), Ok(SortBy::Newest));
    assert!(SortBy::parse(Some("oldest"))
        .unwrap_err()
        .contains("oldest"));
    assert_eq!(SortBy::Auto.as_str(), "auto");
    let gate = RelevanceGate::from_lookup(|key| match key {
        SORT_TOP_K_ENV => Some("40".into()),
        MIN_RELEVANCE_ENV => Some("-1".into()),
        _ => None,
    });
    assert_eq!((gate.top_k, gate.min_ratio), (15, 0.0));
    assert_eq!(RelevanceGate::from_lookup(|_| Some("3".into())).top_k, 10);
    assert_eq!(
        RelevanceGate::from_lookup(|_| None),
        RelevanceGate::default()
    );
    let ranking = TemporalRanking::from_lookup(|_| None).with_request(
        SortBy::Newest,
        true,
        Some(" ".into()),
        Some("2026-01-01".into()),
    );
    assert_eq!(ranking.since, None);
    assert_eq!(ranking.until.as_deref(), Some("2026-01-01"));
    assert!(ranking.include_historical);
}

#[test]
fn korean_and_english_relative_phrases_become_calendar_windows() {
    assert_eq!(
        window_of("지난주 회의 기록"),
        some(
            "last_week",
            Some("2026-11-16T00:00:00"),
            Some("2026-11-23T00:00:00")
        )
    );
    assert_eq!(
        window_of("what happened last week"),
        window_of("지난 주 회의")
    );
    assert_eq!(
        window_of("이번 주 할 일"),
        some("this_week", Some("2026-11-23T00:00:00"), None)
    );
    assert_eq!(
        window_of("이번 달 매출"),
        some("this_month", Some("2026-11-01T00:00:00"), None)
    );
    assert_eq!(
        window_of("지난달 결정"),
        some(
            "last_month",
            Some("2026-10-01T00:00:00"),
            Some("2026-11-01T00:00:00")
        )
    );
    assert_eq!(
        window_of("오늘 메모"),
        some("today", Some("2026-11-26T00:00:00"), None)
    );
    assert_eq!(
        window_of("yesterday notes"),
        some(
            "yesterday",
            Some("2026-11-25T00:00:00"),
            Some("2026-11-26T00:00:00")
        )
    );
    assert_eq!(
        window_of("the day before yesterday"),
        some(
            "day_before_yesterday",
            Some("2026-11-24T00:00:00"),
            Some("2026-11-25T00:00:00")
        )
    );
    assert_eq!(
        window_of("작년 목표"),
        some(
            "last_year",
            Some("2025-01-01T00:00:00"),
            Some("2026-01-01T00:00:00")
        )
    );
    assert_eq!(
        window_of("올해 목표"),
        some("this_year", Some("2026-01-01T00:00:00"), None)
    );
    assert_eq!(
        window_of("past week changes"),
        some("past_week", Some("2026-11-19T15:30:00"), None)
    );
    assert_eq!(
        window_of("최근 3일 변경"),
        some("last_n_days", Some("2026-11-23T15:30:00"), None)
    );
    assert_eq!(
        window_of("past 2 weeks"),
        some("last_n_days", Some("2026-11-12T15:30:00"), None)
    );
    assert_eq!(
        window_of("지난 1개월"),
        some("last_n_days", Some("2026-10-27T15:30:00"), None)
    );
    assert_eq!(
        window_of("최근 결정 사항"),
        some("recent", Some("2026-10-27T15:30:00"), None)
    );
    assert_eq!(window_of("recently merged"), window_of("최근 merged"));
    assert_eq!(window_of("hybrid retrieval ranking"), None);
}

#[test]
fn absolute_dates_and_versions_are_stripped_from_the_search_text() {
    let since = parse_time_intent("refund policy since 2026-03-05", now());
    assert_eq!(since.search_query, "refund policy");
    assert_eq!(
        since.window.unwrap().since_iso().as_deref(),
        Some("2026-03-05T00:00:00")
    );
    let before = parse_time_intent("before 2026-02 budget", now());
    assert_eq!(
        before.window.unwrap().until_iso().as_deref(),
        Some("2026-02-01T00:00:00")
    );
    let ko = parse_time_intent("2026년 3월 이후 환불 정책", now());
    assert_eq!(ko.window.as_ref().unwrap().label, "since_date");
    assert_eq!(ko.search_query, "환불 정책");
    let until = parse_time_intent("2026-03-05까지 일정", now())
        .window
        .unwrap();
    assert_eq!(until.until_iso().as_deref(), Some("2026-03-06T00:00:00"));
    let earlier = parse_time_intent("2026.03 이전 기록", now())
        .window
        .unwrap();
    assert_eq!(
        (earlier.label.as_str(), earlier.until_iso().as_deref()),
        ("before_date", Some("2026-03-01T00:00:00"))
    );
    let on = parse_time_intent("standup 2026-11-02", now())
        .window
        .unwrap();
    assert_eq!(
        (on.since_iso().as_deref(), on.until_iso().as_deref()),
        (Some("2026-11-02T00:00:00"), Some("2026-11-03T00:00:00"))
    );
    let month = parse_time_intent("in 2026-02 planning", now())
        .window
        .unwrap();
    assert_eq!(month.until_iso().as_deref(), Some("2026-03-01T00:00:00"));
    let ko_day = parse_time_intent("2026년 2월 3일 회의", now())
        .window
        .unwrap();
    assert_eq!(ko_day.since_iso().as_deref(), Some("2026-02-03T00:00:00"));
    assert!(parse_time_intent("since 2026-02-30", now())
        .window
        .is_none());
    let version = parse_time_intent("v14 이후 변경점", now());
    assert_eq!(version.version.as_deref(), Some("14"));
    assert_eq!(version.search_query, "변경점");
    assert_eq!(
        parse_time_intent("changes since v14.1", now())
            .version
            .as_deref(),
        Some("14.1")
    );
    assert_eq!(
        parse_time_intent("since version 9", now())
            .version
            .as_deref(),
        Some("9")
    );
    assert_eq!(
        parse_time_intent("since 2026-01-01", now()).search_query,
        "since 2026-01-01"
    );
}

#[test]
fn latest_and_historical_words_are_signals_not_windows() {
    let latest = parse_time_intent("가장 최근 배포 방식", now());
    assert!(latest.latest && latest.window.is_none());
    assert!(latest.has_time_signal());
    assert!(parse_time_intent("latest pricing", now()).latest);
    let history = parse_time_intent("이전 버전 정책은 뭐였지", now());
    assert!(history.historical && !history.has_time_signal());
    assert!(parse_time_intent("what did we previously use", now()).historical);
    assert!(!parse_time_intent("refund policy", now()).historical);
    assert!(!parse_time_intent("refund policy", now()).has_time_signal());
}

pub(crate) fn store() -> Connection {
    let conn = Connection::open_in_memory().unwrap();
    conn.execute_batch(
        "CREATE TABLE nodes(id TEXT PRIMARY KEY, type TEXT, title TEXT, summary TEXT,
                            metadata_json TEXT, created_at TEXT, updated_at TEXT);
         CREATE TABLE edges(id TEXT PRIMARY KEY, from_node TEXT, to_node TEXT, type TEXT);
         INSERT INTO nodes VALUES ('policy_v1', 'Document', 'Refund policy', 'v13 refund 14 days', '{}', '2026-01-10T09:00:00', '2026-01-10T09:00:00');
         INSERT INTO nodes VALUES ('policy_v2', 'Document', 'Refund policy', 'v14 refund 30 days', '{}', '2026-11-18T09:00:00', '2026-11-18T09:00:00');
         INSERT INTO nodes VALUES ('meeting', 'Document', 'Weekly meeting', 'notes', '{}', '2026-11-17 10:00:00', '2026-11-17 10:00:00');
         INSERT INTO nodes VALUES ('old_note', 'Document', 'Old note', 'notes', '{}', '2025-05-01T00:00:00', '2025-05-01T00:00:00');
         INSERT INTO nodes VALUES ('undated', 'Document', 'Undated', 'notes', '{}', '', '');
         INSERT INTO edges VALUES ('s1', 'policy_v2', 'policy_v1', 'SUPERSEDES');",
    )
    .unwrap();
    conn
}

pub(crate) fn hit(id: &str, score: f64, updated_at: &str) -> Value {
    json!({"id": id, "node_id": id, "type": "Document", "score": score, "updated_at": updated_at, "rank": 0})
}

fn ids(matches: &[Value]) -> Vec<String> {
    matches
        .iter()
        .map(|m| m["node_id"].as_str().unwrap().to_string())
        .collect()
}

fn plan(conn: &Connection, query: &str, ranking: &TemporalRanking) -> TemporalPlan {
    TemporalPlan::resolve(conn, query, now(), ranking).unwrap()
}

fn candidates() -> Vec<Value> {
    vec![
        hit("policy_v1", 0.9, "2026-01-10T09:00:00"),
        hit("policy_v2", 0.8, "2026-11-18T09:00:00"),
        hit("meeting", 0.5, "2026-11-17 10:00:00"),
        hit("old_note", 0.4, "2025-05-01T00:00:00"),
        hit("undated", 0.35, ""),
    ]
}

#[test]
fn current_state_queries_drop_superseded_nodes_and_historical_ones_penalise_them() {
    let conn = store();
    let ranking = TemporalRanking::default();
    let mut current = plan(&conn, "refund policy", &ranking);
    let kept = current
        .pre_rerank(&conn, candidates(), true, false)
        .unwrap();
    assert_eq!(ids(&kept), ["policy_v2", "meeting", "old_note", "undated"]);
    assert_eq!(kept[0]["status"], "active");
    assert_eq!(current.report["superseded"]["excluded"], 1);
    let mut history = plan(&conn, "이전 버전 refund policy", &ranking);
    let all = history
        .pre_rerank(&conn, candidates(), true, false)
        .unwrap();
    let old = all.iter().find(|m| m["node_id"] == "policy_v1").unwrap();
    assert_eq!(old["status"], "superseded");
    assert_eq!(old["temporal"]["superseded_by"], "policy_v2");
    assert_eq!(old["score"], json!(0.315));
    assert_eq!(history.report["superseded"]["policy"], "penalize");
    let flagged = TemporalRanking::default().with_request(SortBy::Auto, true, None, None);
    assert!(plan(&conn, "refund policy", &flagged).include_historical());
    let mut lonely = plan(&conn, "refund policy", &ranking);
    let only = lonely
        .pre_rerank(
            &conn,
            vec![hit("policy_v1", 0.9, "2026-01-10T09:00:00")],
            true,
            false,
        )
        .unwrap();
    assert_eq!(ids(&only), ["policy_v1"]);
    assert_eq!(
        lonely.report["superseded"]["fallback"],
        "all_candidates_superseded"
    );
}

#[test]
fn the_prefilter_keeps_in_window_hits_and_falls_back_instead_of_over_filtering() {
    let conn = store();
    let ranking = TemporalRanking::default();
    let mut last_week = plan(&conn, "지난주 meeting", &ranking);
    assert_eq!(last_week.window_ids.as_ref().unwrap().len(), 2);
    assert_eq!(last_week.sort, SortBy::Newest);
    assert!(last_week.include_historical());
    assert_eq!(last_week.fetch_limit(10), 30);
    let kept = last_week
        .pre_rerank(&conn, candidates(), true, false)
        .unwrap();
    assert_eq!(ids(&kept), ["policy_v2", "meeting"]);
    assert_eq!(last_week.report["prefilter"]["applied"], true);

    let mut weak = plan(&conn, "지난주 refund", &ranking);
    let mixed = vec![
        hit("old_note", 1.0, "2025-05-01T00:00:00"),
        hit("meeting", 0.1, "2026-11-17 10:00:00"),
    ];
    let restored = weak.pre_rerank(&conn, mixed, true, false).unwrap();
    assert_eq!(ids(&restored), ["old_note", "meeting"]);
    assert_eq!(
        weak.report["prefilter"]["fallback"],
        "no_relevant_matches_in_window"
    );

    let mut empty = plan(&conn, "since 2030-01-01 refund", &ranking);
    assert_eq!(empty.window_ids.as_ref().unwrap().len(), 0);
    let restored = empty.pre_rerank(&conn, candidates(), true, false).unwrap();
    assert_eq!(restored.len(), 4);
    assert_eq!(
        empty.report["prefilter"]["fallback"],
        "no_documents_in_window"
    );
    let finished = empty.finalize(restored, 10);
    assert_eq!(
        ids(&finished),
        ["policy_v2", "meeting", "old_note", "undated"]
    );

    let mut plain = plan(&conn, "refund", &ranking);
    let all = plain.pre_rerank(&conn, candidates(), true, false).unwrap();
    assert_eq!(all.len(), 4);
    assert_eq!(plain.report["prefilter"]["reason"], "no_time_intent");
    assert_eq!(plain.fetch_limit(500), 100);
}

#[test]
fn a_version_intent_anchors_the_window_at_its_first_mention() {
    let conn = store();
    let ranking = TemporalRanking::default();
    let anchored = plan(&conn, "v14 이후 refund", &ranking);
    assert_eq!(
        anchored.window.as_ref().unwrap().since_iso().as_deref(),
        Some("2026-11-18T09:00:00")
    );
    assert_eq!(anchored.report["version_anchor"]["node_id"], "policy_v2");
    assert_eq!(anchored.window_source, Some("version"));
    let missing = plan(&conn, "since v99 refund", &ranking);
    assert!(missing.window.is_none());
    assert_eq!(missing.report["version_anchor"]["status"], "not_found");
    let explicit = TemporalRanking::default().with_request(
        SortBy::Relevance,
        false,
        Some("2026-11-17T00:00:00".into()),
        None,
    );
    let pinned = plan(&conn, "v14 이후 refund", &explicit);
    assert_eq!(pinned.window_source, Some("explicit"));
    assert_eq!(pinned.sort, SortBy::Relevance);
    let off = TemporalRanking {
        parse_intent: false,
        ..TemporalRanking::default()
    };
    let literal = plan(&conn, " 지난주 meeting ", &off);
    assert!(literal.window.is_none());
    assert_eq!(literal.search_query(), "지난주 meeting");
}

#[test]
fn newest_sort_runs_after_rerank_on_the_relevance_gated_top_k() {
    let conn = store();
    let ranking = TemporalRanking::default().with_request(SortBy::Newest, false, None, None);
    let mut newest = plan(&conn, "notes", &ranking);
    let mut reranked = vec![
        hit("old_note", 0.9, "2025-05-01T00:00:00"),
        hit("undated", 0.8, ""),
        hit("meeting", 0.5, "2026-11-17 10:00:00"),
        hit("policy_v2", 0.1, "2026-11-18T09:00:00"),
    ];
    reranked[3]["rerank_promoted"] = json!(false);
    let mut promoted = hit("ghost", 0.05, "2026-11-20T00:00:00");
    promoted["rerank_promoted"] = json!(true);
    reranked.push(promoted);
    let finished = newest.finalize(reranked, 20);
    assert_eq!(ids(&finished), ["ghost", "meeting", "old_note", "undated"]);
    assert_eq!(finished[0]["rank"], 1);
    assert_eq!(newest.report["sort"]["applied"], "newest");
    assert_eq!(
        newest.report["sort"]["order"],
        "rerank_then_relevance_gate_then_updated_at_desc"
    );

    let many: Vec<Value> = (0..30)
        .map(|i| {
            hit(
                &format!("n{i:02}"),
                1.0,
                &format!("2026-11-{:02}T00:00:00", 1 + i % 28),
            )
        })
        .collect();
    let mut capped = plan(&conn, "notes", &ranking);
    assert_eq!(capped.finalize(many.clone(), 50).len(), 12);
    let mut small = plan(&conn, "notes", &ranking);
    assert_eq!(small.finalize(many.clone(), 3).len(), 3);
    let mut relevance = plan(&conn, "notes", &TemporalRanking::default());
    let kept = relevance.finalize(many, 20);
    assert_eq!(kept.len(), 20);
    assert_eq!(kept[0]["node_id"], "n00");
}

#[test]
fn newest_order_reorders_the_answer_and_never_recruits_from_the_rerank_pool() {
    let conn = store();
    let ranking = TemporalRanking::default().with_request(SortBy::Newest, false, None, None);
    let pool = || {
        vec![
            hit("answer", 0.9, "2026-01-15T00:00:00"),
            hit("sibling", 0.5, "2026-02-01T00:00:00"),
            hit("peer", 0.4, "2026-01-15T00:00:00"),
            hit("off_topic_a", 0.35, "2026-02-01T00:00:00"),
            hit("off_topic_b", 0.33, "2026-02-01T00:00:00"),
            hit("off_topic_c", 0.31, "2026-02-01T00:00:00"),
        ]
    };
    let mut wide = plan(&conn, "notes", &ranking);
    let recruited = wide.finalize(pool(), 20);
    assert_eq!(
        &ids(&recruited)[..3],
        ["sibling", "off_topic_a", "off_topic_b"]
    );
    let mut bounded = plan(&conn, "notes", &ranking);
    let answer = bounded.finalize_within(pool(), 20, 3);
    assert_eq!(ids(&answer), ["sibling", "answer", "peer"]);
    assert_eq!(answer[0]["rank"], 1);
    assert_eq!(bounded.report["sort"]["returned"], 3);

    let mut relevance = plan(&conn, "notes", &TemporalRanking::default());
    let kept = relevance.finalize_within(pool(), 4, 2);
    assert_eq!(ids(&kept), ["answer", "sibling", "peer", "off_topic_a"]);
    let mut clamped = plan(&conn, "notes", &ranking);
    assert_eq!(clamped.finalize_within(pool(), 2, 9).len(), 2);
    let mut floor = plan(&conn, "notes", &ranking);
    assert_eq!(ids(&floor.finalize_within(pool(), 0, 0)), ["answer"]);
}

#[test]
fn engine_reports_carry_the_temporal_block_and_the_original_query() {
    let conn = store();
    let ranking = TemporalRanking::default();
    let report = ranked_search(&conn, "since 2026-11-01 notes", now(), &ranking, 5, false, |call| {
        assert_eq!(call.search_query, "notes");
        assert_eq!(call.fetch_limit, 15);
        assert!(!call.include_historical);
        Ok(json!({"query": call.search_query, "query_class": "fact", "matches": candidates(), "results": []}))
    })
    .unwrap();
    assert_eq!(report["query"], "since 2026-11-01 notes");
    assert_eq!(report["sort_by"], "newest");
    assert_eq!(
        ids(report["matches"].as_array().unwrap()),
        ["policy_v2", "meeting"]
    );
    assert_eq!(report["results"], report["matches"]);
    let decay = report["matches"][0]["recency_decay"].as_f64().unwrap();
    assert!(decay > 0.98 && decay < 1.0, "{decay}");
    let temporal = &report["temporal"];
    assert_eq!(temporal["window"]["source"], "query");
    assert_eq!(temporal["pipeline"][3], "rerank");
    assert_eq!(temporal["recency_decay"]["always_on"], true);
    assert_eq!(
        search_text_for("since 2026-11-01 notes", now(), &ranking),
        "notes"
    );
    let off = TemporalRanking {
        parse_intent: false,
        ..ranking
    };
    assert_eq!(
        search_text_for(" since 2026-11-01 notes ", now(), &off),
        "since 2026-11-01 notes"
    );
}
