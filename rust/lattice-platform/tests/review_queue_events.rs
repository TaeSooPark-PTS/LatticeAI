//! v11.7.0 §F-A — one writer, correct refusals, one timeline event per change.
//!
//! Four disclosed gaps meet here, so they are tested together against one
//! running install:
//!
//! * `workspace_os.json` has a single storage authority, so a Review Center
//!   write and a Workspace OS write can no longer erase one another.
//! * An offset-aware `snooze` succeeds instead of answering 500 after it has
//!   already written, and an unreadable `until` is refused before it is stored.
//! * Rejecting an already-rejected proposal is a 409, not a 500.
//! * Every route that changes a review item records exactly one
//!   `review_item_created` / `review_item_updated` event — and every route that
//!   changes nothing records none.

// The shared harness is written for every suite that includes it, so a
// helper this one does not call still reads as dead in this binary.
#[allow(dead_code)]
mod review_queue_harness;

use std::collections::HashMap;
use std::sync::Arc;

use lattice_agent::proposals::{NewReviewItem, ProposalStore};
use lattice_auth::{AuthConfig, AuthState, Clock};
use lattice_platform::review_queue::{
    GovernanceState, REVIEW_ITEM_CREATED_EVENT, REVIEW_ITEM_UPDATED_EVENT, REVIEW_TIMELINE_AREA,
};
use lattice_platform::workspace::WorkspaceOsStore;
use review_queue_harness::Install;
use serde_json::{json, Value};

/// A far-future offset-aware stamp — the literal the fixture snoozes with.
const AWARE_FUTURE: &str = "2099-01-01T00:00:00+00:00";

/// Every review event on the timeline, oldest first.
fn review_events(install: &Install) -> Vec<Value> {
    install.gov.store().load_state()["timeline"]
        .as_array()
        .cloned()
        .unwrap_or_default()
        .into_iter()
        .filter(|event| event.get("area").and_then(Value::as_str) == Some(REVIEW_TIMELINE_AREA))
        .collect()
}

/// The review events recorded since `mark`.
fn events_since(install: &Install, mark: usize) -> Vec<Value> {
    review_events(install).split_off(mark)
}

fn one_event(install: &Install, mark: usize) -> Value {
    let recorded = events_since(install, mark);
    assert_eq!(
        recorded.len(),
        1,
        "expected exactly one review event, got {recorded:#?}"
    );
    recorded.into_iter().next().expect("event")
}

fn assert_shape(event: &Value, event_type: &str, item_id: &str, action: &str, status: &str) {
    assert_eq!(event["event_type"], json!(event_type));
    assert_eq!(event["area"], json!(REVIEW_TIMELINE_AREA));
    assert_eq!(event["workspace_id"], json!("personal"));
    assert_eq!(event["payload"]["item_id"], json!(item_id));
    assert_eq!(event["payload"]["action"], json!(action));
    assert_eq!(event["payload"]["status"], json!(status));
    assert_eq!(event["payload"]["workspace_id"], json!("personal"));
    assert!(
        event["timestamp"].as_str().is_some_and(|s| s.len() >= 19),
        "{event:#?}"
    );
}

#[tokio::test]
async fn creating_a_review_item_records_one_created_event_whichever_door_it_came_through() {
    let install = Install::start().await;
    // The seven items the harness seeds over HTTP are already on the timeline.
    let seeded = review_events(&install);
    assert_eq!(seeded.len(), 7, "one per seeded item");
    assert!(seeded.iter().all(
        |event| event["event_type"] == json!(REVIEW_ITEM_CREATED_EVENT)
            && event["payload"]["action"] == json!("create")
            && event["payload"]["status"] == json!("pending")
    ));

    let mark = seeded.len();
    let answer = install
        .issue(
            "POST",
            "/automation/reviews",
            Some(&json!({"title": "Event fixture", "source": "chat_followup"})),
            "session:owner",
        )
        .await;
    assert_eq!(answer.status, 200, "{}", answer.body);
    let created: Value = serde_json::from_str(&answer.body).expect("body");
    let event = one_event(&install, mark);
    assert_shape(
        &event,
        REVIEW_ITEM_CREATED_EVENT,
        created["id"].as_str().expect("id"),
        "create",
        "pending",
    );
    assert_eq!(event["payload"]["source"], json!("chat_followup"));

    // The agent loop stages through the same store, so it lands on the same
    // timeline rather than appearing from nowhere.
    let mark = review_events(&install).len();
    let staged = ProposalStore::create(
        &install.gov,
        &NewReviewItem {
            title: "파일 수정 제안: loop.md".into(),
            summary: "staged by the loop".into(),
            source: "change_proposal".into(),
            kind: "file_update".into(),
            payload: json!({"path": "loop.md"}),
            provenance: json!({"proposed_by": "agent"}),
            user_email: Some("owner@lattice.test".into()),
            workspace_id: None,
        },
    )
    .expect("stage");
    assert_shape(
        &one_event(&install, mark),
        REVIEW_ITEM_CREATED_EVENT,
        staged["id"].as_str().expect("id"),
        "create",
        "pending",
    );
}

/// One status route to exercise:
/// `(method, path, body, item id, expected action, expected status)`.
type StatusCase<'a> = (&'a str, String, Option<Value>, &'a str, &'a str, &'a str);

#[tokio::test]
async fn every_status_route_records_exactly_one_updated_event() {
    let install = Install::start().await;
    let approve = install.bind("$review_item");
    let dismiss = install.bind("$review_dismiss");
    let snooze = install.bind("$review_snooze");
    let bulk = install.bind("$review_bulk");
    let proposal_apply = install.bind("$proposal_apply");
    let proposal_reject = install.bind("$proposal_reject");

    let cases: Vec<StatusCase<'_>> = vec![
        (
            "POST",
            format!("/automation/reviews/{approve}/approve"),
            None,
            approve.as_str(),
            "approve",
            "approved",
        ),
        (
            "POST",
            format!("/automation/reviews/{dismiss}/dismiss"),
            Some(json!({"reason": "no"})),
            dismiss.as_str(),
            "dismiss",
            "dismissed",
        ),
        (
            "POST",
            format!("/automation/reviews/{snooze}/snooze"),
            Some(json!({"until": AWARE_FUTURE})),
            snooze.as_str(),
            "snooze",
            "snoozed",
        ),
        (
            "POST",
            format!("/automation/reviews/{snooze}/unsnooze"),
            None,
            snooze.as_str(),
            "unsnooze",
            "pending",
        ),
        (
            "POST",
            "/automation/reviews/bulk/approve".into(),
            Some(json!({"ids": [bulk.clone()]})),
            bulk.as_str(),
            "approve",
            "approved",
        ),
        (
            "POST",
            format!("/api/proposals/{proposal_reject}/reject"),
            Some(json!({"reason": "not this one"})),
            proposal_reject.as_str(),
            "reject",
            "dismissed",
        ),
        (
            "POST",
            format!("/api/proposals/{proposal_apply}/approve"),
            None,
            proposal_apply.as_str(),
            "approve",
            "approved",
        ),
    ];

    for (method, path, body, item_id, action, status) in cases {
        let mark = review_events(&install).len();
        let answer = install
            .issue(method, &path, body.as_ref(), "session:owner")
            .await;
        assert_eq!(answer.status, 200, "{path}: {}", answer.body);
        assert_shape(
            &one_event(&install, mark),
            REVIEW_ITEM_UPDATED_EVENT,
            item_id,
            action,
            status,
        );
    }

    // bulk/dismiss over one still-pending item, to cover the last route.
    let answer = install
        .issue(
            "POST",
            "/automation/reviews",
            Some(&json!({"title": "Bulk dismiss me", "source": "workflow_run"})),
            "session:owner",
        )
        .await;
    let fresh: Value = serde_json::from_str(&answer.body).expect("body");
    let fresh_id = fresh["id"].as_str().expect("id").to_string();
    let mark = review_events(&install).len();
    let answer = install
        .issue(
            "POST",
            "/automation/reviews/bulk/dismiss",
            Some(&json!({"ids": [fresh_id.clone()], "reason": "bulk"})),
            "session:owner",
        )
        .await;
    assert_eq!(answer.status, 200, "{}", answer.body);
    assert_shape(
        &one_event(&install, mark),
        REVIEW_ITEM_UPDATED_EVENT,
        &fresh_id,
        "dismiss",
        "dismissed",
    );
}

#[tokio::test]
async fn a_route_that_changes_nothing_records_nothing() {
    let install = Install::start().await;
    let item = install.bind("$review_item");
    let snooze = install.bind("$review_snooze");

    // run_now reads, guards and answers — it never writes, so there is nothing
    // to record. Both of its branches are covered here.
    let mark = review_events(&install).len();
    let no_workflow = install
        .issue(
            "POST",
            &format!("/automation/reviews/{snooze}/run_now"),
            None,
            "session:owner",
        )
        .await;
    assert_eq!(no_workflow.status, 409, "{}", no_workflow.body);

    // A missing item, a conflicting transition, and a bulk action over both.
    let missing = install
        .issue(
            "POST",
            "/automation/reviews/review-missing/dismiss",
            None,
            "session:owner",
        )
        .await;
    assert_eq!(missing.status, 404);
    let approved = install
        .issue(
            "POST",
            &format!("/automation/reviews/{item}/approve"),
            None,
            "session:owner",
        )
        .await;
    assert_eq!(approved.status, 200, "{}", approved.body);
    let mark_after_approve = review_events(&install).len();
    let again = install
        .issue(
            "POST",
            &format!("/automation/reviews/{item}/approve"),
            None,
            "session:owner",
        )
        .await;
    assert_eq!(again.status, 409, "{}", again.body);
    let bulk = install
        .issue(
            "POST",
            "/automation/reviews/bulk/dismiss",
            Some(&json!({"ids": [item.clone(), "review-missing"]})),
            "session:owner",
        )
        .await;
    assert_eq!(bulk.status, 200, "{}", bulk.body);

    assert!(
        events_since(&install, mark_after_approve).is_empty(),
        "refusals must not reach the timeline"
    );
    // Exactly one event across the whole test: the approve that landed.
    assert_eq!(events_since(&install, mark).len(), 1);
}

#[tokio::test]
async fn an_offset_aware_snooze_is_accepted_and_read_back_correctly() {
    let install = Install::start().await;
    let item = install.bind("$review_snooze");

    for (until, effective) in [
        (AWARE_FUTURE, "snoozed"),
        // Already past, in UTC and in a non-zero offset that also resolves to
        // the past — both read as pending again without touching the store.
        ("2020-01-01T00:00:00+00:00", "pending"),
        ("2020-01-01T00:00:00-05:00", "pending"),
        ("2020-01-01T00:00:00Z", "pending"),
        // Naive, as Python's own writes are.
        ("2099-01-01T00:00:00", "snoozed"),
    ] {
        let answer = install
            .issue(
                "POST",
                &format!("/automation/reviews/{item}/snooze"),
                Some(&json!({"until": until})),
                "session:owner",
            )
            .await;
        assert_eq!(answer.status, 200, "{until}: {}", answer.body);
        let body: Value = serde_json::from_str(&answer.body).expect("body");
        assert_eq!(body["status"], json!("snoozed"), "{until}");
        assert_eq!(body["snoozed_until"], json!(until), "{until}");
        assert_eq!(body["effective_status"], json!(effective), "{until}");

        // …and the same answer on the read path, which is where the 500 was.
        let fetched = install
            .issue(
                "GET",
                &format!("/automation/reviews/{item}"),
                None,
                "session:owner",
            )
            .await;
        assert_eq!(fetched.status, 200, "{until}: {}", fetched.body);
        let fetched: Value = serde_json::from_str(&fetched.body).expect("body");
        assert_eq!(fetched["effective_status"], json!(effective), "{until}");

        // Unsnooze only when the item is still snoozed; an expired snooze is
        // already pending and `snooze` accepts both statuses.
        if effective == "snoozed" {
            let back = install
                .issue(
                    "POST",
                    &format!("/automation/reviews/{item}/unsnooze"),
                    None,
                    "session:owner",
                )
                .await;
            assert_eq!(back.status, 200, "{}", back.body);
        }
    }

    // An unreadable stamp is refused *before* it is written, so it can never
    // be read back: 422, and the stored item is untouched.
    let before = install
        .issue(
            "GET",
            &format!("/automation/reviews/{item}"),
            None,
            "session:owner",
        )
        .await;
    let before: Value = serde_json::from_str(&before.body).expect("body");
    let mark = review_events(&install).len();
    let refused = install
        .issue(
            "POST",
            &format!("/automation/reviews/{item}/snooze"),
            Some(&json!({"until": "next tuesday"})),
            "session:owner",
        )
        .await;
    assert_eq!(refused.status, 422, "{}", refused.body);
    let refused: Value = serde_json::from_str(&refused.body).expect("body");
    assert!(
        refused["detail"]
            .as_str()
            .is_some_and(|text| text.starts_with("until must be an ISO-8601 datetime")),
        "{refused:#?}"
    );
    assert!(events_since(&install, mark).is_empty());
    let after = install
        .issue(
            "GET",
            &format!("/automation/reviews/{item}"),
            None,
            "session:owner",
        )
        .await;
    let after: Value = serde_json::from_str(&after.body).expect("body");
    assert_eq!(after, before, "a refused snooze must change nothing");
}

#[tokio::test]
async fn rejecting_an_already_rejected_proposal_is_a_conflict_not_a_crash() {
    let install = Install::start().await;
    let proposal = install.bind("$proposal_reject");
    let first = install
        .issue(
            "POST",
            &format!("/api/proposals/{proposal}/reject"),
            Some(&json!({"reason": "no"})),
            "session:owner",
        )
        .await;
    assert_eq!(first.status, 200, "{}", first.body);

    let mark = review_events(&install).len();
    let second = install
        .issue(
            "POST",
            &format!("/api/proposals/{proposal}/reject"),
            Some(&json!({"reason": "again"})),
            "session:owner",
        )
        .await;
    assert_eq!(second.status, 409, "{}", second.body);
    assert_eq!(second.content_type, "application/json");
    let body: Value = serde_json::from_str(&second.body).expect("body");
    assert_eq!(
        body,
        json!({"detail": "cannot 'dismiss' a review item in status 'dismissed'"})
    );
    // The sibling review route answers the same thing, which is the point.
    let sibling = install
        .issue(
            "POST",
            &format!("/automation/reviews/{proposal}/dismiss"),
            None,
            "session:owner",
        )
        .await;
    assert_eq!(sibling.status, 409);
    assert_eq!(sibling.body, second.body);
    assert!(events_since(&install, mark).is_empty());
}

/// The regression the single-writer change exists for.
///
/// Before it, `GovernanceState` held a full in-memory copy of the document and
/// wrote it back wholesale, while `WorkspaceOsStore` reloaded per mutation.
/// Interleave the two and whichever saved last erased the other's work. Now
/// both hold the same handle, so both changes survive every interleaving.
#[test]
fn interleaved_review_and_workspace_writes_both_survive() {
    const ROUNDS: usize = 40;

    let dir = tempfile::tempdir().expect("tempdir");
    let mut env = HashMap::new();
    env.insert(
        "LATTICEAI_DATA_DIR".to_string(),
        dir.path().to_string_lossy().into_owned(),
    );
    let mut config = AuthConfig::from_map(&env, None);
    config.data_dir = dir.path().to_path_buf();
    let auth = Arc::new(AuthState::with_clock(
        config,
        Clock::frozen(1_786_000_000.0),
    ));

    let store = Arc::new(WorkspaceOsStore::open(dir.path()));
    let gov = GovernanceState::with_store(
        Arc::clone(&auth),
        Arc::clone(&store),
        dir.path().join("agent_workspace"),
        None,
    );
    assert!(
        Arc::ptr_eq(gov.store(), &store),
        "the Review Center must not open a second store"
    );

    std::thread::scope(|scope| {
        let reviewer = &gov;
        scope.spawn(move || {
            for index in 0..ROUNDS {
                ProposalStore::create(
                    reviewer,
                    &NewReviewItem {
                        title: format!("proposal {index}"),
                        summary: String::new(),
                        source: "change_proposal".into(),
                        kind: "file_update".into(),
                        payload: json!({"path": format!("file-{index}.md")}),
                        provenance: json!({}),
                        user_email: Some("owner@lattice.test".into()),
                        workspace_id: None,
                    },
                )
                .expect("stage");
            }
        });
        let workspace = Arc::clone(&store);
        scope.spawn(move || {
            for index in 0..ROUNDS {
                workspace
                    .mutate(|state| {
                        let mut memories =
                            state["memories"].as_array().cloned().unwrap_or_default();
                        memories.push(json!({"id": format!("memory-{index}")}));
                        state["memories"] = Value::Array(memories);
                        Ok(())
                    })
                    .expect("workspace write");
            }
        });
    });

    let state = store.load_state();
    assert_eq!(
        state["memories"].as_array().expect("memories").len(),
        ROUNDS,
        "a workspace write was erased by a review write"
    );
    assert_eq!(
        state["review_items"].as_array().expect("items").len(),
        ROUNDS,
        "a review write was erased by a workspace write"
    );
    // Each staged proposal also recorded its event, under the same lock.
    let created = state["timeline"]
        .as_array()
        .expect("timeline")
        .iter()
        .filter(|event| event["event_type"] == json!(REVIEW_ITEM_CREATED_EVENT))
        .count();
    assert_eq!(created, ROUNDS);

    // The document on disk is the same document, not a fragment of it.
    let on_disk: Value =
        serde_json::from_str(&std::fs::read_to_string(gov.state_path()).expect("read"))
            .expect("json");
    assert_eq!(on_disk["review_items"], state["review_items"]);
    assert_eq!(on_disk["version"], json!(env!("CARGO_PKG_VERSION")));
}

/// The second half of the single-writer story: the marketplace / plugins /
/// designer view of the same document.
///
/// Those families used to hold their own `workspace_os.json` reader-writer —
/// no shared lock, their own formatting — so a plugin toggle rewrote the whole
/// document from a snapshot taken before the Review Center's last write. Both
/// now reach the one store through `WorkspaceOsStore::shared`, and neither can
/// erase the other however the two interleave.
#[tokio::test(flavor = "multi_thread")]
async fn a_plugin_toggle_and_a_review_write_cannot_erase_one_another() {
    const ROUNDS: usize = 30;

    let install = Install::start().await;
    // Naming the same directory anywhere in the process yields the same store.
    assert!(
        Arc::ptr_eq(
            install.gov.store(),
            &WorkspaceOsStore::shared(&install.data_dir)
        ),
        "the registry must hand back the Review Center's own handle"
    );

    let plugins = lattice_platform::plugins::router(lattice_platform::plugins::PluginsState::new(
        Arc::clone(&install.gov.auth),
        &install.data_dir,
    ));
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .expect("bind");
    let plugin_origin = format!("http://{}", listener.local_addr().expect("addr"));
    let served = tokio::spawn(async move {
        let _ = axum::serve(listener, plugins.into_make_service()).await;
    });

    let seeded = install.gov.store().load_state()["review_items"]
        .as_array()
        .expect("items")
        .len();

    let toggles = async {
        let client = reqwest::Client::builder()
            .no_proxy()
            .build()
            .expect("client");
        for index in 0..ROUNDS {
            let answer = client
                .post(format!("{plugin_origin}/plugins/enable"))
                .header("host", "127.0.0.1:4825")
                .header("origin", "http://127.0.0.1:4825")
                .header("cookie", format!("session_token={}", install.owner_token))
                .header("content-type", "application/json")
                .body(json!({"plugin_id": format!("plugin-{index}")}).to_string())
                .send()
                .await
                .expect("request");
            assert_eq!(answer.status().as_u16(), 200, "toggle {index}");
        }
    };
    let reviews = async {
        for index in 0..ROUNDS {
            let answer = install
                .issue(
                    "POST",
                    "/automation/reviews",
                    Some(&json!({
                        "title": format!("Race item {index}"),
                        "source": "chat_followup"
                    })),
                    "session:owner",
                )
                .await;
            assert_eq!(answer.status, 200, "create {index}: {}", answer.body);
        }
    };
    tokio::join!(toggles, reviews);

    let state = install.gov.store().load_state();
    assert_eq!(
        state["review_items"].as_array().expect("items").len(),
        seeded + ROUNDS,
        "a review write was erased by a plugin toggle"
    );
    let registry = state["plugin_registry"].as_object().expect("registry");
    assert_eq!(
        registry.len(),
        ROUNDS,
        "a plugin toggle was erased by a review write"
    );
    for index in 0..ROUNDS {
        assert_eq!(registry[&format!("plugin-{index}")]["enabled"], json!(true));
    }
    // Every review write also recorded its event under the same lock.
    assert_eq!(review_events(&install).len(), seeded + ROUNDS);
    served.abort();
}
