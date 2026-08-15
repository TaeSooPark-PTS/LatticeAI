//! `POST /tools/write_file` runs the write-side guarantee (v11.7.0).
//!
//! The hole this closes is named in the 11.6.0 release notes §5.3: Python
//! applied `sanitize_write_content` in the agent loop only, so a payload posted
//! to this endpoint — the VS Code extension's writes, an MCP tool call, a
//! script — was persisted exactly as the model produced it, fences and all.
//! The recorded HTTP fixtures (`fixtures/http/tools_misc.json`) all carry clean
//! content and are unaffected, which is the point: sanitize is an identity
//! transform on content that validates.

#![allow(dead_code, unused_imports, unused_variables)]
#![allow(clippy::all)]
mod mcp_harness;

use mcp_harness::Install;
use serde_json::{json, Value};

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
