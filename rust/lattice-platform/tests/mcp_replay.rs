#[allow(dead_code)]
mod mcp_harness;

use lattice_platform::toolsurface::agent_registry;
use lattice_platform::toolsurface::agents;
use lattice_platform::toolsurface::marketplace;
use lattice_platform::toolsurface::mcp;
use lattice_platform::toolsurface::plugins;
use lattice_platform::toolsurface::tools;
use serde_json::{json, Value};

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

#[tokio::test]
async fn agents_and_loop_fixtures_replay() {
    let doc = mcp_harness::load_http("mcp_ecosystem.json");
    let cases: Vec<Value> = doc["fixtures"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|c| c["family"] == "agents.py" || c["family"] == "chat_agent_http.py")
        .filter(|c| c["name"] != "agents_page")
        .cloned()
        .collect();
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
                case["family"], case["name"], path, expected, answer.status, answer.body
            ));
            continue;
        }
        if case.get("sse_frames").is_some() {
            if !answer.body.contains("[DONE]") {
                failed.push(format!("{} missing SSE [DONE]", case["name"]));
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

#[tokio::test]
async fn marketplace_fixtures_replay() {
    replay("marketplace.py").await;
}

async fn replay(family: &str) {
    let doc = mcp_harness::load_http("mcp_ecosystem.json");
    let cases: Vec<Value> = doc["fixtures"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|c| c["family"] == family)
        .cloned()
        .collect();
    let install = Install::start().await;
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

fn mask_machine_facts(name: &str, expected: &mut Value, actual: &mut Value) {
    match name {
        "obsidian_status" => {
            if expected.get("ocr_engine").is_some() {
                expected["ocr_engine"] = json!("@any");
            }
            sort_by_render(expected.pointer_mut("/folders"));
            sort_by_render(actual.pointer_mut("/folders"));
        }
        "search_files" => {
            sort_matches(expected.pointer_mut("/result/matches"));
            sort_matches(actual.pointer_mut("/result/matches"));
        }
        "git_diff" => {
            truncate_git_usage(expected.pointer_mut("/result/stderr"));
            truncate_git_usage(actual.pointer_mut("/result/stderr"));
        }
        "tool_permissions" => {
            const V13_4_TOOLS: [&str; 3] = ["computer_ui_inspect", "web_fetch", "web_search"];
            if let Some(Value::Array(items)) = actual.get_mut("permissions") {
                for added in V13_4_TOOLS {
                    assert!(
                        items
                            .iter()
                            .any(|item| item.get("tool").and_then(Value::as_str) == Some(added)),
                        "expected {added} in /tools/permissions"
                    );
                }
                items.retain(|item| {
                    !item
                        .get("tool")
                        .and_then(Value::as_str)
                        .is_some_and(|t| V13_4_TOOLS.contains(&t))
                });
            }
        }
        "tool_registry_diagnostics" => {
            if let Some(diag) = actual.get_mut("diagnostics") {
                assert_eq!(diag["registered_tools"], json!(50));
                assert_eq!(diag["governed_tools"], json!(50));
                assert_eq!(diag["described_tools"], json!(55));
                diag["registered_tools"] = json!(47);
                diag["governed_tools"] = json!(47);
                diag["described_tools"] = json!(52);
            }
        }
        "tool_registry" => {
            const V13_4_TOOLS: [&str; 3] = ["computer_ui_inspect", "web_fetch", "web_search"];
            if let Some(diag) = actual.get_mut("diagnostics") {
                assert_eq!(diag["registered_tools"], json!(50));
                assert_eq!(diag["governed_tools"], json!(50));
                assert_eq!(diag["described_tools"], json!(55));
                diag["registered_tools"] = json!(47);
                diag["governed_tools"] = json!(47);
                diag["described_tools"] = json!(52);
            }
            if let Some(Value::String(brief)) = actual.get_mut("catalog_brief") {
                *brief = brief
                    .replace("\nWEB         : web_search  web_fetch", "")
                    .replace("computer_ui_inspect  ", "");
            }
            if let Some(Value::Array(tools)) = actual.get_mut("tools") {
                for added in V13_4_TOOLS {
                    assert!(
                        tools
                            .iter()
                            .any(|item| item.get("name").and_then(Value::as_str) == Some(added)),
                        "expected {added} in /tools/registry"
                    );
                }
                tools.retain(|item| {
                    !item
                        .get("name")
                        .and_then(Value::as_str)
                        .is_some_and(|t| V13_4_TOOLS.contains(&t))
                });
            }
        }
        _ => {}
    }
}

fn sort_by_render(target: Option<&mut Value>) {
    if let Some(Value::Array(items)) = target {
        items.sort_by_key(|item| item.to_string());
    }
}

fn sort_matches(target: Option<&mut Value>) {
    if let Some(Value::Array(items)) = target {
        items.sort_by_key(|item| {
            (
                item.get("path")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .to_string(),
                item.get("line").and_then(Value::as_i64).unwrap_or_default(),
            )
        });
    }
}

const GIT_USAGE_MARKER: &str = "usage: git diff --no-index";

fn truncate_git_usage(target: Option<&mut Value>) {
    if let Some(Value::String(text)) = target {
        if let Some(index) = text.find(GIT_USAGE_MARKER) {
            text.truncate(index + GIT_USAGE_MARKER.len());
        }
    }
}

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
        let mut actual: Value =
            serde_json::from_str(&answer.body).unwrap_or(Value::String(answer.body.clone()));
        let mut expected = case["response_body"].clone();
        mask_machine_facts(
            case["name"].as_str().unwrap_or_default(),
            &mut expected,
            &mut actual,
        );
        if !mcp_harness::match_value(&expected, &actual, &symbols) {
            failed.push(format!(
                "{} body mismatch\nexp {}\nact {}",
                case["name"], expected, actual
            ));
        }
    }
    assert!(failed.is_empty(), "{}", failed.join("\n\n"));
}
