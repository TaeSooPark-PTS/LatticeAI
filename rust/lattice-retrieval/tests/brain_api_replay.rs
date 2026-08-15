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

#![allow(dead_code, unused_imports, unused_variables)]
#![allow(clippy::all)]
mod common;

use serde_json::Value;

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
