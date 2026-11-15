//! Pre-tokenized BM25F field statistics, CJK/Korean/subword tokenizer, and
//! `bm25_node_stats` SQLite sidecar persistence helpers.
//!
//! Shared by both `lattice-core` (`GraphWriter` node upserts) and
//! `lattice-retrieval` (`Bm25SidecarIndex` + GraphRAG) so tokenization and
//! field-length accounting never drift.

use std::collections::{BTreeMap, HashMap};

use rusqlite::{Connection, ToSql, Transaction};
use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::db::CoreError;
use crate::pytext::safe_loads;
use crate::read::NodeRow;

const KO_PARTICLES: [&str; 28] = [
    "에서", "으로", "에게", "부터", "까지", "처럼", "대한", "관한", "하는", "하고", "하며", "이고",
    "이나", "이라", "에는", "에도", "은", "는", "이", "가", "을", "를", "의", "에", "로", "와",
    "과", "도",
];

const EN_STOPWORDS: [&str; 25] = [
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "is", "it", "of", "on",
    "or", "that", "the", "this", "to", "was", "were", "what", "how", "with",
];

const KO_STOPWORDS: [&str; 12] = [
    "있는",
    "없는",
    "하는",
    "되는",
    "그리고",
    "그러나",
    "따라서",
    "경우",
    "통해",
    "위해",
    "대한",
    "관한",
];

/// DDL for the lightweight pre-tokenized BM25F sidecar table.
pub const BM25_NODE_STATS_DDL: &str = "
    CREATE TABLE IF NOT EXISTS bm25_node_stats (
      node_id TEXT PRIMARY KEY,
      updated_at TEXT NOT NULL,
      dl_title INTEGER NOT NULL,
      dl_tags INTEGER NOT NULL,
      dl_body INTEGER NOT NULL,
      dl_meta INTEGER NOT NULL,
      tf_json TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_bm25_node_stats_updated
      ON bm25_node_stats(updated_at);
";

/// A normalized query/document term with its specificity boost and kind.
#[derive(Debug, Clone, PartialEq)]
pub struct QueryTerm {
    pub token: String,
    pub boost: f64,
    pub is_subword_or_bigram: bool,
}

/// Extracted raw strings + wikilinks/tags for each BM25F field of a node.
#[derive(Debug, Clone, Default)]
pub struct ExtractedDocFieldStrings {
    pub title_str: String,
    pub tags_headings_str: String,
    pub body_str: String,
    pub meta_str: String,
    pub wikilinks: Vec<String>,
    pub tags: Vec<String>,
}

/// JSON-serialized per-field term frequency maps and structural links for one node.
#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq, Eq)]
pub struct Bm25TfPayload {
    #[serde(default)]
    pub tf_title: BTreeMap<String, usize>,
    #[serde(default)]
    pub tf_tags: BTreeMap<String, usize>,
    #[serde(default)]
    pub tf_body: BTreeMap<String, usize>,
    #[serde(default)]
    pub tf_meta: BTreeMap<String, usize>,
    #[serde(default)]
    pub wikilinks: Vec<String>,
    #[serde(default)]
    pub tags: Vec<String>,
}

/// Pre-tokenized BM25F field lengths and term frequencies for one node.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NodeBm25Stats {
    pub node_id: String,
    pub updated_at: String,
    pub dl_title: usize,
    pub dl_tags: usize,
    pub dl_body: usize,
    pub dl_meta: usize,
    pub payload: Bm25TfPayload,
}

pub fn is_hangul(c: char) -> bool {
    ('\u{AC00}'..='\u{D7A3}').contains(&c)
}

pub fn is_cjk(c: char) -> bool {
    ('\u{4E00}'..='\u{9FFF}').contains(&c) || ('\u{3040}'..='\u{30FF}').contains(&c)
}

fn strip_ko_particle(token: &str) -> &str {
    let char_len = token.chars().count();
    for suffix in KO_PARTICLES {
        if let Some(stripped) = token.strip_suffix(suffix) {
            let s_len = suffix.chars().count();
            if char_len >= s_len + 2 {
                return stripped;
            }
        }
    }
    if let Some(stripped) = token.strip_suffix("만") {
        if char_len >= 3 {
            return stripped;
        }
    }
    token
}

fn split_camel_case(text: &str) -> Vec<String> {
    let mut parts = Vec::new();
    let mut current = String::new();
    let chars: Vec<char> = text.chars().collect();
    for i in 0..chars.len() {
        let c = chars[i];
        if c.is_ascii_alphanumeric() {
            if c.is_ascii_uppercase()
                && !current.is_empty()
                && (chars[i - 1].is_ascii_lowercase()
                    || (i + 1 < chars.len() && chars[i + 1].is_ascii_lowercase()))
            {
                if current.len() >= 2 {
                    parts.push(current.to_ascii_lowercase());
                }
                current = String::new();
            }
            current.push(c);
        } else if !current.is_empty() {
            if current.len() >= 2 {
                parts.push(current.to_ascii_lowercase());
            }
            current = String::new();
        }
    }
    if current.len() >= 2 {
        parts.push(current.to_ascii_lowercase());
    }
    parts
}

/// Tokenize text for BM25F indexing and querying, producing primary word/stem tokens
/// AND CJK character bigrams + compound subwords.
pub fn tokenize_bm25_cjk(text: &str) -> Vec<String> {
    extract_query_terms(text)
        .into_iter()
        .map(|qt| qt.token)
        .collect()
}

/// Extract weighted query/document terms (primary words/stems + CJK bigrams + subwords).
pub fn extract_query_terms(text: &str) -> Vec<QueryTerm> {
    if text.trim().is_empty() {
        return Vec::new();
    }
    let camel_parts = split_camel_case(text);
    let lowered = text.to_lowercase();
    let chars: Vec<char> = lowered.chars().collect();
    let mut terms: Vec<QueryTerm> = Vec::new();

    let mut i = 0;
    while i < chars.len() {
        let c = chars[i];
        if c.is_ascii_alphanumeric() {
            let start = i;
            i += 1;
            while i < chars.len()
                && (chars[i].is_ascii_alphanumeric()
                    || matches!(chars[i], '_' | '-' | '.' | '/' | ':'))
            {
                i += 1;
            }
            let raw: String = chars[start..i]
                .iter()
                .collect::<String>()
                .trim_matches(|ch: char| !ch.is_ascii_alphanumeric())
                .to_string();
            if raw.is_empty() {
                continue;
            }
            let subparts: Vec<&str> = raw
                .split(['_', '-', '.', '/', ':'])
                .filter(|s| !s.is_empty())
                .collect();
            if subparts.len() > 1 {
                terms.push(QueryTerm {
                    token: raw.clone(),
                    boost: 1.15,
                    is_subword_or_bigram: false,
                });
            }
            for part in subparts {
                if part.len() < 2 || EN_STOPWORDS.contains(&part) {
                    continue;
                }
                terms.push(QueryTerm {
                    token: part.to_string(),
                    boost: 1.0,
                    is_subword_or_bigram: false,
                });
                if part.len() >= 5 && part.chars().any(|ch| ch.is_ascii_alphabetic()) {
                    let pchars: Vec<char> = part.chars().collect();
                    for j in 0..pchars.len().saturating_sub(3) {
                        let sub: String = pchars[j..j + 4].iter().collect();
                        terms.push(QueryTerm {
                            token: format!("sub:{sub}"),
                            boost: 0.45,
                            is_subword_or_bigram: true,
                        });
                    }
                }
            }
        } else if is_hangul(c) {
            let start = i;
            i += 1;
            while i < chars.len() && is_hangul(chars[i]) {
                i += 1;
            }
            let raw: String = chars[start..i].iter().collect();
            let raw_chars = i - start;
            if raw_chars < 2 || KO_STOPWORDS.contains(&raw.as_str()) {
                continue;
            }
            let stem = strip_ko_particle(&raw);
            terms.push(QueryTerm {
                token: stem.to_string(),
                boost: 1.10,
                is_subword_or_bigram: false,
            });
            if stem != raw {
                terms.push(QueryTerm {
                    token: raw.clone(),
                    boost: 0.85,
                    is_subword_or_bigram: false,
                });
            }
            let stem_chars: Vec<char> = stem.chars().collect();
            if stem_chars.len() >= 2 {
                for j in 0..stem_chars.len() - 1 {
                    let bg: String = stem_chars[j..j + 2].iter().collect();
                    terms.push(QueryTerm {
                        token: format!("ko_bi:{bg}"),
                        boost: 0.65,
                        is_subword_or_bigram: true,
                    });
                }
            }
        } else if is_cjk(c) {
            let start = i;
            i += 1;
            while i < chars.len() && is_cjk(chars[i]) {
                i += 1;
            }
            let raw: String = chars[start..i].iter().collect();
            let cjk_chars: Vec<char> = raw.chars().collect();
            if !raw.is_empty() {
                terms.push(QueryTerm {
                    token: raw,
                    boost: 1.05,
                    is_subword_or_bigram: false,
                });
            }
            if cjk_chars.len() >= 2 {
                for j in 0..cjk_chars.len() - 1 {
                    let bg: String = cjk_chars[j..j + 2].iter().collect();
                    terms.push(QueryTerm {
                        token: format!("cjk_bi:{bg}"),
                        boost: 0.70,
                        is_subword_or_bigram: true,
                    });
                }
            }
        } else {
            i += 1;
        }
    }

    for cp in camel_parts {
        if cp.len() >= 2
            && !EN_STOPWORDS.contains(&cp.as_str())
            && !terms.iter().any(|t| t.token == cp)
        {
            terms.push(QueryTerm {
                token: cp,
                boost: 0.85,
                is_subword_or_bigram: true,
            });
        }
    }

    terms
}

/// Extract wikilinks `[[target]]` and `#hashtags` from markdown/text.
pub fn extract_wikilinks_and_tags(text: &str) -> (Vec<String>, Vec<String>) {
    let mut wikilinks = Vec::new();
    let mut tags = Vec::new();

    let mut rest = text;
    while let Some(open) = rest.find("[[") {
        let after_open = &rest[open + 2..];
        if let Some(close) = after_open.find("]]") {
            let inner = &after_open[..close];
            let target = inner.split('|').next().unwrap_or(inner).trim();
            if !target.is_empty() {
                let norm = target.to_lowercase();
                if !wikilinks.contains(&norm) {
                    wikilinks.push(norm);
                }
            }
            rest = &after_open[close + 2..];
        } else {
            break;
        }
    }

    for line in text.lines() {
        let trimmed = line.trim();
        for token in trimmed.split_whitespace() {
            if let Some(tag) = token.strip_prefix('#') {
                let clean: String = tag
                    .chars()
                    .take_while(|c| {
                        c.is_alphanumeric()
                            || is_hangul(*c)
                            || is_cjk(*c)
                            || matches!(*c, '_' | '-' | '/')
                    })
                    .collect();
                if clean.chars().count() >= 2 {
                    let norm = clean.to_lowercase();
                    if !tags.contains(&norm) {
                        tags.push(norm);
                    }
                }
            }
        }
    }

    (wikilinks, tags)
}

/// Extract markdown headings (`# Heading`, `## Subheading`) from text.
pub fn extract_markdown_headings(text: &str) -> String {
    let mut headings = Vec::new();
    for line in text.lines() {
        let trimmed = line.trim();
        if trimmed.starts_with('#') {
            let without_hashes = trimmed.trim_start_matches('#').trim();
            if !without_hashes.is_empty() {
                headings.push(without_hashes);
            }
        }
    }
    headings.join(" ")
}

/// Extract normalized field texts, wikilinks, and hashtags for one node.
pub fn extract_doc_field_strings(
    node_type: Option<&str>,
    title: Option<&str>,
    summary: Option<&str>,
    metadata_json: Option<&str>,
) -> ExtractedDocFieldStrings {
    let meta = safe_loads(metadata_json);
    let title_str = title.unwrap_or_default();
    let summary_str = summary.unwrap_or_default();
    let meta_json_str = metadata_json.unwrap_or_default();
    let node_type_str = node_type.unwrap_or_default();

    let mut tags_headings_parts: Vec<String> = Vec::new();
    let mut body_parts: Vec<String> = vec![summary_str.to_string()];

    for key in [
        "tags", "concepts", "headings", "keywords", "category", "aliases",
    ] {
        if let Some(val) = meta.get(key) {
            match val {
                Value::Array(arr) => {
                    for item in arr {
                        if let Some(s) = item.as_str() {
                            tags_headings_parts.push(s.to_string());
                        }
                    }
                }
                Value::String(s) => tags_headings_parts.push(s.clone()),
                _ => {}
            }
        }
    }

    for key in ["content", "text", "body", "markdown"] {
        if let Some(Value::String(s)) = meta.get(key) {
            if !s.is_empty() {
                let hd = extract_markdown_headings(s);
                if !hd.is_empty() {
                    tags_headings_parts.push(hd);
                }
                body_parts.push(s.clone());
            }
        }
    }

    let summary_headings = extract_markdown_headings(summary_str);
    if !summary_headings.is_empty() {
        tags_headings_parts.push(summary_headings);
    }

    let combined_text = format!("{title_str} {summary_str} {meta_json_str}");
    let (wikilinks, tags) = extract_wikilinks_and_tags(&combined_text);
    for wl in &wikilinks {
        tags_headings_parts.push(wl.clone());
    }
    for tg in &tags {
        tags_headings_parts.push(tg.clone());
    }

    ExtractedDocFieldStrings {
        title_str: title_str.to_string(),
        tags_headings_str: tags_headings_parts.join(" "),
        body_str: body_parts.join("\n"),
        meta_str: format!("{node_type_str} {meta_json_str}"),
        wikilinks,
        tags,
    }
}

fn count_field_terms(text: &str) -> (BTreeMap<String, usize>, usize) {
    let terms = extract_query_terms(text);
    let mut tf = BTreeMap::new();
    let mut primary_len = 0usize;
    for qt in terms {
        if !qt.is_subword_or_bigram {
            primary_len += 1;
        }
        *tf.entry(qt.token).or_insert(0) += 1;
    }
    (tf, primary_len.max(1))
}

/// Compute pre-tokenized BM25F field lengths and term frequencies for a node.
pub fn compute_node_bm25_stats(
    node_id: &str,
    node_type: Option<&str>,
    title: Option<&str>,
    summary: Option<&str>,
    metadata_json: Option<&str>,
    updated_at: Option<&str>,
) -> (NodeBm25Stats, ExtractedDocFieldStrings) {
    let fields = extract_doc_field_strings(node_type, title, summary, metadata_json);
    let (tf_title, dl_title) = count_field_terms(&fields.title_str);
    let (tf_tags, dl_tags) = count_field_terms(&fields.tags_headings_str);
    let (tf_body, dl_body) = count_field_terms(&fields.body_str);
    let (tf_meta, dl_meta) = count_field_terms(&fields.meta_str);

    let stats = NodeBm25Stats {
        node_id: node_id.to_string(),
        updated_at: updated_at.unwrap_or_default().to_string(),
        dl_title,
        dl_tags,
        dl_body,
        dl_meta,
        payload: Bm25TfPayload {
            tf_title,
            tf_tags,
            tf_body,
            tf_meta,
            wikilinks: fields.wikilinks.clone(),
            tags: fields.tags.clone(),
        },
    };
    (stats, fields)
}

/// Compute pre-tokenized BM25F stats directly from a `NodeRow`.
pub fn compute_node_bm25_stats_from_row(
    row: &NodeRow,
) -> (NodeBm25Stats, ExtractedDocFieldStrings) {
    compute_node_bm25_stats(
        &row.id,
        row.node_type.as_deref(),
        row.title.as_deref(),
        row.summary.as_deref(),
        row.metadata_json.as_deref(),
        row.updated_at.as_deref(),
    )
}

/// Ensure the `bm25_node_stats` sidecar table exists on a writable connection.
pub fn ensure_bm25_node_stats_schema(conn: &Connection) -> Result<(), CoreError> {
    conn.execute_batch(BM25_NODE_STATS_DDL)?;
    Ok(())
}

/// Return true if `bm25_node_stats` exists in `sqlite_master`.
pub fn bm25_node_stats_exists(conn: &Connection) -> bool {
    conn.query_row(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='bm25_node_stats'",
        [],
        |_| Ok(()),
    )
    .is_ok()
}

/// Upsert one node's pre-tokenized BM25F stats inside an active write transaction.
pub fn upsert_bm25_node_stats(
    txn: &Transaction<'_>,
    stats: &NodeBm25Stats,
) -> Result<(), CoreError> {
    let tf_json = serde_json::to_string(&stats.payload).unwrap_or_else(|_| "{}".to_string());
    txn.execute(
        "INSERT INTO bm25_node_stats(\
           node_id, updated_at, dl_title, dl_tags, dl_body, dl_meta, tf_json\
         ) VALUES (?, ?, ?, ?, ?, ?, ?) \
         ON CONFLICT(node_id) DO UPDATE SET \
           updated_at=excluded.updated_at, \
           dl_title=excluded.dl_title, \
           dl_tags=excluded.dl_tags, \
           dl_body=excluded.dl_body, \
           dl_meta=excluded.dl_meta, \
           tf_json=excluded.tf_json",
        rusqlite::params![
            stats.node_id,
            stats.updated_at,
            stats.dl_title as i64,
            stats.dl_tags as i64,
            stats.dl_body as i64,
            stats.dl_meta as i64,
            tf_json,
        ],
    )?;
    Ok(())
}

/// Recompute and upsert `bm25_node_stats` from the current `nodes` row for `node_id`.
pub fn sync_bm25_node_stats_by_id(txn: &Transaction<'_>, node_id: &str) -> Result<(), CoreError> {
    let row: Option<(String, String, Option<String>, String, String)> = txn
        .query_row(
            "SELECT type, title, summary, metadata_json, updated_at FROM nodes WHERE id=?",
            rusqlite::params![node_id],
            |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?)),
        )
        .ok();
    let Some((node_type, title, summary, metadata_json, updated_at)) = row else {
        return Ok(());
    };
    let (stats, _) = compute_node_bm25_stats(
        node_id,
        Some(&node_type),
        Some(&title),
        summary.as_deref(),
        Some(&metadata_json),
        Some(&updated_at),
    );
    upsert_bm25_node_stats(txn, &stats)
}

/// Delete `bm25_node_stats` rows for `ids` if the table exists.
pub fn delete_bm25_node_stats_by_ids(
    txn: &Transaction<'_>,
    ids: &[String],
) -> Result<(), CoreError> {
    if ids.is_empty() || !bm25_node_stats_exists(txn) {
        return Ok(());
    }
    for chunk in ids.chunks(450) {
        let placeholders = vec!["?"; chunk.len()].join(",");
        let params: Vec<&dyn ToSql> = chunk.iter().map(|id| id as &dyn ToSql).collect();
        txn.execute(
            &format!("DELETE FROM bm25_node_stats WHERE node_id IN ({placeholders})"),
            params.as_slice(),
        )?;
    }
    Ok(())
}

/// Load pre-tokenized `bm25_node_stats` rows for `node_ids`.
///
/// Safe on read-only connections and frozen golden DBs lacking `bm25_node_stats`:
/// returns an empty map without errors or schema mutations.
pub fn load_bm25_node_stats(
    conn: &Connection,
    node_ids: &[String],
) -> HashMap<String, NodeBm25Stats> {
    let mut out = HashMap::new();
    if node_ids.is_empty() || !bm25_node_stats_exists(conn) {
        return out;
    }
    for chunk in node_ids.chunks(450) {
        let placeholders = vec!["?"; chunk.len()].join(",");
        let sql = format!(
            "SELECT node_id, updated_at, dl_title, dl_tags, dl_body, dl_meta, tf_json \
             FROM bm25_node_stats WHERE node_id IN ({placeholders})"
        );
        let Ok(mut stmt) = conn.prepare(&sql) else {
            return out;
        };
        let params: Vec<&dyn ToSql> = chunk.iter().map(|id| id as &dyn ToSql).collect();
        let Ok(rows) = stmt.query_map(params.as_slice(), |row| {
            let node_id: String = row.get(0)?;
            let updated_at: String = row.get(1)?;
            let dl_title: i64 = row.get(2)?;
            let dl_tags: i64 = row.get(3)?;
            let dl_body: i64 = row.get(4)?;
            let dl_meta: i64 = row.get(5)?;
            let tf_json: String = row.get(6)?;
            Ok((
                node_id, updated_at, dl_title, dl_tags, dl_body, dl_meta, tf_json,
            ))
        }) else {
            return out;
        };
        for item in rows.flatten() {
            let (node_id, updated_at, dl_title, dl_tags, dl_body, dl_meta, tf_json) = item;
            if let Ok(payload) = serde_json::from_str::<Bm25TfPayload>(&tf_json) {
                out.insert(
                    node_id.clone(),
                    NodeBm25Stats {
                        node_id,
                        updated_at,
                        dl_title: (dl_title.max(1)) as usize,
                        dl_tags: (dl_tags.max(1)) as usize,
                        dl_body: (dl_body.max(1)) as usize,
                        dl_meta: (dl_meta.max(1)) as usize,
                        payload,
                    },
                );
            }
        }
    }
    out
}
