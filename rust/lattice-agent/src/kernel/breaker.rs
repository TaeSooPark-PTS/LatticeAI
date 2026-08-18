//! Circuit breakers — the denials no mode can lift.
//!
//! `latticeai.core.permission_mode.is_circuit_breaker`, in its own module for
//! the reason the Python docstring gives: these refusals do not read the mode.
//! `bypass` skips approval prompts; it never unlocks a destructive tool, a
//! root/home target, or an `rm -rf /` shell string. Keeping them out of
//! [`crate::kernel::mode`] is how the code says that rather than merely claiming it.
//!
//! One thing is deliberately *absent*: system-sandbox writes. Deciding them
//! here would make `bypass` unable to drive the desktop at all, so they stay
//! mode-sensitive in [`crate::kernel::mode::effective_auto_approve`] — the same
//! reasoning, and the same comment, as the Python original.

use serde_json::{Map, Value};

use crate::kernel::policy::ToolPolicy;

/// `str(args.get(first) or args.get(second) or "")`.
///
/// Falsy values fall through to the next key exactly as Python's `or` chain
/// does, so `{"path": ""}` reads the `filename` key instead of short-circuiting
/// on an empty string.
fn arg_text(args: &Map<String, Value>, keys: [&str; 2]) -> String {
    for key in keys {
        match args.get(key) {
            Some(Value::String(text)) if !text.is_empty() => return text.clone(),
            Some(Value::Bool(true)) => return "True".into(),
            Some(Value::Number(number)) if number.as_f64() != Some(0.0) => {
                return number.to_string()
            }
            Some(Value::Array(items)) if !items.is_empty() => {
                return Value::Array(items.clone()).to_string()
            }
            Some(Value::Object(map)) if !map.is_empty() => {
                return Value::Object(map.clone()).to_string()
            }
            _ => continue,
        }
    }
    String::new()
}

/// Python's `repr()` of a string, which is what the breaker message embeds.
fn py_repr(text: &str) -> String {
    let quote = if text.contains('\'') && !text.contains('"') {
        '"'
    } else {
        '\''
    };
    let mut out = String::with_capacity(text.len() + 2);
    out.push(quote);
    for ch in text.chars() {
        match ch {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c == quote => {
                out.push('\\');
                out.push(c);
            }
            c if (c as u32) < 0x20 || c as u32 == 0x7f => {
                out.push_str(&format!("\\x{:02x}", c as u32))
            }
            c => out.push(c),
        }
    }
    out.push(quote);
    out
}

/// A reason this call must be denied **in every mode**, or `None`.
pub fn is_circuit_breaker(
    _tool_name: &str,
    policy: &ToolPolicy,
    args: &Map<String, Value>,
) -> Option<String> {
    if policy.is_destructive() {
        return Some("destructive action is always blocked".into());
    }

    let path = arg_text(args, ["path", "filename"]);
    if !path.is_empty() {
        let normalized = path.replace('\\', "/");
        let trimmed = normalized.trim_end_matches('/');
        if matches!(normalized.as_str(), "/" | "~" | "/home" | "/Users")
            || matches!(trimmed, "/" | "~")
        {
            return Some(format!("circuit breaker: refusing path {}", py_repr(&path)));
        }
    }

    let command = arg_text(args, ["command", "cmd"]).to_lowercase();
    for token in ["rm -rf /", "rm -rf ~", "rm -rf /*", "rm -rf $home"] {
        if command.contains(token) {
            return Some("circuit breaker: refusing destructive shell command".into());
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::kernel::mode::ALL_MODES;
    use serde_json::json;

    fn args(value: Value) -> Map<String, Value> {
        value.as_object().expect("object").clone()
    }

    #[test]
    fn a_breaker_fires_in_every_mode_including_bypass() {
        let policy = ToolPolicy::default();
        let call = args(json!({"path": "/"}));
        // The mode is not even a parameter — that is the property.
        for mode in ALL_MODES {
            assert!(
                is_circuit_breaker("write_file", &policy, &call).is_some(),
                "{mode:?}"
            );
        }
    }

    #[test]
    fn a_destructive_policy_is_a_breaker_by_either_spelling() {
        let call = args(json!({"path": "notes/a.txt"}));
        for policy in [
            ToolPolicy {
                destructive: true,
                ..ToolPolicy::default()
            },
            ToolPolicy {
                risk: "destructive".into(),
                ..ToolPolicy::default()
            },
        ] {
            assert_eq!(
                is_circuit_breaker("delete_file", &policy, &call).as_deref(),
                Some("destructive action is always blocked")
            );
        }
    }

    #[test]
    fn the_root_guard_normalises_backslashes_and_trailing_slashes() {
        let policy = ToolPolicy::default();
        for path in ["/", "~", "/home", "/Users", "\\home", "\\", "~/", "~//"] {
            let call = args(json!({ "path": path }));
            assert!(
                is_circuit_breaker("write_file", &policy, &call).is_some(),
                "{path} must trip the breaker"
            );
        }
        // `rstrip("/")` of "//" is "", which is in neither set — Python does not
        // trip here and neither does the port. Pinned because it looks like it
        // should, and a "fix" would be a divergence.
        for path in [
            "//",
            "/home/",
            "/home/user",
            "~/notes",
            "notes/a.txt",
            "/Users/me",
        ] {
            let call = args(json!({ "path": path }));
            assert!(
                is_circuit_breaker("write_file", &policy, &call).is_none(),
                "{path} must not trip the breaker"
            );
        }
    }

    #[test]
    fn the_shell_breaker_is_case_insensitive_and_reads_both_keys() {
        let policy = ToolPolicy::default();
        for value in ["rm -rf /", "RM -RF ~", "sudo rm -rf /* now", "rm -rf $HOME"] {
            for key in ["command", "cmd"] {
                let call = args(json!({ key: value }));
                assert_eq!(
                    is_circuit_breaker("run_command", &policy, &call).as_deref(),
                    Some("circuit breaker: refusing destructive shell command"),
                    "{key}={value}"
                );
            }
        }
        let harmless = args(json!({"command": "rm -rf build"}));
        assert!(is_circuit_breaker("run_command", &policy, &harmless).is_none());
    }

    #[test]
    fn the_breaker_message_uses_python_repr() {
        assert_eq!(py_repr("/"), "'/'");
        assert_eq!(py_repr("\\"), "'\\\\'");
        assert_eq!(py_repr("a'b"), "\"a'b\"");
        assert_eq!(py_repr("a'b\"c"), "'a\\'b\"c'");
        assert_eq!(py_repr("a\nb"), "'a\\nb'");
        assert_eq!(py_repr("\u{1}"), "'\\x01'");
    }

    #[test]
    fn an_empty_path_falls_through_to_the_filename_key() {
        let call = args(json!({"path": "", "filename": "/"}));
        let policy = ToolPolicy::default();
        assert!(is_circuit_breaker("create_pdf", &policy, &call).is_some());
    }

    #[test]
    fn a_non_string_argument_stringifies_the_way_python_does() {
        let policy = ToolPolicy::default();
        // Falsy values are skipped, truthy non-strings are stringified — and
        // neither shape can accidentally match a guarded path.
        for args_value in [
            json!({"path": 0}),
            json!({"path": false}),
            json!({"path": []}),
        ] {
            assert!(is_circuit_breaker("write_file", &policy, &args(args_value)).is_none());
        }
        assert!(is_circuit_breaker("write_file", &policy, &args(json!({"path": 5}))).is_none());
    }
}
