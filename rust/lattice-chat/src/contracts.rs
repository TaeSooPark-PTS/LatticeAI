//! `ChatRequest`, and the 422 pydantic answers for a body that is not one.
//!
//! Port of `latticeai/api/chat_contracts.py`. The model itself is four lines of
//! Python; what has to be reproduced is the **refusal**, because
//! `rust/fixtures/http/chat.json` pins two of them byte-for-byte
//! (`validation_missing_message`, `wrong_types_422`) and the SPA's
//! `friendlyError` reads the `detail[]` shape.
//!
//! `lattice_auth::body::parse_model` covers flat all-`str` models and stops
//! there — `ChatRequest` has an `int`, a `float` and two `bool`s, whose lax-mode
//! coercions and error codes (`int_parsing`, `float_parsing`, `bool_parsing`,
//! `int_from_float`, `*_type`) are what a caller sees when it sends
//! `{"stream": "yes please"}`. Those live here rather than being pushed into
//! `lattice-auth`, which is another package's file.
//!
//! Order matters twice: errors are reported in **field-declaration** order, and
//! a `missing` entry's `input` is the **whole body**, not the absent value.

use axum::http::StatusCode;
use axum::response::Response;
use lattice_auth::response::json_response;
use lattice_auth::OrderedMap;
use serde_json::{json, Value};

/// What one field of the model accepts.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Kind {
    /// `str` — required, no default.
    Str,
    /// `Optional[str] = None`.
    OptStr,
    /// `int` with a default.
    Int,
    /// `float` with a default.
    Float,
    /// `bool` with a default.
    Bool,
}

/// `ChatRequest`'s fields, in declaration order. The order is the contract.
const FIELDS: &[(&str, Kind)] = &[
    ("message", Kind::Str),
    ("conversation_id", Kind::OptStr),
    ("client_url", Kind::OptStr),
    ("model", Kind::OptStr),
    ("max_tokens", Kind::Int),
    ("temperature", Kind::Float),
    ("stream", Kind::Bool),
    ("context", Kind::OptStr),
    ("source", Kind::OptStr),
    ("user_email", Kind::OptStr),
    ("user_nickname", Kind::OptStr),
    ("image_data", Kind::OptStr),
    ("allow_file_context", Kind::Bool),
    ("network_mode", Kind::OptStr),
];

/// `ChatRequest` with Python's defaults already applied.
#[derive(Debug, Clone, PartialEq)]
pub struct ChatRequest {
    pub message: String,
    pub conversation_id: Option<String>,
    pub client_url: Option<String>,
    pub model: Option<String>,
    pub max_tokens: i64,
    pub temperature: f64,
    pub stream: bool,
    pub context: Option<String>,
    pub source: Option<String>,
    pub user_email: Option<String>,
    pub user_nickname: Option<String>,
    pub image_data: Option<String>,
    pub allow_file_context: bool,
    pub network_mode: Option<String>,
}

impl Default for ChatRequest {
    fn default() -> Self {
        Self {
            message: String::new(),
            conversation_id: None,
            client_url: None,
            model: None,
            max_tokens: 2048,
            temperature: 0.2,
            stream: true,
            context: None,
            source: None,
            user_email: None,
            user_nickname: None,
            image_data: None,
            allow_file_context: false,
            network_mode: None,
        }
    }
}

/// One pydantic error entry, in the key order FastAPI emits.
fn entry(kind: &str, loc: Value, msg: &str, input: Value, ctx: Option<Value>) -> OrderedMap {
    let mut problem = OrderedMap::new();
    problem.insert("type", json!(kind));
    problem.insert("loc", loc);
    problem.insert("msg", json!(msg));
    problem.insert("input", input);
    if let Some(ctx) = ctx {
        problem.insert("ctx", ctx);
    }
    problem
}

/// The common case: one field of the body.
fn problem(kind: &str, field: &str, msg: &str, input: Value) -> OrderedMap {
    entry(kind, json!(["body", field]), msg, input, None)
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

/// Pydantic v2 lax `bool`: `str` from a fixed vocabulary, `0`/`1` numbers.
fn coerce_bool(field: &str, value: &Value) -> Result<bool, OrderedMap> {
    const TRUE: [&str; 6] = ["1", "on", "t", "true", "y", "yes"];
    const FALSE: [&str; 6] = ["0", "off", "f", "false", "n", "no"];
    match value {
        Value::Bool(flag) => Ok(*flag),
        Value::String(text) => {
            let lowered = text.trim().to_lowercase();
            if TRUE.contains(&lowered.as_str()) {
                Ok(true)
            } else if FALSE.contains(&lowered.as_str()) {
                Ok(false)
            } else {
                Err(problem(
                    "bool_parsing",
                    field,
                    "Input should be a valid boolean, unable to interpret input",
                    value.clone(),
                ))
            }
        }
        Value::Number(number) => match number.as_f64() {
            Some(1.0) => Ok(true),
            Some(0.0) => Ok(false),
            _ => Err(problem(
                "bool_parsing",
                field,
                "Input should be a valid boolean, unable to interpret input",
                value.clone(),
            )),
        },
        other => Err(problem(
            "bool_type",
            field,
            "Input should be a valid boolean",
            other.clone(),
        )),
    }
}

/// Pydantic v2 lax `int`: numeric strings and integral floats, never `bool`.
fn coerce_int(field: &str, value: &Value) -> Result<i64, OrderedMap> {
    match value {
        Value::Number(number) => {
            if let Some(integer) = number.as_i64() {
                return Ok(integer);
            }
            match number.as_f64() {
                Some(float) if float.fract() == 0.0 => Ok(float as i64),
                Some(_) => Err(problem(
                    "int_from_float",
                    field,
                    "Input should be a valid integer, got a number with a fractional part",
                    value.clone(),
                )),
                None => Err(problem(
                    "int_type",
                    field,
                    "Input should be a valid integer",
                    value.clone(),
                )),
            }
        }
        Value::String(text) => text.trim().parse::<i64>().map_err(|_| {
            problem(
                "int_parsing",
                field,
                "Input should be a valid integer, unable to parse string as an integer",
                value.clone(),
            )
        }),
        other => Err(problem(
            "int_type",
            field,
            "Input should be a valid integer",
            other.clone(),
        )),
    }
}

/// Pydantic v2 lax `float`: numbers and numeric strings, never `bool`.
fn coerce_float(field: &str, value: &Value) -> Result<f64, OrderedMap> {
    match value {
        Value::Number(number) => number.as_f64().ok_or_else(|| {
            problem(
                "float_type",
                field,
                "Input should be a valid number",
                value.clone(),
            )
        }),
        Value::String(text) => text.trim().parse::<f64>().map_err(|_| {
            problem(
                "float_parsing",
                field,
                "Input should be a valid number, unable to parse string as a number",
                value.clone(),
            )
        }),
        other => Err(problem(
            "float_type",
            field,
            "Input should be a valid number",
            other.clone(),
        )),
    }
}

fn coerce_str(field: &str, value: &Value) -> Result<String, OrderedMap> {
    match value {
        Value::String(text) => Ok(text.clone()),
        other => Err(problem(
            "string_type",
            field,
            "Input should be a valid string",
            other.clone(),
        )),
    }
}

/// Parse a `POST /chat` body, or render FastAPI's 422.
pub fn parse_chat_request(bytes: &[u8]) -> Result<ChatRequest, Response> {
    let parsed: Value = match serde_json::from_slice(bytes) {
        Ok(value) => value,
        Err(error) => {
            return Err(errors(&[entry(
                "json_invalid",
                json!(["body", 0]),
                "JSON decode error",
                json!({}),
                Some(json!({"error": error.to_string()})),
            )]))
        }
    };
    let Some(object) = parsed.as_object() else {
        return Err(errors(&[entry(
            "model_attributes_type",
            json!(["body"]),
            "Input should be a valid dictionary or object to extract fields from",
            parsed.clone(),
            None,
        )]));
    };

    let mut request = ChatRequest::default();
    let mut problems: Vec<OrderedMap> = Vec::new();
    for (name, kind) in FIELDS {
        let Some(value) = object.get(*name) else {
            if *kind == Kind::Str {
                problems.push(problem(
                    "missing",
                    name,
                    "Field required",
                    // The whole body, which is what pydantic reports for a
                    // missing key — not the absent value.
                    parsed.clone(),
                ));
            }
            continue;
        };
        // `Optional[...] = None` fields take an explicit null as the default;
        // `str`/`int`/`float`/`bool` fields do not.
        if value.is_null() && *kind == Kind::OptStr {
            continue;
        }
        match kind {
            Kind::Str => match coerce_str(name, value) {
                Ok(text) => request.message = text,
                Err(entry) => problems.push(entry),
            },
            Kind::OptStr => match coerce_str(name, value) {
                Ok(text) => assign_opt(&mut request, name, Some(text)),
                Err(entry) => problems.push(entry),
            },
            Kind::Int => match coerce_int(name, value) {
                Ok(number) => request.max_tokens = number,
                Err(entry) => problems.push(entry),
            },
            Kind::Float => match coerce_float(name, value) {
                Ok(number) => request.temperature = number,
                Err(entry) => problems.push(entry),
            },
            Kind::Bool => match coerce_bool(name, value) {
                Ok(flag) => {
                    if *name == "stream" {
                        request.stream = flag;
                    } else {
                        request.allow_file_context = flag;
                    }
                }
                Err(entry) => problems.push(entry),
            },
        }
    }
    if problems.is_empty() {
        Ok(request)
    } else {
        Err(errors(&problems))
    }
}

fn assign_opt(request: &mut ChatRequest, name: &str, value: Option<String>) {
    match name {
        "conversation_id" => request.conversation_id = value,
        "client_url" => request.client_url = value,
        "model" => request.model = value,
        "context" => request.context = value,
        "source" => request.source = value,
        "user_email" => request.user_email = value,
        "user_nickname" => request.user_nickname = value,
        "image_data" => request.image_data = value,
        "network_mode" => request.network_mode = value,
        // Unreachable: `FIELDS` is the only caller and every `OptStr` entry is
        // named above. A silent no-op rather than a panic, because a future
        // field added to `FIELDS` and forgotten here must not take the process
        // down mid-request.
        _ => {}
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn body_of(response: Response) -> String {
        let (_, body) = response.into_parts();
        tokio::runtime::Builder::new_current_thread()
            .build()
            .unwrap()
            .block_on(async move {
                String::from_utf8(axum::body::to_bytes(body, 65_536).await.unwrap().to_vec())
                    .unwrap()
            })
    }

    #[test]
    fn defaults_match_the_pydantic_model() {
        let parsed = parse_chat_request(br#"{"message":"hi"}"#).unwrap();
        assert_eq!(parsed.message, "hi");
        assert_eq!(parsed.max_tokens, 2048);
        assert!((parsed.temperature - 0.2).abs() < f64::EPSILON);
        assert!(parsed.stream);
        assert!(!parsed.allow_file_context);
        assert_eq!(
            parsed,
            ChatRequest {
                message: "hi".into(),
                ..Default::default()
            }
        );
    }

    #[test]
    fn every_declared_field_round_trips() {
        let parsed = parse_chat_request(
            br#"{"message":"hello","conversation_id":"c","client_url":"u","model":null,
                 "max_tokens":512,"temperature":0.1,"stream":false,"context":"ctx",
                 "source":"web","user_email":null,"user_nickname":"owner","image_data":null,
                 "allow_file_context":false,"network_mode":"local_only"}"#,
        )
        .unwrap();
        assert_eq!(parsed.conversation_id.as_deref(), Some("c"));
        assert_eq!(parsed.client_url.as_deref(), Some("u"));
        assert_eq!(parsed.model, None, "an explicit null is the default");
        assert_eq!(parsed.max_tokens, 512);
        assert_eq!(parsed.context.as_deref(), Some("ctx"));
        assert_eq!(parsed.source.as_deref(), Some("web"));
        assert_eq!(parsed.user_nickname.as_deref(), Some("owner"));
        assert_eq!(parsed.network_mode.as_deref(), Some("local_only"));
    }

    #[test]
    fn a_missing_message_reports_the_whole_body_as_input() {
        let refusal = parse_chat_request(br#"{"stream":false}"#).unwrap_err();
        assert_eq!(refusal.status(), StatusCode::UNPROCESSABLE_ENTITY);
        assert_eq!(
            body_of(refusal),
            r#"{"detail":[{"type":"missing","loc":["body","message"],"msg":"Field required","input":{"stream":false}}]}"#
        );
    }

    #[test]
    fn three_type_refusals_report_in_declaration_order() {
        let refusal = parse_chat_request(
            br#"{"message":"hello","stream":"yes please","temperature":"hot","max_tokens":"many"}"#,
        )
        .unwrap_err();
        assert_eq!(
            body_of(refusal),
            r#"{"detail":[{"type":"int_parsing","loc":["body","max_tokens"],"msg":"Input should be a valid integer, unable to parse string as an integer","input":"many"},{"type":"float_parsing","loc":["body","temperature"],"msg":"Input should be a valid number, unable to parse string as a number","input":"hot"},{"type":"bool_parsing","loc":["body","stream"],"msg":"Input should be a valid boolean, unable to interpret input","input":"yes please"}]}"#
        );
    }

    #[test]
    fn lax_coercions_follow_pydantic() {
        let parsed = parse_chat_request(
            br#"{"message":"m","max_tokens":"512","temperature":1,"stream":"no",
                 "allow_file_context":1}"#,
        )
        .unwrap();
        assert_eq!(parsed.max_tokens, 512);
        assert!((parsed.temperature - 1.0).abs() < f64::EPSILON);
        assert!(!parsed.stream);
        assert!(parsed.allow_file_context);
        // An integral float is an int; a fractional one is not.
        assert_eq!(
            parse_chat_request(br#"{"message":"m","max_tokens":8.0}"#)
                .unwrap()
                .max_tokens,
            8
        );
        assert!(
            body_of(parse_chat_request(br#"{"message":"m","max_tokens":8.5}"#).unwrap_err())
                .contains("int_from_float")
        );
    }

    #[test]
    fn wrong_container_types_report_the_type_codes() {
        for (body, code) in [
            (r#"{"message":"m","max_tokens":{}}"#, "int_type"),
            (r#"{"message":"m","temperature":[]}"#, "float_type"),
            (r#"{"message":"m","stream":{}}"#, "bool_type"),
            (r#"{"message":"m","stream":2}"#, "bool_parsing"),
            (r#"{"message":7}"#, "string_type"),
            (r#"{"message":"m","source":7}"#, "string_type"),
            (r#"{"message":"m","max_tokens":null}"#, "int_type"),
            (r#"{"message":"m","temperature":null}"#, "float_type"),
        ] {
            let rendered = body_of(parse_chat_request(body.as_bytes()).unwrap_err());
            assert!(
                rendered.contains(code),
                "{body} should report {code}: {rendered}"
            );
        }
    }

    #[test]
    fn a_body_that_is_not_an_object_or_not_json_is_reported_as_such() {
        assert!(body_of(parse_chat_request(b"[]").unwrap_err()).contains("model_attributes_type"));
        assert!(body_of(parse_chat_request(b"not json").unwrap_err()).contains("json_invalid"));
    }

    #[test]
    fn an_unknown_optional_name_is_a_no_op_rather_than_a_panic() {
        let mut request = ChatRequest::default();
        assign_opt(&mut request, "not_a_field", Some("x".into()));
        assert_eq!(request, ChatRequest::default());
        assert!(format!("{request:?}").contains("max_tokens"));
    }
}
