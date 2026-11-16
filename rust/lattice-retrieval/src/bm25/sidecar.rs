//! Pre-tokenized BM25F Sidecar + Inverted Index + Graph Adjacency Cache.
//!
//! Eliminates per-query full-corpus tokenization and wikilink/tag scanning by
//! maintaining:
//! - O(1) per-node field lengths (`dl_title`, `dl_tags`, `dl_body`, `dl_meta`)
//!   and per-field term frequencies (`tf_*`), synced incrementally by
//!   `(node_id, updated_at, content_hash)` and hydrated from `bm25_node_stats`
//!   when available (while remaining 100% transparent and schema-free on
//!   read-only golden databases).
//! - Inverted posting lists (`term -> Vec<PostingEntry>`), O(1) `df(t)` lookup,
//!   and pre-aggregated `avgdl_f`.
//! - Cached wikilink, hashtag, title, and edge adjacency maps for O(1) / O(deg)
//!   GraphRAG expansion.

use std::collections::{BTreeMap, HashMap, HashSet};
use std::hash::{Hash, Hasher};

use lattice_core::bm25_stats::{
    compute_node_bm25_stats_from_row, extract_doc_field_strings, load_bm25_node_stats,
    NodeBm25Stats,
};
use lattice_core::{CoreError, FallbackTextEmbedder, NodeRow};
use rusqlite::Connection;

use crate::service::Scope;

use super::scoring::{
    extract_meta_from_json, load_scoped_corpus_records, CorpusLoadOptions,
    KnowledgeLifecycleStatus, ScopedNodeRecord,
};

const MAX_CACHED_EDGES: usize = 50_000;

/// One posting entry in the inverted index for a single `(term, node)` pair.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PostingEntry {
    pub doc_idx: usize,
    pub node_id: String,
    pub tf_title: usize,
    pub tf_tags: usize,
    pub tf_body: usize,
    pub tf_meta: usize,
}

/// An undirected/directed graph edge cached in memory for fast 1-hop/2-hop GraphRAG.
#[derive(Debug, Clone)]
pub struct CachedEdge {
    pub src: String,
    pub dst: String,
    pub rel: String,
    pub weight: f64,
}

/// Edge schema column layout (`schema.rs` vs legacy/test `source_id` layout).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct EdgeColumnLayout {
    pub src_col: &'static str,
    pub dst_col: &'static str,
    pub rel_col: &'static str,
}

/// Observability counters for cache hits, incremental syncs, and sidecar DB hits.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct SidecarCacheStats {
    pub cache_hits: u64,
    pub cache_misses: u64,
    pub incremental_updates: u64,
    pub tokenized_nodes: u64,
    pub sidecar_table_hits: u64,
    pub subword_computed_nodes: u64,
    pub total_docs: usize,
    pub unique_terms: usize,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct SyncToken {
    conn_addr: usize,
    data_version: i64,
    schema_version: i64,
    total_changes: i64,
    max_rows: i64,
    include_historical: bool,
}

#[derive(Debug, Clone)]
pub struct SidecarDoc {
    pub node: NodeRow,
    pub updated_at: String,
    pub content_hash: u64,
    pub lifecycle_status: KnowledgeLifecycleStatus,
    pub importance_score: Option<f64>,
    pub visibility: Option<String>,
    pub dl_title: usize,
    pub dl_tags: usize,
    pub dl_body: usize,
    pub dl_meta: usize,
    pub tf_title: BTreeMap<String, usize>,
    pub tf_tags: BTreeMap<String, usize>,
    pub tf_body: BTreeMap<String, usize>,
    pub tf_meta: BTreeMap<String, usize>,
    pub title_lower: String,
    pub tags_headings_lower: String,
    pub body_lower: String,
    pub wikilinks: Vec<String>,
    pub tags: Vec<String>,
    pub title_norm: String,
    all_terms: Vec<String>,
    subword_embedding: Option<Vec<f32>>,
}

/// Pre-tokenized BM25F sidecar & inverted index with incremental updates and graph caches.
#[derive(Debug, Clone, Default)]
pub struct Bm25SidecarIndex {
    docs: Vec<Option<SidecarDoc>>,
    free_slots: Vec<usize>,
    id_to_idx: HashMap<String, usize>,
    total_dl_title: usize,
    total_dl_tags: usize,
    total_dl_body: usize,
    total_dl_meta: usize,
    active_docs: usize,
    avgdl_title: f64,
    avgdl_tags: f64,
    avgdl_body: f64,
    avgdl_meta: f64,
    postings: HashMap<String, Vec<PostingEntry>>,
    df: HashMap<String, usize>,
    title_to_doc_idxs: HashMap<String, Vec<usize>>,
    wikilink_to_doc_idxs: HashMap<String, Vec<usize>>,
    tag_to_doc_idxs: HashMap<String, Vec<usize>>,
    edges: Vec<CachedEdge>,
    edge_adj: HashMap<String, Vec<usize>>,
    edges_too_large: bool,
    edges_sync_token: Option<SyncToken>,
    edge_layout: Option<EdgeColumnLayout>,
    last_sync_token: Option<SyncToken>,
    pub(crate) stats: SidecarCacheStats,
}

fn node_content_hash(row: &NodeRow) -> u64 {
    let mut hasher = std::collections::hash_map::DefaultHasher::new();
    row.id.hash(&mut hasher);
    row.node_type.hash(&mut hasher);
    row.title.hash(&mut hasher);
    row.summary.hash(&mut hasher);
    row.metadata_json.hash(&mut hasher);
    row.updated_at.hash(&mut hasher);
    hasher.finish()
}

fn build_sidecar_doc(
    row: &NodeRow,
    precomputed: Option<&NodeBm25Stats>,
    scoped_meta: Option<(KnowledgeLifecycleStatus, Option<f64>, Option<String>)>,
) -> (SidecarDoc, bool) {
    let updated_at = row.updated_at.clone().unwrap_or_default();
    let content_hash = node_content_hash(row);
    let (lifecycle_status, importance_score, visibility) =
        scoped_meta.unwrap_or_else(|| extract_meta_from_json(row.metadata_json.as_deref()));

    let (stats, fields, used_table) = match precomputed {
        Some(st) if st.updated_at == updated_at => {
            let fields = extract_doc_field_strings(
                row.node_type.as_deref(),
                row.title.as_deref(),
                row.summary.as_deref(),
                row.metadata_json.as_deref(),
            );
            (st.clone(), fields, true)
        }
        _ => {
            let (st, fields) = compute_node_bm25_stats_from_row(row);
            (st, fields, false)
        }
    };

    let mut term_set: HashSet<&str> = HashSet::new();
    for k in stats.payload.tf_title.keys() {
        term_set.insert(k.as_str());
    }
    for k in stats.payload.tf_tags.keys() {
        term_set.insert(k.as_str());
    }
    for k in stats.payload.tf_body.keys() {
        term_set.insert(k.as_str());
    }
    for k in stats.payload.tf_meta.keys() {
        term_set.insert(k.as_str());
    }
    let all_terms: Vec<String> = term_set.into_iter().map(str::to_string).collect();

    let title_lower = fields.title_str.to_lowercase();
    let title_norm = title_lower.trim().to_string();

    let doc = SidecarDoc {
        node: row.clone(),
        updated_at,
        content_hash,
        lifecycle_status,
        importance_score,
        visibility,
        dl_title: stats.dl_title.max(1),
        dl_tags: stats.dl_tags.max(1),
        dl_body: stats.dl_body.max(1),
        dl_meta: stats.dl_meta.max(1),
        tf_title: stats.payload.tf_title,
        tf_tags: stats.payload.tf_tags,
        tf_body: stats.payload.tf_body,
        tf_meta: stats.payload.tf_meta,
        title_lower,
        tags_headings_lower: fields.tags_headings_str.to_lowercase(),
        body_lower: fields.body_str.to_lowercase(),
        wikilinks: stats.payload.wikilinks,
        tags: stats.payload.tags,
        title_norm,
        all_terms,
        subword_embedding: None,
    };
    (doc, used_table)
}

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

fn remove_idx_from_map(map: &mut HashMap<String, Vec<usize>>, key: &str, doc_idx: usize) {
    if key.is_empty() {
        return;
    }
    let empty_now = if let Some(list) = map.get_mut(key) {
        list.retain(|&idx| idx != doc_idx);
        list.is_empty()
    } else {
        false
    };
    if empty_now {
        map.remove(key);
    }
}

fn conn_sync_token(
    conn: &Connection,
    max_rows: i64,
    include_historical: bool,
) -> Result<SyncToken, CoreError> {
    let data_version: i64 = conn
        .query_row("PRAGMA data_version", [], |r| r.get(0))
        .unwrap_or(0);
    let schema_version: i64 = conn
        .query_row("PRAGMA schema_version", [], |r| r.get(0))
        .unwrap_or(0);
    let total_changes: i64 = conn
        .query_row("SELECT total_changes()", [], |r| r.get(0))
        .unwrap_or(0);
    Ok(SyncToken {
        conn_addr: std::ptr::from_ref(conn) as usize,
        data_version,
        schema_version,
        total_changes,
        max_rows,
        include_historical,
    })
}

/// Detect the column names on `edges` (`from_node/to_node/type` vs `source_id/target_id/relation_type`).
pub fn detect_edge_columns(conn: &Connection) -> Option<EdgeColumnLayout> {
    let mut stmt = conn.prepare("PRAGMA table_info(edges)").ok()?;
    let cols: Vec<String> = stmt
        .query_map([], |row| row.get::<_, String>(1))
        .ok()?
        .filter_map(Result::ok)
        .collect();
    if cols.is_empty() {
        return None;
    }
    if cols.iter().any(|c| c == "from_node") {
        Some(EdgeColumnLayout {
            src_col: "from_node",
            dst_col: "to_node",
            rel_col: "type",
        })
    } else if cols.iter().any(|c| c == "source_id") {
        Some(EdgeColumnLayout {
            src_col: "source_id",
            dst_col: "target_id",
            rel_col: "relation_type",
        })
    } else if cols.iter().any(|c| c == "source") {
        Some(EdgeColumnLayout {
            src_col: "source",
            dst_col: "target",
            rel_col: "type",
        })
    } else {
        None
    }
}

impl Bm25SidecarIndex {
    pub fn new() -> Self {
        Self {
            avgdl_title: 1.0,
            avgdl_tags: 1.0,
            avgdl_body: 1.0,
            avgdl_meta: 1.0,
            ..Self::default()
        }
    }

    /// Build a fresh sidecar index from a slice of `NodeRow`s.
    pub fn from_corpus(rows: &[NodeRow]) -> Self {
        let mut idx = Self::new();
        idx.sync_from_nodes(rows, None);
        idx
    }

    /// O(1) field length lookup `(dl_title, dl_tags, dl_body, dl_meta)` by `node_id`.
    pub fn field_lengths(&self, node_id: &str) -> Option<(usize, usize, usize, usize)> {
        let &doc_idx = self.id_to_idx.get(node_id)?;
        let doc = self.docs.get(doc_idx)?.as_ref()?;
        Some((doc.dl_title, doc.dl_tags, doc.dl_body, doc.dl_meta))
    }

    /// O(1) per-field term frequency lookup `(tf_title, tf_tags, tf_body, tf_meta)` by `node_id`.
    #[allow(clippy::type_complexity)]
    pub fn term_freqs(
        &self,
        node_id: &str,
    ) -> Option<(
        &BTreeMap<String, usize>,
        &BTreeMap<String, usize>,
        &BTreeMap<String, usize>,
        &BTreeMap<String, usize>,
    )> {
        let &doc_idx = self.id_to_idx.get(node_id)?;
        let doc = self.docs.get(doc_idx)?.as_ref()?;
        Some((&doc.tf_title, &doc.tf_tags, &doc.tf_body, &doc.tf_meta))
    }

    /// O(1) document frequency `df(t)` lookup.
    pub fn doc_freq(&self, term: &str) -> usize {
        self.df.get(term).copied().unwrap_or(0)
    }

    /// O(1) pre-aggregated average field lengths `(avgdl_title, avgdl_tags, avgdl_body, avgdl_meta)`.
    pub fn avgdl(&self) -> (f64, f64, f64, f64) {
        (
            self.avgdl_title,
            self.avgdl_tags,
            self.avgdl_body,
            self.avgdl_meta,
        )
    }

    /// O(1) inverted posting list lookup for `term`.
    pub fn postings(&self, term: &str) -> &[PostingEntry] {
        self.postings.get(term).map(Vec::as_slice).unwrap_or(&[])
    }

    /// O(1) document entry lookup by `node_id`.
    pub fn get_doc(&self, node_id: &str) -> Option<&SidecarDoc> {
        let &doc_idx = self.id_to_idx.get(node_id)?;
        self.docs.get(doc_idx)?.as_ref()
    }

    /// O(1) document entry lookup by internal `doc_idx`.
    pub(crate) fn doc_at(&self, doc_idx: usize) -> Option<&SidecarDoc> {
        self.docs.get(doc_idx)?.as_ref()
    }

    pub(crate) fn doc_idxs_by_title(&self, title_norm: &str) -> &[usize] {
        self.title_to_doc_idxs
            .get(title_norm)
            .map(Vec::as_slice)
            .unwrap_or(&[])
    }

    pub(crate) fn doc_idxs_by_wikilink(&self, wikilink: &str) -> &[usize] {
        self.wikilink_to_doc_idxs
            .get(wikilink)
            .map(Vec::as_slice)
            .unwrap_or(&[])
    }

    pub(crate) fn doc_idxs_by_tag(&self, tag: &str) -> &[usize] {
        self.tag_to_doc_idxs
            .get(tag)
            .map(Vec::as_slice)
            .unwrap_or(&[])
    }

    pub(crate) fn cached_edge_indices_for(&self, node_id: &str) -> Option<&[usize]> {
        if self.edges_too_large {
            return None;
        }
        self.edge_adj.get(node_id).map(Vec::as_slice)
    }

    pub(crate) fn cached_edge(&self, idx: usize) -> Option<&CachedEdge> {
        self.edges.get(idx)
    }

    pub(crate) fn edge_layout(&self) -> Option<EdgeColumnLayout> {
        self.edge_layout
    }

    pub(crate) fn has_cached_edges(&self) -> bool {
        !self.edges_too_large && self.edges_sync_token.is_some()
    }

    pub fn cache_stats(&self) -> &SidecarCacheStats {
        &self.stats
    }

    pub fn doc_count(&self) -> usize {
        self.active_docs
    }

    pub fn corpus_nodes(&self) -> Vec<NodeRow> {
        self.docs
            .iter()
            .filter_map(|d| d.as_ref().map(|doc| doc.node.clone()))
            .collect()
    }

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

    /// True if node index token still matches `conn` for `(max_rows, include_historical)`.
    pub(crate) fn is_synced_with(
        &self,
        conn: &Connection,
        max_rows: i64,
        include_historical: bool,
    ) -> bool {
        let Ok(token) = conn_sync_token(conn, max_rows, include_historical) else {
            return false;
        };
        self.last_sync_token.as_ref() == Some(&token)
    }

    /// True if graph edges token still matches `conn`.
    pub(crate) fn edges_synced_with(&self, conn: &Connection) -> bool {
        let Ok(token) = conn_sync_token(conn, 0, false) else {
            return false;
        };
        self.edges_sync_token.as_ref() == Some(&token)
    }

    fn remove_doc_by_id(&mut self, node_id: &str) {
        let Some(doc_idx) = self.id_to_idx.remove(node_id) else {
            return;
        };
        let Some(doc) = self.docs[doc_idx].take() else {
            return;
        };
        self.free_slots.push(doc_idx);
        self.active_docs = self.active_docs.saturating_sub(1);
        self.total_dl_title = self.total_dl_title.saturating_sub(doc.dl_title);
        self.total_dl_tags = self.total_dl_tags.saturating_sub(doc.dl_tags);
        self.total_dl_body = self.total_dl_body.saturating_sub(doc.dl_body);
        self.total_dl_meta = self.total_dl_meta.saturating_sub(doc.dl_meta);

        remove_idx_from_map(&mut self.title_to_doc_idxs, &doc.title_norm, doc_idx);
        for wl in &doc.wikilinks {
            remove_idx_from_map(&mut self.wikilink_to_doc_idxs, wl, doc_idx);
        }
        for tg in &doc.tags {
            remove_idx_from_map(&mut self.tag_to_doc_idxs, tg, doc_idx);
        }

        for term in &doc.all_terms {
            let empty_postings = if let Some(list) = self.postings.get_mut(term) {
                list.retain(|entry| entry.doc_idx != doc_idx);
                list.is_empty()
            } else {
                false
            };
            if empty_postings {
                self.postings.remove(term);
                self.df.remove(term);
            } else if let Some(cnt) = self.df.get_mut(term) {
                *cnt = cnt.saturating_sub(1);
                if *cnt == 0 {
                    self.df.remove(term);
                }
            }
        }
    }

    fn insert_doc(&mut self, doc: SidecarDoc) {
        if self.id_to_idx.contains_key(&doc.node.id) {
            self.remove_doc_by_id(&doc.node.id);
        }
        let doc_idx = match self.free_slots.pop() {
            Some(slot) => slot,
            None => {
                self.docs.push(None);
                self.docs.len() - 1
            }
        };

        self.active_docs += 1;
        self.total_dl_title += doc.dl_title;
        self.total_dl_tags += doc.dl_tags;
        self.total_dl_body += doc.dl_body;
        self.total_dl_meta += doc.dl_meta;

        if !doc.title_norm.is_empty() {
            self.title_to_doc_idxs
                .entry(doc.title_norm.clone())
                .or_default()
                .push(doc_idx);
        }
        for wl in &doc.wikilinks {
            self.wikilink_to_doc_idxs
                .entry(wl.clone())
                .or_default()
                .push(doc_idx);
        }
        for tg in &doc.tags {
            self.tag_to_doc_idxs
                .entry(tg.clone())
                .or_default()
                .push(doc_idx);
        }

        for term in &doc.all_terms {
            let entry = PostingEntry {
                doc_idx,
                node_id: doc.node.id.clone(),
                tf_title: *doc.tf_title.get(term).unwrap_or(&0),
                tf_tags: *doc.tf_tags.get(term).unwrap_or(&0),
                tf_body: *doc.tf_body.get(term).unwrap_or(&0),
                tf_meta: *doc.tf_meta.get(term).unwrap_or(&0),
            };
            self.postings.entry(term.clone()).or_default().push(entry);
            *self.df.entry(term.clone()).or_insert(0) += 1;
        }

        let node_id = doc.node.id.clone();
        self.docs[doc_idx] = Some(doc);
        self.id_to_idx.insert(node_id, doc_idx);
    }

    fn recompute_avgs(&mut self) {
        let n = (self.active_docs as f64).max(1.0);
        self.avgdl_title = (self.total_dl_title as f64 / n).max(1.0);
        self.avgdl_tags = (self.total_dl_tags as f64 / n).max(1.0);
        self.avgdl_body = (self.total_dl_body as f64 / n).max(1.0);
        self.avgdl_meta = (self.total_dl_meta as f64 / n).max(1.0);
        self.stats.total_docs = self.active_docs;
        self.stats.unique_terms = self.df.len();
    }

    /// Incrementally sync index from `rows`, reusing unchanged nodes and optional
    /// precomputed `bm25_node_stats` without re-tokenizing.
    pub fn sync_from_nodes(
        &mut self,
        rows: &[NodeRow],
        precomputed: Option<&HashMap<String, NodeBm25Stats>>,
    ) {
        let records: Vec<ScopedNodeRecord> = rows
            .iter()
            .map(|row| {
                let (lifecycle_status, importance_score, visibility) =
                    extract_meta_from_json(row.metadata_json.as_deref());
                ScopedNodeRecord {
                    node: row.clone(),
                    lifecycle_status,
                    importance_score,
                    visibility,
                }
            })
            .collect();
        self.sync_from_scoped_records(&records, precomputed);
    }

    /// Incrementally sync index from [`ScopedNodeRecord`] slices, preserving unchanged
    /// tokenized fields and cached subword embeddings while updating changed/new docs.
    pub fn sync_from_scoped_records(
        &mut self,
        records: &[ScopedNodeRecord],
        precomputed: Option<&HashMap<String, NodeBm25Stats>>,
    ) {
        let incoming_ids: HashSet<&str> = records.iter().map(|r| r.node.id.as_str()).collect();
        let to_remove: Vec<String> = self
            .id_to_idx
            .keys()
            .filter(|id| !incoming_ids.contains(id.as_str()))
            .cloned()
            .collect();

        let mut changed = !to_remove.is_empty();
        for id in to_remove {
            self.remove_doc_by_id(&id);
            self.stats.incremental_updates += 1;
        }

        for rec in records {
            let row = &rec.node;
            let hash = node_content_hash(row);
            let updated_at = row.updated_at.as_deref().unwrap_or_default();
            let unchanged = self
                .id_to_idx
                .get(&row.id)
                .and_then(|&idx| self.docs.get(idx))
                .and_then(|opt| opt.as_ref())
                .is_some_and(|doc| {
                    doc.updated_at == updated_at
                        && doc.content_hash == hash
                        && doc.lifecycle_status == rec.lifecycle_status
                        && doc.importance_score == rec.importance_score
                        && doc.visibility == rec.visibility
                });
            if unchanged {
                continue;
            }
            changed = true;
            let pre = precomputed.and_then(|m| m.get(&row.id));
            let (doc, used_table) = build_sidecar_doc(
                row,
                pre,
                Some((
                    rec.lifecycle_status,
                    rec.importance_score,
                    rec.visibility.clone(),
                )),
            );
            if used_table {
                self.stats.sidecar_table_hits += 1;
            } else {
                self.stats.tokenized_nodes += 1;
            }
            self.insert_doc(doc);
            self.stats.incremental_updates += 1;
        }

        if changed {
            self.recompute_avgs();
            if self.stats.cache_misses == 0 && self.stats.cache_hits == 0 {
                self.stats.cache_misses += 1;
            }
        } else {
            self.stats.cache_hits += 1;
        }
    }

    /// Sync index incrementally from SQLite with explicit `include_historical` lifecycle control.
    pub fn sync_from_conn_with_options(
        &mut self,
        conn: &Connection,
        scope: &Scope,
        max_rows: i64,
        include_historical: bool,
    ) -> Result<(), CoreError> {
        let token = conn_sync_token(conn, max_rows, include_historical)?;
        if self.last_sync_token.as_ref() == Some(&token) {
            self.stats.cache_hits += 1;
            return Ok(());
        }

        let records = load_scoped_corpus_records(
            conn,
            scope,
            &CorpusLoadOptions {
                max_rows,
                include_historical,
            },
        )?;
        let mut stale_or_missing_ids: Vec<String> = Vec::new();
        for rec in &records {
            let row = &rec.node;
            let hash = node_content_hash(row);
            let updated_at = row.updated_at.as_deref().unwrap_or_default();
            let unchanged = self
                .id_to_idx
                .get(&row.id)
                .and_then(|&idx| self.docs.get(idx))
                .and_then(|opt| opt.as_ref())
                .is_some_and(|doc| {
                    doc.updated_at == updated_at
                        && doc.content_hash == hash
                        && doc.lifecycle_status == rec.lifecycle_status
                        && doc.importance_score == rec.importance_score
                        && doc.visibility == rec.visibility
                });
            if !unchanged {
                stale_or_missing_ids.push(row.id.clone());
            }
        }

        let was_empty = self.active_docs == 0 && self.last_sync_token.is_none();
        if was_empty && !records.is_empty() {
            self.stats.cache_misses += 1;
        }

        let precomputed = if stale_or_missing_ids.is_empty() {
            HashMap::new()
        } else {
            load_bm25_node_stats(conn, &stale_or_missing_ids)
        };
        let had_updates = !stale_or_missing_ids.is_empty() || records.len() != self.active_docs;
        if had_updates {
            self.sync_from_scoped_records(&records, Some(&precomputed));
        } else {
            self.stats.cache_hits += 1;
        }
        self.last_sync_token = Some(token);
        Ok(())
    }

    /// Sync index incrementally from SQLite, hydrating unchanged/precomputed nodes
    /// from `bm25_node_stats` when available and never mutating read-only DBs.
    pub fn sync_from_conn(
        &mut self,
        conn: &Connection,
        scope: &Scope,
        max_rows: i64,
    ) -> Result<(), CoreError> {
        self.sync_from_conn_with_options(conn, scope, max_rows, false)
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
