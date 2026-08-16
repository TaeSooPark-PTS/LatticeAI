//! Agent runs and workflows — the two record kinds this family writes.
//!
//! Port of the slice of `core/workspace_runs.py` these routes reach:
//! `list_agents`, `record_agent_run`, `create_workflow`, `list_workflows` and
//! `record_workflow_event`. The rest of that module (workflow *runs*, handoff
//! replay, run reconciliation) belongs to the automation and realtime families.
//!
//! Two things here are easy to get wrong and are pinned by tests:
//!
//! * **A simulation run never enters the knowledge graph.** `/workspace/agents/
//!   runs` has no `mode` field, so every run it records is a simulation — replay
//!   scaffolding, not an experience — and the record says so in `graph_skipped`
//!   rather than silently carrying a null node id.
//! * **The contract envelope is written after the run is stored**, then patched
//!   back in. Python does the same two-phase write, and the returned body is the
//!   patched one.

use serde_json::{json, Map, Value};

use super::pyutil::{json_hash_prefix, listify, now_iso, py_dumps};
use super::store::{StoreError, WorkspaceOsStore};

/// The contract family every observability record in the product carries.
pub const CONTRACT_FAMILY: &str = "agent-run-contract/v1";
/// The agent-run schema under that family.
pub const AGENT_RUN_SCHEMA: &str = "agent-run-contract/v1";
/// Statuses `AgentRunContract.is_terminal` treats as finished.
pub const TERMINAL_STATUSES: [&str; 8] = [
    "ok",
    "retried_ok",
    "failed",
    "rejected",
    "cancelled",
    "interrupted",
    "partial",
    "done",
];

/// How many runs `list_agents` answers with.
const RUN_WINDOW: usize = 100;

/// `list_agents` — the roster plus the most recent runs, newest first.
pub fn list_agents(store: &WorkspaceOsStore, workspace_id: Option<&str>) -> Value {
    let state = store.load_state();
    let mut runs = WorkspaceOsStore::scoped(listify(state.get("agent_runs")), workspace_id);
    if runs.len() > RUN_WINDOW {
        runs = runs.split_off(runs.len() - RUN_WINDOW);
    }
    runs.reverse();
    json!({
        "agents": state.get("agents").cloned().unwrap_or_else(|| json!([])),
        "runs": runs,
    })
}

/// What `record_agent_run` is asked to store.
#[derive(Debug, Clone, Default)]
pub struct AgentRunRequest {
    /// Which agent ran.
    pub agent_id: String,
    /// The lifecycle status it finished in.
    pub status: String,
    /// The goal it was given.
    pub input: String,
    /// What it produced (truncated to 1,000 characters when stored).
    pub output: String,
    /// The step timeline, replayed onto the workspace timeline.
    pub timeline: Vec<Value>,
    /// Related node ids.
    pub relationships: Vec<Value>,
    /// `"simulation"` from this route; a real run comes from the agent family.
    pub mode: String,
}

/// `record_agent_run`.
///
/// `graph` carries the seam's answer for a **non-simulation** run; a simulation
/// ignores it entirely, which is why the route may pass `None`.
pub fn record_agent_run(
    store: &WorkspaceOsStore,
    request: &AgentRunRequest,
    user_email: Option<&str>,
    workspace_id: Option<&str>,
    graph: Option<Result<Value, String>>,
) -> Result<Value, StoreError> {
    let state = store.load_state();
    let scope = WorkspaceOsStore::resolve_scope(workspace_id, &state);
    let now = now_iso();
    let mode = if request.mode.is_empty() {
        "simulation"
    } else {
        &request.mode
    };
    let mut run = json!({
        "id": format!("agent-run-{}", json_hash_prefix(
            &json!([request.agent_id, request.input, request.output, now]), 16)),
        "record_schema_version": 2,
        "agent_id": request.agent_id,
        "mode": mode,
        "status": request.status,
        "input": request.input,
        "output_preview": request.output.chars().take(1000).collect::<String>(),
        "user_email": user_email,
        "workspace_id": scope,
        "relationships": request.relationships,
        "timeline": request.timeline,
        "handoffs": [],
        "context_packets": [],
        "plan": [],
        "plan_review": {},
        "review_history": [],
        "retry_history": [],
        "memory_snapshots": [],
        "created_at": now,
    });
    if mode == "simulation" {
        // Simulated runs are replay scaffolding, not experiences — they must
        // never enter the knowledge graph as real provenance.
        run["graph_node_id"] = Value::Null;
        run["graph_skipped"] = json!("simulation runs are not recorded in the knowledge graph");
    } else {
        match graph {
            Some(Ok(ingested)) => {
                run["graph_node_id"] = ingested.get("node_id").cloned().unwrap_or(Value::Null);
            }
            Some(Err(error)) => run["graph_error"] = json!(error),
            None => {}
        }
    }

    let run_id = run["id"].as_str().unwrap_or_default().to_string();
    let stored = run.clone();
    store.mutate(|state| {
        let mut runs = listify(state.get("agent_runs"));
        runs.push(stored);
        state["agent_runs"] = Value::Array(runs);
        Ok(())
    })?;

    store.emit_replayable_timeline_events("agent", &run_id, &request.timeline, Some(&scope));
    if request.status == "failed" {
        store.emit_execution_event(
            "agent",
            "execution_failed",
            json!({"run_id": run_id, "agent_id": request.agent_id,
                   "status": request.status}),
            Some(&scope),
        );
    }
    store.record_timeline_event(
        "agent",
        "agent_run",
        json!({"run_id": run_id, "agent_id": request.agent_id, "status": request.status}),
        Some(&scope),
    );

    run["contract"] = run_record_contract(&run, "multi_agent");
    let contract = run["contract"].clone();
    store.mutate(|state| {
        let mut runs = listify(state.get("agent_runs"));
        for item in runs.iter_mut() {
            if item.get("id").and_then(Value::as_str) == Some(run_id.as_str()) {
                item["contract"] = contract.clone();
                break;
            }
        }
        state["agent_runs"] = Value::Array(runs);
        Ok(())
    })?;
    Ok(run)
}

/// `run_record_contract(run, runtime=…)` — the family envelope for one run.
pub fn run_record_contract(run: &Value, runtime: &str) -> Value {
    let timeline = listify(run.get("timeline"));
    let retries = listify(run.get("retry_history")).len();
    let mut roles: Vec<Value> = listify(run.get("roles_run"));
    if roles.is_empty() {
        roles = listify(run.get("requested_roles"));
    }
    if roles.is_empty() {
        roles = timeline
            .iter()
            .filter_map(|item| item.get("role").filter(|role| !role.is_null()).cloned())
            .map(|role| match role {
                Value::String(text) => json!(text),
                other => json!(other.to_string()),
            })
            .collect();
    }
    let status = run
        .get("status")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .unwrap_or("unknown");
    let identity = run
        .get("id")
        .filter(|value| !value.is_null())
        .or_else(|| run.get("run_id"))
        .cloned()
        .unwrap_or(Value::Null);
    let goal = run
        .get("input")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .or_else(|| run.get("goal").and_then(Value::as_str))
        .unwrap_or_default();
    let blocking: Vec<Value> = match run.get("error") {
        Some(Value::String(error)) if !error.is_empty() => vec![json!(error)],
        _ => Vec::new(),
    };

    json!({
        "run_id": identity,
        "agent_id": run.get("agent_id").and_then(Value::as_str).unwrap_or("agent:executor"),
        "runtime": runtime,
        "mode": run.get("mode").and_then(Value::as_str).unwrap_or("simulation"),
        "goal": goal,
        "roles": roles,
        "current_role": run.get("current_role").cloned().unwrap_or(Value::Null),
        "retries": retries,
        "timeline": timeline,
        "artifacts": [{
            "type": "run_record",
            "workspace_id": run.get("workspace_id").cloned().unwrap_or(Value::Null),
            "graph_node_id": run.get("graph_node_id").cloned().unwrap_or(Value::Null),
            "execution_mode": run.get("execution_mode").cloned().unwrap_or(Value::Null),
        }],
        "blocking_reasons": blocking,
        "is_terminal": TERMINAL_STATUSES.contains(&status),
        "family": CONTRACT_FAMILY,
        "schema_version": AGENT_RUN_SCHEMA,
        "kind": "agent_run",
        "id": identity,
        "status": status,
        "timestamp": now_iso(),
    })
}

// ── workflows ───────────────────────────────────────────────────────────────

/// A workflow decided but not yet written.
#[derive(Debug, Clone)]
pub struct WorkflowPlan {
    /// The record as it will be stored, before the graph outcome is attached.
    pub workflow: Value,
}

impl WorkflowPlan {
    /// The workflow id, for the ingest metadata.
    pub fn id(&self) -> &str {
        self.workflow
            .get("id")
            .and_then(Value::as_str)
            .unwrap_or_default()
    }
    /// The name, which is the ingest title.
    pub fn name(&self) -> &str {
        self.workflow
            .get("name")
            .and_then(Value::as_str)
            .unwrap_or_default()
    }
    /// The workspace the ingest should be tagged with.
    pub fn workspace_id(&self) -> Option<&str> {
        self.workflow.get("workspace_id").and_then(Value::as_str)
    }
    /// The steps, for the ingest metadata.
    pub fn steps(&self) -> Value {
        self.workflow
            .get("steps")
            .cloned()
            .unwrap_or_else(|| json!([]))
    }
}

/// Decide the workflow `create_workflow` will write.
pub fn plan_workflow(
    store: &WorkspaceOsStore,
    name: &str,
    steps: &[Value],
    metadata: &Value,
    user_email: Option<&str>,
    workspace_id: Option<&str>,
) -> WorkflowPlan {
    let state = store.load_state();
    let now = now_iso();
    let resolved_name = if name.is_empty() {
        "Untitled workflow"
    } else {
        name
    };
    WorkflowPlan {
        workflow: json!({
            "id": format!("workflow-{}", json_hash_prefix(
                &json!([name, steps, user_email, now]), 16)),
            "name": resolved_name,
            "steps": steps,
            "user_email": user_email,
            "workspace_id": WorkspaceOsStore::resolve_scope(workspace_id, &state),
            "metadata": if metadata.is_object() { metadata.clone() } else { json!({}) },
            "events": [{"type": "created", "timestamp": now}],
            "created_at": now,
            "updated_at": now,
        }),
    }
}

/// Write the planned workflow, carrying whatever the graph seam answered.
pub fn commit_workflow(
    store: &WorkspaceOsStore,
    plan: WorkflowPlan,
    graph: Option<Result<Value, String>>,
) -> Result<Value, StoreError> {
    let WorkflowPlan { mut workflow } = plan;
    match graph {
        Some(Ok(ingested)) => {
            workflow["graph_node_id"] = ingested.get("node_id").cloned().unwrap_or(Value::Null);
        }
        Some(Err(error)) => workflow["graph_error"] = json!(error),
        None => {}
    }
    let scope = WorkspaceOsStore::record_workspace(&workflow);
    let workflow_id = workflow["id"].as_str().unwrap_or_default().to_string();
    let name = workflow["name"].as_str().unwrap_or_default().to_string();
    let stored = workflow.clone();
    store.mutate(|state| {
        let mut workflows = listify(state.get("workflows"));
        workflows.push(stored);
        state["workflows"] = Value::Array(workflows);
        Ok(())
    })?;
    store.record_timeline_event(
        "workflow",
        "workflow_created",
        json!({"workflow_id": workflow_id, "name": name}),
        Some(&scope),
    );
    Ok(workflow)
}

/// `list_workflows(query, workspace_id)` — newest first, substring-filtered.
pub fn list_workflows(store: &WorkspaceOsStore, query: &str, workspace_id: Option<&str>) -> Value {
    let state = store.load_state();
    let mut workflows = WorkspaceOsStore::scoped(listify(state.get("workflows")), workspace_id);
    workflows.reverse();
    let needle = query.trim().to_lowercase();
    if !needle.is_empty() {
        workflows.retain(|workflow| {
            let name = workflow
                .get("name")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_lowercase();
            let steps = py_dumps(
                &workflow.get("steps").cloned().unwrap_or_else(|| json!([])),
                false,
            )
            .to_lowercase();
            name.contains(&needle) || steps.contains(&needle)
        });
    }
    json!({"workflows": workflows})
}

/// `record_workflow_event`.
pub fn record_workflow_event(
    store: &WorkspaceOsStore,
    workflow_id: &str,
    event_type: &str,
    payload: &Value,
) -> Result<Value, StoreError> {
    let event = json!({
        "type": event_type,
        "timestamp": now_iso(),
        "payload": if payload.is_object() { payload.clone() } else { json!({}) },
    });
    let workflow = store.mutate(|state| {
        let mut workflows = listify(state.get("workflows"));
        let found = workflows
            .iter_mut()
            .find(|item| item.get("id").and_then(Value::as_str) == Some(workflow_id))
            .ok_or_else(|| StoreError::NotFound(workflow_id.to_string()))?;
        let mut events = listify(found.get("events"));
        events.push(event.clone());
        found["events"] = Value::Array(events);
        found["updated_at"] = json!(now_iso());
        let updated = found.clone();
        state["workflows"] = Value::Array(workflows);
        Ok(updated)
    })?;
    store.record_timeline_event(
        "workflow",
        "workflow_event",
        json!({"workflow_id": workflow_id, "event_type": event_type}),
        None,
    );
    Ok(workflow)
}

/// The steps a `POST /workspace/vscode/send` records, as Python builds them.
pub fn vscode_steps(
    action: &str,
    file_path: Option<&str>,
    language: Option<&str>,
    chars: usize,
) -> Vec<Value> {
    vec![
        json!({"action": action, "file_path": file_path, "language": language}),
        json!({"action": "send_to_lattice", "chars": chars}),
    ]
}

/// The metadata a `POST /workspace/vscode/send` records.
pub fn vscode_metadata(file_path: Option<&str>, language: Option<&str>, preview: &str) -> Value {
    let mut metadata = Map::new();
    metadata.insert(
        "file_path".into(),
        file_path.map_or(Value::Null, |v| json!(v)),
    );
    metadata.insert(
        "language".into(),
        language.map_or(Value::Null, |v| json!(v)),
    );
    metadata.insert(
        "content_preview".into(),
        json!(preview.chars().take(500).collect::<String>()),
    );
    Value::Object(metadata)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn store() -> (tempfile::TempDir, WorkspaceOsStore) {
        let dir = tempfile::tempdir().expect("tempdir");
        let store = WorkspaceOsStore::open(dir.path());
        (dir, store)
    }

    fn request(status: &str) -> AgentRunRequest {
        AgentRunRequest {
            agent_id: "agent:executor".into(),
            status: status.into(),
            input: "요약해줘".into(),
            output: "요약했습니다.".into(),
            timeline: Vec::new(),
            relationships: Vec::new(),
            mode: String::new(),
        }
    }

    #[test]
    fn a_fresh_install_lists_five_agents_and_no_runs() {
        let (_dir, store) = store();
        let listing = list_agents(&store, None);
        assert_eq!(listing["agents"].as_array().unwrap().len(), 5);
        assert_eq!(listing["agents"][0]["id"], json!("agent:planner"));
        assert!(listing["runs"].as_array().unwrap().is_empty());
    }

    #[test]
    fn a_simulation_run_is_stored_with_its_contract_and_never_ingested() {
        let (_dir, store) = store();
        let run = record_agent_run(
            &store,
            &request("ok"),
            Some("owner@lattice.test"),
            None,
            Some(Ok(json!({"node_id": "should-be-ignored"}))),
        )
        .unwrap();
        assert!(run["id"].as_str().unwrap().starts_with("agent-run-"));
        assert_eq!(run["mode"], json!("simulation"));
        assert_eq!(run["graph_node_id"], Value::Null);
        assert_eq!(
            run["graph_skipped"],
            json!("simulation runs are not recorded in the knowledge graph")
        );
        assert_eq!(run["record_schema_version"], json!(2));
        assert_eq!(run["workspace_id"], json!("personal"));
        assert_eq!(run["output_preview"], json!("요약했습니다."));

        let contract = &run["contract"];
        assert_eq!(contract["family"], json!(CONTRACT_FAMILY));
        assert_eq!(contract["kind"], json!("agent_run"));
        assert_eq!(contract["runtime"], json!("multi_agent"));
        assert_eq!(contract["status"], json!("ok"));
        assert_eq!(contract["is_terminal"], json!(true));
        assert_eq!(contract["goal"], json!("요약해줘"));
        assert_eq!(contract["artifacts"][0]["type"], json!("run_record"));
        assert_eq!(contract["artifacts"][0]["workspace_id"], json!("personal"));
        assert_eq!(contract["id"], run["id"]);

        // The stored copy carries the same contract.
        let listing = list_agents(&store, None);
        assert_eq!(listing["runs"][0]["contract"]["id"], run["id"]);
    }

    #[test]
    fn a_real_run_carries_the_graph_outcome_and_a_failure_emits_an_event() {
        let (_dir, store) = store();
        let mut failed = request("failed");
        failed.mode = "live".into();
        let run = record_agent_run(
            &store,
            &failed,
            None,
            Some("org-x"),
            Some(Ok(json!({"node_id": "node-7"}))),
        )
        .unwrap();
        assert_eq!(run["graph_node_id"], json!("node-7"));
        assert!(run.get("graph_skipped").is_none());
        assert_eq!(run["contract"]["is_terminal"], json!(true));

        let events: Vec<String> = store.load_state()["timeline"]
            .as_array()
            .unwrap()
            .iter()
            .map(|event| event["event_type"].as_str().unwrap().to_string())
            .collect();
        assert_eq!(events, vec!["execution_failed", "agent_run"]);

        let mut broken = request("ok");
        broken.mode = "live".into();
        let run =
            record_agent_run(&store, &broken, None, None, Some(Err("seam down".into()))).unwrap();
        assert_eq!(run["graph_error"], json!("seam down"));
    }

    #[test]
    fn a_runs_own_timeline_is_replayed_onto_the_workspace_timeline() {
        let (_dir, store) = store();
        let mut with_timeline = request("ok");
        with_timeline.timeline = vec![
            json!({"event": "agent_started", "role": "planner"}),
            json!({"step": "not an execution event"}),
        ];
        record_agent_run(&store, &with_timeline, None, None, None).unwrap();
        let events: Vec<String> = store.load_state()["timeline"]
            .as_array()
            .unwrap()
            .iter()
            .map(|event| event["event_type"].as_str().unwrap().to_string())
            .collect();
        assert_eq!(events, vec!["agent_started", "agent_run"]);
    }

    #[test]
    fn the_contract_derives_roles_from_the_timeline_when_none_are_declared() {
        let contract = run_record_contract(
            &json!({
                "id": "r1", "status": "queued", "timeline": [{"role": "planner"}, {"role": 3}, {}],
                "retry_history": [{"n": 1}], "error": "boom",
            }),
            "multi_agent",
        );
        assert_eq!(contract["roles"], json!(["planner", "3"]));
        assert_eq!(contract["retries"], json!(1));
        assert_eq!(contract["is_terminal"], json!(false));
        assert_eq!(contract["blocking_reasons"], json!(["boom"]));
        assert_eq!(contract["agent_id"], json!("agent:executor"));
        assert_eq!(contract["mode"], json!("simulation"));

        let declared = run_record_contract(&json!({"roles_run": ["a"], "goal": "g"}), "single");
        assert_eq!(declared["roles"], json!(["a"]));
        assert_eq!(declared["goal"], json!("g"));
        assert_eq!(declared["status"], json!("unknown"));
        assert_eq!(declared["run_id"], Value::Null);
    }

    #[test]
    fn runs_are_scoped_and_windowed_newest_first() {
        let (_dir, store) = store();
        store
            .mutate(|state| {
                let runs: Vec<Value> = (0..120)
                    .map(|index| json!({"id": format!("r{index}"), "workspace_id": "personal"}))
                    .collect();
                state["agent_runs"] = Value::Array(runs);
                Ok(())
            })
            .unwrap();
        let listing = list_agents(&store, Some("personal"));
        let runs = listing["runs"].as_array().unwrap();
        assert_eq!(runs.len(), RUN_WINDOW);
        assert_eq!(runs[0]["id"], json!("r119"));
        assert_eq!(runs[99]["id"], json!("r20"));
        assert!(list_agents(&store, Some("org-x"))["runs"]
            .as_array()
            .unwrap()
            .is_empty());
    }

    #[test]
    fn a_workflow_is_planned_ingested_and_stored() {
        let (_dir, store) = store();
        let planned = plan_workflow(
            &store,
            "Fixture workflow",
            &[json!({"action": "note", "detail": "첫 단계"})],
            &json!({"origin": "fixture"}),
            Some("owner@lattice.test"),
            None,
        );
        assert!(planned.id().starts_with("workflow-"));
        assert_eq!(planned.name(), "Fixture workflow");
        assert_eq!(planned.workspace_id(), Some("personal"));
        assert_eq!(planned.steps().as_array().unwrap().len(), 1);

        let workflow =
            commit_workflow(&store, planned, Some(Ok(json!({"node_id": "node-3"})))).unwrap();
        assert_eq!(workflow["graph_node_id"], json!("node-3"));
        assert_eq!(workflow["events"][0]["type"], json!("created"));
        assert_eq!(workflow["metadata"], json!({"origin": "fixture"}));
        assert_eq!(
            list_workflows(&store, "", None)["workflows"]
                .as_array()
                .unwrap()
                .len(),
            1
        );
    }

    #[test]
    fn an_unnamed_workflow_gets_the_placeholder_name() {
        let (_dir, store) = store();
        let planned = plan_workflow(&store, "", &[], &json!(null), None, None);
        assert_eq!(planned.name(), "Untitled workflow");
        let workflow = commit_workflow(&store, planned, Some(Err("no graph".into()))).unwrap();
        assert_eq!(workflow["metadata"], json!({}));
        assert_eq!(workflow["graph_error"], json!("no graph"));
    }

    #[test]
    fn the_workflow_query_searches_the_name_and_the_serialized_steps() {
        let (_dir, store) = store();
        for (name, step) in [("Alpha", "첫 단계"), ("Beta", "second")] {
            let planned = plan_workflow(
                &store,
                name,
                &[json!({"detail": step})],
                &json!({}),
                None,
                None,
            );
            commit_workflow(&store, planned, None).unwrap();
        }
        let by_name = list_workflows(&store, "alpha", None);
        assert_eq!(by_name["workflows"].as_array().unwrap().len(), 1);
        let by_step = list_workflows(&store, "SECOND", None);
        assert_eq!(by_step["workflows"].as_array().unwrap().len(), 1);
        assert_eq!(by_step["workflows"][0]["name"], json!("Beta"));
        assert!(list_workflows(&store, "없음", None)["workflows"]
            .as_array()
            .unwrap()
            .is_empty());
        // Newest first.
        assert_eq!(
            list_workflows(&store, "", None)["workflows"][0]["name"],
            json!("Beta")
        );
    }

    #[test]
    fn an_event_appends_to_the_workflow_and_an_unknown_id_is_not_found() {
        let (_dir, store) = store();
        let planned = plan_workflow(&store, "W", &[], &json!({}), None, None);
        let workflow = commit_workflow(&store, planned, None).unwrap();
        let id = workflow["id"].as_str().unwrap().to_string();

        let updated = record_workflow_event(&store, &id, "started", &json!({"by": "me"})).unwrap();
        let events = updated["events"].as_array().unwrap();
        assert_eq!(events.len(), 2);
        assert_eq!(events[1]["type"], json!("started"));
        assert_eq!(events[1]["payload"], json!({"by": "me"}));

        assert_eq!(
            record_workflow_event(&store, "workflow-missing", "x", &json!({})).unwrap_err(),
            StoreError::NotFound("workflow-missing".into())
        );
        // A non-object payload normalises to `{}`.
        let updated = record_workflow_event(&store, &id, "y", &json!("nope")).unwrap();
        assert_eq!(updated["events"][2]["payload"], json!({}));
    }

    #[test]
    fn the_vscode_helpers_build_the_recorded_shapes() {
        assert_eq!(
            vscode_steps("explain", Some("src/lib.rs"), Some("rust"), 12),
            vec![
                json!({"action": "explain", "file_path": "src/lib.rs", "language": "rust"}),
                json!({"action": "send_to_lattice", "chars": 12}),
            ]
        );
        assert_eq!(
            vscode_metadata(None, None, &"가".repeat(600))["content_preview"]
                .as_str()
                .unwrap()
                .chars()
                .count(),
            500
        );
        assert_eq!(vscode_metadata(None, None, "")["file_path"], Value::Null);
    }
}
