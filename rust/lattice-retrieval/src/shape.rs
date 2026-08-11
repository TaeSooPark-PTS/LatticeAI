//! The response shape `hybrid_search` has to produce, and the Python
//! truthiness it is built on.
//!
//! Split out of `hybrid.rs` so the pipeline file stays readable: everything here
//! is a pure function over JSON, everything there is the order of operations.
//! `truthy` is the one to read twice — Python's `or` chains treat `0`, `""` and
//! `{}` as absent, and this codebase has repeatedly been bitten by a score of
//! `0.0` disappearing because Rust would have called it `Some(0.0)`.

use serde_json::{Map, Value};

/// `fusion.DEFAULT_EXPANSION_CAP` — reported even while expansion is off.
pub const DEFAULT_EXPANSION_CAP: i64 = 5;
/// Node types that make a result set more than prose (`retrieval.signals`).
pub const MULTIMODAL_NODE_TYPES: [&str; 2] = ["Image", "ImageText"];

/// `_parent_node_id` — chunk hits dedupe to the content node they came from.
pub fn parent_node_id(item: &Map<String, Value>) -> String {
    if item.get("type").and_then(Value::as_str) == Some("Chunk") {
        if let Some(Value::Object(meta)) = item.get("metadata") {
            let parent = meta
                .get("source_node")
                .filter(|v| truthy(v))
                .or_else(|| meta.get("parent_source_node").filter(|v| truthy(v)));
            if let Some(parent) = parent {
                return py_str(parent);
            }
        }
    }
    let fallback = item
        .get("node_id")
        .filter(|v| truthy(v))
        .or_else(|| item.get("id").filter(|v| truthy(v)));
    fallback.map(py_str).unwrap_or_default()
}

pub fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(flag) => *flag,
        Value::Number(number) => number.as_f64() != Some(0.0),
        Value::String(text) => !text.is_empty(),
        Value::Array(items) => !items.is_empty(),
        Value::Object(map) => !map.is_empty(),
    }
}

pub fn py_str(value: &Value) -> String {
    match value {
        Value::String(text) => text.clone(),
        other => other.to_string(),
    }
}

pub fn empty_result(
    query: &str,
    alpha: f64,
    query_class: &Option<String>,
    top_k: i64,
    search_query: &str,
    rewrite_rules: &[String],
) -> Value {
    let mut out = Map::new();
    out.insert("query".into(), Value::String(query.to_string()));
    out.insert("mode".into(), Value::String("hybrid".into()));
    out.insert("alpha".into(), Value::from(alpha));
    out.insert("query_class".into(), class_json(query_class));
    out.insert("top_k".into(), Value::from(top_k));
    out.insert("sources".into(), sources_json(0, 0));
    out.insert("matches".into(), Value::Array(Vec::new()));
    out.insert("policy".into(), policy_json(search_query, rewrite_rules));
    out.insert("fusion_strategy".into(), Value::String("alpha".into()));
    out.insert("detail".into(), Value::Null);
    Value::Object(out)
}

pub fn class_json(query_class: &Option<String>) -> Value {
    query_class
        .clone()
        .map(Value::String)
        .unwrap_or(Value::Null)
}

pub fn sources_json(lexical: usize, vector: usize) -> Value {
    let mut map = Map::new();
    map.insert("lexical".into(), Value::from(lexical));
    map.insert("vector".into(), Value::from(vector));
    Value::Object(map)
}

pub fn policy_json(search_query: &str, rewrite_rules: &[String]) -> Value {
    let mut map = Map::new();
    map.insert(
        "search_query".into(),
        Value::String(search_query.to_string()),
    );
    map.insert(
        "rewrite_rules".into(),
        Value::Array(rewrite_rules.iter().cloned().map(Value::String).collect()),
    );
    Value::Object(map)
}

pub fn expansion_json() -> Value {
    let mut map = Map::new();
    map.insert("enabled".into(), Value::Bool(false));
    map.insert("seeds".into(), Value::from(0));
    map.insert("added".into(), Value::from(0));
    map.insert("cap".into(), Value::from(DEFAULT_EXPANSION_CAP));
    map.insert("truncated".into(), Value::Bool(false));
    map.insert("failed_seeds".into(), Value::from(0));
    Value::Object(map)
}

pub fn multimodal_signal(matches: &[Value]) -> Option<Value> {
    let mut images = 0usize;
    let mut seen: Vec<String> = Vec::new();
    for item in matches {
        let node_type = item.get("type").and_then(Value::as_str).unwrap_or("");
        if MULTIMODAL_NODE_TYPES.contains(&node_type) {
            images += 1;
            if !seen.iter().any(|known| known == node_type) {
                seen.push(node_type.to_string());
            }
        }
    }
    if images == 0 {
        return None;
    }
    let mut map = Map::new();
    map.insert("images".into(), Value::from(images));
    map.insert(
        "types".into(),
        Value::Array(seen.into_iter().map(Value::String).collect()),
    );
    Some(Value::Object(map))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn parent_node_id_prefers_a_chunks_source() {
        let chunk = json!({"type": "Chunk", "metadata": {"source_node": "doc:1"}, "node_id": "x"});
        assert_eq!(parent_node_id(chunk.as_object().unwrap()), "doc:1");
        let fallback = json!({"type": "Chunk", "metadata": {"parent_source_node": "doc:2"}});
        assert_eq!(parent_node_id(fallback.as_object().unwrap()), "doc:2");
        let bare = json!({"type": "Chunk", "metadata": {}, "node_id": "doc:3"});
        assert_eq!(parent_node_id(bare.as_object().unwrap()), "doc:3");
        let no_meta = json!({"type": "Chunk", "id": "doc:4"});
        assert_eq!(parent_node_id(no_meta.as_object().unwrap()), "doc:4");
        let plain = json!({"type": "Document", "metadata": {"source_node": "ignored"}, "id": "d"});
        assert_eq!(parent_node_id(plain.as_object().unwrap()), "d");
        assert_eq!(parent_node_id(json!({}).as_object().unwrap()), "");
        let numeric = json!({"type": "Chunk", "metadata": {"source_node": 7}});
        assert_eq!(parent_node_id(numeric.as_object().unwrap()), "7");
    }

    #[test]
    fn truthiness_follows_python_not_rust() {
        assert!(!truthy(&Value::Null));
        assert!(!truthy(&json!(0)));
        assert!(!truthy(&json!("")));
        assert!(!truthy(&json!([])));
        assert!(!truthy(&json!({})));
        assert!(!truthy(&json!(false)));
        assert!(truthy(&json!(0.5)));
        assert!(truthy(&json!("x")));
        assert!(truthy(&json!([1])));
        assert!(truthy(&json!({"a": 1})));
        assert!(truthy(&json!(true)));
        assert_eq!(py_str(&json!("x")), "x");
        assert_eq!(py_str(&json!(3)), "3");
    }

    #[test]
    fn multimodal_is_absent_unless_a_picture_is_in_the_answer() {
        assert!(multimodal_signal(&[json!({"type": "Document"})]).is_none());
        let signal = multimodal_signal(&[
            json!({"type": "Image"}),
            json!({"type": "ImageText"}),
            json!({"type": "Image"}),
        ])
        .unwrap();
        assert_eq!(signal["images"], 3);
        assert_eq!(signal["types"], json!(["Image", "ImageText"]));
    }

    #[test]
    fn the_shared_blocks_have_the_python_shape() {
        assert_eq!(expansion_json()["cap"], 5);
        assert_eq!(expansion_json()["enabled"], false);
        assert_eq!(sources_json(2, 3)["lexical"], 2);
        assert_eq!(
            policy_json("q", &["r".to_string()])["rewrite_rules"],
            json!(["r"])
        );
        assert_eq!(class_json(&None), Value::Null);
        assert_eq!(class_json(&Some("fact".into())), "fact");
        let empty = empty_result("", 0.6, &None, 20, "", &[]);
        assert_eq!(empty["mode"], "hybrid");
        assert_eq!(empty["matches"], json!([]));
        assert!(
            empty.get("vector").is_none(),
            "the early return carries no vector block"
        );
    }
}
