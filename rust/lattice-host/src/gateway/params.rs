use std::collections::HashMap;

use axum::extract::Query;
use axum::http::{StatusCode, Uri};
use axum::response::{IntoResponse, Response};
use axum::Json;
use lattice_core::parse_iso;
use serde_json::{Map, Value};

pub const MAX_BODY_BYTES: usize = 64 * 1024;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ParamError {
    pub field: String,
    pub detail: String,
}

impl ParamError {
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

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct SearchParams {
    values: Map<String, Value>,
}

impl SearchParams {
    pub fn from_uri(uri: &Uri) -> Result<Self, ParamError> {
        let parsed = Query::<HashMap<String, String>>::try_from_uri(uri)
            .map_err(|err| ParamError::new("query", err.body_text()))?;
        let values = parsed
            .0
            .into_iter()
            .map(|(key, value)| (key, Value::String(value)))
            .collect();
        Ok(Self { values })
    }

    pub fn merge_json(&mut self, raw: &[u8]) -> Result<(), ParamError> {
        if raw.iter().all(u8::is_ascii_whitespace) {
            return Ok(());
        }
        let parsed: Value = serde_json::from_slice(raw)
            .map_err(|err| ParamError::new("body", format!("body is not valid JSON: {err}")))?;
        let Value::Object(object) = parsed else {
            return Err(ParamError::new(
                "body",
                "body must be a JSON object of search parameters",
            ));
        };
        for (key, value) in object {
            self.values.insert(key, value);
        }
        Ok(())
    }

    fn first<'a>(&'a self, keys: &[&'a str]) -> Option<(&'a str, &'a Value)> {
        keys.iter()
            .find_map(|key| self.values.get(*key).map(|value| (*key, value)))
    }

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

    fn params(query: &str) -> SearchParams {
        let uri: Uri = format!("/rust/search/hybrid{query}").parse().expect("uri");
        SearchParams::from_uri(&uri).expect("query string parses")
    }

    #[test]
    fn a_missing_query_string_is_an_empty_bag_not_a_failure() {
        let bag = params("");
        assert_eq!(bag, SearchParams::default());
        assert!(bag.required_text(&["query", "q"]).is_err());
    }

    #[test]
    fn percent_encoding_and_plus_are_decoded() {
        let bag = params("?q=%ED%9A%8C%EC%9D%98+%EA%B8%B0%EB%A1%9D");
        assert_eq!(bag.required_text(&["q"]).expect("text"), "회의 기록");
    }

    #[test]
    fn the_body_wins_over_the_query_string() {
        let mut bag = params("?q=from-url&top_k=3");
        bag.merge_json(br#"{"q": "from-body"}"#).expect("merge");
        assert_eq!(bag.required_text(&["q"]).expect("text"), "from-body");
        assert_eq!(bag.optional_int("top_k", 1, 100), Ok(Some(3)));
    }

    #[test]
    fn an_empty_body_is_allowed_and_a_malformed_one_is_not() {
        let mut bag = params("?q=x");
        bag.merge_json(b"").expect("empty body is fine");
        bag.merge_json(b"  \n ").expect("whitespace body is fine");
        let err = bag.merge_json(b"{nope").expect_err("malformed");
        assert_eq!(err.field, "body");
        assert!(err.detail.contains("not valid JSON"));
        let err = bag.merge_json(b"[1,2]").expect_err("not an object");
        assert!(err.detail.contains("JSON object"));
    }

    #[test]
    fn a_present_but_empty_query_is_accepted() {
        let bag = params("?q=");
        assert_eq!(bag.required_text(&["q"]).expect("text"), "");
    }

    #[test]
    fn a_non_string_query_is_rejected_by_name() {
        let mut bag = params("");
        bag.merge_json(br#"{"query": 7}"#).expect("merge");
        let err = bag.required_text(&["query", "q"]).expect_err("wrong type");
        assert_eq!(err.field, "query");
        assert!(err.detail.contains("must be a string"));
    }

    #[test]
    fn integers_come_from_either_a_string_or_a_json_number() {
        let mut bag = params("?limit=7");
        assert_eq!(bag.optional_int("limit", 1, 100), Ok(Some(7)));
        bag.merge_json(br#"{"limit": 9}"#).expect("merge");
        assert_eq!(bag.optional_int("limit", 1, 100), Ok(Some(9)));
        assert_eq!(bag.optional_int("absent", 1, 100), Ok(None));
    }

    #[test]
    fn out_of_range_and_unparseable_integers_are_refused() {
        for raw in [
            "?top_k=0",
            "?top_k=101",
            "?top_k=abc",
            "?top_k=",
            "?top_k=1.5",
        ] {
            let err = params(raw)
                .optional_int("top_k", 1, 100)
                .expect_err("{raw} must be refused");
            assert_eq!(err.field, "top_k");
            assert!(err.detail.contains("between 1 and 100"), "{}", err.detail);
        }
        let mut bag = params("");
        bag.merge_json(br#"{"top_k": true}"#).expect("merge");
        assert!(bag.optional_int("top_k", 1, 100).is_err());
    }

    #[test]
    fn floats_are_range_checked_too() {
        assert_eq!(
            params("?alpha=0.25").optional_float("alpha", 0.0, 1.0),
            Ok(Some(0.25))
        );
        assert_eq!(
            params("?alpha=0").optional_float("alpha", 0.0, 1.0),
            Ok(Some(0.0))
        );
        for raw in ["?alpha=2", "?alpha=-0.5", "?alpha=nope", "?alpha=NaN"] {
            let err = params(raw)
                .optional_float("alpha", 0.0, 1.0)
                .expect_err("must be refused");
            assert_eq!(err.field, "alpha");
        }
        let mut bag = params("");
        bag.merge_json(br#"{"alpha": null}"#).expect("merge");
        assert!(bag.optional_float("alpha", 0.0, 1.0).is_err());
    }

    #[test]
    fn now_accepts_iso_and_epoch_seconds_and_nothing_else() {
        assert_eq!(
            params("?now=1970-01-02").optional_instant("now"),
            Ok(Some(86_400.0))
        );
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
    }

    #[test]
    fn a_rejection_renders_as_422_json() {
        let err = ParamError::new("top_k", "boom");
        assert_eq!(err.to_string(), "top_k: boom");
        let response = err.into_response();
        assert_eq!(response.status(), StatusCode::UNPROCESSABLE_ENTITY);
    }
}
