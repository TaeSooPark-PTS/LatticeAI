//! Personal and workspace memory records.
//!
//! Port of `core/workspace_memory.py` plus the `get_memory` / `delete_memory` /
//! `search_memories` methods the store keeps beside it.
//!
//! An upsert also ingests the memory into the knowledge graph, and a graph
//! write is not this process's to make (WAVE2_COMMON rule 6). So the operation
//! is split in two: [`plan`] decides what the record will be — including which
//! workspace it belongs to, which the seam call needs — and [`commit`] writes
//! it with whatever the seam answered. Python does the same three steps in the
//! same order inside one function; splitting them is what lets the middle one
//! be an `await`.

use serde_json::{json, Map, Value};

use super::constants::MEMORY_KINDS;
use super::pyutil::{json_hash_prefix, listify, now_iso};
use super::store::{StoreError, WorkspaceOsStore};

/// A memory record decided but not yet written.
#[derive(Debug, Clone)]
pub struct MemoryPlan {
    /// The record as it will be stored, before the graph outcome is attached.
    pub record: Value,
    /// Whether this is a new record or an update of an existing one.
    pub is_new: bool,
}

impl MemoryPlan {
    /// The workspace the ingest should be tagged with.
    pub fn workspace_id(&self) -> Option<&str> {
        self.record.get("workspace_id").and_then(Value::as_str)
    }

    /// The memory id, for the ingest metadata.
    pub fn memory_id(&self) -> &str {
        self.record
            .get("id")
            .and_then(Value::as_str)
            .unwrap_or_default()
    }

    /// The kind, for the ingest title.
    pub fn kind(&self) -> &str {
        self.record
            .get("kind")
            .and_then(Value::as_str)
            .unwrap_or_default()
    }

    /// The content, for the ingest title.
    pub fn content(&self) -> &str {
        self.record
            .get("content")
            .and_then(Value::as_str)
            .unwrap_or_default()
    }

    /// The tags, for the ingest metadata.
    pub fn tags(&self) -> Value {
        self.record
            .get("tags")
            .cloned()
            .unwrap_or_else(|| json!([]))
    }
}

/// Decide the record an upsert will write.
#[allow(clippy::too_many_arguments)]
pub fn plan(
    store: &WorkspaceOsStore,
    kind: &str,
    content: &str,
    user_email: Option<&str>,
    tags: &[Value],
    memory_id: Option<&str>,
    metadata: &Value,
    workspace_id: Option<&str>,
) -> Result<MemoryPlan, StoreError> {
    if !MEMORY_KINDS.contains(&kind) {
        return Err(StoreError::Value(format!("unknown memory kind: {kind}")));
    }
    if content.trim().is_empty() {
        return Err(StoreError::Value("content is required".into()));
    }
    let state = store.load_state();
    let memories = listify(state.get("memories"));
    let now = now_iso();
    let memory_id = memory_id
        .filter(|value| !value.is_empty())
        .map(str::to_string)
        .unwrap_or_else(|| {
            format!(
                "memory-{}",
                json_hash_prefix(&json!([kind, content, user_email, now]), 16,)
            )
        });
    let existing = memories
        .iter()
        .find(|item| item.get("id").and_then(Value::as_str) == Some(memory_id.as_str()))
        .cloned();
    let is_new = existing.is_none();
    let mut record = existing.unwrap_or_else(|| json!({"id": memory_id, "created_at": now}));

    // A new record lands in the resolved scope; an existing one keeps its own,
    // so a re-scoped caller cannot drag someone else's memory into their vault.
    let scope = if is_new {
        WorkspaceOsStore::resolve_scope(workspace_id, &state)
    } else {
        WorkspaceOsStore::record_workspace(&record)
    };
    let mut merged_metadata = match metadata {
        Value::Object(map) => map.clone(),
        _ => Map::new(),
    };
    merged_metadata.insert("memory_scope".into(), json!(kind));

    record["kind"] = json!(kind);
    record["content"] = json!(content);
    record["user_email"] = user_email.map_or(Value::Null, |email| json!(email));
    record["tags"] = Value::Array(tags.to_vec());
    record["metadata"] = Value::Object(merged_metadata);
    record["workspace_id"] = json!(scope);
    record["updated_at"] = json!(now);
    Ok(MemoryPlan { record, is_new })
}

/// Write the planned record, carrying whatever the graph seam answered.
pub fn commit(
    store: &WorkspaceOsStore,
    plan: MemoryPlan,
    graph: Option<Result<Value, String>>,
) -> Result<Value, StoreError> {
    let MemoryPlan { mut record, is_new } = plan;
    match graph {
        Some(Ok(ingested)) => {
            record["graph_node_id"] = ingested.get("node_id").cloned().unwrap_or(Value::Null);
        }
        Some(Err(error)) => {
            record["graph_error"] = json!(error);
        }
        None => {}
    }
    let stored = record.clone();
    let memory_id = record
        .get("id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string();
    let workspace_id = WorkspaceOsStore::record_workspace(&record);
    let kind = record
        .get("kind")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string();
    store.mutate(|state| {
        let mut memories = listify(state.get("memories"));
        if is_new {
            memories.push(stored);
        } else {
            for item in memories.iter_mut() {
                if item.get("id").and_then(Value::as_str) == Some(memory_id.as_str()) {
                    *item = stored.clone();
                }
            }
        }
        state["memories"] = Value::Array(memories);
        Ok(())
    })?;
    store.record_timeline_event(
        "memory",
        "memory_upserted",
        json!({"memory_id": memory_id, "kind": kind}),
        Some(&workspace_id),
    );
    Ok(record)
}

/// `list_memories` — newest first, scoped, and filtered by owner and kind.
///
/// The owner filter keeps records with **no** owner as well as this caller's:
/// ownerless memories predate accounts and stay visible to the local user.
pub fn list_memories(
    store: &WorkspaceOsStore,
    user_email: Option<&str>,
    kind: Option<&str>,
    workspace_id: Option<&str>,
) -> Value {
    let state = store.load_state();
    let mut memories = WorkspaceOsStore::scoped(listify(state.get("memories")), workspace_id);
    if let Some(email) = user_email.filter(|value| !value.is_empty()) {
        memories.retain(|item| match item.get("user_email") {
            None | Some(Value::Null) => true,
            Some(Value::String(owner)) => owner == email,
            Some(_) => false,
        });
    }
    if let Some(kind) = kind.filter(|value| !value.is_empty()) {
        memories.retain(|item| item.get("kind").and_then(Value::as_str) == Some(kind));
    }
    memories.reverse();
    json!({"memories": memories})
}

/// `search_memories` — substring over content, tags and kind.
pub fn search_memories(
    store: &WorkspaceOsStore,
    query: &str,
    user_email: Option<&str>,
    limit: i64,
    workspace_id: Option<&str>,
) -> Value {
    let needle = query.trim().to_lowercase();
    let listed = list_memories(store, user_email, None, workspace_id);
    let mut memories = listify(listed.get("memories"));
    if !needle.is_empty() {
        memories.retain(|item| {
            let content = item
                .get("content")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_lowercase();
            let tags = listify(item.get("tags"))
                .iter()
                .map(|tag| tag.as_str().unwrap_or_default().to_string())
                .collect::<Vec<_>>()
                .join(" ")
                .to_lowercase();
            let kind = item
                .get("kind")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_lowercase();
            content.contains(&needle) || tags.contains(&needle) || kind.contains(&needle)
        });
    }
    let cap = limit.clamp(1, 100) as usize;
    memories.truncate(cap);
    json!({"query": query, "memories": memories})
}

/// `get_memory` — the stored record, or not-found.
pub fn get_memory(store: &WorkspaceOsStore, memory_id: &str) -> Result<Value, StoreError> {
    listify(store.load_state().get("memories"))
        .into_iter()
        .find(|item| item.get("id").and_then(Value::as_str) == Some(memory_id))
        .ok_or_else(|| StoreError::NotFound(memory_id.to_string()))
}

/// `delete_memory`.
pub fn delete_memory(store: &WorkspaceOsStore, memory_id: &str) -> Result<Value, StoreError> {
    let workspace_id = store.mutate(|state| {
        let memories = listify(state.get("memories"));
        let target = memories
            .iter()
            .find(|item| item.get("id").and_then(Value::as_str) == Some(memory_id))
            .cloned()
            .ok_or_else(|| StoreError::NotFound(memory_id.to_string()))?;
        state["memories"] = Value::Array(
            memories
                .into_iter()
                .filter(|item| item.get("id").and_then(Value::as_str) != Some(memory_id))
                .collect(),
        );
        Ok(target
            .get("workspace_id")
            .and_then(Value::as_str)
            .map(str::to_string))
    })?;
    store.record_timeline_event(
        "memory",
        "memory_deleted",
        json!({"memory_id": memory_id}),
        workspace_id.as_deref(),
    );
    Ok(json!({"status": "ok", "memory_id": memory_id}))
}

/// `authorize_memory_delete` — owning the record, or writing its workspace.
///
/// Ownerless records with no workspace keep their pre-v4 behaviour: any
/// authenticated local user may delete them.
pub fn authorize_delete(
    store: &WorkspaceOsStore,
    record: &Value,
    user_id: Option<&str>,
    resolved_user: Option<&str>,
) -> Result<(), StoreError> {
    let owner = record
        .get("user_email")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty());
    let workspace_id = record
        .get("workspace_id")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty());
    let is_owner =
        owner.is_some_and(|owner| Some(owner) == user_id || Some(owner) == resolved_user);
    if is_owner {
        return Ok(());
    }
    if let Some(workspace_id) = workspace_id {
        if super::orgs::has_permission(store, workspace_id, resolved_user, "write") {
            return Ok(());
        }
        return Err(StoreError::Permission(format!(
            "'{}' lacks 'write' on workspace '{workspace_id}'",
            user_id
                .filter(|value| !value.is_empty())
                .unwrap_or("anonymous"),
        )));
    }
    if owner.is_some() {
        return Err(StoreError::Permission(format!(
            "'{}' is not the owner of memory '{}'",
            user_id
                .filter(|value| !value.is_empty())
                .unwrap_or("anonymous"),
            record.get("id").and_then(Value::as_str).unwrap_or_default(),
        )));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn store() -> (tempfile::TempDir, WorkspaceOsStore) {
        let dir = tempfile::tempdir().expect("tempdir");
        let store = WorkspaceOsStore::open(dir.path());
        (dir, store)
    }

    fn upsert(store: &WorkspaceOsStore, kind: &str, content: &str, owner: Option<&str>) -> Value {
        let planned = plan(store, kind, content, owner, &[], None, &json!({}), None).unwrap();
        commit(store, planned, None).unwrap()
    }

    #[test]
    fn an_upsert_stamps_the_scope_the_metadata_and_the_timeline() {
        let (_dir, store) = store();
        let record = upsert(&store, "decisions", "유지한다", Some("a@b.test"));
        assert!(record["id"].as_str().unwrap().starts_with("memory-"));
        assert_eq!(record["workspace_id"], json!("personal"));
        assert_eq!(record["metadata"]["memory_scope"], json!("decisions"));
        assert_eq!(record["user_email"], json!("a@b.test"));
        assert_eq!(record["created_at"], record["updated_at"]);
        let timeline = store.load_state();
        let events = timeline["timeline"].as_array().unwrap();
        assert_eq!(events[0]["event_type"], json!("memory_upserted"));
        assert_eq!(events[0]["workspace_id"], json!("personal"));
    }

    #[test]
    fn the_two_validation_refusals_happen_before_any_write() {
        let (_dir, store) = store();
        assert_eq!(
            plan(&store, "preference", "x", None, &[], None, &json!({}), None).unwrap_err(),
            StoreError::Value("unknown memory kind: preference".into())
        );
        assert_eq!(
            plan(
                &store,
                "decisions",
                "   ",
                None,
                &[],
                None,
                &json!({}),
                None
            )
            .unwrap_err(),
            StoreError::Value("content is required".into())
        );
        assert!(store.load_state()["memories"]
            .as_array()
            .unwrap()
            .is_empty());
    }

    #[test]
    fn a_second_upsert_of_the_same_id_updates_rather_than_appends() {
        let (_dir, store) = store();
        let first = upsert(&store, "decisions", "first", None);
        let id = first["id"].as_str().unwrap().to_string();
        let planned = plan(
            &store,
            "workspace",
            "second",
            None,
            &[json!("t")],
            Some(&id),
            &json!({}),
            Some("org-x"),
        )
        .unwrap();
        assert!(!planned.is_new);
        // An existing record keeps its own workspace, not the requested one.
        assert_eq!(planned.workspace_id(), Some("personal"));
        let updated = commit(&store, planned, None).unwrap();
        assert_eq!(updated["content"], json!("second"));
        assert_eq!(updated["kind"], json!("workspace"));
        assert_eq!(updated["created_at"], first["created_at"]);
        assert_eq!(store.load_state()["memories"].as_array().unwrap().len(), 1);
    }

    #[test]
    fn the_graph_outcome_is_carried_either_way() {
        let (_dir, store) = store();
        let planned = plan(&store, "decisions", "x", None, &[], None, &json!({}), None).unwrap();
        assert_eq!(planned.kind(), "decisions");
        assert_eq!(planned.content(), "x");
        assert_eq!(planned.tags(), json!([]));
        assert!(!planned.memory_id().is_empty());
        let stored = commit(&store, planned, Some(Ok(json!({"node_id": "node-9"})))).unwrap();
        assert_eq!(stored["graph_node_id"], json!("node-9"));

        let planned = plan(&store, "decisions", "y", None, &[], None, &json!({}), None).unwrap();
        let stored = commit(&store, planned, Some(Err("seam down".into()))).unwrap();
        assert_eq!(stored["graph_error"], json!("seam down"));
        assert!(stored.get("graph_node_id").is_none());
    }

    #[test]
    fn listing_is_newest_first_and_filtered_by_owner_and_kind() {
        let (_dir, store) = store();
        upsert(&store, "decisions", "mine", Some("a@b.test"));
        upsert(&store, "workspace", "theirs", Some("c@d.test"));
        upsert(&store, "decisions", "ownerless", None);

        let all = list_memories(&store, None, None, None);
        assert_eq!(all["memories"].as_array().unwrap().len(), 3);
        assert_eq!(all["memories"][0]["content"], json!("ownerless"));

        let mine = list_memories(&store, Some("a@b.test"), None, None);
        let contents: Vec<&str> = mine["memories"]
            .as_array()
            .unwrap()
            .iter()
            .map(|item| item["content"].as_str().unwrap())
            .collect();
        assert_eq!(contents, vec!["ownerless", "mine"]);

        let by_kind = list_memories(&store, None, Some("workspace"), None);
        assert_eq!(by_kind["memories"].as_array().unwrap().len(), 1);
        let other_workspace = list_memories(&store, None, None, Some("org-x"));
        assert!(other_workspace["memories"].as_array().unwrap().is_empty());
    }

    #[test]
    fn search_matches_content_tags_or_kind_and_clamps_the_limit() {
        let (_dir, store) = store();
        let planned = plan(
            &store,
            "decisions",
            "하이브리드 검색",
            None,
            &[json!("retrieval")],
            None,
            &json!({}),
            None,
        )
        .unwrap();
        commit(&store, planned, None).unwrap();
        upsert(&store, "workspace", "unrelated", None);

        assert_eq!(
            search_memories(&store, "하이브리드", None, 20, None)["memories"]
                .as_array()
                .unwrap()
                .len(),
            1
        );
        assert_eq!(
            search_memories(&store, "RETRIEVAL", None, 20, None)["memories"]
                .as_array()
                .unwrap()
                .len(),
            1
        );
        assert_eq!(
            search_memories(&store, "workspace", None, 20, None)["memories"]
                .as_array()
                .unwrap()
                .len(),
            1
        );
        let none = search_memories(&store, "없는말", None, 20, None);
        assert!(none["memories"].as_array().unwrap().is_empty());
        assert_eq!(none["query"], json!("없는말"));
        // limit 0 clamps up to 1, a huge limit clamps down to 100.
        assert_eq!(
            search_memories(&store, "", None, 0, None)["memories"]
                .as_array()
                .unwrap()
                .len(),
            1
        );
        assert_eq!(
            search_memories(&store, "", None, 9_999, None)["memories"]
                .as_array()
                .unwrap()
                .len(),
            2
        );
    }

    #[test]
    fn get_and_delete_answer_not_found_for_an_unknown_id() {
        let (_dir, store) = store();
        let record = upsert(&store, "decisions", "x", None);
        let id = record["id"].as_str().unwrap().to_string();
        assert_eq!(get_memory(&store, &id).unwrap()["content"], json!("x"));
        assert_eq!(
            get_memory(&store, "memory-missing").unwrap_err(),
            StoreError::NotFound("memory-missing".into())
        );
        assert_eq!(
            delete_memory(&store, &id).unwrap(),
            json!({"status": "ok", "memory_id": id})
        );
        assert!(store.load_state()["memories"]
            .as_array()
            .unwrap()
            .is_empty());
        assert_eq!(
            delete_memory(&store, &id).unwrap_err(),
            StoreError::NotFound(id)
        );
    }

    #[test]
    fn deleting_is_authorised_by_ownership_or_by_workspace_write() {
        let (_dir, store) = store();
        super::super::orgs::create_organization_workspace(&store, "Team", Some("user:1"), None)
            .unwrap();
        let mine = json!({"id": "m1", "user_email": "a@b.test"});
        assert!(authorize_delete(&store, &mine, Some("a@b.test"), None).is_ok());
        assert!(authorize_delete(&store, &mine, None, Some("a@b.test")).is_ok());
        let refusal = authorize_delete(&store, &mine, Some("c@d.test"), None).unwrap_err();
        assert_eq!(
            refusal,
            StoreError::Permission("'c@d.test' is not the owner of memory 'm1'".into())
        );

        let scoped = json!({"id": "m2", "user_email": "a@b.test", "workspace_id": "org-Team"});
        assert!(authorize_delete(&store, &scoped, Some("c@d.test"), Some("user:1")).is_ok());
        let denied =
            authorize_delete(&store, &scoped, Some("c@d.test"), Some("user:9")).unwrap_err();
        assert_eq!(
            denied,
            StoreError::Permission("'c@d.test' lacks 'write' on workspace 'org-Team'".into())
        );

        // Ownerless and unscoped stays deletable by any local user.
        assert!(authorize_delete(&store, &json!({"id": "m3"}), Some("x@y.test"), None).is_ok());
    }
}
