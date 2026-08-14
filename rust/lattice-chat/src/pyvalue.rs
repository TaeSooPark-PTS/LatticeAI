//! Two Python primitives the ported chat code leans on constantly.
//!
//! `lattice-retrieval` has its own copies (`shape::truthy` / `shape::py_str`)
//! but they are `pub` on a crate whose semantics are the retrieval layer's;
//! these are chat's, they are four lines each, and having them here means the
//! chat port never has to reason about which crate's definition it imported.
//!
//! * [`truthy`] is `bool(value)` — empty string, empty list, `0`, `null` and
//!   `false` are all falsy. Chat asks it about a *value that may legitimately
//!   be zero*, so `unwrap_or` defaults are wrong wherever this is right.
//! * [`text`] is `str(value)` for the shapes JSON can hold: a string is itself
//!   (**not** quoted), everything else renders the way `json.dumps` would,
//!   which is what the Python code's f-strings produce for scalars.

use serde_json::Value;

/// `bool(value)`.
pub fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(flag) => *flag,
        Value::Number(number) => number.as_f64().is_some_and(|number| number != 0.0),
        Value::String(text) => !text.is_empty(),
        Value::Array(items) => !items.is_empty(),
        Value::Object(entries) => !entries.is_empty(),
    }
}

/// `str(value)` for JSON scalars — a string stays unquoted.
pub fn text(value: &Value) -> String {
    match value {
        Value::String(text) => text.clone(),
        Value::Null => String::new(),
        other => other.to_string(),
    }
}

/// The string at `key`, or `""` — the reading every optional field gets.
pub fn field(value: &Value, key: &str) -> String {
    value
        .get(key)
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn truthiness_is_pythons() {
        for falsy in [
            json!(null),
            json!(false),
            json!(0),
            json!(0.0),
            json!(""),
            json!([]),
            json!({}),
        ] {
            assert!(!truthy(&falsy), "{falsy} should be falsy");
        }
        for value in [
            json!(true),
            json!(1),
            json!(-1),
            json!("x"),
            json!([0]),
            json!({"a": 1}),
        ] {
            assert!(truthy(&value), "{value} should be truthy");
        }
    }

    #[test]
    fn text_leaves_a_string_unquoted() {
        assert_eq!(text(&json!("hi")), "hi");
        assert_eq!(text(&json!(null)), "");
        assert_eq!(text(&json!(3)), "3");
        assert_eq!(text(&json!(["a"])), "[\"a\"]");
    }

    #[test]
    fn field_reads_strings_and_defaults_to_empty() {
        assert_eq!(field(&json!({"a": "b"}), "a"), "b");
        assert_eq!(field(&json!({"a": 1}), "a"), "");
        assert_eq!(field(&json!(null), "a"), "");
    }
}
