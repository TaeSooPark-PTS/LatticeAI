//! The MCP-ecosystem families, replayed against the Python oracle:
//! `mcp.py`, `agent_registry.py`, `agents.py` / `chat_agent_http.py`,
//! `marketplace.py`, `plugins.py` and `tools.py` — plus the OpenAPI contract
//! the six compose and the write-side guarantee `POST /tools/write_file`
//! carries.
//!
//! Seven test binaries collapsed into one; each recompiled the same 14kB
//! harness to run one replay loop. Every test function is the one it was.

// The shared harness is written for every suite that includes it, so a
// helper this one does not call still reads as dead in this binary.
#[allow(dead_code)]
mod mcp_harness;

use lattice_platform::agent_registry;
use lattice_platform::agents;
use lattice_platform::marketplace;
use lattice_platform::mcp;
use lattice_platform::plugins;
use lattice_platform::tools;
use serde_json::{json, Value};

use mcp_harness::{
    cookie_session, load_http, load_openapi, match_value, query_string, substitute_path,
    to_openapi, Install,
};

// ── mcp.py ──

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

// ── agent_registry.py ──

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

// ── agents.py / chat_agent_http.py ──

#[tokio::test]
async fn agents_and_loop_fixtures_replay() {
    let doc = mcp_harness::load_http("mcp_ecosystem.json");
    let cases: Vec<Value> = doc["fixtures"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|c| c["family"] == "agents.py" || c["family"] == "chat_agent_http.py")
        // `GET /agents` is a page shell, not an agents route: it is filed under
        // `ui_redirects.json` in the committed contract and, since the v11.6.0
        // gateway integration (§1), served by `ui_redirects` — which preserves
        // the query string the way `app_redirect(request)` does and which this
        // router never did. `tests/ui_redirects_parity.rs` replays it there.
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

// ── marketplace.py ──

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

// ── plugins.py ──

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

// ── tools.py ──

/// Everything in `tools_misc.json` that records the *recording machine* rather
/// than the handler.
///
/// Every mask below is applied to both sides, and each one is here because the
/// Python oracle captured a value that no other computer can reproduce — never
/// because the two implementations disagree. Anything not named here is still
/// compared literally.
fn mask_machine_facts(name: &str, expected: &mut Value, actual: &mut Value) {
    match name {
        "obsidian_status" => {
            // `ocr_engine` is a `which(tesseract)` probe: an absolute Homebrew
            // path on the laptop that had it, `null` on a machine that does
            // not. `@any` is the harness's existing token for "present in
            // whatever form this host has it".
            if expected.get("ocr_engine").is_some() {
                expected["ocr_engine"] = json!("@any");
            }
            // The vault folders come back in directory order. The oracle did
            // not sort them — `30_Projects` precedes `10_Wiki` in the
            // recording — so the contract is the set, which APFS and overlayfs
            // enumerate differently.
            sort_by_render(expected.pointer_mut("/folders"));
            sort_by_render(actual.pointer_mut("/folders"));
        }
        "search_files" => {
            // Same story one level down: the recording lists `fixture-note.md`
            // before `capture-write.md`, so the walk order was never sorted and
            // the honest comparison is the multiset of matches.
            sort_matches(expected.pointer_mut("/result/matches"));
            sort_matches(actual.pointer_mut("/result/matches"));
        }
        "git_diff" => {
            // The `git diff --no-index` option table is written by whichever
            // git the machine ships, and it grows between releases. Keep the
            // part that is behaviour — the "not a git repository" warning and
            // the usage line it precedes — and cut the table off both sides.
            truncate_git_usage(expected.pointer_mut("/result/stderr"));
            truncate_git_usage(actual.pointer_mut("/result/stderr"));
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

// ── POST /tools/write_file: the write-side guarantee (v11.7.0) ──
//
// The hole this closes is named in the 11.6.0 release notes §5.3: Python
// applied `sanitize_write_content` in the agent loop only, so a payload posted
// to this endpoint — the VS Code extension's writes, an MCP tool call, a
// script — was persisted exactly as the model produced it, fences and all.
// The recorded HTTP fixtures (`fixtures/http/tools_misc.json`) all carry clean
// content and are unaffected, which is the point: sanitize is an identity
// transform on content that validates.

async fn write(install: &Install, path: &str, content: &str) -> (u16, Value, String) {
    let answer = install
        .issue(
            "POST",
            "/tools/write_file",
            Some("session:owner"),
            Some(json!({"path": path, "content": content})),
        )
        .await;
    let body: Value = serde_json::from_str(&answer.body).unwrap_or(Value::Null);
    let on_disk = std::fs::read_to_string(install.agent_root.join(path)).unwrap_or_default();
    (answer.status, body, on_disk)
}

#[tokio::test]
async fn a_fenced_payload_posted_to_the_endpoint_is_cleaned_before_the_disk() {
    let install = Install::start().await;
    let dirty = "Sure! Here is the page:\n```html\n\
<!DOCTYPE html><html><body>ok</body></html>\n```\nLet me know!";
    let (status, body, on_disk) = write(&install, "posted.html", dirty).await;
    assert_eq!(status, 200, "{body}");
    assert_eq!(on_disk, "<!DOCTYPE html><html><body>ok</body></html>");
    assert_eq!(
        body["result"]["bytes"],
        json!(on_disk.len() as u64),
        "`bytes` is what landed, not what was posted"
    );
    assert_eq!(body["result"]["path"], json!("posted.html"));
}

#[tokio::test]
async fn a_truncated_document_posted_to_the_endpoint_is_closed() {
    let install = Install::start().await;
    let truncated = "<!DOCTYPE html><html><head><title>t</title></head><body><p>hi</p>";
    let (status, body, on_disk) = write(&install, "truncated.html", truncated).await;
    assert_eq!(status, 200, "{body}");
    assert!(on_disk.ends_with("</body>\n</html>"), "{on_disk}");
}

#[tokio::test]
async fn hand_authored_content_is_written_byte_for_byte() {
    let install = Install::start().await;
    for (path, content) in [
        ("notes/plain.md", "# Notes\n\nA document a person wrote.\n"),
        ("data/config.json", "{\n  \"a\": 1\n}\n"),
        ("empty.py", ""),
        ("script.py", "import sys\n\nprint(sys.argv)\n"),
        ("styles.css", "body { margin: 0; }\n"),
    ] {
        let (status, body, on_disk) = write(&install, path, content).await;
        assert_eq!(status, 200, "{path}: {body}");
        assert_eq!(on_disk, content, "{path} must not be rewritten");
        assert_eq!(
            body["result"]["bytes"],
            json!(content.len() as u64),
            "{path}"
        );
    }
}

#[tokio::test]
async fn the_refusals_still_come_first() {
    let install = Install::start().await;
    // Containment is judged before content: an escaping path is refused with
    // its own message, not with something the sanitizer decided.
    let answer = install
        .issue(
            "POST",
            "/tools/write_file",
            Some("session:owner"),
            Some(json!({"path": "../../tmp/escape.md", "content": "```\nx\n```"})),
        )
        .await;
    assert_eq!(answer.status, 400, "{}", answer.body);
    assert!(
        answer.body.contains("Path escapes the agent workspace."),
        "{}",
        answer.body
    );
}

fn write_review_skill(root: &std::path::Path) {
    let dir = root.join("code_review");
    std::fs::create_dir_all(&dir).unwrap();
    std::fs::write(
        dir.join("SKILL.md"),
        "name: code_review\ndescription: Review a file or snippet.\n\n# Skill: code_review\nReview code.\n",
    )
    .unwrap();
    std::fs::write(
        dir.join("schema.json"),
        r#"{"version":"1.4.0","input":{"required":["target"],"properties":{"target":{"type":"string"}}}}"#,
    )
    .unwrap();
}

fn rpc(method: &str, id: i64, params: Value) -> Value {
    json!({"jsonrpc": "2.0", "id": id, "method": method, "params": params})
}

#[tokio::test]
async fn streamable_mcp_initialize_lists_and_calls_tools() {
    let install = Install::start().await;
    write_review_skill(&install.skills_dir);

    let init = install
        .issue(
            "POST",
            "/mcp",
            Some("session:owner"),
            Some(rpc(
                "initialize",
                1,
                json!({
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0"}
                }),
            )),
        )
        .await;
    assert_eq!(init.status, 200, "{}", init.body);
    let init_body: Value = serde_json::from_str(&init.body).unwrap();
    assert_eq!(init_body["result"]["protocolVersion"], "2025-03-26");
    assert_eq!(init_body["result"]["serverInfo"]["name"], "lattice-ai");
    assert!(init_body["result"]["serverInfo"]["version"]
        .as_str()
        .is_some());
    assert!(init_body["result"]["capabilities"]["tools"].is_object());

    let notified = install
        .issue(
            "POST",
            "/mcp",
            Some("session:owner"),
            Some(json!({
                "jsonrpc": "2.0",
                "method": "notifications/initialized"
            })),
        )
        .await;
    assert_eq!(notified.status, 202, "{}", notified.body);
    assert!(notified.body.is_empty());

    let listed = install
        .issue(
            "POST",
            "/mcp",
            Some("session:owner"),
            Some(rpc("tools/list", 2, json!({}))),
        )
        .await;
    assert_eq!(listed.status, 200, "{}", listed.body);
    let listed_body: Value = serde_json::from_str(&listed.body).unwrap();
    let tools = listed_body["result"]["tools"].as_array().unwrap();
    let names: Vec<&str> = tools.iter().filter_map(|t| t["name"].as_str()).collect();
    assert!(names.contains(&"list_dir"), "{names:?}");
    assert!(names.contains(&"read_file"), "{names:?}");
    assert!(names.contains(&"skill.code_review"), "{names:?}");
    let skill = tools
        .iter()
        .find(|t| t["name"] == "skill.code_review")
        .unwrap();
    assert_eq!(skill["description"], "Review a file or snippet.");
    assert_eq!(
        skill["inputSchema"]["properties"]["target"]["type"],
        "string"
    );
    assert_eq!(skill["inputSchema"]["required"], json!(["target"]));

    let called = install
        .issue(
            "POST",
            "/mcp",
            Some("session:owner"),
            Some(rpc(
                "tools/call",
                3,
                json!({"name": "list_dir", "arguments": {"path": "."}}),
            )),
        )
        .await;
    assert_eq!(called.status, 200, "{}", called.body);
    let called_body: Value = serde_json::from_str(&called.body).unwrap();
    assert_eq!(called_body["result"]["isError"], false);
    let text = called_body["result"]["content"][0]["text"]
        .as_str()
        .unwrap();
    assert!(text.contains("fixture-note.md"), "{text}");

    let skill_call = install
        .issue(
            "POST",
            "/mcp",
            Some("session:owner"),
            Some(rpc(
                "tools/call",
                4,
                json!({"name": "skill.code_review", "arguments": {"target": "a.rs"}}),
            )),
        )
        .await;
    assert_eq!(skill_call.status, 200, "{}", skill_call.body);
    let skill_body: Value = serde_json::from_str(&skill_call.body).unwrap();
    let skill_text = skill_body["result"]["content"][0]["text"].as_str().unwrap();
    assert!(skill_text.contains("# Skill: code_review"), "{skill_text}");
    assert!(skill_text.contains("a.rs"), "{skill_text}");

    let unknown = install
        .issue(
            "POST",
            "/mcp",
            Some("session:owner"),
            Some(rpc("no/such", 5, json!({}))),
        )
        .await;
    assert_eq!(unknown.status, 200, "{}", unknown.body);
    let unknown_body: Value = serde_json::from_str(&unknown.body).unwrap();
    assert_eq!(unknown_body["error"]["code"], -32601);

    let malformed = install
        .issue(
            "POST",
            "/mcp",
            Some("session:owner"),
            Some(json!({"jsonrpc": "2.0", "id": 6})),
        )
        .await;
    let malformed_body: Value = serde_json::from_str(&malformed.body).unwrap();
    assert_eq!(malformed_body["error"]["code"], -32600);
}

#[tokio::test]
async fn streamable_mcp_governance_refusal_is_jsonrpc_error() {
    let install = Install::start().await;
    let refused = install
        .issue(
            "POST",
            "/mcp",
            Some("session:member"),
            Some(rpc(
                "tools/call",
                1,
                json!({"name": "knowledge_search", "arguments": {"query": "x"}}),
            )),
        )
        .await;
    assert_eq!(refused.status, 200, "{}", refused.body);
    let body: Value = serde_json::from_str(&refused.body).unwrap();
    assert_eq!(body["error"]["code"], -32001);
    assert!(
        body["error"]["message"]
            .as_str()
            .unwrap_or("")
            .contains("명시 승인이 필요합니다"),
        "{}",
        body
    );
    assert!(body.get("result").is_none());

    let allowed = install
        .issue(
            "POST",
            "/mcp",
            Some("session:member"),
            Some(rpc(
                "tools/call",
                2,
                json!({"name": "list_dir", "arguments": {"path": "."}}),
            )),
        )
        .await;
    let allowed_body: Value = serde_json::from_str(&allowed.body).unwrap();
    assert!(allowed_body.get("result").is_some(), "{allowed_body}");
    assert!(allowed_body.get("error").is_none());
}

#[tokio::test]
async fn mcp_call_dispatches_in_parity_with_tools_call() {
    let install = Install::start().await;
    let rest = install
        .issue(
            "POST",
            "/mcp/call",
            Some("session:owner"),
            Some(json!({"action": "list_dir", "args": {"path": "."}})),
        )
        .await;
    assert_eq!(rest.status, 200, "{}", rest.body);
    let rest_body: Value = serde_json::from_str(&rest.body).unwrap();
    assert_eq!(rest_body["status"], "ok");
    assert!(rest_body["result"]["items"].is_array());

    let rpc_answer = install
        .issue(
            "POST",
            "/mcp",
            Some("session:owner"),
            Some(rpc(
                "tools/call",
                1,
                json!({"name": "list_dir", "arguments": {"path": "."}}),
            )),
        )
        .await;
    let rpc_body: Value = serde_json::from_str(&rpc_answer.body).unwrap();
    assert_eq!(
        rpc_body["result"]["structuredContent"]["items"],
        rest_body["result"]["items"]
    );

    let unknown = install
        .issue(
            "POST",
            "/mcp/call",
            Some("session:owner"),
            Some(json!({"action": "not-a-tool", "args": {}})),
        )
        .await;
    assert_eq!(unknown.status, 400, "{}", unknown.body);
    assert_eq!(
        serde_json::from_str::<Value>(&unknown.body).unwrap()["detail"],
        "Unknown action: not-a-tool"
    );

    let denied = install
        .issue(
            "POST",
            "/mcp/call",
            Some("session:member"),
            Some(json!({"action": "knowledge_search", "args": {"query": "x"}})),
        )
        .await;
    assert_eq!(denied.status, 403, "{}", denied.body);
}

#[tokio::test]
async fn mcp_install_is_honest_about_remote_and_enables_skills() {
    let install = Install::start().await;
    write_review_skill(&install.skills_dir);

    let unknown = install
        .issue(
            "POST",
            "/mcp/install",
            Some("session:owner"),
            Some(json!({"mcp_id": "no-such-mcp"})),
        )
        .await;
    assert_eq!(unknown.status, 404, "{}", unknown.body);

    let remote = install
        .issue(
            "POST",
            "/mcp/install",
            Some("session:owner"),
            Some(json!({"mcp_id": "fixture-mcp"})),
        )
        .await;
    assert_eq!(remote.status, 200, "{}", remote.body);
    let remote_body: Value = serde_json::from_str(&remote.body).unwrap();
    assert_eq!(remote_body["status"], "manual_required");
    assert_eq!(remote_body["install_mode"], "npm");
    assert!(remote_body["instructions"].as_array().unwrap().len() >= 2);

    let bundled = install
        .issue(
            "POST",
            "/mcp/install",
            Some("session:owner"),
            Some(json!({"mcp_id": "filesystem"})),
        )
        .await;
    assert_eq!(bundled.status, 200, "{}", bundled.body);
    assert_eq!(
        serde_json::from_str::<Value>(&bundled.body).unwrap()["status"],
        "already_available"
    );

    let skill = install
        .issue(
            "POST",
            "/mcp/install",
            Some("session:owner"),
            Some(json!({"mcp_id": "code_review"})),
        )
        .await;
    assert_eq!(skill.status, 200, "{}", skill.body);
    let skill_body: Value = serde_json::from_str(&skill.body).unwrap();
    assert_eq!(skill_body["status"], "ok");
    assert_eq!(skill_body["kind"], "skill");
    assert_eq!(skill_body["skill"]["enabled"], true);
    assert_eq!(skill_body["skill"]["installed"], true);

    let market = install
        .issue(
            "POST",
            "/mcp/install",
            Some("session:owner"),
            Some(json!({"mcp_id": "fixture-skill"})),
        )
        .await;
    assert_eq!(market.status, 200, "{}", market.body);
    assert_eq!(
        serde_json::from_str::<Value>(&market.body).unwrap()["status"],
        "ok"
    );
}
