use super::{action_succeeded, named_in};
use crate::kernel::state::AgentRunContext;

pub fn url_named_in_request(request: &str) -> Option<String> {
    let is_url_char = |c: char| {
        c.is_ascii_alphanumeric()
            || matches!(
                c,
                ':' | '/' | '.' | '-' | '_' | '?' | '&' | '=' | '%' | '#' | '+' | '~' | '@'
            )
    };
    for scheme in ["https://", "http://"] {
        if let Some(start) = request.find(scheme) {
            let tail = &request[start..];
            let raw: String = tail.chars().take_while(|c| is_url_char(*c)).collect();
            let cleaned = raw
                .trim_end_matches(['.', ',', ';', ':', ')', ']', '}'])
                .to_string();
            if cleaned.len() > scheme.len() {
                return Some(cleaned);
            }
        }
    }
    for token in request.split_whitespace() {
        let ascii_prefix: String = token
            .trim_start_matches(['"', '\'', '`', '(', '[', '<'])
            .chars()
            .take_while(|c| is_url_char(*c))
            .collect();
        let candidate = ascii_prefix.trim_end_matches(['.', ',', ';', ')']);
        if candidate.starts_with("localhost:")
            || candidate.starts_with("127.0.0.1:")
            || candidate.starts_with("www.")
        {
            return Some(candidate.to_string());
        }
    }
    None
}

pub fn request_wants_web(request: &str) -> bool {
    let trimmed = request.trim();
    if trimmed.is_empty() {
        return false;
    }
    if named_in(trimmed, "web_search")
        || named_in(trimmed, "web_fetch")
        || url_named_in_request(trimmed).is_some()
    {
        return true;
    }
    let lower = trimmed.to_lowercase();
    const CUES: &[&str] = &[
        "web search",
        "search the web",
        "search online",
        "on the web",
        "from the web",
        "fetch url",
        "fetch page",
        "web page",
        "webpage",
        "website",
        "internet",
        "duckduckgo",
        "웹 검색",
        "웹검색",
        "웹에서",
        "웹사이트",
        "웹페이지",
        "웹 페이지",
        "인터넷에서",
        "인터넷 검색",
        "온라인에서",
        "온라인 검색",
        "구글 검색",
        "사이트 접속",
        "페이지 가져",
    ];
    CUES.iter().any(|cue| lower.contains(cue))
}

pub fn request_wants_computer_use(request: &str) -> bool {
    let trimmed = request.trim();
    if trimmed.is_empty() {
        return false;
    }
    const TOOLS: &[&str] = &[
        "computer_ui_inspect",
        "computer_screenshot",
        "computer_status",
        "computer_click",
        "computer_type",
        "computer_key",
        "computer_scroll",
        "computer_move",
        "computer_drag",
        "computer_open_app",
        "computer_open_url",
    ];
    if TOOLS.iter().any(|tool| named_in(trimmed, tool)) {
        return true;
    }
    let lower = trimmed.to_lowercase();
    const CUES: &[&str] = &[
        "screenshot",
        "screen shot",
        "ui inspect",
        "inspect ui",
        "click button",
        "click on",
        "click the",
        "double click",
        "right click",
        "scroll down",
        "scroll up",
        "mouse",
        "cursor",
        "open app",
        "launch app",
        "press key",
        "keyboard",
        "on the screen",
        "스크린샷",
        "화면 캡처",
        "화면캡처",
        "화면을 캡처",
        "화면 확인",
        "화면에서",
        "클릭해",
        "클릭하",
        "버튼을 클릭",
        "버튼 클릭",
        "더블클릭",
        "우클릭",
        "마우스",
        "스크롤",
        "앱을 열",
        "앱 열기",
        "앱 실행",
        "키보드",
        "단축키",
        "창에서",
    ];
    CUES.iter().any(|cue| lower.contains(cue))
}

pub fn request_wants_knowledge(request: &str) -> bool {
    let trimmed = request.trim();
    if trimmed.is_empty() {
        return false;
    }
    const TOOLS: &[&str] = &[
        "knowledge_search",
        "knowledge_graph_search",
        "knowledge_graph_context",
        "knowledge_tree",
        "knowledge_save",
        "obsidian_search",
        "obsidian_tree",
        "obsidian_save",
    ];
    if TOOLS.iter().any(|tool| named_in(trimmed, tool)) {
        return true;
    }
    let lower = trimmed.to_lowercase();
    const CUES: &[&str] = &[
        "knowledge graph",
        "knowledge base",
        "knowledge garden",
        "obsidian",
        "vault",
        "second brain",
        "graphrag",
        "graph search",
        "graph context",
        "entity graph",
        "bitemporal",
        "superseded",
        "지식 그래프",
        "지식그래프",
        "지식 베이스",
        "지식베이스",
        "지식 가든",
        "옵시디언",
        "볼트",
        "브레인에서",
        "브레인 검색",
        "노트 검색",
        "지식 검색",
        "엔티티",
        "그래프 검색",
    ];
    CUES.iter().any(|cue| lower.contains(cue))
}

pub(in crate::kernel::agentloop) fn has_ui_observation(ctx: &AgentRunContext) -> bool {
    action_succeeded(ctx, "computer_ui_inspect")
        || action_succeeded(ctx, "computer_screenshot")
        || action_succeeded(ctx, "mcp.computer_ui_inspect")
        || action_succeeded(ctx, "mcp.computer_screenshot")
}

pub(in crate::kernel::agentloop) fn domain_priority_tools(
    request: &str,
    ui_observed: bool,
) -> Vec<&'static str> {
    let mut out = Vec::new();
    if request_wants_computer_use(request) {
        if !ui_observed {
            out.extend_from_slice(&[
                "computer_ui_inspect",
                "computer_screenshot",
                "mcp.computer_ui_inspect",
                "mcp.computer_screenshot",
                "computer_click",
                "computer_type",
                "computer_key",
                "computer_scroll",
                "computer_move",
                "computer_drag",
                "computer_open_app",
                "computer_open_url",
                "computer_status",
            ]);
        } else {
            out.extend_from_slice(&[
                "computer_click",
                "computer_type",
                "computer_key",
                "computer_scroll",
                "computer_move",
                "computer_drag",
                "computer_open_app",
                "computer_open_url",
                "computer_ui_inspect",
                "computer_screenshot",
                "computer_status",
            ]);
        }
    }
    if request_wants_web(request) {
        if url_named_in_request(request).is_some() {
            out.extend_from_slice(&["web_fetch", "web_search", "mcp.web_fetch", "mcp.web_search"]);
        } else {
            out.extend_from_slice(&["web_search", "web_fetch", "mcp.web_search", "mcp.web_fetch"]);
        }
    }
    if request_wants_knowledge(request) {
        out.extend_from_slice(&[
            "knowledge_search",
            "knowledge_graph_context",
            "knowledge_graph_search",
            "obsidian_search",
            "knowledge_tree",
            "obsidian_tree",
            "knowledge_save",
            "obsidian_save",
            "mcp.knowledge_search",
            "mcp.knowledge_graph_context",
            "mcp.knowledge_graph_search",
            "mcp.obsidian_search",
        ]);
    }
    out
}
