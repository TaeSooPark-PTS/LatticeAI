use serde_json::{Map, Value};

use crate::parse::pystr::{is_truthy, py_str};
use crate::tools::sandbox::ToolError;

pub fn required<'a>(args: &'a Map<String, Value>, key: &str) -> Result<&'a Value, ToolError> {
    args.get(key)
        .ok_or_else(|| ToolError::tool(format!("'{key}'")))
}

pub fn required_str(args: &Map<String, Value>, key: &str) -> Result<String, ToolError> {
    as_string(required(args, key)?, key)
}

pub fn optional_str(
    args: &Map<String, Value>,
    key: &str,
    default: &str,
) -> Result<String, ToolError> {
    match args.get(key) {
        None => Ok(default.to_string()),
        Some(value) => as_string(value, key),
    }
}

pub fn coerced_str(args: &Map<String, Value>, key: &str, fallback: &str) -> String {
    match args.get(key) {
        Some(value) if is_truthy(value) => py_str(value),
        _ => fallback.to_string(),
    }
}

pub fn defaulted_str(args: &Map<String, Value>, key: &str, handler_default: &str) -> String {
    match args.get(key) {
        None => handler_default.to_string(),
        Some(value) if is_truthy(value) => py_str(value),
        Some(_) => String::new(),
    }
}

pub fn truthy_str(
    args: &Map<String, Value>,
    key: &str,
    fallback: &str,
) -> Result<String, ToolError> {
    match args.get(key).filter(|value| is_truthy(value)) {
        None => Ok(fallback.to_string()),
        Some(value) => as_string(value, key),
    }
}

pub fn truthy_value<'a>(args: &'a Map<String, Value>, key: &str) -> Option<&'a Value> {
    args.get(key).filter(|value| is_truthy(value))
}

fn type_name(value: &Value) -> &'static str {
    match value {
        Value::Null => "NoneType",
        Value::Bool(_) => "bool",
        Value::Number(number) => {
            if number.is_f64() {
                "float"
            } else {
                "int"
            }
        }
        Value::String(_) => "str",
        Value::Array(_) => "list",
        Value::Object(_) => "dict",
    }
}

fn as_string(value: &Value, key: &str) -> Result<String, ToolError> {
    match value {
        Value::String(text) => Ok(text.clone()),
        other => Err(ToolError::tool(format!(
            "'{key}' must be a string, not {}.",
            type_name(other)
        ))),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn args(value: Value) -> Map<String, Value> {
        value.as_object().expect("object").clone()
    }

    #[test]
    fn a_missing_key_is_pythons_key_error_repr() {
        let empty = Map::new();
        assert_eq!(
            required(&empty, "path").expect_err("missing").message,
            "'path'"
        );
        assert_eq!(
            required_str(&empty, "old_string")
                .expect_err("missing")
                .message,
            "'old_string'"
        );
    }

    #[test]
    fn optional_reads_the_default_only_when_the_key_is_absent() {
        let present = args(json!({"content": "x"}));
        assert_eq!(
            optional_str(&present, "content", "fallback").expect("string"),
            "x"
        );
        let empty = args(json!({"content": ""}));
        assert_eq!(optional_str(&empty, "content", "fallback").expect("s"), "");
        assert_eq!(
            optional_str(&Map::new(), "content", "fallback").expect("s"),
            "fallback"
        );
    }

    #[test]
    fn coerced_takes_the_fallback_for_anything_falsy() {
        for falsy in [json!(""), json!(null), json!(0), json!(false), json!([])] {
            let map = args(json!({"app": falsy}));
            assert_eq!(coerced_str(&map, "app", "Google Chrome"), "Google Chrome");
        }
        assert_eq!(
            coerced_str(&args(json!({"app": "Safari"})), "app", "Google Chrome"),
            "Safari"
        );
        assert_eq!(coerced_str(&args(json!({"app": 7})), "app", "d"), "7");
        assert_eq!(coerced_str(&Map::new(), "app", "d"), "d");
    }

    #[test]
    fn a_non_string_where_a_path_belongs_is_a_named_refusal() {
        let map = args(json!({"path": 7, "content": null}));
        assert_eq!(
            required_str(&map, "path").expect_err("type").message,
            "'path' must be a string, not int."
        );
        assert_eq!(
            optional_str(&map, "content", "").expect_err("type").message,
            "'content' must be a string, not NoneType."
        );
    }

    #[test]
    fn truthy_value_drops_the_falsy_ones() {
        let map = args(json!({"todos": [], "keep": [1]}));
        assert_eq!(truthy_value(&map, "todos"), None);
        assert_eq!(truthy_value(&map, "keep"), Some(&json!([1])));
        assert_eq!(truthy_value(&map, "absent"), None);
    }

    #[test]
    fn the_type_names_are_the_python_ones() {
        assert_eq!(type_name(&json!(1.5)), "float");
        assert_eq!(type_name(&json!(1)), "int");
        assert_eq!(type_name(&json!(true)), "bool");
        assert_eq!(type_name(&json!({})), "dict");
        assert_eq!(type_name(&json!([])), "list");
        assert_eq!(type_name(&json!("s")), "str");
    }
}
