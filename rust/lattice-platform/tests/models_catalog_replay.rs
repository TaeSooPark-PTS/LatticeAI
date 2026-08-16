//! The R10 families: the models catalog, the permission-mode dial and the
//! workflow designer — fixture replay for each, plus the OpenAPI contract the
//! three compose and the worker-KEEP tripwire that guards it.
//!
//! Four test binaries collapsed into one, all recompiling the same 12kB
//! support module. Every test function is the one it was; only the three
//! identically-named `router` factories are aliased so they can share a scope.

// The shared harness is written for every suite that includes it, so a
// helper this one does not call still reads as dead in this binary.
#[allow(dead_code)]
#[path = "models_catalog_support/mod.rs"]
mod support;

use std::collections::HashMap;
use std::sync::Arc;

use lattice_platform::models_catalog::{
    self, keep_worker_paths, router as catalog_router, ModelsCatalogState, MOUNTED as CATALOG,
};
use lattice_platform::permission_mode::{
    router as permission_mode_router, PermissionModeState, MOUNTED as PERMISSION,
};
use lattice_platform::workflow_designer::{
    router as workflow_router, LocalGraphSink, WorkflowDesignerState, MOUNTED as WORKFLOW,
};
use serde_json::json;

// ── models_catalog.py ──

#[tokio::test(flavor = "multi_thread")]
async fn models_catalog_fixtures_replay() {
    let root = support::fixture("tools_misc.json");
    let records = support::family_records(&root, "models.py");
    assert_eq!(records.len(), 11, "models.py fixture count");

    let install = support::Install::start();
    let state = ModelsCatalogState::new(install.auth.clone(), install.data_dir()).with_runtime(
        "local",
        "127.0.0.1",
        4825,
    );
    let (origin, handle) = support::serve(catalog_router(state)).await;
    let symbols = HashMap::new();

    for case in records {
        let name = format!(
            "{}/{}",
            case["name"].as_str().unwrap_or("?"),
            case["branch"].as_str().unwrap_or("?")
        );
        let path = support::substitute_path(case["path"].as_str().unwrap(), &symbols);
        let answer = support::issue(
            &origin,
            case["method"].as_str().unwrap(),
            &path,
            &case["query"],
            &case["request_headers"],
            &case["request_body"],
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
        support::assert_matches(&case["response_body"], &actual, &name);
    }
    handle.abort();
}

#[tokio::test(flavor = "multi_thread")]
async fn leftover_mode_and_engines_require_auth() {
    let install = support::Install::start();
    let state = ModelsCatalogState::new(install.auth.clone(), install.data_dir());
    let (origin, handle) = support::serve(catalog_router(state)).await;

    for path in ["/mode", "/runtime_features", "/engines"] {
        let answer = support::issue(
            &origin,
            "GET",
            path,
            &serde_json::json!({}),
            &serde_json::json!({"cookie": "absent", "origin": "http://127.0.0.1:4825"}),
            &serde_json::Value::Null,
            &install,
        )
        .await;
        assert_eq!(answer.status, 401, "{path} anonymous");
        let ok = support::issue(
            &origin,
            "GET",
            path,
            &serde_json::json!({}),
            &serde_json::json!({"cookie": "session:owner", "origin": "http://127.0.0.1:4825"}),
            &serde_json::Value::Null,
            &install,
        )
        .await;
        assert_eq!(ok.status, 200, "{path} owner: {}", ok.body);
    }

    handle.abort();
}

// ── permission_mode.py ──

#[tokio::test(flavor = "multi_thread")]
async fn permission_mode_fixtures_replay() {
    let root = support::fixture("platform_misc.json");
    let records = support::family_records(&root, "permission_mode.py");
    assert_eq!(records.len(), 13, "permission_mode fixture count");

    let install = support::Install::start();
    let state = PermissionModeState::new(install.auth.clone(), install.data_dir());
    let (origin, handle) = support::serve(permission_mode_router(state)).await;
    let symbols = HashMap::new();

    for case in records {
        let name = format!(
            "{}/{}",
            case["name"].as_str().unwrap_or("?"),
            case["branch"].as_str().unwrap_or("?")
        );
        let path = support::substitute_path(case["path"].as_str().unwrap(), &symbols);
        let answer = support::issue(
            &origin,
            case["method"].as_str().unwrap(),
            &path,
            &case["query"],
            &case["request_headers"],
            &case["request_body"],
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
        support::assert_matches(&case["response_body"], &actual, &name);
    }
    handle.abort();
}

// ── workflow_designer.py ──

#[tokio::test(flavor = "multi_thread")]
async fn workflow_designer_fixtures_replay() {
    let root = support::fixture("tools_misc.json");
    let records = support::family_records(&root, "workflow_designer.py");
    assert_eq!(records.len(), 65, "workflow fixture count");

    let install = support::Install::start();
    let state = WorkflowDesignerState::new(install.auth.clone(), install.data_dir())
        .with_graph(Arc::new(LocalGraphSink));
    let (origin, handle) = support::serve(workflow_router(state)).await;

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

// ── the composed OpenAPI contract ──

#[test]
fn mounted_routes_match_the_committed_contract() {
    let spec = support::openapi_fragment("models_misc.json");
    let mut expected: Vec<String> = spec["operation_order"]
        .as_array()
        .expect("operation_order")
        .iter()
        .map(|value| value.as_str().unwrap().to_string())
        .collect();
    let mut actual: Vec<String> = PERMISSION
        .iter()
        .chain(CATALOG.iter())
        .chain(WORKFLOW.iter())
        .map(|(method, path)| format!("{method} {}", support::to_openapi(path)))
        .collect();
    expected.sort();
    actual.sort();
    assert_eq!(
        actual, expected,
        "router and rust/fixtures/openapi/models_misc.json disagree"
    );

    let greedy = spec["greedy_path_params"].as_object().expect("greedy");
    for (key, param) in greedy {
        let path = key.split_once(' ').unwrap().1;
        let param = param.as_str().unwrap();
        assert!(
            PERMISSION
                .iter()
                .chain(CATALOG.iter())
                .chain(WORKFLOW.iter())
                .any(|(_, mounted)| {
                    support::to_openapi(mounted) == path && mounted.contains(&format!("*{param}"))
                }),
            "{key} matches slashes in Python; mount it as /*{param}"
        );
    }
}

#[test]
fn worker_keep_and_worker_tools_routes_are_not_mounted() {
    let worker_tools = support::fixture("worker_tools.json");
    let mut forbidden = std::collections::BTreeSet::new();
    for row in worker_tools["fixtures"].as_array().expect("fixtures") {
        forbidden.insert(format!(
            "{} {}",
            row["method"].as_str().unwrap(),
            row["path"].as_str().unwrap()
        ));
    }
    for (method, path) in keep_worker_paths() {
        forbidden.insert(format!("{method} {path}"));
    }
    forbidden.insert("GET /workflows".into());

    let mounted: Vec<String> = PERMISSION
        .iter()
        .chain(CATALOG.iter())
        .chain(WORKFLOW.iter())
        .map(|(method, path)| format!("{method} {path}"))
        .collect();
    for key in &forbidden {
        assert!(
            !mounted.iter().any(|row| row == key),
            "R10 must not claim worker/static route {key}"
        );
    }
    assert!(
        !mounted.iter().any(|row| row == "GET /models"),
        "GET /models is KEEP_WORKER"
    );
    assert!(
        !mounted.iter().any(|row| row.contains("prepare-model")),
        "prepare-model is KEEP_WORKER"
    );
    let _ = models_catalog::CLOUD_VERIFY_TTL_SECONDS;
}
