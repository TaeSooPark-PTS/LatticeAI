//! The small reads and the Python arithmetic the command centre shares.
//!
//! Nothing here is command-centre policy — it is the collaborators
//! `CommandCenterService` reaches through (`WorkspaceOSStore.list_workflows`,
//! `ReviewQueueService.list`, `KnowledgeGraphStore.last_noise_curate_at`,
//! `DiscoveryMixin.local_sources`) reduced to exactly the readings the two
//! routes render, plus the two rounding/parsing helpers whose Python semantics
//! decide branch outcomes rather than decoration.

use lattice_core::pytext::{self, parse_iso, strip, truncate_chars};
use lattice_core::CoreError;
use rusqlite::Connection;
use serde_json::Value;
use sha2::{Digest, Sha256};

use crate::memory_api::wsos;
use crate::shape::{py_str, truthy};

/// `graph/projection/curation.py:_LAST_NOISE_CURATE_KEY`.
const LAST_NOISE_CURATE_KEY: &str = "last_noise_curate_at";

/// `command_center._clip` — `str(text or "").strip()[:limit]`.
///
/// `text or ""` is Python truthiness, so `0`, `false`, `[]` and `{}` all clip to
/// the empty string rather than to their repr. `[:limit]` counts **characters**,
/// which is why a 120-character Korean title is 120 code points and not 360
/// bytes.
pub(crate) fn clip(value: &Value, limit: usize) -> String {
    let text = if truthy(value) {
        py_str(value)
    } else {
        String::new()
    };
    truncate_chars(&strip(&text), limit)
}

/// `str(value or "")` — the spelling `_conversation_section` and
/// `_search_conversations` use for timestamps and roles.
pub(crate) fn py_text(value: Option<&Value>) -> String {
    match value {
        Some(value) if truthy(value) => py_str(value),
        _ => String::new(),
    }
}

/// CPython's `round(value)` — half-to-even on the exact binary value.
///
/// Every health score is `round(ratio * 100)`, and the grade thresholds are
/// `>= 85` / `>= 70` / `>= 50`. Half-away-from-zero would move a score sitting
/// exactly on `x.5` by one and, at the boundary, change the grade word the
/// briefing prints.
pub(crate) fn py_round_int(value: f64) -> i64 {
    pytext::round_to(value, 0) as i64
}

/// `automation_intelligence._stable_id`.
pub(crate) fn stable_id(prefix: &str, seed: &str) -> String {
    let digest = Sha256::digest(seed.as_bytes());
    let hex: String = digest.iter().map(|byte| format!("{byte:02x}")).collect();
    format!("{prefix}-{}", &hex[..10])
}

/// `WorkspaceRunsMixin.list_workflows(workspace_id=…)["workflows"]`.
///
/// `query` is never passed by this family, so the name/steps filter is not
/// reachable from either route and is not reproduced.
pub(crate) fn workflows(state: &Value, workspace_id: Option<&str>) -> Vec<Value> {
    let mut items = wsos::scoped(listify(state.get("workflows")), workspace_id);
    items.reverse();
    items
}

/// `_listify` — a non-list reads as empty rather than raising.
fn listify(value: Option<&Value>) -> Vec<Value> {
    match value {
        Some(Value::Array(items)) => items.clone(),
        _ => Vec::new(),
    }
}

/// `ReviewQueueService.list(status="pending")` — the length, which is all the
/// briefing renders.
///
/// `now` is naive local seconds, because `ReviewQueueService`'s default clock is
/// `datetime.now` and `snoozed_until` is written by `now_iso()`.
pub(crate) fn pending_reviews(
    state: &Value,
    workspace_id: Option<&str>,
    user_email: &str,
    now: f64,
) -> i64 {
    let mut items = wsos::scoped(listify(state.get("review_items")), workspace_id);
    if !user_email.is_empty() {
        // `item.get("user_email") in {None, user_email}` — a record written
        // before accounts existed stays visible to whoever asks.
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

/// `ReviewQueueService._effective_status` — read-time snooze expiry only.
fn effective_status(item: &Value, now: f64) -> String {
    let status = item.get("status");
    // `str(item.get("status")) != "snoozed"` — an absent status stringifies to
    // `"None"`, which is not `"snoozed"`, so it takes the first branch.
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

/// `KnowledgeGraphCurationMixin.last_noise_curate_at` — `None` on any failure.
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

/// `CommandCenterService._older_than_days`.
///
/// An unreadable stamp is stale, because suggesting a dry-run pass is safe.
/// `now` is naive local seconds. One divergence, and it is narrow: a stamp that
/// carries a UTC offset makes [`parse_iso`] answer `None` here (its documented
/// behaviour) and therefore reads as stale, where Python would compare it in its
/// own zone. Nothing writes this key with an offset — `curation.py` stamps it
/// with `now_iso()`, which is naive local.
pub(crate) fn older_than_days(stamp: &str, days: i64, now: f64) -> bool {
    match parse_iso(Some(stamp)) {
        None => true,
        Some(parsed) => (now - parsed) > (days as f64) * 86_400.0,
    }
}

/// `_parse_ts` — ISO text as **UTC** epoch seconds, naive stamps included.
///
/// `datetime.fromisoformat(text.replace("Z", "+00:00"))` followed by
/// `replace(tzinfo=utc)` for a naive result: a naive local stamp is read as if
/// it were UTC, and then compared against a real `datetime.now(timezone.utc)`.
/// On a UTC+9 machine that makes freshly written nodes look nine hours into the
/// future. That is what the health report measures today and the port keeps it.
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

/// Split a trailing `±HH[:MM]` designator off an ISO stamp.
fn split_offset(text: &str) -> (&str, f64) {
    let chars: Vec<(usize, char)> = text.char_indices().collect();
    // Index 10 is the end of `YYYY-MM-DD`; the two hyphens before it are date
    // separators, never offsets.
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

/// One connected knowledge folder, reduced to what a suggestion reads.
#[derive(Debug, Clone)]
pub(crate) struct LocalSource {
    /// `knowledge_sources.id`.
    pub(crate) id: String,
    /// `knowledge_sources.root_path`.
    pub(crate) root_path: String,
    /// `knowledge_sources.label`.
    pub(crate) label: String,
    /// `bool(knowledge_sources.watch_enabled)`.
    pub(crate) watch_enabled: bool,
    /// `sum(file_status.values())` — every `local_file_index` row for this id.
    pub(crate) indexed: i64,
}

/// `DiscoveryMixin.local_sources()["sources"]`, narrowed to five fields.
///
/// The seven columns this drops (`os_type`, `drive_id`, `status`,
/// `include_ocr`, `consent`, the three stamps) are never read on the path from
/// `AutomationIntelligenceService.suggestions` to the briefing's `top`, so
/// materialising them would be weight that still had to be kept true.
///
/// `indexed` is `sum(int(v or 0) for v in file_status.values())` where
/// `file_status` is the per-status histogram: summing every bucket is the row
/// count for that source, which is what one `COUNT(*)` answers.
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
        // sha256("") = e3b0c44298fc1c14...
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
