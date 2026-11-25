use std::fmt;

use serde::de::{MapAccess, Visitor};
use serde::ser::SerializeMap;
use serde::{Deserialize, Deserializer, Serialize, Serializer};
use serde_json::Value;

#[derive(Debug, Clone, Default, PartialEq)]
pub struct OrderedMap {
    entries: Vec<(String, Value)>,
}

impl OrderedMap {
    pub fn new() -> Self {
        Self {
            entries: Vec::new(),
        }
    }

    pub fn get(&self, key: &str) -> Option<&Value> {
        self.entries
            .iter()
            .find(|(name, _)| name == key)
            .map(|(_, value)| value)
    }

    pub fn contains_key(&self, key: &str) -> bool {
        self.get(key).is_some()
    }

    pub fn insert(&mut self, key: impl Into<String>, value: Value) {
        let key = key.into();
        match self.entries.iter_mut().find(|(name, _)| *name == key) {
            Some(slot) => slot.1 = value,
            None => self.entries.push((key, value)),
        }
    }

    pub fn remove(&mut self, key: &str) -> Option<Value> {
        let index = self.entries.iter().position(|(name, _)| name == key)?;
        Some(self.entries.remove(index).1)
    }

    pub fn iter(&self) -> impl Iterator<Item = (&str, &Value)> {
        self.entries
            .iter()
            .map(|(key, value)| (key.as_str(), value))
    }

    pub fn iter_mut(&mut self) -> impl Iterator<Item = (&str, &mut Value)> {
        self.entries
            .iter_mut()
            .map(|(key, value)| (key.as_str(), value))
    }

    pub fn first_key(&self) -> Option<&str> {
        self.entries.first().map(|(key, _)| key.as_str())
    }

    pub fn len(&self) -> usize {
        self.entries.len()
    }

    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }
}

impl Serialize for OrderedMap {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        let mut map = serializer.serialize_map(Some(self.entries.len()))?;
        for (key, value) in &self.entries {
            map.serialize_entry(key, value)?;
        }
        map.end()
    }
}

impl<'de> Deserialize<'de> for OrderedMap {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        struct Ordered;

        impl<'de> Visitor<'de> for Ordered {
            type Value = OrderedMap;

            fn expecting(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
                formatter.write_str("a JSON object")
            }

            fn visit_map<A: MapAccess<'de>>(self, mut access: A) -> Result<Self::Value, A::Error> {
                let mut out = OrderedMap::new();
                while let Some((key, value)) = access.next_entry::<String, Value>()? {
                    out.insert(key, value);
                }
                Ok(out)
            }
        }

        deserializer.deserialize_map(Ordered)
    }
}

pub fn dumps_indent2<T: Serialize>(payload: &T) -> Result<String, serde_json::Error> {
    serde_json::to_string_pretty(payload)
}

pub fn dumps_spaced(entries: &[(&str, Value)]) -> String {
    let rendered: Vec<String> = entries
        .iter()
        .map(|(key, value)| {
            format!(
                "{}: {}",
                Value::String((*key).to_string()),
                serde_json::to_string(value).unwrap_or_else(|_| "null".into())
            )
        })
        .collect();
    format!("{{{}}}", rendered.join(", "))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn insertion_order_survives_a_round_trip() {
        let map: OrderedMap = serde_json::from_str(r#"{"z":1,"a":2,"m":3}"#).unwrap();
        assert_eq!(map.first_key(), Some("z"));
        assert_eq!(
            serde_json::to_string(&map).unwrap(),
            r#"{"z":1,"a":2,"m":3}"#
        );
    }

    #[test]
    fn replacing_a_key_keeps_its_position() {
        let mut map: OrderedMap = serde_json::from_str(r#"{"z":1,"a":2}"#).unwrap();
        map.insert("z", json!(9));
        assert_eq!(serde_json::to_string(&map).unwrap(), r#"{"z":9,"a":2}"#);
        assert_eq!(map.len(), 2);
        assert!(map.contains_key("a"));
        assert_eq!(map.remove("z"), Some(json!(9)));
        assert_eq!(map.first_key(), Some("a"));
    }

    #[test]
    fn empty_object_renders_like_python() {
        let map = OrderedMap::new();
        assert!(map.is_empty());
        assert_eq!(dumps_indent2(&map).unwrap(), "{}");
    }

    #[test]
    fn indent_two_matches_python() {
        let map: OrderedMap = serde_json::from_str(r#"{"a":{"b":"한글"}}"#).unwrap();
        assert_eq!(
            dumps_indent2(&map).unwrap(),
            "{\n  \"a\": {\n    \"b\": \"한글\"\n  }\n}"
        );
    }

    #[test]
    fn spaced_dump_matches_python_defaults() {
        let body = dumps_spaced(&[("detail", json!("x")), ("error", json!("y"))]);
        assert_eq!(body, r#"{"detail": "x", "error": "y"}"#);
    }

    #[test]
    fn iteration_exposes_entries_in_order() {
        let mut map: OrderedMap = serde_json::from_str(r#"{"a":1,"b":2}"#).unwrap();
        let keys: Vec<&str> = map.iter().map(|(key, _)| key).collect();
        assert_eq!(keys, vec!["a", "b"]);
        for (_, value) in map.iter_mut() {
            *value = json!(0);
        }
        assert_eq!(map.get("a"), Some(&json!(0)));
    }
}
