//! Port of `latticeai/core/context_builder.py` — the document-generation
//! context, assembled and budgeted.
//!
//! Same Brain, same quality contract as chat: an explicit token budget, the
//! honest `context_quality` signal, and a trace in the assembler's shape. The
//! *rendering* is different on purpose (a document prompt wants structured
//! sections, a chat prompt wants terse lines); the contract is one.
//!
//! Four details decide whether this is a port or a rewrite:
//!
//! * **The profile is charged to the same budget**, including the blank line
//!   that joins it to the knowledge. Injecting who the user is can therefore
//!   never push the assembled context over the ceiling the caller asked for.
//! * **The trim backs off to a section boundary** — but only when the boundary
//!   is past a third of the cut, so a budget too small for even one section
//!   keeps a truncated head rather than collapsing to nothing.
//! * **Two "first non-empty" chains that look identical are not.** The rendered
//!   `출처:` line consults `metadata.source`; the extracted source list does
//!   not. Unifying them would change which sources a document cites.
//! * **The empty answer reports the default budget**, not the caller's — the
//!   early return never saw one.

use std::collections::HashMap;

use lattice_core::pytext::{clean_text, rstrip, strip, truncate_chars};
use lattice_core::CoreError;
use rusqlite::Connection;
use serde_json::{Map, Value};

use crate::context::approx_tokens;
use crate::docgen::{
    context_for_query, first_truthy, multi_hop_context, py_text, search_for_document_generation,
};
use crate::self_model::{summary_for_prompt, DEFAULT_SUMMARY_TOKENS};
use crate::service::Scope;
use crate::shape::{context_quality_signal, multimodal_signal, truthy};

/// `DEFAULT_DOCUMENT_CONTEXT_BUDGET` — mirrors the chat assembler's default so
/// neither path can silently outgrow the other on the same Brain.
pub const DEFAULT_DOCUMENT_CONTEXT_BUDGET: i64 = 2000;

const SELF_MODEL_SECTION_TITLE: &str = "사용자 프로필";
const SELF_MODEL_TRACE_SOURCE: &str = "self_model";
const SELF_MODEL_SECTION_HEADER: &str = "### 🙋 사용자 프로필\n\n";

/// One document-generation context request.
#[derive(Debug, Clone)]
pub struct DocumentContextRequest {
    pub query: String,
    /// How many hybrid document matches to seed with.
    pub max_results: i64,
    /// Expansion rounds for the traversal around those seeds.
    pub max_hops: i64,
    /// Approximate-token ceiling for the whole assembled block.
    pub budget: i64,
    /// Whether the owner's profile rides along.
    pub include_self_model: bool,
    /// Ceiling for the profile block alone, before the budget halves it.
    pub self_model_tokens: i64,
    pub scope: Scope,
    /// The wall clock the recency term reads.
    pub now_secs: f64,
}

impl Default for DocumentContextRequest {
    fn default() -> Self {
        Self {
            query: String::new(),
            max_results: 10,
            max_hops: 2,
            budget: DEFAULT_DOCUMENT_CONTEXT_BUDGET,
            include_self_model: true,
            self_model_tokens: DEFAULT_SUMMARY_TOKENS,
            scope: Scope::default(),
            now_secs: 0.0,
        }
    }
}

/// One rendered section: a fixed title and icon over the items that qualified.
struct Section {
    title: &'static str,
    icon: &'static str,
    items: Vec<Value>,
}

/// `_clean` — collapse whitespace, strip, cut to 700 characters.
fn clean(value: Option<&Value>) -> String {
    let text = match value {
        Some(value) if truthy(value) => py_text(Some(value)),
        _ => String::new(),
    };
    truncate_chars(&clean_text(&text), 700)
}

/// `str(value or "")` — falsy becomes empty, *not* the string `None`.
fn text_or_empty(value: Option<&Value>) -> String {
    match value {
        Some(value) if truthy(value) => py_text(Some(value)),
        _ => String::new(),
    }
}

/// `item.get(key, "")` — an absent key is empty, a present NULL is `None`.
fn field(item: &Value, key: &str) -> String {
    match item.get(key) {
        None => String::new(),
        present => py_text(present),
    }
}

/// `str.rfind(needle)` over characters: the last start index, or `-1`.
fn rfind_chars(haystack: &[char], needle: &str) -> i64 {
    let needle: Vec<char> = needle.chars().collect();
    let Some(last) = haystack.len().checked_sub(needle.len()) else {
        return -1;
    };
    (0..=last)
        .rev()
        .find(|start| haystack[*start..*start + needle.len()] == needle[..])
        .map(|start| start as i64)
        .unwrap_or(-1)
}

/// `_fit_to_budget` — trim to `budget` approximate tokens at a section boundary.
fn fit_to_budget(context_md: &str, budget: i64) -> (String, bool) {
    if budget <= 0 || approx_tokens(context_md) as i64 <= budget {
        return (context_md.to_string(), false);
    }
    let limit = (budget * 4) as usize;
    let mut head: Vec<char> = context_md.chars().take(limit).collect();
    // Python's `str.rfind` answers in characters and `-1` when absent, and the
    // comparison against `limit // 3` is what keeps a tiny budget from cutting
    // everything away.
    let boundary = rfind_chars(&head, "\n### ");
    if boundary > (limit / 3) as i64 {
        head.truncate(boundary as usize);
    }
    (rstrip(&head.iter().collect::<String>()), true)
}

/// `_self_model_budget` — never more than half the budget.
fn self_model_budget(budget: i64, limit_tokens: i64) -> i64 {
    if budget <= 0 {
        return limit_tokens;
    }
    limit_tokens.min(budget / 2)
}

/// `_self_model_block` — the rendered profile section, or `""`.
fn self_model_block(conn: &Connection, request: &DocumentContextRequest) -> String {
    // The header is charged to the same allowance as the summary, so the
    // *rendered block* honours the ceiling rather than just its text.
    let allowance = self_model_budget(request.budget, request.self_model_tokens)
        - approx_tokens(SELF_MODEL_SECTION_HEADER) as i64;
    if !request.include_self_model || allowance <= 0 {
        return String::new();
    }
    let summary = summary_for_prompt(conn, allowance, request.scope.allowed_workspaces.as_ref());
    if summary.is_empty() {
        return String::new();
    }
    format!("{SELF_MODEL_SECTION_HEADER}{summary}")
}

/// `_with_self_model` — either part may be empty.
fn with_self_model(block: &str, context_md: &str) -> String {
    [block, context_md]
        .iter()
        .filter(|part| !part.is_empty())
        .copied()
        .collect::<Vec<_>>()
        .join("\n\n")
}

/// `_self_model_trace` — one extra trace section, only when a block was injected.
fn self_model_trace(block: &str) -> Vec<Value> {
    if block.is_empty() {
        return Vec::new();
    }
    vec![serde_json::json!({
        "name": SELF_MODEL_SECTION_TITLE,
        "source": SELF_MODEL_TRACE_SOURCE,
        "approx_tokens": approx_tokens(block),
        "provenance": [],
    })]
}

/// `_empty_result` — a query this cannot answer, said plainly.
///
/// The trace reports `DEFAULT_DOCUMENT_CONTEXT_BUDGET` rather than the caller's
/// budget: this return happens before the request is looked at.
fn empty_result(query: &str) -> Value {
    serde_json::json!({
        "query": query,
        "context_markdown": "",
        "sources": [],
        "stats": {"method": "none", "matches": 0},
        "context_quality": context_quality_signal(
            "none", 0, Some("문서 생성 컨텍스트를 조회할 수 없습니다"), None,
        ),
        "trace": {
            "budget_approx_tokens": DEFAULT_DOCUMENT_CONTEXT_BUDGET,
            "used_approx_tokens": 0,
            "sections": [],
        },
    })
}

/// `retrieve_context_for_generation`.
pub fn retrieve_context_for_generation(
    conn: &Connection,
    request: &DocumentContextRequest,
) -> Result<Value, CoreError> {
    let query = request.query.trim().to_string();
    if query.is_empty() {
        return Ok(empty_result(&query));
    }
    let profile = self_model_block(conn, request);
    let knowledge_budget = if profile.is_empty() {
        request.budget
    } else {
        request.budget - approx_tokens(&format!("{profile}\n\n")) as i64
    };

    let results = search_for_document_generation(
        conn,
        &query,
        request.max_results,
        &request.scope,
        request.now_secs,
    )?;
    if results.is_empty() {
        return fallback_result(conn, request, &query, &profile, knowledge_budget);
    }

    let seeds: Vec<String> = results
        .iter()
        .map(|item| item["id"].as_str().unwrap_or_default().to_string())
        .collect();
    let hops = multi_hop_context(conn, &seeds, request.max_hops, &request.scope)?;
    let empty: Vec<Value> = Vec::new();
    let hop_nodes = hops["nodes"].as_array().unwrap_or(&empty);
    let hop_edges = hops["edges"].as_array().unwrap_or(&empty);

    let sections = build_context_sections(&results, hop_nodes, &seeds);
    let context_md = render_markdown(&sections);
    let sources = extract_sources(&results);
    let (context_md, trimmed) = fit_to_budget(&context_md, knowledge_budget);
    let assembled = with_self_model(&profile, &context_md);

    let mut trace_sections = self_model_trace(&profile);
    trace_sections.extend(sections.iter().map(section_trace));
    Ok(serde_json::json!({
        "query": query,
        "context_markdown": assembled,
        "sources": sources,
        "stats": {
            "method": "hybrid",
            "primary_matches": results.len(),
            "graph_nodes": hop_nodes.len(),
            "graph_edges": hop_edges.len(),
            "budget_trimmed": trimmed,
        },
        "context_quality": context_quality_signal(
            "hybrid", results.len() as i64, None, multimodal_signal(&results),
        ),
        "trace": {
            "budget_approx_tokens": request.budget,
            "used_approx_tokens": approx_tokens(&assembled),
            "sections": trace_sections,
        },
    }))
}

/// The lexical fallback: the hybrid document search found nothing, so the signal
/// says `lexical_only` — exactly what chat reports in the same situation, never
/// a quiet downgrade.
fn fallback_result(
    conn: &Connection,
    request: &DocumentContextRequest,
    query: &str,
    profile: &str,
    knowledge_budget: i64,
) -> Result<Value, CoreError> {
    let lexical = context_for_query(conn, query, request.max_results, &request.scope)?;
    let (lexical, trimmed) = fit_to_budget(&lexical, knowledge_budget);
    let found = !lexical.is_empty();
    let assembled = with_self_model(profile, &lexical);
    let mut trace_sections = self_model_trace(profile);
    trace_sections.push(serde_json::json!({
        "name": "Knowledge (fallback)",
        "source": "knowledge",
        "approx_tokens": approx_tokens(&lexical),
        "provenance": [],
    }));
    Ok(serde_json::json!({
        "query": query,
        "context_markdown": assembled,
        "sources": [],
        "stats": {"method": "fallback", "matches": 0, "budget_trimmed": trimmed},
        "context_quality": context_quality_signal(
            if found { "lexical_only" } else { "none" },
            i64::from(found),
            None,
            None,
        ),
        "trace": {
            "budget_approx_tokens": request.budget,
            "used_approx_tokens": approx_tokens(&assembled),
            "sections": trace_sections,
        },
    }))
}

const DOC_TYPES: [&str; 8] = [
    "Document",
    "File",
    "SlideDeck",
    "Spreadsheet",
    "CodeFile",
    "Image",
    "ImageText",
    "Audio",
];

fn is_type(item: &Value, types: &[&str]) -> bool {
    item.get("type")
        .and_then(Value::as_str)
        .map(|node_type| types.contains(&node_type))
        .unwrap_or(false)
}

/// `_build_context_sections` — four sections in one fixed order.
///
/// The traversal's own nodes only ever reach the last one, and only the first
/// eight of them: the graph is context around the answer, not the answer.
fn build_context_sections(
    results: &[Value],
    hop_nodes: &[Value],
    seeds: &[String],
) -> Vec<Section> {
    let mut order: Vec<String> = Vec::new();
    let mut extras: HashMap<String, Value> = HashMap::new();
    for node in hop_nodes {
        let id = node["id"].as_str().unwrap_or_default().to_string();
        if seeds.contains(&id) {
            continue;
        }
        if extras.insert(id.clone(), node.clone()).is_none() {
            order.push(id);
        }
    }
    let pick = |types: &[&str]| -> Vec<Value> {
        results
            .iter()
            .filter(|item| is_type(item, types))
            .cloned()
            .collect()
    };

    let mut sections: Vec<Section> = Vec::new();
    let mut push = |title, icon, items: Vec<Value>| {
        if !items.is_empty() {
            sections.push(Section { title, icon, items });
        }
    };
    push("관련 문서/파일", "📄", pick(&DOC_TYPES));
    push("관련 결정사항/작업", "✅", pick(&["Decision", "Task"]));
    push("관련 대화", "💬", pick(&["Chat"]));
    let mut concepts = pick(&["Concept", "Feature"]);
    concepts.extend(
        order
            .iter()
            .filter_map(|id| extras.get(id))
            .filter(|node| is_type(node, &["Concept", "Feature"]))
            .take(8)
            .cloned(),
    );
    push("관련 개념/기술", "🔗", concepts);
    sections
}

/// `_render_markdown`. Its `query` argument is unused in Python too.
fn render_markdown(sections: &[Section]) -> String {
    let mut lines: Vec<String> = Vec::new();
    for section in sections {
        lines.push(format!("### {} {}", section.icon, section.title));
        lines.push(String::new());
        for item in section.items.iter().take(8) {
            let score = match item.get("hybrid_score") {
                Some(score) => format!(" (relevance: {:.2})", score.as_f64().unwrap_or(0.0)),
                None => String::new(),
            };
            let empty = Map::new();
            let meta = item
                .get("metadata")
                .and_then(Value::as_object)
                .unwrap_or(&empty);
            let id = field(item, "id");
            let source = first_truthy(
                meta,
                &["relative_path", "filename", "conversation_id", "source"],
            )
            .unwrap_or_else(|| id.clone());
            lines.push(format!(
                "- **[{}] {}**{score}",
                field(item, "type"),
                field(item, "title"),
            ));
            if !source.is_empty() && source != id {
                lines.push(format!("  - 출처: {source}"));
            }
            let summary = clean(item.get("summary"));
            if !summary.is_empty() {
                lines.push(format!("  - {summary}"));
            }
            let related = item
                .get("related_concepts")
                .and_then(Value::as_array)
                .cloned()
                .unwrap_or_default();
            if !related.is_empty() {
                let tags: Vec<String> = related
                    .iter()
                    .take(5)
                    .map(|concept| py_text(concept.get("title")))
                    .collect();
                lines.push(format!("  - 관련: {}", tags.join(", ")));
            }
            lines.push(String::new());
        }
    }
    strip(&lines.join("\n"))
}

/// `_extract_sources` — one row per distinct source key, first title wins.
///
/// The chain is one link shorter than the rendered `출처:` line's: a document
/// cites where it came *from*, and `metadata.source` is not that.
fn extract_sources(results: &[Value]) -> Vec<Value> {
    let mut seen: Vec<String> = Vec::new();
    let mut sources: Vec<Value> = Vec::new();
    for item in results {
        let empty = Map::new();
        let meta = item
            .get("metadata")
            .and_then(Value::as_object)
            .unwrap_or(&empty);
        let key = first_truthy(meta, &["relative_path", "filename", "conversation_id"])
            .unwrap_or_else(|| field(item, "id"));
        if key.is_empty() || seen.contains(&key) {
            continue;
        }
        seen.push(key.clone());
        sources.push(serde_json::json!({
            "id": field(item, "id"),
            "type": field(item, "type"),
            "title": field(item, "title"),
            "source": key,
        }));
    }
    sources
}

/// `format_sources_footnote` — the citation block appended to a generated
/// document, ten entries at most.
pub fn format_sources_footnote(sources: &[Value]) -> String {
    if sources.is_empty() {
        return String::new();
    }
    let mut lines = vec!["\n---\n**참조된 지식 그래프 노드:**".to_string()];
    for (index, source) in sources.iter().take(10).enumerate() {
        lines.push(format!(
            "{}. [{}] {} ({})",
            index + 1,
            field(source, "type"),
            field(source, "title"),
            field(source, "source"),
        ));
    }
    lines.join("\n")
}

fn section_trace(section: &Section) -> Value {
    // The token count covers *every* item's summary; the provenance stops at
    // eight. Not a typo on either side — reproduced.
    let joined: Vec<String> = section
        .items
        .iter()
        .map(|item| text_or_empty(item.get("summary")))
        .collect();
    let provenance: Vec<Value> = section
        .items
        .iter()
        .take(8)
        .map(|item| serde_json::json!({"id": item.get("id"), "type": item.get("type")}))
        .collect();
    serde_json::json!({
        "name": section.title,
        "source": "knowledge",
        "approx_tokens": approx_tokens(&joined.join(" ")),
        "provenance": provenance,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn item(id: &str, node_type: &str, title: &str) -> Value {
        json!({"id": id, "type": node_type, "title": title, "summary": "요약",
               "metadata": {}, "hybrid_score": 0.5})
    }

    #[test]
    fn the_budget_backs_off_to_a_section_boundary_only_when_it_is_late_enough() {
        let text = "### A\n\nbody body body\n### B\n\ntail";
        let (kept, trimmed) = fit_to_budget(text, 1000);
        assert_eq!((kept.as_str(), trimmed), (text, false));
        // budget*4 = 32 characters; the boundary at 23 is past 32//3 = 10.
        let (kept, trimmed) = fit_to_budget(text, 8);
        assert_eq!(kept, "### A\n\nbody body body");
        assert!(trimmed);
        // A budget too small to reach any boundary keeps a truncated head.
        let (kept, trimmed) = fit_to_budget(text, 2);
        assert_eq!(kept, "### A\n\nb");
        assert!(trimmed);
        // A boundary inside the first third is ignored for the same reason:
        // backing off to it would throw away almost everything.
        let early = format!("x\n### B\n\n{}", "y".repeat(40));
        assert_eq!(fit_to_budget(&early, 8).0.chars().count(), 32);
        // No boundary at all → the head stands, right-stripped.
        assert_eq!(fit_to_budget("aaaa bbbb  ", 1).0, "aaaa");
        assert_eq!(rfind_chars(&['a'], "\n### "), -1, "shorter than the needle");
        assert_eq!(
            fit_to_budget("anything", 0),
            ("anything".to_string(), false)
        );
    }

    #[test]
    fn the_profile_allowance_is_half_the_budget_less_its_own_header() {
        assert_eq!(self_model_budget(2000, 200), 200);
        assert_eq!(self_model_budget(40, 200), 20);
        assert_eq!(
            self_model_budget(0, 200),
            200,
            "no budget = the raw ceiling"
        );
        assert_eq!(self_model_budget(-5, 200), 200);
        assert_eq!(approx_tokens(SELF_MODEL_SECTION_HEADER), 4);
        assert_eq!(with_self_model("", "body"), "body");
        assert_eq!(with_self_model("head", ""), "head");
        assert_eq!(with_self_model("head", "body"), "head\n\nbody");
        assert!(self_model_trace("").is_empty());
        assert_eq!(self_model_trace("abcd")[0]["approx_tokens"], 1);
    }

    #[test]
    fn sections_are_fixed_and_the_traversal_only_reaches_the_last_one() {
        let results = vec![
            item("d1", "Document", "문서"),
            item("t1", "Task", "작업"),
            item("ch1", "Chat", "대화"),
            item("c1", "Concept", "개념"),
        ];
        let seeds: Vec<String> = results
            .iter()
            .map(|item| item["id"].as_str().unwrap().to_string())
            .collect();
        let hops = vec![
            json!({"id": "d1", "type": "Document", "title": "seed again", "hop": 0}),
            json!({"id": "c2", "type": "Feature", "title": "기능", "hop": 1}),
            json!({"id": "p1", "type": "Person", "title": "사람", "hop": 1}),
        ];
        let sections = build_context_sections(&results, &hops, &seeds);
        let titles: Vec<&str> = sections.iter().map(|section| section.title).collect();
        assert_eq!(
            titles,
            vec![
                "관련 문서/파일",
                "관련 결정사항/작업",
                "관련 대화",
                "관련 개념/기술"
            ]
        );
        let concepts = &sections[3].items;
        assert_eq!(concepts.len(), 2, "the Person is not a concept");
        assert_eq!(concepts[1]["id"], "c2");
        // A section with nothing in it is left out entirely.
        assert_eq!(build_context_sections(&[], &[], &[]).len(), 0);
    }

    #[test]
    fn the_markdown_names_the_source_only_when_it_is_not_the_id() {
        let mut doc = item("d1", "Document", "문서");
        doc["metadata"] = json!({"filename": "p.md"});
        doc["related_concepts"] = json!([{"title": "가"}, {"title": "나"}]);
        let plain = item("c1", "Concept", "개념");
        let sections = build_context_sections(&[doc, plain], &[], &[]);
        let rendered = render_markdown(&sections);
        assert!(rendered.contains("- **[Document] 문서** (relevance: 0.50)"));
        assert!(rendered.contains("  - 출처: p.md"));
        assert!(rendered.contains("  - 관련: 가, 나"));
        assert!(
            !rendered.contains("출처: c1"),
            "a source equal to the id is not a source"
        );
        assert!(render_markdown(&[]).is_empty());
    }

    #[test]
    fn sources_dedupe_by_key_and_the_footnote_stops_at_ten() {
        let mut first = item("a", "Document", "A");
        first["metadata"] = json!({"relative_path": "docs/a.md", "filename": "a.md"});
        let mut same = item("b", "Document", "B");
        same["metadata"] = json!({"relative_path": "docs/a.md"});
        let bare = item("c", "Concept", "C");
        let sources = extract_sources(&[first, same, bare]);
        assert_eq!(sources.len(), 2, "the shared path is one source");
        assert_eq!(sources[0]["source"], "docs/a.md");
        assert_eq!(sources[1]["source"], "c", "the id is the last resort");
        assert!(format_sources_footnote(&[]).is_empty());

        let many: Vec<Value> = (0..12)
            .map(|index| {
                json!({"id": format!("n{index}"), "type": "Document",
                                "title": "t", "source": format!("s{index}")})
            })
            .collect();
        let footnote = format_sources_footnote(&many);
        assert_eq!(
            footnote.lines().count(),
            3 + 10,
            "header block plus ten rows"
        );
        assert!(footnote.contains("10. [Document] t (s9)"));
        assert!(!footnote.contains("11."));
    }

    #[test]
    fn a_blank_query_is_answered_honestly_and_without_a_budget_it_never_saw() {
        let dir = tempfile::tempdir().unwrap();
        let conn = Connection::open(dir.path().join("g.sqlite")).unwrap();
        let out = retrieve_context_for_generation(
            &conn,
            &DocumentContextRequest {
                query: "   ".into(),
                budget: 50,
                ..Default::default()
            },
        )
        .unwrap();
        assert_eq!(out["stats"], json!({"method": "none", "matches": 0}));
        assert_eq!(
            out["context_quality"]["reason"],
            "문서 생성 컨텍스트를 조회할 수 없습니다"
        );
        assert_eq!(out["trace"]["budget_approx_tokens"], 2000);
        assert_eq!(out["context_markdown"], "");
        assert_eq!(clean(None), "");
        assert_eq!(text_or_empty(Some(&Value::Null)), "");
        assert_eq!(field(&json!({}), "title"), "");
        assert!(format!("{:?}", DocumentContextRequest::default()).contains("max_hops"));
    }
}
