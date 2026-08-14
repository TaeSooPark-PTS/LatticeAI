//! `latticeai/services/evidence_actions.py` — the deterministic composer.
//!
//! Given the citations an answer actually used, resolve them against the graph
//! and compose ready-to-send, evidence-scoped prompts. No model runs and
//! nothing is written, so the whole module is a pure function of the question,
//! the citation ids, the language, and the nodes those ids name.
//!
//! Python details that decide byte parity, reproduced exactly:
//!
//! * **`if value:` is Python truthiness.** A stored `title` that is `NULL`
//!   *or* `""` falls back to the node id; a metadata origin key holding `""`
//!   is skipped. [`crate::shape::truthy`] is that test — `Option::is_some`
//!   would keep an empty string and change both fields.
//! * **the excerpt cap counts characters** (`body[:600]`), so a Korean summary
//!   keeps 600 syllables rather than 600 bytes' worth of them.
//! * **the cap comes before the de-duplication.** `source_ids[:8]` slices the
//!   raw list, so eight repeats of one id resolve to a single source, not to
//!   eight distinct ones.
//! * **failures are reported, never invented.** A citation that does not
//!   resolve lands in `missing`; when nothing resolves, `actions` is empty and
//!   `reason` says why. That is the module's stated honesty rule.
//!
//! Node reads go through [`crate::memory_api::kg::get_node`] and workspace
//! scoping through [`lattice_core::filter_scoped_nodes`] — the same two
//! readers `KnowledgeGraphReadsMixin.get_node` composes on the Python side.

#![allow(
    dead_code,
    unused_imports,
    unused_variables,
    unused_assignments,
    unused_mut,
    private_interfaces,
    clippy::result_large_err,
    clippy::needless_lifetimes,
    clippy::too_many_arguments,
    clippy::type_complexity,
    clippy::collapsible_if,
    clippy::needless_as_bytes,
    clippy::redundant_closure,
    clippy::needless_return,
    clippy::manual_clamp,
    clippy::ptr_arg,
    clippy::unnecessary_sort_by,
    clippy::result_unit_err,
    clippy::useless_vec,
    clippy::uninlined_format_args,
    clippy::manual_contains,
    clippy::needless_borrows_for_generic_args,
    clippy::implicit_clone,
    clippy::unnecessary_map_or,
    clippy::match_like_matches_macro,
    clippy::manual_range_contains,
    clippy::derivable_impls,
    clippy::needless_pass_by_ref_mut,
    clippy::redundant_guards,
    clippy::map_identity,
    clippy::iter_overeager_cloned,
    clippy::explicit_auto_deref,
    clippy::bool_comparison,
    clippy::nonminimal_bool,
    clippy::if_same_then_else,
    clippy::question_mark,
    clippy::single_char_pattern,
    clippy::manual_pattern_char_comparison,
    clippy::manual_is_ascii_check,
    clippy::repeat_once,
    clippy::unused_self,
    clippy::module_inception
)]
use std::collections::BTreeSet;

use lattice_core::pytext;
use rusqlite::Connection;
use serde_json::Value;

use super::json::Json;
use crate::memory_api::kg;
use crate::shape::{py_str, truthy};

/// `_EXCERPT_CHARS` — the per-source excerpt cap, in characters.
///
/// The service takes it as `max(120, int(excerpt_chars))`; every call site in
/// the product builds `EvidenceActionService()` with no argument, so the
/// clamp has exactly one reachable value and it is this one.
pub const EXCERPT_CHARS: usize = 600;

/// `_MAX_SOURCES` — how many citations one answer may spend.
pub const MAX_SOURCES: usize = 8;

/// `slugify(..., fallback="evidence-note")`.
pub const SLUG_FALLBACK: &str = "evidence-note";

/// The metadata keys an origin is read from, in Python's order.
const ORIGIN_KEYS: [&str; 5] = [
    "relative_path",
    "file_path",
    "filename",
    "source_uri",
    "source",
];

/// One Korean/English phrase pair — `latticeai.core.messages.bilingual`.
#[derive(Debug, Clone, Copy)]
pub struct Phrase {
    /// `phrase["ko"]`.
    pub ko: &'static str,
    /// `phrase["en"]`.
    pub en: &'static str,
}

impl Phrase {
    /// `phrase[language]`, where `language` is already normalized.
    pub fn get(self, language: &str) -> &'static str {
        if language == "ko" {
            self.ko
        } else {
            self.en
        }
    }

    /// `dict(phrase)` — both languages, Korean first, whatever was asked for.
    pub fn to_json(self) -> Json {
        Json::Object(vec![
            ("ko", Json::string(self.ko)),
            ("en", Json::string(self.en)),
        ])
    }
}

/// One entry of the closed action catalog.
#[derive(Debug, Clone, Copy)]
pub struct ActionSpec {
    /// `spec["id"]`.
    pub id: &'static str,
    /// `spec["kind"]` — `chat` or `file`.
    pub kind: &'static str,
    /// `spec["label"]`.
    pub label: Phrase,
    /// `spec["instruction"]`, with `{path}` still in it.
    pub instruction: Phrase,
    /// `spec["extension"]`, or `""` when the action produces no file.
    pub extension: &'static str,
}

/// `EVIDENCE_ACTIONS` — closed and deterministic, in the UI's render order.
pub const EVIDENCE_ACTIONS: [ActionSpec; 4] = [
    ActionSpec {
        id: "summary",
        kind: "chat",
        label: Phrase {
            ko: "이 근거로 요약 만들기",
            en: "Summarize from this evidence",
        },
        instruction: Phrase {
            ko: "핵심만 5줄 이내로 요약해 주세요.",
            en: "Summarize the key points in five lines or fewer.",
        },
        extension: "",
    },
    ActionSpec {
        id: "checklist",
        kind: "chat",
        label: Phrase {
            ko: "이 근거로 체크리스트 만들기",
            en: "Build a checklist",
        },
        instruction: Phrase {
            ko: "실행 가능한 체크리스트를 만들어 주세요. 각 항목은 한 줄이고, 근거가 있는 항목만 넣으세요.",
            en: "Build an actionable checklist. One line per item, only items the evidence supports.",
        },
        extension: "",
    },
    ActionSpec {
        id: "document",
        kind: "file",
        label: Phrase {
            ko: "이 근거로 문서 파일 만들기",
            en: "Write a document file",
        },
        instruction: Phrase {
            ko: "정리된 마크다운 문서를 만들어 {path} 파일로 저장해 주세요.",
            en: "Write a structured markdown document and save it as {path}.",
        },
        extension: ".md",
    },
    ActionSpec {
        id: "page",
        kind: "file",
        label: Phrase {
            ko: "이 근거로 한 페이지 만들기",
            en: "Build a one-page view",
        },
        instruction: Phrase {
            ko: "내용을 한눈에 보는 HTML 한 페이지로 만들어 {path} 파일로 저장해 주세요.",
            en: "Build a single self-contained HTML page and save it as {path}.",
        },
        extension: ".html",
    },
];

/// The `[근거 자료]` / `[EVIDENCE]` header the composed prompt opens with.
const EVIDENCE_HEADER: Phrase = Phrase {
    ko: "[근거 자료]",
    en: "[EVIDENCE]",
};

/// `_guard(language)` — use the quoted evidence, and say when it is not enough.
const GUARD: Phrase = Phrase {
    ko: "위 근거 자료에 있는 내용만 사용하세요. 근거에 없는 사실은 지어내지 말고, 근거가 부족하면 '이 부분은 근거가 없습니다'라고 적으세요.",
    en: "Use only the evidence quoted above. Do not invent facts that are not in it; when the evidence does not cover something, say so explicitly.",
};

/// The `원래 질문:` / `Original question:` label of the trailing prompt line.
const QUESTION_LABEL: Phrase = Phrase {
    ko: "원래 질문: ",
    en: "Original question: ",
};

/// The `reason` an answer with no usable evidence carries.
const NO_EVIDENCE_REASON: Phrase = Phrase {
    ko: "근거로 쓸 출처를 찾지 못했습니다.",
    en: "No usable evidence could be resolved.",
};

/// One resolved citation, in the key order the response renders it.
#[derive(Debug, Clone, PartialEq)]
pub struct Source {
    /// The citation id as the caller sent it, stripped.
    pub id: String,
    /// `title or id or node_id`.
    pub title: String,
    /// `str(record["type"] or "")`.
    pub node_type: String,
    /// The first truthy of five metadata keys, or `""`.
    pub origin: String,
    /// `body[:600]`.
    pub excerpt: String,
    /// Whether the body was longer than the cap.
    pub truncated: bool,
}

impl Source {
    /// The JSON object one source renders as.
    pub fn to_json(&self) -> Json {
        Json::Object(vec![
            ("id", Json::string(self.id.clone())),
            ("title", Json::string(self.title.clone())),
            ("type", Json::string(self.node_type.clone())),
            ("origin", Json::string(self.origin.clone())),
            ("excerpt", Json::string(self.excerpt.clone())),
            ("truncated", Json::boolean(self.truncated)),
        ])
    }
}

/// `resolve()` — `{"sources": [...], "missing": [...]}`.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Resolved {
    /// The citations that resolved, in the order they were cited.
    pub sources: Vec<Source>,
    /// The citations that did not, in the order they were cited.
    pub missing: Vec<String>,
}

/// The citation ids one call will actually look up.
///
/// `list(source_ids or [])[:8]`, then `str(raw or "").strip()`, then skip
/// blanks and repeats — in that order, because the cap slices the raw list.
pub fn candidate_ids(source_ids: &[String]) -> Vec<String> {
    let mut ids: Vec<String> = Vec::new();
    for raw in source_ids.iter().take(MAX_SOURCES) {
        let node_id = pytext::strip(raw);
        if node_id.is_empty() || ids.contains(&node_id) {
            continue;
        }
        ids.push(node_id);
    }
    ids
}

/// Resolve citation ids against the graph.
///
/// `conn == None` is the Python service with `node_reader=None`: the graph is
/// disabled (`KNOWLEDGE_GRAPH` is `None`, so `getattr(..., "get_node", None)`
/// answered nothing), and every citation is reported missing rather than
/// silently dropped.
pub fn resolve(
    conn: Option<&Connection>,
    ids: &[String],
    allowed_workspaces: Option<&BTreeSet<String>>,
) -> Resolved {
    let mut resolved = Resolved::default();
    for node_id in ids {
        match read_node(conn, node_id, allowed_workspaces) {
            Some(record) => resolved.sources.push(source_from(&record, node_id)),
            None => resolved.missing.push(node_id.clone()),
        }
    }
    resolved
}

/// `_read_node` — best-effort, and a failure is a missing citation.
///
/// Python catches **every** exception the reader raises (an unknown id, a
/// workspace the caller may not read, a busy database) and answers `None`, so
/// a store failure surfaces as "this citation did not resolve" rather than as
/// a 500. `Err` is swallowed here for exactly that reason.
fn read_node(
    conn: Option<&Connection>,
    node_id: &str,
    allowed_workspaces: Option<&BTreeSet<String>>,
) -> Option<Value> {
    let conn = conn?;
    let node = kg::get_node(conn, node_id).ok().flatten()?;
    let Some(allowed) = allowed_workspaces else {
        return Some(node);
    };
    // `get_node(..., allowed_workspaces=…)` raises `graph node not found` when
    // the scope filter drops the row, and `include_legacy_global` stays false:
    // a legacy row with no workspace is private, not public.
    lattice_core::filter_scoped_nodes(conn, vec![node], Some(allowed), false, |item| {
        item.get("id")
            .filter(|v| truthy(v))
            .map(py_str)
            .unwrap_or_default()
    })
    .ok()?
    .into_iter()
    .next()
}

/// One resolved graph record as the citation the prompt quotes.
fn source_from(record: &Value, node_id: &str) -> Source {
    // `/api/graph/node` wraps its record under "node"; `get_node` — the one
    // reader this port is wired to — returns it directly, and its key set is
    // fixed (`id, type, title, summary, metadata, created_at, updated_at`), so
    // Python's `node.get("node")` unwrap has no reachable input here.
    let title = truthy_field(record, "title")
        .or_else(|| truthy_field(record, "id"))
        .map(py_str)
        .unwrap_or_else(|| node_id.to_string());
    let body = truthy_field(record, "summary")
        .or_else(|| truthy_field(record, "content"))
        .map(py_str)
        .unwrap_or_default();
    let body = pytext::strip(&body);
    let origin = record
        .get("metadata")
        .and_then(Value::as_object)
        .and_then(|metadata| {
            ORIGIN_KEYS
                .iter()
                .find_map(|key| metadata.get(*key).filter(|value| truthy(value)))
        })
        .map(py_str)
        .unwrap_or_default();
    Source {
        id: node_id.to_string(),
        title,
        node_type: truthy_field(record, "type").map(py_str).unwrap_or_default(),
        origin,
        excerpt: pytext::truncate_chars(&body, EXCERPT_CHARS),
        truncated: body.chars().count() > EXCERPT_CHARS,
    }
}

/// `record.get(key)`, but only when Python would have called it truthy.
fn truthy_field<'a>(record: &'a Value, key: &str) -> Option<&'a Value> {
    record.get(key).filter(|value| truthy(value))
}

/// `"ko" if str(language or "ko").lower().startswith("ko") else "en"`.
pub fn normalize_language(language: &str) -> &'static str {
    let raw = if language.is_empty() { "ko" } else { language };
    if raw.to_lowercase().starts_with("ko") {
        "ko"
    } else {
        "en"
    }
}

/// `slugify(text)` — a deterministic, filesystem-safe artifact stem.
///
/// Non-ASCII text (a Korean question is the common case) leaves nothing to
/// slug, and the fallback keeps the suggested filename predictable instead of
/// mangled or empty.
pub fn slugify(text: &str) -> String {
    // `_SLUG_STRIP_RE.sub("-", text.lower())`: every run of characters outside
    // `[a-z0-9]` collapses to one dash, leading and trailing runs included.
    let mut collapsed = String::new();
    let mut pending = false;
    for character in text.to_lowercase().chars() {
        if character.is_ascii_lowercase() || character.is_ascii_digit() {
            if pending {
                collapsed.push('-');
                pending = false;
            }
            collapsed.push(character);
        } else {
            pending = true;
        }
    }
    if pending {
        collapsed.push('-');
    }
    let slug: String = collapsed
        .trim_matches('-')
        .split('-')
        .filter(|part| !part.is_empty())
        .collect::<Vec<_>>()
        .join("-")
        .chars()
        .take(48)
        .collect();
    let slug = slug.trim_matches('-');
    if slug.is_empty() {
        SLUG_FALLBACK.to_string()
    } else {
        slug.to_string()
    }
}

/// `_evidence_block` — the numbered citation list the prompt opens with.
pub fn evidence_block(sources: &[Source], language: &str) -> String {
    let mut lines: Vec<String> = vec![EVIDENCE_HEADER.get(language).to_string()];
    for (index, source) in sources.iter().enumerate() {
        let origin = if source.origin.is_empty() {
            String::new()
        } else {
            format!(" ({})", source.origin)
        };
        lines.push(format!("{}. {}{origin}", index + 1, source.title));
        let excerpt = pytext::strip(&source.excerpt);
        if !excerpt.is_empty() {
            let suffix = if source.truncated { " …" } else { "" };
            lines.push(format!("   {excerpt}{suffix}"));
        }
    }
    lines.join("\n")
}

/// `_guard(language)`.
pub fn guard(language: &str) -> &'static str {
    GUARD.get(language)
}

/// `actions_for(...)` — the whole answer, in Python's key order.
pub fn actions_for(question: &str, language: &str, resolved: &Resolved) -> Json {
    let language = normalize_language(language);
    let question_text = pytext::strip(question);
    let missing = Json::strings(resolved.missing.clone());
    if resolved.sources.is_empty() {
        return Json::Object(vec![
            ("sources", Json::Array(Vec::new())),
            ("missing", missing),
            ("actions", Json::Array(Vec::new())),
            ("reason", Json::string(NO_EVIDENCE_REASON.get(language))),
        ]);
    }

    let evidence = evidence_block(&resolved.sources, language);
    let guard = guard(language);
    let stem = slugify(&question_text);
    let source_ids: Vec<String> = resolved
        .sources
        .iter()
        .map(|source| source.id.clone())
        .collect();
    let question_line = if question_text.is_empty() {
        String::new()
    } else {
        format!("{}{question_text}", QUESTION_LABEL.get(language))
    };

    let mut actions: Vec<Json> = Vec::new();
    for spec in EVIDENCE_ACTIONS {
        let path = if spec.extension.is_empty() {
            String::new()
        } else {
            format!("{stem}{}", spec.extension)
        };
        let instruction = spec.instruction.get(language).replace("{path}", &path);
        let prompt = [
            evidence.as_str(),
            instruction.as_str(),
            guard,
            question_line.as_str(),
        ]
        .into_iter()
        .filter(|part| !part.is_empty())
        .collect::<Vec<_>>()
        .join("\n\n");
        let mut action = vec![
            ("id", Json::string(spec.id)),
            ("kind", Json::string(spec.kind)),
            ("label", spec.label.to_json()),
            ("prompt", Json::string(prompt)),
            ("source_ids", Json::strings(source_ids.clone())),
        ];
        if !path.is_empty() {
            action.push(("suggested_path", Json::string(path)));
        }
        actions.push(Json::Object(action));
    }

    Json::Object(vec![
        (
            "sources",
            Json::Array(resolved.sources.iter().map(Source::to_json).collect()),
        ),
        ("missing", missing),
        ("actions", Json::Array(actions)),
        ("reason", Json::string("")),
    ])
}
