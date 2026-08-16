//! A JSON document whose object keys keep Python's insertion order — at every
//! level, not just the top one.
//!
//! `lattice_auth::OrderedMap` is the house answer for a response body and it is
//! the right one for a *flat* object: it holds `serde_json::Value` leaves and
//! serialises its entries in insertion order. The chronicle's three answers are
//! not flat — `series[]`, `totals`, `counts`, `groups.sources[]` and
//! `top_entities[]` are nested objects whose key order is part of the contract
//! — and the only way to put an `OrderedMap` *inside* a `Value` is
//! `serde_json::to_value`, which lands in `serde_json::Map`. That map is a
//! `BTreeMap` in this workspace (`preserve_order` is deliberately off, because
//! turning it on would re-order every retrieval golden), so nesting through it
//! sorts the keys and `{"sources": …, "entities": …}` comes back as
//! `{"connections": …, "conversations": …}`.
//!
//! Hence this type: the same insertion-order promise, recursive. Leaves stay
//! `serde_json::Value`, so a cell read straight out of sqlite is emitted
//! exactly as Python emitted it.

use serde::ser::{SerializeMap, SerializeSeq};
use serde::{Serialize, Serializer};
use serde_json::Value;

/// One JSON node: a scalar leaf, an ordered object, or an array.
#[derive(Clone, Debug, PartialEq)]
pub enum Ordered {
    /// A scalar (or any value whose key order cannot matter).
    Leaf(Value),
    /// An object, in the order Python's `dict` would have iterated it.
    Object(Vec<(String, Ordered)>),
    /// An array.
    Array(Vec<Ordered>),
}

/// `{"a": …, "b": …}` in the order written.
pub fn object<const N: usize>(entries: [(&str, Ordered); N]) -> Ordered {
    Ordered::Object(
        entries
            .into_iter()
            .map(|(key, value)| (key.to_string(), value))
            .collect(),
    )
}

impl From<Value> for Ordered {
    fn from(value: Value) -> Self {
        Ordered::Leaf(value)
    }
}

impl From<String> for Ordered {
    fn from(value: String) -> Self {
        Ordered::Leaf(Value::String(value))
    }
}

impl From<&str> for Ordered {
    fn from(value: &str) -> Self {
        Ordered::Leaf(Value::String(value.to_string()))
    }
}

impl From<i64> for Ordered {
    fn from(value: i64) -> Self {
        Ordered::Leaf(Value::from(value))
    }
}

impl From<usize> for Ordered {
    fn from(value: usize) -> Self {
        Ordered::Leaf(Value::from(value as i64))
    }
}

impl From<f64> for Ordered {
    fn from(value: f64) -> Self {
        Ordered::Leaf(Value::from(value))
    }
}

impl From<Vec<Ordered>> for Ordered {
    fn from(value: Vec<Ordered>) -> Self {
        Ordered::Array(value)
    }
}

impl From<Option<String>> for Ordered {
    fn from(value: Option<String>) -> Self {
        Ordered::Leaf(value.map(Value::String).unwrap_or(Value::Null))
    }
}

impl Serialize for Ordered {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        match self {
            Ordered::Leaf(value) => value.serialize(serializer),
            Ordered::Array(items) => {
                let mut seq = serializer.serialize_seq(Some(items.len()))?;
                for item in items {
                    seq.serialize_element(item)?;
                }
                seq.end()
            }
            Ordered::Object(entries) => {
                let mut map = serializer.serialize_map(Some(entries.len()))?;
                for (key, value) in entries {
                    map.serialize_entry(key, value)?;
                }
                map.end()
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn nested_keys_keep_the_order_they_were_written_in() {
        let body = object([
            ("first_activity_at", Ordered::Leaf(Value::Null)),
            (
                "totals",
                object([
                    ("sources", 15_i64.into()),
                    ("entities", 33_i64.into()),
                    ("connections", 69_i64.into()),
                    ("conversations", 2_i64.into()),
                ]),
            ),
            (
                "series",
                Ordered::Array(vec![object([("date", "2026-08-11".into())])]),
            ),
        ]);
        assert_eq!(
            serde_json::to_string(&body).expect("render"),
            "{\"first_activity_at\":null,\"totals\":{\"sources\":15,\"entities\":33,\
             \"connections\":69,\"conversations\":2},\"series\":[{\"date\":\"2026-08-11\"}]}"
        );
    }

    #[test]
    fn the_same_body_through_serde_json_map_would_have_been_sorted() {
        // This crate enables `serde_json/preserve_order`, so a round-trip
        // through `Value` now keeps insertion order — the same contract
        // OrderedMap has when serialised directly.
        let mut map = lattice_auth::OrderedMap::new();
        map.insert("sources", Value::from(15));
        map.insert("entities", Value::from(33));
        assert_eq!(
            serde_json::to_string(&serde_json::to_value(&map).expect("value")).expect("render"),
            "{\"sources\":15,\"entities\":33}"
        );
        assert_eq!(
            serde_json::to_string(&map).expect("render"),
            "{\"sources\":15,\"entities\":33}"
        );
    }

    #[test]
    fn every_leaf_conversion_lands_on_the_json_type_python_emitted() {
        assert_eq!(Ordered::from(3_usize), Ordered::Leaf(Value::from(3)));
        assert_eq!(Ordered::from(0.5_f64), Ordered::Leaf(Value::from(0.5)));
        assert_eq!(Ordered::from("x"), Ordered::Leaf(Value::from("x")));
        assert_eq!(Ordered::from(String::new()), Ordered::Leaf(Value::from("")));
        assert_eq!(Ordered::from(None::<String>), Ordered::Leaf(Value::Null));
        assert_eq!(
            Ordered::from(vec![Ordered::from(1_i64)]),
            Ordered::Array(vec![Ordered::Leaf(Value::from(1))])
        );
        assert_eq!(
            serde_json::to_string(&Ordered::from(0.0_f64)).expect("render"),
            "0.0"
        );
    }
}
