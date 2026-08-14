//! The other end of the change-proposal pipeline: what the **agent loop**
//! stages, these routes must list and apply.
//!
//! `lattice-agent` cannot depend on this crate, so the loop stages through a
//! port (`lattice_agent::proposals::ProposalStore`) that `GovernanceState`
//! implements. Both crates having tests that pass in isolation proves nothing
//! about the seam between them — this file is the seam:
//!
//! 1. the loop's own governor, with the Review Center's store injected, stages
//!    a proposal, and `GET /api/proposals` lists it and `POST …/approve`
//!    applies it to the file on disk;
//! 2. the loop's *standalone* store (`JsonProposalStore`, for an install with
//!    no Review Center in process) writes a document `GovernanceState::open`
//!    reads back without losing the item — the byte-compatibility claim its
//!    doc comment makes, checked rather than asserted in prose.

mod review_queue_harness;

use std::sync::Arc;

use lattice_agent::policy::ToolPolicy;
use lattice_agent::proposals::{
    Governor, JsonProposalStore, NewReviewItem, ProposalStore, Verdict,
};
use lattice_agent::sandbox::Workspace;
use lattice_auth::{AuthConfig, AuthState, Clock};
use lattice_platform::review_queue::GovernanceState;
use review_queue_harness::Install;
use serde_json::{json, Value};

fn write_policy() -> ToolPolicy {
    ToolPolicy {
        risk: "write".into(),
        ..ToolPolicy::default()
    }
}

fn args(value: Value) -> serde_json::Map<String, Value> {
    value.as_object().expect("object").clone()
}

#[tokio::test]
async fn a_loop_staged_proposal_is_listed_and_applied_by_the_review_center() {
    let install = Install::start().await;
    let workspace = Workspace::new(&install.agent_root).expect("workspace");
    std::fs::write(workspace.root().join("loop-staged.md"), "before\n").expect("seed");

    // The loop's governor, with the product's store behind it.
    let verdict = Governor {
        workspace: &workspace,
        store: &install.gov,
        user_email: Some("owner@lattice.test"),
        workspace_id: Some("personal"),
        conversation_id: Some("conv-loop"),
    }
    .review(
        "write_file",
        &args(json!({"path": "loop-staged.md", "content": "after\n"})),
        &write_policy(),
    );
    let Verdict::Proposed { proposal, .. } = verdict else {
        panic!("an overwrite under strict must stage: {verdict:?}");
    };
    let item_id = proposal["id"].as_str().expect("id").to_string();
    // Staging never writes the file — that is the whole contract.
    assert_eq!(
        std::fs::read_to_string(workspace.root().join("loop-staged.md")).expect("read"),
        "before\n"
    );

    // 1. The Review Center lists it, through the same route the SPA calls.
    let listed = install
        .issue("GET", "/api/proposals", None, "session:owner")
        .await;
    assert_eq!(listed.status, 200, "{}", listed.body);
    let body: Value = serde_json::from_str(&listed.body).expect("json");
    let mine = body["items"]
        .as_array()
        .expect("items")
        .iter()
        .find(|item| item["id"] == json!(item_id))
        .unwrap_or_else(|| panic!("the staged proposal is not listed: {}", listed.body));
    assert_eq!(mine["source"], json!("change_proposal"));
    assert_eq!(mine["kind"], json!("file_update"));
    assert_eq!(mine["payload"]["path"], json!("loop-staged.md"));
    assert_eq!(mine["payload"]["new_content"], json!("after\n"));
    assert_eq!(
        mine["payload"]["diff"],
        json!([
            "--- a/loop-staged.md",
            "+++ b/loop-staged.md",
            "@@ -1 +1 @@",
            "-before",
            "+after"
        ])
    );
    assert_eq!(mine["provenance"]["proposed_by"], json!("agent"));
    assert_eq!(mine["provenance"]["conversation_id"], json!("conv-loop"));

    // 2. Approving it applies the staged content exactly as reviewed.
    let approved = install
        .issue(
            "POST",
            &format!("/api/proposals/{item_id}/approve"),
            None,
            "session:owner",
        )
        .await;
    assert_eq!(approved.status, 200, "{}", approved.body);
    let applied: Value = serde_json::from_str(&approved.body).expect("json");
    assert_eq!(applied["applied"], json!(true));
    assert_eq!(applied["path"], json!("loop-staged.md"));
    assert_eq!(applied["item"]["status"], json!("approved"));
    assert_eq!(
        std::fs::read_to_string(workspace.root().join("loop-staged.md")).expect("read"),
        "after\n"
    );

    // 3. …and the base-SHA check the staging wrote is live: a second approve
    //    of the same proposal is the "already resolved" 400 of this surface.
    let again = install
        .issue(
            "POST",
            &format!("/api/proposals/{item_id}/approve"),
            None,
            "session:owner",
        )
        .await;
    assert_eq!(again.status, 400, "{}", again.body);
    assert_eq!(
        serde_json::from_str::<Value>(&again.body).expect("json")["detail"],
        json!("change proposal conflict (already_approved): loop-staged.md")
    );
}

#[tokio::test]
async fn a_proposal_staged_against_a_drifted_file_conflicts_with_both_shapes() {
    let install = Install::start().await;
    let workspace = Workspace::new(&install.agent_root).expect("workspace");
    std::fs::write(workspace.root().join("drifts.md"), "base\n").expect("seed");
    let staged = |content: &str| {
        Governor {
            workspace: &workspace,
            store: &install.gov,
            user_email: Some("owner@lattice.test"),
            workspace_id: Some("personal"),
            conversation_id: None,
        }
        .review(
            "write_file",
            &args(json!({"path": "drifts.md", "content": content})),
            &write_policy(),
        )
    };
    let Verdict::Proposed {
        proposal: first, ..
    } = staged("first\n")
    else {
        panic!("must stage");
    };
    let Verdict::Proposed {
        proposal: second, ..
    } = staged("second\n")
    else {
        panic!("must stage");
    };
    // Someone edits the file out of band, after both proposals were staged.
    std::fs::write(workspace.root().join("drifts.md"), "drifted\n").expect("drift");

    // The Review Center path answers the structured 409…
    let review_center = install
        .issue(
            "POST",
            &format!(
                "/automation/reviews/{}/approve",
                first["id"].as_str().expect("id")
            ),
            None,
            "session:owner",
        )
        .await;
    assert_eq!(review_center.status, 409, "{}", review_center.body);
    let detail =
        serde_json::from_str::<Value>(&review_center.body).expect("json")["detail"].clone();
    assert_eq!(detail["error"], json!("change_proposal_conflict"));
    assert_eq!(detail["conflict"], json!(true));
    assert_eq!(detail["reason"], json!("file_modified_since_proposal"));
    assert_eq!(detail["path"], json!("drifts.md"));
    assert_eq!(detail["kind"], json!("file_update"));
    assert_eq!(
        detail["base_sha256"],
        json!(lattice_agent::proposals::sha256_text("base\n"))
    );
    assert_eq!(
        detail["current_sha256"],
        json!(lattice_agent::proposals::sha256_text("drifted\n"))
    );
    assert!(detail["rebase_hint"]
        .as_str()
        .expect("hint")
        .contains("거부"));

    // …and `/api/proposals` stringifies the same conflict into its 400.
    let proposals = install
        .issue(
            "POST",
            &format!(
                "/api/proposals/{}/approve",
                second["id"].as_str().expect("id")
            ),
            None,
            "session:owner",
        )
        .await;
    assert_eq!(proposals.status, 400, "{}", proposals.body);
    assert_eq!(
        serde_json::from_str::<Value>(&proposals.body).expect("json")["detail"],
        json!("change proposal conflict (file_modified_since_proposal): drifts.md")
    );
    // Neither refusal touched the file.
    assert_eq!(
        std::fs::read_to_string(workspace.root().join("drifts.md")).expect("read"),
        "drifted\n"
    );
}

#[tokio::test]
async fn the_standalone_json_store_writes_a_document_governance_state_reads() {
    // An install with no Review Center in process: the loop stages through
    // `JsonProposalStore`, and a Review Center opened over that data directory
    // afterwards must see the item rather than a document it cannot parse.
    let data = tempfile::tempdir().expect("data dir");
    let agent_root = data.path().join("agent_workspace");
    let workspace = Workspace::new(&agent_root).expect("workspace");
    std::fs::write(workspace.root().join("standalone.md"), "before\n").expect("seed");

    let json_store = JsonProposalStore::new(data.path());
    let Verdict::Proposed { proposal, .. } = (Governor {
        workspace: &workspace,
        store: &json_store,
        user_email: Some("owner@lattice.test"),
        workspace_id: None,
        conversation_id: None,
    })
    .review(
        "write_file",
        &args(json!({"path": "standalone.md", "content": "after\n"})),
        &write_policy(),
    ) else {
        panic!("must stage");
    };
    let staged_id = proposal["id"].as_str().expect("id").to_string();

    // Now the platform opens the same directory and stages one of its own.
    let mut env = std::collections::HashMap::new();
    env.insert(
        "LATTICEAI_DATA_DIR".to_string(),
        data.path().to_string_lossy().into_owned(),
    );
    let mut config = AuthConfig::from_map(&env, None);
    config.data_dir = data.path().to_path_buf();
    let auth = AuthState::with_clock(config, Clock::frozen(1_786_000_000.0));
    let gov = GovernanceState::open(
        Arc::clone(&auth),
        data.path().to_path_buf(),
        agent_root.clone(),
        None,
    );
    let platform_item = ProposalStore::create(
        &gov,
        &NewReviewItem {
            title: "파일 수정 제안: other.md".into(),
            summary: "s".into(),
            source: "change_proposal".into(),
            kind: "file_update".into(),
            payload: json!({"path": "other.md"}),
            provenance: json!({"proposed_by": "agent"}),
            user_email: Some("owner@lattice.test".into()),
            workspace_id: None,
        },
    )
    .expect("platform create");

    let document: Value = serde_json::from_str(
        &std::fs::read_to_string(data.path().join("workspace_os.json")).expect("read"),
    )
    .expect("json");
    let rows = document["review_items"].as_array().expect("rows");
    assert_eq!(
        rows.len(),
        2,
        "the platform's save must not drop the standalone item"
    );
    let ids: Vec<&str> = rows
        .iter()
        .map(|row| row["id"].as_str().unwrap_or_default())
        .collect();
    assert!(ids.contains(&staged_id.as_str()), "{ids:?}");
    assert!(
        ids.contains(&platform_item["id"].as_str().expect("id")),
        "{ids:?}"
    );
    // The two writers agree on the shape, which is what "byte-compatible" has
    // to mean for a store two crates append to.
    let keys = |value: &Value| {
        let mut names: Vec<String> = value.as_object().expect("object").keys().cloned().collect();
        names.sort();
        names
    };
    assert_eq!(keys(&rows[0]), keys(&rows[1]));
    assert_eq!(rows[0]["status"], json!("pending"));
    assert_eq!(rows[0]["workspace_id"], json!("personal"));
    // …and the document itself is still the workspace OS, not a fragment.
    for key in ["version", "identity", "active_workspace", "workspaces"] {
        assert!(document.get(key).is_some(), "missing {key}");
    }
}
