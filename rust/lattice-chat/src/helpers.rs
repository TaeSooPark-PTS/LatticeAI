//! Pure chat helpers — a 1:1 port of `latticeai/api/chat_helpers.py`.
//!
//! Intent detection, file-target parsing, network-status formatting and the
//! answer-citation binding. Everything here is a total function of its
//! arguments: no database, no clock, no I/O. That is why the whole module is
//! unit-testable against the Python originals' documented edges.
//!
//! `fancy_regex`, not `regex`: `_FILE_TARGET_RE` opens with the lookbehind
//! `(?<![\w.-])`, which `regex` refuses to compile at all. The rest of the
//! patterns would work either way, and are kept on one engine so the semantics
//! (backtracking, leftmost-first) are `re`'s everywhere.
//!
//! Two Python behaviours a port loses by accident and this one keeps:
//!
//! * `detect_language` counts **characters**, not bytes — `len()` on a `str`.
//! * `str.strip("`'\".,:;)]}")` strips a *set* of characters from both ends,
//!   not a prefix; `trim_matches` over the same set is the equivalent.

use std::sync::OnceLock;

use fancy_regex::Regex;
use lattice_core::pytext::strip;
use serde_json::{json, Map, Value};

use crate::pyvalue::field;

fn compile(cell: &'static OnceLock<Regex>, pattern: &str) -> &'static Regex {
    cell.get_or_init(|| Regex::new(pattern).expect("ported pattern must compile"))
}

fn hit(regex: &Regex, text: &str) -> bool {
    regex.is_match(text).unwrap_or(false)
}

/// The per-language instruction prepended to every prompt (`_LANG_HINT`).
pub fn language_hint(language: &str) -> &'static str {
    match language {
        "ko" => "Respond in Korean (한국어로 답변하세요).",
        _ => "Respond in English.",
    }
}

/// `detect_language` — `"ko"` when over 5 % of the characters are Hangul.
pub fn detect_language(text: &str) -> &'static str {
    let total = text.chars().count().max(1);
    let korean = text.chars().filter(|c| ('가'..='힣').contains(c)).count();
    if korean as f64 / total as f64 > 0.05 {
        "ko"
    } else {
        "en"
    }
}

const NETWORK_PHRASES: [&str; 6] = [
    "ipconfig",
    "ifconfig",
    "network status",
    "네트워크 상태",
    "네트워크 확인",
    "현재 네트워크",
];

/// `is_network_status_request`.
pub fn is_network_status_request(text: &str) -> bool {
    static CURRENT_IP: OnceLock<Regex> = OnceLock::new();
    static IP_VERB: OnceLock<Regex> = OnceLock::new();
    let lower = text.to_lowercase();
    if NETWORK_PHRASES.iter().any(|phrase| lower.contains(phrase)) {
        return true;
    }
    hit(
        compile(
            &CURRENT_IP,
            r"(내|현재|지금|로컬|local|public|공인|외부|내부)\s*(ip|아이피)\s*(주소)?",
        ),
        &lower,
    ) || hit(
        compile(
            &IP_VERB,
            r"(ip|아이피)\s*(주소)?\s*(확인|상태|알려줘|보여줘)",
        ),
        &lower,
    )
}

const URL_PHRASES: [&str; 4] = ["현재 url", "current url", "page url", "페이지 url"];

/// `is_current_url_request`.
pub fn is_current_url_request(text: &str) -> bool {
    static PAGE_LINK: OnceLock<Regex> = OnceLock::new();
    static URL_VERB: OnceLock<Regex> = OnceLock::new();
    let lower = text.to_lowercase();
    if URL_PHRASES.iter().any(|phrase| lower.contains(phrase)) {
        return true;
    }
    hit(
        compile(
            &PAGE_LINK,
            r"(현재|지금|여기|이\s*페이지|브라우저|접속)\s*(페이지\s*)?(url|링크|주소)",
        ),
        &lower,
    ) || hit(
        compile(&URL_VERB, r"(url|링크)\s*(알려줘|보여줘|확인)"),
        &lower,
    )
}

/// `is_clear_command` — the two spellings of "wipe the screen".
pub fn is_clear_command(text: &str) -> bool {
    matches!(strip(text).to_lowercase().as_str(), "/clear" | "/clear_all")
}

/// The characters `file_action_target` strips off both ends of its match.
const TARGET_TRIM: [char; 10] = ['`', '\'', '"', '.', ',', ':', ';', ')', ']', '}'];

fn file_target_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    compile(
        &RE,
        r"(?i)(?<![\w.-])(?:[~./\\]?[\w.@()+-]+[\\/])*[\w.@()+-]+\.(?:py|js|jsx|ts|tsx|md|markdown|txt|json|yaml|yml|toml|html|css|csv|xml|pdf|docx|xlsx|pptx|sh|sql)",
    )
}

const QUESTION_PHRASES: [&str; 7] = [
    "how to ",
    "how do i ",
    "방법",
    "어떻게",
    "예시",
    "sample",
    "example",
];
const OVERRIDE_PHRASES: [&str; 6] = [
    "actually create",
    "real file",
    "실제로",
    "파일로",
    "저장해",
    "만들어",
];
/// Explicit artifact types are here on purpose: a user asking for "an html
/// page" expects a real file, not a code block in chat.
const FILE_WORDS: &[&str] = &[
    "file",
    "파일",
    "문서",
    "artifact",
    "아티팩트",
    "save as",
    "저장",
    "html",
    "웹페이지",
    "웹 페이지",
    "홈페이지",
    "webpage",
    "web page",
];
/// The Python tuple also lists `만들어줘`, which is a superstring of `만들`:
/// membership is unchanged, so it is not repeated here.
const ACTION_WORDS: &[&str] = &[
    "create", "make", "write", "save", "generate", "edit", "update", "만들", "생성", "작성",
    "저장", "수정", "써줘",
];

/// `is_file_action_request` — should this turn run file tools instead of prose?
pub fn is_file_action_request(text: &str) -> bool {
    let raw = strip(text);
    if raw.is_empty() {
        return false;
    }
    let lower = raw.to_lowercase();

    let asks_how = QUESTION_PHRASES.iter().any(|p| lower.contains(p));
    let overrides = OVERRIDE_PHRASES.iter().any(|p| lower.contains(p));
    if asks_how && !overrides {
        return false;
    }

    let has_target = file_target_re().is_match(&raw).unwrap_or(false);
    let has_file_word = FILE_WORDS.iter().any(|w| lower.contains(w));
    // "만들어줘" is in the Python tuple as its own entry; it is a superstring of
    // "만들", so the membership answer is identical and the list stays short.
    let has_action = ACTION_WORDS.iter().any(|w| lower.contains(w));

    if !has_action {
        return false;
    }
    if has_target {
        return true;
    }
    has_file_word
}

/// `file_action_target` — the first explicit workspace file the request names.
pub fn file_action_target(text: &str) -> Option<String> {
    let found = file_target_re().find(text).ok().flatten()?;
    let trimmed = strip(found.as_str())
        .trim_matches(|c| TARGET_TRIM.contains(&c))
        .to_string();
    Some(trimmed)
}

/// `inline_file_action_content` — short user-supplied content for a direct write.
///
/// Every pattern requires an explicit binder, so "create a text file report.txt"
/// does not capture "file report.txt" as the file's content.
pub fn inline_file_action_content(text: &str) -> Option<String> {
    static PATTERNS: OnceLock<Vec<Regex>> = OnceLock::new();
    let patterns = PATTERNS.get_or_init(|| {
        [
            r"(?is)(?:내용|본문)\s*(?:은|는|이에요|입니다)\s*(.+)$",
            r"(?is)(?:내용|본문|content|body|text)\s*[:=]\s*(.+)$",
            r"(?is)(?:content|body)\s+(?:is|as)\s+(.+)$",
            r"(?is)(?:with the content|with content|containing)\s+(.+)$",
        ]
        .iter()
        .map(|pattern| Regex::new(pattern).expect("ported pattern must compile"))
        .collect()
    });
    let raw = strip(text);
    for pattern in patterns {
        if let Ok(Some(captures)) = pattern.captures(&raw) {
            let content = strip(captures.get(1).map(|m| m.as_str()).unwrap_or(""));
            return Some(content.trim_matches(['`', '\'', '"']).to_string());
        }
    }
    None
}

/// `strip_generated_file_content` — unwrap a fenced block, if the model used one.
pub fn strip_generated_file_content(text: &str) -> String {
    static FENCE: OnceLock<Regex> = OnceLock::new();
    let content = strip(text);
    let fence = compile(&FENCE, r"(?s)```(?:[\w.+-]+)?\s*(.*?)\s*```");
    match fence.captures(&content) {
        Ok(Some(captures)) => strip(captures.get(1).map(|m| m.as_str()).unwrap_or("")),
        _ => content,
    }
}

fn or_unknown(value: Option<&Value>) -> String {
    match value {
        Some(Value::String(text)) if !text.is_empty() => text.clone(),
        Some(other) if !matches!(other, Value::Null | Value::Bool(false)) => other.to_string(),
        _ => "확인 안 됨".to_string(),
    }
}

/// `format_network_status` — the answer the network intent speaks.
pub fn format_network_status(info: &Map<String, Value>) -> String {
    let mut lines = vec![
        format!("내부 IP: {}", or_unknown(info.get("local_ip"))),
        format!("외부 IP: {}", or_unknown(info.get("public_ip"))),
        format!("호스트명: {}", or_unknown(info.get("hostname"))),
    ];
    if let Some(Value::Object(local_ips)) = info.get("local_ips") {
        if !local_ips.is_empty() {
            lines.push(String::new());
            lines.push("인터페이스:".to_string());
            for (name, ip) in local_ips {
                let ip = ip
                    .as_str()
                    .map(str::to_string)
                    .unwrap_or_else(|| ip.to_string());
                lines.push(format!("- {name}: {ip}"));
            }
        }
    }
    if let Some(note) = info
        .get("note")
        .and_then(Value::as_str)
        .filter(|n| !n.is_empty())
    {
        lines.push(String::new());
        lines.push(note.to_string());
    }
    lines.join("\n")
}

// ── answer-citation binding ──────────────────────────────────────────────────

/// `_GROUNDING_STOP_TOKENS` — tokens that overlap by accident, not by grounding.
const STOP_TOKENS: &[&str] = &[
    "그리고",
    "그러나",
    "하지만",
    "그래서",
    "있습니다",
    "입니다",
    "합니다",
    "있는",
    "없는",
    "위해",
    "통해",
    "대한",
    "관련",
    "경우",
    "내용",
    "answer",
    "the",
    "and",
    "for",
    "with",
    "this",
    "that",
    "from",
    "have",
    "are",
    "was",
    "were",
    "can",
    "will",
    "not",
    "you",
    "your",
];

/// `GROUNDING_MIN_OVERLAP_TOKENS`.
pub const GROUNDING_MIN_OVERLAP_TOKENS: usize = 2;
/// `GROUNDING_MIN_OVERLAP_RATIO`.
pub const GROUNDING_MIN_OVERLAP_RATIO: f64 = 0.08;

fn grounding_tokens(text: &str) -> Vec<String> {
    static RE: OnceLock<Regex> = OnceLock::new();
    let pattern = compile(&RE, r"[0-9A-Za-z가-힣]{2,}");
    let mut seen: Vec<String> = Vec::new();
    let mut position = 0usize;
    while let Ok(Some(found)) = pattern.find_from_pos(text, position) {
        position = found.end().max(found.start() + 1);
        let token = found.as_str().to_lowercase();
        if STOP_TOKENS.contains(&token.as_str()) {
            continue;
        }
        if !seen.contains(&token) {
            seen.push(token);
        }
    }
    seen
}

fn text_of(value: &Value, key: &str) -> String {
    value
        .get(key)
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string()
}

fn joined(parts: &[Value]) -> String {
    parts
        .iter()
        .map(|part| match part {
            Value::String(text) => text.clone(),
            Value::Null => String::new(),
            other => other.to_string(),
        })
        .collect::<Vec<_>>()
        .join(" ")
}

/// One retrieved source, as `assess_answer_grounding` reduces it.
struct Source {
    id: String,
    title: String,
    body: String,
}

fn sources_of(trace: Option<&Value>) -> Vec<Source> {
    let mut sources = Vec::new();
    let mut seen: Vec<String> = Vec::new();
    let empty: Vec<Value> = Vec::new();
    let array = |key: &str| -> Vec<Value> {
        trace
            .and_then(|trace| trace.get(key))
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_else(|| empty.clone())
    };
    for node in array("graph_nodes") {
        if !node.is_object() {
            continue;
        }
        let id = match (node.get("id"), node.get("node_id")) {
            (Some(Value::String(id)), _) if !id.is_empty() => id.clone(),
            (_, Some(Value::String(id))) if !id.is_empty() => id.clone(),
            _ => String::new(),
        };
        if id.is_empty() || seen.contains(&id) {
            continue;
        }
        seen.push(id.clone());
        let filename = node
            .get("metadata")
            .and_then(Value::as_object)
            .and_then(|meta| meta.get("filename"))
            .cloned()
            .unwrap_or(Value::Null);
        sources.push(Source {
            title: text_of(&node, "title"),
            body: joined(&[
                node.get("title").cloned().unwrap_or(Value::Null),
                node.get("summary").cloned().unwrap_or(Value::Null),
                filename,
            ]),
            id,
        });
    }
    for src in array("source_files") {
        if !src.is_object() {
            continue;
        }
        let id = match (src.get("node_id"), src.get("source")) {
            (Some(Value::String(id)), _) if !id.is_empty() => id.clone(),
            (_, Some(Value::String(id))) if !id.is_empty() => id.clone(),
            _ => String::new(),
        };
        if id.is_empty() || seen.contains(&id) {
            continue;
        }
        seen.push(id.clone());
        sources.push(Source {
            title: text_of(&src, "node_title"),
            body: joined(&[
                src.get("node_title").cloned().unwrap_or(Value::Null),
                src.get("source").cloned().unwrap_or(Value::Null),
            ]),
            id,
        });
    }
    sources
}

/// `pair_user_history` — one identity's exchanges, never someone else's replies.
pub fn pair_user_history(history: &[Value], user_email: &str) -> Vec<Value> {
    let mut paired = Vec::new();
    let mut include_next_assistant = false;
    for item in history {
        if field(item, "role") == "assistant" {
            if include_next_assistant {
                let assistant_user = field(item, "user_email");
                if !assistant_user.is_empty() && assistant_user != user_email {
                    continue;
                }
                paired.push(item.clone());
                include_next_assistant = false;
            }
        } else if field(item, "user_email") == user_email {
            paired.push(item.clone());
            include_next_assistant = true;
        } else {
            include_next_assistant = false;
        }
    }
    paired
}

/// `build_recent_chat_context` over an already-scoped history slice.
pub fn build_recent_chat_context(
    history: &[Value],
    limit: usize,
    include_image_missing_replies: bool,
    user_email: Option<&str>,
    conversation_id: Option<&str>,
    workspace_id: Option<&str>,
) -> String {
    let mut items: Vec<&Value> = history.iter().collect();
    if let Some(workspace_id) = workspace_id {
        items.retain(|item| {
            let workspace = item
                .get("workspace_id")
                .and_then(Value::as_str)
                .filter(|value| !value.is_empty())
                .unwrap_or("personal");
            workspace == workspace_id
        });
    }
    if let Some(conversation_id) = conversation_id.filter(|id| !id.is_empty()) {
        items.retain(|item| field(item, "conversation_id") == conversation_id);
    }
    let owned: Vec<Value> = items.into_iter().cloned().collect();
    let scoped = if let Some(user_email) = user_email.filter(|email| !email.is_empty()) {
        pair_user_history(&owned, user_email)
    } else {
        owned
    };
    let start = scoped.len().saturating_sub(limit);
    let mut lines = Vec::new();
    for item in &scoped[start..] {
        let role = field(item, "role");
        let role = if role.is_empty() {
            "user".to_string()
        } else {
            role
        };
        let content = field(item, "content");
        if !include_image_missing_replies && role == "assistant" {
            let mentions_image = content.contains("이미지")
                && ["업로드", "제공", "올려"]
                    .iter()
                    .any(|word| content.contains(word));
            if mentions_image {
                continue;
            }
        }
        let source = field(item, "source");
        let label = if source.is_empty() {
            role
        } else {
            format!("{role} ({source})")
        };
        lines.push(format!("{label}: {content}"));
    }
    lines.join("\n")
}

/// `assess_answer_grounding` — bind an answer to its sources, honestly.
///
/// Annotation only: the answer is never modified or blocked. `no_context` is a
/// distinct status from `unsupported` so the UI can tell "the Brain had nothing"
/// from "the Brain had sources and the answer ignored them".
pub fn assess_answer_grounding(
    answer: &str,
    trace: Option<&Value>,
    context_quality: Option<&Value>,
) -> Value {
    let answer_text = strip(answer);
    let sources = sources_of(trace.filter(|value| value.is_object()));
    let mode = context_quality
        .and_then(|quality| quality.get("mode"))
        .and_then(Value::as_str)
        .filter(|mode| !mode.is_empty())
        .unwrap_or("none")
        .to_string();

    if sources.is_empty() {
        return json!({
            "status": "no_context",
            "label": "근거 없음",
            "source_ids": [],
            "cited": [],
            "overlap": 0.0,
            "reason": if mode == "none" {
                "검색된 출처가 없습니다"
            } else {
                "출처 후보를 답변에 연결하지 못했습니다"
            },
        });
    }
    if answer_text.is_empty() {
        return json!({
            "status": "unsupported",
            "label": "근거 없음",
            "source_ids": [],
            "cited": [],
            "overlap": 0.0,
            "reason": "답변이 비어 있습니다",
        });
    }

    let answer_tokens = grounding_tokens(&answer_text);
    let answer_lower = answer_text.to_lowercase();
    let mut cited: Vec<Value> = Vec::new();
    let mut source_ids: Vec<Value> = Vec::new();
    let mut best_overlap = 0.0f64;
    for source in &sources {
        let source_tokens = grounding_tokens(&source.body);
        if source_tokens.is_empty() {
            continue;
        }
        let shared = source_tokens
            .iter()
            .filter(|token| answer_tokens.contains(token))
            .count();
        let denominator = source_tokens.len().clamp(1, 60) as f64;
        let ratio = shared as f64 / denominator;
        best_overlap = best_overlap.max(ratio);
        let title = strip(&source.title).to_lowercase();
        let explicit =
            !title.is_empty() && title.chars().count() >= 4 && answer_lower.contains(&title);
        // Explicit citation OR token overlap. Comparisons are explicit: a 0.0
        // score is falsy but a valid value, so `or`-style defaults are wrong.
        if explicit
            || (shared >= GROUNDING_MIN_OVERLAP_TOKENS && ratio >= GROUNDING_MIN_OVERLAP_RATIO)
        {
            source_ids.push(json!(source.id));
            cited.push(json!({
                "id": source.id,
                "title": source.title,
                "overlap": lattice_core::pytext::round4(ratio),
                "explicit": explicit,
            }));
        }
    }
    if !cited.is_empty() {
        return json!({
            "status": "supported",
            "label": "근거 있음",
            "source_ids": source_ids,
            "cited": cited,
            "overlap": lattice_core::pytext::round4(best_overlap),
            "reason": Value::Null,
        });
    }
    json!({
        "status": "unsupported",
        "label": "근거 없음",
        "source_ids": [],
        "cited": [],
        "overlap": lattice_core::pytext::round4(best_overlap),
        "reason": "답변이 검색된 출처의 내용을 사용하지 않았습니다",
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn language_detection_counts_characters() {
        assert_eq!(detect_language("무엇을 기억하고 있나요?"), "ko");
        assert_eq!(detect_language("what do you remember?"), "en");
        assert_eq!(detect_language(""), "en");
        // 1 Hangul character in 19 is 5.2 %, over the threshold.
        assert_eq!(detect_language("aaaaaaaaaaaaaaaaaa가"), "ko");
        assert_eq!(detect_language("aaaaaaaaaaaaaaaaaaaa가"), "en");
        assert_eq!(
            language_hint("ko"),
            "Respond in Korean (한국어로 답변하세요)."
        );
        assert_eq!(language_hint("de"), "Respond in English.");
    }

    #[test]
    fn the_network_intent_matches_phrases_and_both_patterns() {
        assert!(is_network_status_request("네트워크 상태 알려줘"));
        assert!(is_network_status_request("IPCONFIG"));
        assert!(is_network_status_request("내 ip 주소"));
        assert!(is_network_status_request("아이피 확인"));
        assert!(!is_network_status_request("무엇을 기억하고 있나요?"));
        assert!(!is_network_status_request(""));
    }

    #[test]
    fn the_current_url_intent_matches_phrases_and_both_patterns() {
        assert!(is_current_url_request("현재 url 알려줘"));
        assert!(is_current_url_request("이 페이지 주소 알려줘"));
        assert!(is_current_url_request("링크 보여줘"));
        assert!(is_current_url_request("Page URL"));
        assert!(!is_current_url_request("hello"));
    }

    #[test]
    fn the_clear_command_takes_exactly_two_spellings() {
        assert!(is_clear_command("/clear"));
        assert!(is_clear_command("  /CLEAR_ALL \n"));
        assert!(!is_clear_command("/clearall"));
        assert!(!is_clear_command(""));
    }

    #[test]
    fn file_intent_needs_an_action_and_a_target_or_a_file_word() {
        assert!(is_file_action_request("fixture-note.md 파일 만들어줘"));
        assert!(is_file_action_request("create report.txt"));
        assert!(is_file_action_request("html 페이지 만들어줘"));
        assert!(!is_file_action_request("report.txt"), "no action verb");
        assert!(!is_file_action_request("how do i create a file?"));
        assert!(
            is_file_action_request("how do i actually create report.txt"),
            "the override phrase re-opens the gate"
        );
        assert!(!is_file_action_request("   "));
        assert!(
            !is_file_action_request("만들어줘"),
            "no target, no file word"
        );
    }

    #[test]
    fn the_target_regex_keeps_single_token_paths_and_trims_punctuation() {
        assert_eq!(
            file_action_target("fixture-note.md 파일 만들어줘"),
            Some("fixture-note.md".into())
        );
        assert_eq!(
            file_action_target("create `docs/notes.md`."),
            Some("docs/notes.md".into())
        );
        assert_eq!(file_action_target("no target here"), None);
    }

    #[test]
    fn inline_content_needs_an_explicit_binder() {
        assert_eq!(
            inline_file_action_content("fixture-note.md 파일 만들어줘. 내용: Hello Lattice"),
            Some("Hello Lattice".into())
        );
        assert_eq!(
            inline_file_action_content("내용은 두 줄\n입니다"),
            Some("두 줄\n입니다".into())
        );
        assert_eq!(
            inline_file_action_content("report.md with the content \"a b\""),
            Some("a b".into())
        );
        assert_eq!(inline_file_action_content("body is  x "), Some("x".into()));
        assert_eq!(
            inline_file_action_content("create a text file report.txt"),
            None,
            "an ambiguous word without a binder captures nothing"
        );
    }

    #[test]
    fn generated_content_loses_its_fence() {
        assert_eq!(strip_generated_file_content("```md\n# hi\n```"), "# hi");
        assert_eq!(strip_generated_file_content("```\nplain\n```"), "plain");
        assert_eq!(strip_generated_file_content("  no fence  "), "no fence");
    }

    #[test]
    fn network_status_formats_every_optional_block() {
        let info: Map<String, Value> = serde_json::from_value(json!({
            "local_ip": "10.0.0.2", "public_ip": null, "hostname": "box",
            "local_ips": {"en0": "10.0.0.2"}, "note": "partial",
        }))
        .unwrap();
        assert_eq!(
            format_network_status(&info),
            "내부 IP: 10.0.0.2\n외부 IP: 확인 안 됨\n호스트명: box\n\n인터페이스:\n- en0: 10.0.0.2\n\npartial"
        );
        let bare: Map<String, Value> = Map::new();
        assert_eq!(
            format_network_status(&bare),
            "내부 IP: 확인 안 됨\n외부 IP: 확인 안 됨\n호스트명: 확인 안 됨"
        );
    }

    #[test]
    fn grounding_reports_no_context_when_the_trace_is_empty() {
        let verdict = assess_answer_grounding("answer", None, None);
        assert_eq!(verdict["status"], "no_context");
        assert_eq!(verdict["reason"], "검색된 출처가 없습니다");
        let verdict = assess_answer_grounding("answer", None, Some(&json!({"mode": "hybrid"})));
        assert_eq!(verdict["reason"], "출처 후보를 답변에 연결하지 못했습니다");
    }

    #[test]
    fn grounding_binds_by_overlap_and_by_explicit_title() {
        let trace = json!({
            "graph_nodes": [
                {"id": "n1", "title": "리트리벌 가중치", "summary": "가중치 정리 문서",
                 "metadata": {"filename": "weights.md"}},
                {"id": "n1", "title": "duplicate"},
                {"not": "an object"},
                {"title": "no id"},
            ],
            "source_files": [
                {"node_id": "n1", "node_title": "already seen"},
                {"source": "orphan.md", "node_title": "Orphan Document"},
                "not an object",
            ],
        });
        let verdict = assess_answer_grounding(
            "리트리벌 가중치 문서를 정리했습니다",
            Some(&trace),
            Some(&json!({"mode": "hybrid"})),
        );
        assert_eq!(verdict["status"], "supported");
        assert_eq!(verdict["source_ids"][0], "n1");
        assert_eq!(verdict["cited"][0]["explicit"], true);

        let verdict = assess_answer_grounding("완전히 다른 이야기", Some(&trace), None);
        assert_eq!(verdict["status"], "unsupported");
        assert_eq!(verdict["overlap"], 0.0);

        let verdict = assess_answer_grounding("", Some(&trace), None);
        assert_eq!(verdict["status"], "unsupported");
        assert_eq!(verdict["reason"], "답변이 비어 있습니다");
    }

    #[test]
    fn grounding_skips_sources_with_no_usable_tokens() {
        let trace = json!({"graph_nodes": [{"id": "n", "title": "", "summary": ""}]});
        let verdict = assess_answer_grounding("something", Some(&trace), None);
        assert_eq!(verdict["status"], "unsupported");
        assert!(
            grounding_tokens("the and for").is_empty(),
            "stop tokens drop"
        );
        assert_eq!(grounding_tokens("your You"), Vec::<String>::new());
    }
}
