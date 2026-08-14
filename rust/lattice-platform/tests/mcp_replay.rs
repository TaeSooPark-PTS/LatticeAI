//! Fixture replay for mcp.py + OpenAPI contract for the mcp_market family.

#![allow(dead_code, unused_imports, unused_variables)]
#![allow(clippy::all)]
mod mcp_harness;

use lattice_platform::agent_registry;
use lattice_platform::agents;
use lattice_platform::marketplace;
use lattice_platform::mcp;
use lattice_platform::plugins;
use lattice_platform::tools;
use serde_json::Value;

use mcp_harness::{
    cookie_session, load_http, load_openapi, match_value, query_string, substitute_path,
    to_openapi, Install,
};

#[tokio::test]
async fn mcp_fixtures_replay() {
    replay_family("mcp.py").await;
}

async fn replay_family(family: &str) {
    let doc = load_http("mcp_ecosystem.json");
    let cases: Vec<Value> = doc["fixtures"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|c| c["family"] == family)
        .cloned()
        .collect();
    assert!(!cases.is_empty(), "{family} must have fixtures");
    let install = Install::start().await;
    let mut symbols = std::collections::HashMap::new();
    // After bootstrap the custom MCP and agent exist.
    if let Ok(text) = std::fs::read_to_string(install.data_dir.join("custom_mcps.json")) {
        if let Ok(Value::Array(items)) = serde_json::from_str::<Value>(&text) {
            if let Some(id) = items
                .first()
                .and_then(|i| i.get("id"))
                .and_then(Value::as_str)
            {
                symbols.insert("$custom_mcp_id".into(), id.to_string());
            }
        }
    }
    if let Ok(text) = std::fs::read_to_string(install.data_dir.join("agent_registry.json")) {
        if let Ok(doc) = serde_json::from_str::<Value>(&text) {
            if let Some(id) = doc
                .get("custom")
                .and_then(Value::as_array)
                .and_then(|a| a.first())
                .and_then(|i| i.get("id"))
                .and_then(Value::as_str)
            {
                symbols.insert("$agent_id".into(), id.to_string());
            }
        }
    }
    let mut failed = Vec::new();
    for case in &cases {
        let method = case["method"].as_str().unwrap();
        let path = substitute_path(case["path"].as_str().unwrap(), &symbols);
        let qs = query_string(&case["query"]);
        let session = cookie_session(&case["request_headers"]);
        let session = session.as_deref().filter(|s| *s != "absent");
        let body = if case["request_body"].is_null() {
            None
        } else {
            Some(case["request_body"].clone())
        };
        let answer = install
            .issue(method, &format!("{path}{qs}"), session, body)
            .await;
        let expected_status = case["status"].as_u64().unwrap() as u16;
        if answer.status != expected_status {
            failed.push(format!(
                "{} {} {} expected {} got {} body={}",
                case["name"], method, path, expected_status, answer.status, answer.body
            ));
            continue;
        }
        if let Some(exp) = case.get("sse_frames") {
            if !answer.body.contains("[DONE]") && !exp.is_null() {
                failed.push(format!(
                    "{} missing SSE [DONE]: {}",
                    case["name"], answer.body
                ));
            }
            continue;
        }
        if case["response_body"].is_null() {
            continue;
        }
        let actual: Value =
            serde_json::from_str(&answer.body).unwrap_or(Value::String(answer.body.clone()));
        if !match_value(&case["response_body"], &actual, &symbols) {
            failed.push(format!(
                "{} body mismatch\nexpected {}\nactual {}",
                case["name"], case["response_body"], actual
            ));
        }
    }
    assert!(
        failed.is_empty(),
        "replay failures:\n{}",
        failed.join("\n\n")
    );
}

#[test]
fn mcp_market_mounted_routes_match_the_committed_contract() {
    let spec = load_openapi("mcp_market.json");
    let mut expected: Vec<String> = spec["operation_order"]
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_str().unwrap().to_string())
        .collect();
    let mut actual: Vec<String> = mcp::MOUNTED
        .iter()
        .chain(marketplace::MOUNTED)
        .chain(plugins::MOUNTED)
        .chain(agent_registry::MOUNTED)
        .chain(agents::MOUNTED)
        .chain(tools::MOUNTED)
        // W3b: create_* are native product routes; their spec stays in
        // worker_keep.json so fragment byte-composition is unchanged.
        .filter(|(_, p)| !p.starts_with("/tools/create_"))
        .map(|(m, p)| format!("{m} {}", to_openapi(p)))
        .collect();
    expected.sort();
    actual.sort();
    assert_eq!(
        actual, expected,
        "router and rust/fixtures/openapi/mcp_market.json disagree"
    );
    for (key, param) in spec["greedy_path_params"].as_object().unwrap() {
        let path = key.split_once(' ').unwrap().1;
        let mounted = mcp::MOUNTED
            .iter()
            .chain(agent_registry::MOUNTED)
            .any(|(_, p)| {
                to_openapi(p) == path && p.contains(&format!("*{}", param.as_str().unwrap()))
            });
        assert!(
            mounted,
            "{key} matches slashes in Python; mount it as /*{param}"
        );
    }
}
