use lattice_core::pytext::{self, parse_iso, strip, truncate_chars};
use lattice_core::CoreError;
use rusqlite::Connection;
use serde_json::Value;
use sha2::{Digest, Sha256};

use crate::memory_api::wsos;
use crate::shape::{py_str, truthy};

const LAST_NOISE_CURATE_KEY: &str = "last_noise_curate_at";

pub(crate) fn clip(value: &Value, limit: usize) -> String {
    let text = if truthy(value) {
        py_str(value)
    } else {
        String::new()
    };
    truncate_chars(&strip(&text), limit)
}

pub(crate) fn py_text(value: Option<&Value>) -> String {
    match value {
        Some(value) if truthy(value) => py_str(value),
        _ => String::new(),
    }
}

pub(crate) fn py_round_int(value: f64) -> i64 {
    pytext::round_to(value, 0) as i64
}

pub(crate) fn stable_id(prefix: &str, seed: &str) -> String {
    let digest = Sha256::digest(seed.as_bytes());
    let hex: String = digest.iter().map(|byte| format!("{byte:02x}")).collect();
    format!("{prefix}-{}", &hex[..10])
}

pub(crate) fn workflows(state: &Value, workspace_id: Option<&str>) -> Vec<Value> {
    let mut items = wsos::scoped(listify(state.get("workflows")), workspace_id);
    items.reverse();
    items
}

fn listify(value: Option<&Value>) -> Vec<Value> {
    match value {
        Some(Value::Array(items)) => items.clone(),
        _ => Vec::new(),
    }
}

pub(crate) fn pending_reviews(
    state: &Value,
    workspace_id: Option<&str>,
    user_email: &str,
    now: f64,
) -> i64 {
    let mut items = wsos::scoped(listify(state.get("review_items")), workspace_id);
    if !user_email.is_empty() {
        items.retain(|item| match item.get("user_email") {
            None | Some(Value::Null) => true,
            Some(Value::String(owner)) => owner == user_email,
            Some(_) => false,
        });
    }
    items
        .iter()
        .filter(|item| effective_status(item, now) == "pending")
        .count() as i64
}

fn effective_status(item: &Value, now: f64) -> String {
    let status = item.get("status");
    if status.and_then(Value::as_str) != Some("snoozed") {
        let raw = py_text(status);
        return if raw.is_empty() {
            "pending".to_string()
        } else {
            raw
        };
    }
    let until = item
        .get("snoozed_until")
        .and_then(Value::as_str)
        .and_then(|text| parse_iso(Some(text)));
    match until {
        Some(until) if until <= now => "pending".to_string(),
        _ => "snoozed".to_string(),
    }
}

pub(crate) fn last_noise_curate_at(conn: &Connection) -> Option<String> {
    let value: Option<String> = conn
        .query_row(
            "SELECT value FROM graph_meta WHERE key=?",
            [LAST_NOISE_CURATE_KEY],
            |row| row.get(0),
        )
        .ok()?;
    value.filter(|text| !text.is_empty())
}

pub(crate) fn older_than_days(stamp: &str, days: i64, now: f64) -> bool {
    match parse_iso(Some(stamp)) {
        None => true,
        Some(parsed) => (now - parsed) > (days as f64) * 86_400.0,
    }
}

pub(crate) fn parse_ts(value: &Value) -> Option<f64> {
    let text = if truthy(value) {
        py_str(value)
    } else {
        String::new()
    };
    let text = strip(&text).replace('Z', "+00:00");
    if text.is_empty() {
        return None;
    }
    let (naive, offset) = split_offset(&text);
    Some(parse_iso(Some(naive))? - offset)
}

fn split_offset(text: &str) -> (&str, f64) {
    let chars: Vec<(usize, char)> = text.char_indices().collect();
    let found = chars
        .iter()
        .skip(11)
        .rev()
        .find(|(_, c)| *c == '+' || *c == '-');
    let Some(&(at, sign)) = found else {
        return (text, 0.0);
    };
    let designator = &text[at + 1..];
    let mut parts = designator.split(':');
    let hours: f64 = match parts.next().and_then(|part| part.parse::<f64>().ok()) {
        Some(value) => value,
        None => return (text, 0.0),
    };
    let minutes: f64 = parts
        .next()
        .and_then(|part| part.parse::<f64>().ok())
        .unwrap_or(0.0);
    let magnitude = hours * 3600.0 + minutes * 60.0;
    (
        &text[..at],
        if sign == '-' { -magnitude } else { magnitude },
    )
}

#[derive(Debug, Clone)]
pub(crate) struct LocalSource {
    pub(crate) id: String,
    pub(crate) root_path: String,
    pub(crate) label: String,
    pub(crate) watch_enabled: bool,
    pub(crate) indexed: i64,
}

pub(crate) fn local_sources(conn: &Connection) -> Result<Vec<LocalSource>, CoreError> {
    let mut counts: std::collections::HashMap<String, i64> = std::collections::HashMap::new();
    {
        let mut stmt =
            conn.prepare("SELECT source_id, COUNT(*) FROM local_file_index GROUP BY source_id")?;
        let mut rows = stmt.query([])?;
        while let Some(row) = rows.next()? {
            let source_id: Option<String> = row.get(0)?;
            counts.insert(source_id.unwrap_or_default(), row.get(1)?);
        }
    }
    let mut stmt = conn.prepare(
        "SELECT id, root_path, label, watch_enabled FROM knowledge_sources \
         ORDER BY updated_at DESC, id ASC",
    )?;
    let mut rows = stmt.query([])?;
    let mut sources = Vec::new();
    while let Some(row) = rows.next()? {
        let id: String = row.get::<_, Option<String>>("id")?.unwrap_or_default();
        let indexed = counts.get(&id).copied().unwrap_or(0);
        sources.push(LocalSource {
            root_path: row
                .get::<_, Option<String>>("root_path")?
                .unwrap_or_default(),
            label: row.get::<_, Option<String>>("label")?.unwrap_or_default(),
            watch_enabled: row.get::<_, Option<i64>>("watch_enabled")?.unwrap_or(0) != 0,
            indexed,
            id,
        });
    }
    Ok(sources)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn clip_follows_python_truthiness_and_counts_characters() {
        assert_eq!(clip(&json!("  hello  "), 160), "hello");
        assert_eq!(clip(&json!("한글제목입니다"), 3), "한글제");
        assert_eq!(clip(&Value::Null, 10), "");
        assert_eq!(
            clip(&json!(0), 10),
            "",
            "0 is falsy, so `text or \"\"` wins"
        );
        assert_eq!(clip(&json!([]), 10), "");
        assert_eq!(clip(&json!(7), 10), "7");
    }

    #[test]
    fn rounding_is_half_to_even_like_cpython() {
        assert_eq!(py_round_int(0.5), 0);
        assert_eq!(py_round_int(1.5), 2);
        assert_eq!(py_round_int(2.5), 2);
        assert_eq!(py_round_int(84.5), 84);
        assert_eq!(py_round_int(85.5), 86);
    }

    #[test]
    fn a_stable_id_is_the_first_ten_hex_digits_of_the_sha256() {
        assert_eq!(stable_id("pat", ""), "pat-e3b0c44298");
    }

    #[test]
    fn workflows_are_scoped_and_newest_first() {
        let state = json!({"workflows": [
            {"id": "a", "workspace_id": "personal"},
            {"id": "b", "workspace_id": "team"},
            {"id": "c"},
        ]});
        let personal = workflows(&state, Some("personal"));
        assert_eq!(personal.len(), 2, "a legacy record belongs to personal");
        assert_eq!(personal[0]["id"], "c", "reversed()");
        assert_eq!(workflows(&state, None).len(), 3);
        assert!(workflows(&json!({}), Some("personal")).is_empty());
    }

    #[test]
    fn pending_reviews_counts_effective_status() {
        let state = json!({"review_items": [
            {"id": "1", "status": "pending"},
            {"id": "2", "status": "snoozed", "snoozed_until": "2000-01-01T00:00:00"},
            {"id": "3", "status": "snoozed", "snoozed_until": "2999-01-01T00:00:00"},
            {"id": "4", "status": "approved"},
            {"id": "5"},
            {"id": "6", "status": "pending", "user_email": "other@example.com"},
        ]});
        let now = parse_iso(Some("2026-08-14T00:00:00")).expect("stamp");
        assert_eq!(
            pending_reviews(&state, None, "", now),
            4,
            "expired snooze reads pending; a missing status defaults to pending"
        );
        assert_eq!(
            pending_reviews(&state, None, "me@example.com", now),
            3,
            "another user's item is filtered out"
        );
    }

    #[test]
    fn older_than_days_is_fail_open_on_an_unreadable_stamp() {
        let now = parse_iso(Some("2026-08-14T00:00:00")).expect("stamp");
        assert!(older_than_days("not a date", 7, now));
        assert!(older_than_days("2026-08-01T00:00:00", 7, now));
        assert!(!older_than_days("2026-08-10T00:00:00", 7, now));
    }

    #[test]
    fn parse_ts_reads_naive_stamps_as_utc_and_honours_an_offset() {
        let naive = parse_ts(&json!("2026-08-14T00:00:00")).expect("naive");
        let zulu = parse_ts(&json!("2026-08-14T00:00:00Z")).expect("zulu");
        assert_eq!(naive, zulu, "a naive stamp is read as if it were UTC");
        let plus_nine = parse_ts(&json!("2026-08-14T09:00:00+09:00")).expect("offset");
        assert_eq!(plus_nine, naive);
        assert_eq!(parse_ts(&Value::Null), None);
        assert_eq!(parse_ts(&json!("  ")), None);
    }
}
