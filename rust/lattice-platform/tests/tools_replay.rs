#![allow(dead_code, unused_imports, unused_variables)]
#![allow(clippy::all)]
mod mcp_harness;

use serde_json::Value;

#[tokio::test]
async fn tools_fixtures_replay() {
    let doc = mcp_harness::load_http("tools_misc.json");
    let cases: Vec<Value> = doc["fixtures"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|c| c["family"] == "tools.py")
        .cloned()
        .collect();
    assert!(!cases.is_empty());
    let install = mcp_harness::Install::start().await;
    let symbols = std::collections::HashMap::new();
    let mut failed = Vec::new();
    for case in &cases {
        let method = case["method"].as_str().unwrap();
        let path = case["path"].as_str().unwrap();
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
                "{} {} {} expected {} got {} {}",
                case["name"], method, path, expected, answer.status, answer.body
            ));
            continue;
        }
        if case["response_body"]
            .as_object()
            .map(|o| o.contains_key("@binary"))
            .unwrap_or(false)
        {
            let magic = case["response_body"]["@binary"]["leading_magic"]
                .as_str()
                .unwrap_or("");
            if !magic.is_empty() {
                let prefix: String = answer
                    .bytes
                    .iter()
                    .take(magic.len() / 2)
                    .map(|b| format!("{b:02x}"))
                    .collect();
                if prefix != magic.to_lowercase() {
                    failed.push(format!("{} magic {prefix} != {magic}", case["name"]));
                }
            }
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
