use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::{Mutex, OnceLock};

use fancy_regex::Regex;
use serde_json::{json, Value};

const MAX_PERSISTED_SESSIONS: usize = 256;
pub const SESSIONS_FILE_NAME: &str = "chat_document_sessions.json";

pub const DOCUMENT_SYSTEM_PROMPT: &str = "당신은 사용자의 개인 AI 지식 어시스턴트 Lattice AI입니다.
사용자의 기존 지식 기반을 활용하여 고품질 문서를 생성합니다.

## 지침
1. 아래 제공된 지식 그래프 컨텍스트를 최대한 활용하세요.
2. 이전 문서의 스타일과 톤을 유지하면서 최신적이고 전문적인 문서를 작성하세요.
3. 출처는 자연스럽게 본문이나 각주에 포함하세요.
4. 사용자의 언어(한국어/영어)에 맞춰 작성하세요.
5. 구조화된 포맷(제목, 소제목, 목록 등)을 사용하세요.

## 사용자의 지식 기반

{graph_context}";

pub const DOCUMENT_FOLLOWUP_PROMPT: &str =
    "당신은 사용자의 개인 AI 지식 어시스턴트 Lattice AI입니다.
이전에 생성한 문서를 사용자의 요청에 따라 수정/보완합니다.

## 이전 생성 컨텍스트

{graph_context}

## 이전 문서
{previous_document}

위 문서를 사용자의 요청에 따라 수정하세요. 기존 스타일과 톤을 유지하세요.";

pub const NO_KNOWLEDGE_BASE: &str =
    "(사용 가능한 지식 기반이 없습니다. 일반 지식을 활용하여 작성합니다.)";

fn strong_patterns() -> &'static Vec<Regex> {
    static SET: OnceLock<Vec<Regex>> = OnceLock::new();
    SET.get_or_init(|| {
        [
            r"(?i)(작성해|만들어\s*줘|써\s*줘|생성해|write\s+(?:a|me|the)|create\s+(?:a|me|the)|draft\s+(?:a|me|the))",
            r"(?i)(보고서|계획서|기획서|제안서|전략서|매뉴얼).*(작성|만들|생성|써)",
            r"(?i)(작성|만들|생성|써).*(보고서|계획서|기획서|제안서|전략서|매뉴얼)",
        ]
        .iter()
        .map(|pattern| Regex::new(pattern).expect("ported pattern must compile"))
        .collect()
    })
}

fn intent_patterns() -> &'static Vec<Regex> {
    static SET: OnceLock<Vec<Regex>> = OnceLock::new();
    SET.get_or_init(|| {
        [
            r"(?i)(보고서|계획서|기획서|제안서|문서|리포트|요약서|분석서|전략서|매뉴얼|가이드)",
            r"(?i)(작성|만들어|생성|써|줘|write|create|generate|draft|compose|prepare)",
            r"(?i)(report|proposal|plan|document|summary|analysis|strategy|guide|manual|brief)",
        ]
        .iter()
        .map(|pattern| Regex::new(pattern).expect("ported pattern must compile"))
        .collect()
    })
}

pub fn detect_document_intent(message: &str) -> bool {
    if message.chars().count() < 5 {
        return false;
    }
    if strong_patterns()
        .iter()
        .any(|pattern| pattern.is_match(message).unwrap_or(false))
    {
        return true;
    }
    intent_patterns()
        .iter()
        .filter(|pattern| pattern.is_match(message).unwrap_or(false))
        .count()
        >= 2
}

pub fn document_system_prompt(graph_context: &str) -> String {
    let filled = if graph_context.is_empty() {
        NO_KNOWLEDGE_BASE
    } else {
        graph_context
    };
    DOCUMENT_SYSTEM_PROMPT.replace("{graph_context}", filled)
}

pub fn followup_system_prompt(graph_context: &str, previous_document: &str) -> String {
    let context = if graph_context.is_empty() {
        "(없음)"
    } else {
        graph_context
    };
    let previous = if previous_document.is_empty() {
        "(없음)"
    } else {
        previous_document
    };
    DOCUMENT_FOLLOWUP_PROMPT
        .replace("{graph_context}", context)
        .replace("{previous_document}", previous)
}

type SessionKey = (String, String, String);
type SessionValue = (String, String);

#[derive(Debug, Default)]
pub struct DocumentSessions {
    sessions: Mutex<HashMap<SessionKey, SessionValue>>,
    persist_path: Option<PathBuf>,
}

impl DocumentSessions {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn with_data_dir(data_dir: &Path) -> Self {
        if data_dir.as_os_str().is_empty() {
            return Self::new();
        }
        let path = data_dir.join(SESSIONS_FILE_NAME);
        let loaded = Self::load_from_disk(&path);
        Self {
            sessions: Mutex::new(loaded),
            persist_path: Some(path),
        }
    }

    fn load_from_disk(path: &Path) -> HashMap<SessionKey, SessionValue> {
        let Ok(raw) = std::fs::read_to_string(path) else {
            return HashMap::new();
        };
        let Ok(parsed) = serde_json::from_str::<Value>(&raw) else {
            return HashMap::new();
        };
        let Some(entries) = parsed.get("entries").and_then(Value::as_array) else {
            return HashMap::new();
        };
        let mut map = HashMap::new();
        for entry in entries.iter().take(MAX_PERSISTED_SESSIONS) {
            let Some(user) = entry.get("user").and_then(Value::as_str) else {
                continue;
            };
            let Some(workspace) = entry.get("workspace").and_then(Value::as_str) else {
                continue;
            };
            let Some(conversation) = entry.get("conversation").and_then(Value::as_str) else {
                continue;
            };
            let context = entry
                .get("context")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_string();
            let document = entry
                .get("document")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_string();
            map.insert(
                (
                    user.to_string(),
                    workspace.to_string(),
                    conversation.to_string(),
                ),
                (context, document),
            );
        }
        map
    }

    fn persist_snapshot_to_path(path: &Path, map: &HashMap<SessionKey, SessionValue>) {
        let mut keys: Vec<&SessionKey> = map.keys().collect();
        keys.sort();
        if keys.len() > MAX_PERSISTED_SESSIONS {
            keys.truncate(MAX_PERSISTED_SESSIONS);
        }
        let entries: Vec<Value> = keys
            .into_iter()
            .filter_map(|k| {
                let (ctx, doc) = map.get(k)?;
                Some(json!({
                    "user": k.0,
                    "workspace": k.1,
                    "conversation": k.2,
                    "context": ctx,
                    "document": doc,
                }))
            })
            .collect();
        let body = json!({
            "version": 1,
            "entries": entries,
        });
        match serde_json::to_string(&body) {
            Ok(serialized) => {
                if let Some(parent) = path.parent() {
                    if let Err(err) = std::fs::create_dir_all(parent) {
                        eprintln!(
                            "lattice-chat: failed to create document session directory {}: {err}",
                            parent.display()
                        );
                        return;
                    }
                }
                lattice_auth::atomic::write_text(path, &serialized);
            }
            Err(err) => {
                eprintln!("lattice-chat: failed to serialize document sessions: {err}");
            }
        }
    }

    fn key(
        user_email: Option<&str>,
        workspace_id: Option<&str>,
        conversation_id: Option<&str>,
    ) -> (String, String, String) {
        (
            user_email.unwrap_or("").to_string(),
            workspace_id.unwrap_or("").to_string(),
            conversation_id
                .filter(|id| !id.is_empty())
                .unwrap_or("default")
                .to_string(),
        )
    }

    pub fn system_prompt(
        &self,
        user_email: Option<&str>,
        workspace_id: Option<&str>,
        conversation_id: Option<&str>,
        graph_context: &str,
    ) -> String {
        let key = Self::key(user_email, workspace_id, conversation_id);
        let guard = self
            .sessions
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        match guard.get(&key) {
            Some((last_context, last_document)) => {
                let context = if graph_context.is_empty() {
                    last_context.as_str()
                } else {
                    graph_context
                };
                followup_system_prompt(context, last_document)
            }
            None => document_system_prompt(graph_context),
        }
    }

    pub fn update(
        &self,
        user_email: Option<&str>,
        workspace_id: Option<&str>,
        conversation_id: Option<&str>,
        graph_context: &str,
        document: &str,
    ) {
        let key = Self::key(user_email, workspace_id, conversation_id);
        let snapshot = {
            let mut guard = self
                .sessions
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner());
            if guard.len() >= MAX_PERSISTED_SESSIONS && !guard.contains_key(&key) {
                if let Some(evict_key) = guard.keys().next().cloned() {
                    guard.remove(&evict_key);
                }
            }
            guard.insert(key, (graph_context.to_string(), document.to_string()));
            self.persist_path
                .as_ref()
                .map(|p| (p.clone(), guard.clone()))
        };
        if let Some((path, map)) = snapshot {
            if let Ok(handle) = tokio::runtime::Handle::try_current() {
                if matches!(
                    handle.runtime_flavor(),
                    tokio::runtime::RuntimeFlavor::MultiThread
                ) {
                    handle.spawn_blocking(move || Self::persist_snapshot_to_path(&path, &map));
                    return;
                }
            }
            Self::persist_snapshot_to_path(&path, &map);
        }
    }

    pub fn len(&self) -> usize {
        self.sessions
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .len()
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }
}

#[derive(Debug, Clone, Default)]
pub struct DocumentPreparation {
    pub is_document: bool,
    pub context: String,
    pub retrieval: Option<Value>,
}

impl DocumentPreparation {
    pub fn graph_markdown(&self) -> String {
        crate::pyvalue::field(
            self.retrieval.as_ref().unwrap_or(&Value::Null),
            "context_markdown",
        )
    }

    pub fn sources(&self) -> Vec<Value> {
        self.retrieval
            .as_ref()
            .and_then(|retrieval| retrieval.get("sources"))
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default()
    }

    pub fn context_quality(&self) -> Option<&Value> {
        self.retrieval
            .as_ref()
            .and_then(|retrieval| retrieval.get("context_quality"))
            .filter(|quality| crate::pyvalue::truthy(quality))
    }

    pub fn assembly_trace(&self) -> Option<&Value> {
        self.retrieval
            .as_ref()
            .and_then(|retrieval| retrieval.get("trace"))
            .filter(|trace| crate::pyvalue::truthy(trace))
    }
}

pub fn prepare(
    conn: Option<&rusqlite::Connection>,
    message: &str,
    context: &str,
    workspace_id: Option<&str>,
    now_secs: f64,
) -> DocumentPreparation {
    let is_document = detect_document_intent(message);
    let mut prepared = DocumentPreparation {
        is_document,
        context: context.to_string(),
        retrieval: None,
    };
    let Some(conn) = conn.filter(|_| is_document) else {
        return prepared;
    };
    let request = lattice_retrieval::docgen_context::DocumentContextRequest {
        query: message.to_string(),
        max_results: 10,
        max_hops: 2,
        scope: lattice_retrieval::service::Scope {
            allowed_workspaces: workspace_id
                .filter(|id| !id.is_empty())
                .map(|id| [id.to_string()].into_iter().collect()),
            include_legacy_global: false,
        },
        now_secs,
        ..Default::default()
    };
    let Ok(retrieval) = lattice_retrieval::retrieve_context_for_generation(conn, &request) else {
        return prepared;
    };
    let graph_context = crate::pyvalue::field(&retrieval, "context_markdown");
    if !graph_context.is_empty() {
        prepared
            .context
            .push_str("\n\n[KNOWLEDGE GRAPH — Document Generation Context]\n");
        prepared.context.push_str(&graph_context);
    }
    prepared.retrieval = Some(retrieval);
    prepared
}

pub fn screenshot_context(image_data: &str) -> String {
    if image_data.is_empty() {
        return String::new();
    }
    let mut lines = vec!["[SCREENSHOT INGESTION]".to_string()];
    match decode_dimensions(image_data) {
        Some((width, height)) => {
            lines.push(format!("- image_size: {width}x{height}"));
            lines.push("- image_mode: RGB".to_string());
        }
        None => {
            lines.push("- image_decode_error: unreadable image data".to_string());
            return lines.join("\n");
        }
    }
    lines
        .push("- ocr: unavailable; install `tesseract` to enable OCR text extraction.".to_string());
    lines.join("\n")
}

fn decode_dimensions(image_data: &str) -> Option<(u32, u32)> {
    let payload = image_data
        .split_once("base64,")
        .map(|(_, rest)| rest)
        .unwrap_or(image_data);
    let bytes = base64_decode(payload.trim())?;
    if bytes.len() >= 24 && bytes.starts_with(&[0x89, b'P', b'N', b'G']) {
        let width = u32::from_be_bytes([bytes[16], bytes[17], bytes[18], bytes[19]]);
        let height = u32::from_be_bytes([bytes[20], bytes[21], bytes[22], bytes[23]]);
        return Some((width, height));
    }
    if bytes.len() >= 10 && (bytes.starts_with(b"GIF87a") || bytes.starts_with(b"GIF89a")) {
        let width = u16::from_le_bytes([bytes[6], bytes[7]]);
        let height = u16::from_le_bytes([bytes[8], bytes[9]]);
        return Some((u32::from(width), u32::from(height)));
    }
    if bytes.len() > 4 && bytes.starts_with(&[0xFF, 0xD8]) {
        let mut index = 2usize;
        while index + 9 < bytes.len() {
            if bytes[index] != 0xFF {
                index += 1;
                continue;
            }
            let marker = bytes[index + 1];
            if (0xC0..=0xCF).contains(&marker) && !matches!(marker, 0xC4 | 0xC8 | 0xCC) {
                let height = u16::from_be_bytes([bytes[index + 5], bytes[index + 6]]);
                let width = u16::from_be_bytes([bytes[index + 7], bytes[index + 8]]);
                return Some((u32::from(width), u32::from(height)));
            }
            let length = u16::from_be_bytes([bytes[index + 2], bytes[index + 3]]) as usize;
            index += 2 + length.max(2);
        }
    }
    None
}

fn base64_decode(text: &str) -> Option<Vec<u8>> {
    let value_of = |byte: u8| -> Option<u32> {
        Some(match byte {
            b'A'..=b'Z' => u32::from(byte - b'A'),
            b'a'..=b'z' => u32::from(byte - b'a') + 26,
            b'0'..=b'9' => u32::from(byte - b'0') + 52,
            b'+' => 62,
            b'/' => 63,
            _ => return None,
        })
    };
    let mut out: Vec<u8> = Vec::new();
    let mut accumulator: u32 = 0;
    let mut bits = 0u32;
    for byte in text.bytes() {
        if byte == b'=' || byte.is_ascii_whitespace() {
            continue;
        }
        let value = value_of(byte)?;
        accumulator = (accumulator << 6) | value;
        bits += 6;
        if bits >= 8 {
            bits -= 8;
            out.push(((accumulator >> bits) & 0xFF) as u8);
        }
    }
    Some(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn short_messages_never_read_as_document_requests() {
        assert!(!detect_document_intent(""));
        assert!(!detect_document_intent("보고서"));
        assert!(detect_document_intent("보고서 작성해줘"));
    }

    #[test]
    fn one_strong_signal_or_two_weak_ones() {
        assert!(detect_document_intent("write a summary of Q3"));
        assert!(detect_document_intent("전략서를 만들어 주세요"));
        assert!(detect_document_intent("리포트를 정리해 줘"));
        assert!(!detect_document_intent("hello there friend"));
    }

    #[test]
    fn the_prompts_substitute_their_placeholders() {
        let prompt = document_system_prompt("");
        assert!(prompt.contains(NO_KNOWLEDGE_BASE));
        assert!(!prompt.contains("{graph_context}"));
        assert!(document_system_prompt("KG").ends_with("KG"));

        let followup = followup_system_prompt("", "");
        assert_eq!(followup.matches("(없음)").count(), 2);
        let followup = followup_system_prompt("ctx", "doc");
        assert!(followup.contains("ctx") && followup.contains("doc"));
    }

    #[test]
    fn a_session_upgrades_the_prompt_after_the_first_document() {
        let sessions = DocumentSessions::new();
        assert!(sessions.is_empty());
        let first = sessions.system_prompt(Some("a@x"), None, Some("c1"), "KG");
        assert!(first.contains("## 사용자의 지식 기반"));
        sessions.update(Some("a@x"), None, Some("c1"), "KG", "the document");
        assert_eq!(sessions.len(), 1);
        let second = sessions.system_prompt(Some("a@x"), None, Some("c1"), "KG");
        assert!(second.contains("## 이전 문서"));
        assert!(second.contains("the document"));
        let third = sessions.system_prompt(Some("a@x"), None, Some("c1"), "");
        assert!(third.contains("KG"));
        let other = sessions.system_prompt(Some("a@x"), None, Some("c2"), "KG");
        assert!(other.contains("## 사용자의 지식 기반"));
        sessions.update(None, None, None, "K", "D");
        assert!(sessions
            .system_prompt(None, None, Some(""), "K")
            .contains("D"));
    }

    #[test]
    fn document_sessions_persist_and_reload_across_restarts() {
        let dir = tempfile::tempdir().unwrap();
        {
            let first = DocumentSessions::with_data_dir(dir.path());
            first.update(
                Some("alice@lattice.local"),
                Some("ws-alpha"),
                Some("conv-42"),
                "## Graph Context A",
                "# Draft v1\nInitial quarterly plan",
            );
            assert_eq!(first.len(), 1);
        }
        let reloaded = DocumentSessions::with_data_dir(dir.path());
        assert_eq!(reloaded.len(), 1);
        let prompt = reloaded.system_prompt(
            Some("alice@lattice.local"),
            Some("ws-alpha"),
            Some("conv-42"),
            "",
        );
        assert!(prompt.contains("## Graph Context A"));
        assert!(prompt.contains("# Draft v1\nInitial quarterly plan"));
    }

    #[test]
    fn preparation_without_a_graph_carries_the_context_through() {
        let prepared = prepare(None, "보고서 작성해줘", "base", None, 0.0);
        assert!(prepared.is_document);
        assert_eq!(prepared.context, "base");
        assert!(prepared.retrieval.is_none());
        assert_eq!(prepared.graph_markdown(), "");
        assert!(prepared.sources().is_empty());
        assert!(prepared.context_quality().is_none());
        assert!(prepared.assembly_trace().is_none());
        assert!(format!("{prepared:?}").contains("is_document"));
    }

    #[test]
    fn preparation_appends_the_graph_block_when_there_is_one() {
        let dir = tempfile::tempdir().unwrap();
        let conn = rusqlite::Connection::open(dir.path().join("g.sqlite")).unwrap();
        conn.execute_batch(
            "CREATE TABLE nodes(id TEXT PRIMARY KEY, type TEXT, title TEXT, summary TEXT,
               metadata_json TEXT, created_at TEXT, updated_at TEXT, user_email TEXT,
               workspace_id TEXT, organization_id TEXT);
             CREATE TABLE edges(id INTEGER PRIMARY KEY AUTOINCREMENT, from_node TEXT,
               to_node TEXT, type TEXT, weight REAL, metadata_json TEXT, created_at TEXT,
               user_email TEXT, workspace_id TEXT, organization_id TEXT);
             INSERT INTO nodes VALUES('n1','Document','분기 보고서','매출 정리','{}',
               '2026-01-01','2026-01-01',NULL,NULL,NULL);",
        )
        .unwrap();
        let prepared = prepare(Some(&conn), "분기 보고서 작성해줘", "base", None, 0.0);
        assert!(prepared.is_document);
        assert!(prepared.retrieval.is_some());
        assert!(prepared.context.starts_with("base"));
        let plain = prepare(Some(&conn), "hi", "base", None, 0.0);
        assert!(!plain.is_document);
        assert!(plain.retrieval.is_none());
    }

    #[test]
    fn a_screenshot_block_reports_the_size_and_the_missing_ocr() {
        let png = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=";
        let block = screenshot_context(&format!("data:image/png;base64,{png}"));
        assert!(block.starts_with("[SCREENSHOT INGESTION]"));
        assert!(block.contains("- image_size: 1x1"));
        assert!(block.contains("- image_mode: RGB"));
        assert!(block.contains("- ocr: unavailable"));
        assert_eq!(screenshot_context(""), "");
        assert!(screenshot_context("!!not base64!!").contains("image_decode_error"));
    }

    #[test]
    fn dimensions_come_off_gif_and_jpeg_headers_too() {
        let gif: Vec<u8> = b"GIF89a\x0a\x00\x14\x00rest".to_vec();
        assert_eq!(decode_dimensions(&base64_encode(&gif)), Some((10, 20)));
        let mut jpeg: Vec<u8> = vec![0xFF, 0xD8, 0xFF, 0xE0, 0x00, 0x04, 0x00, 0x00];
        jpeg.extend_from_slice(&[0xFF, 0xC0, 0x00, 0x11, 0x08, 0x00, 0x20, 0x00, 0x30, 0x03]);
        assert_eq!(decode_dimensions(&base64_encode(&jpeg)), Some((48, 32)));
        assert_eq!(decode_dimensions(&base64_encode(b"nope")), None);
    }

    fn base64_encode(bytes: &[u8]) -> String {
        const ALPHABET: &[u8; 64] =
            b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
        let mut out = String::new();
        for chunk in bytes.chunks(3) {
            let mut buffer = [0u8; 3];
            buffer[..chunk.len()].copy_from_slice(chunk);
            let value =
                (u32::from(buffer[0]) << 16) | (u32::from(buffer[1]) << 8) | u32::from(buffer[2]);
            for index in 0..4 {
                if index <= chunk.len() {
                    out.push(ALPHABET[((value >> (18 - index * 6)) & 0x3F) as usize] as char);
                } else {
                    out.push('=');
                }
            }
        }
        out
    }
}
