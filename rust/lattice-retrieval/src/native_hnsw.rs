//! Native pure-Rust HNSW / navigable small-world ANN sidecar with incremental
//! insert & tombstone updates, eliminating the mandatory Python `hnswlib` hop.
//!
//! Also inspects `.hnsw.meta.json` on disk when present so sidecar freshness
//! and model/dimension alignment can be validated without an IPC round-trip.

use std::cmp::Ordering;
use std::collections::{BinaryHeap, HashMap, HashSet};
use std::path::Path;
use std::sync::{Mutex, OnceLock};

use rusqlite::Connection;
use serde_json::Value;

const MAX_NEIGHBORS: usize = 16;
const EF_CONSTRUCTION: usize = 48;
const TOMBSTONE_REBUILD_RATIO: f64 = 0.20;

/// Parsed `.hnsw.meta.json` sidecar metadata when present alongside SQLite.
#[derive(Debug, Clone)]
pub struct SidecarMeta {
    pub embedding_model: String,
    pub embedding_dim: i64,
    pub active_count: usize,
    pub deleted_count: usize,
}

/// Inspect `<db>.hnsw.meta.json` directly from disk without calling Python.
pub fn read_sidecar_meta(db_path: &Path) -> Option<SidecarMeta> {
    let meta_path = db_path.with_extension("hnsw.meta.json");
    let raw = std::fs::read_to_string(meta_path).ok()?;
    let val: Value = serde_json::from_str(&raw).ok()?;
    let model = val.get("embedding_model")?.as_str()?.to_string();
    let dim = val.get("embedding_dim")?.as_i64()?;
    let labels = val
        .get("labels")
        .and_then(Value::as_array)
        .map(Vec::len)
        .unwrap_or(0);
    let deleted = val
        .get("deleted_labels")
        .and_then(Value::as_array)
        .map(Vec::len)
        .unwrap_or(0);
    Some(SidecarMeta {
        embedding_model: model,
        embedding_dim: dim,
        active_count: labels.saturating_sub(deleted),
        deleted_count: deleted,
    })
}

#[derive(Clone, Copy, PartialEq)]
struct ScoredSlot {
    score: f32,
    slot: usize,
}

impl Eq for ScoredSlot {}

impl Ord for ScoredSlot {
    fn cmp(&self, other: &Self) -> Ordering {
        self.score
            .partial_cmp(&other.score)
            .unwrap_or(Ordering::Equal)
            .then_with(|| other.slot.cmp(&self.slot))
    }
}

impl PartialOrd for ScoredSlot {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

#[derive(Clone)]
struct MinScored(ScoredSlot);

impl PartialEq for MinScored {
    fn eq(&self, other: &Self) -> bool {
        self.0 == other.0
    }
}
impl Eq for MinScored {}
impl Ord for MinScored {
    fn cmp(&self, other: &Self) -> Ordering {
        other.0.cmp(&self.0)
    }
}
impl PartialOrd for MinScored {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

struct GraphNode {
    item_id: String,
    text_hash: String,
    unit_vec: Vec<f32>,
    neighbors: Vec<usize>,
    deleted: bool,
}

/// Pure-Rust navigable small-world graph supporting incremental insert & tombstone delete.
pub struct NativeHnswGraph {
    dim: usize,
    nodes: Vec<GraphNode>,
    by_id: HashMap<String, usize>,
    deleted_slots: usize,
    watermark_indexed_at: String,
}

impl NativeHnswGraph {
    pub fn new(dim: usize) -> Self {
        Self {
            dim,
            nodes: Vec::new(),
            by_id: HashMap::new(),
            deleted_slots: 0,
            watermark_indexed_at: String::new(),
        }
    }

    pub fn active_len(&self) -> usize {
        self.by_id.len()
    }

    fn dot(a: &[f32], b: &[f32]) -> f32 {
        a.iter().zip(b.iter()).map(|(x, y)| x * y).sum()
    }

    fn normalize(raw: &[f32]) -> Vec<f32> {
        let norm = raw
            .iter()
            .map(|x| (*x as f64) * (*x as f64))
            .sum::<f64>()
            .sqrt();
        if norm <= 1e-12 {
            return vec![0.0; raw.len()];
        }
        let inv = (1.0 / norm) as f32;
        raw.iter().map(|x| *x * inv).collect()
    }

    fn entry_slot(&self) -> Option<usize> {
        self.nodes.iter().position(|n| !n.deleted)
    }

    fn beam_search(&self, query: &[f32], ef: usize) -> Vec<ScoredSlot> {
        let Some(entry) = self.entry_slot() else {
            return Vec::new();
        };
        let ef = ef.max(1);
        let mut visited = vec![false; self.nodes.len()];
        let mut candidates = BinaryHeap::new();
        let mut results: BinaryHeap<MinScored> = BinaryHeap::new();

        visited[entry] = true;
        let entry_score = Self::dot(query, &self.nodes[entry].unit_vec);
        let start = ScoredSlot {
            score: entry_score,
            slot: entry,
        };
        candidates.push(start);
        if !self.nodes[entry].deleted {
            results.push(MinScored(start));
        }

        while let Some(curr) = candidates.pop() {
            if results.len() >= ef {
                if let Some(worst) = results.peek() {
                    if curr.score < worst.0.score {
                        break;
                    }
                }
            }
            for &nbr in &self.nodes[curr.slot].neighbors {
                if visited[nbr] {
                    continue;
                }
                visited[nbr] = true;
                let s = Self::dot(query, &self.nodes[nbr].unit_vec);
                let should_explore =
                    results.len() < ef || results.peek().is_some_and(|w| s > w.0.score);
                if should_explore {
                    let item = ScoredSlot {
                        score: s,
                        slot: nbr,
                    };
                    candidates.push(item);
                    if !self.nodes[nbr].deleted {
                        results.push(MinScored(item));
                        if results.len() > ef {
                            results.pop();
                        }
                    }
                }
            }
        }

        let mut out: Vec<ScoredSlot> = results.into_iter().map(|m| m.0).collect();
        out.sort_by(|a, b| b.cmp(a));
        out
    }

    pub fn upsert(&mut self, item_id: String, text_hash: String, raw_vec: &[f32]) {
        if raw_vec.len() != self.dim {
            return;
        }
        if let Some(&existing) = self.by_id.get(&item_id) {
            if self.nodes[existing].text_hash == text_hash && !self.nodes[existing].deleted {
                return;
            }
            self.nodes[existing].deleted = true;
            self.deleted_slots += 1;
            self.by_id.remove(&item_id);
        }

        let unit_vec = Self::normalize(raw_vec);
        let near = self.beam_search(&unit_vec, EF_CONSTRUCTION);
        let neighbors: Vec<usize> = near
            .into_iter()
            .take(MAX_NEIGHBORS)
            .map(|s| s.slot)
            .collect();

        let new_slot = self.nodes.len();
        self.nodes.push(GraphNode {
            item_id: item_id.clone(),
            text_hash,
            unit_vec,
            neighbors: neighbors.clone(),
            deleted: false,
        });
        self.by_id.insert(item_id, new_slot);

        for nbr in neighbors {
            if !self.nodes[nbr].neighbors.contains(&new_slot) {
                self.nodes[nbr].neighbors.push(new_slot);
                if self.nodes[nbr].neighbors.len() > MAX_NEIGHBORS * 2 {
                    let nbr_vec = self.nodes[nbr].unit_vec.clone();
                    let current_list = self.nodes[nbr].neighbors.clone();
                    let mut rescored: Vec<(f32, usize)> = current_list
                        .into_iter()
                        .filter(|&slot| !self.nodes[slot].deleted)
                        .map(|slot| (Self::dot(&nbr_vec, &self.nodes[slot].unit_vec), slot))
                        .collect();
                    rescored.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap_or(Ordering::Equal));
                    rescored.truncate(MAX_NEIGHBORS);
                    self.nodes[nbr].neighbors =
                        rescored.into_iter().map(|(_, slot)| slot).collect();
                }
            }
        }
    }

    pub fn remove_missing(&mut self, present_ids: &HashSet<String>) -> bool {
        let stale_ids: Vec<String> = self
            .by_id
            .keys()
            .filter(|id| !present_ids.contains(*id))
            .cloned()
            .collect();
        for id in stale_ids {
            if let Some(slot) = self.by_id.remove(&id) {
                if !self.nodes[slot].deleted {
                    self.nodes[slot].deleted = true;
                    self.deleted_slots += 1;
                }
            }
        }
        let total = self.nodes.len();
        total >= 16 && (self.deleted_slots as f64 / total as f64) > TOMBSTONE_REBUILD_RATIO
    }

    pub fn query(&self, raw_query: &[f64], k: usize) -> Vec<String> {
        if raw_query.len() != self.dim || self.by_id.is_empty() {
            return Vec::new();
        }
        let q32: Vec<f32> = raw_query.iter().map(|v| *v as f32).collect();
        let unit_q = Self::normalize(&q32);
        let ef = (k.saturating_mul(2)).max(64);
        self.beam_search(&unit_q, ef)
            .into_iter()
            .take(k)
            .map(|hit| self.nodes[hit.slot].item_id.clone())
            .collect()
    }
}

fn decode_f32_blob(blob: &[u8], dim: usize) -> Option<Vec<f32>> {
    if blob.len() == dim * 4 {
        Some(
            (0..dim)
                .map(|idx| {
                    let off = idx * 4;
                    f32::from_le_bytes([blob[off], blob[off + 1], blob[off + 2], blob[off + 3]])
                })
                .collect(),
        )
    } else if blob.len() == dim * 8 {
        Some(
            (0..dim)
                .map(|idx| {
                    let off = idx * 8;
                    f64::from_le_bytes([
                        blob[off],
                        blob[off + 1],
                        blob[off + 2],
                        blob[off + 3],
                        blob[off + 4],
                        blob[off + 5],
                        blob[off + 6],
                        blob[off + 7],
                    ]) as f32
                })
                .collect(),
        )
    } else {
        None
    }
}

static NATIVE_CACHE: OnceLock<Mutex<HashMap<String, NativeHnswGraph>>> = OnceLock::new();

fn cache() -> &'static Mutex<HashMap<String, NativeHnswGraph>> {
    NATIVE_CACHE.get_or_init(|| Mutex::new(HashMap::new()))
}

/// Synchronize (incrementally or via full rebuild) and query the in-process Rust HNSW graph.
pub fn query_native_hnsw(
    conn: &Connection,
    model_id: &str,
    dim: usize,
    query_vector: &[f64],
    k: usize,
) -> Result<(Vec<String>, usize), String> {
    let db_key = conn
        .path()
        .filter(|p| !p.is_empty())
        .map(|p| format!("{p}::{model_id}::{dim}"));

    if let Some(key) = db_key {
        let mut guard = cache()
            .lock()
            .map_err(|_| "native HNSW cache poisoned".to_string())?;
        let graph = guard
            .entry(key)
            .or_insert_with(|| NativeHnswGraph::new(dim));
        sync_graph_with_conn(conn, model_id, dim, graph)?;
        let active = graph.active_len();
        let ids = graph.query(query_vector, k);
        Ok((ids, active))
    } else {
        // In-memory SQLite database: build an ephemeral graph for this connection.
        let mut graph = NativeHnswGraph::new(dim);
        sync_graph_with_conn(conn, model_id, dim, &mut graph)?;
        let active = graph.active_len();
        let ids = graph.query(query_vector, k);
        Ok((ids, active))
    }
}

fn sync_graph_with_conn(
    conn: &Connection,
    model_id: &str,
    dim: usize,
    graph: &mut NativeHnswGraph,
) -> Result<(), String> {
    let mut id_stmt = conn
        .prepare(
            "SELECT item_id FROM vector_embeddings WHERE embedding_model=?1 AND embedding_dim=?2",
        )
        .map_err(|e| e.to_string())?;
    let present: HashSet<String> = id_stmt
        .query_map(rusqlite::params![model_id, dim as i64], |r| {
            r.get::<_, String>(0)
        })
        .map_err(|e| e.to_string())?
        .filter_map(Result::ok)
        .collect();

    let needs_full_rebuild = graph.remove_missing(&present) || graph.active_len() == 0;
    if needs_full_rebuild {
        *graph = NativeHnswGraph::new(dim);
    }

    let sql = if graph.watermark_indexed_at.is_empty() {
        "SELECT item_id, COALESCE(text_hash, ''), embedding, COALESCE(indexed_at, '') 
         FROM vector_embeddings WHERE embedding_model=?1 AND embedding_dim=?2 
         ORDER BY indexed_at ASC"
    } else {
        "SELECT item_id, COALESCE(text_hash, ''), embedding, COALESCE(indexed_at, '') 
         FROM vector_embeddings WHERE embedding_model=?1 AND embedding_dim=?2 
         AND COALESCE(indexed_at, '') >= ?3 ORDER BY indexed_at ASC"
    };

    let mut stmt = conn.prepare(sql).map_err(|e| e.to_string())?;
    let prev_wm = graph.watermark_indexed_at.clone();
    let mut max_ts = prev_wm.clone();

    if prev_wm.is_empty() {
        let rows = stmt
            .query_map(rusqlite::params![model_id, dim as i64], |r| {
                Ok((
                    r.get::<_, String>(0)?,
                    r.get::<_, String>(1)?,
                    r.get::<_, Vec<u8>>(2)?,
                    r.get::<_, String>(3)?,
                ))
            })
            .map_err(|e| e.to_string())?;
        for (id, hash, blob, ts) in rows.flatten() {
            if let Some(vec) = decode_f32_blob(&blob, dim) {
                graph.upsert(id, hash, &vec);
            }
            if ts > max_ts {
                max_ts = ts;
            }
        }
    } else {
        let rows = stmt
            .query_map(rusqlite::params![model_id, dim as i64, prev_wm], |r| {
                Ok((
                    r.get::<_, String>(0)?,
                    r.get::<_, String>(1)?,
                    r.get::<_, Vec<u8>>(2)?,
                    r.get::<_, String>(3)?,
                ))
            })
            .map_err(|e| e.to_string())?;
        for (id, hash, blob, ts) in rows.flatten() {
            if let Some(vec) = decode_f32_blob(&blob, dim) {
                graph.upsert(id, hash, &vec);
            }
            if ts > max_ts {
                max_ts = ts;
            }
        }
    }
    graph.watermark_indexed_at = max_ts;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn native_hnsw_supports_incremental_upsert_and_tombstone_delete() {
        let mut g = NativeHnswGraph::new(4);
        g.upsert("a".into(), "h1".into(), &[1.0, 0.0, 0.0, 0.0]);
        g.upsert("b".into(), "h2".into(), &[0.0, 1.0, 0.0, 0.0]);
        g.upsert("c".into(), "h3".into(), &[0.9, 0.1, 0.0, 0.0]);

        let hits = g.query(&[1.0, 0.0, 0.0, 0.0], 2);
        assert_eq!(hits[0], "a");
        assert!(hits.contains(&"c".to_string()));

        // Incremental delete of 'a'
        let present: HashSet<String> = ["b".to_string(), "c".to_string()].into_iter().collect();
        g.remove_missing(&present);
        let hits2 = g.query(&[1.0, 0.0, 0.0, 0.0], 2);
        assert!(!hits2.contains(&"a".to_string()));
        assert_eq!(hits2[0], "c");
    }
}
