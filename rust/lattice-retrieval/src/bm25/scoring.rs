//! Calibrated field-weighted Okapi BM25F scoring and scoped corpus loader.
//!
//! Backed by [`super::sidecar::Bm25SidecarIndex`] for O(1) field-length &
//! `df(t)` lookup and inverted posting-list candidate evaluation.

use lattice_core::{clean_text, filter_scoped_nodes, CoreError, NodeRow};
use rusqlite::Connection;

use crate::service::Scope;

use super::sidecar::Bm25SidecarIndex;
use super::sidecar_cache::with_bm25_sidecar;

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

pub(crate) fn node_type_multiplier(node_type: &str) -> f64 {
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
    if rows.is_empty() || query.trim().is_empty() {
        return Vec::new();
    }
    Bm25SidecarIndex::from_corpus(rows).score(query, params)
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

/// Run field-weighted Okapi BM25F search over the scoped Knowledge Graph nodes
/// using the cached pre-tokenized [`Bm25SidecarIndex`].
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
    let mut scored = with_bm25_sidecar(conn, scope, 5_000, |sidecar| {
        Ok(sidecar.score(&clean, params))
    })?;
    let take = limit.max(1) as usize;
    scored.truncate(take);
    Ok(scored)
}
