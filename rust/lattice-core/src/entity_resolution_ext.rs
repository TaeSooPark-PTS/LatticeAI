//! Extended Entity Resolution: Fuzzy & Multi-Signal Disambiguation, Persistent Aliases,
//! Merge Audit Trail, Reference Migration, Selective Rollback & Cross-Workspace Guard (P0.3).

use std::collections::{BTreeSet, HashMap, HashSet};

use rusqlite::{params, Connection, OptionalExtension};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use crate::db::CoreError;
use crate::entity_resolution::{canonical_entity_key, ENTITY_NODE_TYPES};
use crate::graph_write::clock::{Clock, SystemClock};
use crate::graph_write::pyaux::sha256_text;
use crate::pytext::parse_iso;

fn utc_now_iso() -> String {
    SystemClock.now_iso()
}

/// Decision band separating high-confidence auto-merges from human-review candidates.
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum MergeDecisionBand {
    AutoMerge,
    ReviewCandidate,
    RejectedHomonym,
}

/// Breakdown of disambiguation signals explaining why two entities matched or collided.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct DisambiguationSignals {
    pub exact_key_match: bool,
    pub surface_similarity: f64,
    pub token_jaccard: f64,
    pub same_type: bool,
    pub provenance_overlap: f64,
    pub relational_overlap: f64,
    pub temporal_proximity: f64,
    pub combined_score: f64,
    pub homonym_risk: bool,
}

/// One candidate entity merge pair evaluated by [`evaluate_merge_candidates`].
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct EntityMergeCandidate {
    pub canonical_id: String,
    pub duplicate_id: String,
    pub canonical_title: String,
    pub duplicate_title: String,
    pub node_type: String,
    pub workspace_id: String,
    pub decision: MergeDecisionBand,
    pub signals: DisambiguationSignals,
    pub rationale: String,
}

/// Persistent audit record for one applied entity merge (supports selective unmerge).
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct EntityMergeAuditRecord {
    pub merge_id: String,
    pub workspace_id: String,
    pub canonical_id: String,
    pub duplicate_id: String,
    pub actor: String,
    pub rationale: String,
    pub signals: Value,
    pub migrated_edges: Vec<MigratedEdgeSnapshot>,
    pub created_at: String,
    pub rolled_back_at: Option<String>,
}

/// Pre-merge endpoint snapshot enabling lossless edge unmerge.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct MigratedEdgeSnapshot {
    pub edge_id: String,
    pub original_from: String,
    pub original_to: String,
}

/// Ensure persistent tables for entity aliases, merge audit trail, and do-not-merge rules exist.
pub fn ensure_entity_resolution_tables(conn: &Connection) -> Result<(), CoreError> {
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS entity_aliases (
            workspace_id TEXT NOT NULL,
            canonical_id TEXT NOT NULL,
            alias TEXT NOT NULL,
            alias_key TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'user',
            created_at TEXT NOT NULL,
            PRIMARY KEY (workspace_id, canonical_id, alias_key)
        );
        CREATE TABLE IF NOT EXISTS entity_merge_audit (
            merge_id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            canonical_id TEXT NOT NULL,
            duplicate_id TEXT NOT NULL,
            actor TEXT NOT NULL,
            rationale TEXT NOT NULL,
            signals_json TEXT NOT NULL DEFAULT '{}',
            migrated_edges_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL,
            rolled_back_at TEXT
        );
        CREATE TABLE IF NOT EXISTS entity_do_not_merge (
            node_a TEXT NOT NULL,
            node_b TEXT NOT NULL,
            workspace_id TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            PRIMARY KEY (node_a, node_b)
        );",
    )?;
    Ok(())
}

/// Load all persisted `DIFFERENT_FROM` / do-not-merge pairs.
pub fn load_do_not_merge_pairs(conn: &Connection) -> Result<BTreeSet<(String, String)>, CoreError> {
    let exists: bool = conn
        .query_row(
            "SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type='table' AND name='entity_do_not_merge')",
            [],
            |r| r.get(0),
        )
        .unwrap_or(false);
    if !exists {
        return Ok(BTreeSet::new());
    }
    let mut stmt = conn.prepare("SELECT node_a, node_b FROM entity_do_not_merge")?;
    let pairs = stmt
        .query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?)))?
        .filter_map(Result::ok)
        .map(|(a, b)| if a <= b { (a, b) } else { (b, a) })
        .collect();
    Ok(pairs)
}

/// Persist an alias for `canonical_id` inside `workspace_id`.
pub fn register_persistent_alias(
    conn: &Connection,
    workspace_id: &str,
    canonical_id: &str,
    alias: &str,
    source: &str,
) -> Result<(), CoreError> {
    ensure_entity_resolution_tables(conn)?;
    let key = canonical_entity_key(alias);
    if key.is_empty() {
        return Err(CoreError::InvalidRequest(
            "alias cannot be empty".to_string(),
        ));
    }
    conn.execute(
        "INSERT OR REPLACE INTO entity_aliases(workspace_id, canonical_id, alias, alias_key, source, created_at)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
        params![workspace_id, canonical_id, alias.trim(), key, source, utc_now_iso()],
    )?;
    Ok(())
}

/// List persisted aliases for a canonical entity (or entire workspace when `canonical_id` is None).
pub fn list_persistent_aliases(
    conn: &Connection,
    workspace_id: &str,
    canonical_id: Option<&str>,
) -> Result<Vec<(String, String, String)>, CoreError> {
    ensure_entity_resolution_tables(conn)?;
    let mut out = Vec::new();
    if let Some(cid) = canonical_id {
        let mut stmt = conn.prepare(
            "SELECT canonical_id, alias, source FROM entity_aliases
             WHERE workspace_id = ?1 AND canonical_id = ?2 ORDER BY alias ASC",
        )?;
        for row in stmt.query_map(params![workspace_id, cid], |r| {
            Ok((r.get(0)?, r.get(1)?, r.get(2)?))
        })? {
            out.push(row?);
        }
    } else {
        let mut stmt = conn.prepare(
            "SELECT canonical_id, alias, source FROM entity_aliases
             WHERE workspace_id = ?1 ORDER BY canonical_id ASC, alias ASC",
        )?;
        for row in stmt.query_map(params![workspace_id], |r| {
            Ok((r.get(0)?, r.get(1)?, r.get(2)?))
        })? {
            out.push(row?);
        }
    }
    Ok(out)
}

/// Normalized Levenshtein similarity in `[0.0, 1.0]`.
pub fn normalized_levenshtein(a: &str, b: &str) -> f64 {
    let a_chars: Vec<char> = a.chars().collect();
    let b_chars: Vec<char> = b.chars().collect();
    if a_chars.is_empty() && b_chars.is_empty() {
        return 1.0;
    }
    let max_len = a_chars.len().max(b_chars.len());
    if max_len == 0 {
        return 1.0;
    }
    let mut prev: Vec<usize> = (0..=b_chars.len()).collect();
    let mut curr = vec![0usize; b_chars.len() + 1];
    for (i, &ca) in a_chars.iter().enumerate() {
        curr[0] = i + 1;
        for (j, &cb) in b_chars.iter().enumerate() {
            let cost = usize::from(ca != cb);
            curr[j + 1] = (prev[j + 1] + 1).min(curr[j] + 1).min(prev[j] + cost);
        }
        std::mem::swap(&mut prev, &mut curr);
    }
    1.0 - (prev[b_chars.len()] as f64 / max_len as f64)
}

fn token_jaccard(a: &str, b: &str) -> f64 {
    let ta: HashSet<String> = a
        .split(|c: char| !c.is_alphanumeric() && !('\u{AC00}'..='\u{D7A3}').contains(&c))
        .filter(|s| !s.is_empty())
        .map(str::to_lowercase)
        .collect();
    let tb: HashSet<String> = b
        .split(|c: char| !c.is_alphanumeric() && !('\u{AC00}'..='\u{D7A3}').contains(&c))
        .filter(|s| !s.is_empty())
        .map(str::to_lowercase)
        .collect();
    if ta.is_empty() || tb.is_empty() {
        return 0.0;
    }
    let inter = ta.intersection(&tb).count() as f64;
    let union = ta.union(&tb).count() as f64;
    inter / union
}

fn set_jaccard(a: &HashSet<String>, b: &HashSet<String>) -> f64 {
    if a.is_empty() || b.is_empty() {
        return 0.0;
    }
    let inter = a.intersection(b).count() as f64;
    let union = a.union(b).count() as f64;
    inter / union
}

/// Evaluate exact + fuzzy entity merge candidates within a single strict `workspace_id`,
/// separating high-confidence `AutoMerge` from `ReviewCandidate` and `RejectedHomonym`.
pub fn evaluate_merge_candidates(
    conn: &Connection,
    workspace_id: &str,
) -> Result<Vec<EntityMergeCandidate>, CoreError> {
    ensure_entity_resolution_tables(conn)?;
    let ws = workspace_id.trim();
    if ws.is_empty() {
        return Err(CoreError::InvalidRequest(
            "workspace_id is required for entity merge evaluation".to_string(),
        ));
    }
    let blocked = load_do_not_merge_pairs(conn)?;
    let allowed_types: HashSet<&str> = ENTITY_NODE_TYPES.iter().copied().collect();

    struct Row {
        id: String,
        node_type: String,
        title: String,
        key: String,
        created_epoch: Option<f64>,
        domain_hint: Option<String>,
    }

    let mut rows = Vec::new();
    let mut stmt = conn.prepare(
        "SELECT n.id, n.type, n.title, n.metadata_json, n.created_at,
                COALESCE(NULLIF(TRIM(v.workspace_id), ''), NULLIF(TRIM(json_extract(n.metadata_json, '$.workspace_id')), ''), 'personal')
         FROM nodes n
         LEFT JOIN nodes_v2 v ON v.id = n.id
         WHERE COALESCE(v.superseded_by, '') = ''
         ORDER BY n.id ASC",
    )?;
    for item in stmt.query_map([], |r| {
        Ok((
            r.get::<_, String>(0)?,
            r.get::<_, String>(1)?,
            r.get::<_, String>(2)?,
            r.get::<_, Option<String>>(3)?.unwrap_or_default(),
            r.get::<_, Option<String>>(4)?.unwrap_or_default(),
            r.get::<_, String>(5)?,
        ))
    })? {
        let (id, node_type, title, meta_raw, created_at, eff_ws) = item?;
        if eff_ws != ws || !allowed_types.contains(node_type.as_str()) {
            continue;
        }
        let key = canonical_entity_key(&title);
        if key.is_empty() {
            continue;
        }
        let meta: Value = serde_json::from_str(&meta_raw).unwrap_or_else(|_| json!({}));
        let domain_hint = meta
            .get("domain")
            .or_else(|| meta.get("organization"))
            .and_then(Value::as_str)
            .map(|s| s.trim().to_lowercase())
            .filter(|s| !s.is_empty());
        rows.push(Row {
            id,
            node_type,
            title,
            key,
            created_epoch: parse_iso(Some(&created_at)),
            domain_hint,
        });
    }

    // Build neighbor sets for relational + provenance overlap.
    let id_set: HashSet<&str> = rows.iter().map(|r| r.id.as_str()).collect();
    let mut neighbors: HashMap<String, HashSet<String>> = HashMap::new();
    let mut prov_docs: HashMap<String, HashSet<String>> = HashMap::new();
    let mut estmt = conn.prepare("SELECT from_node, to_node, type FROM edges")?;
    for row in estmt
        .query_map([], |r| {
            Ok((
                r.get::<_, String>(0)?,
                r.get::<_, String>(1)?,
                r.get::<_, String>(2)?,
            ))
        })?
        .filter_map(Result::ok)
    {
        let (u, v, et) = row;
        if et.eq_ignore_ascii_case("SAME_AS") {
            continue;
        }
        if id_set.contains(u.as_str()) {
            neighbors.entry(u.clone()).or_default().insert(v.clone());
            if !id_set.contains(v.as_str()) {
                prov_docs.entry(u.clone()).or_default().insert(v.clone());
            }
        }
        if id_set.contains(v.as_str()) {
            neighbors.entry(v.clone()).or_default().insert(u.clone());
            if !id_set.contains(u.as_str()) {
                prov_docs.entry(v.clone()).or_default().insert(u.clone());
            }
        }
    }

    let mut out = Vec::new();
    for i in 0..rows.len() {
        for j in (i + 1)..rows.len() {
            let a = &rows[i];
            let b = &rows[j];
            let pair_key = if a.id <= b.id {
                (a.id.clone(), b.id.clone())
            } else {
                (b.id.clone(), a.id.clone())
            };
            if blocked.contains(&pair_key) {
                continue;
            }
            let exact = a.key == b.key;
            let surface_sim = if exact {
                1.0
            } else {
                normalized_levenshtein(&a.key, &b.key)
            };
            let tok_jaccard = token_jaccard(&a.title, &b.title);
            if !exact && surface_sim < 0.78 && tok_jaccard < 0.60 {
                continue;
            }
            let same_type = a.node_type == b.node_type;
            let empty_set = HashSet::new();
            let prov_ov = set_jaccard(
                prov_docs.get(&a.id).unwrap_or(&empty_set),
                prov_docs.get(&b.id).unwrap_or(&empty_set),
            );
            let rel_ov = set_jaccard(
                neighbors.get(&a.id).unwrap_or(&empty_set),
                neighbors.get(&b.id).unwrap_or(&empty_set),
            );
            let temp_prox = match (a.created_epoch, b.created_epoch) {
                (Some(ea), Some(eb)) => {
                    let days = (ea - eb).abs() / 86_400.0;
                    (1.0 / (1.0 + days / 30.0)).clamp(0.0, 1.0)
                }
                _ => 0.5,
            };
            // Homonym collision guard: different entity types, conflicting explicit domains,
            // or short acronym (<= 4 chars) with zero shared provenance/relations.
            let conflicting_domain = match (&a.domain_hint, &b.domain_hint) {
                (Some(da), Some(db)) => da != db,
                _ => false,
            };
            let short_uncorroborated =
                a.key.chars().count() <= 4 && prov_ov == 0.0 && rel_ov == 0.0;
            let homonym_risk = !same_type || conflicting_domain || short_uncorroborated;

            let combined = (0.45 * surface_sim
                + 0.20 * tok_jaccard
                + 0.15 * prov_ov
                + 0.12 * rel_ov
                + 0.08 * temp_prox)
                .clamp(0.0, 1.0);

            let (decision, rationale) = if !same_type || conflicting_domain {
                (
                    MergeDecisionBand::RejectedHomonym,
                    format!(
                        "Homonym safeguard blocked merge ({} vs {}, domain conflict={}).",
                        a.node_type, b.node_type, conflicting_domain
                    ),
                )
            } else if exact && !short_uncorroborated && combined >= 0.65 {
                (
                    MergeDecisionBand::AutoMerge,
                    "Exact canonical match with compatible entity type and non-conflicting context."
                        .to_string(),
                )
            } else {
                (
                    MergeDecisionBand::ReviewCandidate,
                    if short_uncorroborated {
                        "Short name/acronym lacks shared provenance or graph context; requires review."
                            .to_string()
                    } else {
                        "Fuzzy/contextual candidate requires human review before merge.".to_string()
                    },
                )
            };

            out.push(EntityMergeCandidate {
                canonical_id: a.id.clone(),
                duplicate_id: b.id.clone(),
                canonical_title: a.title.clone(),
                duplicate_title: b.title.clone(),
                node_type: a.node_type.clone(),
                workspace_id: ws.to_string(),
                decision,
                signals: DisambiguationSignals {
                    exact_key_match: exact,
                    surface_similarity: surface_sim,
                    token_jaccard: tok_jaccard,
                    same_type,
                    provenance_overlap: prov_ov,
                    relational_overlap: rel_ov,
                    temporal_proximity: temp_prox,
                    combined_score: combined,
                    homonym_risk,
                },
                rationale,
            });
        }
    }
    Ok(out)
}

fn node_effective_workspace(conn: &Connection, node_id: &str) -> Result<String, CoreError> {
    let row: Option<(Option<String>, Option<String>)> = conn
        .query_row(
            "SELECT v.workspace_id, json_extract(n.metadata_json, '$.workspace_id')
             FROM nodes n LEFT JOIN nodes_v2 v ON v.id = n.id
             WHERE n.id = ?1",
            params![node_id],
            |r| Ok((r.get(0)?, r.get(1)?)),
        )
        .optional()?;
    let Some((v_ws, m_ws)) = row else {
        return Err(CoreError::InvalidRequest(format!(
            "unknown entity node: {node_id}"
        )));
    };
    Ok(v_ws
        .or(m_ws)
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| "personal".to_string()))
}

/// Execute one audited, reversible entity merge with strict cross-workspace prohibition
/// and edge reference migration (preserving exact pre-merge snapshots for selective rollback).
pub fn execute_audited_entity_merge(
    conn: &mut Connection,
    workspace_id: &str,
    canonical_id: &str,
    duplicate_id: &str,
    actor: &str,
    rationale: &str,
    signals: Value,
) -> Result<EntityMergeAuditRecord, CoreError> {
    ensure_entity_resolution_tables(conn)?;
    if canonical_id == duplicate_id {
        return Err(CoreError::InvalidRequest(
            "cannot merge entity with itself".to_string(),
        ));
    }
    let ws_a = node_effective_workspace(conn, canonical_id)?;
    let ws_b = node_effective_workspace(conn, duplicate_id)?;
    if ws_a != ws_b || ws_a != workspace_id.trim() {
        return Err(CoreError::InvalidRequest(format!(
            "cross-workspace entity merge prohibited: canonical={ws_a}, duplicate={ws_b}, requested={workspace_id}"
        )));
    }

    let now = utc_now_iso();
    let merge_id = format!(
        "emerge:{}",
        &sha256_text(&format!(
            "{workspace_id}|{canonical_id}|{duplicate_id}|{now}"
        ))[..20]
    );
    let tx = conn.transaction()?;

    // Snapshot & migrate incident edges (excluding SAME_AS and self-loops) to canonical_id.
    let mut migrated = Vec::new();
    let incident: Vec<(String, String, String)> = {
        let mut stmt = tx.prepare(
            "SELECT id, from_node, to_node FROM edges
             WHERE UPPER(type) != 'SAME_AS'
               AND (from_node = ?1 OR to_node = ?1)",
        )?;
        let out = stmt
            .query_map(params![duplicate_id], |r| {
                Ok((r.get(0)?, r.get(1)?, r.get(2)?))
            })?
            .filter_map(Result::ok)
            .collect();
        out
    };

    for (eid, orig_from, orig_to) in incident {
        let new_from = if orig_from == duplicate_id {
            canonical_id
        } else {
            orig_from.as_str()
        };
        let new_to = if orig_to == duplicate_id {
            canonical_id
        } else {
            orig_to.as_str()
        };
        if new_from == new_to {
            continue;
        }
        // Only migrate when it won't violate UNIQUE(from_node, to_node, type).
        if tx.execute(
            "UPDATE OR IGNORE edges SET from_node = ?1, to_node = ?2 WHERE id = ?3",
            params![new_from, new_to, eid],
        )? > 0
        {
            migrated.push(MigratedEdgeSnapshot {
                edge_id: eid,
                original_from: orig_from,
                original_to: orig_to,
            });
        }
    }

    // Record persistent alias from duplicate's title onto canonical_id.
    if let Some(dup_title) = tx
        .query_row(
            "SELECT title FROM nodes WHERE id = ?1",
            params![duplicate_id],
            |r| r.get::<_, String>(0),
        )
        .optional()?
    {
        let akey = canonical_entity_key(&dup_title);
        if !akey.is_empty() {
            let _ = tx.execute(
                "INSERT OR IGNORE INTO entity_aliases(workspace_id, canonical_id, alias, alias_key, source, created_at)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
                params![workspace_id, canonical_id, dup_title, akey, merge_id, now],
            );
        }
    }

    // Link duplicate -> canonical via SAME_AS edge.
    let same_edge_id = format!("er_same_as:{duplicate_id}->{canonical_id}");
    tx.execute(
        "INSERT INTO edges(id, from_node, to_node, type, weight, metadata_json, created_at)
         VALUES (?1, ?2, ?3, 'SAME_AS', 1.0, ?4, ?5)
         ON CONFLICT(from_node, to_node, type) DO UPDATE SET metadata_json = excluded.metadata_json",
        params![
            same_edge_id,
            duplicate_id,
            canonical_id,
            json!({"merge_id": merge_id, "actor": actor}).to_string(),
            now
        ],
    )?;

    let migrated_json = serde_json::to_string(&migrated).unwrap_or_else(|_| "[]".to_string());
    let signals_json = serde_json::to_string(&signals).unwrap_or_else(|_| "{}".to_string());
    tx.execute(
        "INSERT INTO entity_merge_audit(
            merge_id, workspace_id, canonical_id, duplicate_id, actor, rationale,
            signals_json, migrated_edges_json, created_at
         ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
        params![
            merge_id,
            workspace_id,
            canonical_id,
            duplicate_id,
            actor,
            rationale,
            signals_json,
            migrated_json,
            now
        ],
    )?;
    tx.commit()?;

    Ok(EntityMergeAuditRecord {
        merge_id,
        workspace_id: workspace_id.to_string(),
        canonical_id: canonical_id.to_string(),
        duplicate_id: duplicate_id.to_string(),
        actor: actor.to_string(),
        rationale: rationale.to_string(),
        signals,
        migrated_edges: migrated,
        created_at: now,
        rolled_back_at: None,
    })
}

/// Selectively roll back one entity merge by `merge_id`, restoring all migrated edges
/// to their original endpoints and adding a `DIFFERENT_FROM` blocklist entry so it
/// will not be erroneously re-merged.
pub fn rollback_entity_merge(
    conn: &mut Connection,
    merge_id: &str,
    add_do_not_merge: bool,
) -> Result<EntityMergeAuditRecord, CoreError> {
    ensure_entity_resolution_tables(conn)?;
    struct AuditRow {
        workspace_id: String,
        canonical_id: String,
        duplicate_id: String,
        actor: String,
        rationale: String,
        signals_json: String,
        migrated_json: String,
        created_at: String,
        existing_rb: Option<String>,
    }
    let rec: Option<AuditRow> = conn
        .query_row(
            "SELECT workspace_id, canonical_id, duplicate_id, actor, rationale,
                    signals_json, migrated_edges_json, created_at, rolled_back_at
             FROM entity_merge_audit WHERE merge_id = ?1",
            params![merge_id],
            |r| {
                Ok(AuditRow {
                    workspace_id: r.get(0)?,
                    canonical_id: r.get(1)?,
                    duplicate_id: r.get(2)?,
                    actor: r.get(3)?,
                    rationale: r.get(4)?,
                    signals_json: r.get(5)?,
                    migrated_json: r.get(6)?,
                    created_at: r.get(7)?,
                    existing_rb: r.get(8)?,
                })
            },
        )
        .optional()?;
    let Some(AuditRow {
        workspace_id,
        canonical_id,
        duplicate_id,
        actor,
        rationale,
        signals_json,
        migrated_json,
        created_at,
        existing_rb,
    }) = rec
    else {
        return Err(CoreError::InvalidRequest(format!(
            "unknown merge_id: {merge_id}"
        )));
    };
    if existing_rb.is_some() {
        return Err(CoreError::InvalidRequest(format!(
            "merge {merge_id} was already rolled back"
        )));
    }
    let migrated: Vec<MigratedEdgeSnapshot> =
        serde_json::from_str(&migrated_json).unwrap_or_default();
    let now = utc_now_iso();
    let tx = conn.transaction()?;

    for snap in &migrated {
        tx.execute(
            "UPDATE edges SET from_node = ?1, to_node = ?2 WHERE id = ?3",
            params![snap.original_from, snap.original_to, snap.edge_id],
        )?;
    }
    tx.execute(
        "DELETE FROM edges WHERE id = ?1",
        params![format!("er_same_as:{duplicate_id}->{canonical_id}")],
    )?;
    tx.execute(
        "DELETE FROM entity_aliases WHERE source = ?1",
        params![merge_id],
    )?;
    if add_do_not_merge {
        let (a, b) = if canonical_id <= duplicate_id {
            (&canonical_id, &duplicate_id)
        } else {
            (&duplicate_id, &canonical_id)
        };
        tx.execute(
            "INSERT OR REPLACE INTO entity_do_not_merge(node_a, node_b, workspace_id, reason, created_at)
             VALUES (?1, ?2, ?3, ?4, ?5)",
            params![a, b, workspace_id, format!("rolled back merge {merge_id}"), now],
        )?;
    }
    tx.execute(
        "UPDATE entity_merge_audit SET rolled_back_at = ?2 WHERE merge_id = ?1",
        params![merge_id, now],
    )?;
    tx.commit()?;

    Ok(EntityMergeAuditRecord {
        merge_id: merge_id.to_string(),
        workspace_id,
        canonical_id,
        duplicate_id,
        actor,
        rationale,
        signals: serde_json::from_str(&signals_json).unwrap_or_else(|_| json!({})),
        migrated_edges: migrated,
        created_at,
        rolled_back_at: Some(now),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::entity_resolution::{resolve_entities_in_conn, EntityAliasBook};
    use crate::graph_write::schema::bootstrap;

    #[test]
    fn homonym_guard_fuzzy_scoring_cross_workspace_block_and_selective_rollback() {
        let mut conn = Connection::open_in_memory().unwrap();
        {
            let tx = conn.transaction().unwrap();
            bootstrap(&tx, "2026-01-01T00:00:00").unwrap();
            tx.commit().unwrap();
        }

        // Person "Apple" vs Organization "Apple" in ws-1 -> must NEVER auto-merge.
        conn.execute_batch(
            "INSERT INTO nodes(id, type, title, summary, raw_json, metadata_json, created_at, updated_at) VALUES
                ('p-apple', 'Person', 'Apple', 'Singer', '{}', '{\"workspace_id\":\"ws-1\"}', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z'),
                ('o-apple', 'Organization', 'Apple', 'Tech corp', '{}', '{\"workspace_id\":\"ws-1\"}', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z'),
                ('o-apple-inc', 'Organization', 'Apple Inc', 'Tech corp', '{}', '{\"workspace_id\":\"ws-1\"}', '2026-01-02T00:00:00Z', '2026-01-02T00:00:00Z'),
                ('doc-1', 'Document', 'Report', 'Mentions Apple', '{}', '{\"workspace_id\":\"ws-1\"}', '2026-01-02T00:00:00Z', '2026-01-02T00:00:00Z'),
                ('o-apple-ws2', 'Organization', 'Apple', 'Other workspace', '{}', '{\"workspace_id\":\"ws-2\"}', '2026-01-02T00:00:00Z', '2026-01-02T00:00:00Z');
             INSERT INTO nodes_v2(id, type, label, summary, attrs, workspace_id, importance_score, created_at, updated_at) VALUES
                ('p-apple', 'Person', 'Apple', 'Singer', '{}', 'ws-1', 0.5, '2026-01-01', '2026-01-01'),
                ('o-apple', 'Organization', 'Apple', 'Tech corp', '{}', 'ws-1', 0.7, '2026-01-01', '2026-01-01'),
                ('o-apple-inc', 'Organization', 'Apple Inc', 'Tech corp', '{}', 'ws-1', 0.6, '2026-01-02', '2026-01-02'),
                ('doc-1', 'Document', 'Report', 'Mentions Apple', '{}', 'ws-1', 0.6, '2026-01-02', '2026-01-02'),
                ('o-apple-ws2', 'Organization', 'Apple', 'Other workspace', '{}', 'ws-2', 0.6, '2026-01-02', '2026-01-02');
             INSERT INTO edges(id, from_node, to_node, type, weight, metadata_json, created_at) VALUES
                ('e-mention', 'doc-1', 'o-apple-inc', 'MENTIONS', 1.0, '{}', '2026-01-02');",
        )
        .unwrap();

        // Type-scoped exact clustering keeps Person and Organization separate.
        let clusters =
            resolve_entities_in_conn(&conn, Some("ws-1"), &EntityAliasBook::new()).unwrap();
        assert!(!clusters
            .iter()
            .any(|c| c.member_node_ids.contains(&"p-apple".to_string())
                && c.member_node_ids.contains(&"o-apple".to_string())));

        // Candidate evaluator flags Person/Org collision as RejectedHomonym and Apple/Apple Inc as ReviewCandidate.
        let cands = evaluate_merge_candidates(&conn, "ws-1").unwrap();
        assert!(cands
            .iter()
            .any(|c| c.decision == MergeDecisionBand::RejectedHomonym));

        // Cross-workspace merge MUST be rejected.
        let cross = execute_audited_entity_merge(
            &mut conn,
            "ws-1",
            "o-apple",
            "o-apple-ws2",
            "tester",
            "should fail",
            json!({}),
        );
        assert!(cross.is_err());

        // Same-workspace audited merge migrates edge `e-mention` to `o-apple`.
        let merged = execute_audited_entity_merge(
            &mut conn,
            "ws-1",
            "o-apple",
            "o-apple-inc",
            "tester",
            "verified alias",
            json!({"surface_similarity": 0.72}),
        )
        .unwrap();
        assert_eq!(merged.migrated_edges.len(), 1);
        let target: String = conn
            .query_row(
                "SELECT to_node FROM edges WHERE id = 'e-mention'",
                [],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(target, "o-apple");

        // Selective rollback restores `e-mention` back to `o-apple-inc` and records do-not-merge.
        let rolled = rollback_entity_merge(&mut conn, &merged.merge_id, true).unwrap();
        assert!(rolled.rolled_back_at.is_some());
        let restored_target: String = conn
            .query_row(
                "SELECT to_node FROM edges WHERE id = 'e-mention'",
                [],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(restored_target, "o-apple-inc");
        let blocked = load_do_not_merge_pairs(&conn).unwrap();
        assert!(blocked.contains(&("o-apple".to_string(), "o-apple-inc".to_string())));
    }
}
