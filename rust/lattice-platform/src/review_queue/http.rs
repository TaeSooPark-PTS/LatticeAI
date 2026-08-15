//! Auth, JSON, and time helpers shared by the R7 families.

#![allow(
    dead_code,
    unused_imports,
    unused_variables,
    unused_assignments,
    unused_mut,
    private_interfaces,
    clippy::result_large_err,
    clippy::needless_lifetimes,
    clippy::too_many_arguments,
    clippy::type_complexity,
    clippy::collapsible_if,
    clippy::needless_as_bytes,
    clippy::redundant_closure,
    clippy::needless_return,
    clippy::manual_clamp,
    clippy::ptr_arg,
    clippy::unnecessary_sort_by,
    clippy::result_unit_err,
    clippy::useless_vec,
    clippy::uninlined_format_args,
    clippy::manual_contains,
    clippy::needless_borrows_for_generic_args,
    clippy::implicit_clone,
    clippy::unnecessary_map_or,
    clippy::match_like_matches_macro,
    clippy::manual_range_contains,
    clippy::derivable_impls,
    clippy::needless_pass_by_ref_mut,
    clippy::redundant_guards,
    clippy::map_identity,
    clippy::iter_overeager_cloned,
    clippy::explicit_auto_deref,
    clippy::bool_comparison,
    clippy::nonminimal_bool,
    clippy::if_same_then_else,
    clippy::question_mark,
    clippy::single_char_pattern,
    clippy::manual_pattern_char_comparison,
    clippy::manual_is_ascii_check,
    clippy::repeat_once,
    clippy::unused_self,
    clippy::module_inception
)]
use std::time::{SystemTime, UNIX_EPOCH};

use axum::http::{HeaderMap, StatusCode};
use axum::response::Response;
use lattice_auth::response::json_response;
use lattice_auth::{Identity, OrderedMap};
use lattice_core::messages::{self, LANGUAGE_HEADER};
use lattice_core::worker::WorkerSeamError;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

use super::{GovernanceState, DEFAULT_WORKSPACE_ID};

// ── auth / http helpers ───────────────────────────────────────────────────

#[derive(Debug, Default, serde::Deserialize)]
pub(crate) struct ListQuery {
    pub(crate) status: Option<String>,
    pub(crate) source: Option<String>,
}

pub(crate) fn require_user(
    state: &GovernanceState,
    headers: &HeaderMap,
) -> Result<Identity, Response> {
    state.auth.require_user(headers)
}

pub(crate) fn require_admin(
    state: &GovernanceState,
    headers: &HeaderMap,
) -> Result<Identity, Response> {
    state.auth.require_admin(headers)
}

pub(crate) fn gate_read(headers: &HeaderMap) -> Option<String> {
    requested_workspace(headers).or_else(|| Some(DEFAULT_WORKSPACE_ID.to_string()))
}

pub(crate) fn gate_write(headers: &HeaderMap) -> Option<String> {
    gate_read(headers)
}

fn requested_workspace(headers: &HeaderMap) -> Option<String> {
    headers
        .get("x-workspace-id")
        .and_then(|v| v.to_str().ok())
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string)
}

pub(crate) fn language(headers: &HeaderMap) -> &'static str {
    messages::resolve_language(
        headers.get(LANGUAGE_HEADER).and_then(|v| v.to_str().ok()),
        headers
            .get(axum::http::header::ACCEPT_LANGUAGE)
            .and_then(|v| v.to_str().ok()),
    )
}

pub(crate) fn localized(headers: &HeaderMap, status: StatusCode, id: &str) -> Response {
    let err = messages::http_error(status.as_u16(), id, language(headers), &[]);
    let (code, body) = err.into_response_parts();
    json_status(StatusCode::from_u16(code).unwrap_or(status), &body)
}

pub(crate) fn not_found_localized(headers: &HeaderMap, id: &str) -> Response {
    localized(headers, StatusCode::NOT_FOUND, id)
}

pub(crate) fn http_detail(status: StatusCode, detail: &str) -> Response {
    let mut body = OrderedMap::new();
    body.insert("detail", json!(detail));
    json_status(status, &into_value(body))
}

pub(crate) fn json_ok(body: &OrderedMap) -> Response {
    json_status(StatusCode::OK, &into_value(body.clone()))
}

pub(crate) fn json_status(status: StatusCode, body: &Value) -> Response {
    let text = serde_json::to_string(body).unwrap_or_else(|_| "{\"detail\":\"error\"}".into());
    json_response(status, &text, None)
}

pub(crate) fn parse_object(bytes: &[u8]) -> Result<serde_json::Map<String, Value>, Response> {
    if bytes.is_empty() {
        return Ok(serde_json::Map::new());
    }
    match serde_json::from_slice::<Value>(bytes) {
        Ok(Value::Object(map)) => Ok(map),
        Ok(other) => Err(pydantic_model_type(other)),
        Err(error) => Err(pydantic_json_invalid(&error.to_string())),
    }
}

pub(crate) fn parse_object_optional(
    bytes: &[u8],
) -> Result<serde_json::Map<String, Value>, Response> {
    if bytes.is_empty() {
        return Ok(serde_json::Map::new());
    }
    parse_object(bytes)
}

pub(crate) fn require_field(
    parsed: &serde_json::Map<String, Value>,
    name: &str,
) -> Result<(), Response> {
    if parsed.contains_key(name) {
        Ok(())
    } else {
        Err(pydantic_missing(name, Value::Object(parsed.clone())))
    }
}

pub(crate) fn string_field(parsed: &serde_json::Map<String, Value>, name: &str) -> String {
    match parsed.get(name) {
        Some(Value::String(text)) => text.clone(),
        Some(other) => other.as_str().unwrap_or("").to_string(),
        None => String::new(),
    }
}

pub(crate) fn string_field_or(
    parsed: &serde_json::Map<String, Value>,
    name: &str,
    default: &str,
) -> String {
    if parsed.contains_key(name) {
        string_field(parsed, name)
    } else {
        default.to_string()
    }
}

fn pydantic_missing(name: &str, input: Value) -> Response {
    let mut entry = OrderedMap::new();
    entry.insert("type", json!("missing"));
    entry.insert("loc", json!(["body", name]));
    entry.insert("msg", json!("Field required"));
    entry.insert("input", input);
    let mut body = OrderedMap::new();
    body.insert("detail", Value::Array(vec![into_value(entry)]));
    json_status(StatusCode::UNPROCESSABLE_ENTITY, &into_value(body))
}

fn pydantic_model_type(input: Value) -> Response {
    let mut entry = OrderedMap::new();
    entry.insert("type", json!("model_attributes_type"));
    entry.insert("loc", json!(["body"]));
    entry.insert(
        "msg",
        json!("Input should be a valid dictionary or object to extract fields from"),
    );
    entry.insert("input", input);
    let mut body = OrderedMap::new();
    body.insert("detail", Value::Array(vec![into_value(entry)]));
    json_status(StatusCode::UNPROCESSABLE_ENTITY, &into_value(body))
}

fn pydantic_json_invalid(error: &str) -> Response {
    let mut ctx = OrderedMap::new();
    ctx.insert("error", json!(error));
    let mut entry = OrderedMap::new();
    entry.insert("type", json!("json_invalid"));
    entry.insert("loc", json!(["body", 0]));
    entry.insert("msg", json!("JSON decode error"));
    entry.insert("input", json!({}));
    entry.insert("ctx", into_value(ctx));
    let mut body = OrderedMap::new();
    body.insert("detail", Value::Array(vec![into_value(entry)]));
    json_status(StatusCode::UNPROCESSABLE_ENTITY, &into_value(body))
}

pub(crate) fn into_value(map: OrderedMap) -> Value {
    serde_json::to_value(map).unwrap_or(Value::Null)
}

pub(crate) fn now_iso() -> String {
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    format_unix_naive(secs)
}

fn format_unix_naive(secs: u64) -> String {
    let days = secs / 86400;
    let rem = secs % 86400;
    let hour = rem / 3600;
    let min = (rem % 3600) / 60;
    let sec = rem % 60;
    let (year, month, day) = civil_from_days(days as i64);
    format!("{year:04}-{month:02}-{day:02}T{hour:02}:{min:02}:{sec:02}")
}

fn civil_from_days(mut days: i64) -> (i32, u32, u32) {
    days += 719_468;
    let era = if days >= 0 { days } else { days - 146_096 } / 146_097;
    let doe = (days - era * 146_097) as u64;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    let y = yoe as i64 + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = if m <= 2 { y + 1 } else { y };
    (y as i32, m as u32, d as u32)
}

/// One parsed ISO-8601 stamp.
///
/// `naive_secs` is the wall-clock reading with the offset *ignored*, which is
/// what a naive `datetime.fromisoformat` produces; `utc_secs` is the instant
/// the stamp actually names. They differ only for an offset-aware value, and
/// keeping both is why an aware `snoozed_until` can be compared against the
/// naive clock instead of raising (v11.6.0 answered 500 here).
pub(crate) struct ParsedIso {
    pub(crate) aware: bool,
    pub(crate) naive_secs: i64,
    pub(crate) utc_secs: i64,
}

pub(crate) fn parse_iso(value: &str) -> Option<ParsedIso> {
    if value.is_empty() {
        return None;
    }
    let (core, offset, aware) = split_offset(value);
    let (date, time) = core.split_once('T').or_else(|| core.split_once(' '))?;
    let mut d = date.split('-');
    let year: i32 = d.next()?.parse().ok()?;
    let month: u32 = d.next()?.parse().ok()?;
    let day: u32 = d.next()?.parse().ok()?;
    let mut t = time.split(':');
    let hour: u32 = t.next()?.parse().ok()?;
    let min: u32 = t.next()?.parse().ok()?;
    let sec_s = t.next().unwrap_or("0");
    let sec: u32 = sec_s.split('.').next().unwrap_or("0").parse().ok()?;
    let naive_secs = ymd_hms_to_secs(year, month, day, hour, min, sec);
    Some(ParsedIso {
        aware,
        naive_secs,
        utc_secs: naive_secs - offset,
    })
}

/// Split `<wall clock>` from its trailing UTC offset: the reading, the offset
/// in seconds, and whether the stamp carried an offset at all.
///
/// `Z` and `+HH:MM` are unambiguous. A trailing `-HH:MM` is only an offset when
/// the dash sits past the date (index > 9), which is the same guard the naive
/// parse has always used — `2099-01-01T00:00:00` must not lose its day.
fn split_offset(value: &str) -> (&str, i64, bool) {
    if let Some(core) = value.strip_suffix('Z').or_else(|| value.strip_suffix('z')) {
        return (core, 0, true);
    }
    let sign_at = value
        .rfind('+')
        .map(|idx| (idx, 1i64))
        .or_else(|| value.rfind('-').filter(|idx| *idx > 9).map(|idx| (idx, -1)));
    let Some((idx, sign)) = sign_at else {
        return (value, 0, false);
    };
    let (core, raw) = value.split_at(idx);
    let digits: String = raw[1..].chars().filter(char::is_ascii_digit).collect();
    let part =
        |range: std::ops::Range<usize>| digits.get(range).and_then(|text| text.parse::<i64>().ok());
    let offset = match digits.len() {
        // `+HH`
        1 | 2 => digits.parse::<i64>().ok().map(|hours| hours * 3600),
        // `+HHMM` / `+HH:MM`, and `+HHMMSS`, which Python also accepts.
        4 | 6 => match (part(0..2), part(2..4)) {
            (Some(hours), Some(minutes)) => {
                Some(hours * 3600 + minutes * 60 + part(4..6).unwrap_or(0))
            }
            _ => None,
        },
        _ => None,
    };
    // An unreadable offset is treated as absent rather than guessed at, so the
    // stamp still parses as the wall clock it plainly reads as.
    (core, sign * offset.unwrap_or(0), offset.is_some())
}

fn ymd_hms_to_secs(year: i32, month: u32, day: u32, hour: u32, min: u32, sec: u32) -> i64 {
    let days = days_from_civil(year, month, day);
    days * 86400 + i64::from(hour) * 3600 + i64::from(min) * 60 + i64::from(sec)
}

fn days_from_civil(mut year: i32, month: u32, day: u32) -> i64 {
    if month <= 2 {
        year -= 1;
    }
    let era = if year >= 0 { year } else { year - 399 } / 400;
    let yoe = (year - era * 400) as u32;
    let mp = if month > 2 { month - 3 } else { month + 9 };
    let doy = (153 * mp + 2) / 5 + day - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    i64::from(era) * 146_097 + i64::from(doe) - 719_468
}

pub(crate) fn naive_now_secs() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs() as i64
}

pub(crate) fn json_hash(value: &Value) -> String {
    let payload = serde_json::to_string(value).unwrap_or_else(|_| "null".into());
    // Python uses sort_keys=True, ensure_ascii=False. serde_json::to_string
    // on a Value preserves the Value's own key order (BTreeMap = sorted).
    let digest = Sha256::digest(payload.as_bytes());
    digest.iter().map(|b| format!("{b:02x}")).collect()
}

pub(crate) fn detail_to_string(detail: &Value) -> String {
    match detail {
        Value::String(text) => text.clone(),
        other => serde_json::to_string(other).unwrap_or_else(|_| other.to_string()),
    }
}

pub(crate) fn map_str<'a>(map: &'a OrderedMap, key: &str) -> &'a str {
    map.get(key).and_then(Value::as_str).unwrap_or("")
}

pub(crate) fn sha256_text(content: &str) -> String {
    let digest = Sha256::digest(content.as_bytes());
    digest.iter().map(|b| format!("{b:02x}")).collect()
}

pub(crate) fn map_worker_error(error: WorkerSeamError) -> Response {
    if let Some(status) = error.status() {
        http_detail(
            StatusCode::from_u16(status).unwrap_or(StatusCode::BAD_GATEWAY),
            &error.to_string(),
        )
    } else {
        http_detail(StatusCode::BAD_GATEWAY, &error.to_string())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn parsed(text: &str) -> ParsedIso {
        parse_iso(text).unwrap_or_else(|| panic!("{text} must parse"))
    }

    #[test]
    fn a_naive_stamp_reads_as_itself_in_both_readings() {
        let value = parsed("2099-01-01T00:00:00");
        assert!(!value.aware);
        assert_eq!(value.naive_secs, value.utc_secs);
        assert_eq!(value.naive_secs, 4_070_908_800);
        // Space separator and fractional seconds, as Python accepts them.
        assert_eq!(parsed("2099-01-01 00:00:00.500").naive_secs, 4_070_908_800);
        // Minutes only.
        assert_eq!(parsed("2099-01-01T00:00").naive_secs, 4_070_908_800);
    }

    #[test]
    fn an_offset_is_removed_from_the_wall_clock_to_give_the_instant() {
        let utc = parsed("2099-01-01T00:00:00+00:00");
        assert!(utc.aware);
        assert_eq!(utc.utc_secs, 4_070_908_800);
        assert_eq!(utc.utc_secs, utc.naive_secs);

        // 09:00 in +09:00 is midnight UTC; 19:00 the previous day in -05:00 is
        // the same instant. Both used to be a 500 rather than a comparison.
        for text in [
            "2099-01-01T09:00:00+09:00",
            "2099-01-01T09:00:00+0900",
            "2098-12-31T19:00:00-05:00",
        ] {
            let value = parsed(text);
            assert!(value.aware, "{text}");
            assert_eq!(value.utc_secs, 4_070_908_800, "{text}");
            assert_ne!(value.naive_secs, value.utc_secs, "{text}");
        }

        for text in ["2099-01-01T00:00:00Z", "2099-01-01T00:00:00z"] {
            let value = parsed(text);
            assert!(value.aware, "{text}");
            assert_eq!(value.utc_secs, 4_070_908_800, "{text}");
        }
        // Seconds in the offset, and an hours-only offset.
        assert_eq!(parsed("2099-01-01T00:00:30+000030").utc_secs, 4_070_908_800);
        assert_eq!(parsed("2099-01-01T09:00:00+09").utc_secs, 4_070_908_800);
    }

    #[test]
    fn an_unreadable_offset_leaves_the_wall_clock_alone_and_junk_does_not_parse() {
        // Five digits is not an offset shape; the reading stays the wall clock
        // rather than becoming a guess, and the stamp does not claim to be
        // offset-aware when nothing readable said so.
        let value = parsed("2099-01-01T00:00:00+09000");
        assert_eq!(value.utc_secs, value.naive_secs);
        assert!(!value.aware);
        assert!(parse_iso("").is_none());
        assert!(parse_iso("next tuesday").is_none());
        assert!(parse_iso("2099-01-01").is_none());
        assert!(parse_iso("2099-13-XXT00:00:00").is_none());
    }
}
