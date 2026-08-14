//! Replay `platform_misc.json` permission_mode records.

#![allow(dead_code, unused_imports, unused_variables)]
#![allow(clippy::all)]
#[path = "models_catalog_support.rs"]
mod support;

use std::collections::HashMap;

use lattice_platform::permission_mode::{router, PermissionModeState};

#[tokio::test(flavor = "multi_thread")]
async fn permission_mode_fixtures_replay() {
    let root = support::fixture("platform_misc.json");
    let records = support::family_records(&root, "permission_mode.py");
    assert_eq!(records.len(), 13, "permission_mode fixture count");

    let install = support::Install::start();
    let state = PermissionModeState::new(install.auth.clone(), install.data_dir());
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
