//! Shared HTTP kit for the WP-R6 retrieval families.

use axum::http::{HeaderMap, StatusCode};
use axum::response::Response;
use lattice_auth::response::json_response;
use lattice_auth::OrderedMap;
use lattice_core::messages::{self, LANGUAGE_HEADER};
use lattice_core::CoreError;
use serde_json::{json, Map, Value};

use super::GRAPH_DISABLED_DETAIL;

// ── responses ───────────────────────────────────────────────────────────────

/// One JSON body, rendered the way Starlette's `JSONResponse` renders it
/// (compact separators, `application/json` with no charset).
pub fn ok(value: &Value) -> Response {
    let body = serde_json::to_string(value).unwrap_or_else(|_| "null".into());
    json_response(StatusCode::OK, &body, None)
}

/// One `HTTPException(status, detail)` with a literal detail.
pub fn detail(status: u16, text: &str) -> Response {
    let body = serde_json::to_string(&json!({ "detail": text })).unwrap_or_default();
    json_response(
        StatusCode::from_u16(status).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR),
        &body,
        None,
    )
}

/// One `http_error(status, id, language, **args)` from the shared catalog.
pub fn http_error(status: u16, id: &str, lang: &str) -> Response {
    detail(status, &messages::text(id, lang, &[]))
}

/// One `http_error` with interpolation arguments.
pub fn http_error_with(status: u16, id: &str, lang: &str, args: &[(&str, &str)]) -> Response {
    detail(status, &messages::text(id, lang, args))
}

/// `_require_graph()` — 404, and the same sentence Python prints.
pub fn graph_disabled() -> Response {
    detail(404, GRAPH_DISABLED_DETAIL)
}

/// The language this request is answered in.
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

/// `_raise_graph_error(exc)` — every search handler's `except ValueError`.
///
/// The engines report Python's `ValueError` as [`CoreError::InvalidRequest`],
/// and `search.py` turns every one of them into a 404 carrying `str(exc)`.
/// Anything else is a real failure and answers 500, which is what an
/// unhandled exception does in FastAPI.
pub fn engine_error(error: CoreError) -> Response {
    match error {
        CoreError::InvalidRequest(message) => detail(404, &message),
        other => detail(500, &format!("Internal Server Error: {other}")),
    }
}

// ── FastAPI-shaped validation ───────────────────────────────────────────────

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

/// `{"detail":[…]}` at 422, rendered exactly once.
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

/// The parsed query string, with FastAPI's coercions and its refusals.
///
/// Repeated keys keep the **last** value, which is what Starlette's
/// `QueryParams.__getitem__` returns for a scalar annotation.
#[derive(Debug, Clone, Default)]
pub struct Query {
    pairs: Vec<(String, String)>,
}

impl Query {
    /// Parse a raw query string (no leading `?`).
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

    /// The last value for `name`, if the caller sent one.
    pub fn raw(&self, name: &str) -> Option<&str> {
        self.pairs
            .iter()
            .rev()
            .find(|(key, _)| key == name)
            .map(|(_, value)| value.as_str())
    }

    /// A required `str` parameter.
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

    /// An optional `str` parameter with a default.
    pub fn str_or(&self, name: &str, fallback: &str) -> String {
        self.raw(name).unwrap_or(fallback).to_string()
    }

    /// An `int` parameter with a default.
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

    /// A `float` parameter with a default.
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

    /// A `bool` parameter with a default, in pydantic v2's vocabulary.
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

/// Percent-decoding with `+` meaning space — Starlette's `parse_qsl`.
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

/// What one pydantic field accepts.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    /// `str`, with an optional `min_length`.
    Str(usize),
    /// `Optional[str] = None`.
    OptStr,
    /// `int`.
    Int,
    /// `float`.
    Float,
    /// `bool`.
    Bool,
    /// `Dict[str, Any]` / `Optional[Dict[…]]`.
    Object,
    /// `List[...]` / `Optional[List[...]]`.
    Array,
}

/// One field of a flat pydantic model.
#[derive(Debug, Clone, Copy)]
pub struct FieldSpec {
    /// The field name, which is also its `loc` tail.
    pub name: &'static str,
    /// What it accepts.
    pub kind: Kind,
    /// Whether the model declares it without a default.
    pub required: bool,
}

/// A required field.
pub const fn required(name: &'static str, kind: Kind) -> FieldSpec {
    FieldSpec {
        name,
        kind,
        required: true,
    }
}

/// A field with a default.
pub const fn optional(name: &'static str, kind: Kind) -> FieldSpec {
    FieldSpec {
        name,
        kind,
        required: false,
    }
}

/// A validated request body: present fields, coerced, by name.
#[derive(Debug, Clone, Default)]
pub struct Model {
    values: Map<String, Value>,
}

impl Model {
    /// Parse and validate one JSON body against `fields`.
    ///
    /// An empty body is `{}`, which is how FastAPI treats a `POST` whose model
    /// has no required fields (`index_jobs.DrainRequest`, the promotion
    /// actions). A body that is not an object refuses the way pydantic does.
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

    /// The field's string value, or `""`.
    pub fn str(&self, name: &str) -> &str {
        self.values.get(name).and_then(Value::as_str).unwrap_or("")
    }

    /// The field's string value when the caller sent a non-null one.
    pub fn opt_str(&self, name: &str) -> Option<&str> {
        self.values.get(name).and_then(Value::as_str)
    }

    /// The field's integer value, or `fallback`.
    pub fn int(&self, name: &str, fallback: i64) -> i64 {
        self.values
            .get(name)
            .and_then(Value::as_i64)
            .unwrap_or(fallback)
    }

    /// The field's float value, or `fallback`.
    pub fn float(&self, name: &str, fallback: f64) -> f64 {
        self.values
            .get(name)
            .and_then(Value::as_f64)
            .unwrap_or(fallback)
    }

    /// The field's boolean value, or `fallback`.
    pub fn bool(&self, name: &str, fallback: bool) -> bool {
        self.values
            .get(name)
            .and_then(Value::as_bool)
            .unwrap_or(fallback)
    }

    /// The field's raw JSON value, if present and not null.
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
