use super::*;
use std::path::PathBuf;
use std::sync::Mutex;

#[derive(Debug, Default)]
struct Recorder {
    items: Mutex<Vec<NewReviewItem>>,
    fail: bool,
}

impl ProposalStore for Recorder {
    fn create(&self, item: &NewReviewItem) -> Result<Value, String> {
        if self.fail {
            return Err("store is read-only".into());
        }
        self.items.lock().expect("lock").push(item.clone());
        Ok(json!({"id": "review-test", "title": item.title}))
    }
}

fn workspace() -> (tempfile::TempDir, Workspace) {
    let dir = tempfile::tempdir().expect("tempdir");
    let workspace = Workspace::new(dir.path().join("agent_workspace")).expect("workspace");
    (dir, workspace)
}

fn args(value: Value) -> Map<String, Value> {
    value.as_object().expect("object").clone()
}

fn write_policy() -> ToolPolicy {
    ToolPolicy {
        risk: "write".into(),
        ..ToolPolicy::default()
    }
}

#[test]
fn an_ungoverned_tool_and_an_additive_write_never_stage() {
    let (_dir, workspace) = workspace();
    let store = Recorder::default();
    let governor = Governor {
        workspace: &workspace,
        store: &store,
        user_email: None,
        workspace_id: None,
        conversation_id: None,
    };
    assert_eq!(
        governor.review("run_command", &Map::new(), &write_policy()),
        Verdict::Silent
    );
    let verdict = governor.review(
        "write_file",
        &args(json!({"path": "new.md", "content": "hi"})),
        &write_policy(),
    );
    match verdict {
        Verdict::AllowAdditive(classification) => {
            assert_eq!(classification.change_class, "additive");
        }
        other => panic!("expected allow_additive, got {other:?}"),
    }
    assert!(store.items.lock().expect("lock").is_empty());
}

#[test]
fn an_overwrite_stages_the_payload_the_review_center_applies() {
    let (_dir, workspace) = workspace();
    std::fs::write(workspace.root().join("a.md"), "old\n").expect("seed");
    let store = Recorder::default();
    let governor = Governor {
        workspace: &workspace,
        store: &store,
        user_email: Some("owner@example.com"),
        workspace_id: Some("personal"),
        conversation_id: Some("conv-1"),
    };
    let verdict = governor.review(
        "write_file",
        &args(json!({"path": "a.md", "content": "new\n"})),
        &write_policy(),
    );
    assert!(matches!(verdict, Verdict::Proposed { .. }));
    let staged = store.items.lock().expect("lock").remove(0);
    assert_eq!(staged.title, "파일 수정 제안: a.md");
    assert_eq!(staged.source, "change_proposal");
    assert_eq!(staged.kind, "file_update");
    assert_eq!(staged.user_email.as_deref(), Some("owner@example.com"));
    let payload = &staged.payload;
    assert_eq!(payload["path"], json!("a.md"));
    assert_eq!(payload["new_content"], json!("new\n"));
    assert_eq!(payload["tier"], json!("small"));
    assert_eq!(payload["before_bytes"], json!(4));
    assert_eq!(payload["after_bytes"], json!(4));
    assert_eq!(payload["base_exists"], json!(true));
    assert_eq!(payload["base_sha256"], json!(sha256_text("old\n")));
    assert_eq!(
        payload["diff"],
        json!(["--- a/a.md", "+++ b/a.md", "@@ -1 +1 @@", "-old", "+new"])
    );
    assert_eq!(staged.provenance["proposed_by"], json!("agent"));
    assert_eq!(staged.provenance["change_class"], json!("mutation"));
    assert_eq!(staged.provenance["conversation_id"], json!("conv-1"));
    assert_eq!(
        staged.provenance["source_detail"],
        json!("agent change governor")
    );
    // Nothing was written: staging is the whole point.
    assert_eq!(
        std::fs::read_to_string(workspace.root().join("a.md")).expect("read"),
        "old\n"
    );
}

#[test]
fn an_undecidable_edit_falls_through_instead_of_staging_a_guess() {
    let (_dir, workspace) = workspace();
    std::fs::write(workspace.root().join("a.md"), "x\nx\n").expect("seed");
    let store = Recorder::default();
    let governor = Governor {
        workspace: &workspace,
        store: &store,
        user_email: None,
        workspace_id: None,
        conversation_id: None,
    };
    // `old_string` absent, not present, and ambiguous — all three are the
    // same `Silent`, exactly as Python collapses them.
    for call in [
        json!({"path": "a.md", "new_string": "y"}),
        json!({"path": "a.md", "old_string": "zzz", "new_string": "y"}),
        json!({"path": "a.md", "old_string": "x", "new_string": "y"}),
    ] {
        assert_eq!(
            governor.review("edit_file", &args(call), &write_policy()),
            Verdict::Silent
        );
    }
    // …and `replace_all` makes the ambiguous one decidable again.
    let verdict = governor.review(
        "edit_file",
        &args(json!({"path": "a.md", "old_string": "x", "new_string": "y", "replace_all": true})),
        &write_policy(),
    );
    assert!(matches!(verdict, Verdict::Proposed { .. }));
    assert_eq!(
        store.items.lock().expect("lock")[0].payload["new_content"],
        json!("y\ny\n")
    );
}

#[test]
fn a_store_that_refuses_is_a_failure_not_a_silent_fallthrough() {
    let (_dir, workspace) = workspace();
    std::fs::write(workspace.root().join("a.md"), "old\n").expect("seed");
    let store = Recorder {
        fail: true,
        ..Recorder::default()
    };
    let governor = Governor {
        workspace: &workspace,
        store: &store,
        user_email: None,
        workspace_id: None,
        conversation_id: None,
    };
    assert_eq!(
        governor.review(
            "write_file",
            &args(json!({"path": "a.md", "content": "new\n"})),
            &write_policy()
        ),
        Verdict::Failed("store is read-only".into())
    );
}

#[test]
fn a_path_outside_the_workspace_is_no_file_and_stages_as_a_create() {
    // `_snapshot` swallows the sandbox refusal exactly as Python's does,
    // so the proposal is staged against "no base" rather than crashing the
    // run. Approving it is what re-checks the path.
    let (_dir, workspace) = workspace();
    let store = Recorder::default();
    let governor = Governor {
        workspace: &workspace,
        store: &store,
        user_email: None,
        workspace_id: None,
        conversation_id: None,
    };
    assert_eq!(governor.snapshot("../escape.md"), (false, String::new()));
    assert!(!governor.path_is_file("../escape.md"));
}

#[test]
fn the_json_store_writes_the_shape_the_review_center_reads() {
    let dir = tempfile::tempdir().expect("tempdir");
    let store = JsonProposalStore::new(dir.path());
    let item = NewReviewItem {
        title: "파일 수정 제안: a.md".into(),
        summary: "s".into(),
        source: "change_proposal".into(),
        kind: "file_update".into(),
        payload: json!({"path": "a.md"}),
        provenance: json!({"proposed_by": "agent"}),
        user_email: Some("owner@example.com".into()),
        workspace_id: None,
    };
    let stored = store.create(&item).expect("create");
    assert!(stored["id"].as_str().expect("id").starts_with("review-"));
    assert_eq!(stored["id"].as_str().expect("id").len(), 7 + 16);
    assert_eq!(stored["status"], json!("pending"));
    assert_eq!(stored["snoozed_until"], Value::Null);
    assert_eq!(stored["workspace_id"], json!("personal"));
    assert_eq!(stored["created_at"], stored["updated_at"]);

    let raw = std::fs::read_to_string(store.state_path()).expect("read");
    assert!(raw.ends_with("\n"), "the file ends with a newline");
    let document: Value = serde_json::from_str(&raw).expect("json");
    assert_eq!(document["version"], json!(crate::VERSION));
    assert_eq!(document["active_workspace"], json!("personal"));
    assert_eq!(document["review_items"].as_array().expect("rows").len(), 1);
    assert_eq!(document["review_items"][0]["id"], stored["id"]);
    // A second item appends rather than replacing, and gets its own id
    // even when every hashed field is identical within the same second.
    let second = store.create(&item).expect("create");
    assert_ne!(second["id"], stored["id"]);
    let document: Value =
        serde_json::from_str(&std::fs::read_to_string(store.state_path()).expect("read"))
            .expect("json");
    assert_eq!(document["review_items"].as_array().expect("rows").len(), 2);
    // A blank title is the store's own refusal.
    assert!(store
        .create(&NewReviewItem {
            title: "  ".into(),
            ..item
        })
        .is_err());
}

#[test]
fn the_env_store_prefers_the_data_dir_over_home() {
    // No env mutation: the rule is asserted through the two constructors it
    // is built from, because a test that sets a process-wide variable races
    // every other test in the binary.
    assert_eq!(
        JsonProposalStore::new("/tmp/data").state_path(),
        PathBuf::from("/tmp/data/workspace_os.json")
    );
    assert!(JsonProposalStore::from_env()
        .state_path()
        .ends_with("workspace_os.json"));
}
