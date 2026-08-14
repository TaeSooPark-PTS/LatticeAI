//! Request-body validation with FastAPI's 422 shape.
//!
//! Every model in `api/auth.py` is a flat pydantic model of `str` and
//! `Optional[str]` fields, so one validator covers all four. What has to match
//! is not the check but the *report*: FastAPI answers
//! `{"detail": [{"type", "loc", "msg", "input"}, …]}` with one entry per bad
//! field, in **field-declaration order**, and the SPA's `friendlyError`
//! (`frontend/src/api/base.ts`) reads that shape.
//!
//! Validation runs before the handler, which is why a malformed login body is
//! a 422 and never reaches the rate limiter.
//!
//! Not reproduced: the `json_invalid` entry's `ctx.error` text, which is
//! CPython's own JSON decoder message and changes between releases. A body
//! that is not JSON at all gets the same `type`/`loc`/`msg` with this crate's
//! decoder message in `ctx.error`.

use axum::http::StatusCode;
use axum::response::Response;
use serde_json::{json, Map, Value};

use crate::pyjson::OrderedMap;
use crate::response::json_response;

/// One pydantic error entry, in the key order FastAPI emits.
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

/// One field of a flat pydantic model.
#[derive(Debug, Clone, Copy)]
pub struct Field {
    /// The field name, which is also its `loc` tail.
    pub name: &'static str,
    /// Whether the model declares it without a default.
    pub required: bool,
}

/// A required `str` field.
pub const fn required(name: &'static str) -> Field {
    Field {
        name,
        required: true,
    }
}

/// An `Optional[str] = None` field.
pub const fn optional(name: &'static str) -> Field {
    Field {
        name,
        required: false,
    }
}

/// A validated flat model: present string fields, by name.
#[derive(Debug, Clone, Default)]
pub struct Validated {
    values: Map<String, Value>,
}

impl Validated {
    /// The field's value, or `""` — the reading a required field always has.
    pub fn str(&self, name: &str) -> &str {
        self.values.get(name).and_then(Value::as_str).unwrap_or("")
    }

    /// The field's value when the caller sent one that was not `null`.
    pub fn opt(&self, name: &str) -> Option<&str> {
        self.values.get(name).and_then(Value::as_str)
    }

    /// Whether the caller sent this key at all (`null` counts as sent).
    pub fn present(&self, name: &str) -> bool {
        self.values.contains_key(name)
    }
}

/// Parse and validate one request body against `fields`.
pub fn parse_model(bytes: &[u8], fields: &[Field]) -> Result<Validated, Response> {
    let parsed: Value = match serde_json::from_slice(bytes) {
        Ok(value) => value,
        Err(error) => {
            return Err(errors(&[problem(
                "json_invalid",
                json!(["body", 0]),
                "JSON decode error",
                json!({}),
                Some(json!({ "error": error.to_string() })),
            )]))
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
    for field in fields {
        match object.get(field.name) {
            None => {
                if field.required {
                    problems.push(problem(
                        "missing",
                        json!(["body", field.name]),
                        "Field required",
                        parsed.clone(),
                        None,
                    ));
                }
            }
            Some(Value::Null) if !field.required => {
                // `Optional[str] = None`: an explicit null is the default.
                values.insert(field.name.to_string(), Value::Null);
            }
            Some(Value::String(text)) => {
                values.insert(field.name.to_string(), json!(text));
            }
            Some(other) => problems.push(problem(
                "string_type",
                json!(["body", field.name]),
                "Input should be a valid string",
                other.clone(),
                None,
            )),
        }
    }
    if problems.is_empty() {
        Ok(Validated { values })
    } else {
        Err(errors(&problems))
    }
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

    const LOGIN: &[Field] = &[required("email"), required("password")];
    const PROFILE: &[Field] = &[optional("name"), optional("nickname")];

    fn body_of(response: Response) -> String {
        let (_, body) = response.into_parts();
        let bytes = futures_lite_block_on(body);
        String::from_utf8(bytes).unwrap()
    }

    /// A tiny blocking body reader so this module needs no async runtime.
    fn futures_lite_block_on(body: axum::body::Body) -> Vec<u8> {
        tokio::runtime::Builder::new_current_thread()
            .build()
            .unwrap()
            .block_on(async move { axum::body::to_bytes(body, 65_536).await.unwrap().to_vec() })
    }

    #[test]
    fn a_complete_body_validates() {
        let parsed = parse_model(br#"{"email":"a@b.com","password":"x"}"#, LOGIN).unwrap();
        assert_eq!(parsed.str("email"), "a@b.com");
        assert_eq!(parsed.str("password"), "x");
        assert_eq!(parsed.str("absent"), "");
    }

    #[test]
    fn missing_fields_report_in_declaration_order() {
        let refusal = parse_model(b"{}", LOGIN).unwrap_err();
        assert_eq!(refusal.status(), StatusCode::UNPROCESSABLE_ENTITY);
        assert_eq!(
            body_of(refusal),
            r#"{"detail":[{"type":"missing","loc":["body","email"],"msg":"Field required","input":{}},{"type":"missing","loc":["body","password"],"msg":"Field required","input":{}}]}"#
        );
    }

    #[test]
    fn wrong_types_report_as_string_type() {
        let refusal = parse_model(br#"{"email":1,"password":true}"#, LOGIN).unwrap_err();
        let body = body_of(refusal);
        assert!(body.contains(r#""type":"string_type""#));
        assert!(body.contains(r#""input":1"#));
        assert!(body.contains(r#""input":true"#));
    }

    #[test]
    fn a_non_object_body_reports_the_model_type() {
        let refusal = parse_model(b"[]", LOGIN).unwrap_err();
        assert!(body_of(refusal).contains("model_attributes_type"));
    }

    #[test]
    fn undecodable_bytes_report_a_json_error() {
        let refusal = parse_model(b"not json", LOGIN).unwrap_err();
        assert!(body_of(refusal).contains("json_invalid"));
    }

    #[test]
    fn optional_fields_accept_absence_and_null() {
        let parsed = parse_model(b"{}", PROFILE).unwrap();
        assert!(!parsed.present("name"));
        assert_eq!(parsed.opt("name"), None);

        let parsed = parse_model(br#"{"name":null,"nickname":"  "}"#, PROFILE).unwrap();
        assert!(parsed.present("name"));
        assert_eq!(parsed.opt("name"), None);
        assert_eq!(parsed.opt("nickname"), Some("  "));
    }

    #[test]
    fn an_optional_field_still_has_to_be_a_string() {
        let refusal = parse_model(br#"{"name":7}"#, PROFILE).unwrap_err();
        assert!(body_of(refusal).contains("string_type"));
    }
}
