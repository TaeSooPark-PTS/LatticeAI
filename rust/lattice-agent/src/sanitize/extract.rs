//! `extraction.py` — recover the intended payload from a model reply.

use crate::pyjson;
use crate::pystr::char_len;

use super::text::{chat_line, ext_of, fence, find_ci, matched, rfind_ci, think_block, think_open};

// ── extraction (`extraction.py`) ────────────────────────────────────────────

/// `_EXT_FENCE_LANGS` — the fence languages that identify a payload.
fn fence_langs(ext: &str) -> &'static [&'static str] {
    match ext {
        ".html" | ".htm" => &["html", "htm", "xhtml"],
        ".css" => &["css"],
        ".js" => &["js", "javascript"],
        ".jsx" => &["jsx", "javascript"],
        ".ts" => &["ts", "typescript"],
        ".tsx" => &["tsx", "typescript"],
        ".py" => &["py", "python"],
        ".json" => &["json"],
        ".yaml" | ".yml" => &["yaml", "yml"],
        ".toml" => &["toml"],
        ".md" | ".markdown" => &["md", "markdown"],
        ".sql" => &["sql"],
        ".sh" => &["sh", "bash", "shell", "zsh"],
        ".xml" => &["xml", "svg"],
        ".csv" => &["csv"],
        ".txt" => &["txt", "text", "plaintext"],
        ".vue" => &["vue", "html"],
        ".svelte" => &["svelte", "html"],
        _ => &[],
    }
}

/// `extract_file_content(raw, target_path)`.
pub fn extract_file_content(raw: &str, target_path: &str) -> String {
    let text = raw.trim();
    if text.is_empty() {
        return String::new();
    }
    // Gemma-4 / gpt-oss wrap the document in `<|channel>thought` frames.
    // Drop those before think/fence extraction so a framed JS reply validates.
    let text = match crate::channel::strip_channel_frames(text) {
        Some(stripped) if !stripped.is_empty() => stripped,
        _ => text.to_string(),
    };
    let text = think_block().replace_all(&text, "");
    let text = think_open().replace_all(&text, "").trim().to_string();

    let ext = ext_of(target_path);
    // An odd number of fences means the last block never closed; Python closes
    // it so the payload inside is still recoverable.
    let padded = if text.matches("```").count() % 2 == 1 {
        format!("{text}\n```")
    } else {
        text.clone()
    };
    let fences: Vec<(String, String)> = fence()
        .captures_iter(&padded)
        .filter_map(Result::ok)
        .map(|captures| {
            let group = |index: usize| {
                captures
                    .get(index)
                    .map(|found| found.as_str().to_string())
                    .unwrap_or_default()
            };
            (group(1), group(2))
        })
        .collect();

    let mut content = if fences.is_empty() {
        strip_chat_lines(&text)
    } else {
        let wanted = fence_langs(&ext);
        let matching: Vec<&str> = fences
            .iter()
            .filter(|(lang, _)| wanted.contains(&lang.to_lowercase().as_str()))
            .map(|(_, body)| body.as_str())
            .collect();
        let candidates: Vec<&str> = if matching.is_empty() {
            fences.iter().map(|(_, body)| body.as_str()).collect()
        } else {
            matching
        };
        // `max(candidates, key=len)` keeps the **first** longest on a tie, and
        // `len` counts characters.
        candidates
            .into_iter()
            .fold(None::<&str>, |best, candidate| match best {
                Some(best) if char_len(best) >= char_len(candidate) => Some(best),
                _ => Some(candidate),
            })
            .unwrap_or_default()
            .trim()
            .to_string()
    };

    if ext == ".html" || ext == ".htm" {
        content = slice_html_document(&content);
    } else if ext == ".json" {
        if let Some(sliced) = slice_json_document(&content) {
            content = sliced;
        }
    }
    content.trim().to_string()
}

/// `_strip_chat_lines(text)`.
fn strip_chat_lines(text: &str) -> String {
    let lines: Vec<&str> = text.split('\n').collect();
    let (mut start, mut end) = (0usize, lines.len());
    let noise = |line: &str| line.trim().is_empty() || matched(chat_line(), line);
    while start < end && noise(lines[start]) {
        start += 1;
    }
    while end > start && noise(lines[end - 1]) {
        end -= 1;
    }
    let stripped = lines[start..end].join("\n").trim().to_string();
    if stripped.is_empty() {
        text.trim().to_string()
    } else {
        stripped
    }
}

/// `_slice_html_document(content)`.
pub(super) fn slice_html_document(content: &str) -> String {
    let mut out = content.to_string();
    // Prefer the last complete document. A thought preamble often *mentions*
    // `<!doctype html>` before the real payload starts.
    if let Some(end) = rfind_ci(&out, "</html>") {
        let close = end + "</html>".len();
        let head = &out[..close];
        if let Some(start) = rfind_ci(head, "<!doctype").or_else(|| rfind_ci(head, "<html")) {
            out = out[start..close].to_string();
        } else {
            out.truncate(close);
        }
    } else if let Some(start) = find_ci(&out, "<!doctype").or_else(|| find_ci(&out, "<html")) {
        if start > 0 {
            out = out[start..].to_string();
        }
    }
    out
}

/// `_slice_json_document(content)` — the largest parseable value inside.
pub(super) fn slice_json_document(content: &str) -> Option<String> {
    let mut candidates: Vec<&str> = vec![content];
    for (opener, closer) in [('{', '}'), ('[', ']')] {
        if let (Some(start), Some(end)) = (content.find(opener), content.rfind(closer)) {
            if end > start {
                candidates.push(&content[start..=end]);
            }
        }
    }
    let mut best: Option<&str> = None;
    for candidate in candidates {
        if pyjson::loads(candidate).is_err() {
            continue;
        }
        if best.is_none_or(|current| char_len(candidate) > char_len(current)) {
            best = Some(candidate);
        }
    }
    best.map(str::to_string)
}
