//! v11.7.0 §F-A — `workspace_os.json` has one owner, and this crate asks it.
//!
//! The memory tiers and brain synthesis write review items and memories into a
//! document `lattice-platform` also writes. This crate cannot name that store
//! (the dependency runs the other way), so it declares a port and the host
//! installs the implementation. The port is process-wide, which is why this
//! lives in its own test binary: installing a writer inside the lib tests
//! would redirect every other test's writes into it.

use std::sync::{Arc, Mutex};

use lattice_retrieval::memory_api::wsos::{
    delete_memory, install_state_writer, mutate_state, record_review_event, save_state,
    state_writer, StateWriter, REVIEW_ITEM_CREATED_EVENT, REVIEW_ITEM_UPDATED_EVENT,
    REVIEW_TIMELINE_AREA,
};
use serde_json::{json, Value};

/// Stands in for the platform store the gateway installs.
struct Recorder {
    document: Mutex<Value>,
    calls: Mutex<usize>,
    events: Mutex<Vec<Value>>,
}

impl StateWriter for Recorder {
    fn mutate(&self, body: &mut dyn FnMut(&mut Value)) -> Result<(), String> {
        let mut document = self.document.lock().expect("document");
        body(&mut document);
        *self.calls.lock().expect("calls") += 1;
        Ok(())
    }

    fn record_event(
        &self,
        area: &str,
        event_type: &str,
        payload: Value,
        workspace_id: Option<&str>,
    ) -> Result<(), String> {
        self.events.lock().expect("events").push(json!({
            "area": area,
            "event_type": event_type,
            "payload": payload,
            "workspace_id": workspace_id,
        }));
        Ok(())
    }
}

#[test]
fn every_state_write_goes_through_the_installed_owner() {
    let recorder = Arc::new(Recorder {
        document: Mutex::new(json!({"memories": [{"id": "m1"}, {"id": "m2"}]})),
        calls: Mutex::new(0),
        events: Mutex::new(Vec::new()),
    });
    assert!(state_writer().is_none(), "nothing installed yet");
    assert!(install_state_writer(recorder.clone()));
    assert!(
        !install_state_writer(recorder.clone()),
        "a second install must not swap the document under a live route"
    );

    // Neither is ever reached: with a writer installed every call below
    // short-circuits before it would touch SQLite or the mirror file.
    let scratch = tempfile::tempdir().expect("tempdir");
    let store = Arc::new(
        lattice_core::db::Store::open(&scratch.path().join("unused.sqlite")).expect("store"),
    );
    let dir = scratch.path();

    // A delete is one load-apply-save under the owner's lock, not a read
    // followed by an unrelated write.
    assert!(delete_memory(&store, dir, "m1").expect("delete"));
    assert_eq!(*recorder.calls.lock().unwrap(), 1);
    assert_eq!(
        recorder.document.lock().unwrap()["memories"],
        json!([{"id": "m2"}])
    );

    // Deleting something absent reports "nothing removed" rather than
    // inventing a write of its own.
    assert!(!delete_memory(&store, dir, "m1").expect("delete"));
    assert_eq!(*recorder.calls.lock().unwrap(), 2);

    // `save_state` (whole-document callers: brain synthesis) goes through the
    // same door, so the bytes and the version stamp are the owner's.
    save_state(
        &store,
        dir,
        &json!({"memories": [], "review_items": [{"id": "r1"}]}),
    )
    .expect("save");
    assert_eq!(
        recorder.document.lock().unwrap()["review_items"],
        json!([{"id": "r1"}])
    );

    // …and `mutate_state` is the shape that also closes the read-modify-write
    // window, which is what the remaining `save_state` callers should adopt.
    mutate_state(&store, dir, |state| {
        state["memories"] = json!([{"id": "m3"}]);
    })
    .expect("mutate");
    assert_eq!(
        recorder.document.lock().unwrap()["memories"],
        json!([{"id": "m3"}])
    );
    assert_eq!(*recorder.calls.lock().unwrap(), 4);
    assert!(state_writer().is_some());
    // Nothing was written beside the owner: no mirror file was created.
    assert!(!dir.join("workspace_os.json").exists());

    // A review write this crate makes is funnelled to the owner's timeline the
    // way the Review Center's own writers funnel theirs — same area, same
    // event ids, same payload keys in the same order.
    record_review_event(
        &json!({"id": "review-1", "status": "pending", "source": "kg_change_digest",
                "workspace_id": "personal"}),
        "create",
    );
    record_review_event(
        &json!({"id": "review-1", "status": "approved", "source": "kg_change_digest"}),
        "approve",
    );
    let events = recorder.events.lock().expect("events").clone();
    assert_eq!(events.len(), 2, "{events:?}");
    assert_eq!(events[0]["area"], json!(REVIEW_TIMELINE_AREA));
    assert_eq!(events[0]["event_type"], json!(REVIEW_ITEM_CREATED_EVENT));
    assert_eq!(events[1]["event_type"], json!(REVIEW_ITEM_UPDATED_EVENT));
    assert_eq!(
        serde_json::to_string(&events[1]["payload"]).unwrap(),
        r#"{"item_id":"review-1","action":"approve","status":"approved","source":"kg_change_digest","workspace_id":"personal"}"#,
        "an item with no workspace still lands in the default one"
    );
}
