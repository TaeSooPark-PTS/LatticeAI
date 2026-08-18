//! Argument extraction that speaks Python's error text.
//!
//! `TOOL_HANDLERS` is a table of lambdas over one `args` dict, and the way each
//! lambda reads that dict is part of the contract: `a["path"]` raises
//! `KeyError`, `a.get("path", ".")` does not, and the seam turns a `KeyError`
//! into `{"error": str(exc)}` — which for `KeyError("path")` is the **repr of
//! the key**, `'path'`, quotes included. Porting the handlers without porting
//! that would change what the transcript records for a malformed tool call.

use serde_json::{Map, Value};

use crate::parse::pystr::{is_truthy, py_str};
use crate::tools::sandbox::ToolError;

/// `args[key]` — absent is `KeyError`, whose `str()` is `'key'`.
pub fn required<'a>(args: &'a Map<String, Value>, key: &str) -> Result<&'a Value, ToolError> {
    args.get(key)
        .ok_or_else(|| ToolError::tool(format!("'{key}'")))
}

/// `args[key]`, as the string the handler passes straight into a path or a
/// `.encode()`.
pub fn required_str(args: &Map<String, Value>, key: &str) -> Result<String, ToolError> {
    as_string(required(args, key)?, key)
}

/// `args.get(key, default)` for a value used as a string without an `or`.
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

/// `str(args.get(key) or fallback)` — the idiom for a value the tool
/// stringifies anyway, so **any** type is accepted and a falsy one takes the
/// fallback exactly as Python's `or` does.
pub fn coerced_str(args: &Map<String, Value>, key: &str, fallback: &str) -> String {
    match args.get(key) {
        Some(value) if is_truthy(value) => py_str(value),
        _ => fallback.to_string(),
    }
}

/// The two-layer default: the **handler's** (`a.get(key, default)`, so an
/// absent key takes `handler_default`) and the **tool's** (`str(value or "")`,
/// so a *present* falsy value becomes the empty string rather than the
/// handler's default). `computer_open_url`'s `app` is the case that makes the
/// distinction visible: absent means Chrome, explicitly empty means "no app".
pub fn defaulted_str(args: &Map<String, Value>, key: &str, handler_default: &str) -> String {
    match args.get(key) {
        None => handler_default.to_string(),
        Some(value) if is_truthy(value) => py_str(value),
        Some(_) => String::new(),
    }
}

/// `args.get(key) or fallback` for a value that must then **be** a string —
/// `cwd` is the one: `_resolve_path(cwd or ".")` takes the fallback for a falsy
/// value and would raise on a non-string truthy one.
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

/// `args.get(key) or []` — a falsy value is the empty list, as Python's `or`.
pub fn truthy_value<'a>(args: &'a Map<String, Value>, key: &str) -> Option<&'a Value> {
    args.get(key).filter(|value| is_truthy(value))
}

/// The type name Python would print, for the one deviation this module has.
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

/// A string argument, or the stated deviation.
///
/// Python hands a non-string to `PurePath.__truediv__` or `str.encode` and
/// raises `TypeError` / `AttributeError`. The seam catches the first and
/// answers 500 for the second; either way the loop learns nothing useful. A
/// named refusal is recorded instead — the step still fails, and it says why.
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
        // A *present* empty string is not an absent key: `.get(k, d)` returns
        // it, and the document creators depend on that ("" → artifact.docx).
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
        // Any type stringifies, the way `str()` does.
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
