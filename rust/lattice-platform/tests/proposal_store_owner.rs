//! v11.7.0 §F-G — the fourth unlocked `workspace_os.json` writer, closed.
//!
//! `lattice-agent`'s `JsonProposalStore` was the last writer that appended to
//! `workspace_os.json` without taking the document owner's lock — and worse
//! than "another lock": it wrote the JSON file only, while
//! `WorkspaceOsStore::load_state` reads the `workspace_os_state` SQLite row
//! first and rewrites the file from it. A proposal staged that way, in a
//! process that also runs the Review Center, was invisible and then gone.
//!
//! The fix is the inversion `HookSink` and `wsos::StateWriter` already use:
//! `lattice-agent` declares `DocumentWriter`, the owner implements it, and the
//! host installs it once. Installing is process-wide, which is why this lives
//! in its own test binary — a writer installed inside the shared harness would
//! redirect every other test's staging into it.

#![allow(clippy::all)]

use std::sync::Arc;

use lattice_agent::proposals::{
    install_document_writer, DocumentWriter, JsonProposalStore, NewReviewItem, ProposalStore,
};
use lattice_platform::workspace::WorkspaceOsStore;
use serde_json::{json, Value};

/// The host's `SharedStateWriter`, in miniature: the platform store answering
/// the agent crate's port.
struct Owner(Arc<WorkspaceOsStore>);

impl DocumentWriter for Owner {
    fn mutate(&self, body: &mut dyn FnMut(&mut Value)) -> Result<(), String> {
        self.0
            .mutate(|state| {
                body(state);
                Ok(())
            })
            .map_err(|error| error.to_string())
    }
}

fn proposal(index: usize) -> NewReviewItem {
    NewReviewItem {
        title: format!("파일 수정 제안: staged-{index}.md"),
        summary: "s".into(),
        source: "change_proposal".into(),
        kind: "file_update".into(),
        payload: json!({"path": format!("staged-{index}.md")}),
        provenance: json!({"proposed_by": "agent"}),
        user_email: Some("owner@lattice.test".into()),
        workspace_id: None,
    }
}

#[test]
fn a_staged_proposal_and_a_workspace_write_cannot_erase_one_another() {
    let data = tempfile::tempdir().expect("data dir");
    let store = WorkspaceOsStore::shared(data.path());
    assert!(install_document_writer(Arc::new(Owner(Arc::clone(&store)))));
    assert!(
        !install_document_writer(Arc::new(Owner(Arc::clone(&store)))),
        "a second install must not swap the document under a run mid-stage"
    );

    // 30 proposals staged through the agent's store while 30 memories are
    // appended through the platform's, from two threads over one directory.
    // Before the port these interleaved as read-modify-write over two
    // different locks *and two different media*, so a whole batch could vanish.
    const N: usize = 30;
    let staging = {
        let data_dir = data.path().to_path_buf();
        std::thread::spawn(move || {
            let json_store = JsonProposalStore::new(&data_dir);
            (0..N)
                .map(|index| {
                    json_store
                        .create(&proposal(index))
                        .expect("stage")
                        .get("id")
                        .and_then(Value::as_str)
                        .expect("id")
                        .to_string()
                })
                .collect::<Vec<String>>()
        })
    };
    let workspace = {
        let store = Arc::clone(&store);
        std::thread::spawn(move || {
            for index in 0..N {
                store
                    .mutate(|state| {
                        let rows = state
                            .as_object_mut()
                            .expect("object")
                            .entry("memories")
                            .or_insert_with(|| json!([]));
                        rows.as_array_mut()
                            .expect("list")
                            .push(json!({"id": format!("m{index}")}));
                        Ok(())
                    })
                    .expect("workspace write");
            }
        })
    };
    let staged_ids = staging.join().expect("staging thread");
    workspace.join().expect("workspace thread");

    let document = store.load_state();
    let items = document["review_items"].as_array().expect("review_items");
    assert_eq!(items.len(), N, "a staged proposal was lost");
    assert_eq!(
        document["memories"].as_array().map(Vec::len),
        Some(N),
        "a workspace write was lost"
    );
    let ids: Vec<&str> = items
        .iter()
        .map(|row| row["id"].as_str().unwrap_or_default())
        .collect();
    for staged in &staged_ids {
        assert!(ids.contains(&staged.as_str()), "{staged} is not stored");
    }
    assert_eq!(
        staged_ids
            .iter()
            .collect::<std::collections::BTreeSet<_>>()
            .len(),
        N,
        "two proposals were handed the same id"
    );

    // The write landed where the owner keeps it: the `workspace_os_state`
    // SQLite row `load_state` reads *before* the JSON file. The standalone
    // store wrote the file alone, so a fresh handle saw none of this.
    let reopened = WorkspaceOsStore::open(data.path()).load_state();
    assert_eq!(
        reopened["review_items"].as_array().map(Vec::len),
        Some(N),
        "a fresh handle over the same directory does not see the staged proposals"
    );
}
