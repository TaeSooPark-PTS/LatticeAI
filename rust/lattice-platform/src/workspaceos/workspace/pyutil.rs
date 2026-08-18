//! The Python helpers the Workspace OS store is written on top of.
//!
//! Port of `latticeai/core/workspace_os_utils.py` plus the two time helpers it
//! borrows from `lattice_brain.utils`. None of these are interesting on their
//! own; every one of them decides the exact bytes of a stored record, so they
//! live together and are tested against the Python behaviour rather than
//! against what a Rust author would reach for.
//!
//! The three that would silently diverge if written from instinct:
//!
//! * [`now_iso`] is `datetime.now().isoformat(timespec="seconds")` — **naive
//!   local** time, no offset. Stamping UTC on a machine at UTC+9 would make
//!   every record nine hours old to the sort in `timeline()`, which compares
//!   these strings lexicographically against stamps Python wrote.
//! * [`py_dumps`] reproduces `json.dumps`'s *default* separators (`", "` and
//!   `": "`), not the compact ones a REST body uses. `_json_hash` feeds its
//!   output to sha256, so a missing space changes every generated id.
//! * [`deep_merge`] keeps the **default's** key order and only appends keys the
//!   loaded document adds — the shape `load_state` depends on.

use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::{Map, Value};
use sha2::{Digest, Sha256};

/// `datetime.now().isoformat(timespec="seconds")`.
pub fn now_iso() -> String {
    iso_seconds_for(naive_local_secs_now())
}

/// The same stamp, for a caller-supplied naive-local epoch second.
///
/// Split out so tests can pin the clock without a global.
pub fn iso_seconds_for(secs: i64) -> String {
    let (year, month, day) = civil_from_days(secs.div_euclid(86_400));
    let time_of_day = secs.rem_euclid(86_400);
    format!(
        "{year:04}-{month:02}-{day:02}T{:02}:{:02}:{:02}",
        time_of_day / 3600,
        (time_of_day % 3600) / 60,
        time_of_day % 60,
    )
}

/// Now, as naive local seconds since the epoch — Python's `datetime.now()`.
fn naive_local_secs_now() -> i64 {
    let utc = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs() as i64;
    naive_local_secs(utc).unwrap_or(utc)
}

#[cfg(unix)]
fn naive_local_secs(utc_secs: i64) -> Option<i64> {
    let stamp = utc_secs as libc::time_t;
    let mut broken: libc::tm = unsafe { std::mem::zeroed() };
    // SAFETY: `localtime_r` fills the caller-owned `tm` we just zeroed and
    // returns a pointer to it (or null on failure); nothing else is touched.
    if unsafe { libc::localtime_r(&stamp, &mut broken) }.is_null() {
        return None;
    }
    Some(utc_secs + i64::from(broken.tm_gmtoff as i32))
}

#[cfg(not(unix))]
fn naive_local_secs(utc_secs: i64) -> Option<i64> {
    Some(utc_secs)
}

/// Howard Hinnant's `civil_from_days`: days since 1970-01-01 → `(y, m, d)`.
fn civil_from_days(days: i64) -> (i64, u32, u32) {
    let shifted = days + 719_468;
    let era = shifted.div_euclid(146_097);
    let day_of_era = shifted.rem_euclid(146_097);
    let year_of_era =
        (day_of_era - day_of_era / 1460 + day_of_era / 36_524 - day_of_era / 146_096) / 365;
    let year = year_of_era + era * 400;
    let day_of_year = day_of_era - (365 * year_of_era + year_of_era / 4 - year_of_era / 100);
    let shifted_month = (5 * day_of_year + 2) / 153;
    let day = (day_of_year - (153 * shifted_month + 2) / 5 + 1) as u32;
    let month = if shifted_month < 10 {
        shifted_month + 3
    } else {
        shifted_month - 9
    } as u32;
    (year + i64::from(month <= 2), month, day)
}

/// `json.dumps(value, ensure_ascii=False, sort_keys=…)` with Python's
/// **default** separators.
///
/// `serde_json::to_string` writes `,` and `:`; CPython writes `, ` and `: `
/// unless `separators=` says otherwise. Only two call sites care, and both of
/// them are contracts: `_json_hash` (record ids) and `list_workflows`'s
/// substring query over the serialized steps.
pub fn py_dumps(value: &Value, sort_keys: bool) -> String {
    let mut out = String::new();
    write_value(&mut out, value, sort_keys);
    out
}

fn write_value(out: &mut String, value: &Value, sort_keys: bool) {
    match value {
        Value::Null => out.push_str("null"),
        Value::Bool(true) => out.push_str("true"),
        Value::Bool(false) => out.push_str("false"),
        Value::Number(number) => out.push_str(&number.to_string()),
        Value::String(text) => write_py_string(out, text),
        Value::Array(items) => {
            out.push('[');
            for (index, item) in items.iter().enumerate() {
                if index > 0 {
                    out.push_str(", ");
                }
                write_value(out, item, sort_keys);
            }
            out.push(']');
        }
        Value::Object(map) => {
            out.push('{');
            // `json.dumps(..., sort_keys=True)` sorts; without it, insertion
            // order is kept (this crate now compiles with `preserve_order`
            // unified from lattice-retrieval).
            let mut entries: Vec<_> = map.iter().collect();
            if sort_keys {
                entries.sort_by(|left, right| left.0.cmp(right.0));
            }
            for (index, (key, item)) in entries.iter().enumerate() {
                if index > 0 {
                    out.push_str(", ");
                }
                write_py_string(out, key);
                out.push_str(": ");
                write_value(out, item, sort_keys);
            }
            out.push('}');
        }
    }
}

/// `ensure_ascii=False` string escaping: only the characters JSON requires.
fn write_py_string(out: &mut String, text: &str) {
    out.push('"');
    for ch in text.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
}

/// `_json_hash` — sha256 over the sorted, default-separator dump.
pub fn json_hash(value: &Value) -> String {
    let payload = py_dumps(value, true);
    Sha256::digest(payload.as_bytes())
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

/// The first `n` characters of [`json_hash`] — how every id is built.
pub fn json_hash_prefix(value: &Value, n: usize) -> String {
    json_hash(value).chars().take(n).collect()
}

/// `_safe_slug` — the workspace-id and snapshot-file name maker.
///
/// Python's `str.isalnum()` is Unicode-aware (a Hangul syllable is alphanumeric
/// and survives), and so is Rust's `char::is_alphanumeric`; the two agree on
/// every character reachable through a workspace name.
pub fn safe_slug(raw: &str) -> String {
    let replaced: String = raw
        .trim()
        .chars()
        .map(|ch| {
            if ch.is_alphanumeric() || matches!(ch, '-' | '_' | '.') {
                ch
            } else {
                '-'
            }
        })
        .collect();
    let joined = replaced
        .split('-')
        .filter(|part| !part.is_empty())
        .collect::<Vec<_>>()
        .join("-");
    let value = if joined.is_empty() { "item" } else { &joined };
    value.chars().take(96).collect()
}

/// `_deep_merge(default, loaded)`.
pub fn deep_merge(default: &Value, loaded: Option<&Value>) -> Value {
    match (default, loaded) {
        (Value::Object(base), Some(Value::Object(over))) => {
            let mut merged = Map::new();
            for (key, value) in base {
                merged.insert(key.clone(), deep_merge(value, over.get(key)));
            }
            for (key, value) in over {
                if !merged.contains_key(key) {
                    merged.insert(key.clone(), value.clone());
                }
            }
            Value::Object(merged)
        }
        (_, None) | (_, Some(Value::Null)) => default.clone(),
        (_, Some(other)) => other.clone(),
    }
}

/// `_listify` — a list, or an empty one when the value is anything else.
pub fn listify(value: Option<&Value>) -> Vec<Value> {
    match value {
        Some(Value::Array(items)) => items.clone(),
        _ => Vec::new(),
    }
}

/// `_listify`, borrowed rather than cloned.
pub fn listify_ref(value: Option<&Value>) -> &[Value] {
    match value {
        Some(Value::Array(items)) => items.as_slice(),
        _ => &[],
    }
}

/// `str(value or "")` for a JSON field that Python stringifies.
pub fn as_str(value: Option<&Value>) -> &str {
    value.and_then(Value::as_str).unwrap_or_default()
}

/// A `Value` read as Python's truthiness for the `x or default` idiom.
pub fn or_empty_object(value: Option<&Value>) -> Value {
    match value {
        Some(Value::Object(map)) if !map.is_empty() => Value::Object(map.clone()),
        Some(Value::Object(_)) => Value::Object(Map::new()),
        _ => Value::Object(Map::new()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn the_dump_uses_pythons_default_separators() {
        let value = json!({"b": 1, "a": [1, 2, {"c": "x"}]});
        assert_eq!(
            py_dumps(&value, true),
            "{\"a\": [1, 2, {\"c\": \"x\"}], \"b\": 1}"
        );
    }

    #[test]
    fn non_ascii_survives_and_control_characters_escape() {
        assert_eq!(py_dumps(&json!("결정"), true), "\"결정\"");
        assert_eq!(
            py_dumps(&json!("a\nb\t\"c\""), true),
            "\"a\\nb\\t\\\"c\\\"\""
        );
        assert_eq!(py_dumps(&json!("\u{1}"), true), "\"\\u0001\"");
    }

    #[test]
    fn the_hash_is_stable_and_the_prefix_is_the_id_shape() {
        let value = json!(["decisions", "content", null, "2026-08-14T00:00:00"]);
        let digest = json_hash(&value);
        assert_eq!(digest.len(), 64);
        assert_eq!(json_hash_prefix(&value, 16), digest[..16]);
    }

    #[test]
    fn the_slug_collapses_runs_and_caps_at_96() {
        assert_eq!(safe_slug("org-Fixture Team"), "org-Fixture-Team");
        assert_eq!(safe_slug("  a // b  "), "a-b");
        assert_eq!(safe_slug(""), "item");
        assert_eq!(safe_slug("///"), "item");
        assert_eq!(safe_slug("org-결정"), "org-결정");
        assert_eq!(safe_slug(&"x".repeat(200)).chars().count(), 96);
        assert_eq!(
            safe_slug("keep.dots_and_underscores"),
            "keep.dots_and_underscores"
        );
    }

    #[test]
    fn the_merge_keeps_defaults_and_appends_extras() {
        let default = json!({"a": 1, "nested": {"x": 1, "y": 2}});
        let loaded = json!({"nested": {"y": 9, "z": 3}, "extra": true});
        assert_eq!(
            deep_merge(&default, Some(&loaded)),
            json!({"a": 1, "nested": {"x": 1, "y": 9, "z": 3}, "extra": true})
        );
        // `loaded is None` keeps the default — the `if loaded is None` arm.
        assert_eq!(deep_merge(&json!({"a": 1}), None), json!({"a": 1}));
        assert_eq!(deep_merge(&json!(1), Some(&json!(2))), json!(2));
    }

    #[test]
    fn listify_answers_an_empty_list_for_anything_that_is_not_one() {
        assert_eq!(listify(Some(&json!([1, 2]))), vec![json!(1), json!(2)]);
        assert!(listify(Some(&json!({}))).is_empty());
        assert!(listify(None).is_empty());
        assert_eq!(listify_ref(Some(&json!([1]))).len(), 1);
        assert!(listify_ref(None).is_empty());
    }

    #[test]
    fn the_stamp_reads_the_way_isoformat_does() {
        assert_eq!(iso_seconds_for(0), "1970-01-01T00:00:00");
        assert_eq!(iso_seconds_for(1_786_000_000), "2026-08-06T07:06:40");
        // A real stamp is 19 characters and parses back.
        let stamp = now_iso();
        assert_eq!(stamp.len(), 19, "{stamp}");
        assert!(lattice_core::parse_iso(Some(&stamp)).is_some());
    }

    #[test]
    fn helpers_for_the_or_idioms() {
        assert_eq!(as_str(Some(&json!("x"))), "x");
        assert_eq!(as_str(Some(&json!(1))), "");
        assert_eq!(as_str(None), "");
        assert_eq!(or_empty_object(Some(&json!({"a": 1}))), json!({"a": 1}));
        assert_eq!(or_empty_object(Some(&json!(null))), json!({}));
        assert_eq!(or_empty_object(None), json!({}));
        assert_eq!(or_empty_object(Some(&json!({}))), json!({}));
    }
}
