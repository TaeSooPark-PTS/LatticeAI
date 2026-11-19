//! Write-endpoint sanitization and streamable MCP JSON-RPC parity tests.

#[allow(dead_code)]
mod mcp_harness;

use serde_json::{json, Value};

use mcp_harness::Install;

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
