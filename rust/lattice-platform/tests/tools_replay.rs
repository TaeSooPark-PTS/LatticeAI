#![allow(dead_code, unused_imports, unused_variables)]
#![allow(clippy::all)]
mod mcp_harness;

use serde_json::{json, Value};

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
