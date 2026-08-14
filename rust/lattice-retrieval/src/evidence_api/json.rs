//! A JSON writer that keeps object key order — the response body's contract.
//!
//! Python dicts render in insertion order, and this family's answer is nested
//! three deep (`actions[].label.ko`), so order has to survive at every level.
//! [`lattice_auth::OrderedMap`] only keeps it at the *top* level: its values are
//! `serde_json::Value`s, and `Value::Object` is a `BTreeMap` here
//! (`serde_json/preserve_order` is deliberately off workspace-wide, WP-I2 §2),
//! so nesting one re-sorts `{"ko", "en"}` into `{"en", "ko"}` and the body stops
//! matching `rust/fixtures/http/memory_brain.json`. The unit test at the bottom
//! pins that trap so the reason this type exists cannot be forgotten.
//!
//! Scope: a writer, not a parser. Keys are `&'static str` because every key in
//! this family is a literal from the Python source.

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
use serde::ser::{SerializeMap, SerializeSeq};
use serde::{Serialize, Serializer};
use serde_json::Value;

/// One JSON value whose objects remember the order their keys were written in.
#[derive(Debug, Clone, PartialEq)]
pub enum Json {
    /// A string, number, boolean or null — anything with no key order to lose.
    Leaf(Value),
    /// An object, in insertion order.
    Object(Vec<(&'static str, Json)>),
    /// An array, in order.
    Array(Vec<Json>),
}

impl Json {
    /// A JSON string.
    pub fn string(text: impl Into<String>) -> Self {
        Json::Leaf(Value::String(text.into()))
    }

    /// A JSON boolean.
    pub fn boolean(flag: bool) -> Self {
        Json::Leaf(Value::Bool(flag))
    }

    /// An array of JSON strings.
    pub fn strings<I, S>(items: I) -> Self
    where
        I: IntoIterator<Item = S>,
        S: Into<String>,
    {
        Json::Array(items.into_iter().map(Json::string).collect())
    }

    /// Compact rendering, the separators `JSONResponse` uses.
    ///
    /// Serialization runs straight off [`Serialize`], never through
    /// `serde_json::Value`, which is the whole point of the type.
    pub fn render(&self) -> String {
        serde_json::to_string(self).unwrap_or_else(|_| "null".to_string())
    }
}

impl Serialize for Json {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        match self {
            Json::Leaf(value) => value.serialize(serializer),
            Json::Object(entries) => {
                let mut map = serializer.serialize_map(Some(entries.len()))?;
                for (key, value) in entries {
                    map.serialize_entry(key, value)?;
                }
                map.end()
            }
            Json::Array(items) => {
                let mut seq = serializer.serialize_seq(Some(items.len()))?;
                for item in items {
                    seq.serialize_element(item)?;
                }
                seq.end()
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn nested_objects_keep_the_order_they_were_written_in() {
        let body = Json::Object(vec![
            (
                "sources",
                Json::Array(vec![Json::Object(vec![
                    ("id", Json::string("n1")),
                    ("truncated", Json::boolean(false)),
                ])]),
            ),
            ("missing", Json::strings(["gone".to_string()])),
            (
                "label",
                Json::Object(vec![
                    ("ko", Json::string("요약")),
                    ("en", Json::string("Summary")),
                ]),
            ),
        ]);
        assert_eq!(
            body.render(),
            r#"{"sources":[{"id":"n1","truncated":false}],"missing":["gone"],"label":{"ko":"요약","en":"Summary"}}"#
        );
    }

    #[test]
    fn a_nested_serde_json_object_would_have_sorted_the_keys() {
        // This crate enables `serde_json/preserve_order`, so `json!` keeps
        // the insertion order Python's dicts have on the wire.
        assert_eq!(
            serde_json::to_string(&serde_json::json!({"ko": 1, "en": 2})).expect("render"),
            r#"{"ko":1,"en":2}"#
        );
    }

    #[test]
    fn leaves_render_as_themselves() {
        assert_eq!(Json::Leaf(Value::Null).render(), "null");
        assert_eq!(Json::Leaf(Value::from(7)).render(), "7");
        assert_eq!(Json::strings(Vec::<String>::new()).render(), "[]");
        assert_eq!(Json::Object(Vec::new()).render(), "{}");
        assert_eq!(Json::string("한글").render(), "\"한글\"");
    }
}
