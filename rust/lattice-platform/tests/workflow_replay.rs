//! Replay `tools_misc.json` workflow_designer records.

#![allow(dead_code, unused_imports, unused_variables)]
#![allow(clippy::all)]
#[path = "models_catalog_support.rs"]
mod support;

use std::collections::HashMap;
use std::sync::Arc;

use lattice_platform::workflow_designer::{router, LocalGraphSink, WorkflowDesignerState};
use serde_json::json;

#[tokio::test(flavor = "multi_thread")]
async fn workflow_designer_fixtures_replay() {
    let root = support::fixture("tools_misc.json");
    let records = support::family_records(&root, "workflow_designer.py");
    assert_eq!(records.len(), 65, "workflow fixture count");

    let install = support::Install::start();
    let state = WorkflowDesignerState::new(install.auth.clone(), install.data_dir())
        .with_graph(Arc::new(LocalGraphSink));
    let (origin, handle) = support::serve(router(state)).await;

    let mut symbols = HashMap::new();
    seed_workflows(&origin, &install, &mut symbols).await;

    for case in records {
        let name = format!(
            "{}/{}",
            case["name"].as_str().unwrap_or("?"),
            case["branch"].as_str().unwrap_or("?")
        );
        let path = case["path"].as_str().unwrap();
        // STATIC page shell — WP-I4 owns GET /workflows.
        if path == "/workflows" {
            continue;
        }
        let path = support::substitute_path(path, &symbols);
        let request_body = support::substitute_value(&case["request_body"], &symbols);
        let expected = support::substitute_value(&case["response_body"], &symbols);
        let answer = support::issue(
            &origin,
            case["method"].as_str().unwrap(),
            &path,
            &case["query"],
            &case["request_headers"],
            &request_body,
            &install,
        )
        .await;
        assert_eq!(
            answer.status,
            case["status"].as_u64().unwrap() as u16,
            "{name}: status (body {})",
            answer.body
        );
        if let Some(expected_ct) = case["response_headers"]
            .get("content-type")
            .and_then(|v| v.as_str())
        {
            assert_eq!(
                answer.content_type.as_deref(),
                Some(expected_ct),
                "{name}: content-type"
            );
        }
        let actual = support::parse_body(&answer.body);
        if case["name"].as_str() == Some("run_definition")
            && case["branch"].as_str() == Some("happy")
        {
            if let Some(run_id) = actual
                .get("run")
                .and_then(|run| run.get("id"))
                .and_then(|id| id.as_str())
            {
                symbols.insert("$workflow_run_id".into(), run_id.to_string());
            }
        }
        let expected = support::substitute_value(&expected, &symbols);
        support::assert_matches(&expected, &actual, &name);
    }
    handle.abort();
}

async fn seed_workflows(
    origin: &str,
    install: &support::Install,
    symbols: &mut HashMap<String, String>,
) {
    let owner = json!({
        "cookie": "session:owner",
        "origin": "http://127.0.0.1:4825",
        "content-type": "application/json"
    });

    let created = support::issue(
        origin,
        "POST",
        "/workflows/api/definitions",
        &json!({}),
        &owner,
        &json!({
            "name": "Fixture workflow",
            "nodes": [
                {"id": "trigger", "type": "trigger", "name": "Manual start", "config": {"trigger": "manual"}, "next": "output"},
                {"id": "output", "type": "output", "name": "Output", "config": {}, "next": null}
            ],
            "metadata": {"origin": "fixture"}
        }),
        install,
    )
    .await;
    assert_eq!(
        created.status, 200,
        "seed fixture workflow: {}",
        created.body
    );
    let body = support::parse_body(&created.body);
    let workflow_id = body["workflow"]["id"].as_str().expect("workflow id");
    symbols.insert("$workflow_id".into(), workflow_id.to_string());

    let recipe = support::issue(
        origin,
        "POST",
        "/workflows/api/automation/recipes/daily-memory-digest",
        &json!({}),
        &owner,
        &json!({"enabled": false}),
        install,
    )
    .await;
    assert_eq!(recipe.status, 200, "seed recipe: {}", recipe.body);
    let recipe_body = support::parse_body(&recipe.body);
    let automation_id = recipe_body["workflow"]["id"]
        .as_str()
        .expect("automation id");
    symbols.insert("$automation_workflow_id".into(), automation_id.to_string());

    // The shared ecosystem capture stamped a dry-run last_execution onto the
    // recipe (an `edited` event). Reproduce that so list/install already-
    // installed match the recorded document.
    let _ = support::issue(
        origin,
        "PATCH",
        &format!("/workflows/api/definitions/{automation_id}"),
        &json!({}),
        &owner,
        &json!({
            "metadata": {
                "last_execution": {
                    "mode": "dry_run",
                    "status": "ok",
                    "summary": "1 step(s) would run locally and produce a reviewable draft (memory digest, decision summary, next-action suggestions); no external actions, nothing is written until you approve.",
                    "run_id": null,
                    "finished_at": "2026-08-01T09:00:00"
                }
            }
        }),
        install,
    )
    .await;

    let installed = support::issue(
        origin,
        "POST",
        "/workflows/api/definitions",
        &json!({}),
        &owner,
        &json!({
            "name": "Installed from fixture",
            "nodes": [
                {"id": "trigger", "type": "trigger", "name": "Manual", "config": {"trigger": "manual"}, "next": null}
            ],
            "metadata": {"template_id": "workflow-agent-plugin-review"}
        }),
        install,
    )
    .await;
    assert_eq!(
        installed.status, 200,
        "seed installed template: {}",
        installed.body
    );
}
