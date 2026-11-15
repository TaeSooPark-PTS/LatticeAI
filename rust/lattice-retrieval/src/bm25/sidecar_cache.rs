//! Fast inverted-index BM25F scoring and process-wide sidecar cache pool.

use std::collections::HashMap;
use std::sync::{Arc, Mutex, OnceLock};

use lattice_core::bm25_stats::QueryTerm;
use lattice_core::{clean_text, CoreError};
use rusqlite::Connection;

use crate::service::Scope;

use super::scoring::{node_type_multiplier, Bm25fParams, Bm25fScoredNode};
use super::sidecar::{Bm25SidecarIndex, SidecarCacheStats};
use super::tokenizer::extract_query_terms;

const MAX_CACHED_SIDECARS: usize = 64;

impl Bm25SidecarIndex {
    /// Score `query` using the pre-tokenized inverted posting lists and O(1) field lengths.
    pub fn score(&self, query: &str, params: &Bm25fParams) -> Vec<Bm25fScoredNode> {
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
    last_used: u64,
    index: Arc<Mutex<Bm25SidecarIndex>>,
}

#[derive(Default)]
struct GlobalSidecarStore {
    tick: u64,
    entries: HashMap<String, CacheEntry>,
}

static SIDECAR_CACHE: OnceLock<Mutex<GlobalSidecarStore>> = OnceLock::new();

fn cache_key(conn: &Connection, scope: &Scope) -> String {
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
    format!("{db_ident}|ws={ws}|legacy={}", scope.include_legacy_global)
}

fn acquire_sidecar_entry(conn: &Connection, scope: &Scope) -> Arc<Mutex<Bm25SidecarIndex>> {
    let key = cache_key(conn, scope);
    let store = SIDECAR_CACHE.get_or_init(|| Mutex::new(GlobalSidecarStore::default()));
    let mut guard = store.lock().unwrap_or_else(|e| e.into_inner());
    guard.tick += 1;
    let tick = guard.tick;
    if let Some(entry) = guard.entries.get_mut(&key) {
        entry.last_used = tick;
        return Arc::clone(&entry.index);
    }
    if guard.entries.len() >= MAX_CACHED_SIDECARS {
        if let Some(oldest_key) = guard
            .entries
            .iter()
            .min_by_key(|(_, v)| v.last_used)
            .map(|(k, _)| k.clone())
        {
            guard.entries.remove(&oldest_key);
        }
    }
    let idx = Arc::new(Mutex::new(Bm25SidecarIndex::new()));
    guard.entries.insert(
        key,
        CacheEntry {
            last_used: tick,
            index: Arc::clone(&idx),
        },
    );
    idx
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
    let arc = acquire_sidecar_entry(conn, scope);
    let mut guard = arc.lock().unwrap_or_else(|e| e.into_inner());
    guard.sync_from_conn(conn, scope, max_rows)?;
    f(&mut guard)
}

/// Snapshot current cache stats for `(conn, scope)` if cached.
pub fn cached_sidecar_stats(conn: &Connection, scope: &Scope) -> Option<SidecarCacheStats> {
    let key = cache_key(conn, scope);
    let store = SIDECAR_CACHE.get()?;
    let guard = store.lock().unwrap_or_else(|e| e.into_inner());
    let arc = guard.entries.get(&key)?.index.clone();
    drop(guard);
    let idx = arc.lock().unwrap_or_else(|e| e.into_inner());
    Some(idx.cache_stats().clone())
}
