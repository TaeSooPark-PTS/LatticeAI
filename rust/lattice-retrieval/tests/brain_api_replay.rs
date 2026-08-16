//! Replay `brain_intelligence` records from `memory_brain.json`.
//!
//! ## Five of these cases were golden fixtures with a fuse
//!
//! Every node in the seeded store carries one stamp, `2026-08-14T12:00:00`, read
//! as UTC. Five routes compare that stamp against `now - N days`. A shared stamp
//! does not decay across a threshold, it *jumps*: on one instant every node in
//! the sample changes bucket together and the pinned body changes with it.
//!
//! | fuse | route | would have fired |
//! |---|---|---|
//! | `RECENT_DAYS` 7 | `insights`, `garden`, `proactive-brief` | 2026-08-21T12:00Z |
//! | `STALE_DAYS` 45 | `health` | 2026-09-28T12:00Z |
//! | `QUALITY_STALE_DAYS` 90 | `quality-report` | 2026-11-12T12:00Z |
//!
//! All five now read `BrainState::now_utc()`, which the harness freezes at the
//! capture instant; production keeps the real clock by default.
//! [`the_five_freshness_fuses_are_defused`] is the falsifier — it fails if any
//! of them is ever unwired again.
//!
//! ## Chronicle and command-center ride along
//!
//! All three families share one `common::brain` install, and each used to be
//! its own test binary recompiling it.

// The shared harness is written for every suite that includes it, so a
// helper this one does not call still reads as dead in this binary.
#[allow(dead_code)]
mod common;

use common::brain::{CAPTURE_DATE, CAPTURE_END_OF_DAY, CAPTURE_NOON};
use serde_json::Value;

// ── brain_api ──

#[tokio::test]
async fn brain_replays_the_python_oracle() {
    let install = common::brain::Install::start().await;
    install.replay_family("brain_intelligence").await;
}

/// Every clock threshold reachable from a replayed `/api/brain/*` route.
///
/// `(case name, fuse in days, what crosses it)`. Adding a windowed route without
/// adding it here is the mistake this table exists to make loud.
const FUSES: [(&str, f64, &str); 5] = [
    ("insights", 7.0, "digest::build_insights RECENT_DAYS"),
    ("garden", 7.0, "digest::build_garden RECENT_DAYS"),
    ("proactive_brief", 7.0, "proposals::recent_window"),
    ("health", 45.0, "health::freshness_dimension STALE_DAYS"),
    (
        "quality_report",
        90.0,
        "proactive::quality_report QUALITY_STALE_DAYS",
    ),
];

fn case_named(name: &str) -> Value {
    common::brain::cases_for("brain_intelligence")
        .into_iter()
        .find(|case| case["name"] == name)
        .unwrap_or_else(|| panic!("no `{name}` case in the fixture"))
}

/// Issue `case` against an install whose UTC clock sits at `now_utc`.
async fn answer_at(case: &Value, now_utc: f64) -> Value {
    let install = common::brain::Install::start_at(now_utc).await;
    let (status, _ct, body) = install.issue(case).await;
    assert_eq!(status, 200, "{} status", case["name"]);
    serde_json::from_str(&body).expect("json")
}

/// The five windowed routes answer the capture's answer forever.
///
/// Each fuse is checked at three instants, because a frozen clock that is never
/// consulted is indistinguishable from one that works:
///
/// 1. **at the capture** — must match the fixture, or the freeze is wrong;
/// 2. **one day short of the fuse** — must still match, or the fuse is not
///    where this table says it is;
/// 3. **one day past the fuse** — must **not** match. This is the falsifier: if
///    a route goes back to `sampling::now_utc_secs()`, all three instants give
///    the same answer and this assertion fails immediately, instead of the
///    fixture failing months later on a date nobody is watching.
#[tokio::test]
async fn the_five_freshness_fuses_are_defused() {
    let capture = common::brain::capture_utc_secs();
    let day = 86_400.0;
    for (name, fuse_days, what) in FUSES {
        let case = case_named(name);
        let expected = &case["response_body"];

        let at_capture = answer_at(&case, capture).await;
        assert!(
            common::brain::matches_token(expected, &at_capture),
            "{name}: the capture instant must reproduce the fixture ({what})"
        );

        let before = answer_at(&case, capture + (fuse_days - 1.0) * day).await;
        assert!(
            common::brain::matches_token(expected, &before),
            "{name}: one day short of the {fuse_days}-day fuse the answer moved; \
             the fuse is not where FUSES says it is ({what})"
        );

        let after = answer_at(&case, capture + (fuse_days + 1.0) * day).await;
        assert!(
            !common::brain::matches_token(expected, &after),
            "{name}: the answer did not change one day past the {fuse_days}-day \
             fuse, so {what} is reading the machine clock rather than \
             BrainState::now_utc() — this fixture will start failing on its own"
        );
    }
}

// ── chronicle_api ──

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

// ── command_center_api ──

#[tokio::test]
async fn command_replays_the_python_oracle() {
    let install = common::brain::Install::start().await;
    install.replay_family("command_center").await;
}

/// `health::freshness_dimension`'s fuse, in days (`health.rs:73`).
const FRESHNESS_FUSE_DAYS: f64 = 45.0;

/// `sections.health` of the `briefing` case, as the fixture pins it.
fn pinned_health() -> Value {
    common::brain::cases_for("command_center")
        .into_iter()
        .find(|case| case["name"] == "briefing")
        .expect("the briefing case")["response_body"]["sections"]["health"]
        .clone()
}

/// One live `GET /api/command/briefing`, reduced to its health section.
async fn health_section(install: &common::brain::Install) -> Value {
    let body = reqwest::Client::builder()
        .no_proxy()
        .build()
        .expect("client")
        .get(format!("{}/api/command/briefing", install.origin))
        .header("cookie", format!("session_token={}", install.token))
        .send()
        .await
        .expect("briefing")
        .text()
        .await
        .expect("text");
    serde_json::from_str::<Value>(&body).expect("json")["sections"]["health"].clone()
}

/// The briefing's grade is the capture's grade on every future date.
///
/// `freshness_dimension` scores `updated_at < now - 45 days`, and `parse_ts`
/// reads the store's naive stamps as UTC. Every node in the capture is stamped
/// `2026-08-14T12:00:00` (checked: one distinct value across all 62), so they do
/// not decay one by one — they all go stale at the same instant,
/// **2026-09-28T12:00:00Z**, taking `"grade"` from `"excellent"` to `"good"` and
/// adding a `refresh_stale_knowledge` action. The fixture pins both.
///
/// Three instants, because a frozen clock that is never consulted looks exactly
/// like a frozen clock that works:
///
/// 1. the capture instant, which must still match the fixture;
/// 2. one day *before* the fuse, which must also match — otherwise the fuse is
///    not where this test claims it is;
/// 3. one day *after*, which must **not** match. That is the falsifier: it fails
///    if `with_utc_clock` is ever dropped from the seam and `briefing.rs` goes
///    back to reading the machine, because then all three instants agree and the
///    third assertion cannot hold.
#[tokio::test]
async fn the_briefing_grade_does_not_expire_with_the_freshness_fuse() {
    let expected = pinned_health();
    let capture = common::brain::capture_utc_secs();

    let at_capture = health_section(&common::brain::Install::start().await).await;
    assert!(
        common::brain::matches_token(&expected, &at_capture),
        "the capture instant must reproduce the fixture, got {at_capture}"
    );
    assert_eq!(at_capture["grade"], "excellent");

    let day = 86_400.0;
    let before = health_section(
        &common::brain::Install::start_at(capture + (FRESHNESS_FUSE_DAYS - 1.0) * day).await,
    )
    .await;
    assert!(
        common::brain::matches_token(&expected, &before),
        "one day short of the fuse the answer must not have moved, got {before}"
    );

    let after = health_section(
        &common::brain::Install::start_at(capture + (FRESHNESS_FUSE_DAYS + 1.0) * day).await,
    )
    .await;
    assert_eq!(
        after["grade"], "good",
        "past the fuse every node is stale at once"
    );
    assert_eq!(
        after["recommended_actions"]
            .as_array()
            .expect("actions")
            .len(),
        2,
        "refresh_stale_knowledge joins rebuild_vector_index"
    );
    assert!(
        !common::brain::matches_token(&expected, &after),
        "if this passes, the injected clock is not being read and the fixture \
         will start failing on 2026-09-28 with nobody watching"
    );
}

/// The injected instant is the end of the capture day, in UTC.
#[test]
fn the_capture_instant_is_the_end_of_the_capture_day() {
    assert_eq!(
        common::brain::capture_utc_secs(),
        1_786_751_999.0,
        "2026-08-14T23:59:59Z"
    );
    // 2026-08-14T12:00:00Z, the stamp every seeded node carries, is 43,199s
    // earlier — inside the fuse and staying there.
    assert_eq!(
        common::brain::capture_utc_secs() - 1_786_708_800.0,
        43_199.0
    );
}
