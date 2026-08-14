//! The Python primitives the write engine's *bytes* depend on.
//!
//! None of these is interesting on its own. Every one of them decides either
//! the exact text stored in a `*_json` column or an id derived by hashing that
//! text, so writing any of them from Rust instinct would produce a store that
//! reads back the same and hashes differently.
//!
//! The four that would silently diverge:
//!
//! * [`py_dumps`] is `json.dumps(value, ensure_ascii=False, sort_keys=True)`
//!   with CPython's **default** separators (`", "` and `": "`).
//!   `serde_json::to_string` writes `,` and `:`; every `metadata_json` in the
//!   graph, and therefore every `edge:`/`event:` id hashed from one, would
//!   differ by exactly those spaces.
//! * [`slug`] keeps Hangul (`가-힣`) as-is and collapses everything else — the
//!   `conversation:`/`person:`/`topic:` ids are built from it.
//! * [`py_float_repr`] is `str(float)`: `1785974400.0`, not `1785974400`. It
//!   seeds `vector_index_operations.id`.
//! * [`truncate_chars`] (in [`crate::pytext`]) is Python's `[:n]` — characters,
//!   not bytes. A 240-byte cut through a Korean title is a panic in Rust and a
//!   different title in the store.

use serde_json::{Map, Value};
use sha2::{Digest, Sha256};

/// `json.dumps(value, ensure_ascii=False, sort_keys=True)`, default separators.
///
/// The keys are sorted **here**, explicitly. They used to be left to
/// `serde_json::Map`, which is a `BTreeMap` and therefore already iterates in
/// code-point order — the order CPython's `sort_keys=True` produces, because
/// UTF-8 byte order and code-point order agree for `str` keys. That reasoning
/// was sound and the premise was not: `lattice-retrieval` enables
/// `serde_json/preserve_order`, and cargo unifies features across a build, so
/// in **every build that contains the product** (`lattice-host` depends on
/// `lattice-retrieval`) a `Map` is an `IndexMap` and iterates in insertion
/// order. `py_dumps` was then writing unsorted `metadata_json` — read-back
/// identical, hashed differently, which is exactly the divergence this module
/// exists to prevent. Sorting here makes the function independent of a feature
/// another crate chose.
pub fn py_dumps(value: &Value) -> String {
    let mut out = String::new();
    write_value(&mut out, value);
    out
}

/// `json.dumps(value, ensure_ascii=False)` — **no** `sort_keys`.
///
/// One writer in the graph omits `sort_keys`: `_store_pending_promotions`. Its
/// value is a row in `graph_meta`, so the key order is part of the bytes, and
/// CPython's is the order the dict was built in. `serde_json::Map` is a
/// `BTreeMap` and has forgotten that, so the order is supplied: `key_order`
/// first (those that are present), then whatever else the object carries.
///
/// Documented limit: an object whose extra keys the caller did not name falls
/// back to sorted order for that tail. The only producer of the queue is
/// `curate`, whose entries this covers exactly.
pub fn py_dumps_ordered(value: &Value, key_order: &[&str]) -> String {
    let mut out = String::new();
    write_ordered(&mut out, value, key_order);
    out
}

fn write_ordered(out: &mut String, value: &Value, key_order: &[&str]) {
    match value {
        // Only the outermost objects are reordered: an array's elements are the
        // records `_store_pending_promotions` builds, and everything *inside*
        // one of those was itself written by `_json` and is therefore already
        // sorted. Recursing with `key_order` would reorder a nested object that
        // happened to share a key name with the outer one.
        Value::Array(items) => {
            out.push('[');
            for (index, item) in items.iter().enumerate() {
                if index > 0 {
                    out.push_str(", ");
                }
                write_ordered(out, item, key_order);
            }
            out.push(']');
        }
        Value::Object(map) => {
            let mut keys: Vec<&String> = key_order
                .iter()
                .filter_map(|wanted| map.keys().find(|key| key.as_str() == *wanted))
                .collect();
            // Whatever the caller did not name, in sorted order — the tail
            // this function documents. `map.keys()` would be insertion order
            // under `preserve_order` (see [`py_dumps`]).
            for key in sorted_keys(map) {
                if !keys.contains(&key) {
                    keys.push(key);
                }
            }
            out.push('{');
            for (index, key) in keys.into_iter().enumerate() {
                if index > 0 {
                    out.push_str(", ");
                }
                write_py_string(out, key);
                out.push_str(": ");
                write_value(out, &map[key]);
            }
            out.push('}');
        }
        scalar => write_value(out, scalar),
    }
}

/// The top-level keys of a JSON object, in **document** order.
///
/// `json.loads` gives CPython a dict in the order the document lists them, and
/// two writers in the graph re-serialize a loaded dict without `sort_keys`. The
/// order therefore survives a round trip in Python and would not survive one
/// through `serde_json::Map`, which is a `BTreeMap`. Serde itself visits a map
/// in document order, so this is a visitor rather than a hand-rolled scanner.
pub fn object_key_order(raw: &str) -> Vec<String> {
    struct KeyOrder(Vec<String>);

    impl<'de> serde::de::Visitor<'de> for KeyOrder {
        type Value = Vec<String>;

        fn expecting(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
            formatter.write_str("a JSON object")
        }

        fn visit_map<A: serde::de::MapAccess<'de>>(
            mut self,
            mut access: A,
        ) -> Result<Self::Value, A::Error> {
            while let Some(key) = access.next_key::<String>()? {
                let _: serde::de::IgnoredAny = access.next_value()?;
                self.0.push(key);
            }
            Ok(self.0)
        }
    }

    let mut deserializer = serde_json::Deserializer::from_str(raw);
    serde::Deserializer::deserialize_map(&mut deserializer, KeyOrder(Vec::new()))
        .unwrap_or_default()
}

/// `lattice_brain.graph.json_utils._json` — `_json(data or {})`.
///
/// An absent map and an empty one are the same `"{}"` in Python, which is why
/// the write door takes a `Map` rather than an `Option<Map>`.
pub fn json_of(map: &Map<String, Value>) -> String {
    py_dumps(&Value::Object(map.clone()))
}

fn write_value(out: &mut String, value: &Value) {
    match value {
        Value::Null => out.push_str("null"),
        Value::Bool(true) => out.push_str("true"),
        Value::Bool(false) => out.push_str("false"),
        Value::Number(number) => out.push_str(&number_repr(number)),
        Value::String(text) => write_py_string(out, text),
        Value::Array(items) => {
            out.push('[');
            for (index, item) in items.iter().enumerate() {
                if index > 0 {
                    out.push_str(", ");
                }
                write_value(out, item);
            }
            out.push(']');
        }
        Value::Object(map) => {
            out.push('{');
            for (index, key) in sorted_keys(map).into_iter().enumerate() {
                if index > 0 {
                    out.push_str(", ");
                }
                write_py_string(out, key);
                out.push_str(": ");
                write_value(out, &map[key]);
            }
            out.push('}');
        }
    }
}

/// An object's keys in code-point order — CPython's `sort_keys=True`.
///
/// Not `map.keys()`: whether that is already sorted depends on the
/// `serde_json/preserve_order` feature, which a *different* crate in this
/// workspace turns on (see [`py_dumps`]).
fn sorted_keys(map: &Map<String, Value>) -> Vec<&String> {
    let mut keys: Vec<&String> = map.keys().collect();
    keys.sort();
    keys
}

fn number_repr(number: &serde_json::Number) -> String {
    match number.as_f64() {
        // `serde_json` keeps integers and floats apart, and so does CPython:
        // `json.dumps(1)` is `1` while `json.dumps(1.0)` is `1.0`.
        Some(value) if !number.is_i64() && !number.is_u64() => py_float_repr(value),
        _ => number.to_string(),
    }
}

/// `str(float)` — CPython's shortest round-tripping repr, with the `.0`.
///
/// Rust's `{}` already emits the shortest round-tripping form, but it drops the
/// fractional part of an integral float; CPython never does.
pub fn py_float_repr(value: f64) -> String {
    if value.is_nan() {
        return "NaN".into();
    }
    if value.is_infinite() {
        return if value > 0.0 {
            "Infinity".into()
        } else {
            "-Infinity".into()
        };
    }
    let rendered = format!("{value}");
    if rendered.contains(['.', 'e', 'E']) {
        rendered
    } else {
        format!("{rendered}.0")
    }
}

/// `ensure_ascii=False` escaping: only what JSON itself requires.
fn write_py_string(out: &mut String, text: &str) {
    out.push('"');
    for ch in text.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{08}' => out.push_str("\\b"),
            '\u{0c}' => out.push_str("\\f"),
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
}

/// `hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()`.
///
/// A Rust `&str` is already valid UTF-8, so `errors="replace"` never fires.
pub fn sha256_text(text: &str) -> String {
    format!("{:x}", Sha256::digest(text.as_bytes()))
}

/// `hashlib.sha256(data).hexdigest()`.
pub fn sha256_bytes(data: &[u8]) -> String {
    format!("{:x}", Sha256::digest(data))
}

/// `_kg_fsutil._slug` — the id-safe form of a label.
///
/// `[^0-9a-zA-Z가-힣._:@/-]+ → "-"`, then `strip("-")`, then `[:max_len]`
/// **characters**. `"untitled"` when nothing survives.
pub fn slug(text: &str, max_len: usize) -> String {
    // `re.sub(r"\s+", " ", …).strip().lower()`
    let collapsed = crate::pytext::clean_text(text).to_lowercase();
    let mut replaced = String::with_capacity(collapsed.len());
    let mut in_run = false;
    for ch in collapsed.chars() {
        if is_slug_char(ch) {
            in_run = false;
            replaced.push(ch);
        } else if !in_run {
            in_run = true;
            replaced.push('-');
        }
    }
    let stripped = replaced.trim_matches('-');
    let value = if stripped.is_empty() {
        "untitled"
    } else {
        stripped
    };
    crate::pytext::truncate_chars(value, max_len)
}

fn is_slug_char(ch: char) -> bool {
    ch.is_ascii_digit()
        || ch.is_ascii_alphabetic()
        || ('\u{AC00}'..='\u{D7A3}').contains(&ch)
        || matches!(ch, '.' | '_' | ':' | '@' | '/' | '-')
}

/// `_scoped_slug_id` — legacy ids stay as they were; scoped ones get a prefix.
pub fn scoped_slug_id(prefix: &str, value: &str, workspace_id: Option<&str>) -> String {
    let slugged = slug(value, 96);
    match workspace_id.filter(|w| !w.is_empty()) {
        None => format!("{prefix}:{slugged}"),
        Some(workspace) => {
            let scope = &sha256_text(workspace)[..12];
            format!("{prefix}:{scope}:{slugged}")
        }
    }
}

/// `_scoped_hash_id` — content identity, workspace-isolated when scoped.
pub fn scoped_hash_id(prefix: &str, value: &str, workspace_id: Option<&str>) -> String {
    let identity = match workspace_id.filter(|w| !w.is_empty()) {
        Some(workspace) => format!("{workspace}|{value}"),
        None => value.to_string(),
    };
    format!("{prefix}:{}", &sha256_text(&identity)[..24])
}

/// Python truthiness for a JSON value, as `if value:` applies it.
pub fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(flag) => *flag,
        Value::Number(number) => number.as_f64().map(|v| v != 0.0).unwrap_or(true),
        Value::String(text) => !text.is_empty(),
        Value::Array(items) => !items.is_empty(),
        Value::Object(map) => !map.is_empty(),
    }
}

/// `str(value)` for the JSON scalars the metadata keys actually carry.
pub fn py_str(value: &Value) -> String {
    match value {
        Value::Null => "None".into(),
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        Value::Number(number) => number_repr(number),
        Value::String(text) => text.clone(),
        other => py_dumps(other),
    }
}

/// `repr(some_str)` for the two sensitivity messages that interpolate `{x!r}`.
///
/// Narrow on purpose: CPython prefers single quotes and only switches to double
/// quotes when the value itself contains a single quote and no double quote.
pub fn py_repr_str(text: &str) -> String {
    let has_single = text.contains('\'');
    let has_double = text.contains('"');
    let (quote, escape_quote) = if has_single && !has_double {
        ('"', false)
    } else {
        ('\'', has_single)
    };
    let mut out = String::with_capacity(text.len() + 2);
    out.push(quote);
    for ch in text.chars() {
        match ch {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\'' if escape_quote => out.push_str("\\'"),
            c => out.push(c),
        }
    }
    out.push(quote);
    out
}

// ── never-leaves stamping (port of `lattice_brain/sensitivity.py`) ───────────

/// Path fragments checked case-insensitively against the POSIX form.
pub const SENSITIVE_PATH_FRAGMENTS: [&str; 15] = [
    "/.ssh/",
    "/.gnupg/",
    "/.aws/",
    "/.kube/",
    "/.docker/config.json",
    "/.netrc",
    "/.npmrc",
    "/.pypirc",
    "/.git-credentials",
    "id_rsa",
    "id_ed25519",
    ".pem",
    ".p12",
    ".pfx",
    ".keystore",
];

/// Exact filenames that are secret-bearing by convention.
pub const SENSITIVE_FILENAMES: [&str; 8] = [
    ".env",
    ".env.local",
    ".env.production",
    ".env.development",
    "credentials",
    "secrets.yaml",
    "secrets.yml",
    "secrets.json",
];

/// `sensitivity.LOCAL_ONLY_FLAG`.
pub const LOCAL_ONLY_FLAG: &str = "local_only";
/// `sensitivity.LOCAL_ONLY_REASON`.
pub const LOCAL_ONLY_REASON: &str = "local_only_reason";

/// `sensitivity.sensitive_reason_for_path`.
pub fn sensitive_reason_for_path(path: &str) -> Option<String> {
    if path.is_empty() {
        return None;
    }
    let lowered = path.replace('\\', "/").to_lowercase();
    let name = lowered.rsplit('/').next().unwrap_or(&lowered);
    if SENSITIVE_FILENAMES.contains(&name) {
        return Some(format!(
            "{} is a secret-bearing filename",
            py_repr_str(name)
        ));
    }
    for fragment in SENSITIVE_PATH_FRAGMENTS {
        if lowered.contains(fragment) {
            return Some(format!("path contains {}", py_repr_str(fragment)));
        }
    }
    None
}

/// `sensitivity.stamp_sensitivity` — never clears an existing flag.
pub fn stamp_sensitivity(metadata: &mut Map<String, Value>, path: &str) -> Option<String> {
    let reason = sensitive_reason_for_path(path)?;
    metadata.insert(LOCAL_ONLY_FLAG.into(), Value::Bool(true));
    metadata
        .entry(LOCAL_ONLY_REASON.to_string())
        .or_insert_with(|| Value::String(reason.clone()));
    Some(reason)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn dumps_uses_cpythons_default_separators() {
        let value = json!({"b": 1, "a": [1, 2], "c": {"d": "결정"}});
        assert_eq!(
            py_dumps(&value),
            "{\"a\": [1, 2], \"b\": 1, \"c\": {\"d\": \"결정\"}}"
        );
    }

    #[test]
    fn dumps_keeps_python_float_and_int_apart() {
        assert_eq!(py_dumps(&json!(1)), "1");
        assert_eq!(py_dumps(&json!(1.0)), "1.0");
        assert_eq!(py_dumps(&json!(0.75)), "0.75");
    }

    #[test]
    fn dumps_escapes_only_what_json_requires() {
        assert_eq!(py_dumps(&json!("a\nb\t\"c\"")), "\"a\\nb\\t\\\"c\\\"\"");
        assert_eq!(py_dumps(&json!("\u{1}")), "\"\\u0001\"");
        assert_eq!(py_dumps(&json!("한글")), "\"한글\"");
    }

    #[test]
    fn slug_keeps_hangul_and_collapses_the_rest() {
        assert_eq!(slug("Rust Write Engine", 96), "rust-write-engine");
        assert_eq!(slug("검색 랭킹!!", 96), "검색-랭킹");
        assert_eq!(slug("***", 96), "untitled");
        assert_eq!(slug("a.b_c:d@e/f-g", 96), "a.b_c:d@e/f-g");
        assert_eq!(slug("가나다라마바사", 3), "가나다");
    }

    #[test]
    fn scoped_ids_leave_unscoped_identities_untouched() {
        assert_eq!(
            scoped_slug_id("conversation", "conv-1", None),
            "conversation:conv-1"
        );
        let scoped = scoped_slug_id("conversation", "conv-1", Some("ws-alpha"));
        assert!(scoped.starts_with("conversation:"));
        assert_ne!(scoped, "conversation:conv-1");
        assert_eq!(scoped.split(':').count(), 3);
    }

    #[test]
    fn float_repr_matches_str_float() {
        assert_eq!(py_float_repr(1785974400.0), "1785974400.0");
        assert_eq!(py_float_repr(1785974400.5), "1785974400.5");
        assert_eq!(py_float_repr(0.0), "0.0");
    }

    #[test]
    fn sensitivity_names_the_rule_it_matched() {
        assert_eq!(
            sensitive_reason_for_path("/home/me/project/.env"),
            Some("'.env' is a secret-bearing filename".into())
        );
        assert_eq!(
            sensitive_reason_for_path("/home/me/.ssh/config"),
            Some("path contains '/.ssh/'".into())
        );
        assert_eq!(sensitive_reason_for_path("/home/me/notes.md"), None);
    }

    #[test]
    fn stamping_never_overwrites_an_existing_reason() {
        let mut metadata = Map::new();
        metadata.insert(LOCAL_ONLY_REASON.into(), json!("set by the user"));
        stamp_sensitivity(&mut metadata, "/home/me/.aws/credentials");
        assert_eq!(metadata[LOCAL_ONLY_FLAG], json!(true));
        assert_eq!(metadata[LOCAL_ONLY_REASON], json!("set by the user"));
    }
}
