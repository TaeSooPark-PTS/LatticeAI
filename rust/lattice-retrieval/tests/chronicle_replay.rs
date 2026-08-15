//! Replay `chronicle` records from `memory_brain.json`.
//!
//! ## This family is the one that can be broken by the calendar
//!
//! `GET /api/chronicle/day/{date}` takes its day from the path, and the fixture
//! writes that day as the `@today` token. The harness used to expand `@today`
//! with `SystemTime::now()`, so the replay asked for *the day it ran on* while
//! every row in the seeded store is stamped `2026-08-14`. It passed once, on the
//! capture date, and failed with `counts.sources: 15 vs 0` every day after —
//! a green suite that quietly turned red on a calendar page turn.
//!
//! `@today` now expands to `CAPTURE_DATE`, which is what it always meant: the
//! day the data is from. That was the harness's own last `SystemTime::now()`;
//! the product clocks were already frozen (`CAPTURE_NOON` for `BrainState`,
//! `Clock::frozen` for auth) and nothing about them changed here. `@ts` in a
//! query likewise stopped being "now" — see `CAPTURE_END_OF_DAY`.

#![allow(dead_code, unused_imports, unused_variables)]
#![allow(clippy::all)]
mod common;

use common::brain::{CAPTURE_DATE, CAPTURE_END_OF_DAY, CAPTURE_NOON};

#[tokio::test]
async fn chronicle_replays_the_python_oracle() {
    let install = common::brain::Install::start().await;
    install.replay_family("chronicle").await;
}

/// The replay asks for a fixed day, and that day is the only one in the store.
///
/// Two independent halves, because either one alone can rot:
///
/// 1. The harness resolves `@today` to a constant. On any day that is not the
///    capture date this assertion is a live falsifier — reintroduce the clock
///    read and it fails immediately rather than at the next release.
/// 2. The committed store really is a single-day capture, and every stamp in it
///    precedes the as-of instant. Re-seed it across two days and this names the
///    reason instead of leaving a replay to disagree about counts.
#[tokio::test]
async fn the_capture_is_one_day_and_the_replay_asks_for_that_day() {
    let install = common::brain::Install::start().await;
    assert_eq!(
        install.today, CAPTURE_DATE,
        "@today must be the capture date, not the calendar's"
    );
    assert!(CAPTURE_NOON.starts_with(CAPTURE_DATE));
    assert!(CAPTURE_END_OF_DAY.starts_with(CAPTURE_DATE));

    let store: std::path::PathBuf = [
        env!("CARGO_MANIFEST_DIR"),
        "..",
        "fixtures",
        "http",
        "brain_store.sqlite",
    ]
    .iter()
    .collect();
    if !store.exists() {
        // The fallback schema in `seed.rs` writes `CAPTURE_NOON` throughout, so
        // there is nothing left to check.
        return;
    }
    let conn = rusqlite::Connection::open_with_flags(
        &store,
        rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY | rusqlite::OpenFlags::SQLITE_OPEN_URI,
    )
    .expect("read-only open; never mutate the committed store");

    // Exactly the expressions `chronicle_api::store` buckets its four lanes by.
    for (table, column) in [
        ("ingestion_provenance", "COALESCE(captured_at, created_at)"),
        ("nodes_v2", "COALESCE(valid_from, created_at)"),
        ("edges_v2", "created_at"),
        ("conversation_messages", "timestamp"),
    ] {
        let days: Vec<String> = conn
            .prepare(&format!(
                "SELECT DISTINCT substr({column}, 1, 10) FROM {table} ORDER BY 1"
            ))
            .expect("prepare")
            .query_map([], |row| row.get::<_, Option<String>>(0))
            .expect("query")
            .filter_map(Result::ok)
            .flatten()
            .collect();
        assert_eq!(
            days,
            vec![CAPTURE_DATE.to_string()],
            "{table} spans more than the capture day; the day route cannot pin it"
        );
        let latest: Option<String> = conn
            .query_row(&format!("SELECT MAX({column}) FROM {table}"), [], |row| {
                row.get(0)
            })
            .expect("max");
        let latest = latest.expect("a non-empty lane");
        assert!(
            latest.as_str() <= CAPTURE_END_OF_DAY,
            "{table} holds {latest}, after the as-of instant {CAPTURE_END_OF_DAY}; \
             /api/chronicle/as-of would answer short"
        );
    }
}

/// No stamp the chronicle buckets by carries a UTC offset after seeding.
///
/// An offset-bearing stamp is moved into the runner's zone by
/// `chronicle_api::pytime::_local`, so it can change which *day* a row belongs
/// to and split a one-day fixture across two. `seed_naive_provenance_stamps`
/// normalises the fifteen the capture left behind; this is the end-state check,
/// read off the sandbox copy rather than the committed file.
#[tokio::test]
async fn nothing_the_chronicle_buckets_by_carries_a_timezone() {
    let install = common::brain::Install::start().await;
    let conn = rusqlite::Connection::open(install.data_dir().join("knowledge_graph.sqlite"))
        .expect("sandbox store");
    for (table, column) in [
        ("ingestion_provenance", "captured_at"),
        ("ingestion_provenance", "created_at"),
        ("nodes_v2", "created_at"),
        ("nodes_v2", "valid_from"),
        ("edges_v2", "created_at"),
        ("conversation_messages", "timestamp"),
    ] {
        let offset_bearing: i64 = conn
            .query_row(
                &format!(
                    // `+HH:MM`, a trailing `Z`, or a `-HH:MM` — the last one
                    // only counts after the `T`, since the date has hyphens too.
                    "SELECT COUNT(*) FROM {table} \
                     WHERE {column} LIKE '%+%' OR {column} LIKE '%Z' \
                        OR ({column} LIKE '%T%' \
                            AND instr(substr({column}, instr({column}, 'T')), '-') > 0)"
                ),
                [],
                |row| row.get(0),
            )
            .expect("count");
        assert_eq!(
            offset_bearing, 0,
            "{table}.{column} carries a UTC offset; the day it lands on then \
             depends on the runner's TZ, not on the data"
        );
    }
}
