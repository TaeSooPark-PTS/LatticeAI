//! Port of `lattice_brain/context.py` — the budgeted, provenance-carrying
//! context assembly (Phase 3b of `docs/v11.5.0_RUST_COMPLETE_PLAN.md`).
//!
//! Five sections in one fixed order — memories, artifacts, knowledge, notes,
//! recent — because the order *is* the priority: what survives a tight budget is
//! whatever came first. Sections whose content is blank are dropped before the
//! budget runs, so an unconfigured seam contributes nothing rather than an empty
//! heading, and every section records why it is in the prompt.
//!
//! `approx_tokens` is `(len + 3) / 4` on **characters**. It is not a tokenizer
//! and the field name says so; what matters here is that Rust and Python agree
//! on the same wrong number, because the budget is spent in that currency.
//!
//! The truncation is where a port drifts. Python cuts `content[:remaining * 4]`
//! and then measures the *cut* content when charging the budget — so a section
//! trimmed to fit costs exactly what fits, and the next section sees whatever is
//! left. Cutting on bytes instead of characters would also split a Korean
//! syllable down the middle; `truncate_chars` is what keeps that honest.

use std::collections::BTreeSet;

use lattice_core::pytext::truncate_chars;
use lattice_core::{CoreError, LocalEmbeddingModel};
use rusqlite::Connection;
use serde_json::{Map, Value};

use crate::history::{recent_chat, RecentChatOptions};
use crate::service::Scope;
use crate::service_hybrid::{service_hybrid_search, ServiceHybridOptions};
use crate::shape::truthy;

/// Scope the knowledge seam the way chat/memory/command do: a named workspace
/// is a membership filter, and `"personal"` also matches NULL/blank rows.
fn knowledge_scope(workspace_id: Option<&str>) -> Scope {
    match workspace_id.filter(|id| !id.is_empty()) {
        Some(id) => Scope {
            allowed_workspaces: Some(BTreeSet::from([id.to_string()])),
            include_legacy_global: false,
        },
        None => Scope::default(),
    }
}

/// `context.approx_tokens` — the documented chars/4 approximation.
pub fn approx_tokens(text: &str) -> usize {
    text.chars().count().div_ceil(4)
}

/// One assembled section, before and after the budget has had its say.
#[derive(Debug, Clone)]
struct Section {
    name: &'static str,
    source: &'static str,
    content: String,
    provenance: Vec<Value>,
    truncated: bool,
}

impl Section {
    fn approx_tokens(&self) -> usize {
        approx_tokens(&self.content)
    }

    fn as_trace(&self) -> Value {
        let mut trace = Map::new();
        trace.insert("name".into(), Value::String(self.name.to_string()));
        trace.insert("source".into(), Value::String(self.source.to_string()));
        trace.insert("approx_tokens".into(), Value::from(self.approx_tokens()));
        trace.insert("truncated".into(), Value::Bool(self.truncated));
        trace.insert("provenance".into(), Value::Array(self.provenance.clone()));
        Value::Object(trace)
    }
}

/// What `recent_chat` should be asked for, when it is asked at all.
#[derive(Debug, Clone, Default)]
pub struct RecentRequest {
    pub limit: Option<i64>,
    pub include_image_missing_replies: Option<bool>,
    pub user_email: Option<String>,
    pub conversation_id: Option<String>,
    pub workspace_id: Option<String>,
}

/// One context assembly. Every seam is optional, and an absent seam is an
/// absent section — honest absence, never a fabricated one.
#[derive(Debug, Clone, Default)]
pub struct ContextRequest {
    pub query: String,
    /// Token budget; anything below 1 clamps to 1.
    pub budget: i64,
    pub memory_limit: i64,
    pub knowledge_limit: i64,
    /// A memory-recall payload (`{"results": [...]}`), or `None` for no seam.
    pub memories: Option<Value>,
    /// An artifact ledger slice, or `None` for no seam.
    pub artifacts: Option<Value>,
    /// Whether the knowledge section runs the native service hybrid.
    pub knowledge: bool,
    /// Garden-note context, or `None` for no seam.
    pub notes: Option<String>,
    /// Recent-conversation seam configuration, or `None` for no seam.
    pub recent: Option<RecentRequest>,
    /// Identity/scope of the assembly itself. These do **not** steer the native
    /// seams (a loopback owner is trusted); they are what the recent section
    /// records as its provenance, exactly as `assemble()`'s own arguments do.
    pub user_email: Option<String>,
    pub conversation_id: Option<String>,
    pub workspace_id: Option<String>,
    /// The wall clock the knowledge search's recency decay reads.
    pub now_secs: f64,
}

fn string_of(value: Option<&Value>) -> String {
    match value {
        Some(Value::String(text)) => text.clone(),
        Some(other) if truthy(other) => crate::shape::py_str(other),
        _ => String::new(),
    }
}

/// `_memories_section` — workspace memories only, capped at `memory_limit`.
fn memories_section(payload: &Value, limit: i64) -> Section {
    let results: Vec<&Value> = payload
        .get("results")
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter(|item| item.get("source").and_then(Value::as_str) == Some("workspace"))
                .take(limit.max(0) as usize)
                .collect()
        })
        .unwrap_or_default();
    let mut lines = Vec::new();
    let mut provenance = Vec::new();
    for item in results {
        let kind = item.get("kind").filter(|value| truthy(value));
        let kind_text = kind.map(string_of_ref).unwrap_or_else(|| "memory".into());
        lines.push(format!(
            "- ({kind_text}) {}",
            string_of(item.get("snippet"))
        ));
        let mut record = Map::new();
        record.insert("id".into(), item.get("id").cloned().unwrap_or(Value::Null));
        record.insert(
            "kind".into(),
            item.get("kind").cloned().unwrap_or(Value::Null),
        );
        record.insert(
            "score".into(),
            item.get("score").cloned().unwrap_or(Value::Null),
        );
        provenance.push(Value::Object(record));
    }
    Section {
        name: "User memories",
        source: "memory",
        content: lines.join("\n"),
        provenance,
        truncated: false,
    }
}

fn string_of_ref(value: &Value) -> String {
    string_of(Some(value))
}

/// `_artifacts_section` — files this conversation produced, ten at most.
fn artifacts_section(payload: &Value) -> Section {
    let rows: Vec<&Value> = payload
        .as_array()
        .map(|items| {
            items
                .iter()
                .filter(|item| item.is_object() && item.get("path").map(truthy).unwrap_or(false))
                .take(10)
                .collect()
        })
        .unwrap_or_default();
    let mut lines = Vec::new();
    let mut provenance = Vec::new();
    for item in &rows {
        let path = string_of(item.get("path"));
        let at = item.get("at").filter(|value| truthy(value));
        lines.push(match at {
            Some(at) => format!("- {path} ({})", string_of_ref(at)),
            None => format!("- {path}"),
        });
        let mut record = Map::new();
        record.insert(
            "path".into(),
            item.get("path").cloned().unwrap_or(Value::Null),
        );
        record.insert(
            "run_id".into(),
            item.get("run_id").cloned().unwrap_or(Value::Null),
        );
        provenance.push(Value::Object(record));
    }
    Section {
        name: "Files created in this conversation",
        source: "artifacts",
        content: lines.join("\n"),
        provenance,
        truncated: false,
    }
}

/// `_knowledge_section` — the product's own search, cut to `knowledge_limit`.
fn knowledge_section(payload: &Value, limit: i64) -> Section {
    let empty: Vec<Value> = Vec::new();
    let matches = payload
        .get("matches")
        .and_then(Value::as_array)
        .unwrap_or(&empty);
    let mut lines = Vec::new();
    let mut provenance = Vec::new();
    for item in matches.iter().take(limit.max(0) as usize) {
        let title = item
            .get("title")
            .filter(|value| truthy(value))
            .or_else(|| item.get("id").filter(|value| truthy(value)))
            .map(string_of_ref)
            .unwrap_or_else(|| "item".to_string());
        let body_source = item
            .get("summary")
            .filter(|value| truthy(value))
            .or_else(|| item.get("snippet").filter(|value| truthy(value)));
        let body = truncate_chars(&body_source.map(string_of_ref).unwrap_or_default(), 400);
        lines.push(if body.is_empty() {
            format!("- {title}")
        } else {
            format!("- {title}: {body}")
        });
        let mut record = Map::new();
        record.insert("id".into(), item.get("id").cloned().unwrap_or(Value::Null));
        record.insert(
            "score".into(),
            item.get("score").cloned().unwrap_or(Value::Null),
        );
        record.insert(
            "sources".into(),
            item.get("sources").cloned().unwrap_or(Value::Null),
        );
        provenance.push(Value::Object(record));
    }
    Section {
        name: "Knowledge",
        source: "knowledge",
        content: lines.join("\n"),
        provenance,
        truncated: false,
    }
}

/// `ContextAssembler._apply_budget` — trim from the end until it fits.
fn apply_budget(sections: &mut [Section], budget: i64) {
    let budget = budget.max(1) as usize;
    let mut used = 0usize;
    for section in sections.iter_mut() {
        let remaining = budget.saturating_sub(used);
        if remaining == 0 {
            section.content = String::new();
            section.truncated = true;
            continue;
        }
        if section.approx_tokens() > remaining {
            section.content = truncate_chars(&section.content, remaining * 4);
            section.truncated = true;
        }
        // Re-measured after the cut, so a trimmed section costs what it kept.
        used += section.approx_tokens();
    }
}

/// `AssembledContext.text` — named blocks, blank ones left out.
fn assembled_text(sections: &[Section]) -> String {
    sections
        .iter()
        .filter(|section| !section.content.trim().is_empty())
        .map(|section| format!("[{}]\n{}", section.name, section.content.trim()))
        .collect::<Vec<_>>()
        .join("\n\n")
}

/// `ContextAssembler.assemble` over the native seams.
///
/// The knowledge seam is the service-layer hybrid search and the recent seam is
/// the durable history reader — the same engines the rest of this crate ports,
/// wired in rather than mocked. `memories` / `artifacts` / `notes` arrive as
/// data because their producers (the memory service, the artifact ledger, the
/// garden) live in the Python worker and are not part of this phase.
pub fn assemble_context(
    conn: &Connection,
    model: &LocalEmbeddingModel,
    request: &ContextRequest,
) -> Result<Value, CoreError> {
    let mut sections: Vec<Section> = Vec::new();
    if let Some(memories) = request.memories.as_ref() {
        sections.push(memories_section(memories, request.memory_limit));
    }
    if let Some(artifacts) = request.artifacts.as_ref() {
        sections.push(artifacts_section(artifacts));
    }
    if request.knowledge {
        let payload = service_hybrid_search(
            conn,
            model,
            &request.query,
            &ServiceHybridOptions {
                limit: request.knowledge_limit,
                scope: knowledge_scope(request.workspace_id.as_deref()),
                now_secs: request.now_secs,
                ..ServiceHybridOptions::default()
            },
        )?;
        sections.push(knowledge_section(&payload, request.knowledge_limit));
    }
    if let Some(notes) = request.notes.as_ref() {
        sections.push(Section {
            name: "Garden notes",
            source: "notes",
            content: notes.clone(),
            provenance: vec![serde_json::json!({
                "source": "garden",
                "included": !notes.is_empty(),
            })],
            truncated: false,
        });
    }
    if let Some(recent) = request.recent.as_ref() {
        let options = RecentChatOptions {
            limit: recent.limit.unwrap_or(10),
            include_image_missing_replies: recent.include_image_missing_replies.unwrap_or(true),
            user_email: recent.user_email.clone(),
            conversation_id: recent.conversation_id.clone(),
            workspace_id: recent.workspace_id.clone(),
        };
        let content = recent_chat(conn, &options)?;
        sections.push(Section {
            name: "Recent conversation",
            source: "recent_chat",
            content,
            provenance: vec![serde_json::json!({
                "conversation_id": request.conversation_id.clone(),
                "user_email": request.user_email.clone(),
            })],
            truncated: false,
        });
    }

    // Blank sections leave before the budget is spent, so an empty seam never
    // costs a later section its place.
    sections.retain(|section| !section.content.trim().is_empty());
    apply_budget(&mut sections, request.budget);

    let used: usize = sections.iter().map(Section::approx_tokens).sum();
    let mut trace = Map::new();
    trace.insert("budget_approx_tokens".into(), Value::from(request.budget));
    trace.insert("used_approx_tokens".into(), Value::from(used));
    trace.insert(
        "sections".into(),
        Value::Array(sections.iter().map(Section::as_trace).collect()),
    );

    let mut out = Map::new();
    out.insert("text".into(), Value::String(assembled_text(&sections)));
    out.insert("approx_tokens".into(), Value::from(used));
    out.insert("trace".into(), Value::Object(trace));
    Ok(Value::Object(out))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn section(name: &'static str, content: &str) -> Section {
        Section {
            name,
            source: "memory",
            content: content.to_string(),
            provenance: Vec::new(),
            truncated: false,
        }
    }

    #[test]
    fn approx_tokens_rounds_up_on_characters() {
        assert_eq!(approx_tokens(""), 0);
        assert_eq!(approx_tokens("a"), 1);
        assert_eq!(approx_tokens("abcd"), 1);
        assert_eq!(approx_tokens("abcde"), 2);
        // Characters, not bytes: three Korean syllables are nine UTF-8 bytes.
        assert_eq!(approx_tokens("회의록"), 1);
    }

    #[test]
    fn the_budget_trims_from_the_end_and_re_measures() {
        let mut sections = vec![
            section("first", &"a".repeat(40)),
            section("second", &"b".repeat(40)),
            section("third", &"c".repeat(40)),
        ];
        apply_budget(&mut sections, 12);
        assert_eq!(sections[0].content.len(), 40);
        assert!(!sections[0].truncated);
        assert_eq!(
            sections[1].content.len(),
            8,
            "cut to the remaining 2 tokens"
        );
        assert!(sections[1].truncated);
        assert_eq!(sections[2].content, "", "no budget left at all");
        assert!(sections[2].truncated);
        let used: usize = sections.iter().map(Section::approx_tokens).sum();
        assert_eq!(used, 12, "a trimmed section costs exactly what it kept");
    }

    #[test]
    fn a_budget_below_one_still_admits_one_token() {
        let mut sections = vec![section("first", "abcdefgh")];
        apply_budget(&mut sections, 0);
        assert_eq!(sections[0].content, "abcd");
        assert!(sections[0].truncated);
        let mut korean = vec![section("first", "회의 결정 사항입니다")];
        apply_budget(&mut korean, 1);
        assert_eq!(
            korean[0].content.chars().count(),
            4,
            "characters, not bytes"
        );
    }

    #[test]
    fn the_text_names_each_block_and_skips_blank_ones() {
        let sections = vec![
            section("User memories", "  - one  "),
            section("Knowledge", "   "),
            section("Garden notes", "note"),
        ];
        assert_eq!(
            assembled_text(&sections),
            "[User memories]\n- one\n\n[Garden notes]\nnote"
        );
        assert!(assembled_text(&[]).is_empty());
    }

    #[test]
    fn memories_keep_workspace_rows_only() {
        let payload = json!({"results": [
            {"id": "m1", "kind": "preference", "snippet": "한국어로", "score": 0.9,
             "source": "workspace"},
            {"id": "m2", "kind": null, "snippet": "", "score": 0.0, "source": "workspace"},
            {"id": "m3", "kind": "decision", "snippet": "dropped", "source": "personal"},
        ]});
        let built = memories_section(&payload, 5);
        assert_eq!(built.content, "- (preference) 한국어로\n- (memory) ");
        assert_eq!(built.provenance.len(), 2);
        assert_eq!(built.provenance[1]["kind"], Value::Null);
        assert_eq!(built.provenance[1]["score"], json!(0.0));
        assert_eq!(memories_section(&payload, 1).provenance.len(), 1);
        assert!(memories_section(&json!({}), 5).content.is_empty());
    }

    #[test]
    fn artifacts_drop_pathless_and_non_dict_rows_and_stop_at_ten() {
        let mut rows = vec![
            json!({"path": "a.md", "at": "2026-01-01", "run_id": "r1"}),
            json!({"path": "b.md"}),
            json!({"path": ""}),
            json!("not-a-dict"),
        ];
        for index in 0..12 {
            rows.push(json!({"path": format!("f{index}.md")}));
        }
        let built = artifacts_section(&Value::Array(rows));
        assert_eq!(built.content.lines().count(), 10);
        assert!(built.content.starts_with("- a.md (2026-01-01)\n- b.md\n"));
        assert_eq!(built.provenance[1]["run_id"], Value::Null);
        assert!(artifacts_section(&json!({})).content.is_empty());
    }

    #[test]
    fn knowledge_lines_fall_back_from_title_to_id_to_item() {
        let payload = json!({"matches": [
            {"id": "n1", "title": "Title", "summary": "Body", "score": 0.5,
             "sources": ["keyword"]},
            {"id": "n2", "summary": "", "snippet": "from snippet"},
            {"summary": ""},
        ]});
        let built = knowledge_section(&payload, 5);
        assert_eq!(built.content, "- Title: Body\n- n2: from snippet\n- item");
        assert_eq!(built.provenance[2]["id"], Value::Null);
        assert_eq!(built.provenance[0]["sources"], json!(["keyword"]));
        assert_eq!(knowledge_section(&payload, 1).content, "- Title: Body");
        // A long body is cut at 400 characters.
        let long = json!({"matches": [{"id": "x", "summary": "가".repeat(500)}]});
        assert_eq!(
            knowledge_section(&long, 5).content.chars().count(),
            400 + "- x: ".len()
        );
    }

    #[test]
    fn an_absent_seam_is_an_absent_section() {
        let dir = tempfile::tempdir().unwrap();
        let conn = Connection::open(dir.path().join("g.sqlite")).unwrap();
        conn.execute_batch(
            "CREATE TABLE conversation_messages(id INTEGER PRIMARY KEY AUTOINCREMENT,
               conversation_id TEXT, role TEXT, content TEXT, user_email TEXT,
               user_nickname TEXT, source TEXT, timestamp TEXT,
               metadata_json TEXT NOT NULL DEFAULT '{}', workspace_id TEXT,
               organization_id TEXT);
             CREATE TABLE nodes_v2(id TEXT PRIMARY KEY, workspace_id TEXT);",
        )
        .unwrap();
        let model = LocalEmbeddingModel::new(384);
        let request = ContextRequest {
            query: "anything".into(),
            budget: 2000,
            memory_limit: 5,
            knowledge_limit: 5,
            notes: Some("   ".into()),
            ..Default::default()
        };
        let out = assemble_context(&conn, &model, &request).unwrap();
        assert_eq!(out["text"], "");
        assert_eq!(out["approx_tokens"], 0);
        assert_eq!(out["trace"]["sections"], json!([]));
        assert_eq!(out["trace"]["budget_approx_tokens"], 2000);
        assert!(format!("{request:?}").contains("knowledge_limit"));
        assert!(format!("{:?}", RecentRequest::default()).contains("limit"));
    }

    fn knowledge_graph() -> (tempfile::TempDir, Connection) {
        let dir = tempfile::tempdir().unwrap();
        let conn = Connection::open(dir.path().join("g.sqlite")).unwrap();
        conn.execute_batch(
            "CREATE TABLE nodes(id TEXT PRIMARY KEY, type TEXT, title TEXT, summary TEXT,
                                metadata_json TEXT, updated_at TEXT);
             CREATE TABLE edges(id TEXT PRIMARY KEY, from_node TEXT, to_node TEXT, type TEXT,
                                weight REAL, metadata_json TEXT, created_at TEXT);
             CREATE TABLE nodes_v2(id TEXT PRIMARY KEY, workspace_id TEXT);
             CREATE TABLE chunks(id TEXT PRIMARY KEY, source_node TEXT, text TEXT, metadata_json TEXT);
             CREATE TABLE vector_embeddings(item_id TEXT PRIMARY KEY, item_type TEXT,
                                            source_node TEXT, embedding BLOB, embedding_dim INTEGER,
                                            embedding_model TEXT, metadata_json TEXT,
                                            indexed_at TEXT);
             INSERT INTO nodes VALUES
               ('note','Document','Phoenix launch notes','the phoenix plan','{}','2026-08-11T09:00:00'),
               ('team','Document','Acme only','secret','{}','2026-08-11T09:00:00');
             INSERT INTO nodes_v2 VALUES ('note', NULL), ('team', 'acme');",
        )
        .unwrap();
        (dir, conn)
    }

    fn match_ids(out: &Value) -> Vec<String> {
        out["trace"]["sections"]
            .as_array()
            .into_iter()
            .flatten()
            .find(|section| section["source"] == "knowledge")
            .and_then(|section| section["provenance"].as_array())
            .into_iter()
            .flatten()
            .filter_map(|row| row.get("id").and_then(Value::as_str).map(str::to_string))
            .collect()
    }

    #[test]
    fn knowledge_assembly_treats_null_workspace_as_personal() {
        let (_dir, conn) = knowledge_graph();
        let model = LocalEmbeddingModel::new(384);
        let personal = assemble_context(
            &conn,
            &model,
            &ContextRequest {
                query: "phoenix".into(),
                budget: 2000,
                knowledge_limit: 5,
                knowledge: true,
                workspace_id: Some("personal".into()),
                ..Default::default()
            },
        )
        .unwrap();
        assert_eq!(match_ids(&personal), vec!["note".to_string()]);
        let named = assemble_context(
            &conn,
            &model,
            &ContextRequest {
                query: "phoenix".into(),
                budget: 2000,
                knowledge_limit: 5,
                knowledge: true,
                workspace_id: Some("acme".into()),
                ..Default::default()
            },
        )
        .unwrap();
        assert!(
            match_ids(&named).is_empty(),
            "a named workspace must not see the unstamped node"
        );
        let stamped = assemble_context(
            &conn,
            &model,
            &ContextRequest {
                query: "secret".into(),
                budget: 2000,
                knowledge_limit: 5,
                knowledge: true,
                workspace_id: Some("acme".into()),
                ..Default::default()
            },
        )
        .unwrap();
        assert_eq!(match_ids(&stamped), vec!["team".to_string()]);
    }
}
