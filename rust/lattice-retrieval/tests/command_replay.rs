//! Replay `command_center` records from `memory_brain.json`.
//!
//! ## Three of these bodies deliberately diverge from the capture
//!
//! The Python oracle's `_search_knowledge` read `payload['results']` while
//! `keyword_search` answers `matches`, so `/api/command/search` shipped a
//! knowledge group that could never fill. v11.6.0 ported that faithfully and
//! disclosed it (RELEASE_NOTES §5.2); v11.7.0 fixes the key.
//!
//! So `search`, `search_conversation_hit` and `search_korean` no longer pin what
//! Python answered. They pin what this port answers against the *same* seeded
//! store (`rust/fixtures/http/brain_store.sqlite`), and each carries a
//! DIVERGENCE FROM ORACLE note saying so. Everything the fix did not touch is
//! byte-identical to the capture — including the whole conversation group of
//! `search_conversation_hit`, which is the control: had the fix perturbed the
//! other lanes, that group would have moved with it.
//!
//! `briefing`, `search_empty_query` and both auth denials are untouched
//! captures. The unit tests in `command_center_api::search` pin the wiring
//! itself against a store built in-test, so a store that stops matching cannot
//! quietly restore the old empty answer here.

#![allow(dead_code, unused_imports, unused_variables)]
#![allow(clippy::all)]
mod common;

use serde_json::Value;

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
