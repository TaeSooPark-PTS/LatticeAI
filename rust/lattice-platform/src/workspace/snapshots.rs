//! Snapshots, the Time Machine view, the export zip, and the diff.
//!
//! Port of `core/workspace_snapshots.py`. A snapshot is an immutable JSON
//! document under `<data_dir>/workspace_snapshots/`, plus a small metadata row
//! in the state document that the listing reads. Nothing here ever mutates the
//! live graph.
//!
//! ## One finding, reproduced rather than fixed
//!
//! `restore_snapshot` calls `graph.import_graph(...)`, and the graph store has
//! no such method — it has `import_graph_data`. The `except Exception` around
//! the call swallows the `AttributeError`, so **restore has never imported
//! anything**: it records a timeline event and answers `{"restored": true}`.
//! The captured fixture confirms it (the happy response has no `imported` key).
//!
//! This port reproduces that contract exactly. Wiring it to the real seam op
//! would turn a no-op into a live merge of an old snapshot into the current
//! graph — a data-affecting change with no fixture behind it and no request for
//! it. It is reported in the wiring note instead.

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
use std::io::Write;
use std::path::PathBuf;

use serde_json::{json, Map, Value};

use super::constants::WORKSPACE_OS_VERSION;
use super::deps::GraphReads;
use super::pyutil::{json_hash, json_hash_prefix, listify, now_iso, safe_slug};
use super::store::{StoreError, WorkspaceOsStore};

/// How many nodes/edges a snapshot captures from the live graph.
pub const SNAPSHOT_GRAPH_LIMIT: usize = 2_000;

/// `create_snapshot`.
pub fn create_snapshot(
    store: &WorkspaceOsStore,
    name: &str,
    graph: Option<&dyn GraphReads>,
    history: &[Value],
    settings: &Value,
    models: &Value,
    workspace_id: Option<&str>,
) -> Result<Value, StoreError> {
    let scope = WorkspaceOsStore::resolve_scope(workspace_id, &store.load_state());
    let mut graph_payload = json!({"nodes": [], "edges": []});
    let mut graph_stats = json!({});
    let mut local_sources = json!({"sources": []});
    if let Some(graph) = graph {
        if let Some(window) = graph.window(SNAPSHOT_GRAPH_LIMIT) {
            graph_payload = window;
        }
        if let Some(stats) = graph.stats() {
            graph_stats = stats;
        }
        if let Some(sources) = graph.local_sources() {
            local_sources = sources;
        }
    }
    let resolved_name = if name.is_empty() {
        "Workspace snapshot"
    } else {
        name
    };
    let indexed_folders = local_sources
        .get("sources")
        .cloned()
        .unwrap_or_else(|| json!([]));

    let mut body = json!({
        "version": WORKSPACE_OS_VERSION,
        "name": resolved_name,
        "created_at": now_iso(),
        "workspace": scope,
        "workspace_id": scope,
        "graph": graph_payload,
        "graph_stats": graph_stats,
        "chat": history,
        "settings": settings,
        "indexed_folders": indexed_folders,
        "models": models,
    });
    // The id is a hash of the body *before* the id is in it.
    let snapshot_id = format!(
        "snapshot-{}-{}",
        compact_stamp(),
        json_hash_prefix(&body, 10)
    );
    body["id"] = json!(snapshot_id);

    let path = store.snapshots_dir().join(format!("{snapshot_id}.json"));
    let rendered = serde_json::to_string_pretty(&body)
        .map_err(|error| StoreError::Value(format!("snapshot render failed: {error}")))?;
    lattice_auth::atomic::write_text(&path, &rendered);

    let meta = json!({
        "id": snapshot_id,
        "name": resolved_name,
        "created_at": body["created_at"],
        "workspace_id": scope,
        "path": path.to_string_lossy(),
        "node_count": listify(body["graph"].get("nodes")).len(),
        "edge_count": listify(body["graph"].get("edges")).len(),
        "chat_count": history.len(),
        "model_count": listify(models.get("loaded_models")).len(),
        "indexed_folder_count": listify(local_sources.get("sources")).len(),
    });
    let stored = meta.clone();
    store.mutate(|state| {
        let mut snapshots = listify(state.get("snapshots"));
        snapshots.push(stored);
        state["snapshots"] = Value::Array(snapshots);
        Ok(())
    })?;
    store.record_timeline_event(
        "snapshot",
        "snapshot_saved",
        json!({"snapshot_id": snapshot_id, "name": name}),
        None,
    );
    Ok(json!({"snapshot": meta}))
}

/// `datetime.now().strftime("%Y%m%d%H%M%S")`, derived from the one clock.
fn compact_stamp() -> String {
    now_iso().chars().filter(char::is_ascii_digit).collect()
}

/// `list_snapshots` — newest first.
pub fn list_snapshots(store: &WorkspaceOsStore, workspace_id: Option<&str>) -> Value {
    let state = store.load_state();
    let mut snapshots = WorkspaceOsStore::scoped(listify(state.get("snapshots")), workspace_id);
    snapshots.reverse();
    json!({"snapshots": snapshots})
}

/// `get_snapshot` — the document on disk, by slug and then by recorded path.
pub fn get_snapshot(store: &WorkspaceOsStore, snapshot_id: &str) -> Result<Value, StoreError> {
    let mut path: PathBuf = store
        .snapshots_dir()
        .join(format!("{}.json", safe_slug(snapshot_id)));
    if !path.exists() {
        // A snapshot whose id does not survive slugging is still findable: the
        // metadata row recorded the absolute path it was written to.
        if let Some(recorded) = listify(store.load_state().get("snapshots"))
            .into_iter()
            .find(|item| item.get("id").and_then(Value::as_str) == Some(snapshot_id))
            .and_then(|item| item.get("path").and_then(Value::as_str).map(PathBuf::from))
        {
            path = recorded;
        }
    }
    if !path.exists() {
        return Err(StoreError::NotFound(snapshot_id.to_string()));
    }
    let text = std::fs::read_to_string(&path)
        .map_err(|_| StoreError::NotFound(snapshot_id.to_string()))?;
    serde_json::from_str(&text).map_err(|error| StoreError::Value(error.to_string()))
}

/// `snapshot_view(snapshot_id, area)` — the three named areas, else the whole.
pub fn snapshot_view(
    store: &WorkspaceOsStore,
    snapshot_id: &str,
    area: &str,
) -> Result<Value, StoreError> {
    let snapshot = get_snapshot(store, snapshot_id)?;
    Ok(match area {
        "graph" => json!({
            "snapshot_id": snapshot_id,
            "graph": object_or_empty(snapshot.get("graph")),
            "graph_stats": object_or_empty(snapshot.get("graph_stats")),
        }),
        "chat" => json!({
            "snapshot_id": snapshot_id,
            "chat": snapshot.get("chat").cloned().unwrap_or_else(|| json!([])),
        }),
        "decision" => {
            let decisions: Vec<Value> =
                listify(snapshot.get("graph").and_then(|graph| graph.get("nodes")))
                    .into_iter()
                    .filter(|node| node.get("type").and_then(Value::as_str) == Some("Decision"))
                    .collect();
            json!({"snapshot_id": snapshot_id, "decisions": decisions})
        }
        _ => json!({"snapshot_id": snapshot_id, "snapshot": snapshot}),
    })
}

fn object_or_empty(value: Option<&Value>) -> Value {
    match value {
        Some(Value::Object(map)) => Value::Object(map.clone()),
        _ => Value::Object(Map::new()),
    }
}

/// The six members `export_snapshot` writes into the zip, in Python's order.
const EXPORT_MEMBERS: [(&str, &str); 6] = [
    ("snapshot.json", ""),
    ("graph.json", "graph"),
    ("chat.json", "chat"),
    ("settings.json", "settings"),
    ("indexed_folders.json", "indexed_folders"),
    ("models.json", "models"),
];

/// `export_snapshot` — a deflate zip of the snapshot and its five slices.
pub fn export_snapshot(store: &WorkspaceOsStore, snapshot_id: &str) -> Result<Value, StoreError> {
    let snapshot = get_snapshot(store, snapshot_id)?;
    let export_path = store
        .exports_dir()
        .join(format!("{}.zip", safe_slug(snapshot_id)));

    let file = std::fs::File::create(&export_path)
        .map_err(|error| StoreError::Value(format!("export failed: {error}")))?;
    let mut archive = zip::ZipWriter::new(file);
    let options: zip::write::FileOptions<'_, ()> =
        zip::write::FileOptions::default().compression_method(zip::CompressionMethod::Deflated);
    for (member, key) in EXPORT_MEMBERS {
        let payload = if key.is_empty() {
            snapshot.clone()
        } else {
            snapshot
                .get(key)
                .filter(|value| !value.is_null())
                .cloned()
                .unwrap_or_else(|| default_for(key))
        };
        let rendered = serde_json::to_string_pretty(&payload)
            .map_err(|error| StoreError::Value(format!("export render failed: {error}")))?;
        archive
            .start_file(member, options)
            .and_then(|()| archive.write_all(rendered.as_bytes()).map_err(Into::into))
            .map_err(|error| StoreError::Value(format!("export write failed: {error}")))?;
    }
    archive
        .finish()
        .map_err(|error| StoreError::Value(format!("export close failed: {error}")))?;

    let bytes = std::fs::metadata(&export_path)
        .map(|meta| meta.len())
        .unwrap_or(0);
    store.record_timeline_event(
        "snapshot",
        "snapshot_exported",
        json!({"snapshot_id": snapshot_id, "path": export_path.to_string_lossy()}),
        None,
    );
    Ok(json!({
        "snapshot_id": snapshot_id,
        "export_path": export_path.to_string_lossy(),
        "bytes": bytes,
    }))
}

fn default_for(key: &str) -> Value {
    match key {
        "chat" | "indexed_folders" => json!([]),
        _ => json!({}),
    }
}

/// `restore_snapshot` — records the restore; see the module note.
pub fn restore_snapshot(
    store: &WorkspaceOsStore,
    snapshot_id: &str,
    workspace_id: Option<&str>,
) -> Result<Value, StoreError> {
    // The load is not decorative: an unknown id must still answer 404.
    get_snapshot(store, snapshot_id)?;
    store.record_timeline_event(
        "snapshot",
        "snapshot_restored",
        json!({"snapshot_id": snapshot_id}),
        workspace_id,
    );
    Ok(json!({"restored": true, "snapshot_id": snapshot_id}))
}

/// `compare_snapshots` — an additive/removed/changed diff of two snapshots.
pub fn compare_snapshots(
    store: &WorkspaceOsStore,
    before_id: &str,
    after_id: &str,
) -> Result<Value, StoreError> {
    let before = get_snapshot(store, before_id)?;
    let after = get_snapshot(store, after_id)?;
    let before_nodes = nodes_by_id(&before);
    let after_nodes = nodes_by_id(&after);
    let before_edges = edges_by_key(&before);
    let after_edges = edges_by_key(&after);

    let added_nodes = only_in(&after_nodes, &before_nodes);
    let removed_nodes = only_in(&before_nodes, &after_nodes);
    let changed_nodes: Vec<Value> = sorted_keys(&before_nodes)
        .into_iter()
        .filter(|key| after_nodes.contains_key(key))
        .filter(|key| json_hash(&before_nodes[key]) != json_hash(&after_nodes[key]))
        .map(|key| json!({"before": before_nodes[&key], "after": after_nodes[&key]}))
        .collect();
    let added_edges = only_in(&after_edges, &before_edges);
    let removed_edges = only_in(&before_edges, &after_edges);

    let before_decisions = decisions(&before_nodes);
    let after_decisions = decisions(&after_nodes);
    let mut decision_keys: Vec<String> = before_decisions
        .keys()
        .chain(after_decisions.keys())
        .cloned()
        .collect();
    decision_keys.sort();
    decision_keys.dedup();
    let decisions_changed: Vec<Value> = decision_keys
        .into_iter()
        .filter(|key| {
            let before = before_decisions.get(key).cloned().unwrap_or(Value::Null);
            let after = after_decisions.get(key).cloned().unwrap_or(Value::Null);
            json_hash(&before) != json_hash(&after)
        })
        .map(|key| {
            json!({
                "before": before_decisions.get(&key).cloned().unwrap_or(Value::Null),
                "after": after_decisions.get(&key).cloned().unwrap_or(Value::Null),
            })
        })
        .collect();

    Ok(json!({
        "before": before_id,
        "after": after_id,
        "nodes_added": added_nodes,
        "nodes_removed": removed_nodes,
        "nodes_changed": changed_nodes,
        "edges_added": added_edges,
        "edges_removed": removed_edges,
        "decisions_changed": decisions_changed,
        "summary": {
            "nodes_added": added_nodes.len(),
            "nodes_removed": removed_nodes.len(),
            "nodes_changed": changed_nodes.len(),
            "edges_added": added_edges.len(),
            "edges_removed": removed_edges.len(),
            "decisions_changed": decisions_changed.len(),
        },
    }))
}

type Indexed = std::collections::BTreeMap<String, Value>;

fn nodes_by_id(snapshot: &Value) -> Indexed {
    listify(snapshot.get("graph").and_then(|graph| graph.get("nodes")))
        .into_iter()
        .filter_map(|node| {
            let id = node
                .get("id")
                .and_then(Value::as_str)
                .filter(|value| !value.is_empty())?
                .to_string();
            Some((id, node))
        })
        .collect()
}

fn edges_by_key(snapshot: &Value) -> Indexed {
    listify(snapshot.get("graph").and_then(|graph| graph.get("edges")))
        .into_iter()
        .map(|edge| {
            let key = ["from", "to", "type"]
                .iter()
                .map(|field| {
                    edge.get(*field)
                        .and_then(Value::as_str)
                        .unwrap_or_default()
                        .to_string()
                })
                .collect::<Vec<_>>()
                .join("|");
            (key, edge)
        })
        .collect()
}

fn sorted_keys(map: &Indexed) -> Vec<String> {
    map.keys().cloned().collect()
}

fn only_in(source: &Indexed, other: &Indexed) -> Vec<Value> {
    sorted_keys(source)
        .into_iter()
        .filter(|key| !other.contains_key(key))
        .map(|key| source[&key].clone())
        .collect()
}

fn decisions(nodes: &Indexed) -> Indexed {
    nodes
        .iter()
        .filter(|(_, node)| node.get("type").and_then(Value::as_str) == Some("Decision"))
        .map(|(key, node)| (key.clone(), node.clone()))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    struct Graph;
    impl GraphReads for Graph {
        fn stats(&self) -> Option<Value> {
            Some(json!({"nodes": {"Concept": 2}, "edges": {}}))
        }
        fn window(&self, _limit: usize) -> Option<Value> {
            Some(json!({
                "nodes": [{"id": "n1", "type": "Concept"}, {"id": "n2", "type": "Decision"}],
                "edges": [{"from": "n1", "to": "n2", "type": "MENTIONS"}],
            }))
        }
        fn local_sources(&self) -> Option<Value> {
            Some(json!({"sources": [{"id": "s1"}]}))
        }
        fn neighbors(&self, _node_id: &str) -> Option<Value> {
            None
        }
    }

    fn store() -> (tempfile::TempDir, WorkspaceOsStore) {
        let dir = tempfile::tempdir().expect("tempdir");
        let store = WorkspaceOsStore::open(dir.path());
        (dir, store)
    }

    fn create(store: &WorkspaceOsStore, name: &str, graph: Option<&dyn GraphReads>) -> String {
        let answer = create_snapshot(
            store,
            name,
            graph,
            &[json!({"role": "user", "content": "hi"})],
            &json!({"mode": "local"}),
            &json!({"loaded_models": []}),
            None,
        )
        .unwrap();
        answer["snapshot"]["id"].as_str().unwrap().to_string()
    }

    #[test]
    fn a_snapshot_is_written_to_disk_and_indexed_in_the_state() {
        let (dir, store) = store();
        let answer = create_snapshot(
            &store,
            "Fixture snapshot A",
            Some(&Graph),
            &[json!({"role": "user"}), json!({"role": "assistant"})],
            &json!({"mode": "local"}),
            &json!({"loaded_models": ["m1"]}),
            None,
        )
        .unwrap();
        let meta = &answer["snapshot"];
        let id = meta["id"].as_str().unwrap();
        assert!(id.starts_with("snapshot-"));
        assert_eq!(meta["name"], json!("Fixture snapshot A"));
        assert_eq!(meta["workspace_id"], json!("personal"));
        assert_eq!(meta["node_count"], json!(2));
        assert_eq!(meta["edge_count"], json!(1));
        assert_eq!(meta["chat_count"], json!(2));
        assert_eq!(meta["model_count"], json!(1));
        assert_eq!(meta["indexed_folder_count"], json!(1));
        assert!(dir
            .path()
            .join("workspace_snapshots")
            .join(format!("{id}.json"))
            .is_file());

        let document = get_snapshot(&store, id).unwrap();
        assert_eq!(document["version"], json!(WORKSPACE_OS_VERSION));
        assert_eq!(document["workspace"], json!("personal"));
        assert_eq!(document["indexed_folders"][0]["id"], json!("s1"));
        assert_eq!(document["id"], json!(id));
    }

    #[test]
    fn a_snapshot_without_a_graph_captures_the_empty_shape() {
        let (_dir, store) = store();
        let id = create(&store, "", None);
        let document = get_snapshot(&store, &id).unwrap();
        assert_eq!(document["name"], json!("Workspace snapshot"));
        assert_eq!(document["graph"], json!({"nodes": [], "edges": []}));
        assert_eq!(document["graph_stats"], json!({}));
        assert_eq!(document["indexed_folders"], json!([]));
    }

    #[test]
    fn listing_is_newest_first_and_scoped() {
        let (_dir, store) = store();
        create(&store, "A", None);
        std::thread::sleep(std::time::Duration::from_millis(5));
        create(&store, "B", None);
        let listing = list_snapshots(&store, None);
        let snapshots = listing["snapshots"].as_array().unwrap();
        assert_eq!(snapshots.len(), 2);
        assert_eq!(snapshots[0]["name"], json!("B"));
        assert!(list_snapshots(&store, Some("org-x"))["snapshots"]
            .as_array()
            .unwrap()
            .is_empty());
    }

    #[test]
    fn an_unknown_snapshot_is_not_found_everywhere_it_can_be_asked_for() {
        let (_dir, store) = store();
        for outcome in [
            get_snapshot(&store, "snapshot-missing").map(|_| ()),
            snapshot_view(&store, "snapshot-missing", "graph").map(|_| ()),
            export_snapshot(&store, "snapshot-missing").map(|_| ()),
            restore_snapshot(&store, "snapshot-missing", None).map(|_| ()),
            compare_snapshots(&store, "snapshot-missing", "x").map(|_| ()),
        ] {
            assert_eq!(
                outcome.unwrap_err(),
                StoreError::NotFound("snapshot-missing".into())
            );
        }
    }

    #[test]
    fn the_four_snapshot_views_answer_their_slice() {
        let (_dir, store) = store();
        let id = create(&store, "A", Some(&Graph));
        let graph = snapshot_view(&store, &id, "graph").unwrap();
        assert_eq!(graph["graph"]["nodes"].as_array().unwrap().len(), 2);
        assert_eq!(graph["graph_stats"]["nodes"]["Concept"], json!(2));

        let chat = snapshot_view(&store, &id, "chat").unwrap();
        assert_eq!(chat["chat"].as_array().unwrap().len(), 1);

        let decision = snapshot_view(&store, &id, "decision").unwrap();
        assert_eq!(decision["decisions"].as_array().unwrap().len(), 1);
        assert_eq!(decision["decisions"][0]["id"], json!("n2"));

        let whole = snapshot_view(&store, &id, "settings").unwrap();
        assert_eq!(whole["snapshot"]["settings"], json!({"mode": "local"}));
    }

    #[test]
    fn an_export_is_a_readable_zip_of_six_members() {
        let (dir, store) = store();
        let id = create(&store, "A", Some(&Graph));
        let answer = export_snapshot(&store, &id).unwrap();
        assert_eq!(answer["snapshot_id"], json!(id));
        assert!(answer["bytes"].as_u64().unwrap() > 0);
        let path = dir
            .path()
            .join("workspace_exports")
            .join(format!("{id}.zip"));
        assert_eq!(answer["export_path"], json!(path.to_string_lossy()));

        let file = std::fs::File::open(&path).unwrap();
        let mut archive = zip::ZipArchive::new(file).unwrap();
        assert_eq!(archive.len(), 6);
        let names: Vec<String> = archive.file_names().map(str::to_string).collect();
        for (member, _) in EXPORT_MEMBERS {
            assert!(names.contains(&member.to_string()), "{member}");
        }
        let mut entry = archive.by_name("graph.json").unwrap();
        let mut text = String::new();
        std::io::Read::read_to_string(&mut entry, &mut text).unwrap();
        let parsed: Value = serde_json::from_str(&text).unwrap();
        assert_eq!(parsed["nodes"].as_array().unwrap().len(), 2);
    }

    #[test]
    fn restore_records_the_event_and_claims_nothing_it_did_not_do() {
        let (_dir, store) = store();
        let id = create(&store, "A", Some(&Graph));
        let answer = restore_snapshot(&store, &id, Some("personal")).unwrap();
        assert_eq!(answer, json!({"restored": true, "snapshot_id": id}));
        assert!(answer.get("imported").is_none());
        let events = store.load_state();
        let recorded = events["timeline"]
            .as_array()
            .unwrap()
            .iter()
            .any(|event| event["event_type"] == json!("snapshot_restored"));
        assert!(recorded);
    }

    #[test]
    fn a_diff_reports_added_removed_changed_and_decisions() {
        let (_dir, store) = store();
        let before = create_snapshot(&store, "before", None, &[], &json!({}), &json!({}), None)
            .unwrap()["snapshot"]["id"]
            .as_str()
            .unwrap()
            .to_string();
        // Rewrite the two documents directly: the diff is over their contents,
        // and building them by hand is what makes the expectations legible.
        let write = |id: &str, graph: Value| {
            let path = store.snapshots_dir().join(format!("{id}.json"));
            let mut document: Value =
                serde_json::from_str(&std::fs::read_to_string(&path).unwrap()).unwrap();
            document["graph"] = graph;
            std::fs::write(&path, serde_json::to_string(&document).unwrap()).unwrap();
        };
        write(
            &before,
            json!({
                "nodes": [{"id": "keep"}, {"id": "gone"}, {"id": "moved", "v": 1},
                          {"id": "d1", "type": "Decision", "text": "old"}],
                "edges": [{"from": "keep", "to": "gone", "type": "X"}],
            }),
        );
        std::thread::sleep(std::time::Duration::from_millis(5));
        let after = create_snapshot(&store, "after", None, &[], &json!({}), &json!({}), None)
            .unwrap()["snapshot"]["id"]
            .as_str()
            .unwrap()
            .to_string();
        write(
            &after,
            json!({
                "nodes": [{"id": "keep"}, {"id": "new"}, {"id": "moved", "v": 2},
                          {"id": "d1", "type": "Decision", "text": "new"}],
                "edges": [{"from": "keep", "to": "new", "type": "Y"}],
            }),
        );

        let diff = compare_snapshots(&store, &before, &after).unwrap();
        assert_eq!(diff["before"], json!(before));
        assert_eq!(diff["summary"]["nodes_added"], json!(1));
        assert_eq!(diff["nodes_added"][0]["id"], json!("new"));
        assert_eq!(diff["summary"]["nodes_removed"], json!(1));
        assert_eq!(diff["nodes_removed"][0]["id"], json!("gone"));
        assert_eq!(diff["summary"]["nodes_changed"], json!(2));
        let changed = diff["nodes_changed"].as_array().unwrap();
        assert_eq!(changed[0]["before"]["id"], json!("d1"));
        assert_eq!(changed[1]["before"]["v"], json!(1));
        assert_eq!(changed[1]["after"]["v"], json!(2));
        assert_eq!(diff["summary"]["edges_added"], json!(1));
        assert_eq!(diff["summary"]["edges_removed"], json!(1));
        assert_eq!(diff["summary"]["decisions_changed"], json!(1));
        assert_eq!(diff["decisions_changed"][0]["after"]["text"], json!("new"));
    }

    #[test]
    fn a_snapshot_recorded_at_a_custom_path_is_still_found() {
        let (dir, store) = store();
        let elsewhere = dir.path().join("elsewhere.json");
        std::fs::write(
            &elsewhere,
            serde_json::to_string(&json!({"id": "odd/id"})).unwrap(),
        )
        .unwrap();
        store
            .mutate(|state| {
                state["snapshots"] = json!([{"id": "odd/id", "path": elsewhere.to_string_lossy()}]);
                Ok(())
            })
            .unwrap();
        assert_eq!(
            get_snapshot(&store, "odd/id").unwrap()["id"],
            json!("odd/id")
        );
    }

    #[test]
    fn the_compact_stamp_is_fourteen_digits() {
        let stamp = compact_stamp();
        assert_eq!(stamp.len(), 14, "{stamp}");
        assert!(stamp.chars().all(|ch| ch.is_ascii_digit()));
    }
}
