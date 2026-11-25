use serde::ser::{SerializeMap, SerializeSeq};
use serde::{Serialize, Serializer};
use serde_json::Value;

#[derive(Debug, Clone, PartialEq)]
pub enum Json {
    Leaf(Value),
    Object(Vec<(&'static str, Json)>),
    Array(Vec<Json>),
}

impl Json {
    pub fn string(text: impl Into<String>) -> Self {
        Json::Leaf(Value::String(text.into()))
    }

    pub fn boolean(flag: bool) -> Self {
        Json::Leaf(Value::Bool(flag))
    }

    pub fn strings<I, S>(items: I) -> Self
    where
        I: IntoIterator<Item = S>,
        S: Into<String>,
    {
        Json::Array(items.into_iter().map(Json::string).collect())
    }

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
