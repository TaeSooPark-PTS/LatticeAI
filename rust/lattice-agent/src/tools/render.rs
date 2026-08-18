//! `create_docx` / `create_xlsx` / `create_pptx` / `create_pdf` — the split.
//!
//! These four are the plan's "render flip": the builders need python-docx,
//! openpyxl, python-pptx and reportlab, so the **compute** stays in the worker
//! and only the **write** moves. The worker answers bytes
//! (`POST /worker/render/{kind}`, WP-W2) and this side does what the Python
//! handler did after `document.save(path)`: resolve the target through the
//! creator's own output directory, write it, and report the size on disk.
//!
//! The counts in the result (`rows`, `slides`) are the ones Python derived from
//! the *arguments* — `len(rows)` and `1 + len(slides)`, the title slide being
//! the one the builder adds. W2 §3 reports the same numbers beside the bytes, so
//! the seam's answer is preferred and the argument-derived count is the fallback
//! for a builder that sends none: two ways to be right, never two answers.
//!
//! One reconciliation with W2 lives here: its request models type `title`,
//! `body`, `sheet_name` and `filename` as `str`, while the handlers accepted
//! whatever the model produced and stringified it themselves. `_body_to_str`
//! and the `or`-defaults therefore run on this side, before the call.

use serde_json::{json, Map, Value};

use crate::surface::worker::WorkerClient;
use crate::tools::args;
use crate::tools::files::{file_size, io_error};
use crate::tools::sandbox::{ToolError, Workspace};

/// tool → (render kind, default filename).
const CREATORS: [(&str, &str, &str); 4] = [
    ("create_docx", "docx", "document.docx"),
    ("create_pdf", "pdf", "document.pdf"),
    ("create_pptx", "pptx", "presentation.pptx"),
    ("create_xlsx", "xlsx", "spreadsheet.xlsx"),
];

/// Whether `tool` is one of the four document creators.
pub fn is_creator(tool: &str) -> bool {
    CREATORS.iter().any(|(name, _, _)| *name == tool)
}

/// `rows` / `slides` may arrive as a JSON **string**; `_h_create_xlsx` and
/// `_h_create_pptx` parse it before the builder sees it.
fn listed(args: &Map<String, Value>, key: &str) -> Result<Vec<Value>, ToolError> {
    let raw = match args.get(key) {
        None => return Ok(Vec::new()),
        Some(Value::String(text)) => serde_json::from_str::<Value>(text).map_err(|error| {
            // `json.loads` raises `JSONDecodeError`, which the seam does not
            // catch — a 500. Named as a deviation: an error step instead.
            ToolError::tool(format!("{key} is not valid JSON: {error}"))
        })?,
        Some(other) => other.clone(),
    };
    match raw {
        Value::Array(items) => Ok(items),
        // `slides or []` tolerates a falsy value; `rows` is validated by the
        // builder ("Rows must be a list of lists.") and re-checked below.
        Value::Null => Ok(Vec::new()),
        other => Ok(vec![other]),
    }
}

/// `_body_to_str`: a list becomes its items joined by a blank line, anything
/// else is `str(body or "")`.
fn body_to_str(body: Option<&Value>) -> String {
    match body {
        Some(Value::Array(items)) => items
            .iter()
            .map(crate::parse::pystr::py_str)
            .collect::<Vec<_>>()
            .join("\n\n"),
        Some(value) if crate::parse::pystr::is_truthy(value) => crate::parse::pystr::py_str(value),
        _ => String::new(),
    }
}

/// Build one document: compute in the worker, write here.
pub async fn create_document(
    worker: &WorkerClient,
    workspace: &Workspace,
    tool: &str,
    arguments: &Map<String, Value>,
) -> Result<Value, ToolError> {
    let Some((_, kind, default_name)) = CREATORS.iter().find(|(name, _, _)| *name == tool) else {
        return Err(ToolError::tool(format!(
            "'{tool}' has no document output target."
        )));
    };
    // `a.get("filename", default)`, then `_safe_filename(name or "artifact…")`:
    // an absent key is the handler default and a *present* falsy one is the
    // creator's own `artifact.<ext>` fallback, which `safe_filename` applies.
    let filename = args::defaulted_str(arguments, "filename", default_name);

    // W2's request models type `title`, `body`, `sheet_name` and `filename` as
    // `str`, while the Python handlers accepted whatever the model produced and
    // stringified it themselves (`_body_to_str`, `str(title)`, `sheet_name or
    // "Sheet1"`). The coercion moves here, so a list body still becomes a
    // document instead of a 422 from a stricter schema.
    let mut request = Map::new();
    request.insert("filename".into(), json!(filename));
    let mut extra: Option<(&str, usize)> = None;
    match *kind {
        "xlsx" => {
            let rows = listed(arguments, "rows")?;
            if !rows.iter().all(Value::is_array) {
                return Err(ToolError::tool("Rows must be a list of lists."));
            }
            extra = Some(("rows", rows.len()));
            request.insert("rows".into(), Value::Array(rows));
            request.insert(
                "sheet_name".into(),
                json!(args::defaulted_str(arguments, "sheet_name", "Sheet1")),
            );
        }
        "pptx" => {
            let slides = listed(arguments, "slides")?;
            // The builder always adds a title slide, so the deck is one longer
            // than the caller's list — that is what `len(presentation.slides)`
            // counted.
            extra = Some(("slides", slides.len() + 1));
            request.insert("slides".into(), Value::Array(slides));
            request.insert(
                "title".into(),
                json!(args::defaulted_str(arguments, "title", "")),
            );
        }
        _ => {
            request.insert(
                "title".into(),
                json!(args::defaulted_str(arguments, "title", "")),
            );
            request.insert("body".into(), json!(body_to_str(arguments.get("body"))));
        }
    }

    // The target is resolved before the call: a filename that escapes the
    // workspace must not cost a document render first.
    let relative = crate::tools::documents::document_output_target(tool, &filename)
        .ok_or_else(|| ToolError::tool(format!("'{tool}' has no document output target.")))?;
    let target = workspace.resolve(&relative)?;

    let rendered = worker
        .render(kind, Value::Object(request))
        .await
        .map_err(ToolError::tool)?;

    if let Some(parent) = target.parent() {
        std::fs::create_dir_all(parent).map_err(io_error)?;
    }
    std::fs::write(&target, &rendered.content).map_err(io_error)?;

    let mut result = Map::new();
    result.insert("path".into(), json!(workspace.relative(&target)));
    if let Some((key, derived)) = extra {
        // W2 §3 reports the count at the top level; a nested `meta` is honoured
        // too, and the argument-derived count stands when neither is present.
        let reported = rendered
            .report
            .get(key)
            .or_else(|| rendered.report.get("meta").and_then(|meta| meta.get(key)))
            .and_then(Value::as_u64)
            .unwrap_or(derived as u64);
        result.insert(key.into(), json!(reported));
    }
    result.insert("bytes".into(), json!(file_size(&target)));
    Ok(Value::Object(result))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn args(value: Value) -> Map<String, Value> {
        value.as_object().expect("object").clone()
    }

    #[test]
    fn the_four_creators_are_the_document_target_table() {
        for (tool, _, _) in CREATORS {
            assert!(is_creator(tool), "{tool}");
            assert!(
                crate::tools::documents::document_output_target(tool, "x").is_some(),
                "{tool}"
            );
        }
        assert!(!is_creator("write_file"));
        assert_eq!(CREATORS.len(), 4);
    }

    #[test]
    fn a_json_string_of_rows_is_parsed_the_way_the_handler_parses_it() {
        let rows = listed(&args(json!({"rows": "[[1, 2], [3]]"})), "rows").expect("rows");
        assert_eq!(rows, vec![json!([1, 2]), json!([3])]);
        assert_eq!(
            listed(&Map::new(), "rows").expect("absent"),
            Vec::<Value>::new()
        );
        assert_eq!(
            listed(&args(json!({"rows": null})), "rows").expect("null"),
            Vec::<Value>::new()
        );
        let error = listed(&args(json!({"rows": "not json"})), "rows").expect_err("broken");
        assert!(
            error.message.starts_with("rows is not valid JSON: "),
            "{}",
            error.message
        );
    }

    #[tokio::test]
    async fn a_render_writes_the_bytes_into_the_creators_own_directory() {
        let dir = tempfile::tempdir().expect("tempdir");
        let workspace = Workspace::new(dir.path().join("agent_workspace")).expect("workspace");
        let server = super::tests_support::render_server(json!({
            "content_b64": "SGVsbG8=", "meta": {}
        }))
        .await;
        let worker = WorkerClient::new(&server.origin);
        let result = create_document(
            &worker,
            &workspace,
            "create_docx",
            &args(json!({"title": "보고서", "body": "본문", "filename": "../report"})),
        )
        .await
        .expect("render");
        assert_eq!(result["path"], "generated_documents/report.docx");
        assert_eq!(result["bytes"], 5);
        assert_eq!(
            std::fs::read(workspace.root().join("generated_documents/report.docx")).expect("file"),
            b"Hello"
        );
        let sent = server.last_request();
        assert_eq!(sent["path"], "/worker/render/docx");
        assert_eq!(sent["body"]["title"], "보고서");
        assert_eq!(sent["body"]["filename"], "../report");
    }

    #[tokio::test]
    async fn the_counts_come_from_the_arguments_and_the_builder_can_override_them() {
        let dir = tempfile::tempdir().expect("tempdir");
        let workspace = Workspace::new(dir.path().join("agent_workspace")).expect("workspace");
        let server = super::tests_support::render_server(json!({"content_b64": "AAAA"})).await;
        let worker = WorkerClient::new(&server.origin);

        let sheet = create_document(
            &worker,
            &workspace,
            "create_xlsx",
            &args(json!({"rows": [[1, 2], [3, 4], [5, 6]]})),
        )
        .await
        .expect("xlsx");
        assert_eq!(sheet["rows"], 3);
        assert_eq!(sheet["path"], "generated_spreadsheets/spreadsheet.xlsx");
        assert_eq!(server.last_request()["body"]["sheet_name"], "Sheet1");

        let deck = create_document(
            &worker,
            &workspace,
            "create_pptx",
            &args(json!({"slides": [{"title": "one"}, {"title": "two"}]})),
        )
        .await
        .expect("pptx");
        assert_eq!(deck["slides"], 3, "the builder's title slide counts");

        let told = super::tests_support::render_server(json!({
            "content_b64": "AAAA", "meta": {"slides": 9}
        }))
        .await;
        let deck = create_document(
            &WorkerClient::new(&told.origin),
            &workspace,
            "create_pptx",
            &args(json!({"slides": []})),
        )
        .await
        .expect("pptx");
        assert_eq!(deck["slides"], 9, "a builder that counts wins");
    }

    #[tokio::test]
    async fn a_builder_refusal_reaches_the_transcript_verbatim() {
        let dir = tempfile::tempdir().expect("tempdir");
        let workspace = Workspace::new(dir.path().join("agent_workspace")).expect("workspace");
        let server = super::tests_support::render_server(json!({
            "error": "openpyxl is not installed. Run `pip install -r requirements.txt`."
        }))
        .await;
        let error = create_document(
            &WorkerClient::new(&server.origin),
            &workspace,
            "create_xlsx",
            &args(json!({"rows": []})),
        )
        .await
        .expect_err("refused");
        assert_eq!(
            error.message,
            "openpyxl is not installed. Run `pip install -r requirements.txt`."
        );
        assert!(
            !workspace.root().join("generated_spreadsheets").exists(),
            "a refused render leaves no directory behind"
        );
    }

    #[tokio::test]
    async fn rows_that_are_not_lists_are_refused_before_the_worker_is_called() {
        let dir = tempfile::tempdir().expect("tempdir");
        let workspace = Workspace::new(dir.path().join("agent_workspace")).expect("workspace");
        let server = super::tests_support::render_server(json!({"content_b64": "AAAA"})).await;
        let error = create_document(
            &WorkerClient::new(&server.origin),
            &workspace,
            "create_xlsx",
            &args(json!({"rows": [1, 2]})),
        )
        .await
        .expect_err("not lists");
        assert_eq!(error.message, "Rows must be a list of lists.");
        assert_eq!(server.requests(), 0, "nothing was rendered");
    }

    #[tokio::test]
    async fn the_request_is_coerced_into_w2s_string_typed_schema() {
        let dir = tempfile::tempdir().expect("tempdir");
        let workspace = Workspace::new(dir.path().join("agent_workspace")).expect("workspace");
        let server = super::tests_support::render_server(json!({"content_b64": "AAAA"})).await;
        create_document(
            &WorkerClient::new(&server.origin),
            &workspace,
            "create_docx",
            // A *list* body is what `_body_to_str` existed for, and W2's model
            // types the field `str` — the join has to happen on this side.
            &args(json!({"title": 7, "body": ["one", "two"]})),
        )
        .await
        .expect("docx");
        let sent = server.last_request();
        assert_eq!(sent["body"]["body"], "one\n\ntwo");
        assert_eq!(sent["body"]["title"], "7");

        create_document(
            &WorkerClient::new(&server.origin),
            &workspace,
            "create_xlsx",
            &args(json!({"rows": [], "sheet_name": null})),
        )
        .await
        .expect("xlsx");
        assert_eq!(
            server.last_request()["body"]["sheet_name"],
            "",
            "a falsy sheet name reaches the builder's own `or \"Sheet1\"`"
        );
    }

    #[test]
    fn the_body_join_is_pythons() {
        assert_eq!(body_to_str(Some(&json!(["a", "b"]))), "a\n\nb");
        assert_eq!(body_to_str(Some(&json!([]))), "");
        assert_eq!(body_to_str(Some(&json!("plain"))), "plain");
        assert_eq!(body_to_str(Some(&json!(null))), "");
        assert_eq!(body_to_str(None), "");
        assert_eq!(body_to_str(Some(&json!([1, true]))), "1\n\nTrue");
    }

    #[tokio::test]
    async fn a_falsy_filename_becomes_the_creators_artifact_fallback() {
        let dir = tempfile::tempdir().expect("tempdir");
        let workspace = Workspace::new(dir.path().join("agent_workspace")).expect("workspace");
        let server = super::tests_support::render_server(json!({"content_b64": "AAAA"})).await;
        let result = create_document(
            &WorkerClient::new(&server.origin),
            &workspace,
            "create_pdf",
            &args(json!({"title": "t", "filename": null})),
        )
        .await
        .expect("pdf");
        assert_eq!(result["path"], "generated_pdfs/artifact.pdf");
    }

    #[tokio::test]
    async fn an_unreachable_worker_is_a_step_error_not_a_panic() {
        let dir = tempfile::tempdir().expect("tempdir");
        let workspace = Workspace::new(dir.path().join("agent_workspace")).expect("workspace");
        let error = create_document(
            &WorkerClient::new("http://127.0.0.1:1"),
            &workspace,
            "create_pdf",
            &args(json!({"title": "t"})),
        )
        .await
        .expect_err("unreachable");
        assert!(error.message.contains("unreachable"), "{}", error.message);
    }
}

/// A one-route fake worker, shared by this module's tests.
#[cfg(test)]
pub(crate) mod tests_support {
    use std::sync::{Arc, Mutex};

    use serde_json::{json, Value};

    pub(crate) struct RenderServer {
        pub origin: String,
        seen: Arc<Mutex<Vec<Value>>>,
    }

    impl RenderServer {
        pub fn last_request(&self) -> Value {
            self.seen
                .lock()
                .expect("lock")
                .last()
                .cloned()
                .expect("a request was made")
        }

        pub fn requests(&self) -> usize {
            self.seen.lock().expect("lock").len()
        }
    }

    /// Start a server that answers every `/worker/render/*` with `body`.
    pub(crate) async fn render_server(body: Value) -> RenderServer {
        let seen: Arc<Mutex<Vec<Value>>> = Arc::new(Mutex::new(Vec::new()));
        let sink = Arc::clone(&seen);
        let app = axum::Router::new().route(
            "/worker/render/:kind",
            axum::routing::post(
                move |axum::extract::Path(kind): axum::extract::Path<String>,
                      axum::Json(request): axum::Json<Value>| {
                    let sink = Arc::clone(&sink);
                    let body = body.clone();
                    async move {
                        sink.lock().expect("lock").push(json!({
                            "path": format!("/worker/render/{kind}"), "body": request,
                        }));
                        axum::Json(body)
                    }
                },
            ),
        );
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
            .await
            .expect("bind");
        let origin = format!("http://{}", listener.local_addr().expect("addr"));
        tokio::spawn(async move {
            let _ = axum::serve(listener, app).await;
        });
        RenderServer { origin, seen }
    }
}
