//! W3b: `POST /worker/graph/mutate` → [`lattice_core::graph_write::GraphWriter`].

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
use lattice_core::graph_write::types::{
    CurateNoiseRequest, CurateRequest, ImportRequest, IngestEventRequest, RebuildRequest,
};
use lattice_core::graph_write::GraphWriter;
use lattice_core::CoreError;
use serde_json::{json, Map, Value};

/// Ops the write engine covers (W1 §1 table). Everything else stays local
/// (Self-Model) or is a 400.
pub fn is_writer_op(op: &str) -> bool {
    matches!(
        op,
        "curate"
            | "curate_noise"
            | "apply_pending_promotions"
            | "reject_pending_promotions"
            | "rebuild_vector_index"
            | "ingest_event"
            | "set_node_sensitivity"
            | "import_graph_data"
            | "delete_document_tree"
            | "set_local_source_watch"
            | "remove_local_source"
    )
}

/// Run one seam op on the native writer. Blocks; call from `spawn_blocking`.
pub fn dispatch(graph: &GraphWriter, op: &str, args: &Value) -> Result<Value, CoreError> {
    match op {
        "curate" => {
            let request = CurateRequest {
                max_documents: int(args, "max_documents", 200),
                max_new_nodes: int(args, "max_new_nodes", 8),
                review_mode: args.get("review_mode").and_then(Value::as_bool),
                overlay: Default::default(),
            };
            graph.curate(&request)
        }
        "curate_noise" => {
            let request: CurateNoiseRequest =
                serde_json::from_value(args.clone()).unwrap_or_default();
            graph.curate_noise(&request)
        }
        "apply_pending_promotions" => {
            let ids = string_list(args.get("ids"));
            graph.apply_promotions(ids.as_deref())
        }
        "reject_pending_promotions" => {
            let ids = string_list(args.get("ids"));
            graph.reject_promotions(ids.as_deref())
        }
        "rebuild_vector_index" => {
            let request: RebuildRequest = serde_json::from_value(args.clone()).unwrap_or_default();
            graph.rebuild_vector_index(&request).map(|o| o.to_json())
        }
        "ingest_event" => {
            let request: IngestEventRequest =
                serde_json::from_value(args.clone()).unwrap_or_default();
            graph.ingest_event(&request).map(|o| o.to_json())
        }
        "set_node_sensitivity" => {
            let node_id = args
                .get("node_id")
                .and_then(Value::as_str)
                .unwrap_or_default();
            let local_only = args
                .get("local_only")
                .and_then(Value::as_bool)
                .unwrap_or(false);
            let reason = args.get("reason").and_then(Value::as_str);
            graph.set_node_sensitivity(node_id, local_only, reason)
        }
        "import_graph_data" => {
            let data = args
                .get("data")
                .and_then(Value::as_object)
                .cloned()
                .unwrap_or_else(Map::new);
            let request = ImportRequest {
                data,
                mode: args
                    .get("mode")
                    .and_then(Value::as_str)
                    .unwrap_or("merge")
                    .to_string(),
                dry_run: args
                    .get("dry_run")
                    .and_then(Value::as_bool)
                    .unwrap_or(false),
            };
            graph.import_graph_data(&request).map(|o| o.to_json())
        }
        "delete_document_tree" => {
            let node_id = args
                .get("node_id")
                .and_then(Value::as_str)
                .unwrap_or_default();
            graph.delete_document_tree(node_id)
        }
        "set_local_source_watch" => {
            let source_id = args
                .get("source_id")
                .and_then(Value::as_str)
                .unwrap_or_default();
            let enabled = args.get("enabled").and_then(Value::as_bool).unwrap_or(true);
            graph.set_local_source_watch(source_id, enabled)
        }
        "remove_local_source" => {
            let source_id = args
                .get("source_id")
                .and_then(Value::as_str)
                .unwrap_or_default();
            graph.remove_local_source(source_id)
        }
        other => Err(CoreError::InvalidRequest(format!(
            "graph mutation op not allowed: {other}"
        ))),
    }
}

/// Map a write-engine error to the HTTP status Python's seam produced.
pub fn status_for(error: &CoreError) -> u16 {
    match error {
        CoreError::InvalidRequest(_) => 400,
        _ => 500,
    }
}

pub fn error_body(error: &CoreError) -> Value {
    json!({ "detail": error.to_string() })
}

fn int(args: &Value, key: &str, default: i64) -> i64 {
    args.get(key).and_then(Value::as_i64).unwrap_or(default)
}

fn string_list(value: Option<&Value>) -> Option<Vec<String>> {
    match value {
        None | Some(Value::Null) => None,
        Some(Value::Array(items)) => Some(
            items
                .iter()
                .filter_map(Value::as_str)
                .map(str::to_string)
                .collect(),
        ),
        _ => None,
    }
}
