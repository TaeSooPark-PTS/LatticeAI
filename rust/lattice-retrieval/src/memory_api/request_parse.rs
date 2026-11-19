//! FastAPI/Starlette-compatible query-string and JSON body parsing helpers.

use axum::body::Body;
use axum::http::Uri;
use axum::response::Response;
use serde_json::{Map, Value};

use super::{detail_response, missing_body, missing_query, validation_response, MAX_BODY_BYTES};

// ── query + body parsing, with FastAPI's refusal shapes ─────────────────────

/// The parsed query string of one request.
#[derive(Debug, Clone, Default)]
pub struct Query {
    values: Vec<(String, String)>,
}

impl Query {
    /// Parse `?a=1&b=2`; the *last* value wins, as Starlette's `QueryParams` do.
    pub fn from_uri(uri: &Uri) -> Self {
        let mut values = Vec::new();
        if let Some(raw) = uri.query() {
            for pair in raw.split('&').filter(|part| !part.is_empty()) {
                let (key, value) = match pair.split_once('=') {
                    Some((key, value)) => (key, value),
                    None => (pair, ""),
                };
                values.push((percent_decode(key), percent_decode(value)));
            }
        }
        Self { values }
    }

    /// The last value sent for `name`, if any.
    pub fn get(&self, name: &str) -> Option<&str> {
        self.values
            .iter()
            .rev()
            .find(|(key, _)| key == name)
            .map(|(_, value)| value.as_str())
    }

    /// `name: str = default` with an optional `max_length` constraint.
    pub fn string(
        &self,
        name: &str,
        default: &str,
        max_length: Option<usize>,
    ) -> Result<String, Response> {
        let value = self.get(name).unwrap_or(default).to_string();
        if let Some(limit) = max_length {
            if value.chars().count() > limit {
                return Err(validation_response(
                    "string_too_long",
                    serde_json::json!(["query", name]),
                    &format!("String should have at most {limit} characters"),
                    Value::String(value),
                ));
            }
        }
        Ok(value)
    }

    /// `name: str` with no default — 422 `missing` when absent.
    pub fn required_string(
        &self,
        name: &str,
        max_length: Option<usize>,
    ) -> Result<String, Response> {
        match self.get(name) {
            None => Err(missing_query(name)),
            Some(_) => self.string(name, "", max_length),
        }
    }

    /// `name: int = default`, optionally bounded by `ge` / `le`.
    pub fn int(
        &self,
        name: &str,
        default: i64,
        ge: Option<i64>,
        le: Option<i64>,
    ) -> Result<i64, Response> {
        let Some(raw) = self.get(name) else {
            return Ok(default);
        };
        let parsed: i64 = raw.trim().parse().map_err(|_| {
            validation_response(
                "int_parsing",
                serde_json::json!(["query", name]),
                "Input should be a valid integer, unable to parse string as an integer",
                Value::String(raw.to_string()),
            )
        })?;
        if let Some(bound) = ge {
            if parsed < bound {
                return Err(validation_response(
                    "greater_than_equal",
                    serde_json::json!(["query", name]),
                    &format!("Input should be greater than or equal to {bound}"),
                    Value::String(raw.to_string()),
                ));
            }
        }
        if let Some(bound) = le {
            if parsed > bound {
                return Err(validation_response(
                    "less_than_equal",
                    serde_json::json!(["query", name]),
                    &format!("Input should be less than or equal to {bound}"),
                    Value::String(raw.to_string()),
                ));
            }
        }
        Ok(parsed)
    }

    /// The raw query string a workspace selector is read out of.
    pub fn workspace(&self) -> Option<&str> {
        self.get(lattice_auth::WORKSPACE_PARAM)
    }
}

/// `%xx` and `+` decoding, the two escapes a browser actually sends.
fn percent_decode(raw: &str) -> String {
    let bytes = raw.replace('+', " ").into_bytes();
    let mut out: Vec<u8> = Vec::with_capacity(bytes.len());
    let mut index = 0;
    while index < bytes.len() {
        if bytes[index] == b'%' && index + 2 < bytes.len() {
            let hex = std::str::from_utf8(&bytes[index + 1..index + 3]).unwrap_or("");
            if let Ok(byte) = u8::from_str_radix(hex, 16) {
                out.push(byte);
                index += 3;
                continue;
            }
        }
        out.push(bytes[index]);
        index += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}

/// One request body, parsed as the JSON object a pydantic model needs.
///
/// An absent body is `{}` — the shape FastAPI gives a model whose fields all
/// have defaults, and the reading every `POST` in these families starts from.
pub async fn json_body(body: Body) -> Result<Value, Response> {
    let bytes = axum::body::to_bytes(body, MAX_BODY_BYTES)
        .await
        .map_err(|error| {
            detail_response(400, &format!("could not read the request body: {error}"))
        })?;
    if bytes.is_empty() {
        return Ok(Value::Object(Map::new()));
    }
    // The `ctx.error` text FastAPI carries here is CPython's own decoder
    // message and changes between releases, so it is deliberately not
    // reproduced (WP-I2 §7.8 made the same call for the auth bodies).
    let parsed: Value = serde_json::from_slice(&bytes).map_err(|_| {
        validation_response(
            "json_invalid",
            serde_json::json!(["body", 0]),
            "JSON decode error",
            serde_json::json!({}),
        )
    })?;
    if !parsed.is_object() {
        return Err(validation_response(
            "model_attributes_type",
            serde_json::json!(["body"]),
            "Input should be a valid dictionary or object to extract fields from",
            parsed,
        ));
    }
    Ok(parsed)
}

// ── the readings a pydantic field gives a handler ───────────────────────────

/// `field: str = ""` — anything that is not a string reads as the default.
pub fn body_str(body: &Value, name: &str, default: &str) -> String {
    match body.get(name) {
        Some(Value::String(text)) => text.clone(),
        _ => default.to_string(),
    }
}

/// `field: str` with no default — 422 `missing` when the key is absent.
pub fn body_required_str(body: &Value, name: &str) -> Result<String, Response> {
    match body.get(name) {
        Some(Value::String(text)) => Ok(text.clone()),
        Some(other) => Err(validation_response(
            "string_type",
            serde_json::json!(["body", name]),
            "Input should be a valid string",
            other.clone(),
        )),
        None => Err(missing_body(name, body)),
    }
}

/// `field: int = default`.
pub fn body_int(body: &Value, name: &str, default: i64) -> i64 {
    match body.get(name) {
        Some(Value::Number(number)) => number.as_i64().unwrap_or(default),
        _ => default,
    }
}

/// `field: bool = default`.
pub fn body_bool(body: &Value, name: &str, default: bool) -> bool {
    match body.get(name) {
        Some(Value::Bool(flag)) => *flag,
        _ => default,
    }
}

/// `field: Optional[bool] = None`.
pub fn body_opt_bool(body: &Value, name: &str) -> Option<bool> {
    match body.get(name) {
        Some(Value::Bool(flag)) => Some(*flag),
        _ => None,
    }
}

/// `field: Optional[str] = None`.
pub fn body_opt_str(body: &Value, name: &str) -> Option<String> {
    match body.get(name) {
        Some(Value::String(text)) => Some(text.clone()),
        _ => None,
    }
}

/// `field: List[str] = []`.
pub fn body_str_list(body: &Value, name: &str) -> Vec<String> {
    match body.get(name) {
        Some(Value::Array(items)) => items
            .iter()
            .filter_map(|item| item.as_str().map(str::to_string))
            .collect(),
        _ => Vec::new(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::memory_api::shared::message_response;
    use axum::http::StatusCode;

    #[test]
    fn a_query_string_is_read_the_way_starlette_reads_it() {
        let uri: Uri = "/x?source=workspace&limit=5&limit=7&q=%ED%95%9C%EA%B8%80&flag"
            .parse()
            .expect("uri");
        let query = Query::from_uri(&uri);
        assert_eq!(query.get("source"), Some("workspace"));
        assert_eq!(query.int("limit", 50, None, None).expect("int"), 7);
        assert_eq!(query.get("q"), Some("한글"));
        assert_eq!(query.get("flag"), Some(""));
        assert_eq!(query.int("missing", 3, None, None).expect("default"), 3);
    }

    #[test]
    fn a_bad_query_value_is_a_422_that_names_the_field() {
        let uri: Uri = "/x?limit=abc&q=toolong".parse().expect("uri");
        let query = Query::from_uri(&uri);
        assert_eq!(
            query.int("limit", 1, None, None).unwrap_err().status(),
            StatusCode::UNPROCESSABLE_ENTITY
        );
        assert_eq!(
            query.string("q", "", Some(3)).unwrap_err().status(),
            StatusCode::UNPROCESSABLE_ENTITY
        );
        assert_eq!(
            query.required_string("ts", None).unwrap_err().status(),
            StatusCode::UNPROCESSABLE_ENTITY
        );
        assert_eq!(
            query
                .int("limit2", 1, Some(1), None)
                .expect("absent is the default"),
            1
        );
        let bounded: Uri = "/x?limit=0&other=99".parse().expect("uri");
        let bounded = Query::from_uri(&bounded);
        assert!(bounded.int("limit", 8, Some(1), Some(20)).is_err());
        assert!(bounded.int("other", 8, Some(1), Some(20)).is_err());
    }

    #[test]
    fn the_pydantic_readings_match_their_defaults() {
        let body = serde_json::json!({
            "query": "x", "limit": 3, "confirm": true, "ids": ["a", 1], "dry_run": false,
        });
        assert_eq!(body_str(&body, "query", ""), "x");
        assert_eq!(body_str(&body, "absent", "d"), "d");
        assert_eq!(body_int(&body, "limit", 20), 3);
        assert_eq!(body_int(&body, "absent", 20), 20);
        assert!(body_bool(&body, "confirm", false));
        assert!(!body_bool(&body, "absent", false));
        assert_eq!(body_opt_bool(&body, "dry_run"), Some(false));
        assert_eq!(body_opt_bool(&body, "absent"), None);
        assert_eq!(body_opt_str(&body, "query"), Some("x".to_string()));
        assert_eq!(body_opt_str(&body, "absent"), None);
        assert_eq!(body_str_list(&body, "ids"), vec!["a".to_string()]);
        assert!(body_str_list(&body, "absent").is_empty());
        assert_eq!(body_required_str(&body, "query").expect("present"), "x");
        assert!(body_required_str(&body, "absent").is_err());
        assert!(body_required_str(&body, "limit").is_err());
    }

    #[test]
    fn the_refusal_bodies_are_the_python_ones() {
        let response = detail_response(400, "clear requires confirm=true");
        assert_eq!(response.status(), StatusCode::BAD_REQUEST);
        assert_eq!(
            response
                .headers()
                .get(axum::http::header::CONTENT_TYPE)
                .and_then(|v| v.to_str().ok()),
            Some("application/json")
        );
        assert_eq!(
            message_response(404, "memory.unknown_source", "en", &[("source", "x")]).status(),
            StatusCode::NOT_FOUND
        );
        assert_eq!(
            missing_body("scope", &serde_json::json!({})).status(),
            StatusCode::UNPROCESSABLE_ENTITY
        );
        assert_eq!(
            missing_query("source").status(),
            StatusCode::UNPROCESSABLE_ENTITY
        );
    }

    #[test]
    fn a_body_is_the_object_pydantic_would_have_seen() {
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .expect("runtime");
        runtime.block_on(async {
            assert_eq!(
                json_body(Body::empty()).await.expect("empty is {}"),
                serde_json::json!({})
            );
            assert_eq!(
                json_body(Body::from("{\"a\":1}")).await.expect("object"),
                serde_json::json!({"a": 1})
            );
            assert_eq!(
                json_body(Body::from("[1]")).await.unwrap_err().status(),
                StatusCode::UNPROCESSABLE_ENTITY
            );
            assert_eq!(
                json_body(Body::from("{nope")).await.unwrap_err().status(),
                StatusCode::UNPROCESSABLE_ENTITY
            );
        });
    }
}
