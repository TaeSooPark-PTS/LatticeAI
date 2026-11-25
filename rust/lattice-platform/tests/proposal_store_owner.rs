use std::sync::Arc;

use lattice_agent::kernel::proposals::{
    install_document_writer, DocumentWriter, JsonProposalStore, NewReviewItem, ProposalStore,
};
use lattice_platform::workspaceos::workspace::WorkspaceOsStore;
use serde_json::{json, Value};

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

    let reopened = WorkspaceOsStore::open(data.path()).load_state();
    assert_eq!(
        reopened["review_items"].as_array().map(Vec::len),
        Some(N),
        "a fresh handle over the same directory does not see the staged proposals"
    );
}
