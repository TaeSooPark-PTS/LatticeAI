//! Subword embedding cache, memory footprint accounting, and edge-adjacency sync
//! methods on [`super::Bm25SidecarIndex`].

use std::collections::HashMap;

use lattice_core::{CoreError, FallbackTextEmbedder, NodeRow};
use rusqlite::Connection;

use super::{
    conn_sync_token, detect_edge_columns, Bm25SidecarIndex, CachedEdge, PostingEntry, SidecarDoc,
    MAX_CACHED_EDGES,
};

fn compute_doc_subword_embedding(embedder: &FallbackTextEmbedder, row: &NodeRow) -> Vec<f32> {
    let doc_text = format!(
        "{} {}",
        row.title.as_deref().unwrap_or_default(),
        row.summary.as_deref().unwrap_or_default()
    );
    if doc_text.trim().is_empty() {
        return Vec::new();
    }
    let vec_f64 = embedder.embed(&doc_text);
    if vec_f64.iter().all(|&v| v == 0.0) {
        Vec::new()
    } else {
        vec_f64.into_iter().map(|v| v as f32).collect()
    }
}

fn dot_f32_f64(doc_vec: &[f32], query_vec: &[f64]) -> f64 {
    if doc_vec.len() != query_vec.len() || doc_vec.is_empty() {
        return 0.0;
    }
    doc_vec
        .iter()
        .zip(query_vec.iter())
        .map(|(&d, &q)| f64::from(d) * q)
        .sum()
}

impl Bm25SidecarIndex {
    /// Clone only the requested `NodeRow` entries by id (O(|ids|), avoiding full-corpus clone).
    pub fn clone_nodes_by_ids(&self, ids: &[String]) -> HashMap<String, NodeRow> {
        let mut out = HashMap::with_capacity(ids.len());
        for id in ids {
            if let Some(doc) = self.get_doc(id) {
                out.insert(id.clone(), doc.node.clone());
            }
        }
        out
    }

    /// True if all active documents already have cached subword embeddings.
    pub fn subword_ready(&self) -> bool {
        self.docs
            .iter()
            .flatten()
            .all(|doc| doc.subword_embedding.is_some())
    }

    /// Precompute subword fallback embeddings ONLY for nodes missing one (incremental on change).
    /// Returns the number of newly computed node embeddings.
    pub fn ensure_subword_embeddings(&mut self) -> usize {
        if self.subword_ready() {
            return 0;
        }
        let embedder = FallbackTextEmbedder::default();
        let mut computed = 0usize;
        for doc_opt in &mut self.docs {
            let Some(doc) = doc_opt.as_mut() else {
                continue;
            };
            if doc.subword_embedding.is_none() {
                doc.subword_embedding = Some(compute_doc_subword_embedding(&embedder, &doc.node));
                self.stats.subword_computed_nodes += 1;
                computed += 1;
            }
        }
        computed
    }

    /// Compute subword cosine similarities only for the specified seed `ids`.
    pub fn subword_similarity_for_ids(
        &self,
        query_vec: &[f64],
        ids: &[String],
    ) -> HashMap<String, f64> {
        let mut out = HashMap::new();
        if query_vec.iter().all(|&v| v == 0.0) {
            return out;
        }
        for id in ids {
            let Some(doc) = self.get_doc(id) else {
                continue;
            };
            if let Some(d_vec) = doc.subword_embedding.as_deref() {
                let sim = dot_f32_f64(d_vec, query_vec);
                if sim > 0.0 {
                    out.insert(id.clone(), sim);
                }
            }
        }
        out
    }

    /// Score cached subword embeddings without cloning the corpus; clones only the top `limit` hits.
    pub fn score_subword_query(
        &self,
        query_vec: &[f64],
        min_sim: f64,
        limit: usize,
        include_historical: bool,
    ) -> Vec<(NodeRow, f64)> {
        if limit == 0 || query_vec.iter().all(|&v| v == 0.0) {
            return Vec::new();
        }
        let mut scored_idxs: Vec<(usize, f64)> = Vec::new();
        for (idx, doc_opt) in self.docs.iter().enumerate() {
            let Some(doc) = doc_opt.as_ref() else {
                continue;
            };
            if !include_historical && doc.lifecycle_status.is_retired() {
                continue;
            }
            let Some(d_vec) = doc.subword_embedding.as_deref() else {
                continue;
            };
            let raw_sim = dot_f32_f64(d_vec, query_vec);
            let weighted = raw_sim * doc.lifecycle_status.multiplier();
            if weighted >= min_sim {
                scored_idxs.push((idx, weighted));
            }
        }
        scored_idxs.sort_by(|a, b| b.1.total_cmp(&a.1));
        scored_idxs.truncate(limit);
        scored_idxs
            .into_iter()
            .filter_map(|(idx, score)| self.doc_at(idx).map(|doc| (doc.node.clone(), score)))
            .collect()
    }

    /// Estimate approximate resident memory footprint in bytes for cache eviction accounting.
    pub fn estimated_memory_bytes(&self) -> usize {
        let mut bytes = std::mem::size_of::<Self>();
        bytes += self.docs.capacity() * std::mem::size_of::<Option<SidecarDoc>>();
        for doc in self.docs.iter().flatten() {
            bytes += doc.node.id.len()
                + doc.node.node_type.as_deref().map_or(0, str::len)
                + doc.node.title.as_deref().map_or(0, str::len)
                + doc.node.summary.as_deref().map_or(0, str::len)
                + doc.node.metadata_json.as_deref().map_or(0, str::len)
                + doc.updated_at.len()
                + doc.title_lower.len()
                + doc.tags_headings_lower.len()
                + doc.body_lower.len()
                + doc.title_norm.len();
            bytes += doc.all_terms.iter().map(|t| t.len() + 24).sum::<usize>();
            bytes += doc.wikilinks.iter().map(|t| t.len() + 24).sum::<usize>();
            bytes += doc.tags.iter().map(|t| t.len() + 24).sum::<usize>();
            let tf_entries =
                doc.tf_title.len() + doc.tf_tags.len() + doc.tf_body.len() + doc.tf_meta.len();
            bytes += tf_entries * 48;
            if let Some(emb) = &doc.subword_embedding {
                bytes += emb.capacity() * std::mem::size_of::<f32>();
            }
        }
        for (term, list) in &self.postings {
            bytes += term.len() + 32 + list.capacity() * std::mem::size_of::<PostingEntry>();
            bytes += list.iter().map(|e| e.node_id.len()).sum::<usize>();
        }
        bytes += self.edges.capacity() * std::mem::size_of::<CachedEdge>();
        for edge in &self.edges {
            bytes += edge.src.len() + edge.dst.len() + edge.rel.len();
        }
        bytes
    }

    /// Sync in-memory graph edge adjacency cache from SQLite `edges` when changed.
    pub fn sync_edges_from_conn(&mut self, conn: &Connection) -> Result<(), CoreError> {
        let token = conn_sync_token(conn, 0, false)?;
        if self.edges_sync_token.as_ref() == Some(&token) {
            return Ok(());
        }
        self.edges.clear();
        self.edge_adj.clear();
        self.edges_too_large = false;
        self.edge_layout = detect_edge_columns(conn);

        let Some(layout) = self.edge_layout else {
            self.edges_sync_token = Some(token);
            return Ok(());
        };
        let count: i64 = conn
            .query_row("SELECT COUNT(*) FROM edges", [], |r| r.get(0))
            .unwrap_or(0);
        if count as usize > MAX_CACHED_EDGES {
            self.edges_too_large = true;
            self.edges_sync_token = Some(token);
            return Ok(());
        }
        let sql = format!(
            "SELECT {}, {}, {}, weight FROM edges",
            layout.src_col, layout.dst_col, layout.rel_col
        );
        let mut stmt = conn.prepare(&sql)?;
        let rows = stmt.query_map([], |row| {
            let src: String = row.get(0)?;
            let dst: String = row.get(1)?;
            let rel: Option<String> = row.get(2)?;
            let weight: Option<f64> = row.get(3)?;
            Ok(CachedEdge {
                src,
                dst,
                rel: rel.unwrap_or_else(|| "RELATES_TO".to_string()),
                weight: weight.unwrap_or(1.0).clamp(0.05, 5.0),
            })
        })?;
        for edge_res in rows {
            let edge = edge_res?;
            let idx = self.edges.len();
            self.edge_adj.entry(edge.src.clone()).or_default().push(idx);
            if edge.dst != edge.src {
                self.edge_adj.entry(edge.dst.clone()).or_default().push(idx);
            }
            self.edges.push(edge);
        }
        self.edges_sync_token = Some(token);
        Ok(())
    }
}
