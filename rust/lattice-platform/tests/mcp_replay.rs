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
