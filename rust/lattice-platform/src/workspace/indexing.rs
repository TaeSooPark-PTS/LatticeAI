//! The local-indexing dashboard and the per-source watch controls.
//!
//! Port of `core/workspace_indexing.py`. Reads come from the knowledge-graph
//! reader; the three controls **write** graph state (`set_local_source_watch`,
//! `remove_local_source`) and therefore go over the worker seam, so they live
//! here as request builders and the handler awaits the seam.
//!
//! The graph is machine-global shared state, not per-workspace: two workspaces
//! on one install see the same indexed folders. That is stated in
//! `WorkspaceService.SHARED_GLOBAL_AREAS` and is why none of these three
//! controls takes a workspace scope.

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
use serde_json::{json, Value};

use super::deps::GraphReads;
use super::pyutil::listify;

/// The watcher block used when no watcher is attached.
pub fn no_watcher() -> Value {
    json!({"available": false, "active": {}})
}

/// `build_indexing_dashboard(graph, watcher_status)`.
pub fn build_dashboard(graph: Option<&dyn GraphReads>, watcher_status: Option<Value>) -> Value {
    let watcher = watcher_status.unwrap_or_else(no_watcher);
    let Some(graph) = graph else {
        return json!({
            "sources": [],
            "watcher": watcher,
            "totals": {"success": 0, "failed": 0, "nodes": 0, "edges": 0},
        });
    };
    let stats = graph.stats().unwrap_or_else(|| json!({}));
    let sources = listify(
        graph
            .local_sources()
            .unwrap_or_else(|| json!({}))
            .get("sources"),
    );
    let active = watcher
        .get("active")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();

    let mut dashboard_sources = Vec::with_capacity(sources.len());
    let mut total_success = 0i64;
    let mut total_failed = 0i64;
    for source in &sources {
        let file_status = source
            .get("file_status")
            .filter(|value| value.is_object())
            .cloned()
            .unwrap_or_else(|| json!({}));
        let count = |key: &str| file_status.get(key).and_then(Value::as_i64).unwrap_or(0);
        let success = count("indexed");
        let failed = count("failed") + count("inaccessible") + count("skipped_empty_text");
        total_success += success;
        total_failed += failed;
        let id = source.get("id").and_then(Value::as_str).unwrap_or_default();
        let watch = active.get(id).cloned().unwrap_or_else(|| json!({}));
        dashboard_sources.push(json!({
            "id": source.get("id").cloned().unwrap_or(Value::Null),
            "label": source.get("label").cloned().unwrap_or(Value::Null),
            "root_path": source.get("root_path").cloned().unwrap_or(Value::Null),
            "status": source.get("status").cloned().unwrap_or(Value::Null),
            "watch_enabled": truthy(source.get("watch_enabled")),
            "watch_active": active.contains_key(id),
            "watch_status": watch,
            "success_count": success,
            "failure_count": failed,
            "last_run_at": source
                .get("last_scanned_at")
                .filter(|value| !value.is_null())
                .or_else(|| source.get("updated_at"))
                .cloned()
                .unwrap_or(Value::Null),
            "file_status": file_status,
            "include_ocr": truthy(source.get("include_ocr")),
        }));
    }

    json!({
        "sources": dashboard_sources,
        "watcher": watcher,
        "totals": {
            "success": total_success,
            "failed": total_failed,
            "nodes": sum_counts(stats.get("nodes")),
            "edges": sum_counts(stats.get("edges")),
            "local_sources": stats
                .get("local_sources")
                .and_then(Value::as_i64)
                .unwrap_or(sources.len() as i64),
        },
        "graph_stats": stats,
    })
}

fn sum_counts(value: Option<&Value>) -> i64 {
    value
        .and_then(Value::as_object)
        .map(|map| map.values().filter_map(Value::as_i64).sum())
        .unwrap_or(0)
}

fn truthy(value: Option<&Value>) -> bool {
    match value {
        Some(Value::Bool(flag)) => *flag,
        Some(Value::String(text)) => !text.is_empty(),
        Some(Value::Number(number)) => number.as_f64().is_some_and(|value| value != 0.0),
        Some(Value::Array(items)) => !items.is_empty(),
        Some(Value::Object(map)) => !map.is_empty(),
        _ => false,
    }
}

/// The answer `pause` builds once the seam and the watcher have replied.
pub fn pause_answer(source: Value, watch: Value) -> Value {
    json!({"status": "ok", "source": source, "watch": watch})
}

/// The `watch` block a paused source gets with no watcher attached.
pub fn stopped_without_watcher(source_id: &str) -> Value {
    json!({"stopped": false, "source_id": source_id})
}

/// The `watch` block a resumed source gets with no watcher attached.
pub fn not_watching(source_id: &str) -> Value {
    json!({"watching": false, "source_id": source_id})
}

/// The source record the resume path hands the watcher, if the graph knows it.
pub fn source_by_id(graph: Option<&dyn GraphReads>, source_id: &str) -> Option<Value> {
    let sources = listify(
        graph?
            .local_sources()
            .unwrap_or_else(|| json!({}))
            .get("sources"),
    );
    sources
        .into_iter()
        .find(|item| item.get("id").and_then(Value::as_str) == Some(source_id))
}

/// `remove_source`'s answer: `{"status": "ok", **result}`.
pub fn remove_answer(result: Value) -> Value {
    let mut merged = serde_json::Map::new();
    merged.insert("status".into(), json!("ok"));
    if let Value::Object(map) = result {
        for (key, value) in map {
            merged.insert(key, value);
        }
    }
    Value::Object(merged)
}

#[cfg(test)]
mod tests {
    use super::*;

    struct Graph {
        stats: Value,
        sources: Value,
    }

    impl GraphReads for Graph {
        fn stats(&self) -> Option<Value> {
            Some(self.stats.clone())
        }
        fn window(&self, _limit: usize) -> Option<Value> {
            None
        }
        fn local_sources(&self) -> Option<Value> {
            Some(self.sources.clone())
        }
        fn neighbors(&self, _node_id: &str) -> Option<Value> {
            None
        }
    }

    #[test]
    fn no_graph_answers_an_empty_dashboard() {
        let dashboard = build_dashboard(None, None);
        assert_eq!(dashboard["sources"], json!([]));
        assert_eq!(dashboard["watcher"], no_watcher());
        assert_eq!(dashboard["totals"]["nodes"], json!(0));
        assert!(dashboard.get("graph_stats").is_none());
    }

    #[test]
    fn an_empty_graph_still_reports_its_stats() {
        let graph = Graph {
            stats: json!({"nodes": {"Concept": 17, "Person": 1}, "edges": {"MENTIONS": 5},
                          "local_sources": 0}),
            sources: json!({"sources": []}),
        };
        let dashboard = build_dashboard(
            Some(&graph),
            Some(json!({"available": true, "error": "", "debounce_seconds": 5.0, "active": {}})),
        );
        assert_eq!(dashboard["totals"]["nodes"], json!(18));
        assert_eq!(dashboard["totals"]["edges"], json!(5));
        assert_eq!(dashboard["totals"]["local_sources"], json!(0));
        assert_eq!(dashboard["watcher"]["available"], json!(true));
        assert_eq!(dashboard["graph_stats"]["nodes"]["Concept"], json!(17));
    }

    #[test]
    fn a_source_is_projected_with_its_counts_and_watch_state() {
        let graph = Graph {
            stats: json!({"nodes": {}, "edges": {}}),
            sources: json!({"sources": [{
                "id": "src-1", "label": "Notes", "root_path": "/n", "status": "ready",
                "watch_enabled": true, "include_ocr": false,
                "file_status": {"indexed": 4, "failed": 1, "inaccessible": 2,
                                "skipped_empty_text": 3},
                "updated_at": "2026-08-01T00:00:00",
            }]}),
        };
        let dashboard = build_dashboard(
            Some(&graph),
            Some(json!({"available": true, "active": {"src-1": {"watching": true}}})),
        );
        let source = &dashboard["sources"][0];
        assert_eq!(source["success_count"], json!(4));
        assert_eq!(source["failure_count"], json!(6));
        assert_eq!(source["watch_active"], json!(true));
        assert_eq!(source["watch_status"], json!({"watching": true}));
        assert_eq!(source["last_run_at"], json!("2026-08-01T00:00:00"));
        assert_eq!(source["include_ocr"], json!(false));
        assert_eq!(dashboard["totals"]["success"], json!(4));
        assert_eq!(dashboard["totals"]["failed"], json!(6));
        // No `local_sources` in stats ⇒ the count of sources.
        assert_eq!(dashboard["totals"]["local_sources"], json!(1));
    }

    #[test]
    fn last_run_prefers_the_scan_stamp_and_falls_back_to_updated_at() {
        let graph = Graph {
            stats: json!({}),
            sources: json!({"sources": [{"id": "s", "last_scanned_at": "2026-08-02T00:00:00",
                                         "updated_at": "2026-08-01T00:00:00"}]}),
        };
        let dashboard = build_dashboard(Some(&graph), None);
        assert_eq!(
            dashboard["sources"][0]["last_run_at"],
            json!("2026-08-02T00:00:00")
        );
        assert_eq!(dashboard["sources"][0]["watch_active"], json!(false));
        assert_eq!(dashboard["sources"][0]["file_status"], json!({}));
    }

    #[test]
    fn the_control_answers_are_the_shapes_python_builds() {
        assert_eq!(
            pause_answer(json!({"id": "s"}), stopped_without_watcher("s")),
            json!({"status": "ok", "source": {"id": "s"},
                   "watch": {"stopped": false, "source_id": "s"}})
        );
        assert_eq!(
            not_watching("s"),
            json!({"watching": false, "source_id": "s"})
        );
        assert_eq!(
            remove_answer(json!({"removed": 3, "source_id": "s"})),
            json!({"status": "ok", "removed": 3, "source_id": "s"})
        );
        assert_eq!(
            remove_answer(json!("not an object")),
            json!({"status": "ok"})
        );
    }

    #[test]
    fn a_source_can_be_looked_up_for_the_resume_path() {
        let graph = Graph {
            stats: json!({}),
            sources: json!({"sources": [{"id": "s1"}, {"id": "s2"}]}),
        };
        assert_eq!(source_by_id(Some(&graph), "s2"), Some(json!({"id": "s2"})));
        assert_eq!(source_by_id(Some(&graph), "nope"), None);
        assert_eq!(source_by_id(None, "s1"), None);
    }
}
