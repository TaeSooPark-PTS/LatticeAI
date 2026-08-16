//! The anchored prompts a chat file-generation call carries.
//!
//! Every string here is written for the *weakest* model the product supports —
//! a 2B running locally — and every rule in it exists because that class of
//! model breaks it by default: it greets, it explains, it wraps the answer in a
//! Markdown fence, and it stops mid-document when the token budget runs out.
//!
//! The prompts are also written **against the validator that judges the reply**
//! ([`lattice_agent::sanitize::validate_file_content`]). That is the point of
//! the per-extension anchors: `.html` is judged by "starts with `<!doctype`,
//! ends with `</html>`, no fences", so the HTML anchor asks for exactly that,
//! in those words. A prompt that asked for something the validator refuses
//! would send every reply through the repair path and call the result the
//! model's work.
//!
//! `POST /worker/llm/stream` with `mode=document` puts [`instructions`] in as
//! **the** system prompt, verbatim (`stream_generate_document_as(model, message,
//! system_prompt)`); `mode=chat` would compose the product persona around it and
//! the persona asks for a conversational answer. So the mode is not an
//! optimisation, it is what makes these words the only instruction the model has.

use lattice_agent::sanitize::ext_of;

/// The rules every file-generation call carries, whatever the file type is.
pub const SYSTEM: &str = "You are writing one file, not a chat reply.

Answer with the complete contents of that file and nothing else:
- no greeting, no explanation, no summary, before or after the file;
- no Markdown code fences (```) anywhere in the answer;
- the first character of your answer is the first character of the file, and the last character of your answer is the last character of the file;
- the file must be finished and work on its own — never leave \"...\" or \"TODO\" where real content belongs;
- write the file's own text in the language the request is written in.";

/// `.html` / `.htm`.
pub const HTML: &str = "This file is one complete HTML document. Start with <!doctype html> and finish with </html>. Include <html>, a <head> with <meta charset=\"utf-8\"> and a <title>, and a <body>. There are no sibling files: put the styles in a <style> tag and the behaviour in a <script> tag inside this document.";

/// `.css`.
pub const CSS: &str = "This file is a CSS stylesheet: rule blocks only, in the form `selector { property: value; }`. No HTML, no <style> tag, no explanation.";

/// `.js` / `.jsx` / `.ts` / `.tsx`.
pub const SCRIPT: &str = "This file is source code that runs as written. Answer with the code only — no <script> tag and no HTML around it — and keep every brace, bracket and parenthesis balanced so the file cannot read as truncated. Do not import a file that does not exist.";

/// `.json`.
pub const JSON: &str = "This file is one JSON document. It must parse with a strict parser: double-quoted keys and strings, no comments, no trailing commas, and nothing before or after the document.";

/// `.md` / `.markdown`.
pub const MARKDOWN: &str = "This file is a Markdown document. Start at its first heading. Do not use code fences (```) anywhere — not even for a code sample; indent a code sample by four spaces instead.";

/// `.txt`.
pub const TEXT: &str = "This file is plain text. Answer with the text of the file itself — no Markdown fences, and no heading that announces what the file is.";

/// `.py`.
pub const PYTHON: &str = "This file is a Python module that must import and run exactly as written: real function bodies, type hints, standard library only unless the request names a package. No fences.";

/// `.csv`.
pub const CSV: &str = "This file is CSV: one header row of column names, then one record per line, comma-separated. No prose, no fences.";

/// `.yaml` / `.yml`.
pub const YAML: &str = "This file is a YAML document: two-space indentation, never tabs, nothing but the document itself.";

/// `.xml`.
pub const XML: &str = "This file is one XML document: a single root element with every tag closed.";

/// `.sql`.
pub const SQL: &str =
    "This file is SQL: statements only, each ended with a semicolon, comments written as -- lines.";

/// `.sh`.
pub const SHELL: &str =
    "This file is a shell script: a #!/bin/sh or #!/bin/bash shebang first, then the commands.";

/// `.vue` / `.svelte`.
pub const COMPONENT: &str = "This file is a single-file component. Write its markup, script and style blocks, and open and close every one of them.";

/// `.docx` / `.pdf` — the text a document render is typeset from.
pub const DOCUMENT: &str = "This is the text of a document that will be typeset into a real Word/PDF file. Write the document's own words as plain paragraphs separated by a blank line. No Markdown fences, no HTML tags.";

/// Any other extension: the shared rules, and nothing invented on top of them.
pub const FALLBACK: &str = "Answer with the contents of the file itself, and nothing else.";

/// The corrective instruction the one retry carries. `{reason}` is the
/// validator's own verdict on the previous answer, never user text.
pub const CORRECTION: &str = "Your previous answer could not be used as this file: {reason}.

Answer again with the file's contents only. The first character of your answer must be the first character of the file and the last character must be the last character of the file — no commentary, no code fences, nothing left unfinished.";

/// extension → anchor. Sorted; `anchor_for` binary-searches it.
const ANCHORS: [(&str, &str); 22] = [
    (".css", CSS),
    (".csv", CSV),
    (".docx", DOCUMENT),
    (".htm", HTML),
    (".html", HTML),
    (".js", SCRIPT),
    (".json", JSON),
    (".jsx", SCRIPT),
    (".markdown", MARKDOWN),
    (".md", MARKDOWN),
    (".pdf", DOCUMENT),
    (".py", PYTHON),
    (".sh", SHELL),
    (".sql", SQL),
    (".svelte", COMPONENT),
    (".ts", SCRIPT),
    (".tsx", SCRIPT),
    (".txt", TEXT),
    (".vue", COMPONENT),
    (".xml", XML),
    (".yaml", YAML),
    (".yml", YAML),
];

/// The anchor for this target's extension.
pub fn anchor_for(target: &str) -> &'static str {
    let ext = ext_of(target);
    ANCHORS
        .binary_search_by_key(&ext.as_str(), |(extension, _)| *extension)
        .map(|index| ANCHORS[index].1)
        .unwrap_or(FALLBACK)
}

/// The system prompt for a first attempt.
pub fn instructions(target: &str) -> String {
    format!("{SYSTEM}\n\n{}", anchor_for(target))
}

/// The system prompt for the one retry, naming what was wrong with the last one.
pub fn correction(target: &str, reason: &str) -> String {
    format!(
        "{}\n\n{}",
        instructions(target),
        CORRECTION.replace("{reason}", reason)
    )
}

/// The user turn: the request in the user's own words, then the file to write.
///
/// `brief` is the manifest's line about this file when the request is a
/// multi-file project ([`lattice_agent::inference::infer_project_manifest`]),
/// so file two knows it is the stylesheet file one links to.
pub fn user_turn(request: &str, target: &str, brief: Option<&str>) -> String {
    let mut turn = request.trim().to_string();
    if let Some(brief) = brief.map(str::trim).filter(|brief| !brief.is_empty()) {
        turn.push_str("\n\nWhat this file must contain: ");
        turn.push_str(brief);
    }
    turn.push_str(&format!("\n\nWrite the complete contents of {target} now."));
    turn
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_anchor_table_is_sorted_because_the_lookup_binary_searches_it() {
        let mut sorted = ANCHORS.to_vec();
        sorted.sort_by_key(|(extension, _)| *extension);
        assert_eq!(ANCHORS.to_vec(), sorted);
    }

    #[test]
    fn every_previewable_type_has_an_anchor_of_its_own() {
        // The set a chat file action can preview is the set it can be asked to
        // write; a previewable type falling through to FALLBACK would be a
        // prompt that never mentions the grammar the validator then applies.
        for extension in crate::intents::PREVIEWABLE {
            assert_ne!(
                anchor_for(&format!("file{extension}")),
                FALLBACK,
                "{extension} has no anchor"
            );
        }
    }

    #[test]
    fn the_anchor_follows_the_extension_case_insensitively() {
        assert_eq!(anchor_for("page.HTML"), HTML);
        assert_eq!(anchor_for("a/b/page.htm"), HTML);
        assert_eq!(anchor_for("report.docx"), DOCUMENT);
        assert_eq!(anchor_for("noextension"), FALLBACK);
        assert_eq!(anchor_for("archive.tar"), FALLBACK);
    }

    #[test]
    fn the_instructions_carry_the_rules_and_the_anchor() {
        let system = instructions("page.html");
        assert!(system.starts_with(SYSTEM));
        assert!(system.contains("<!doctype html>"));
        // The retry keeps both and names the fault on top of them.
        let retry = correction("page.html", "HTML document is truncated (missing </html>)");
        assert!(retry.contains("<!doctype html>"));
        assert!(retry.contains("HTML document is truncated (missing </html>)"));
        assert!(!retry.contains("{reason}"));
    }

    #[test]
    fn the_user_turn_keeps_the_request_and_names_the_file() {
        let turn = user_turn("HTML 파일 만들어줘", "generated_page.html", None);
        assert!(turn.starts_with("HTML 파일 만들어줘"));
        assert!(turn.ends_with("Write the complete contents of generated_page.html now."));
        assert!(!turn.contains("What this file must contain"));
        let briefed = user_turn("만들어줘", "style.css", Some("  All visual styles.  "));
        assert!(briefed.contains("What this file must contain: All visual styles."));
        // An empty brief is no brief, not an empty line.
        assert!(!user_turn("x", "a.md", Some("   ")).contains("What this file must contain"));
    }
}
