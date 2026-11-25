use std::collections::BTreeSet;

use lattice_core::pytext;
use rusqlite::Connection;
use serde_json::Value;

use super::json::Json;
use crate::memory_api::kg;
use crate::shape::{py_str, truthy};

pub const EXCERPT_CHARS: usize = 600;

pub const MAX_SOURCES: usize = 8;

pub const SLUG_FALLBACK: &str = "evidence-note";

const ORIGIN_KEYS: [&str; 5] = [
    "relative_path",
    "file_path",
    "filename",
    "source_uri",
    "source",
];

#[derive(Debug, Clone, Copy)]
pub struct Phrase {
    pub ko: &'static str,
    pub en: &'static str,
}

impl Phrase {
    pub fn get(self, language: &str) -> &'static str {
        if language == "ko" {
            self.ko
        } else {
            self.en
        }
    }

    pub fn to_json(self) -> Json {
        Json::Object(vec![
            ("ko", Json::string(self.ko)),
            ("en", Json::string(self.en)),
        ])
    }
}

#[derive(Debug, Clone, Copy)]
pub struct ActionSpec {
    pub id: &'static str,
    pub kind: &'static str,
    pub label: Phrase,
    pub instruction: Phrase,
    pub extension: &'static str,
}

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

const EVIDENCE_HEADER: Phrase = Phrase {
    ko: "[근거 자료]",
    en: "[EVIDENCE]",
};

const GUARD: Phrase = Phrase {
    ko: "위 근거 자료에 있는 내용만 사용하세요. 근거에 없는 사실은 지어내지 말고, 근거가 부족하면 '이 부분은 근거가 없습니다'라고 적으세요.",
    en: "Use only the evidence quoted above. Do not invent facts that are not in it; when the evidence does not cover something, say so explicitly.",
};

const QUESTION_LABEL: Phrase = Phrase {
    ko: "원래 질문: ",
    en: "Original question: ",
};

const NO_EVIDENCE_REASON: Phrase = Phrase {
    ko: "근거로 쓸 출처를 찾지 못했습니다.",
    en: "No usable evidence could be resolved.",
};

#[derive(Debug, Clone, PartialEq)]
pub struct Source {
    pub id: String,
    pub title: String,
    pub node_type: String,
    pub origin: String,
    pub excerpt: String,
    pub truncated: bool,
}

impl Source {
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

#[derive(Debug, Clone, Default, PartialEq)]
pub struct Resolved {
    pub sources: Vec<Source>,
    pub missing: Vec<String>,
}

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

fn source_from(record: &Value, node_id: &str) -> Source {
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

fn truthy_field<'a>(record: &'a Value, key: &str) -> Option<&'a Value> {
    record.get(key).filter(|value| truthy(value))
}

pub fn normalize_language(language: &str) -> &'static str {
    let raw = if language.is_empty() { "ko" } else { language };
    if raw.to_lowercase().starts_with("ko") {
        "ko"
    } else {
        "en"
    }
}

pub fn slugify(text: &str) -> String {
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

pub fn guard(language: &str) -> &'static str {
    GUARD.get(language)
}

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
