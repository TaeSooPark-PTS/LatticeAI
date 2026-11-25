use std::time::Duration;

use lattice_agent::content::sanitize::ext_of;
use lattice_agent::seam::worker::WorkerClient;

use crate::state::ChatState;

const RENDER_TIMEOUT: Duration = Duration::from_secs(60);

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum Target {
    Text,
    Rendered(&'static str),
    Unsupported,
}

const BINARY_DOCUMENTS: [(&str, Target); 4] = [
    (".docx", Target::Rendered("docx")),
    (".pdf", Target::Rendered("pdf")),
    (".pptx", Target::Unsupported),
    (".xlsx", Target::Unsupported),
];

pub(crate) fn classify(target: &str) -> Target {
    let ext = ext_of(target);
    BINARY_DOCUMENTS
        .iter()
        .find(|(extension, _)| *extension == ext)
        .map(|(_, target)| *target)
        .unwrap_or(Target::Text)
}

pub(crate) async fn render(
    state: &ChatState,
    kind: &str,
    filename: &str,
    text: &str,
) -> Result<Vec<u8>, String> {
    let Some(worker) = state.worker.as_ref() else {
        return Err("no AI worker is reachable, so the document cannot be built".to_string());
    };
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
