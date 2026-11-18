//! Canonical Entity Resolution, alias merging, and provenance-preserving consolidation.
//!
//! Follows Lattice Knowledge Graph invariants:
//! - Non-destructive: duplicate entity mentions are linked via `SAME_AS` edges and
//!   annotated in `metadata_json.entity_resolution` rather than deleted.
//! - Provenance-preserving: every merged alias retains its originating source/document
//!   node IDs so citations and rollback never lose lineage.
//! - Reversible: [`rollback_entity_consolidation`] removes synthesized `SAME_AS`
//!   links (`er_same_as:*`) and clears the projection overlay cleanly.

use std::collections::{BTreeMap, BTreeSet, HashMap};

use rusqlite::{params, Connection, Transaction};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use crate::db::CoreError;
use crate::read::{scoped_workspace_visible, workspace_is_default, DEFAULT_WORKSPACE_ID};

fn node_matches_workspace(row_ws: Option<&str>, meta: &Value, target_ws: &str) -> bool {
    let eff_ws = row_ws.map(str::trim).filter(|s| !s.is_empty()).or_else(|| {
        meta.get("workspace_id")
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|s| !s.is_empty())
    });
    let mut allowed = BTreeSet::new();
    allowed.insert(target_ws);
    scoped_workspace_visible(eff_ws, &allowed, workspace_is_default(Some(target_ws)))
}

/// Node types eligible for canonical entity resolution.
pub const ENTITY_NODE_TYPES: &[&str] = &[
    "Concept",
    "Entity",
    "Organization",
    "Person",
    "Project",
    "System",
    "Tool",
    "Topic",
];

/// Trailing Korean postpositions stripped on multi-syllable Hangul entity tokens.
const KO_ENTITY_PARTICLES: &[&str] = &[
    "에서는",
    "으로는",
    "에게는",
    "로부터",
    "에서",
    "으로",
    "에게",
    "까지",
    "부터",
    "처럼",
    "보다",
    "은",
    "는",
    "이",
    "가",
    "을",
    "를",
    "의",
    "에",
    "로",
    "와",
    "과",
    "도",
    "만",
];

fn is_hangul_char(ch: char) -> bool {
    ('\u{AC00}'..='\u{D7A3}').contains(&ch)
}

fn strip_korean_particle(token: &str) -> &str {
    for suffix in KO_ENTITY_PARTICLES {
        if let Some(stem) = token.strip_suffix(suffix) {
            let hangul_len = stem.chars().filter(|c| is_hangul_char(*c)).count();
            if hangul_len >= 2 {
                return stem;
            }
        }
    }
    token
}

/// Compute a deterministic canonical key for an entity surface form.
///
/// Normalizes case, separators (`-`, `_`, `.`, `/`, whitespace), parenthetical
/// acronyms, and trailing Korean particles so `"Project-Alpha"`, `"project alpha"`,
/// and `"프로젝트 알파는"` map consistently.
pub fn canonical_entity_key(raw: &str) -> String {
    let trimmed = raw.trim();
    if trimmed.is_empty() {
        return String::new();
    }
    let mut parts: Vec<String> = Vec::new();
    for raw_word in trimmed.split(|c: char| {
        c.is_whitespace()
            || matches!(
                c,
                '-' | '_' | '/' | '.' | ',' | ':' | ';' | '(' | ')' | '[' | ']'
            )
    }) {
        if raw_word.is_empty() {
            continue;
        }
        let stem = strip_korean_particle(raw_word);
        let cleaned: String = stem
            .chars()
            .filter(|c| c.is_alphanumeric() || is_hangul_char(*c))
            .flat_map(char::to_lowercase)
            .collect();
        if !cleaned.is_empty() {
            parts.push(cleaned);
        }
    }
    parts.join("")
}

/// Bilingual / domain alias registry mapping surface variations to a shared canonical key.
#[derive(Debug, Clone, Default)]
pub struct EntityAliasBook {
    alias_to_group: HashMap<String, String>,
}

impl EntityAliasBook {
    /// Empty alias book (relies purely on surface normalization, `metadata_json.aliases`, and `SAME_AS` edges).
    pub fn new() -> Self {
        Self::default()
    }

    /// Register a canonical name and one or more surface aliases (e.g. Korean/English/acronym).
    pub fn register_aliases<I, S>(&mut self, canonical: &str, aliases: I)
    where
        I: IntoIterator<Item = S>,
        S: AsRef<str>,
    {
        let root_key = canonical_entity_key(canonical);
        if root_key.is_empty() {
            return;
        }
        self.alias_to_group
            .insert(root_key.clone(), root_key.clone());
        for alias in aliases {
            let k = canonical_entity_key(alias.as_ref());
            if !k.is_empty() {
                self.alias_to_group.insert(k, root_key.clone());
            }
        }
    }

    /// Resolve a surface string through registered alias groups, falling back to [`canonical_entity_key`].
    pub fn resolve_key(&self, surface: &str) -> String {
        let key = canonical_entity_key(surface);
        self.alias_to_group.get(&key).cloned().unwrap_or(key)
    }
}

/// Consolidated canonical entity cluster with full source lineage.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ResolvedEntityCluster {
    pub canonical_id: String,
    pub canonical_title: String,
    pub canonical_key: String,
    pub node_type: String,
    pub member_node_ids: Vec<String>,
    pub aliases: Vec<String>,
    pub provenance_node_ids: Vec<String>,
    pub confidence: f64,
}

/// Summary returned by [`apply_entity_consolidation`].
#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq, Eq)]
pub struct EntityConsolidationSummary {
    pub total_entities_scanned: usize,
    pub clusters_with_merges: usize,
    pub duplicate_nodes_linked: usize,
    pub same_as_edges_upserted: usize,
}

#[derive(Debug, Clone)]
struct CandidateEntityRow {
    id: String,
    node_type: String,
    title: String,
    metadata: Value,
    importance: f64,
    created_at: String,
    aliases: Vec<String>,
}

fn parse_metadata_aliases(meta: &Value) -> Vec<String> {
    let mut out = Vec::new();
    for field in ["aliases", "aka", "surface_forms"] {
        if let Some(arr) = meta.get(field).and_then(Value::as_array) {
            for item in arr {
                if let Some(s) = item.as_str() {
                    let trimmed = s.trim();
                    if !trimmed.is_empty() {
                        out.push(trimmed.to_string());
                    }
                }
            }
        }
    }
    if let Some(canonical_name) = meta.get("canonical_name").and_then(Value::as_str) {
        let trimmed = canonical_name.trim();
        if !trimmed.is_empty() {
            out.push(trimmed.to_string());
        }
    }
    out
}

struct DisjointSet {
    parent: Vec<usize>,
}

impl DisjointSet {
    fn new(n: usize) -> Self {
        Self {
            parent: (0..n).collect(),
        }
    }

    fn find(&mut self, x: usize) -> usize {
        if self.parent[x] != x {
            let root = self.find(self.parent[x]);
            self.parent[x] = root;
        }
        self.parent[x]
    }

    fn union(&mut self, a: usize, b: usize) {
        let ra = self.find(a);
        let rb = self.find(b);
        if ra != rb {
            if ra < rb {
                self.parent[rb] = ra;
            } else {
                self.parent[ra] = rb;
            }
        }
    }
}

fn has_nodes_v2(conn: &Connection) -> bool {
    conn.query_row(
        "SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type='table' AND name='nodes_v2')",
        [],
        |r| r.get::<_, bool>(0),
    )
    .unwrap_or(false)
}

/// Resolve canonical entity clusters across `conn` within `workspace_id` without mutating the store.
pub fn resolve_entities_in_conn(
    conn: &Connection,
    workspace_id: Option<&str>,
    alias_book: &EntityAliasBook,
) -> Result<Vec<ResolvedEntityCluster>, CoreError> {
    let target_ws = workspace_id
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .unwrap_or(DEFAULT_WORKSPACE_ID);
    let v2 = has_nodes_v2(conn);
    let v2_has_lifecycle = v2
        && conn
            .prepare("PRAGMA table_info(nodes_v2)")
            .and_then(|mut s| {
                let cols: Vec<String> = s
                    .query_map([], |r| r.get::<_, String>(1))?
                    .filter_map(Result::ok)
                    .collect();
                Ok(cols.iter().any(|c| c == "lifecycle_status"))
            })
            .unwrap_or(false);
    let sql = if v2 && v2_has_lifecycle {
        "SELECT n.id, n.type, n.title, n.metadata_json, n.created_at,
                v.workspace_id, COALESCE(v.importance_score, 0.5), v.lifecycle_status, v.superseded_by
         FROM nodes n
         LEFT JOIN nodes_v2 v ON v.id = n.id
         ORDER BY n.id ASC"
    } else if v2 {
        "SELECT n.id, n.type, n.title, n.metadata_json, n.created_at,
                v.workspace_id, COALESCE(v.importance_score, 0.5),
                json_extract(n.metadata_json, '$.lifecycle_status'), v.superseded_by
         FROM nodes n
         LEFT JOIN nodes_v2 v ON v.id = n.id
         ORDER BY n.id ASC"
    } else {
        "SELECT n.id, n.type, n.title, n.metadata_json, n.created_at,
                NULL, 0.5, NULL, NULL
         FROM nodes n
         ORDER BY n.id ASC"
    };

    let mut stmt = conn.prepare(sql)?;
    let rows = stmt.query_map([], |row| {
        Ok((
            row.get::<_, String>(0)?,
            row.get::<_, String>(1)?,
            row.get::<_, String>(2)?,
            row.get::<_, Option<String>>(3)?.unwrap_or_default(),
            row.get::<_, Option<String>>(4)?.unwrap_or_default(),
            row.get::<_, Option<String>>(5)?,
            row.get::<_, f64>(6).unwrap_or(0.5),
            row.get::<_, Option<String>>(7)?,
            row.get::<_, Option<String>>(8)?,
        ))
    })?;

    let allowed_types: BTreeSet<&str> = ENTITY_NODE_TYPES.iter().copied().collect();
    let mut candidates: Vec<CandidateEntityRow> = Vec::new();
    for item in rows {
        let (
            id,
            node_type,
            title,
            meta_raw,
            created_at,
            row_ws,
            importance,
            lifecycle,
            superseded_by,
        ) = item?;
        if !allowed_types.contains(node_type.as_str()) {
            continue;
        }
        let meta: Value = serde_json::from_str(&meta_raw).unwrap_or_else(|_| json!({}));
        if !node_matches_workspace(row_ws.as_deref(), &meta, target_ws) {
            continue;
        }
        if superseded_by
            .as_deref()
            .is_some_and(|s| !s.trim().is_empty())
        {
            continue;
        }
        if matches!(
            lifecycle.as_deref(),
            Some("superseded" | "deprecated" | "archived" | "retracted")
        ) {
            continue;
        }
        let aliases = parse_metadata_aliases(&meta);
        candidates.push(CandidateEntityRow {
            id,
            node_type,
            title,
            metadata: meta,
            importance,
            created_at,
            aliases,
        });
    }

    if candidates.is_empty() {
        return Ok(Vec::new());
    }

    let id_to_idx: HashMap<String, usize> = candidates
        .iter()
        .enumerate()
        .map(|(idx, c)| (c.id.clone(), idx))
        .collect();
    let mut dsu = DisjointSet::new(candidates.len());
    let mut key_owner: HashMap<String, usize> = HashMap::new();

    for (idx, cand) in candidates.iter().enumerate() {
        let mut surfaces = Vec::with_capacity(1 + cand.aliases.len());
        surfaces.push(cand.title.as_str());
        for a in &cand.aliases {
            surfaces.push(a.as_str());
        }
        for surface in surfaces {
            let key = alias_book.resolve_key(surface);
            if key.is_empty() {
                continue;
            }
            if let Some(&prev_idx) = key_owner.get(&key) {
                dsu.union(prev_idx, idx);
            } else {
                key_owner.insert(key, idx);
            }
        }
    }

    // Honor pre-existing SAME_AS edges between scoped entity nodes.
    let mut same_stmt =
        conn.prepare("SELECT from_node, to_node FROM edges WHERE UPPER(type) = 'SAME_AS'")?;
    let same_rows =
        same_stmt.query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?)))?;
    for pair in same_rows {
        let (from_id, to_id) = pair?;
        if let (Some(&a), Some(&b)) = (id_to_idx.get(&from_id), id_to_idx.get(&to_id)) {
            dsu.union(a, b);
        }
    }

    // Gather provenance neighbors (edges touching any member entity).
    let mut neighbor_prov: HashMap<String, BTreeSet<String>> = HashMap::new();
    let mut edge_stmt = conn.prepare("SELECT from_node, to_node, type FROM edges")?;
    let edge_rows = edge_stmt.query_map([], |r| {
        Ok((
            r.get::<_, String>(0)?,
            r.get::<_, String>(1)?,
            r.get::<_, String>(2)?,
        ))
    })?;
    for edge in edge_rows {
        let (u, v, etype) = edge?;
        if etype.eq_ignore_ascii_case("SAME_AS") {
            continue;
        }
        if id_to_idx.contains_key(&u) && !id_to_idx.contains_key(&v) {
            neighbor_prov
                .entry(u.clone())
                .or_default()
                .insert(v.clone());
        }
        if id_to_idx.contains_key(&v) && !id_to_idx.contains_key(&u) {
            neighbor_prov.entry(v).or_default().insert(u);
        }
    }

    let mut groups: BTreeMap<usize, Vec<usize>> = BTreeMap::new();
    for idx in 0..candidates.len() {
        let root = dsu.find(idx);
        groups.entry(root).or_default().push(idx);
    }

    let mut clusters: Vec<ResolvedEntityCluster> = Vec::with_capacity(groups.len());
    for members in groups.into_values() {
        // Choose canonical leader deterministically: highest importance, earliest created_at, shortest title, lexical ID.
        let leader_idx = *members
            .iter()
            .min_by(|&&a, &&b| {
                let ca = &candidates[a];
                let cb = &candidates[b];
                cb.importance
                    .total_cmp(&ca.importance)
                    .then_with(|| ca.created_at.cmp(&cb.created_at))
                    .then_with(|| ca.title.chars().count().cmp(&cb.title.chars().count()))
                    .then_with(|| ca.id.cmp(&cb.id))
            })
            .expect("non-empty cluster");

        let leader = &candidates[leader_idx];
        let mut member_ids: Vec<String> =
            members.iter().map(|&i| candidates[i].id.clone()).collect();
        member_ids.sort();

        let mut alias_set: BTreeSet<String> = BTreeSet::new();
        let mut prov_set: BTreeSet<String> = BTreeSet::new();
        for &idx in &members {
            let c = &candidates[idx];
            alias_set.insert(c.title.clone());
            for a in &c.aliases {
                alias_set.insert(a.clone());
            }
            if let Some(src) = c.metadata.get("source_node").and_then(Value::as_str) {
                if !src.trim().is_empty() {
                    prov_set.insert(src.trim().to_string());
                }
            }
            if let Some(ext) = neighbor_prov.get(&c.id) {
                prov_set.extend(ext.iter().cloned());
            }
            prov_set.insert(c.id.clone());
        }

        let confidence = if member_ids.len() == 1 {
            1.0
        } else {
            // Exact normalized-key match yields 0.96, alias-book mediated match yields 0.90.
            let direct_keys: BTreeSet<String> = members
                .iter()
                .map(|&i| canonical_entity_key(&candidates[i].title))
                .collect();
            if direct_keys.len() == 1 {
                0.96
            } else {
                0.90
            }
        };

        clusters.push(ResolvedEntityCluster {
            canonical_id: leader.id.clone(),
            canonical_title: leader.title.clone(),
            canonical_key: alias_book.resolve_key(&leader.title),
            node_type: leader.node_type.clone(),
            member_node_ids: member_ids,
            aliases: alias_set.into_iter().collect(),
            provenance_node_ids: prov_set.into_iter().collect(),
            confidence,
        });
    }

    clusters.sort_by(|a, b| a.canonical_id.cmp(&b.canonical_id));
    Ok(clusters)
}

/// Non-destructively project canonical entity clusters into `metadata_json.entity_resolution`
/// and idempotent `SAME_AS` edges (`er_same_as:<dup>-><canonical>`).
pub fn apply_entity_consolidation(
    txn: &Transaction<'_>,
    workspace_id: Option<&str>,
    alias_book: &EntityAliasBook,
    recorded_at: &str,
) -> Result<EntityConsolidationSummary, CoreError> {
    let clusters = resolve_entities_in_conn(txn, workspace_id, alias_book)?;
    let mut summary = EntityConsolidationSummary {
        total_entities_scanned: clusters.iter().map(|c| c.member_node_ids.len()).sum(),
        ..EntityConsolidationSummary::default()
    };

    for cluster in &clusters {
        if cluster.member_node_ids.len() <= 1 {
            continue;
        }
        summary.clusters_with_merges += 1;
        for member_id in &cluster.member_node_ids {
            let raw_meta: String = txn
                .query_row(
                    "SELECT metadata_json FROM nodes WHERE id = ?1",
                    params![member_id],
                    |r| r.get::<_, Option<String>>(0),
                )?
                .unwrap_or_else(|| "{}".to_string());
            let mut meta: Value = serde_json::from_str(&raw_meta).unwrap_or_else(|_| json!({}));
            if let Some(obj) = meta.as_object_mut() {
                obj.insert(
                    "entity_resolution".to_string(),
                    json!({
                        "canonical_id": cluster.canonical_id,
                        "canonical_title": cluster.canonical_title,
                        "canonical_key": cluster.canonical_key,
                        "is_canonical": member_id == &cluster.canonical_id,
                        "aliases": cluster.aliases,
                        "provenance_node_ids": cluster.provenance_node_ids,
                        "confidence": cluster.confidence,
                        "recorded_at": recorded_at,
                    }),
                );
            }
            txn.execute(
                "UPDATE nodes SET metadata_json = ?1 WHERE id = ?2",
                params![
                    serde_json::to_string(&meta).unwrap_or_else(|_| "{}".to_string()),
                    member_id
                ],
            )?;

            if member_id != &cluster.canonical_id {
                summary.duplicate_nodes_linked += 1;
                let edge_id = format!("er_same_as:{member_id}->{}", cluster.canonical_id);
                let has_edge_meta: bool = txn
                    .prepare("PRAGMA table_info(edges)")?
                    .query_map([], |r| r.get::<_, String>(1))?
                    .filter_map(Result::ok)
                    .any(|col| col == "metadata_json");
                if has_edge_meta {
                    txn.execute(
                        "INSERT INTO edges (id, from_node, to_node, type, weight, metadata_json, created_at)
                         VALUES (?1, ?2, ?3, 'SAME_AS', ?4, '{}', ?5)
                         ON CONFLICT(from_node, to_node, type) DO UPDATE SET weight = excluded.weight",
                        params![
                            edge_id,
                            member_id,
                            cluster.canonical_id,
                            cluster.confidence,
                            recorded_at
                        ],
                    )?;
                } else {
                    txn.execute(
                        "INSERT INTO edges (id, from_node, to_node, type, weight)
                         VALUES (?1, ?2, ?3, 'SAME_AS', ?4)
                         ON CONFLICT(id) DO UPDATE SET weight = excluded.weight",
                        params![edge_id, member_id, cluster.canonical_id, cluster.confidence],
                    )?;
                }
                summary.same_as_edges_upserted += 1;
            }
        }
    }

    Ok(summary)
}

/// Roll back synthesized entity consolidation projections and `er_same_as:*` edges cleanly.
pub fn rollback_entity_consolidation(
    txn: &Transaction<'_>,
    workspace_id: Option<&str>,
) -> Result<usize, CoreError> {
    let target_ws = workspace_id
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .unwrap_or(DEFAULT_WORKSPACE_ID);
    let v2 = has_nodes_v2(txn);
    let sql = if v2 {
        "SELECT n.id, n.metadata_json, v.workspace_id
         FROM nodes n LEFT JOIN nodes_v2 v ON v.id = n.id"
    } else {
        "SELECT n.id, n.metadata_json, NULL FROM nodes n"
    };

    let mut candidates: Vec<(String, Value)> = Vec::new();
    {
        let mut stmt = txn.prepare(sql)?;
        let rows = stmt.query_map([], |r| {
            Ok((
                r.get::<_, String>(0)?,
                r.get::<_, Option<String>>(1)?.unwrap_or_default(),
                r.get::<_, Option<String>>(2)?,
            ))
        })?;
        for row in rows {
            let (id, raw_meta, row_ws) = row?;
            let meta: Value = serde_json::from_str(&raw_meta).unwrap_or_else(|_| json!({}));
            if !node_matches_workspace(row_ws.as_deref(), &meta, target_ws) {
                continue;
            }
            if meta.get("entity_resolution").is_some() {
                candidates.push((id, meta));
            }
        }
    }

    let mut reverted = 0usize;
    for (id, mut meta) in candidates {
        if let Some(obj) = meta.as_object_mut() {
            obj.remove("entity_resolution");
        }
        txn.execute(
            "UPDATE nodes SET metadata_json = ?1 WHERE id = ?2",
            params![
                serde_json::to_string(&meta).unwrap_or_else(|_| "{}".to_string()),
                id
            ],
        )?;
        txn.execute(
            "DELETE FROM edges WHERE id LIKE ?1 OR id LIKE ?2",
            params![format!("er_same_as:{id}->%"), format!("er_same_as:%->{id}")],
        )?;
        reverted += 1;
    }

    Ok(reverted)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn canonical_key_normalizes_separators_case_and_korean_particles() {
        assert_eq!(
            canonical_entity_key("Project-Alpha"),
            canonical_entity_key("project_alpha")
        );
        assert_eq!(
            canonical_entity_key("프로젝트 알파는"),
            canonical_entity_key("프로젝트 알파에서")
        );
    }

    #[test]
    fn consolidation_links_duplicates_preserves_provenance_and_rolls_back() {
        let mut conn = Connection::open_in_memory().expect("mem db");
        conn.execute_batch(
            "CREATE TABLE nodes (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                title TEXT NOT NULL,
                summary TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT '2026-01-01T00:00:00Z'
            );
            CREATE TABLE edges (
                id TEXT PRIMARY KEY,
                from_node TEXT NOT NULL,
                to_node TEXT NOT NULL,
                type TEXT NOT NULL,
                weight REAL NOT NULL DEFAULT 1.0
            );",
        )
        .expect("ddl");

        conn.execute(
            r#"INSERT INTO nodes (id, type, title, summary, metadata_json, created_at) VALUES
             ('doc:1', 'Document', 'Spec 1', 'spec', '{}', '2026-01-01T00:00:00Z'),
             ('doc:2', 'Document', 'Spec 2', 'spec', '{}', '2026-02-01T00:00:00Z'),
             ('ent:1', 'Project', 'Project Alpha', 'Core platform', '{"source_node":"doc:1"}', '2026-01-01T00:00:00Z'),
             ('ent:2', 'Project', 'Project-Alpha', 'Rust rewrite', '{"source_node":"doc:2"}', '2026-02-01T00:00:00Z'),
             ('ent:3', 'Project', '프로젝트 알파는', 'KR reference', '{}', '2026-03-01T00:00:00Z')"#,
            [],
        )
        .expect("seed nodes");
        conn.execute(
            "INSERT INTO edges (id, from_node, to_node, type, weight) VALUES
             ('e1', 'doc:2', 'ent:3', 'MENTIONS', 1.0)",
            [],
        )
        .expect("seed edges");

        let mut book = EntityAliasBook::new();
        book.register_aliases("Project Alpha", ["프로젝트 알파"]);

        let txn = conn.transaction().expect("txn");
        let summary =
            apply_entity_consolidation(&txn, Some("personal"), &book, "2026-11-18T00:00:00Z")
                .expect("consolidate");
        assert_eq!(summary.clusters_with_merges, 1);
        assert_eq!(summary.duplicate_nodes_linked, 2);
        assert_eq!(summary.same_as_edges_upserted, 2);

        let clusters = resolve_entities_in_conn(&txn, Some("personal"), &book).expect("resolve");
        assert_eq!(clusters.len(), 1);
        assert_eq!(clusters[0].canonical_id, "ent:1");
        assert!(clusters[0]
            .provenance_node_ids
            .contains(&"doc:1".to_string()));
        assert!(clusters[0]
            .provenance_node_ids
            .contains(&"doc:2".to_string()));

        // Idempotent second run keeps exact same counts without duplicating SAME_AS rows.
        let summary2 =
            apply_entity_consolidation(&txn, Some("personal"), &book, "2026-11-18T00:00:00Z")
                .expect("idempotent consolidate");
        assert_eq!(summary2.same_as_edges_upserted, 2);
        let same_count: i64 = txn
            .query_row(
                "SELECT COUNT(*) FROM edges WHERE type = 'SAME_AS'",
                [],
                |r| r.get(0),
            )
            .expect("count");
        assert_eq!(same_count, 2);

        // Rollback removes projection cleanly while keeping all 3 entity nodes intact.
        let reverted = rollback_entity_consolidation(&txn, Some("personal")).expect("rollback");
        assert_eq!(reverted, 3);
        let remaining_same: i64 = txn
            .query_row(
                "SELECT COUNT(*) FROM edges WHERE type = 'SAME_AS'",
                [],
                |r| r.get(0),
            )
            .expect("count after rollback");
        assert_eq!(remaining_same, 0);
        txn.commit().expect("commit");
    }
}
