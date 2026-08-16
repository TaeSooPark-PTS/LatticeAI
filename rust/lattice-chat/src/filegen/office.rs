//! Office and PDF targets: a real render, or an honest refusal.
//!
//! Until v11.9.0 a chat request for `report.docx` wrote the *text* to a file
//! named `.docx` and answered "만들었습니다". Word opens that and says the file
//! is corrupt, which is the worst kind of failure: the product reported success
//! and the user found out later. `rust/fixtures/http/chat.json`'s
//! `file_intent_docx_artifact` is the capture of that behaviour.
//!
//! There is exactly one thing in this process that can produce real OOXML or
//! PDF bytes, and it is not this process: `POST /worker/render/{kind}` (on
//! `rust/fixtures/worker_allowlist.json`, group `compute_seam`) builds them with
//! python-docx / reportlab. This module reaches that seam through
//! [`lattice_agent::worker::WorkerClient::render`] — the client the agent's four
//! document creators already use — so there is one implementation of the render
//! call, one base64 decode and one set of error strings for both write surfaces.
//!
//! ## Why only `.docx` and `.pdf`
//!
//! The seam's other two builders take *structure*, not prose: `xlsx` wants
//! `rows: [[…], …]` and `pptx` wants `slides: [{…}, …]`. Turning one block of
//! model prose into either is a guess about what the columns or the slides were
//! meant to be, and a guess that lands in a file the user then relies on. Chat
//! refuses those two by name and says where they *are* built — the agent, whose
//! `create_xlsx` / `create_pptx` tools take the structure as arguments.

use std::time::Duration;

use lattice_agent::sanitize::ext_of;
use lattice_agent::worker::WorkerClient;

use crate::state::ChatState;

/// How long one document render may take before it is a failure.
///
/// A local render of a few paragraphs is milliseconds; this is the ceiling that
/// keeps a wedged worker from holding a chat request open forever.
const RENDER_TIMEOUT: Duration = Duration::from_secs(60);

/// What a target's extension means for how its bytes are made.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum Target {
    /// An ordinary file: its bytes are its text.
    Text,
    /// A document the render seam builds, and the kind that builds it.
    Rendered(&'static str),
    /// A binary Office format with no honest mapping from a block of prose.
    Unsupported,
}

/// Extensions this surface must never write as text, and what it does instead.
const BINARY_DOCUMENTS: [(&str, Target); 4] = [
    (".docx", Target::Rendered("docx")),
    (".pdf", Target::Rendered("pdf")),
    (".pptx", Target::Unsupported),
    (".xlsx", Target::Unsupported),
];

/// How this target's bytes must be produced.
pub(crate) fn classify(target: &str) -> Target {
    let ext = ext_of(target);
    BINARY_DOCUMENTS
        .iter()
        .find(|(extension, _)| *extension == ext)
        .map(|(_, target)| *target)
        .unwrap_or(Target::Text)
}

/// Build the real document bytes from the text a person typed or a model wrote.
///
/// The body is the whole text and the title is deliberately empty: the builder
/// adds a heading when it is given one, and a heading nobody asked for is
/// content this surface invented.
pub(crate) async fn render(
    state: &ChatState,
    kind: &str,
    filename: &str,
    text: &str,
) -> Result<Vec<u8>, String> {
    let Some(worker) = state.worker.as_ref() else {
        return Err("no AI worker is reachable, so the document cannot be built".to_string());
    };
    // `WorkerSeamClient` does not lend out its inner pool, so this builds its
    // own — with `no_proxy()`, because a machine-wide HTTP_PROXY must never
    // intercept loopback traffic to our own worker, and with a timeout, because
    // `WorkerClient` has none of its own. A document render is a once-per-file
    // action, not a hot path.
    let client = reqwest::Client::builder()
        .no_proxy()
        .timeout(RENDER_TIMEOUT)
        .build()
        .map_err(|error| error.to_string())?;
    let seam = WorkerClient::with_client(worker.origin(), client);
    let body = serde_json::json!({"filename": filename, "title": "", "body": text});
    seam.render(kind, body)
        .await
        .map(|rendered| rendered.content)
}

/// The refusal for a target no honest mapping reaches, in the caller's language.
pub(crate) fn unsupported(lang: &str, target: &str) -> String {
    if lang == "en" {
        format!(
            "Chat writes text formats (html, md, txt, css, js, json …) and Word/PDF documents. \
             {target} is a spreadsheet/deck, which needs its rows or slides as data — ask the \
             agent for it, where create_xlsx / create_pptx take that structure."
        )
    } else {
        format!(
            "채팅은 글 형식 파일(html, md, txt, css, js, json …)과 Word/PDF 문서를 만듭니다. \
             {target} 같은 표·발표 자료는 행이나 슬라이드를 데이터로 받아야 해서, 에이전트에게 \
             요청해 주세요 (create_xlsx / create_pptx 가 그 구조를 그대로 받습니다)."
        )
    }
}

/// The refusal for a render that was attempted and failed.
pub(crate) fn render_failed(lang: &str, target: &str, detail: &str) -> String {
    if lang == "en" {
        format!("{target} could not be built as a real document: {detail}")
    } else {
        format!("{target} 파일을 진짜 문서로 만들지 못했습니다: {detail}")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_four_binary_targets_are_the_ones_that_must_not_be_written_as_text() {
        assert_eq!(classify("report.docx"), Target::Rendered("docx"));
        assert_eq!(classify("report.PDF"), Target::Rendered("pdf"));
        assert_eq!(classify("sheet.xlsx"), Target::Unsupported);
        assert_eq!(classify("deck.pptx"), Target::Unsupported);
        assert_eq!(classify("note.md"), Target::Text);
        assert_eq!(classify("noext"), Target::Text);
    }

    #[test]
    fn the_refusals_name_the_file_and_the_way_that_works() {
        let ko = unsupported("ko", "sheet.xlsx");
        assert!(ko.contains("sheet.xlsx") && ko.contains("에이전트"));
        let en = unsupported("en", "sheet.xlsx");
        assert!(en.contains("sheet.xlsx") && en.contains("agent"));
        assert!(render_failed("ko", "a.docx", "boom").contains("boom"));
        assert!(render_failed("en", "a.docx", "boom").starts_with("a.docx"));
    }

    #[tokio::test]
    async fn a_render_without_a_worker_is_a_named_failure_not_a_written_file() {
        let state = crate::state::ChatState::new(
            lattice_auth::AuthState::new(lattice_auth::AuthConfig::default()),
            crate::state::ChatConfig::default(),
        );
        let error = render(&state, "docx", "a.docx", "본문")
            .await
            .expect_err("no worker, no document");
        assert!(error.contains("no AI worker"), "{error}");
    }
}
