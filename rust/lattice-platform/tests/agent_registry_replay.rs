#![allow(dead_code, unused_imports, unused_variables)]
#![allow(clippy::all)]
mod mcp_harness;

use serde_json::Value;

#[tokio::test]
async fn agent_registry_fixtures_replay() {
    let doc = mcp_harness::load_http("mcp_ecosystem.json");
    let cases: Vec<Value> = doc["fixtures"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|c| c["family"] == "agent_registry.py")
        .cloned()
        .collect();
    let install = mcp_harness::Install::start().await;
    let mut symbols = std::collections::HashMap::new();
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
        let path = mcp_harness::substitute_path(case["path"].as_str().unwrap(), &symbols);
        let qs = mcp_harness::query_string(&case["query"]);
        let session = mcp_harness::cookie_session(&case["request_headers"]);
        let session = session.as_deref().filter(|s| *s != "absent");
        let body = if case["request_body"].is_null() {
            None
        } else {
            Some(case["request_body"].clone())
        };
        let answer = install
            .issue(method, &format!("{path}{qs}"), session, body)
            .await;
        let expected = case["status"].as_u64().unwrap() as u16;
        if answer.status != expected {
            failed.push(format!(
                "{} {} expected {} got {} {}",
                case["name"], path, expected, answer.status, answer.body
            ));
            continue;
        }
        if case["response_body"].is_null() {
            continue;
        }
        let actual: Value =
            serde_json::from_str(&answer.body).unwrap_or(Value::String(answer.body.clone()));
        if !mcp_harness::match_value(&case["response_body"], &actual, &symbols) {
            failed.push(format!(
                "{} body mismatch\nexp {}\nact {}",
                case["name"], case["response_body"], actual
            ));
        }
    }
    assert!(failed.is_empty(), "{}", failed.join("\n\n"));
}
