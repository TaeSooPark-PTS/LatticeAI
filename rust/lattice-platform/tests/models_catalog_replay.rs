//! Replay `tools_misc.json` models.py records + leftover route existence.

#![allow(dead_code, unused_imports, unused_variables)]
#![allow(clippy::all)]
#[path = "models_catalog_support.rs"]
mod support;

use std::collections::HashMap;

use lattice_platform::models_catalog::{router, ModelsCatalogState};

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
    let (origin, handle) = support::serve(router(state)).await;
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
    let (origin, handle) = support::serve(router(state)).await;

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

    // `POST /engines/pull-model` used to be replayed here for its 403
    // consent refusal. §4a of the v11.6.0 gateway integration moved the whole
    // route back to the Python worker (the pull is huggingface_hub / ollama in
    // the worker's interpreter), so this router does not answer it at all — a
    // request for it reaches the gateway's proxy allowlist instead.
    let unmounted = support::issue(
        &origin,
        "POST",
        "/engines/pull-model",
        &serde_json::json!({}),
        &serde_json::json!({
            "cookie": "session:owner",
            "origin": "http://127.0.0.1:4825",
            "content-type": "application/json"
        }),
        &serde_json::json!({"model": "mlx-community/test", "allow_download": false}),
        &install,
    )
    .await;
    assert_eq!(
        unmounted.status, 404,
        "the catalog router must not claim the model pull: {}",
        unmounted.body
    );
    handle.abort();
}
