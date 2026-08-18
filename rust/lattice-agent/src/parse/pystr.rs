//! Python string/`repr` semantics the loop depends on for byte parity.
//!
//! Three of them are load-bearing and none is obvious in Rust:
//!
//! * **Slicing is by code point.** `str(raw)[:400]` in the parse-error step and
//!   `str(thoughts)[:600]` in every executor step cut *characters*, not bytes.
//!   A byte slice of Korean prose would panic or split a grapheme; the
//!   transcript would then differ from Python's on the first non-ASCII run.
//! * **`str(x or "")` is two operations.** Falsiness first (`None`, `False`,
//!   `0`, `""`, `[]`, `{}` all become `""`), then `str`. `str(5)` is `"5"` and
//!   `str(True)` is `"True"` — not what a JSON encoder would produce.
//! * **`f"…{list_of_str}"` interpolates `repr`.** The rollback message embeds a
//!   Python list literal, single quotes and all, so the port has to spell
//!   `repr` out rather than reach for JSON.

use serde_json::Value;

/// `text[:limit]` — Python's code-point slice.
pub fn char_slice(text: &str, limit: usize) -> &str {
    match text.char_indices().nth(limit) {
        Some((offset, _)) => &text[..offset],
        None => text,
    }
}

/// `len(text)` — code points, not bytes.
pub fn char_len(text: &str) -> usize {
    text.chars().count()
}

/// Python truthiness for the JSON values the loop actually carries.
pub fn is_truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(flag) => *flag,
        Value::Number(number) => number.as_f64().is_some_and(|value| value != 0.0),
        Value::String(text) => !text.is_empty(),
        Value::Array(items) => !items.is_empty(),
        Value::Object(map) => !map.is_empty(),
    }
}

/// `str(value)`.
pub fn py_str(value: &Value) -> String {
    match value {
        Value::Null => "None".into(),
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        Value::Number(number) => number.to_string(),
        Value::String(text) => text.clone(),
        Value::Array(_) | Value::Object(_) => py_repr(value),
    }
}

/// `str(value or "")` — the idiom the executor reads `action`/`thoughts` with.
pub fn py_str_or_empty(value: Option<&Value>) -> String {
    match value {
        Some(value) if is_truthy(value) => py_str(value),
        _ => String::new(),
    }
}

/// `repr(value)` for the JSON value space.
pub fn py_repr(value: &Value) -> String {
    match value {
        Value::Null => "None".into(),
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        Value::Number(number) => number.to_string(),
        Value::String(text) => repr_str(text),
        Value::Array(items) => {
            let inner: Vec<String> = items.iter().map(py_repr).collect();
            format!("[{}]", inner.join(", "))
        }
        Value::Object(map) => {
            let inner: Vec<String> = map
                .iter()
                .map(|(key, value)| format!("{}: {}", repr_str(key), py_repr(value)))
                .collect();
            format!("{{{}}}", inner.join(", "))
        }
    }
}

/// `repr` of a `str`: single quotes, unless the text holds a `'` and no `"`.
pub fn repr_str(text: &str) -> String {
    let quote = if text.contains('\'') && !text.contains('"') {
        '"'
    } else {
        '\''
    };
    let mut out = String::with_capacity(text.len() + 2);
    out.push(quote);
    for character in text.chars() {
        match character {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            other if other == quote => {
                out.push('\\');
                out.push(other);
            }
            other => out.push(other),
        }
    }
    out.push(quote);
    out
}

/// `repr` of a `list[str]`, which is what the rollback message interpolates.
pub fn py_list_repr(items: &[String]) -> String {
    let inner: Vec<String> = items.iter().map(|item| repr_str(item)).collect();
    format!("[{}]", inner.join(", "))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn slicing_counts_code_points_not_bytes() {
        // 400 bytes of this is a panic; 4 characters of it is the answer.
        assert_eq!(char_slice("작업을 완료했습니다", 4), "작업을 ");
        assert_eq!(char_len("작업을"), 3);
        assert_eq!(char_slice("short", 99), "short");
        assert_eq!(char_slice("", 3), "");
    }

    #[test]
    fn falsiness_is_pythons_not_serdes() {
        for falsy in [
            json!(null),
            json!(false),
            json!(0),
            json!(0.0),
            json!(""),
            json!([]),
            json!({}),
        ] {
            assert!(!is_truthy(&falsy), "{falsy} must be falsy");
            assert_eq!(py_str_or_empty(Some(&falsy)), "");
        }
        for truthy in [
            json!(true),
            json!(1),
            json!("x"),
            json!([0]),
            json!({"a": 1}),
        ] {
            assert!(is_truthy(&truthy), "{truthy} must be truthy");
        }
        assert_eq!(py_str_or_empty(None), "");
    }

    #[test]
    fn str_of_a_non_string_is_pythons_str() {
        assert_eq!(py_str(&json!(5)), "5");
        assert_eq!(py_str(&json!(true)), "True");
        assert_eq!(py_str(&json!(null)), "None");
        assert_eq!(py_str(&json!("write_file")), "write_file");
        assert_eq!(py_str_or_empty(Some(&json!(5))), "5");
    }

    #[test]
    fn repr_quotes_the_way_python_quotes() {
        assert_eq!(repr_str("plain"), "'plain'");
        assert_eq!(repr_str("it's"), "\"it's\"");
        assert_eq!(repr_str("both ' and \""), "'both \\' and \"'");
        assert_eq!(repr_str("line\nbreak"), "'line\\nbreak'");
        assert_eq!(repr_str("back\\slash"), "'back\\\\slash'");
    }

    #[test]
    fn a_list_of_paths_reprs_like_the_rollback_message() {
        assert_eq!(
            py_list_repr(&["notes/a.md (git)".into(), "b.txt (snapshot)".into()]),
            "['notes/a.md (git)', 'b.txt (snapshot)']"
        );
        assert_eq!(py_list_repr(&[]), "[]");
    }

    #[test]
    fn container_repr_is_the_python_literal_form() {
        assert_eq!(
            py_repr(&json!([1, "a", true, null])),
            "[1, 'a', True, None]"
        );
        assert_eq!(py_repr(&json!({"k": "v"})), "{'k': 'v'}");
    }
}
