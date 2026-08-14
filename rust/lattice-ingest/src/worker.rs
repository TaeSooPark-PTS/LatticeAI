//! Delegation to the Python worker — the single-writer seam.
//!
//! This crate detects, parses, chunks and judges duplicates. It does not write
//! the graph, and there is no code path here that could: the only way material
//! reaches the Brain is `POST {origin}/knowledge-graph/ingest` with
//! `type="note"`, which is the same door the MCP surface and the chat client
//! already use (`latticeai/api/knowledge_graph.py:475`). Everything the worker
//! does on the other side — extraction, quality gating, provenance, workspace
//! scoping, the vector queue — keeps happening exactly once, in one place.
//!
//! Authentication is the caller's business. The endpoint calls `require_user`,
//! so a deployment with auth on must supply the cookie or header its worker
//! expects; [`WorkerClient::with_header`] is how, and this module never invents
//! a credential of its own.

#![allow(
    dead_code,
    unused_imports,
    unused_variables,
    unused_assignments,
    unused_mut,
    private_interfaces,
    clippy::result_large_err,
    clippy::needless_lifetimes,
    clippy::too_many_arguments,
    clippy::type_complexity,
    clippy::collapsible_if,
    clippy::needless_as_bytes,
    clippy::redundant_closure,
    clippy::needless_return,
    clippy::manual_clamp,
    clippy::ptr_arg,
    clippy::unnecessary_sort_by,
    clippy::result_unit_err,
    clippy::useless_vec,
    clippy::uninlined_format_args,
    clippy::manual_contains,
    clippy::needless_borrows_for_generic_args,
    clippy::implicit_clone,
    clippy::unnecessary_map_or,
    clippy::match_like_matches_macro,
    clippy::manual_range_contains,
    clippy::derivable_impls,
    clippy::needless_pass_by_ref_mut,
    clippy::redundant_guards,
    clippy::map_identity,
    clippy::iter_overeager_cloned,
    clippy::explicit_auto_deref,
    clippy::bool_comparison,
    clippy::nonminimal_bool,
    clippy::if_same_then_else,
    clippy::question_mark,
    clippy::single_char_pattern,
    clippy::manual_pattern_char_comparison,
    clippy::manual_is_ascii_check,
    clippy::repeat_once,
    clippy::unused_self,
    clippy::module_inception
)]
use std::collections::BTreeMap;
use std::path::Path;
use std::time::Duration;

use serde_json::{Map, Value};

/// The worker path this crate delegates to.
pub const INGEST_PATH: &str = "/knowledge-graph/ingest";
/// How long a delegation may take before it is abandoned.
pub const DEFAULT_TIMEOUT: Duration = Duration::from_secs(30);

/// Why a delegation did not happen.
#[derive(Debug)]
pub enum WorkerError {
    /// The HTTP client could not be built.
    Client(String),
    /// The request never completed.
    Transport(String),
    /// The worker answered, and said no.
    Rejected {
        /// HTTP status.
        status: u16,
        /// Body, truncated for a log line.
        detail: String,
    },
    /// The worker answered 2xx with something that was not JSON.
    Malformed(String),
}

impl std::fmt::Display for WorkerError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            WorkerError::Client(detail) => write!(formatter, "worker client: {detail}"),
            WorkerError::Transport(detail) => write!(formatter, "worker unreachable: {detail}"),
            WorkerError::Rejected { status, detail } => {
                write!(formatter, "worker refused the ingest ({status}): {detail}")
            }
            WorkerError::Malformed(detail) => {
                write!(formatter, "worker answered with non-JSON: {detail}")
            }
        }
    }
}

impl std::error::Error for WorkerError {}

/// One note to hand to the worker.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NoteSubmission {
    /// Human title — the filename, for a watched file.
    pub title: String,
    /// The extracted text.
    pub content: String,
    /// Where it came from, echoed into the node metadata.
    pub source: Option<String>,
    /// Extra provenance merged into the item metadata.
    pub metadata: Map<String, Value>,
}

impl NoteSubmission {
    /// A note for one file a watch detected.
    ///
    /// The metadata carries what the Python watcher carries — the root-relative
    /// path, the `folder_watch` marker and the watch id — plus the absolute
    /// path and the fact that a Rust scanner found it, so the provenance says
    /// who noticed rather than implying the worker did.
    pub fn from_watched_file(
        root: &Path,
        relative_path: &str,
        content: &str,
        watch_id: Option<&str>,
    ) -> Self {
        let path = root.join(relative_path);
        let title = Path::new(relative_path)
            .file_name()
            .map(|name| name.to_string_lossy().into_owned())
            .unwrap_or_else(|| relative_path.to_string());
        let mut metadata = Map::new();
        metadata.insert("relative_path".into(), Value::from(relative_path));
        metadata.insert("path".into(), Value::from(path.display().to_string()));
        metadata.insert("folder_watch".into(), Value::Bool(true));
        metadata.insert("detected_by".into(), Value::from("lattice-ingest"));
        if let Some(watch_id) = watch_id {
            metadata.insert("watch_id".into(), Value::from(watch_id));
        }
        Self {
            title,
            content: content.to_string(),
            source: Some(path.display().to_string()),
            metadata,
        }
    }

    /// The exact request body — a pure function, so a test can pin it without
    /// a server and an operator can diff it against what the worker expects.
    pub fn body(&self) -> Value {
        let mut body = Map::new();
        body.insert("type".into(), Value::from("note"));
        body.insert("title".into(), Value::from(self.title.clone()));
        body.insert("content".into(), Value::from(self.content.clone()));
        body.insert(
            "source".into(),
            match &self.source {
                Some(source) => Value::from(source.clone()),
                None => Value::Null,
            },
        );
        body.insert("metadata".into(), Value::Object(self.metadata.clone()));
        Value::Object(body)
    }
}

/// An HTTP client pointed at one worker origin.
#[derive(Debug, Clone)]
pub struct WorkerClient {
    client: reqwest::Client,
    origin: String,
    headers: BTreeMap<String, String>,
}

impl WorkerClient {
    /// A client with its own connection pool.
    ///
    /// `no_proxy()` matters for the same reason it does in the host: a
    /// machine-wide `HTTP_PROXY` must never intercept loopback traffic to our
    /// own worker.
    pub fn new(origin: impl Into<String>) -> Result<Self, WorkerError> {
        let client = reqwest::Client::builder()
            .no_proxy()
            .timeout(DEFAULT_TIMEOUT)
            .build()
            .map_err(|error| WorkerError::Client(error.to_string()))?;
        Ok(Self::with_client(client, origin))
    }

    /// A client reusing an existing pool (the host's, say).
    pub fn with_client(client: reqwest::Client, origin: impl Into<String>) -> Self {
        Self {
            client,
            origin: origin.into().trim_end_matches('/').to_string(),
            headers: BTreeMap::new(),
        }
    }

    /// Attach a header to every delegation — the auth seam.
    pub fn with_header(mut self, name: impl Into<String>, value: impl Into<String>) -> Self {
        self.headers.insert(name.into(), value.into());
        self
    }

    /// The worker origin, without a trailing slash.
    pub fn origin(&self) -> &str {
        &self.origin
    }

    /// The full ingest URL.
    pub fn ingest_url(&self) -> String {
        format!("{}{INGEST_PATH}", self.origin)
    }

    /// Hand one note to the worker and return whatever it answers.
    pub async fn submit_note(&self, note: &NoteSubmission) -> Result<Value, WorkerError> {
        let body = serde_json::to_vec(&note.body())
            .map_err(|error| WorkerError::Client(error.to_string()))?;
        let mut request = self
            .client
            .post(self.ingest_url())
            .header("content-type", "application/json")
            .header("accept", "application/json");
        for (name, value) in &self.headers {
            request = request.header(name.as_str(), value.as_str());
        }
        let response = request
            .body(body)
            .send()
            .await
            .map_err(|error| WorkerError::Transport(error.to_string()))?;
        let status = response.status();
        let text = response
            .text()
            .await
            .map_err(|error| WorkerError::Transport(error.to_string()))?;
        if !status.is_success() {
            return Err(WorkerError::Rejected {
                status: status.as_u16(),
                detail: text.chars().take(400).collect(),
            });
        }
        serde_json::from_str(&text)
            .map_err(|_| WorkerError::Malformed(text.chars().take(200).collect()))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_origin_loses_its_trailing_slash_exactly_once() {
        let client = WorkerClient::new("http://127.0.0.1:4825/").expect("client");
        assert_eq!(client.origin(), "http://127.0.0.1:4825");
        assert_eq!(
            client.ingest_url(),
            "http://127.0.0.1:4825/knowledge-graph/ingest"
        );
        let bare = WorkerClient::new("http://127.0.0.1:4825").expect("client");
        assert_eq!(bare.ingest_url(), client.ingest_url());
    }

    #[test]
    fn a_watched_file_becomes_a_note_with_its_provenance() {
        let note = NoteSubmission::from_watched_file(
            Path::new("/brain/notes"),
            "회의/2026-08-11.md",
            "결정 사항",
            Some("watch_abc"),
        );
        assert_eq!(note.title, "2026-08-11.md");
        assert_eq!(note.content, "결정 사항");
        let body = note.body();
        assert_eq!(body["type"], Value::from("note"));
        assert_eq!(body["title"], Value::from("2026-08-11.md"));
        assert_eq!(body["content"], Value::from("결정 사항"));
        assert_eq!(
            body["metadata"]["relative_path"],
            Value::from("회의/2026-08-11.md")
        );
        assert_eq!(
            body["metadata"]["path"],
            Value::from("/brain/notes/회의/2026-08-11.md")
        );
        assert_eq!(body["metadata"]["folder_watch"], Value::Bool(true));
        assert_eq!(body["metadata"]["watch_id"], Value::from("watch_abc"));
        assert_eq!(
            body["metadata"]["detected_by"],
            Value::from("lattice-ingest")
        );
    }

    #[test]
    fn without_a_watch_id_the_key_is_absent_rather_than_null() {
        let note = NoteSubmission::from_watched_file(Path::new("/root"), "a.txt", "x", None);
        let body = note.body();
        assert!(body["metadata"].get("watch_id").is_none());
        assert_eq!(body["source"], Value::from("/root/a.txt"));
        let bare = NoteSubmission {
            title: "t".into(),
            content: "c".into(),
            source: None,
            metadata: Map::new(),
        };
        assert_eq!(bare.body()["source"], Value::Null);
    }

    #[test]
    fn errors_say_what_went_wrong_in_words() {
        let rejected = WorkerError::Rejected {
            status: 403,
            detail: "forbidden".into(),
        };
        assert!(rejected.to_string().contains("403"));
        assert!(WorkerError::Transport("refused".into())
            .to_string()
            .contains("unreachable"));
        assert!(WorkerError::Malformed("<html>".into())
            .to_string()
            .contains("non-JSON"));
        assert!(WorkerError::Client("bad".into())
            .to_string()
            .contains("client"));
    }
}
