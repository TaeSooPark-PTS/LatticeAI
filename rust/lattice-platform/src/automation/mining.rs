//! Deterministic local question mining.
//!
//! Reads user turns from `conversation_messages`, normalizes them (trim,
//! lowercase, strip punctuation, Hangul kept), and clusters by exact-normalized
//! match or cheap token-overlap. No LLM. A cluster with `count >= 2` is a
//! pattern; each pattern yields a suggestion with a stable id so
//! `/api/automation/install` can find it.

use std::collections::BTreeSet;
use std::path::Path;

use rusqlite::Connection;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

use crate::review_queue::{into_value, now_iso};
use lattice_auth::OrderedMap;

const MIN_PATTERN_COUNT: i64 = 2;
const MAX_HISTORY: i64 = 4000;
const TOKEN_OVERLAP: f64 = 0.6;
const MIN_CHARS: usize = 6;

/// One mined recurring-question pattern.
#[derive(Debug, Clone)]
pub(crate) struct Pattern {
    pub(crate) id: String,
    pub(crate) question: String,
    pub(crate) count: i64,
    pub(crate) last_seen: String,
    pub(crate) evidence: Vec<String>,
}

/// One installable suggestion derived from a [`Pattern`].
#[derive(Debug, Clone)]
pub(crate) struct Suggestion {
    pub(crate) id: String,
    pub(crate) kind: &'static str,
    pub(crate) title: String,
    pub(crate) pattern_id: String,
    pub(crate) count: i64,
}

/// The mining pass: how many user turns we actually read, plus clusters.
#[derive(Debug, Clone)]
pub(crate) struct Mining {
    pub(crate) questions_scanned: i64,
    pub(crate) patterns: Vec<Pattern>,
}

impl Mining {
    fn empty() -> Self {
        Self {
            questions_scanned: 0,
            patterns: Vec::new(),
        }
    }

    pub(crate) fn suggestions(&self) -> Vec<Suggestion> {
        self.patterns
            .iter()
            .map(|pattern| Suggestion {
                id: stable_id("sug-q", &pattern.id),
                kind: "recurring_question",
                title: pattern.question.clone(),
                pattern_id: pattern.id.clone(),
                count: pattern.count,
            })
            .collect()
    }

    pub(crate) fn find_suggestion(&self, id: &str) -> Option<Suggestion> {
        self.suggestions()
            .into_iter()
            .find(|suggestion| suggestion.id == id)
    }

    pub(crate) fn patterns_body(&self) -> OrderedMap {
        let patterns: Vec<Value> = self
            .patterns
            .iter()
            .map(|pattern| {
                json!({
                    "id": pattern.id,
                    "question": pattern.question,
                    "count": pattern.count,
                    "last_seen": pattern.last_seen,
                    "evidence": pattern.evidence,
                })
            })
            .collect();
        let mut body = OrderedMap::new();
        body.insert("questions_scanned", json!(self.questions_scanned));
        body.insert("patterns", Value::Array(patterns));
        body.insert("generated_at", json!(now_iso()));
        body
    }

    pub(crate) fn suggestions_body(&self) -> OrderedMap {
        let items: Vec<Value> = self
            .suggestions()
            .into_iter()
            .map(|suggestion| {
                json!({
                    "id": suggestion.id,
                    "kind": suggestion.kind,
                    "title": suggestion.title,
                    "pattern_id": suggestion.pattern_id,
                    "count": suggestion.count,
                })
            })
            .collect();
        let mut quality = OrderedMap::new();
        quality.insert("min_confidence", json!(0.35));
        quality.insert("low_confidence_threshold", json!(0.5));
        quality.insert("suppressed_low_confidence", json!(0));
        quality.insert("suppressed_duplicates", json!(0));
        let mut consent = OrderedMap::new();
        consent.insert("default_state", json!("draft_disabled"));
        consent.insert("local_only", json!(true));
        consent.insert("external_actions", json!(false));
        consent.insert("requires_user_enable", json!(true));
        consent.insert("review_before_run", json!(true));
        let mut body = OrderedMap::new();
        body.insert("suggestions", Value::Array(items));
        body.insert("questions_scanned", json!(self.questions_scanned));
        body.insert("quality", into_value(quality));
        body.insert("consent", into_value(consent));
        body.insert("generated_at", json!(now_iso()));
        body
    }
}

/// Mine the Brain sqlite under `data_dir`. Missing file or table → empty, scanned 0.
pub(crate) fn mine(data_dir: &Path, workspace_id: Option<&str>) -> Mining {
    let path = data_dir.join(lattice_core::DB_FILE_NAME);
    if !path.exists() {
        return Mining::empty();
    }
    let Ok(conn) = lattice_core::open_read_only(&path) else {
        return Mining::empty();
    };
    mine_conn(&conn, workspace_id)
}

pub(crate) fn mine_conn(conn: &Connection, workspace_id: Option<&str>) -> Mining {
    let turns = match load_user_turns(conn, workspace_id) {
        Ok(turns) => turns,
        Err(_) => return Mining::empty(),
    };
    let questions_scanned = turns.len() as i64;
    let mut clusters: Vec<Cluster> = Vec::new();
    for turn in &turns {
        let normalized = normalize(&turn.content);
        if normalized.is_empty() {
            continue;
        }
        let tokens = tokens_of(&normalized);
        if tokens.len() < 2 && !looks_like_question(&turn.content) {
            continue;
        }
        if let Some(cluster) = clusters.iter_mut().find(|cluster| {
            cluster.normalized == normalized || token_overlap(&cluster.tokens, &tokens)
        }) {
            cluster.count += 1;
            if turn.timestamp >= cluster.last_seen {
                cluster.last_seen = turn.timestamp.clone();
                cluster.question = turn.content.clone();
            }
            if !cluster.evidence.contains(&turn.content) {
                cluster.evidence.push(turn.content.clone());
            }
            cluster.tokens.extend(tokens);
        } else {
            clusters.push(Cluster {
                normalized,
                tokens,
                question: turn.content.clone(),
                count: 1,
                last_seen: turn.timestamp.clone(),
                evidence: vec![turn.content.clone()],
            });
        }
    }
    let mut recurring: Vec<&Cluster> = clusters
        .iter()
        .filter(|cluster| cluster.count >= MIN_PATTERN_COUNT)
        .collect();
    recurring.sort_by(|left, right| {
        (right.count, right.last_seen.as_str()).cmp(&(left.count, left.last_seen.as_str()))
    });
    let patterns = recurring
        .into_iter()
        .take(20)
        .map(|cluster| {
            let seed = cluster.normalized.clone();
            Pattern {
                id: stable_id("pat", &seed),
                question: cluster.question.clone(),
                count: cluster.count,
                last_seen: cluster.last_seen.clone(),
                evidence: cluster.evidence.iter().take(5).cloned().collect(),
            }
        })
        .collect();
    Mining {
        questions_scanned,
        patterns,
    }
}

struct Turn {
    content: String,
    timestamp: String,
}

struct Cluster {
    normalized: String,
    tokens: BTreeSet<String>,
    question: String,
    count: i64,
    last_seen: String,
    evidence: Vec<String>,
}

fn load_user_turns(
    conn: &Connection,
    workspace_id: Option<&str>,
) -> Result<Vec<Turn>, rusqlite::Error> {
    let sql = "SELECT content, timestamp, workspace_id FROM conversation_messages \
         WHERE role = 'user' ORDER BY timestamp ASC, id ASC LIMIT ?";
    let mut stmt = match conn.prepare(sql) {
        Ok(stmt) => stmt,
        Err(error) if error.to_string().contains("no such table") => return Ok(Vec::new()),
        Err(error) => return Err(error),
    };
    let rows = stmt.query_map([MAX_HISTORY], |row| {
        Ok((
            row.get::<_, String>(0)?,
            row.get::<_, String>(1)?,
            row.get::<_, Option<String>>(2)?,
        ))
    })?;
    let mut turns = Vec::new();
    for row in rows {
        let (content, timestamp, row_ws) = row?;
        if !workspace_visible(workspace_id, row_ws.as_deref()) {
            continue;
        }
        let content = content.trim().to_string();
        if content.is_empty() || content.starts_with('/') || content.chars().count() < MIN_CHARS {
            continue;
        }
        turns.push(Turn { content, timestamp });
    }
    Ok(turns)
}

fn workspace_visible(scope: Option<&str>, row: Option<&str>) -> bool {
    let Some(scope) = scope.map(str::trim).filter(|value| !value.is_empty()) else {
        return true;
    };
    match row.map(str::trim).filter(|value| !value.is_empty()) {
        None => scope == "personal",
        Some(workspace) => workspace == scope || (scope == "personal" && workspace.is_empty()),
    }
}

/// Trim, lowercase, drop punctuation; Hangul syllables and alphanumerics stay.
pub(crate) fn normalize(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut last_space = false;
    for ch in text.trim().chars() {
        if ch.is_alphanumeric() {
            for lower in ch.to_lowercase() {
                out.push(lower);
            }
            last_space = false;
        } else if ch.is_whitespace() && !last_space && !out.is_empty() {
            out.push(' ');
            last_space = true;
        }
    }
    out.trim().to_string()
}

fn tokens_of(normalized: &str) -> BTreeSet<String> {
    normalized
        .split_whitespace()
        .filter(|token| token.chars().count() > 1)
        .map(str::to_string)
        .collect()
}

fn token_overlap(left: &BTreeSet<String>, right: &BTreeSet<String>) -> bool {
    if left.is_empty() || right.is_empty() {
        return false;
    }
    let intersection = left.intersection(right).count() as f64;
    let union = left.union(right).count() as f64;
    if union == 0.0 {
        return false;
    }
    intersection / union >= TOKEN_OVERLAP
}

fn looks_like_question(text: &str) -> bool {
    let lower = text.to_lowercase();
    const HINTS: &[&str] = &[
        "?",
        "어때",
        "뭐야",
        "뭐가",
        "뭘까",
        "알려줘",
        "보여줘",
        "정리해",
        "요약해",
        "해줘",
        "what",
        "how",
        "why",
        "when",
        "where",
        "status",
        "summar",
        "remind",
        "list",
        "show me",
        "tell me",
    ];
    HINTS.iter().any(|hint| lower.contains(hint))
}

pub(crate) fn stable_id(prefix: &str, seed: &str) -> String {
    let digest = Sha256::digest(seed.as_bytes());
    let hex: String = digest.iter().map(|byte| format!("{byte:02x}")).collect();
    format!("{prefix}-{}", &hex[..10])
}

#[cfg(test)]
mod tests {
    use super::*;
    use rusqlite::Connection;

    fn conn_with(turns: &[(&str, &str, Option<&str>)]) -> Connection {
        let conn = Connection::open_in_memory().expect("mem");
        conn.execute_batch(
            "CREATE TABLE conversation_messages (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               message_hash TEXT NOT NULL UNIQUE,
               conversation_id TEXT,
               role TEXT NOT NULL,
               content TEXT NOT NULL,
               user_email TEXT,
               user_nickname TEXT,
               source TEXT,
               timestamp TEXT NOT NULL,
               metadata_json TEXT NOT NULL DEFAULT '{}',
               workspace_id TEXT,
               organization_id TEXT
             );",
        )
        .expect("ddl");
        for (index, (content, ts, ws)) in turns.iter().enumerate() {
            conn.execute(
                "INSERT INTO conversation_messages
                 (message_hash, conversation_id, role, content, timestamp, workspace_id)
                 VALUES (?, 'c1', 'user', ?, ?, ?)",
                rusqlite::params![format!("h{index}"), content, ts, ws],
            )
            .expect("insert");
        }
        conn
    }

    #[test]
    fn normalize_strips_punctuation_and_keeps_hangul() {
        assert_eq!(
            normalize("  오늘 프로젝트 status?? "),
            "오늘 프로젝트 status"
        );
        assert_eq!(
            normalize("How's the weekly review?"),
            "hows the weekly review"
        );
    }

    #[test]
    fn exact_normalized_and_overlap_cluster_together() {
        let conn = conn_with(&[
            ("오늘 프로젝트 status 알려줘?", "2026-01-01T00:00:00", None),
            ("오늘 프로젝트 status 알려줘??", "2026-01-02T00:00:00", None),
            (
                "오늘 프로젝트 status 좀 알려줘",
                "2026-01-03T00:00:00",
                None,
            ),
            ("완전히 다른 질문입니다 맞나요", "2026-01-04T00:00:00", None),
        ]);
        let mined = mine_conn(&conn, Some("personal"));
        assert_eq!(mined.questions_scanned, 4);
        assert_eq!(mined.patterns.len(), 1, "{:?}", mined.patterns);
        assert_eq!(mined.patterns[0].count, 3);
        assert_eq!(mined.patterns[0].evidence.len(), 3);
        assert!(!mined.suggestions().is_empty());
        let suggestion = &mined.suggestions()[0];
        assert!(mined.find_suggestion(&suggestion.id).is_some());
    }

    #[test]
    fn a_single_question_is_not_a_pattern_but_is_scanned() {
        let conn = conn_with(&[("오늘 뭐 했어?", "2026-01-01T00:00:00", None)]);
        let mined = mine_conn(&conn, Some("personal"));
        assert_eq!(mined.questions_scanned, 1);
        assert!(mined.patterns.is_empty());
    }

    #[test]
    fn missing_table_is_empty_not_an_error() {
        let conn = Connection::open_in_memory().expect("mem");
        let mined = mine_conn(&conn, None);
        assert_eq!(mined.questions_scanned, 0);
        assert!(mined.patterns.is_empty());
    }

    #[test]
    fn stable_ids_are_deterministic() {
        assert_eq!(stable_id("pat", "hello"), stable_id("pat", "hello"));
        assert_ne!(stable_id("pat", "hello"), stable_id("sug-q", "hello"));
    }
}
