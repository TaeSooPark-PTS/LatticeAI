use axum::http::StatusCode;
use axum::response::Response;
use serde_json::{json, Map, Value};

use crate::pyjson::OrderedMap;
use crate::response::json_response;

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

#[derive(Debug, Clone, Copy)]
pub struct Field {
    pub name: &'static str,
    pub required: bool,
}

pub const fn required(name: &'static str) -> Field {
    Field {
        name,
        required: true,
    }
}

pub const fn optional(name: &'static str) -> Field {
    Field {
        name,
        required: false,
    }
}

#[derive(Debug, Clone, Default)]
pub struct Validated {
    values: Map<String, Value>,
}

impl Validated {
    pub fn str(&self, name: &str) -> &str {
        self.values.get(name).and_then(Value::as_str).unwrap_or("")
    }

    pub fn opt(&self, name: &str) -> Option<&str> {
        self.values.get(name).and_then(Value::as_str)
    }

    pub fn present(&self, name: &str) -> bool {
        self.values.contains_key(name)
    }
}

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
