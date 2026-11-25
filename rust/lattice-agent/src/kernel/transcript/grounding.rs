use std::collections::BTreeSet;
use std::sync::OnceLock;

use fancy_regex::Regex;
use serde_json::Value;

use crate::kernel::state::AgentState;

const FILE_EXTS: &[&str] = &[
    "md", "txt", "rs", "py", "ts", "tsx", "js", "jsx", "mjs", "cjs", "json", "toml", "yaml", "yml",
    "html", "css", "sql", "sh", "csv", "go", "java", "kt", "swift", "c", "cpp", "h",
];

fn named_file_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(r"(?<![A-Za-z0-9_/@.-])([A-Za-z0-9_./-]*[A-Za-z0-9_-]\.([A-Za-z0-9]{1,6}))(?::(\d+))?(?![A-Za-z0-9_.-])")
            .expect("valid named_file_re")
    })
}

fn backtick_ident_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(r"`([A-Za-z_][A-Za-z0-9_]{3,63})(?:\(\))?`").expect("valid backtick_ident_re")
    })
}

fn is_known_file_ext(ext: &str) -> bool {
    let lower = ext.to_ascii_lowercase();
    FILE_EXTS.contains(&lower.as_str())
}

fn basename_lower(path: &str) -> String {
    let sq = char::from(39u8);
    let bs = char::from(92u8);
    let trimmed = path.trim().trim_matches(['`', '"', sq]);
    trimmed
        .rsplit(['/', bs])
        .next()
        .unwrap_or(trimmed)
        .to_ascii_lowercase()
}

fn extract_named_files(text: &str) -> Vec<(String, Option<String>)> {
    let mut out = Vec::new();
    for caps in named_file_re().captures_iter(text).flatten() {
        let Some(full) = caps.get(1).map(|m| m.as_str()) else {
            continue;
        };
        let Some(ext) = caps.get(2).map(|m| m.as_str()) else {
            continue;
        };
        if !is_known_file_ext(ext) {
            continue;
        };
        let line = caps.get(3).map(|m| m.as_str().to_string());
        out.push((full.to_string(), line));
    }
    out
}

fn request_is_pure_creation(request: &str) -> bool {
    let lower = request.to_lowercase();
    let create_markers = [
        "생성",
        "만들어",
        "작성해",
        "저장해",
        "써줘",
        "써 줘",
        "write ",
        "create ",
        "save ",
        "generate ",
    ];
    let read_markers = [
        "읽",
        "요약",
        "설명",
        "분석",
        "확인해",
        "알려줘",
        "알려 줘",
        "무엇",
        "뭐가",
        "어떤",
        "read ",
        "summarize",
        "explain",
        "analyze",
        "inspect",
        "what is in",
        "what does",
        "check ",
    ];
    let has_create = create_markers.iter().any(|m| lower.contains(m));
    let has_read = read_markers.iter().any(|m| lower.contains(m));
    has_create && !has_read
}

fn request_asks_file_inspection(request: &str) -> bool {
    let lower = request.to_lowercase();
    if request_is_pure_creation(request) {
        return false;
    }
    let inspect_markers = [
        "읽고",
        "읽어",
        "파일 내용",
        "내용을",
        "안의",
        "안에",
        "무엇이",
        "뭐가 있",
        "설명해",
        "분석해",
        "찾아",
        "무슨 함수",
        "무슨 설정",
        "알려줘",
        "알려 줘",
        "read ",
        "in file",
        "inside ",
        "explain ",
        "analyze ",
        "inspect ",
        "what does ",
        "what is inside",
        "contents of",
    ];
    inspect_markers.iter().any(|m| lower.contains(m))
}

fn is_read_action(action: &str) -> bool {
    action == "read_file"
        || action
            .rsplit_once('.')
            .is_some_and(|(_, bare)| bare == "read_file")
}

fn collect_transcript_evidence_blob(transcript: &[Value]) -> (BTreeSet<String>, String, usize) {
    let mut touched_basenames = BTreeSet::new();
    let mut combined = String::new();
    let mut read_count = 0usize;
    let nl = char::from(10u8);

    for step in transcript {
        if step.get("error").is_some() {
            continue;
        }
        let action = step.get("action").and_then(Value::as_str).unwrap_or("");
        if is_read_action(action) {
            if let Some(result) = step.get("result") {
                if let Some(content) = result
                    .get("content")
                    .or_else(|| result.get("text"))
                    .and_then(Value::as_str)
                {
                    if !content.trim().is_empty() {
                        read_count += 1;
                        combined.push_str(content);
                        combined.push(nl);
                    }
                }
                if let Some(p) = result.get("path").and_then(Value::as_str) {
                    if !p.is_empty() {
                        touched_basenames.insert(basename_lower(p));
                    }
                }
            }
        }
        if step.get("state").and_then(Value::as_str) != Some(AgentState::Executing.as_str()) {
            continue;
        }
        if matches!(action, "" | "final" | "parse_error") {
            continue;
        }
        if let Some(params) = step.get("params").or_else(|| step.get("args")) {
            for key in ["path", "file_path", "filename", "target"] {
                if let Some(p) = params.get(key).and_then(Value::as_str) {
                    if step.get("result").is_some_and(Value::is_object) {
                        touched_basenames.insert(basename_lower(p));
                    }
                }
            }
        }
        if let Some(result) = step.get("result") {
            if let Ok(serialized) = serde_json::to_string(result) {
                for (f, _) in extract_named_files(&serialized) {
                    touched_basenames.insert(basename_lower(&f));
                }
                combined.push_str(&serialized);
                combined.push(nl);
            }
        }
    }
    (touched_basenames, combined, read_count)
}

pub fn answer_has_ungrounded_claims(
    said: &str,
    request: &str,
    transcript: &[Value],
) -> Option<String> {
    if said.trim().is_empty() {
        return None;
    }
    let (touched_files, evidence_blob, read_count) = collect_transcript_evidence_blob(transcript);
    let evidence_lower = evidence_blob.to_ascii_lowercase();
    let req_files = extract_named_files(request);

    if !req_files.is_empty() && request_asks_file_inspection(request) {
        for (req_file, _) in &req_files {
            let base = basename_lower(req_file);
            if !base.is_empty() && !touched_files.contains(&base) && !evidence_lower.contains(&base)
            {
                return Some(format!("요청 파일 미열람: {req_file}"));
            }
        }
    }

    let req_basenames: BTreeSet<String> =
        req_files.iter().map(|(f, _)| basename_lower(f)).collect();
    for (cited_file, cited_line) in extract_named_files(said) {
        let Some(line_no) = cited_line else {
            continue;
        };
        let base = basename_lower(&cited_file);
        if !touched_files.contains(&base) && !evidence_lower.contains(&base) {
            return Some(format!("미열람 파일 라인 인용: {cited_file}:{line_no}"));
        }
    }

    if read_count > 0 && !req_basenames.is_empty() && request_asks_file_inspection(request) {
        let req_lower = request.to_ascii_lowercase();
        const SAFE_GENERIC_TOKENS: &[&str] = &[
            "true", "false", "null", "none", "json", "yaml", "toml", "utf8", "http", "https",
            "pass", "fail", "done", "todo", "main", "test",
        ];
        for caps in backtick_ident_re().captures_iter(said).flatten() {
            let Some(ident) = caps.get(1).map(|m| m.as_str()) else {
                continue;
            };
            let looks_like_code_symbol = ident.contains('_')
                || (ident.chars().any(|c| c.is_ascii_lowercase())
                    && ident.chars().any(|c| c.is_ascii_uppercase()))
                || (ident.len() >= 5
                    && ident
                        .chars()
                        .all(|c| c.is_ascii_uppercase() || c.is_ascii_digit() || c == '_'));
            if !looks_like_code_symbol {
                continue;
            }
            let lower = ident.to_ascii_lowercase();
            if SAFE_GENERIC_TOKENS.contains(&lower.as_str()) {
                continue;
            }
            if !req_lower.contains(&lower) && !evidence_lower.contains(&lower) {
                return Some(format!("열람 결과에 없는 식별자 주장: `{ident}`"));
            }
        }
    }

    None
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn flags_unread_requested_file_on_inspection_question() {
        let transcript = vec![json!({
            "state": "EXECUTING",
            "action": "mcp.list_dir",
            "params": {"path": "."},
            "result": {"items": ["README.md"]}
        })];
        let issue = answer_has_ungrounded_claims(
            "config.toml에는 포트가 8080으로 설정되어 있습니다.",
            "config.toml 파일을 읽고 포트 설정을 알려줘",
            &transcript,
        );
        assert_eq!(issue.as_deref(), Some("요청 파일 미열람: config.toml"));
    }

    #[test]
    fn allows_answer_when_requested_file_was_read_and_symbol_exists() {
        let transcript = vec![json!({
            "state": "EXECUTING",
            "action": "read_file",
            "params": {"path": "src/config.toml"},
            "result": {"path": "src/config.toml", "content": "default_port = 4825; max_workers = 4"}
        })];
        let issue = answer_has_ungrounded_claims(
            "`default_port` 값은 4825입니다.",
            "config.toml 파일을 읽고 포트 설정을 알려줘",
            &transcript,
        );
        assert_eq!(issue, None);
    }

    #[test]
    fn flags_hallucinated_backtick_symbol_not_in_read_file() {
        let transcript = vec![json!({
            "state": "EXECUTING",
            "action": "read_file",
            "params": {"path": "src/config.toml"},
            "result": {"path": "src/config.toml", "content": "default_port = 4825"}
        })];
        let issue = answer_has_ungrounded_claims(
            "`secret_master_token` 설정이 활성화되어 있습니다.",
            "config.toml 파일을 읽고 설정을 설명해줘",
            &transcript,
        );
        assert_eq!(
            issue.as_deref(),
            Some("열람 결과에 없는 식별자 주장: `secret_master_token`")
        );
    }

    #[test]
    fn flags_ungrounded_file_line_citation() {
        let transcript = vec![json!({
            "state": "EXECUTING",
            "action": "read_file",
            "params": {"path": "README.md"},
            "result": {"path": "README.md", "content": "# Hello"}
        })];
        let issue = answer_has_ungrounded_claims(
            "버그는 `src/engine.rs:142`에서 발생합니다.",
            "현재 상태를 확인해줘",
            &transcript,
        );
        assert_eq!(
            issue.as_deref(),
            Some("미열람 파일 라인 인용: src/engine.rs:142")
        );
    }

    #[test]
    fn does_not_flag_file_creation_requests() {
        let transcript = vec![json!({
            "state": "EXECUTING",
            "action": "write_file",
            "params": {"path": "hello.md"},
            "result": {"path": "hello.md", "bytes": 12}
        })];
        let issue = answer_has_ungrounded_claims(
            "hello.md 파일을 생성했습니다.",
            "hello.md 파일을 만들어줘",
            &transcript,
        );
        assert_eq!(issue, None);
    }
}
