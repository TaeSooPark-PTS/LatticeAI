use lattice_core::parse_iso;
use serde_json::Value;

use super::{SortBy, TemporalRanking};

pub const TEMPORAL_PARAM_KEYS: [&str; 4] = ["sort_by", "include_historical", "since", "until"];

pub type TemporalParamError = (&'static str, String);

fn text_of(key: &'static str, value: Option<&Value>) -> Result<Option<String>, TemporalParamError> {
    match value {
        None | Some(Value::Null) => Ok(None),
        Some(Value::String(text)) => Ok(Some(text.trim().to_string()).filter(|t| !t.is_empty())),
        Some(other) => Err((key, format!("{key} must be a string, got {other}"))),
    }
}

fn flag_of(value: Option<&Value>) -> Result<bool, TemporalParamError> {
    let key = "include_historical";
    match value {
        None | Some(Value::Null) => Ok(false),
        Some(Value::Bool(flag)) => Ok(*flag),
        Some(Value::String(text)) => match text.trim().to_ascii_lowercase().as_str() {
            "1" | "true" | "yes" | "on" => Ok(true),
            "0" | "false" | "no" | "off" | "" => Ok(false),
            _ => Err((key, format!("{key} must be a boolean, got {text:?}"))),
        },
        Some(other) => Err((key, format!("{key} must be a boolean, got {other}"))),
    }
}

fn instant_of(
    key: &'static str,
    value: Option<&Value>,
) -> Result<Option<String>, TemporalParamError> {
    let Some(text) = text_of(key, value)? else {
        return Ok(None);
    };
    if parse_iso(Some(&text)).is_none() {
        return Err((
            key,
            format!("{key} must be a naive ISO-8601 date or timestamp (2026-08-01 or 2026-08-01T12:00:00), got {text:?}"),
        ));
    }
    Ok(Some(text))
}

pub fn ranking_from_lookup<'a>(
    get: impl Fn(&str) -> Option<&'a Value>,
) -> Result<Option<TemporalRanking>, TemporalParamError> {
    if TEMPORAL_PARAM_KEYS.iter().all(|key| get(key).is_none()) {
        return Ok(None);
    }
    let sort_by = text_of("sort_by", get("sort_by"))?;
    let sort_by = SortBy::parse(sort_by.as_deref()).map_err(|detail| ("sort_by", detail))?;
    Ok(Some(TemporalRanking::from_env().with_request(
        sort_by,
        flag_of(get("include_historical"))?,
        instant_of("since", get("since"))?,
        instant_of("until", get("until"))?,
    )))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn parse(body: Value) -> Result<Option<TemporalRanking>, TemporalParamError> {
        ranking_from_lookup(|key| body.get(key))
    }

    #[test]
    fn absent_keys_keep_the_lane_on_its_parity_contract() {
        assert_eq!(parse(json!({"q": "x"})), Ok(None));
        let ranking =
            parse(json!({"sort_by": "newest", "include_historical": "yes", "since": "2026-01-01"}))
                .unwrap()
                .unwrap();
        assert_eq!(ranking.sort_by, SortBy::Newest);
        assert!(ranking.include_historical);
        assert_eq!(ranking.since.as_deref(), Some("2026-01-01"));
        assert_eq!(parse(json!({"until": null})).unwrap().unwrap().until, None);
        assert_eq!(
            parse(json!({"sort_by": "oldest"})).unwrap_err().0,
            "sort_by"
        );
        assert_eq!(parse(json!({"sort_by": 3})).unwrap_err().0, "sort_by");
        assert_eq!(
            parse(json!({"include_historical": "maybe"})).unwrap_err().0,
            "include_historical"
        );
        assert_eq!(
            parse(json!({"include_historical": 1})).unwrap_err().0,
            "include_historical"
        );
        assert!(
            !parse(json!({"include_historical": false}))
                .unwrap()
                .unwrap()
                .include_historical
        );
        assert_eq!(parse(json!({"since": "last week"})).unwrap_err().0, "since");
    }
}
