//! Calibrated field-weighted Okapi BM25F scoring and scoped corpus loader.

use std::collections::HashMap;

use lattice_core::{clean_text, filter_scoped_nodes, safe_loads, CoreError, NodeRow};
use rusqlite::Connection;
use serde_json::Value;

use crate::service::Scope;

use super::tokenizer::{
    extract_markdown_headings, extract_query_terms, extract_wikilinks_and_tags, QueryTerm,
};

/// Okapi BM25F hyperparameters and field weights.
#[derive(Debug, Clone)]
pub struct Bm25fParams {
    /// Term frequency saturation (`k1`, standard 1.2).
    pub k1: f64,
    /// Default document length normalization (`b`, standard 0.75).
    pub b: f64,
    /// Weight for document/node `title`.
    pub title_weight: f64,
    /// Length normalization `b` for `title`.
    pub b_title: f64,
    /// Weight for `tags`, markdown `#` headings, `[[wikilinks]]`, and `concepts`.
    pub tags_headings_weight: f64,
    /// Length normalization `b` for `tags_headings`.
    pub b_tags_headings: f64,
    /// Weight for `summary` / body content.
    pub body_weight: f64,
    /// Length normalization `b` for `body`.
    pub b_body: f64,
    /// Weight for auxiliary metadata fields.
    pub metadata_weight: f64,
    /// Length normalization `b` for `metadata`.
    pub b_metadata: f64,
}

impl Default for Bm25fParams {
    fn default() -> Self {
        Self {
            k1: 1.2,
            b: 0.75,
            title_weight: 3.0,
            b_title: 0.40,
            tags_headings_weight: 2.5,
            b_tags_headings: 0.45,
            body_weight: 1.0,
            b_body: 0.75,
            metadata_weight: 0.6,
            b_metadata: 0.50,
        }
    }
}

#[derive(Debug, Clone)]
struct ParsedDocFields {
    title_tf: HashMap<String, usize>,
    title_len: usize,
    title_lower: String,
    tags_headings_tf: HashMap<String, usize>,
    tags_headings_len: usize,
    tags_headings_lower: String,
    body_tf: HashMap<String, usize>,
    body_len: usize,
    body_lower: String,
    meta_tf: HashMap<String, usize>,
    meta_len: usize,
    wikilinks: Vec<String>,
    tags: Vec<String>,
}

fn count_terms(text: &str) -> (HashMap<String, usize>, usize) {
    let terms = extract_query_terms(text);
    let mut tf = HashMap::new();
    let mut primary_len = 0usize;
    for qt in terms {
        if !qt.is_subword_or_bigram {
            primary_len += 1;
        }
        *tf.entry(qt.token).or_insert(0) += 1;
    }
    (tf, primary_len.max(1))
}

fn extract_doc_fields(row: &NodeRow) -> ParsedDocFields {
    let meta = safe_loads(row.metadata_json.as_deref());
    let title_str = row.title.as_deref().unwrap_or_default();
    let summary_str = row.summary.as_deref().unwrap_or_default();
    let meta_json_str = row.metadata_json.as_deref().unwrap_or_default();
    let node_type_str = row.node_type.as_deref().unwrap_or_default();

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

    let tags_headings_str = tags_headings_parts.join(" ");
    let body_str = body_parts.join("\n");
    let meta_str = format!("{node_type_str} {meta_json_str}");

    let (title_tf, title_len) = count_terms(title_str);
    let (tags_headings_tf, tags_headings_len) = count_terms(&tags_headings_str);
    let (body_tf, body_len) = count_terms(&body_str);
    let (meta_tf, meta_len) = count_terms(&meta_str);

    ParsedDocFields {
        title_tf,
        title_len,
        title_lower: title_str.to_lowercase(),
        tags_headings_tf,
        tags_headings_len,
        tags_headings_lower: tags_headings_str.to_lowercase(),
        body_tf,
        body_len,
        body_lower: body_str.to_lowercase(),
        meta_tf,
        meta_len,
        wikilinks,
        tags,
    }
}

fn node_type_multiplier(node_type: &str) -> f64 {
    match node_type {
        "Decision" => 1.12,
        "Document" | "Concept" | "Note" => 1.08,
        "Topic" | "Entity" | "Artifact" => 1.05,
        _ => 1.0,
    }
}

/// Detailed BM25F scoring breakdown for one node.
#[derive(Debug, Clone)]
pub struct Bm25fScoredNode {
    pub node: NodeRow,
    pub bm25_score: f64,
    pub matched_terms: Vec<String>,
    pub matched_fields: Vec<String>,
    pub wikilinks: Vec<String>,
    pub tags: Vec<String>,
}

/// Score a slice of `NodeRow` documents against `query` using calibrated field-weighted Okapi BM25F.
pub fn bm25f_score_nodes(
    rows: &[NodeRow],
    query: &str,
    params: &Bm25fParams,
) -> Vec<Bm25fScoredNode> {
    let raw_query_terms = extract_query_terms(query);
    if raw_query_terms.is_empty() || rows.is_empty() {
        return Vec::new();
    }

    // Deduplicate query terms while accumulating query term frequency boost
    let mut unique_qterms: Vec<QueryTerm> = Vec::new();
    for qt in raw_query_terms {
        if let Some(existing) = unique_qterms.iter_mut().find(|e| e.token == qt.token) {
            existing.boost = existing.boost.max(qt.boost) + 0.15 * qt.boost;
        } else {
            unique_qterms.push(qt);
        }
    }

    let docs: Vec<ParsedDocFields> = rows.iter().map(extract_doc_fields).collect();
    let n_docs = docs.len() as f64;

    let avgdl_title = (docs.iter().map(|d| d.title_len).sum::<usize>() as f64 / n_docs).max(1.0);
    let avgdl_tags =
        (docs.iter().map(|d| d.tags_headings_len).sum::<usize>() as f64 / n_docs).max(1.0);
    let avgdl_body = (docs.iter().map(|d| d.body_len).sum::<usize>() as f64 / n_docs).max(1.0);
    let avgdl_meta = (docs.iter().map(|d| d.meta_len).sum::<usize>() as f64 / n_docs).max(1.0);

    // Compute document frequency (df) and Robertson-Spärck Jones IDF for each query term
    let mut idf_map: HashMap<String, f64> = HashMap::with_capacity(unique_qterms.len());
    for qt in &unique_qterms {
        let mut df = 0usize;
        for doc in &docs {
            if doc.title_tf.contains_key(&qt.token)
                || doc.tags_headings_tf.contains_key(&qt.token)
                || doc.body_tf.contains_key(&qt.token)
                || doc.meta_tf.contains_key(&qt.token)
            {
                df += 1;
            }
        }
        if df > 0 {
            let df_f = df as f64;
            let idf = (1.0 + (n_docs - df_f + 0.5) / (df_f + 0.5)).ln();
            idf_map.insert(qt.token.clone(), idf.max(0.10));
        }
    }

    let primary_stems: Vec<&str> = unique_qterms
        .iter()
        .filter(|qt| !qt.is_subword_or_bigram)
        .map(|qt| qt.token.as_str())
        .collect();
    let cleaned_query_phrase = clean_text(query).to_lowercase();

    let mut scored: Vec<Bm25fScoredNode> = Vec::new();
    for (row, doc) in rows.iter().zip(docs) {
        let mut score = 0.0f64;
        let mut matched_terms: Vec<String> = Vec::new();
        let mut title_hit = false;
        let mut tags_hit = false;
        let mut body_hit = false;
        let mut meta_hit = false;

        let norm_title =
            (1.0 - params.b_title) + params.b_title * (doc.title_len as f64 / avgdl_title);
        let norm_tags = (1.0 - params.b_tags_headings)
            + params.b_tags_headings * (doc.tags_headings_len as f64 / avgdl_tags);
        let norm_body = (1.0 - params.b_body) + params.b_body * (doc.body_len as f64 / avgdl_body);
        let norm_meta =
            (1.0 - params.b_metadata) + params.b_metadata * (doc.meta_len as f64 / avgdl_meta);

        for qt in &unique_qterms {
            let Some(&idf) = idf_map.get(&qt.token) else {
                continue;
            };
            let tf_title = *doc.title_tf.get(&qt.token).unwrap_or(&0) as f64;
            let tf_tags = *doc.tags_headings_tf.get(&qt.token).unwrap_or(&0) as f64;
            let tf_body = *doc.body_tf.get(&qt.token).unwrap_or(&0) as f64;
            let tf_meta = *doc.meta_tf.get(&qt.token).unwrap_or(&0) as f64;

            let pseudo_tf = (params.title_weight * tf_title / norm_title)
                + (params.tags_headings_weight * tf_tags / norm_tags)
                + (params.body_weight * tf_body / norm_body)
                + (params.metadata_weight * tf_meta / norm_meta);

            if pseudo_tf > 0.0 {
                let saturated = (pseudo_tf * (params.k1 + 1.0)) / (pseudo_tf + params.k1);
                score += qt.boost * idf * saturated;
                if !qt.is_subword_or_bigram && !matched_terms.contains(&qt.token) {
                    matched_terms.push(qt.token.clone());
                }
                if tf_title > 0.0 {
                    title_hit = true;
                }
                if tf_tags > 0.0 {
                    tags_hit = true;
                }
                if tf_body > 0.0 {
                    body_hit = true;
                }
                if tf_meta > 0.0 {
                    meta_hit = true;
                }
            }
        }

        if score <= 0.0 {
            continue;
        }

        // Exact phrase & adjacent query term proximity bonus
        if cleaned_query_phrase.chars().count() >= 2 {
            if doc.title_lower.contains(&cleaned_query_phrase) {
                score *= 1.28;
                title_hit = true;
            } else if doc.tags_headings_lower.contains(&cleaned_query_phrase) {
                score *= 1.18;
                tags_hit = true;
            } else if doc.body_lower.contains(&cleaned_query_phrase) {
                score *= 1.10;
                body_hit = true;
            }
        }
        if primary_stems.len() >= 2 {
            let matched_primary = primary_stems
                .iter()
                .filter(|s| matched_terms.iter().any(|m| m == *s))
                .count();
            if matched_primary == primary_stems.len() {
                score *= 1.15;
            }
        }

        score *= node_type_multiplier(row.node_type.as_deref().unwrap_or_default());

        let mut matched_fields = Vec::new();
        if title_hit {
            matched_fields.push("title".to_string());
        }
        if tags_hit {
            matched_fields.push("tags_headings".to_string());
        }
        if body_hit {
            matched_fields.push("body".to_string());
        }
        if meta_hit {
            matched_fields.push("metadata".to_string());
        }

        scored.push(Bm25fScoredNode {
            node: row.clone(),
            bm25_score: score,
            matched_terms,
            matched_fields,
            wikilinks: doc.wikilinks,
            tags: doc.tags,
        });
    }

    scored.sort_by(|a, b| {
        b.bm25_score
            .total_cmp(&a.bm25_score)
            .then_with(|| b.node.updated_at.cmp(&a.node.updated_at))
            .then_with(|| a.node.id.cmp(&b.node.id))
    });
    scored
}

pub(crate) fn row_to_node(row: &rusqlite::Row<'_>) -> rusqlite::Result<NodeRow> {
    Ok(NodeRow {
        id: row.get("id")?,
        node_type: row.get("type")?,
        title: row.get("title")?,
        summary: row.get("summary")?,
        metadata_json: row.get("metadata_json")?,
        updated_at: row.get("updated_at")?,
    })
}

/// Load scoped candidate nodes from SQLite `nodes` table for BM25F scoring.
pub fn load_scoped_corpus(
    conn: &Connection,
    scope: &Scope,
    max_rows: i64,
) -> Result<Vec<NodeRow>, CoreError> {
    let cap = max_rows.clamp(100, 20_000);
    let mut stmt = conn.prepare(
        "SELECT id, type, title, summary, metadata_json, updated_at
         FROM nodes
         ORDER BY updated_at DESC
         LIMIT ?1",
    )?;
    let iter = stmt.query_map([cap], row_to_node)?;
    let mut rows = Vec::new();
    for item in iter {
        rows.push(item?);
    }
    filter_scoped_nodes(
        conn,
        rows,
        scope.allowed_workspaces.as_ref(),
        scope.include_legacy_global,
        |r| r.id.clone(),
    )
}

/// Run field-weighted Okapi BM25F search over the scoped Knowledge Graph nodes.
pub fn bm25f_search(
    conn: &Connection,
    query: &str,
    limit: i64,
    scope: &Scope,
    params: &Bm25fParams,
) -> Result<Vec<Bm25fScoredNode>, CoreError> {
    let clean = clean_text(query);
    if clean.is_empty() {
        return Ok(Vec::new());
    }
    let rows = load_scoped_corpus(conn, scope, 5_000)?;
    let mut scored = bm25f_score_nodes(&rows, &clean, params);
    let take = limit.max(1) as usize;
    scored.truncate(take);
    Ok(scored)
}
