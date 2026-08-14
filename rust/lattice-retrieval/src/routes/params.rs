//! Request parameters for the native `/rust/*` retrieval routes.
//!
//! One parser serves both verbs: the query string is read first, then a JSON
//! object body (POST only) is merged over it, so `GET …?q=…` and
//! `POST {"query": "…"}` are the *same* endpoint rather than two handlers that
//! drift apart.
//!
//! The conventions here match `lattice-host`'s `gateway::params` deliberately,
//! because the two families of routes are mounted on one origin and a caller
//! should not have to learn which crate answered: bad input is a 422 whose body
//! is `{"error": "invalid_request", "field": …, "detail": …}`. FastAPI's own 422
//! carries a `detail` array because pydantic validates every field before
//! answering; this parser stops at the first bad value, so the body is flat and
//! names the one that failed.

use std::collections::HashMap;

use axum::extract::Query;
use axum::http::{StatusCode, Uri};
use axum::response::{IntoResponse, Response};
use axum::Json;
use lattice_core::parse_iso;
use serde_json::{Map, Value};

/// Largest request body these routes will read.
///
/// They take a question, a handful of numbers, and (for the context builder) a
/// small seam payload. Anything past this is not one of those.
pub const MAX_BODY_BYTES: usize = 256 * 1024;

/// A rejected request: which field, and why.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ParamError {
    /// The parameter that failed, or `body` / `query` for framing failures.
    pub field: String,
    /// Human-readable reason, including the offending value.
    pub detail: String,
}

impl ParamError {
    /// Build an error for `field`.
    pub fn new(field: impl Into<String>, detail: impl Into<String>) -> Self {
        Self {
            field: field.into(),
            detail: detail.into(),
        }
    }
}

impl std::fmt::Display for ParamError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}: {}", self.field, self.detail)
    }
}

impl std::error::Error for ParamError {}

impl IntoResponse for ParamError {
    fn into_response(self) -> Response {
        (
            StatusCode::UNPROCESSABLE_ENTITY,
            Json(serde_json::json!({
                "error": "invalid_request",
                "field": self.field,
                "detail": self.detail,
            })),
        )
            .into_response()
    }
}

/// The merged parameter bag for one request.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct RequestParams {
    values: Map<String, Value>,
}

impl RequestParams {
    /// Parse the query string of `uri`.
    pub fn from_uri(uri: &Uri) -> Result<Self, ParamError> {
        let parsed = Query::<HashMap<String, String>>::try_from_uri(uri)
            .map_err(|err| ParamError::new("query", err.body_text()))?;
        Ok(Self {
            values: parsed
                .0
                .into_iter()
                .map(|(key, value)| (key, Value::String(value)))
                .collect(),
        })
    }

    /// Merge a JSON object body over whatever the query string said.
    ///
    /// An empty body is not an error — `POST` with everything in the query
    /// string is a legitimate way to call these routes.
    pub fn merge_json(&mut self, raw: &[u8]) -> Result<(), ParamError> {
        if raw.iter().all(u8::is_ascii_whitespace) {
            return Ok(());
        }
        let parsed: Value = serde_json::from_slice(raw)
            .map_err(|err| ParamError::new("body", format!("body is not valid JSON: {err}")))?;
        let Value::Object(object) = parsed else {
            return Err(ParamError::new(
                "body",
                "body must be a JSON object of request parameters",
            ));
        };
        for (key, value) in object {
            self.values.insert(key, value);
        }
        Ok(())
    }

    /// Set a value the router took from the path rather than the query string.
    pub fn set(&mut self, key: &str, value: Value) {
        self.values.insert(key.to_string(), value);
    }

    fn first<'a>(&'a self, keys: &[&'a str]) -> Option<(&'a str, &'a Value)> {
        keys.iter()
            .find_map(|key| self.values.get(*key).map(|value| (*key, value)))
    }

    /// A required text parameter (`query`, aliased to `q`).
    ///
    /// Present-but-empty is allowed: the engines answer an empty query with an
    /// explicitly empty result, which is a truthful answer rather than an error.
    /// Absent is not — a search with no question is a caller bug.
    pub fn required_text(&self, keys: &[&str]) -> Result<String, ParamError> {
        let name = keys.first().copied().unwrap_or("query");
        match self.first(keys) {
            Some((_, Value::String(text))) => Ok(text.clone()),
            Some((key, other)) => Err(ParamError::new(
                key,
                format!("{key} must be a string, got {other}"),
            )),
            None => Err(ParamError::new(
                name,
                format!("{name} is required (alias: {})", keys.join(", ")),
            )),
        }
    }

    /// An optional text parameter; a non-string is a rejection, not a coercion.
    pub fn optional_text(&self, key: &str) -> Result<Option<String>, ParamError> {
        match self.first(&[key]) {
            None | Some((_, Value::Null)) => Ok(None),
            Some((_, Value::String(text))) => Ok(Some(text.clone())),
            Some((key, other)) => Err(ParamError::new(
                key,
                format!("{key} must be a string, got {other}"),
            )),
        }
    }

    /// An optional integer parameter, rejected outside `min..=max`.
    ///
    /// The engines themselves clamp (Python's `max(1, min(100, limit))`); this
    /// front door refuses instead, because a clamped answer looks exactly like
    /// the answer to the question that was asked, and it is not.
    pub fn optional_int(&self, key: &str, min: i64, max: i64) -> Result<Option<i64>, ParamError> {
        let Some((_, value)) = self.first(&[key]) else {
            return Ok(None);
        };
        let parsed = match value {
            Value::Number(number) => number.as_i64(),
            Value::String(text) => text.trim().parse::<i64>().ok(),
            _ => None,
        };
        match parsed {
            Some(number) if (min..=max).contains(&number) => Ok(Some(number)),
            _ => Err(ParamError::new(
                key,
                format!("{key} must be an integer between {min} and {max}, got {value}"),
            )),
        }
    }

    /// An optional float parameter, rejected outside `min..=max`.
    pub fn optional_float(&self, key: &str, min: f64, max: f64) -> Result<Option<f64>, ParamError> {
        let Some((_, value)) = self.first(&[key]) else {
            return Ok(None);
        };
        let parsed = match value {
            Value::Number(number) => number.as_f64(),
            Value::String(text) => text.trim().parse::<f64>().ok(),
            _ => None,
        };
        match parsed {
            Some(number) if number.is_finite() && number >= min && number <= max => {
                Ok(Some(number))
            }
            _ => Err(ParamError::new(
                key,
                format!("{key} must be a number between {min} and {max}, got {value}"),
            )),
        }
    }

    /// An optional boolean; the query string spellings are `1/0`, `true/false`,
    /// `yes/no`, `on/off`, matching what the Python config parser accepts.
    pub fn optional_bool(&self, key: &str) -> Result<Option<bool>, ParamError> {
        let Some((_, value)) = self.first(&[key]) else {
            return Ok(None);
        };
        let parsed = match value {
            Value::Bool(flag) => Some(*flag),
            Value::String(text) => match text.trim().to_ascii_lowercase().as_str() {
                "1" | "true" | "yes" | "on" => Some(true),
                "0" | "false" | "no" | "off" => Some(false),
                _ => None,
            },
            _ => None,
        };
        parsed
            .map(Some)
            .ok_or_else(|| ParamError::new(key, format!("{key} must be a boolean, got {value}")))
    }

    /// An optional JSON value: taken as-is from a body, parsed from a query
    /// string. This is how the context builder's data seams arrive.
    pub fn optional_json(&self, key: &str) -> Result<Option<Value>, ParamError> {
        match self.first(&[key]) {
            None | Some((_, Value::Null)) => Ok(None),
            Some((_, Value::String(text))) => serde_json::from_str(text)
                .map(Some)
                .map_err(|err| ParamError::new(key, format!("{key} must be valid JSON: {err}"))),
            Some((_, other)) => Ok(Some(other.clone())),
        }
    }

    /// An optional map of channel weights, rejected unless every value is a
    /// finite number — a weight map with a string in it is a typo, not a policy.
    pub fn optional_weights(&self, key: &str) -> Result<Option<Map<String, Value>>, ParamError> {
        let Some(value) = self.optional_json(key)? else {
            return Ok(None);
        };
        let Value::Object(map) = value else {
            return Err(ParamError::new(
                key,
                format!("{key} must be a JSON object of channel weights"),
            ));
        };
        let mut weights = Map::new();
        for (channel, weight) in map {
            let number = weight
                .as_f64()
                .filter(|value| value.is_finite())
                .ok_or_else(|| {
                    ParamError::new(
                        key,
                        format!("{key}.{channel} must be a number, got {weight}"),
                    )
                })?;
            weights.insert(channel, Value::from(number));
        }
        Ok(Some(weights))
    }

    /// The optional `now` override, as naive seconds since the epoch.
    ///
    /// Accepts the naive ISO-8601 the graph itself writes, or a raw epoch
    /// second. It exists so a golden can pin a decay curve and so a caller can
    /// ask "how would this have ranked last Tuesday"; it is reachable only from
    /// this machine, and it changes nothing but the recency multiplier.
    pub fn optional_instant(&self, key: &str) -> Result<Option<f64>, ParamError> {
        let Some((_, value)) = self.first(&[key]) else {
            return Ok(None);
        };
        let parsed = match value {
            Value::Number(number) => number.as_f64().filter(|v| v.is_finite()),
            Value::String(text) => {
                let text = text.trim();
                parse_iso(Some(text)).or_else(|| text.parse::<f64>().ok().filter(|v| v.is_finite()))
            }
            _ => None,
        };
        parsed.map(Some).ok_or_else(|| {
            ParamError::new(
                key,
                format!(
                    "{key} must be a naive ISO-8601 timestamp \
                     (2026-08-01T12:00:00) or epoch seconds, got {value}"
                ),
            )
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn params(query: &str) -> RequestParams {
        let uri: Uri = format!("/rust/graph/search{query}").parse().expect("uri");
        RequestParams::from_uri(&uri).expect("query string parses")
    }

    #[test]
    fn a_missing_query_string_is_an_empty_bag_not_a_failure() {
        let bag = params("");
        assert_eq!(bag, RequestParams::default());
        assert!(bag.required_text(&["query", "q"]).is_err());
    }

    #[test]
    fn percent_encoding_and_body_precedence_work_as_in_the_host() {
        let mut bag = params("?q=%ED%9A%8C%EC%9D%98+%EA%B8%B0%EB%A1%9D&limit=3");
        assert_eq!(bag.required_text(&["q"]).expect("text"), "회의 기록");
        bag.merge_json(br#"{"q": "from-body"}"#).expect("merge");
        assert_eq!(bag.required_text(&["q"]).expect("text"), "from-body");
        assert_eq!(bag.optional_int("limit", 1, 100), Ok(Some(3)));
        bag.set("conversation_id", json!("conv-a"));
        assert_eq!(
            bag.optional_text("conversation_id").unwrap(),
            Some("conv-a".into())
        );
    }

    #[test]
    fn framing_failures_name_the_body() {
        let mut bag = params("?q=x");
        bag.merge_json(b"").expect("empty body is fine");
        bag.merge_json(b"  \n ").expect("whitespace body is fine");
        let err = bag.merge_json(b"{nope").expect_err("malformed");
        assert_eq!(err.field, "body");
        assert!(err.detail.contains("not valid JSON"));
        let err = bag.merge_json(b"[1,2]").expect_err("not an object");
        assert!(err.detail.contains("JSON object"));
        assert_eq!(
            err.to_string(),
            "body: body must be a JSON object of request parameters"
        );
        assert_eq!(
            ParamError::new("x", "y").into_response().status(),
            StatusCode::UNPROCESSABLE_ENTITY
        );
    }

    #[test]
    fn text_parameters_refuse_coercion() {
        let mut bag = params("?q=hi&notes=x");
        assert_eq!(bag.optional_text("notes").unwrap(), Some("x".into()));
        assert_eq!(bag.optional_text("absent").unwrap(), None);
        bag.merge_json(br#"{"notes": 7, "blank": null}"#)
            .expect("merge");
        assert_eq!(bag.optional_text("blank").unwrap(), None);
        let err = bag.optional_text("notes").expect_err("wrong type");
        assert_eq!(err.field, "notes");
        let err = bag.required_text(&["notes"]).expect_err("wrong type");
        assert!(err.detail.contains("must be a string"));
    }

    #[test]
    fn numbers_are_range_checked_from_either_representation() {
        assert_eq!(
            params("?limit=7").optional_int("limit", 1, 100),
            Ok(Some(7))
        );
        assert_eq!(params("").optional_int("limit", 1, 100), Ok(None));
        for raw in [
            "?limit=0",
            "?limit=101",
            "?limit=abc",
            "?limit=",
            "?limit=1.5",
        ] {
            let err = params(raw)
                .optional_int("limit", 1, 100)
                .expect_err("refused");
            assert_eq!(err.field, "limit");
            assert!(err.detail.contains("between 1 and 100"));
        }
        assert_eq!(
            params("?alpha=0.25").optional_float("alpha", 0.0, 1.0),
            Ok(Some(0.25))
        );
        for raw in ["?alpha=2", "?alpha=-0.5", "?alpha=nope", "?alpha=NaN"] {
            assert!(params(raw).optional_float("alpha", 0.0, 1.0).is_err());
        }
        assert_eq!(params("").optional_float("alpha", 0.0, 1.0), Ok(None));
        let mut bag = params("");
        bag.merge_json(br#"{"limit": true, "alpha": null}"#)
            .expect("merge");
        assert!(bag.optional_int("limit", 1, 100).is_err());
        assert!(bag.optional_float("alpha", 0.0, 1.0).is_err());
    }

    #[test]
    fn booleans_accept_the_config_spellings() {
        for raw in ["1", "true", "TRUE", "yes", "on"] {
            assert_eq!(
                params(&format!("?legacy={raw}")).optional_bool("legacy"),
                Ok(Some(true))
            );
        }
        for raw in ["0", "false", "no", "off"] {
            assert_eq!(
                params(&format!("?legacy={raw}")).optional_bool("legacy"),
                Ok(Some(false))
            );
        }
        assert_eq!(params("").optional_bool("legacy"), Ok(None));
        let err = params("?legacy=maybe")
            .optional_bool("legacy")
            .expect_err("refused");
        assert_eq!(err.field, "legacy");
        let mut bag = params("");
        bag.merge_json(br#"{"legacy": false, "other": 3}"#)
            .expect("merge");
        assert_eq!(bag.optional_bool("legacy"), Ok(Some(false)));
        assert!(bag.optional_bool("other").is_err());
    }

    #[test]
    fn json_seams_parse_from_a_string_and_pass_through_from_a_body() {
        let bag = params("?memories=%7B%22results%22%3A%5B%5D%7D");
        assert_eq!(
            bag.optional_json("memories").unwrap(),
            Some(json!({"results": []}))
        );
        assert_eq!(params("").optional_json("memories").unwrap(), None);
        let err = params("?memories=nope")
            .optional_json("memories")
            .expect_err("refused");
        assert_eq!(err.field, "memories");
        let mut bag = params("");
        bag.merge_json(br#"{"artifacts": [{"path": "a.md"}], "memories": null}"#)
            .expect("merge");
        assert_eq!(
            bag.optional_json("artifacts").unwrap(),
            Some(json!([{"path": "a.md"}]))
        );
        assert_eq!(bag.optional_json("memories").unwrap(), None);
    }

    #[test]
    fn weight_maps_must_be_numbers_all_the_way_down() {
        let mut bag = params("");
        bag.merge_json(br#"{"weights": {"graph": 1, "keyword": 0.5}}"#)
            .expect("merge");
        let weights = bag.optional_weights("weights").unwrap().expect("weights");
        assert_eq!(weights["graph"], json!(1.0));
        assert_eq!(weights["keyword"], json!(0.5));
        assert_eq!(params("").optional_weights("weights").unwrap(), None);
        let mut bad = params("");
        bad.merge_json(br#"{"weights": {"graph": "lots"}}"#)
            .expect("merge");
        let err = bad.optional_weights("weights").expect_err("refused");
        assert!(err.detail.contains("weights.graph"));
        let mut wrong = params("");
        wrong.merge_json(br#"{"weights": [1,2]}"#).expect("merge");
        assert!(wrong.optional_weights("weights").is_err());
    }

    #[test]
    fn now_accepts_iso_and_epoch_seconds_and_nothing_else() {
        assert_eq!(
            params("?now=2026-08-01T12:00:00").optional_instant("now"),
            Ok(Some(1_785_585_600.0))
        );
        assert_eq!(
            params("?now=1234.5").optional_instant("now"),
            Ok(Some(1234.5))
        );
        assert_eq!(params("").optional_instant("now"), Ok(None));
        let mut bag = params("");
        bag.merge_json(br#"{"now": 42}"#).expect("merge");
        assert_eq!(bag.optional_instant("now"), Ok(Some(42.0)));
        for raw in ["?now=yesterday", "?now="] {
            let err = params(raw).optional_instant("now").expect_err("refused");
            assert_eq!(err.field, "now");
            assert!(err.detail.contains("ISO-8601"));
        }
        let mut bad = params("");
        bad.merge_json(br#"{"now": true}"#).expect("merge");
        assert!(bad.optional_instant("now").is_err());
    }
}
