//! v13.0.0 — Atomic single-transaction review mutations & reversible one-click
//! folder reorganization undo.

use std::collections::HashMap;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;

use axum::http::HeaderMap;
use lattice_agent::kernel::proposals::{NewReviewItem, ProposalStore};
use lattice_auth::{AuthConfig, AuthState, Identity};
use lattice_platform::governance::change_proposals;
use lattice_platform::governance::review_queue::GovernanceState;
use lattice_platform::workspaceos::workspace::WorkspaceOsStore;
use serde_json::json;

fn test_governance() -> (
    tempfile::TempDir,
    GovernanceState,
    Arc<AtomicUsize>,
    Identity,
) {
    let dir = tempfile::tempdir().expect("tempdir");
    let data_dir = dir.path().join("data");
    let agent_root = dir.path().join("agent_workspace");
    std::fs::create_dir_all(&data_dir).unwrap();
    std::fs::create_dir_all(&agent_root).unwrap();

    let sink_count = Arc::new(AtomicUsize::new(0));
    let sink_clone = Arc::clone(&sink_count);
    let store = Arc::new(WorkspaceOsStore::open(&data_dir).with_event_sink(Arc::new(
        move |_event| {
            sink_clone.fetch_add(1, Ordering::SeqCst);
        },
    )));
    let auth = AuthState::new(AuthConfig::from_map(&HashMap::new(), Some(&data_dir)));
    let user = auth.require_user(&HeaderMap::new()).expect("local owner");
    let state = GovernanceState::with_store(auth, store, &agent_root, None);
    (dir, state, sink_count, user)
}

#[tokio::test]
async fn folder_reorganization_stages_one_click_undo_and_rolls_back_cleanly() {
    let (_dir, state, sink_count, user) = test_governance();
    let inbox = state.agent_root.join("inbox");
    std::fs::create_dir_all(&inbox).unwrap();
    std::fs::write(
        inbox.join("rfc.md"),
        "# Architecture RFC
",
    )
    .unwrap();
    std::fs::write(
        inbox.join("budget.csv"),
        "item,cost
gpu,120
",
    )
    .unwrap();

    // Stage a forward folder reorganization proposal.
    let created = state
        .create(&NewReviewItem {
            title: "폴더 정리 제안: inbox".into(),
            summary: "주제별 하위 폴더로 이동".into(),
            source: "change_proposal".into(),
            kind: "folder_reorganization".into(),
            payload: json!({
                "root": "inbox",
                "moves": [
                    {"source": "rfc.md", "target": "topics/architecture/rfc.md"},
                    {"source": "budget.csv", "target": "topics/finance/budget.csv"}
                ]
            }),
            provenance: json!({"proposed_by": "workspace_organizer"}),
            user_email: None,
            workspace_id: Some("personal".into()),
        })
        .expect("stage reorganization proposal");

    // Creating 1 review item emitted exactly 1 timeline event in 1 atomic transaction.
    assert_eq!(sink_count.load(Ordering::SeqCst), 1);

    let forward_id = created["id"].as_str().expect("forward proposal id");
    let applied = change_proposals::apply_proposal(&state, forward_id, &user, Some("personal"))
        .await
        .expect("apply forward reorganization");

    assert_eq!(applied["applied"], json!(true));
    assert!(!inbox.join("rfc.md").exists());
    assert!(inbox.join("topics/architecture/rfc.md").is_file());
    assert!(inbox.join("topics/finance/budget.csv").is_file());

    // An undo proposal was automatically staged and linked in both `undo_proposal_id`
    // and the approved item's `provenance.undo_proposal_id`.
    let undo_id = applied["undo_proposal_id"]
        .as_str()
        .expect("auto-staged undo_proposal_id")
        .to_string();
    assert_eq!(
        applied["item"]["provenance"]["undo_proposal_id"],
        json!(undo_id)
    );
    assert_eq!(applied["moves"]["undo_proposal_id"], json!(undo_id));

    // Approving the undo proposal restores both files to their original paths
    // and does NOT stage an infinite undo-of-undo proposal.
    let rolled_back = change_proposals::apply_proposal(&state, &undo_id, &user, Some("personal"))
        .await
        .expect("apply one-click undo proposal");
    assert_eq!(rolled_back["applied"], json!(true));
    assert!(rolled_back.get("undo_proposal_id").is_none());
    assert!(inbox.join("rfc.md").is_file());
    assert!(inbox.join("budget.csv").is_file());
    assert!(!inbox.join("topics/architecture/rfc.md").exists());
    assert!(!inbox.join("topics/finance/budget.csv").exists());

    // Verify workspace_os state contains all 4 timeline events cleanly (create forward,
    // create undo, approve forward, approve undo).
    let doc = state.store().load_state();
    let timeline = doc["timeline"].as_array().expect("timeline array");
    assert_eq!(timeline.len(), 4);
    assert_eq!(sink_count.load(Ordering::SeqCst), 4);
}
