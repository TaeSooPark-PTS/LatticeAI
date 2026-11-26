use axum::http::{HeaderMap, StatusCode};
use axum::response::Response;
use lattice_auth::response::json_response;
use lattice_auth::OrderedMap;
use lattice_core::messages::{self, LANGUAGE_HEADER};
use lattice_core::CoreError;
use serde_json::{json, Map, Value};

use super::GRAPH_DISABLED_DETAIL;

pub fn ok(value: &Value) -> Response {
    let body = serde_json::to_string(value).unwrap_or_else(|_| "null".into());
    json_response(StatusCode::OK, &body, None)
}

pub fn detail(status: u16, text: &str) -> Response {
    let body = serde_json::to_string(&json!({ "detail": text })).unwrap_or_default();
    json_response(
        StatusCode::from_u16(status).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR),
        &body,
        None,
    )
}

pub fn http_error(status: u16, id: &str, lang: &str) -> Response {
    detail(status, &messages::text(id, lang, &[]))
}

pub fn http_error_with(status: u16, id: &str, lang: &str, args: &[(&str, &str)]) -> Response {
    detail(status, &messages::text(id, lang, args))
}

pub fn graph_disabled() -> Response {
    detail(404, GRAPH_DISABLED_DETAIL)
}

pub fn language(headers: &HeaderMap) -> &'static str {
    messages::resolve_language(
        headers
            .get(LANGUAGE_HEADER)
            .and_then(|value| value.to_str().ok()),
        headers
            .get(axum::http::header::ACCEPT_LANGUAGE)
            .and_then(|value| value.to_str().ok()),
    )
}

pub fn engine_error(error: CoreError) -> Response {
    match error {
        CoreError::InvalidRequest(message) => detail(404, &message),
        other => detail(500, &format!("Internal Server Error: {other}")),
    }
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

fn validation_error(problems: &[OrderedMap]) -> Response {
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

pub fn literal_error(source: &str, name: &str, expected: &str, input: &str) -> Response {
    validation_error(&[problem(
        "literal_error",
        json!([source, name]),
        &format!("Input should be {expected}"),
        json!(input),
        Some(json!({ "expected": expected })),
    )])
}

#[derive(Debug, Clone, Default)]
pub struct Query {
    pairs: Vec<(String, String)>,
}

impl Query {
    pub fn parse(raw: Option<&str>) -> Self {
        let mut pairs = Vec::new();
        for chunk in raw.unwrap_or_default().split('&') {
            if chunk.is_empty() {
                continue;
            }
            let (key, value) = match chunk.split_once('=') {
                Some((key, value)) => (key, value),
                None => (chunk, ""),
            };
            pairs.push((percent_decode(key), percent_decode(value)));
        }
        Self { pairs }
    }

    pub fn raw(&self, name: &str) -> Option<&str> {
        self.pairs
            .iter()
            .rev()
            .find(|(key, _)| key == name)
            .map(|(_, value)| value.as_str())
    }

    pub fn require_str(&self, name: &'static str) -> Result<String, Response> {
        match self.raw(name) {
            Some(value) => Ok(value.to_string()),
            None => Err(validation_error(&[problem(
                "missing",
                json!(["query", name]),
                "Field required",
                Value::Null,
                None,
            )])),
        }
    }

    pub fn str_or(&self, name: &str, fallback: &str) -> String {
        self.raw(name).unwrap_or(fallback).to_string()
    }

    pub fn int_or(&self, name: &'static str, fallback: i64) -> Result<i64, Response> {
        let Some(raw) = self.raw(name) else {
            return Ok(fallback);
        };
        raw.trim().parse::<i64>().map_err(|_| {
            validation_error(&[problem(
                "int_parsing",
                json!(["query", name]),
                "Input should be a valid integer, unable to parse string as an integer",
                json!(raw),
                None,
            )])
        })
    }

    pub fn float_or(&self, name: &'static str, fallback: f64) -> Result<f64, Response> {
        let Some(raw) = self.raw(name) else {
            return Ok(fallback);
        };
        raw.trim().parse::<f64>().map_err(|_| {
            validation_error(&[problem(
                "float_parsing",
                json!(["query", name]),
                "Input should be a valid number, unable to parse string as a number",
                json!(raw),
                None,
            )])
        })
    }

    pub fn bool_or(&self, name: &'static str, fallback: bool) -> Result<bool, Response> {
        let Some(raw) = self.raw(name) else {
            return Ok(fallback);
        };
        match raw.trim().to_ascii_lowercase().as_str() {
            "1" | "on" | "t" | "true" | "y" | "yes" => Ok(true),
            "0" | "off" | "f" | "false" | "n" | "no" => Ok(false),
            _ => Err(validation_error(&[problem(
                "bool_parsing",
                json!(["query", name]),
                "Input should be a valid boolean, unable to interpret input",
                json!(raw),
                None,
            )])),
        }
    }
}

fn percent_decode(raw: &str) -> String {
    let bytes = raw.as_bytes();
    let mut out: Vec<u8> = Vec::with_capacity(bytes.len());
    let mut index = 0;
    while index < bytes.len() {
        match bytes[index] {
            b'+' => {
                out.push(b' ');
                index += 1;
            }
            b'%' if index + 2 < bytes.len() => {
                let hex = std::str::from_utf8(&bytes[index + 1..index + 3]).unwrap_or("");
                match u8::from_str_radix(hex, 16) {
                    Ok(byte) => {
                        out.push(byte);
                        index += 3;
                    }
                    Err(_) => {
                        out.push(bytes[index]);
                        index += 1;
                    }
                }
            }
            byte => {
                out.push(byte);
                index += 1;
            }
        }
    }
    String::from_utf8_lossy(&out).into_owned()
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    Str(usize),
    OptStr,
    Int,
    Float,
    Bool,
    Object,
    Array,
}

#[derive(Debug, Clone, Copy)]
pub struct FieldSpec {
    pub name: &'static str,
    pub kind: Kind,
    pub required: bool,
}

pub const fn required(name: &'static str, kind: Kind) -> FieldSpec {
    FieldSpec {
        name,
        kind,
        required: true,
    }
}

pub const fn optional(name: &'static str, kind: Kind) -> FieldSpec {
    FieldSpec {
        name,
        kind,
        required: false,
    }
}

#[derive(Debug, Clone, Default)]
pub struct Model {
    values: Map<String, Value>,
}

impl Model {
    pub fn parse(bytes: &[u8], fields: &[FieldSpec]) -> Result<Self, Response> {
        let parsed: Value = if bytes.is_empty() {
            Value::Object(Map::new())
        } else {
            match serde_json::from_slice(bytes) {
                Ok(value) => value,
                Err(error) => {
                    return Err(validation_error(&[problem(
                        "json_invalid",
                        json!(["body", 0]),
                        "JSON decode error",
                        json!({}),
                        Some(json!({ "error": error.to_string() })),
                    )]))
                }
            }
        };
        let Some(object) = parsed.as_object() else {
            return Err(validation_error(&[problem(
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
                None | Some(Value::Null) if !field.required => {}
                None => problems.push(problem(
                    "missing",
                    json!(["body", field.name]),
                    "Field required",
                    parsed.clone(),
                    None,
                )),
                Some(value) => match coerce(field.kind, value) {
                    Ok(coerced) => {
                        values.insert(field.name.to_string(), coerced);
                    }
                    Err((kind, msg, ctx)) => problems.push(problem(
                        kind,
                        json!(["body", field.name]),
                        msg,
                        value.clone(),
                        ctx,
                    )),
                },
            }
        }
        if problems.is_empty() {
            Ok(Self { values })
        } else {
            Err(validation_error(&problems))
        }
    }

    pub fn str(&self, name: &str) -> &str {
        self.values.get(name).and_then(Value::as_str).unwrap_or("")
    }

    pub fn opt_str(&self, name: &str) -> Option<&str> {
        self.values.get(name).and_then(Value::as_str)
    }

    pub fn int(&self, name: &str, fallback: i64) -> i64 {
        self.values
            .get(name)
            .and_then(Value::as_i64)
            .unwrap_or(fallback)
    }

    pub fn float(&self, name: &str, fallback: f64) -> f64 {
        self.values
            .get(name)
            .and_then(Value::as_f64)
            .unwrap_or(fallback)
    }

    pub fn bool(&self, name: &str, fallback: bool) -> bool {
        self.values
            .get(name)
            .and_then(Value::as_bool)
            .unwrap_or(fallback)
    }

    pub fn get(&self, name: &str) -> Option<&Value> {
        self.values.get(name)
    }
}

type CoercionError = (&'static str, &'static str, Option<Value>);

fn coerce(kind: Kind, value: &Value) -> Result<Value, CoercionError> {
    match kind {
        Kind::Str(min_length) => {
            let Some(text) = value.as_str() else {
                return Err(("string_type", "Input should be a valid string", None));
            };
            if text.chars().count() < min_length {
                return Err((
                    "string_too_short",
                    "String should have at least 1 character",
                    Some(json!({ "min_length": min_length })),
                ));
            }
            Ok(json!(text))
        }
        Kind::OptStr => value.as_str().map(|text| json!(text)).ok_or((
            "string_type",
            "Input should be a valid string",
            None,
        )),
        Kind::Int => match value {
            Value::Number(number) if number.is_i64() => Ok(value.clone()),
            Value::Number(number) => match number.as_f64() {
                Some(float) if float.fract() == 0.0 => Ok(json!(float as i64)),
                _ => Err((
                    "int_from_float",
                    "Input should be a valid integer, got a number with a fractional part",
                    None,
                )),
            },
            Value::String(text) => text
                .trim()
                .parse::<i64>()
                .map(|int| json!(int))
                .map_err(|_| {
                    (
                        "int_parsing",
                        "Input should be a valid integer, unable to parse string as an integer",
                        None,
                    )
                }),
            _ => Err(("int_type", "Input should be a valid integer", None)),
        },
        Kind::Float => match value {
            Value::Number(_) => Ok(value.clone()),
            Value::String(text) => text
                .trim()
                .parse::<f64>()
                .map(|float| json!(float))
                .map_err(|_| {
                    (
                        "float_parsing",
                        "Input should be a valid number, unable to parse string as a number",
                        None,
                    )
                }),
            _ => Err(("float_type", "Input should be a valid number", None)),
        },
        Kind::Bool => match value {
            Value::Bool(_) => Ok(value.clone()),
            Value::String(text) => match text.trim().to_ascii_lowercase().as_str() {
                "1" | "on" | "t" | "true" | "y" | "yes" => Ok(json!(true)),
                "0" | "off" | "f" | "false" | "n" | "no" => Ok(json!(false)),
                _ => Err((
                    "bool_parsing",
                    "Input should be a valid boolean, unable to interpret input",
                    None,
                )),
            },
            _ => Err(("bool_type", "Input should be a valid boolean", None)),
        },
        Kind::Object => value.is_object().then(|| value.clone()).ok_or((
            "dict_type",
            "Input should be a valid dictionary",
            None,
        )),
        Kind::Array => value.is_array().then(|| value.clone()).ok_or((
            "list_type",
            "Input should be a valid list",
            None,
        )),
    }
}
