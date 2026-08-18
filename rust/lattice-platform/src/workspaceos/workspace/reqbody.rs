//! Request bodies for this family's fourteen pydantic models.
//!
//! `lattice_auth::body::parse_model` covers a flat model of strings, which is
//! every model in `api/auth.py`. The workspace models are not flat: they carry
//! `Dict`, `List[Dict]`, `bool` and `int` fields with defaults, so they need a
//! validator that knows those shapes. The *report* is the same contract —
//! FastAPI's `{"detail": [{"type", "loc", "msg", "input"}, …]}`, one entry per
//! bad field in **field-declaration order** — because `friendlyError` in
//! `frontend/src/api/base.ts` reads it.
//!
//! What is pinned by fixtures is the `missing` entry, and it is reproduced
//! exactly (including `input` being the whole body). The wrong-*type* entries
//! use pydantic v2's type names (`string_type`, `dict_type`, `list_type`,
//! `bool_type`, `int_type`) and its messages; no fixture records one, so they
//! are stated as a best-faith match rather than a proven one.

use axum::http::StatusCode;
use axum::response::Response;
use lattice_auth::response::json_response;
use lattice_auth::OrderedMap;
use serde_json::{json, Map, Value};

/// What a model field accepts, and what it falls back to.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    /// `str` — required, no default.
    RequiredStr,
    /// `str = "<default>"`.
    StrDefault(&'static str),
    /// `Optional[str] = None`.
    OptionalStr,
    /// `Dict = {}`.
    Dict,
    /// `Optional[Dict] = None`.
    OptionalDict,
    /// `List[...] = []`.
    List,
    /// `bool = <default>`.
    Bool(bool),
    /// `Optional[bool] = None`.
    OptionalBool,
    /// `int = <default>`.
    Int(i64),
}

/// One declared field of a model.
#[derive(Debug, Clone, Copy)]
pub struct Field {
    /// The field name, which is also its `loc` tail.
    pub name: &'static str,
    /// What it accepts.
    pub kind: Kind,
}

/// Declare one field.
pub const fn field(name: &'static str, kind: Kind) -> Field {
    Field { name, kind }
}

/// A validated body: every declared field, defaulted where absent.
#[derive(Debug, Clone, Default)]
pub struct Body {
    values: Map<String, Value>,
    sent: Vec<String>,
}

impl Body {
    /// A string field. Absent/`null` reads as its default (`""` for optionals).
    pub fn str(&self, name: &str) -> &str {
        self.values.get(name).and_then(Value::as_str).unwrap_or("")
    }

    /// An optional string: `None` when the caller sent nothing or `null`.
    pub fn opt_str(&self, name: &str) -> Option<&str> {
        self.values
            .get(name)
            .and_then(Value::as_str)
            .filter(|text| !text.is_empty() || self.sent.iter().any(|key| key == name))
    }

    /// The raw value of a field, defaulted.
    pub fn value(&self, name: &str) -> Value {
        self.values.get(name).cloned().unwrap_or(Value::Null)
    }

    /// A `Dict` field.
    pub fn dict(&self, name: &str) -> Value {
        match self.values.get(name) {
            Some(Value::Object(map)) => Value::Object(map.clone()),
            _ => Value::Object(Map::new()),
        }
    }

    /// A `List` field.
    pub fn list(&self, name: &str) -> Vec<Value> {
        match self.values.get(name) {
            Some(Value::Array(items)) => items.clone(),
            _ => Vec::new(),
        }
    }

    /// A `bool` field.
    pub fn bool(&self, name: &str) -> bool {
        self.values
            .get(name)
            .and_then(Value::as_bool)
            .unwrap_or(false)
    }

    /// An `int` field.
    pub fn int(&self, name: &str) -> i64 {
        self.values.get(name).and_then(Value::as_i64).unwrap_or(0)
    }

    /// Whether the caller sent this key at all (an explicit `null` counts).
    pub fn present(&self, name: &str) -> bool {
        self.sent.iter().any(|key| key == name)
    }
}

/// Parse and validate one request body against a model's fields.
pub fn parse(bytes: &[u8], fields: &[Field]) -> Result<Body, Response> {
    // An empty body is `{}` to FastAPI only when every field has a default;
    // otherwise it reports the missing ones, which is what this does anyway.
    let parsed: Value = if bytes.is_empty() {
        json!({})
    } else {
        match serde_json::from_slice(bytes) {
            Ok(value) => value,
            Err(error) => {
                return Err(errors(&[problem(
                    "json_invalid",
                    json!(["body", 0]),
                    "JSON decode error",
                    json!({}),
                    Some(json!({"error": error.to_string()})),
                )]))
            }
        }
    };
    let Some(object) = parsed.as_object() else {
        return Err(errors(&[problem(
            "model_attributes_type",
            json!(["body"]),
            "Input should be a valid dictionary or object to extract fields from",
            parsed,
            None,
        )]));
    };

    let mut problems: Vec<OrderedMap> = Vec::new();
    let mut values = Map::new();
    let mut sent = Vec::new();
    for Field { name, kind } in fields {
        let supplied = object.get(*name);
        if supplied.is_some() {
            sent.push((*name).to_string());
        }
        match coerce(*kind, supplied) {
            Ok(Some(value)) => {
                values.insert((*name).to_string(), value);
            }
            Ok(None) => {}
            Err((kind_name, message)) => problems.push(problem(
                kind_name,
                json!(["body", name]),
                message,
                if kind_name == "missing" {
                    parsed.clone()
                } else {
                    supplied.cloned().unwrap_or(Value::Null)
                },
                None,
            )),
        }
    }
    if problems.is_empty() {
        Ok(Body { values, sent })
    } else {
        Err(errors(&problems))
    }
}

type Refusal = (&'static str, &'static str);

fn coerce(kind: Kind, supplied: Option<&Value>) -> Result<Option<Value>, Refusal> {
    match (kind, supplied) {
        (Kind::RequiredStr, None) => Err(("missing", "Field required")),
        (Kind::RequiredStr, Some(Value::String(text))) => Ok(Some(json!(text))),
        (Kind::RequiredStr, Some(_)) => Err(("string_type", "Input should be a valid string")),

        (Kind::StrDefault(default), None) | (Kind::StrDefault(default), Some(Value::Null)) => {
            Ok(Some(json!(default)))
        }
        (Kind::StrDefault(_), Some(Value::String(text))) => Ok(Some(json!(text))),
        (Kind::StrDefault(_), Some(_)) => Err(("string_type", "Input should be a valid string")),

        (Kind::OptionalStr, None) | (Kind::OptionalStr, Some(Value::Null)) => Ok(None),
        (Kind::OptionalStr, Some(Value::String(text))) => Ok(Some(json!(text))),
        (Kind::OptionalStr, Some(_)) => Err(("string_type", "Input should be a valid string")),

        (Kind::Dict, None) | (Kind::Dict, Some(Value::Null)) => Ok(Some(json!({}))),
        (Kind::Dict, Some(Value::Object(map))) => Ok(Some(Value::Object(map.clone()))),
        (Kind::Dict, Some(_)) => Err(("dict_type", "Input should be a valid dictionary")),

        (Kind::OptionalDict, None) | (Kind::OptionalDict, Some(Value::Null)) => Ok(None),
        (Kind::OptionalDict, Some(Value::Object(map))) => Ok(Some(Value::Object(map.clone()))),
        (Kind::OptionalDict, Some(_)) => Err(("dict_type", "Input should be a valid dictionary")),

        (Kind::List, None) | (Kind::List, Some(Value::Null)) => Ok(Some(json!([]))),
        (Kind::List, Some(Value::Array(items))) => Ok(Some(Value::Array(items.clone()))),
        (Kind::List, Some(_)) => Err(("list_type", "Input should be a valid list")),

        (Kind::Bool(default), None) | (Kind::Bool(default), Some(Value::Null)) => {
            Ok(Some(json!(default)))
        }
        (Kind::Bool(_), Some(Value::Bool(flag))) => Ok(Some(json!(flag))),
        (Kind::Bool(_), Some(_)) => Err(("bool_type", "Input should be a valid boolean")),

        (Kind::OptionalBool, None) | (Kind::OptionalBool, Some(Value::Null)) => Ok(None),
        (Kind::OptionalBool, Some(Value::Bool(flag))) => Ok(Some(json!(flag))),
        (Kind::OptionalBool, Some(_)) => Err(("bool_type", "Input should be a valid boolean")),

        (Kind::Int(default), None) | (Kind::Int(default), Some(Value::Null)) => {
            Ok(Some(json!(default)))
        }
        (Kind::Int(_), Some(Value::Number(number))) if number.is_i64() => {
            Ok(Some(json!(number.as_i64())))
        }
        (Kind::Int(_), Some(_)) => Err(("int_type", "Input should be a valid integer")),
    }
}

/// `{"detail": [{"type": "missing", "loc": ["query", name], …}]}`.
///
/// The one query-parameter model in this family (`q` on the memory search) is
/// declared without a default, so FastAPI reports it exactly like a body field
/// except that `loc` starts at `"query"` and `input` is `null`.
pub fn missing_query_parameter(name: &str) -> Response {
    errors(&[problem(
        "missing",
        json!(["query", name]),
        "Field required",
        Value::Null,
        None,
    )])
}

fn problem(kind: &str, loc: Value, msg: &str, input: Value, ctx: Option<Value>) -> OrderedMap {
    let mut entry = OrderedMap::new();
    entry.insert("type", json!(kind));
    entry.insert("loc", loc);
    entry.insert("msg", json!(msg));
    entry.insert("input", input);
    if let Some(ctx) = ctx {
        entry.insert("ctx", ctx);
    }
    entry
}

fn errors(problems: &[OrderedMap]) -> Response {
    let rendered: Vec<String> = problems
        .iter()
        .filter_map(|entry| serde_json::to_string(entry).ok())
        .collect();
    json_response(
        StatusCode::UNPROCESSABLE_ENTITY,
        &format!("{{\"detail\":[{}]}}", rendered.join(",")),
        None,
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    const MODEL: &[Field] = &[
        field("step", Kind::RequiredStr),
        field("status", Kind::StrDefault("complete")),
        field("data", Kind::Dict),
        field("error", Kind::StrDefault("")),
        field("note", Kind::OptionalStr),
        field("steps", Kind::List),
        field("enabled", Kind::Bool(false)),
        field("expires_hours", Kind::Int(168)),
        field("settings", Kind::OptionalDict),
        field("flag", Kind::OptionalBool),
    ];

    async fn body_of(response: Response) -> (u16, String) {
        let status = response.status().as_u16();
        let bytes = axum::body::to_bytes(response.into_body(), 65_536)
            .await
            .expect("body");
        (status, String::from_utf8(bytes.to_vec()).expect("utf-8"))
    }

    #[tokio::test]
    async fn a_missing_required_field_reports_the_whole_body_as_input() {
        let refusal = parse(br#"{"status": "complete"}"#, MODEL).unwrap_err();
        let (status, body) = body_of(refusal).await;
        assert_eq!(status, 422);
        assert_eq!(
            body,
            r#"{"detail":[{"type":"missing","loc":["body","step"],"msg":"Field required","input":{"status":"complete"}}]}"#
        );
    }

    #[test]
    fn defaults_fill_in_and_sent_keys_are_remembered() {
        let parsed = parse(br#"{"step": "account"}"#, MODEL).unwrap();
        assert_eq!(parsed.str("step"), "account");
        assert_eq!(parsed.str("status"), "complete");
        assert_eq!(parsed.str("error"), "");
        assert_eq!(parsed.dict("data"), json!({}));
        assert!(parsed.list("steps").is_empty());
        assert!(!parsed.bool("enabled"));
        assert_eq!(parsed.int("expires_hours"), 168);
        assert_eq!(parsed.opt_str("note"), None);
        assert_eq!(parsed.value("settings"), Value::Null);
        assert!(parsed.present("step"));
        assert!(!parsed.present("status"));
    }

    #[test]
    fn supplied_values_win_over_defaults() {
        let parsed = parse(
            br#"{"step":"admin","status":"failed","data":{"a":1},"steps":[{"b":2}],
                 "enabled":true,"expires_hours":24,"settings":{"tier":"pro"},"flag":false,
                 "note":"hi"}"#,
            MODEL,
        )
        .unwrap();
        assert_eq!(parsed.str("status"), "failed");
        assert_eq!(parsed.dict("data"), json!({"a": 1}));
        assert_eq!(parsed.list("steps"), vec![json!({"b": 2})]);
        assert!(parsed.bool("enabled"));
        assert_eq!(parsed.int("expires_hours"), 24);
        assert_eq!(parsed.value("settings"), json!({"tier": "pro"}));
        assert_eq!(parsed.value("flag"), json!(false));
        assert_eq!(parsed.opt_str("note"), Some("hi"));
    }

    #[tokio::test]
    async fn an_explicit_null_reads_as_the_default_for_a_defaulted_field() {
        let parsed = parse(
            br#"{"step":"account","status":null,"data":null,"steps":null,
                 "enabled":null,"expires_hours":null,"settings":null,"flag":null,"note":null}"#,
            MODEL,
        )
        .unwrap();
        assert_eq!(parsed.str("status"), "complete");
        assert_eq!(parsed.dict("data"), json!({}));
        assert_eq!(parsed.int("expires_hours"), 168);
        assert_eq!(parsed.value("settings"), Value::Null);
        assert!(parsed.present("settings"));
        let _ = body_of(parse(b"", MODEL).unwrap_err()).await;
    }

    #[tokio::test]
    async fn wrong_types_report_one_entry_each_in_declaration_order() {
        let refusal = parse(
            br#"{"step":1,"data":[],"steps":{},"enabled":"yes","expires_hours":"x"}"#,
            MODEL,
        )
        .unwrap_err();
        let (status, body) = body_of(refusal).await;
        assert_eq!(status, 422);
        let parsed: Value = serde_json::from_str(&body).unwrap();
        let kinds: Vec<&str> = parsed["detail"]
            .as_array()
            .unwrap()
            .iter()
            .map(|entry| entry["type"].as_str().unwrap())
            .collect();
        assert_eq!(
            kinds,
            vec![
                "string_type",
                "dict_type",
                "list_type",
                "bool_type",
                "int_type"
            ]
        );
        assert_eq!(parsed["detail"][0]["loc"], json!(["body", "step"]));
        assert_eq!(parsed["detail"][0]["input"], json!(1));
    }

    #[tokio::test]
    async fn a_body_that_is_not_json_or_not_an_object_is_reported_the_way_fastapi_does() {
        let (status, body) = body_of(parse(b"{not json", MODEL).unwrap_err()).await;
        assert_eq!(status, 422);
        assert!(body.contains("\"json_invalid\""));
        assert!(body.contains("\"loc\":[\"body\",0]"));

        let (_, body) = body_of(parse(b"[1,2]", MODEL).unwrap_err()).await;
        assert!(body.contains("model_attributes_type"));
    }

    #[tokio::test]
    async fn a_missing_query_parameter_reads_at_the_query_location() {
        let (status, body) = body_of(missing_query_parameter("q")).await;
        assert_eq!(status, 422);
        assert_eq!(
            body,
            r#"{"detail":[{"type":"missing","loc":["query","q"],"msg":"Field required","input":null}]}"#
        );
    }
}
