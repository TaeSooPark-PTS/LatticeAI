#![allow(dead_code, unused_imports, unused_variables)]
#![allow(clippy::all)]
mod mcp_harness;

use serde_json::Value;

#[tokio::test]
async fn plugins_fixtures_replay() {
    let doc = mcp_harness::load_http("mcp_ecosystem.json");
    let cases: Vec<Value> = doc["fixtures"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|c| c["family"] == "plugins.py")
        .cloned()
        .collect();
    let install = mcp_harness::Install::start().await;
    let symbols = std::collections::HashMap::new();
    let mut failed = Vec::new();
    for case in &cases {
        let method = case["method"].as_str().unwrap();
        let path = case["path"].as_str().unwrap();
        let session = mcp_harness::cookie_session(&case["request_headers"]);
        let session = session.as_deref().filter(|s| *s != "absent");
        let body = if case["request_body"].is_null() {
            None
        } else {
            Some(case["request_body"].clone())
        };
        let answer = install.issue(method, path, session, body).await;
        let expected = case["status"].as_u64().unwrap() as u16;
        if answer.status != expected {
            failed.push(format!(
                "{} {} expected {} got {} {}",
                case["name"], path, expected, answer.status, answer.body
            ));
            continue;
        }
        if let Some(loc) = case["response_headers"]
            .get("location")
            .and_then(Value::as_str)
        {
            if answer.headers.get("location").map(String::as_str) != Some(loc) {
                failed.push(format!(
                    "{} location {:?} != {loc}",
                    case["name"],
                    answer.headers.get("location")
                ));
            }
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
