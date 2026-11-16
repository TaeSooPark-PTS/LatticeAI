//! Fast inverted-index BM25F scoring and concurrent snapshot sidecar cache pool.

use std::cmp::Reverse;
use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, AtomicUsize, Ordering};
use std::sync::{Arc, OnceLock, RwLock};

use lattice_core::bm25_stats::QueryTerm;
use lattice_core::{clean_text, CoreError};
use rusqlite::Connection;

use crate::service::Scope;

use super::scoring::{
    accessibility_multiplier, importance_multiplier, node_type_multiplier, Bm25fParams,
    Bm25fScoredNode,
};
use super::sidecar::{Bm25SidecarIndex, SidecarCacheStats};
use super::tokenizer::extract_query_terms;

pub const MAX_CACHED_SIDECARS: usize = 64;
pub const DEFAULT_MAX_CACHE_BYTES: usize = 256 * 1024 * 1024;

impl Bm25SidecarIndex {
    /// Score `query` using the pre-tokenized inverted posting lists and O(1) field lengths.
    /// Excludes `superseded` / `deprecated` nodes by default (`include_historical = false`).
    pub fn score(&self, query: &str, params: &Bm25fParams) -> Vec<Bm25fScoredNode> {
        self.score_with_options(query, params, false)
    }

    /// Score `query` with explicit control over historical (`superseded`/`deprecated`) inclusion.
    pub fn score_with_options(
        &self,
        query: &str,
        params: &Bm25fParams,
        include_historical: bool,
    ) -> Vec<Bm25fScoredNode> {
        let raw_query_terms = extract_query_terms(query);
        if raw_query_terms.is_empty() || self.doc_count() == 0 {
            return Vec::new();
        }

        let mut unique_qterms: Vec<QueryTerm> = Vec::new();
        for qt in raw_query_terms {
            if let Some(existing) = unique_qterms.iter_mut().find(|e| e.token == qt.token) {
                existing.boost = existing.boost.max(qt.boost) + 0.15 * qt.boost;
            } else {
                unique_qterms.push(qt);
            }
        }

        let n_docs = self.doc_count() as f64;
        let mut idf_map: HashMap<String, f64> = HashMap::with_capacity(unique_qterms.len());
        for qt in &unique_qterms {
            let df = self.doc_freq(&qt.token);
            if df > 0 {
                let df_f = df as f64;
                let idf = (1.0 + (n_docs - df_f + 0.5) / (df_f + 0.5)).ln();
                idf_map.insert(qt.token.clone(), idf.max(0.10));
            }
        }
        if idf_map.is_empty() {
            return Vec::new();
        }

        struct CandidateAcc {
            score: f64,
            matched_terms: Vec<String>,
            title_hit: bool,
            tags_hit: bool,
            body_hit: bool,
            meta_hit: bool,
        }

        let (avgdl_title, avgdl_tags, avgdl_body, avgdl_meta) = self.avgdl();
        let mut candidates: HashMap<usize, CandidateAcc> = HashMap::new();
        for qt in &unique_qterms {
            let Some(&idf) = idf_map.get(&qt.token) else {
                continue;
            };
            for posting in self.postings(&qt.token) {
                let Some(doc) = self.doc_at(posting.doc_idx) else {
                    continue;
                };
                if !include_historical && doc.lifecycle_status.is_retired() {
                    continue;
                }
                let norm_title =
                    (1.0 - params.b_title) + params.b_title * (doc.dl_title as f64 / avgdl_title);
                let norm_tags = (1.0 - params.b_tags_headings)
                    + params.b_tags_headings * (doc.dl_tags as f64 / avgdl_tags);
                let norm_body =
                    (1.0 - params.b_body) + params.b_body * (doc.dl_body as f64 / avgdl_body);
                let norm_meta = (1.0 - params.b_metadata)
                    + params.b_metadata * (doc.dl_meta as f64 / avgdl_meta);

                let tf_title = posting.tf_title as f64;
                let tf_tags = posting.tf_tags as f64;
                let tf_body = posting.tf_body as f64;
                let tf_meta = posting.tf_meta as f64;

                let pseudo_tf = (params.title_weight * tf_title / norm_title)
                    + (params.tags_headings_weight * tf_tags / norm_tags)
                    + (params.body_weight * tf_body / norm_body)
                    + (params.metadata_weight * tf_meta / norm_meta);

                if pseudo_tf > 0.0 {
                    let saturated = (pseudo_tf * (params.k1 + 1.0)) / (pseudo_tf + params.k1);
                    let acc = candidates
                        .entry(posting.doc_idx)
                        .or_insert_with(|| CandidateAcc {
                            score: 0.0,
                            matched_terms: Vec::new(),
                            title_hit: false,
                            tags_hit: false,
                            body_hit: false,
                            meta_hit: false,
                        });
                    acc.score += qt.boost * idf * saturated;
                    if !qt.is_subword_or_bigram && !acc.matched_terms.contains(&qt.token) {
                        acc.matched_terms.push(qt.token.clone());
                    }
                    if tf_title > 0.0 {
                        acc.title_hit = true;
                    }
                    if tf_tags > 0.0 {
                        acc.tags_hit = true;
                    }
                    if tf_body > 0.0 {
                        acc.body_hit = true;
                    }
                    if tf_meta > 0.0 {
                        acc.meta_hit = true;
                    }
                }
            }
        }

        let primary_stems: Vec<&str> = unique_qterms
            .iter()
            .filter(|qt| !qt.is_subword_or_bigram)
            .map(|qt| qt.token.as_str())
            .collect();
        let cleaned_query_phrase = clean_text(query).to_lowercase();
        let check_phrase = cleaned_query_phrase.chars().count() >= 2;

        let mut scored: Vec<Bm25fScoredNode> = Vec::with_capacity(candidates.len());
        for (doc_idx, mut acc) in candidates {
            if acc.score <= 0.0 {
                continue;
            }
            let Some(doc) = self.doc_at(doc_idx) else {
                continue;
            };
            if check_phrase {
                if doc.title_lower.contains(&cleaned_query_phrase) {
                    acc.score *= 1.28;
                    acc.title_hit = true;
                } else if doc.tags_headings_lower.contains(&cleaned_query_phrase) {
                    acc.score *= 1.18;
                    acc.tags_hit = true;
                } else if doc.body_lower.contains(&cleaned_query_phrase) {
                    acc.score *= 1.10;
                    acc.body_hit = true;
                }
            }
            if primary_stems.len() >= 2 {
                let matched_primary = primary_stems
                    .iter()
                    .filter(|s| acc.matched_terms.iter().any(|m| m == *s))
                    .count();
                if matched_primary == primary_stems.len() {
                    acc.score *= 1.15;
                }
            }

            acc.score *= node_type_multiplier(doc.node.node_type.as_deref().unwrap_or_default());
            acc.score *= doc.lifecycle_status.multiplier();
            acc.score *= importance_multiplier(doc.importance_score);
            acc.score *= accessibility_multiplier(doc.visibility.as_deref());

            let mut matched_fields = Vec::new();
            if acc.title_hit {
                matched_fields.push("title".to_string());
            }
            if acc.tags_hit {
                matched_fields.push("tags_headings".to_string());
            }
            if acc.body_hit {
                matched_fields.push("body".to_string());
            }
            if acc.meta_hit {
                matched_fields.push("metadata".to_string());
            }

            scored.push(Bm25fScoredNode {
                node: doc.node.clone(),
                bm25_score: acc.score,
                matched_terms: acc.matched_terms,
                matched_fields,
                wikilinks: doc.wikilinks.clone(),
                tags: doc.tags.clone(),
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
}

struct CacheEntry {
    last_used: AtomicU64,
    read_hits: AtomicU64,
    estimated_bytes: AtomicUsize,
    snapshot: RwLock<Arc<Bm25SidecarIndex>>,
}

impl CacheEntry {
    fn new(tick: u64) -> Self {
        let idx = Bm25SidecarIndex::new();
        let bytes = idx.estimated_memory_bytes();
        Self {
            last_used: AtomicU64::new(tick),
            read_hits: AtomicU64::new(0),
            estimated_bytes: AtomicUsize::new(bytes),
            snapshot: RwLock::new(Arc::new(idx)),
        }
    }
}

/// Concurrent snapshot pool with `RwLock` shared reads and both count (`max_entries`)
/// and memory (`max_bytes`) LRU/cost-aware eviction.
pub struct SidecarCachePool {
    max_entries: usize,
    max_bytes: usize,
    tick: AtomicU64,
    entries: RwLock<HashMap<String, Arc<CacheEntry>>>,
}

impl SidecarCachePool {
    pub fn new(max_entries: usize, max_bytes: usize) -> Self {
        Self {
            max_entries: max_entries.max(1),
            max_bytes: max_bytes.max(1),
            tick: AtomicU64::new(0),
            entries: RwLock::new(HashMap::new()),
        }
    }

    fn from_env() -> Self {
        let max_bytes = std::env::var("LATTICEAI_SIDECAR_CACHE_MAX_BYTES")
            .ok()
            .and_then(|v| v.parse::<usize>().ok())
            .filter(|&v| v > 0)
            .unwrap_or(DEFAULT_MAX_CACHE_BYTES);
        Self::new(MAX_CACHED_SIDECARS, max_bytes)
    }

    fn next_tick(&self) -> u64 {
        self.tick.fetch_add(1, Ordering::Relaxed) + 1
    }

    pub fn entry_count(&self) -> usize {
        self.entries.read().unwrap_or_else(|e| e.into_inner()).len()
    }

    pub fn total_estimated_bytes(&self) -> usize {
        self.entries
            .read()
            .unwrap_or_else(|e| e.into_inner())
            .values()
            .map(|e| e.estimated_bytes.load(Ordering::Relaxed))
            .sum()
    }

    fn acquire_entry(&self, key: &str) -> Arc<CacheEntry> {
        let tick = self.next_tick();
        {
            let guard = self.entries.read().unwrap_or_else(|e| e.into_inner());
            if let Some(entry) = guard.get(key) {
                entry.last_used.store(tick, Ordering::Relaxed);
                return Arc::clone(entry);
            }
        }
        let mut guard = self.entries.write().unwrap_or_else(|e| e.into_inner());
        if let Some(entry) = guard.get(key) {
            entry.last_used.store(tick, Ordering::Relaxed);
            return Arc::clone(entry);
        }
        let entry = Arc::new(CacheEntry::new(tick));
        guard.insert(key.to_string(), Arc::clone(&entry));
        Self::evict_locked(&mut guard, self.max_entries, self.max_bytes, Some(key));
        entry
    }

    fn evict_locked(
        entries: &mut HashMap<String, Arc<CacheEntry>>,
        max_entries: usize,
        max_bytes: usize,
        protected_key: Option<&str>,
    ) {
        loop {
            let total_bytes: usize = entries
                .values()
                .map(|e| e.estimated_bytes.load(Ordering::Relaxed))
                .sum();
            let over_count = entries.len() > max_entries;
            let over_bytes = total_bytes > max_bytes && entries.len() > 1;
            if !over_count && !over_bytes {
                break;
            }
            let victim = entries
                .iter()
                .filter(|(k, _)| protected_key != Some(k.as_str()))
                .min_by_key(|(_, entry)| {
                    (
                        entry.last_used.load(Ordering::Relaxed),
                        Reverse(entry.estimated_bytes.load(Ordering::Relaxed)),
                    )
                })
                .map(|(k, _)| k.clone());
            match victim {
                Some(k) => {
                    entries.remove(&k);
                }
                None => break,
            }
        }
    }

    fn evict_if_over_budget(&self, protected_key: Option<&str>) {
        let needs_eviction = {
            let guard = self.entries.read().unwrap_or_else(|e| e.into_inner());
            let total_bytes: usize = guard
                .values()
                .map(|e| e.estimated_bytes.load(Ordering::Relaxed))
                .sum();
            guard.len() > self.max_entries || (total_bytes > self.max_bytes && guard.len() > 1)
        };
        if needs_eviction {
            let mut guard = self.entries.write().unwrap_or_else(|e| e.into_inner());
            Self::evict_locked(&mut guard, self.max_entries, self.max_bytes, protected_key);
        }
    }

    /// Return a lock-free immutable [`Arc<Bm25SidecarIndex>`] snapshot for `(conn, scope)`.
    /// Concurrent readers acquire only shared read locks and drop all locks before scoring.
    pub fn snapshot_for_conn(
        &self,
        conn: &Connection,
        scope: &Scope,
        max_rows: i64,
        include_historical: bool,
        ensure_edges: bool,
        ensure_subword: bool,
    ) -> Result<Arc<Bm25SidecarIndex>, CoreError> {
        let key = cache_key(conn, scope, include_historical);
        let tick = self.next_tick();

        // Fast lock-free-for-scoring concurrent read path.
        if let Some(entry) = {
            let guard = self.entries.read().unwrap_or_else(|e| e.into_inner());
            guard.get(&key).cloned()
        } {
            entry.last_used.store(tick, Ordering::Relaxed);
            let snap = {
                let snap_guard = entry.snapshot.read().unwrap_or_else(|e| e.into_inner());
                Arc::clone(&*snap_guard)
            };
            let synced = snap.is_synced_with(conn, max_rows, include_historical)
                && (!ensure_edges || snap.edges_synced_with(conn))
                && (!ensure_subword || snap.subword_ready());
            if synced {
                entry.read_hits.fetch_add(1, Ordering::Relaxed);
                return Ok(snap);
            }
        }

        // Slow incremental write path: blocks only writers to this single cache key.
        let entry = self.acquire_entry(&key);
        let (cloned, bytes) = {
            let mut snap_guard = entry.snapshot.write().unwrap_or_else(|e| e.into_inner());
            let index = Arc::make_mut(&mut *snap_guard);
            index.sync_from_conn_with_options(conn, scope, max_rows, include_historical)?;
            if ensure_edges {
                index.sync_edges_from_conn(conn)?;
            }
            if ensure_subword {
                index.ensure_subword_embeddings();
            }
            let bytes = index.estimated_memory_bytes();
            (Arc::clone(&*snap_guard), bytes)
        };
        entry.estimated_bytes.store(bytes, Ordering::Relaxed);
        self.evict_if_over_budget(Some(&key));
        Ok(cloned)
    }

    /// Acquire and incrementally sync a mutable sidecar index (via copy-on-write snapshot).
    pub fn with_mut<R, F>(
        &self,
        conn: &Connection,
        scope: &Scope,
        max_rows: i64,
        include_historical: bool,
        f: F,
    ) -> Result<R, CoreError>
    where
        F: FnOnce(&mut Bm25SidecarIndex) -> Result<R, CoreError>,
    {
        let key = cache_key(conn, scope, include_historical);
        let entry = self.acquire_entry(&key);
        let (out, bytes) = {
            let mut snap_guard = entry.snapshot.write().unwrap_or_else(|e| e.into_inner());
            let index = Arc::make_mut(&mut *snap_guard);
            index.sync_from_conn_with_options(conn, scope, max_rows, include_historical)?;
            let out = f(index)?;
            let bytes = index.estimated_memory_bytes();
            (out, bytes)
        };
        entry.estimated_bytes.store(bytes, Ordering::Relaxed);
        self.evict_if_over_budget(Some(&key));
        Ok(out)
    }

    pub fn stats_for(&self, conn: &Connection, scope: &Scope) -> Option<SidecarCacheStats> {
        let key = cache_key(conn, scope, false);
        let entry = {
            let guard = self.entries.read().unwrap_or_else(|e| e.into_inner());
            guard.get(&key)?.clone()
        };
        let snap = {
            let snap_guard = entry.snapshot.read().unwrap_or_else(|e| e.into_inner());
            Arc::clone(&*snap_guard)
        };
        let mut stats = snap.cache_stats().clone();
        stats.cache_hits += entry.read_hits.load(Ordering::Relaxed);
        Some(stats)
    }
}

static SIDECAR_CACHE: OnceLock<SidecarCachePool> = OnceLock::new();

fn global_pool() -> &'static SidecarCachePool {
    SIDECAR_CACHE.get_or_init(SidecarCachePool::from_env)
}

fn cache_key(conn: &Connection, scope: &Scope, include_historical: bool) -> String {
    let db_path = conn
        .query_row(
            "SELECT file FROM pragma_database_list WHERE name='main'",
            [],
            |row| row.get::<_, String>(0),
        )
        .unwrap_or_default();
    let db_ident = if db_path.is_empty() {
        format!("mem:{:p}", conn)
    } else {
        db_path
    };
    let ws = match &scope.allowed_workspaces {
        None => "*".to_string(),
        Some(set) => set.iter().cloned().collect::<Vec<_>>().join(","),
    };
    format!(
        "{db_ident}|ws={ws}|legacy={}|hist={include_historical}",
        scope.include_legacy_global
    )
}

/// Obtain a lock-free immutable [`Arc<Bm25SidecarIndex>`] snapshot synced for `(conn, scope)`.
pub fn with_bm25_sidecar_snapshot(
    conn: &Connection,
    scope: &Scope,
    max_rows: i64,
    include_historical: bool,
) -> Result<Arc<Bm25SidecarIndex>, CoreError> {
    global_pool().snapshot_for_conn(conn, scope, max_rows, include_historical, false, false)
}

/// Obtain a lock-free immutable [`Arc<Bm25SidecarIndex>`] snapshot with synced edges and
/// incrementally precomputed subword embeddings.
pub fn with_bm25_sidecar_full_snapshot(
    conn: &Connection,
    scope: &Scope,
    max_rows: i64,
    include_historical: bool,
) -> Result<Arc<Bm25SidecarIndex>, CoreError> {
    global_pool().snapshot_for_conn(conn, scope, max_rows, include_historical, true, true)
}

/// Acquire and incrementally sync the cached `Bm25SidecarIndex` for `(conn, scope)`,
/// then invoke `f(&mut sidecar)`.
pub fn with_bm25_sidecar<R, F>(
    conn: &Connection,
    scope: &Scope,
    max_rows: i64,
    f: F,
) -> Result<R, CoreError>
where
    F: FnOnce(&mut Bm25SidecarIndex) -> Result<R, CoreError>,
{
    global_pool().with_mut(conn, scope, max_rows, false, f)
}

/// Snapshot current cache stats for `(conn, scope)` if cached.
pub fn cached_sidecar_stats(conn: &Connection, scope: &Scope) -> Option<SidecarCacheStats> {
    global_pool().stats_for(conn, scope)
}
