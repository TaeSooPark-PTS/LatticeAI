use std::sync::Arc;

use serde_json::{json, Value};

use crate::governance::workflow_designer::recipes::{build_recipe_workflow, RECIPES};
use crate::governance::workflow_designer::store::WorkflowStore;

use super::{BrainEventPayload, TriggerEngine};

fn open_engine() -> (tempfile::TempDir, Arc<WorkflowStore>, Arc<TriggerEngine>) {
    let dir = tempfile::tempdir().expect("tempdir");
    let store = Arc::new(WorkflowStore::open(dir.path().join("workspace_os.json")));
    let engine = Arc::new(TriggerEngine::new(Arc::clone(&store), 5.0));
    (dir, store, engine)
}

fn install_recipe_workflow(
    store: &WorkflowStore,
    recipe_id: &str,
    enabled: bool,
    workspace: &str,
) -> String {
    let recipe = RECIPES
        .iter()
        .find(|item| item.id == recipe_id)
        .expect("recipe exists");
    let (name, nodes, metadata) = build_recipe_workflow(recipe, enabled);
    let wf = store.create_workflow(
        &name,
        vec![],
        nodes,
        serde_json::to_value(&metadata).expect("metadata"),
        Some("owner@local"),
        workspace,
        None,
    );
    wf.get("id").and_then(Value::as_str).unwrap().to_string()
}

#[test]
fn interval_automation_arms_fires_when_due_and_creates_review_draft() {
    let (_dir, store, engine) = open_engine();
    let wf_id = install_recipe_workflow(&store, "daily-memory-digest", true, "personal");

    let t0 = 1_700_000_000.0;
    let first = engine.tick_once(t0);
    assert_eq!(first.armed_changes, 1);
    assert_eq!(first.fired, 0);

    let armed = engine.armed_rows(Some("personal"));
    assert_eq!(armed.len(), 1);
    assert_eq!(armed[0]["workflow_id"], wf_id);
    assert_eq!(armed[0]["kind"], "schedule");
    assert!(armed[0]["next_run_at"].is_string());

    let before_due = engine.tick_once(t0 + 3_600.0);
    assert_eq!(before_due.fired, 0);

    let at_due = engine.tick_once(t0 + 86_400.0);
    assert_eq!(at_due.fired, 1);
    assert_eq!(at_due.run_ids.len(), 1);
    assert_eq!(at_due.review_ids.len(), 1);

    let runs = store.list_workflow_runs(Some(&wf_id), 10, "personal");
    assert_eq!(runs.len(), 1);
    assert_eq!(runs[0]["status"], "ok");
    assert_eq!(runs[0]["mode"], "scheduled");

    let doc = store.os.load_state();
    let reviews = doc["review_items"].as_array().expect("review items");
    assert_eq!(reviews.len(), 1);
    assert_eq!(reviews[0]["source"], "trigger");
    assert_eq!(reviews[0]["status"], "pending");
    assert_eq!(reviews[0]["payload"]["workflow_id"], wf_id);
}

#[test]
fn disabled_recipe_does_not_arm_or_fire() {
    let (_dir, store, engine) = open_engine();
    let _wf_id = install_recipe_workflow(&store, "daily-memory-digest", false, "personal");

    let report = engine.tick_once(1_700_000_000.0);
    assert_eq!(report.fired, 0);
    assert!(engine.armed_rows(Some("personal")).is_empty());
}

#[test]
fn brain_event_dispatches_only_to_matching_workspace() {
    let (_dir, store, engine) = open_engine();
    let wf_personal = install_recipe_workflow(&store, "follow-up-radar", true, "personal");
    let _wf_other = install_recipe_workflow(&store, "follow-up-radar", true, "org-2");

    engine.sync_armed(1_700_000_000.0);
    assert_eq!(engine.armed_rows(Some("personal")).len(), 1);
    assert_eq!(
        engine.armed_rows(Some("personal"))[0]["kind"],
        "brain_event"
    );

    let event = BrainEventPayload {
        node_id: "webdoc:abc123".into(),
        title: "Q4 Planning Notes".into(),
        source_type: "markdown".into(),
        workspace_id: "personal".into(),
        owner: Some("owner@local".into()),
        visibility: None,
    };
    let report = engine.dispatch_brain_event(&event, 1_700_000_120.0);
    assert_eq!(report.fired, 1);
    assert_eq!(report.review_ids.len(), 1);

    assert_eq!(
        store
            .list_workflow_runs(Some(&wf_personal), 10, "personal")
            .len(),
        1
    );
    assert_eq!(store.list_workflow_runs(None, 10, "org-2").len(), 0);
}

#[test]
fn unsupported_unattended_nodes_are_skipped_with_reason() {
    let (_dir, store, engine) = open_engine();
    let wf = store.create_workflow(
        "Risky Scheduled Tool",
        vec![],
        vec![
            json!({"id": "trigger", "type": "trigger", "config": {"trigger": "interval", "interval_seconds": 120, "enabled": true}, "next": "tool1"}),
            json!({"id": "tool1", "type": "tool", "config": {"tool": "shell"}, "next": "draft"}),
            json!({"id": "draft", "type": "agent", "config": {"prompt": "summarize"}, "next": null}),
        ],
        json!({"automation_state": "enabled"}),
        Some("owner@local"),
        "personal",
        None,
    );
    let wf_id = wf.get("id").and_then(Value::as_str).unwrap().to_string();

    let report = engine.tick_once(1_700_000_000.0);
    assert_eq!(report.fired, 0);
    assert_eq!(report.skipped, 1);

    let armed = engine.armed_rows(Some("personal"));
    assert_eq!(armed.len(), 1);
    assert_eq!(armed[0]["workflow_id"], wf_id);
    assert_eq!(armed[0]["blocked"], true);
    assert_eq!(armed[0]["skip_reason"], "requires_manual_run");
}

fn brain_event_workflow(
    store: &WorkflowStore,
    owner: &str,
    workspace: &str,
    config: Value,
) -> String {
    let mut trigger = json!({"trigger": "brain_event", "enabled": true});
    if let (Some(base), Some(extra)) = (trigger.as_object_mut(), config.as_object()) {
        for (key, value) in extra {
            base.insert(key.clone(), value.clone());
        }
    }
    let wf = store.create_workflow(
        "Event Watch",
        vec![],
        vec![
            json!({"id": "trigger", "type": "trigger", "config": trigger, "next": "draft"}),
            json!({"id": "draft", "type": "agent", "config": {"prompt": "summarize"}, "next": null}),
        ],
        json!({"automation_state": "enabled"}),
        Some(owner),
        workspace,
        None,
    );
    wf.get("id").and_then(Value::as_str).unwrap().to_string()
}

fn event(
    owner: Option<&str>,
    visibility: Option<&str>,
    source_type: &str,
    title: &str,
) -> BrainEventPayload {
    BrainEventPayload {
        node_id: "file:abc".into(),
        title: title.into(),
        source_type: source_type.into(),
        workspace_id: "team".into(),
        owner: owner.map(str::to_string),
        visibility: visibility.map(str::to_string),
    }
}

const NOW: f64 = 1_700_000_000.0;

#[test]
fn ownerless_local_workflows_still_fire_on_ownerless_local_items() {
    let (_dir, store, engine) = open_engine();
    brain_event_workflow(&store, "", "team", json!({}));

    let foreign =
        engine.dispatch_brain_event(&event(Some("bob@team"), None, "upload", "Plan"), NOW);
    assert_eq!(foreign.fired, 0);
    let local = engine.dispatch_brain_event(&event(None, None, "upload", "Plan"), NOW);
    assert_eq!(local.fired, 1);
}

#[test]
fn brain_event_trigger_defaults_to_the_owners_own_items() {
    let (_dir, store, engine) = open_engine();
    brain_event_workflow(&store, "alice@team", "team", json!({}));

    let foreign = engine.dispatch_brain_event(
        &event(Some("bob@team"), Some("workspace"), "upload", "Plan"),
        NOW,
    );
    assert_eq!(foreign.fired, 0);
    let unowned = engine.dispatch_brain_event(&event(None, None, "upload", "Plan"), NOW);
    assert_eq!(unowned.fired, 0);
    let own = engine.dispatch_brain_event(
        &event(Some(" Alice@Team "), Some("private"), "upload", "Plan"),
        NOW,
    );
    assert_eq!(own.fired, 1);
}

#[test]
fn unknown_owner_scope_falls_back_to_self() {
    let (_dir, store, engine) = open_engine();
    brain_event_workflow(
        &store,
        "alice@team",
        "team",
        json!({"owner_scope": "everyone"}),
    );
    let report = engine.dispatch_brain_event(
        &event(Some("bob@team"), Some("workspace"), "upload", "Plan"),
        NOW,
    );
    assert_eq!(report.fired, 0);
}

#[test]
fn workspace_scope_never_fires_on_another_users_private_item() {
    let (_dir, store, engine) = open_engine();
    brain_event_workflow(
        &store,
        "alice@team",
        "team",
        json!({"owner_scope": "workspace"}),
    );

    for visibility in [Some("private"), Some("PRIVATE"), None, Some("legacy")] {
        let report = engine
            .dispatch_brain_event(&event(Some("bob@team"), visibility, "upload", "Plan"), NOW);
        assert_eq!(report.fired, 0, "{visibility:?}");
    }
    let shared = engine.dispatch_brain_event(
        &event(Some("bob@team"), Some("workspace"), "upload", "Plan"),
        NOW,
    );
    assert_eq!(shared.fired, 1);
    let own_private = engine.dispatch_brain_event(
        &event(Some("alice@team"), Some("private"), "upload", "Plan"),
        NOW,
    );
    assert_eq!(own_private.fired, 1);
}

#[test]
fn workspace_scope_never_crosses_workspaces() {
    let (_dir, store, engine) = open_engine();
    brain_event_workflow(
        &store,
        "alice@team",
        "other",
        json!({"owner_scope": "workspace"}),
    );
    let report = engine.dispatch_brain_event(
        &event(Some("alice@team"), Some("workspace"), "upload", "Plan"),
        NOW,
    );
    assert_eq!(report.fired, 0);
    assert!(store.list_workflow_runs(None, 10, "other").is_empty());
}

#[test]
fn source_types_and_title_contains_filter_events() {
    let (_dir, store, engine) = open_engine();
    brain_event_workflow(
        &store,
        "alice@team",
        "team",
        json!({"source_types": ["upload", "Web_URL"], "title_contains": "Q4 Plan"}),
    );

    let wrong_source =
        engine.dispatch_brain_event(&event(Some("alice@team"), None, "note", "q4 planning"), NOW);
    assert_eq!(wrong_source.fired, 0);
    let wrong_title =
        engine.dispatch_brain_event(&event(Some("alice@team"), None, "upload", "Budget"), NOW);
    assert_eq!(wrong_title.fired, 0);
    let upload = engine.dispatch_brain_event(
        &event(Some("alice@team"), None, "upload", "Notes on q4 planning"),
        NOW,
    );
    assert_eq!(upload.fired, 1);
    let web = engine.dispatch_brain_event(
        &event(Some("alice@team"), None, "web_url", "Q4 PLAN review"),
        NOW,
    );
    assert_eq!(web.fired, 1);
}

#[test]
fn trigger_config_written_by_the_form_validates_and_bad_values_are_rejected() {
    use crate::governance::workflow_designer::definition::validate_definition;

    let form = json!({"name": "wf", "nodes": [
        {"id": "trigger", "type": "trigger", "config": {"trigger": "brain_event", "owner_scope": "workspace", "source_types": ["upload", "note"], "title_contains": "plan"}, "next": "draft"},
        {"id": "draft", "type": "agent", "config": {"prompt": "p"}, "next": null},
    ]});
    assert!(validate_definition(&form).is_empty());

    let bad = json!({"name": "wf", "nodes": [
        {"id": "trigger", "type": "trigger", "config": {"trigger": "brain_event", "owner_scope": "all", "source_types": "upload", "title_contains": 3}, "next": null},
    ]});
    let errors = validate_definition(&bad);
    assert_eq!(errors.len(), 3, "{errors:?}");
}

#[test]
fn update_definition_keeps_the_trigger_filter_config() {
    let (_dir, store, engine) = open_engine();
    let wf_id = brain_event_workflow(&store, "alice@team", "team", json!({}));
    let nodes = vec![
        json!({"id": "trigger", "type": "trigger", "config": {"trigger": "brain_event", "enabled": true, "owner_scope": "workspace", "source_types": ["upload"], "title_contains": "plan"}, "next": "draft"}),
        json!({"id": "draft", "type": "agent", "config": {"prompt": "summarize"}, "next": null}),
    ];
    store
        .update_workflow_definition(&wf_id, "team", None, Some(nodes), None)
        .expect("updated");
    let shared = engine.dispatch_brain_event(
        &event(Some("bob@team"), Some("workspace"), "upload", "Plan B"),
        NOW,
    );
    assert_eq!(shared.fired, 1);
    let filtered = engine.dispatch_brain_event(
        &event(Some("bob@team"), Some("workspace"), "note", "Plan B"),
        NOW,
    );
    assert_eq!(filtered.fired, 0);
}
