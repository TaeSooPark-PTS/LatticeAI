//! `MemoryService` — the cross-tier report and the maintenance half.
//!
//! Six tiers, three real backends: Workspace OS state (workspace / project /
//! agent), the durable `conversation_messages` table (conversation), and the
//! knowledge graph (graph / vector). The rule the Python module states and this
//! port keeps is *never invent*: a tier with no backing reports `unavailable`
//! and contributes `None`, never a zero dressed up as a measurement — which is
//! why `count` is `Value::Null` rather than `0` for an absent vector index.
//!
//! [`Snapshot`] is the one read every surface here is computed from. Python
//! re-reads the stores per method and pays for it four times inside
//! `brain_brief` (manager → proof → manager again); taking the reads once is
//! the only intentional difference, and it changes no output because nothing
//! writes between them inside a request.

#![allow(
    dead_code,
    unused_imports,
    unused_variables,
    unused_assignments,
    unused_mut,
    private_interfaces,
    clippy::result_large_err,
    clippy::needless_lifetimes,
    clippy::too_many_arguments,
    clippy::type_complexity,
    clippy::collapsible_if,
    clippy::needless_as_bytes,
    clippy::redundant_closure,
    clippy::needless_return,
    clippy::manual_clamp,
    clippy::ptr_arg,
    clippy::unnecessary_sort_by,
    clippy::result_unit_err,
    clippy::useless_vec,
    clippy::uninlined_format_args,
    clippy::manual_contains,
    clippy::needless_borrows_for_generic_args,
    clippy::implicit_clone,
    clippy::unnecessary_map_or,
    clippy::match_like_matches_macro,
    clippy::manual_range_contains,
    clippy::derivable_impls,
    clippy::needless_pass_by_ref_mut,
    clippy::redundant_guards,
    clippy::map_identity,
    clippy::iter_overeager_cloned,
    clippy::explicit_auto_deref,
    clippy::bool_comparison,
    clippy::nonminimal_bool,
    clippy::if_same_then_else,
    clippy::question_mark,
    clippy::single_char_pattern,
    clippy::manual_pattern_char_comparison,
    clippy::manual_is_ascii_check,
    clippy::repeat_once,
    clippy::unused_self,
    clippy::module_inception
)]
use std::collections::BTreeSet;
use std::path::Path;

use lattice_auth::OrderedMap;
use lattice_core::CoreError;
use rusqlite::Connection;
use serde_json::Value;

use super::kg;
use super::shared::BrainState;
use super::wsos;

/// `memory_service.constants.TIERS`.
pub const TIERS: [&str; 6] = [
    "workspace",
    "project",
    "agent",
    "conversation",
    "graph",
    "vector",
];

/// `memory_service.constants.WORKSPACE_KINDS` — `WorkspaceOS.MEMORY_KINDS`.
pub const WORKSPACE_KINDS: [&str; 7] = [
    "short_term",
    "workspace",
    "preferences",
    "decisions",
    "working_style",
    "frequently_used_tools",
    "long_term",
];

/// Longest inline thumbnail a recall row carries.
pub const MAX_RECALL_THUMBNAIL_CHARS: usize = 24_000;

/// Everything the memory surfaces read, taken once.
pub struct Snapshot {
    /// The Workspace OS document.
    pub state: Value,
    /// `_workspace_memories(user, workspace_id or "personal")` — the reading
    /// `manager`, `inspect("workspace")` and the brief all take.
    pub workspace_memories: Vec<Value>,
    /// `_workspace_memories(user, workspace_id)` — **without** the `"personal"`
    /// default. `prune` and `compact` use this one, and the difference is the
    /// ownership guard: defaulting an unscoped delete to Personal would let a
    /// caller prune a workspace they never named.
    pub owned_memories: Vec<Value>,
    /// `manager`'s project tier, already branched on `workspace_id is None`.
    pub project_memories: Vec<Value>,
    /// `_snapshots(workspace_id)`.
    pub snapshots: Vec<Value>,
    /// `_conversations()` — every conversation, ungrouped by scope.
    pub conversations: Vec<Value>,
    /// `_scoped_conversations(user, workspace_id)`.
    pub scoped_conversations: Vec<Value>,
    /// `_kg_stats()`, or `None` when the graph is off or unreadable.
    pub stats: Option<OrderedMap>,
    /// `_kg_index()`, same rule.
    pub index: Option<OrderedMap>,
    /// `_file_size(data_dir/workspace_os.json)`.
    pub workspace_bytes: i64,
    /// `_file_size(data_dir/knowledge_graph.sqlite)` — also the conversation
    /// store's own `size_bytes()`, because it is the same file.
    pub graph_bytes: i64,
}

impl Snapshot {
    /// Take every reading one request needs, on one connection.
    pub fn read(
        conn: &Connection,
        data_dir: &Path,
        graph_enabled: bool,
        user_email: &str,
        workspace_id: Option<&str>,
    ) -> Result<Self, CoreError> {
        let state = wsos::load_from(conn, data_dir);
        let user = Some(user_email).filter(|value| !value.is_empty());
        let personal = workspace_id.unwrap_or(wsos::DEFAULT_WORKSPACE_ID);
        let workspace_memories = wsos::list_memories(&state, user, None, Some(personal));
        let owned_memories = wsos::list_memories(&state, user, None, workspace_id);
        let project_memories = match workspace_id {
            None => wsos::list_memories(&state, None, None, None)
                .into_iter()
                .filter(|item| wsos::record_workspace(item) != wsos::DEFAULT_WORKSPACE_ID)
                .collect(),
            Some(workspace) => wsos::list_memories(&state, user, None, Some(workspace)),
        };
        let snapshots = wsos::list_memory_snapshots(&state, workspace_id, 200);
        let conversations = conversations(conn)?;
        let scoped_conversations = scoped_conversations(&conversations, user_email, workspace_id);
        let db_path = data_dir.join(lattice_core::DB_FILE_NAME);
        let db_display = db_path.display().to_string();
        // `_kg_stats` / `_kg_index` degrade to None rather than raising: both
        // tiers are optional and report themselves `unavailable` upstream.
        let (stats, index) = if graph_enabled {
            (
                kg::stats(conn, &db_display, None).ok(),
                kg::index_status(conn, &db_display).ok(),
            )
        } else {
            (None, None)
        };
        Ok(Self {
            state,
            workspace_memories,
            owned_memories,
            project_memories,
            snapshots,
            conversations,
            scoped_conversations,
            stats,
            index,
            workspace_bytes: file_size(&data_dir.join("workspace_os.json")),
            graph_bytes: file_size(&db_path),
        })
    }
}

fn file_size(path: &Path) -> i64 {
    std::fs::metadata(path)
        .map(|meta| meta.len() as i64)
        .unwrap_or(0)
}

/// `_conversations()` over the durable store: group `history()` by id.
///
/// `dict.setdefault` keeps first-seen order, and a row whose `conversation_id`
/// column is NULL is filed under the legacy bucket — the same id
/// `history::group_conversations` uses, so the two views agree.
pub fn conversations(conn: &Connection) -> Result<Vec<Value>, CoreError> {
    let history =
        crate::history::history(conn, None, None, &crate::history::HistoryScope::owner())?;
    let mut order: Vec<String> = Vec::new();
    let mut grouped: std::collections::HashMap<String, Vec<Value>> =
        std::collections::HashMap::new();
    for item in history {
        let id = item
            .get("conversation_id")
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty())
            .unwrap_or("legacy-previous-history")
            .to_string();
        if !grouped.contains_key(&id) {
            order.push(id.clone());
        }
        grouped.entry(id).or_default().push(item);
    }
    Ok(order
        .into_iter()
        .map(|id| {
            let messages = grouped.remove(&id).unwrap_or_default();
            let mut record = OrderedMap::new();
            record.insert("id", Value::String(id));
            record.insert("messages", Value::Array(messages));
            serde_json::to_value(&record).unwrap_or(Value::Null)
        })
        .collect())
}

/// `_scoped_conversations` — an anonymous caller sees every conversation.
pub fn scoped_conversations(
    conversations: &[Value],
    user_email: &str,
    workspace_id: Option<&str>,
) -> Vec<Value> {
    if user_email.is_empty() {
        return conversations.to_vec();
    }
    let target = workspace_id.unwrap_or(wsos::DEFAULT_WORKSPACE_ID);
    let mut scoped = Vec::new();
    for conversation in conversations {
        let Some(messages) = conversation.get("messages").and_then(Value::as_array) else {
            continue;
        };
        let kept: Vec<Value> = messages
            .iter()
            .filter(|message| {
                message.is_object()
                    && message.get("user_email").and_then(Value::as_str) == Some(user_email)
                    && message
                        .get("workspace_id")
                        .and_then(Value::as_str)
                        .filter(|value| !value.is_empty())
                        .unwrap_or(wsos::DEFAULT_WORKSPACE_ID)
                        == target
            })
            .cloned()
            .collect();
        if kept.is_empty() {
            continue;
        }
        let mut record = conversation.clone();
        if let Some(object) = record.as_object_mut() {
            object.insert("messages".to_string(), Value::Array(kept));
        }
        scoped.push(record);
    }
    scoped
}

fn sum_counts(counts: Option<&Value>) -> Option<i64> {
    counts
        .and_then(Value::as_object)
        .map(|map| map.values().filter_map(Value::as_i64).sum())
}

/// `MemoryManagerMixin._brain_readiness`.
pub fn brain_readiness(
    memory_count: i64,
    concept_count: Option<i64>,
    relationship_count: Option<i64>,
    healthy_sources: i64,
) -> OrderedMap {
    let concepts = concept_count.unwrap_or(0).max(0);
    let relationships = relationship_count.unwrap_or(0).max(0);
    let memories = memory_count.max(0);
    let healthy = healthy_sources.max(0);
    let mut score = (memories * 12 + concepts * 8 + relationships * 4 + healthy * 3).min(100);
    let (state, depth, title_key, action_key) = if memories < 1 && concepts < 1 {
        score = score.max(12);
        ("quiet", 2, "brain.readiness.quiet", "brain.readiness.start")
    } else if concepts < 3 || relationships < 2 {
        score = score.max(38);
        let depth = if concepts < 3 { 3 } else { 4 };
        (
            "forming",
            depth,
            "brain.readiness.forming",
            "brain.readiness.grow",
        )
    } else {
        score = score.max(72);
        ("alive", 5, "brain.readiness.alive", "brain.readiness.map")
    };
    let mut signals = OrderedMap::new();
    signals.insert("memory_count", Value::from(memories));
    signals.insert("concept_count", Value::from(concepts));
    signals.insert("relationship_count", Value::from(relationships));
    signals.insert("healthy_sources", Value::from(healthy));
    let mut out = OrderedMap::new();
    out.insert("score", Value::from(score));
    out.insert("state", Value::String(state.to_string()));
    out.insert("depth", Value::from(depth));
    out.insert("title_key", Value::String(title_key.to_string()));
    out.insert("action_key", Value::String(action_key.to_string()));
    out.insert("signals", json(&signals));
    out.insert("source", Value::String("memory_service".to_string()));
    out
}

fn json(map: &OrderedMap) -> Value {
    serde_json::to_value(map).unwrap_or(Value::Null)
}

fn source_row(
    id: &str,
    label: &str,
    count: Value,
    size_bytes: i64,
    health: &str,
    detail: &str,
    edges: Option<Value>,
) -> OrderedMap {
    let mut row = OrderedMap::new();
    row.insert("id", Value::String(id.to_string()));
    row.insert("type", Value::String(id.to_string()));
    row.insert("label", Value::String(label.to_string()));
    row.insert("count", count);
    row.insert("size_bytes", Value::from(size_bytes));
    row.insert("health", Value::String(health.to_string()));
    row.insert("detail", Value::String(detail.to_string()));
    if let Some(edges) = edges {
        row.insert("edges", edges);
    }
    row
}

/// `MemoryManagerMixin.manager` — the report every other surface rests on.
pub fn manager(snapshot: &Snapshot, graph_enabled: bool, now: &str) -> OrderedMap {
    let node_total = snapshot
        .stats
        .as_ref()
        .and_then(|stats| sum_counts(stats.get("nodes")));
    let edge_total = snapshot
        .stats
        .as_ref()
        .and_then(|stats| sum_counts(stats.get("edges")));
    // `vector_counts` is not a key `index_status()` produces, and neither are
    // `indexed` / `ready` — so a live store answers `None` here. Reproduced as
    // it is, not as it reads: the Vector row's `count` is genuinely null.
    let vector_total: Option<i64> = snapshot.index.as_ref().and_then(|index| {
        match index.get("vector_counts").and_then(Value::as_object) {
            Some(counts) => Some(counts.values().filter_map(Value::as_i64).sum()),
            None => index
                .get("indexed")
                .and_then(Value::as_i64)
                .or_else(|| index.get("ready").and_then(Value::as_i64)),
        }
    });
    let conv_bytes = snapshot.graph_bytes;
    let sources = vec![
        source_row(
            "workspace",
            "Workspace Memory",
            Value::from(snapshot.workspace_memories.len() as i64),
            if snapshot.workspace_memories.is_empty() {
                0
            } else {
                snapshot.workspace_bytes
            },
            "ok",
            "Personal workspace knowledge, by kind.",
            None,
        ),
        source_row(
            "project",
            "Project Memory",
            Value::from(snapshot.project_memories.len() as i64),
            0,
            "ok",
            "Memory scoped to organization workspaces.",
            None,
        ),
        source_row(
            "agent",
            "Agent Memory",
            Value::from(snapshot.snapshots.len() as i64),
            0,
            "ok",
            "Per-run agent memory snapshots.",
            None,
        ),
        source_row(
            "conversation",
            "Conversation Memory",
            Value::from(snapshot.scoped_conversations.len() as i64),
            conv_bytes,
            // The durable conversation store is always wired in this build, so
            // the tier is `ok` even with nothing in it — the JSON-file fallback
            // is the only branch that could read `empty`.
            "ok",
            "Historical interaction memory from chat.",
            None,
        ),
        source_row(
            "graph",
            "Graph Memory",
            node_total.map(Value::from).unwrap_or(Value::Null),
            snapshot.graph_bytes,
            if snapshot.stats.is_some() {
                "ok"
            } else {
                "unavailable"
            },
            if snapshot.stats.is_some() {
                "Knowledge Graph entities and relations."
            } else {
                "Knowledge graph disabled or unavailable."
            },
            Some(edge_total.map(Value::from).unwrap_or(Value::Null)),
        ),
        source_row(
            "vector",
            "Vector Memory",
            vector_total.map(Value::from).unwrap_or(Value::Null),
            0,
            if snapshot.index.is_some() {
                "ok"
            } else {
                "unavailable"
            },
            if snapshot.index.is_some() {
                "Local embedding vector index."
            } else {
                "Vector index unavailable."
            },
            None,
        ),
    ];
    let total_items: i64 = sources
        .iter()
        .map(|row| row.get("count").and_then(Value::as_i64).unwrap_or(0))
        .sum();
    let healthy = sources
        .iter()
        .filter(|row| row.get("health") == Some(&Value::String("ok".to_string())))
        .count() as i64;
    let overall = if healthy >= 4 {
        "ok"
    } else if healthy >= 1 {
        "degraded"
    } else {
        "unavailable"
    };
    let mut memory_ids: BTreeSet<String> = BTreeSet::new();
    for item in snapshot
        .workspace_memories
        .iter()
        .chain(snapshot.project_memories.iter())
    {
        if let Some(id) = item
            .get("id")
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty())
        {
            memory_ids.insert(id.to_string());
        }
    }
    let memory_count = memory_ids.len() as i64
        + snapshot.snapshots.len() as i64
        + snapshot.scoped_conversations.len() as i64;

    let mut usage = OrderedMap::new();
    usage.insert("total_items", Value::from(total_items));
    usage.insert(
        "total_bytes",
        Value::from(snapshot.workspace_bytes + snapshot.graph_bytes + conv_bytes),
    );
    usage.insert("sources", Value::from(sources.len() as i64));

    let mut out = OrderedMap::new();
    out.insert(
        "sources",
        Value::Array(sources.iter().map(json).collect::<Vec<_>>()),
    );
    out.insert("recent_memories", recent_memories(snapshot, 8));
    out.insert(
        "tiers",
        Value::Array(TIERS.iter().map(|t| Value::String(t.to_string())).collect()),
    );
    out.insert("usage", json(&usage));
    out.insert(
        "brain_readiness",
        json(&brain_readiness(
            memory_count,
            node_total,
            edge_total,
            healthy,
        )),
    );
    out.insert("health", Value::String(overall.to_string()));
    out.insert("graph_enabled", Value::Bool(graph_enabled));
    out.insert("generated_at", Value::String(now.to_string()));
    out
}

/// `_manager_recent_memories` — the first `limit` of workspace + project.
fn recent_memories(snapshot: &Snapshot, limit: usize) -> Value {
    let mut rows = Vec::new();
    for item in snapshot
        .workspace_memories
        .iter()
        .chain(snapshot.project_memories.iter())
        .take(limit.max(1))
    {
        let mut row = OrderedMap::new();
        row.insert("id", Value::String(text_or(item, "id", "").to_string()));
        row.insert("kind", Value::String(nonempty_or(item, "kind", "memory")));
        row.insert(
            "content",
            Value::String(lattice_core::truncate_chars(
                text_or(item, "content", ""),
                320,
            )),
        );
        row.insert(
            "tags",
            match item.get("tags") {
                Some(Value::Array(tags)) => Value::Array(tags.clone()),
                _ => Value::Array(Vec::new()),
            },
        );
        row.insert(
            "metadata",
            match item.get("metadata") {
                Some(value @ Value::Object(_)) => value.clone(),
                _ => Value::Object(serde_json::Map::new()),
            },
        );
        row.insert(
            "workspace_id",
            Value::String(nonempty_or(
                item,
                "workspace_id",
                wsos::DEFAULT_WORKSPACE_ID,
            )),
        );
        row.insert(
            "created_at",
            item.get("created_at").cloned().unwrap_or(Value::Null),
        );
        row.insert(
            "updated_at",
            item.get("updated_at").cloned().unwrap_or(Value::Null),
        );
        rows.push(json(&row));
    }
    Value::Array(rows)
}

/// `str(item.get(key) or "")` — the reading Python takes everywhere here.
pub fn text_or<'a>(item: &'a Value, key: &str, default: &'a str) -> &'a str {
    item.get(key).and_then(Value::as_str).unwrap_or(default)
}

/// `item.get(key) or fallback` on a string field.
pub fn nonempty_or(item: &Value, key: &str, fallback: &str) -> String {
    item.get(key)
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .unwrap_or(fallback)
        .to_string()
}

/// `MemoryRecallMixin.tiers`.
pub fn tiers() -> OrderedMap {
    let mut out = OrderedMap::new();
    out.insert(
        "tiers",
        Value::Array(TIERS.iter().map(|t| Value::String(t.to_string())).collect()),
    );
    out.insert(
        "workspace_kinds",
        Value::Array(
            WORKSPACE_KINDS
                .iter()
                .map(|t| Value::String(t.to_string()))
                .collect(),
        ),
    );
    out
}

/// `MemoryRecallMixin.inspect` — `None` for a source the service does not know.
pub fn inspect(snapshot: &Snapshot, source: &str, limit: i64) -> Option<OrderedMap> {
    let cap = limit.max(0) as usize;
    let mut out = OrderedMap::new();
    out.insert("source", Value::String(source.to_string()));
    match source {
        "workspace" | "project" | "agent" => {
            let items: Vec<Value> = match source {
                "workspace" => snapshot
                    .workspace_memories
                    .iter()
                    .take(cap)
                    .cloned()
                    .collect(),
                "project" => snapshot
                    .project_memories
                    .iter()
                    .take(cap)
                    .cloned()
                    .collect(),
                _ => snapshot.snapshots.iter().take(cap).cloned().collect(),
            };
            let count = items.len() as i64;
            out.insert("items", Value::Array(items));
            out.insert("count", Value::from(count));
        }
        "conversation" => {
            let items: Vec<Value> = snapshot
                .scoped_conversations
                .iter()
                .take(cap)
                .map(|conversation| {
                    let id = conversation.get("id").cloned().unwrap_or(Value::Null);
                    let mut row = OrderedMap::new();
                    row.insert("id", id.clone());
                    row.insert(
                        "title",
                        match conversation.get("title") {
                            Some(value @ Value::String(text)) if !text.is_empty() => value.clone(),
                            _ => id,
                        },
                    );
                    row.insert(
                        "messages",
                        Value::from(
                            conversation
                                .get("messages")
                                .and_then(Value::as_array)
                                .map(Vec::len)
                                .unwrap_or(0) as i64,
                        ),
                    );
                    json(&row)
                })
                .collect();
            out.insert("items", Value::Array(items));
            out.insert(
                "count",
                Value::from(snapshot.scoped_conversations.len() as i64),
            );
        }
        "graph" => {
            out.insert(
                "stats",
                snapshot
                    .stats
                    .as_ref()
                    .map(json)
                    .unwrap_or(Value::Object(serde_json::Map::new())),
            );
            out.insert("available", Value::Bool(snapshot.stats.is_some()));
        }
        "vector" => {
            out.insert(
                "index",
                snapshot
                    .index
                    .as_ref()
                    .map(json)
                    .unwrap_or(Value::Object(serde_json::Map::new())),
            );
            out.insert("available", Value::Bool(snapshot.index.is_some()));
        }
        _ => return None,
    }
    Some(out)
}

// ── the mutating half ───────────────────────────────────────────────────────

/// The answer `prune` builds, before it becomes a response body.
pub struct PruneOutcome {
    /// Ids that were removed, in the order they were targeted.
    pub removed: Vec<String>,
    /// Ids the caller asked for but does not own.
    pub skipped: Vec<String>,
    /// Ids whose deletion failed, with the reason.
    pub failed: Vec<(String, String)>,
}

impl PruneOutcome {
    /// `{"removed", "count"}` plus the two optional blocks.
    pub fn to_body(&self) -> OrderedMap {
        let mut out = OrderedMap::new();
        out.insert(
            "removed",
            Value::Array(
                self.removed
                    .iter()
                    .map(|id| Value::String(id.clone()))
                    .collect(),
            ),
        );
        out.insert("count", Value::from(self.removed.len() as i64));
        if !self.skipped.is_empty() {
            out.insert(
                "skipped",
                Value::Array(
                    self.skipped
                        .iter()
                        .map(|id| Value::String(id.clone()))
                        .collect(),
                ),
            );
        }
        if !self.failed.is_empty() {
            out.insert("failed", failed_rows(&self.failed));
            out.insert(
                "status",
                Value::String(
                    if self.removed.is_empty() {
                        "error"
                    } else {
                        "partial"
                    }
                    .to_string(),
                ),
            );
        }
        out
    }
}

fn failed_rows(failed: &[(String, String)]) -> Value {
    Value::Array(
        failed
            .iter()
            .map(|(id, detail)| {
                let mut row = OrderedMap::new();
                row.insert("id", Value::String(id.clone()));
                row.insert("detail", Value::String(detail.clone()));
                json(&row)
            })
            .collect(),
    )
}

/// `MemoryMaintenanceMixin.prune`, ownership guard included.
///
/// The guard is the point: both the explicit-id path and the by-kind path are
/// intersected with the caller's *own* memories, so a forged id belonging to
/// someone else is reported `skipped` rather than silently deleted.
pub fn prune(
    state: &BrainState,
    snapshot: &Snapshot,
    ids: &[String],
    kind: Option<&str>,
) -> PruneOutcome {
    let owned: BTreeSet<String> = snapshot
        .owned_memories
        .iter()
        .filter_map(|item| item.get("id").and_then(Value::as_str))
        .filter(|id| !id.is_empty())
        .map(str::to_string)
        .collect();
    let mut seen: BTreeSet<String> = BTreeSet::new();
    let mut targets: Vec<String> = Vec::new();
    let mut skipped: Vec<String> = Vec::new();
    for id in ids {
        if !seen.insert(id.clone()) {
            continue;
        }
        if owned.contains(id) {
            targets.push(id.clone());
        } else {
            skipped.push(id.clone());
        }
    }
    if let Some(kind) = kind.filter(|value| !value.is_empty()) {
        for item in &snapshot.owned_memories {
            let matches = item.get("kind").and_then(Value::as_str) == Some(kind);
            let id = item.get("id").and_then(Value::as_str).unwrap_or_default();
            if matches && !id.is_empty() && seen.insert(id.to_string()) {
                targets.push(id.to_string());
            }
        }
    }
    delete_all(state, &targets, skipped)
}

/// `MemoryMaintenanceMixin.compact` — drop repeats of one `(kind, content)`.
pub fn compact(state: &BrainState, snapshot: &Snapshot) -> OrderedMap {
    let mut seen: BTreeSet<(String, String)> = BTreeSet::new();
    let mut targets: Vec<String> = Vec::new();
    // Oldest first, so the first occurrence — the oldest — is the one kept.
    for item in snapshot.owned_memories.iter().rev() {
        let key = (
            nonempty_or(item, "kind", ""),
            lattice_core::pytext::strip(text_or(item, "content", "")),
        );
        if seen.contains(&key) {
            if let Some(id) = item
                .get("id")
                .and_then(Value::as_str)
                .filter(|value| !value.is_empty())
            {
                targets.push(id.to_string());
            }
        } else {
            seen.insert(key);
        }
    }
    let outcome = delete_all(state, &targets, Vec::new());
    let mut out = OrderedMap::new();
    out.insert("compacted", Value::from(outcome.removed.len() as i64));
    out.insert(
        "removed",
        Value::Array(
            outcome
                .removed
                .iter()
                .map(|id| Value::String(id.clone()))
                .collect(),
        ),
    );
    out.insert("remaining", Value::from(seen.len() as i64));
    out.insert("failed", failed_rows(&outcome.failed));
    out.insert(
        "status",
        Value::String(
            if !outcome.failed.is_empty() && !outcome.removed.is_empty() {
                "partial"
            } else if !outcome.failed.is_empty() {
                "error"
            } else {
                "ok"
            }
            .to_string(),
        ),
    );
    out
}

fn delete_all(state: &BrainState, targets: &[String], skipped: Vec<String>) -> PruneOutcome {
    let mut removed = Vec::new();
    let mut failed = Vec::new();
    for id in targets {
        match wsos::delete_memory(state.store(), state.data_dir(), id) {
            Ok(true) => removed.push(id.clone()),
            // `WorkspaceOSStore.delete_memory` raises FileNotFoundError for an
            // id that vanished between the read and the write; Python's
            // `except Exception` files that as a failure, not a silent skip.
            Ok(false) => failed.push((id.clone(), id.clone())),
            Err(error) => failed.push((id.clone(), error.to_string())),
        }
    }
    PruneOutcome {
        removed,
        skipped,
        failed,
    }
}

/// `MemoryMaintenanceMixin.clear`'s scope router, as a decision.
pub enum ClearPlan {
    /// `scope` is one of `WORKSPACE_KINDS` — prune by kind.
    ByKind(String),
    /// Every other scope is refused with this exact sentence.
    Refused(String),
}

/// `clear(scope, confirm)` before anything is deleted.
pub fn clear_plan(scope: &str, confirm: bool) -> ClearPlan {
    if !confirm {
        return ClearPlan::Refused("clear requires confirm=true".to_string());
    }
    if WORKSPACE_KINDS.contains(&scope) {
        return ClearPlan::ByKind(scope.to_string());
    }
    if scope == "graph" {
        return ClearPlan::Refused(
            "graph clear is disabled from Memory Manager because it is not workspace-scoped"
                .to_string(),
        );
    }
    ClearPlan::Refused(format!("unsupported clear scope: {scope}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn readiness_scores_the_way_the_product_grades_a_brain() {
        let quiet = brain_readiness(0, Some(0), Some(0), 0);
        assert_eq!(quiet.get("state"), Some(&Value::String("quiet".into())));
        assert_eq!(quiet.get("score"), Some(&Value::from(12)));
        assert_eq!(quiet.get("depth"), Some(&Value::from(2)));
        let forming = brain_readiness(1, Some(2), Some(5), 2);
        assert_eq!(forming.get("state"), Some(&Value::String("forming".into())));
        assert_eq!(forming.get("depth"), Some(&Value::from(3)));
        let deeper = brain_readiness(1, Some(4), Some(1), 2);
        assert_eq!(deeper.get("depth"), Some(&Value::from(4)));
        let alive = brain_readiness(2, Some(62), Some(68), 6);
        assert_eq!(alive.get("state"), Some(&Value::String("alive".into())));
        assert_eq!(alive.get("score"), Some(&Value::from(100)));
        // An unmeasured tier contributes nothing rather than a zero.
        assert_eq!(
            brain_readiness(0, None, None, 0).get("score"),
            Some(&Value::from(12))
        );
    }

    #[test]
    fn conversations_are_grouped_first_seen_first_with_a_legacy_bucket() {
        let history = vec![
            serde_json::json!({"conversation_id": "c1", "content": "a"}),
            serde_json::json!({"content": "orphan"}),
            serde_json::json!({"conversation_id": "c1", "content": "b"}),
        ];
        let mut order: Vec<String> = Vec::new();
        let mut grouped: std::collections::HashMap<String, Vec<Value>> = Default::default();
        for item in history {
            let id = item
                .get("conversation_id")
                .and_then(Value::as_str)
                .filter(|v| !v.is_empty())
                .unwrap_or("legacy-previous-history")
                .to_string();
            if !grouped.contains_key(&id) {
                order.push(id.clone());
            }
            grouped.entry(id).or_default().push(item);
        }
        assert_eq!(order, vec!["c1", "legacy-previous-history"]);
        assert_eq!(grouped["c1"].len(), 2);
    }

    #[test]
    fn a_scoped_conversation_keeps_only_the_callers_own_messages() {
        let conversations = vec![serde_json::json!({
            "id": "c1",
            "messages": [
                {"content": "mine", "user_email": "me@x", "workspace_id": "personal"},
                {"content": "theirs", "user_email": "you@x", "workspace_id": "personal"},
                {"content": "elsewhere", "user_email": "me@x", "workspace_id": "org"},
            ]
        })];
        let scoped = scoped_conversations(&conversations, "me@x", None);
        assert_eq!(scoped.len(), 1);
        assert_eq!(scoped[0]["messages"].as_array().expect("messages").len(), 1);
        assert_eq!(scoped[0]["messages"][0]["content"], "mine");
        assert_eq!(
            scoped_conversations(&conversations, "", None).len(),
            1,
            "the trusted local owner is unscoped"
        );
        assert!(scoped_conversations(&conversations, "nobody@x", None).is_empty());
    }

    #[test]
    fn the_clear_router_refuses_everything_it_does_not_own() {
        assert!(matches!(
            clear_plan("decisions", true),
            ClearPlan::ByKind(_)
        ));
        match clear_plan("conversations", true) {
            ClearPlan::Refused(detail) => {
                assert_eq!(detail, "unsupported clear scope: conversations")
            }
            _ => panic!("conversations is not a clear scope"),
        }
        match clear_plan("decisions", false) {
            ClearPlan::Refused(detail) => assert_eq!(detail, "clear requires confirm=true"),
            _ => panic!("confirm is the guard"),
        }
        match clear_plan("graph", true) {
            ClearPlan::Refused(detail) => assert!(detail.starts_with("graph clear is disabled")),
            _ => panic!("graph is refused outright"),
        }
    }

    #[test]
    fn a_prune_outcome_reports_partial_and_error_apart() {
        let clean = PruneOutcome {
            removed: vec!["a".into()],
            skipped: Vec::new(),
            failed: Vec::new(),
        };
        let body = serde_json::to_value(clean.to_body()).expect("json");
        assert_eq!(body, serde_json::json!({"removed": ["a"], "count": 1}));
        let partial = PruneOutcome {
            removed: vec!["a".into()],
            skipped: vec!["b".into()],
            failed: vec![("c".into(), "boom".into())],
        };
        let body = serde_json::to_value(partial.to_body()).expect("json");
        assert_eq!(body["status"], "partial");
        assert_eq!(body["skipped"], serde_json::json!(["b"]));
        assert_eq!(body["failed"][0]["detail"], "boom");
        let all_bad = PruneOutcome {
            removed: Vec::new(),
            skipped: Vec::new(),
            failed: vec![("c".into(), "boom".into())],
        };
        assert_eq!(
            serde_json::to_value(all_bad.to_body()).expect("json")["status"],
            "error"
        );
    }

    #[test]
    fn tiers_are_the_vocabulary_the_ui_renders() {
        let body = serde_json::to_value(tiers()).expect("json");
        assert_eq!(body["tiers"][0], "workspace");
        assert_eq!(body["tiers"].as_array().expect("tiers").len(), 6);
        assert_eq!(body["workspace_kinds"].as_array().expect("kinds").len(), 7);
    }
}
