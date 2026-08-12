//! The small Python behaviours the retrieval port leans on, ported exactly.
//!
//! `round6` is the one that decides rankings: CPython's `round(x, 6)` formats
//! the *exact* value of the double to six decimals with round-half-even and
//! parses the result back, which is precisely what Rust's `{:.6}` + `parse`
//! does. Anything cheaper (`(x * 1e6).round() / 1e6`) rounds half-away-from-zero
//! and disagrees on every tie.

use serde_json::{Map, Value};

/// `round(value, 6)` with CPython's semantics (round-half-even on the exact value).
pub fn round6(value: f64) -> f64 {
    round_to(value, 6)
}

/// `round(value, 4)` — the precision the document-generation scores are stored at.
pub fn round4(value: f64) -> f64 {
    round_to(value, 4)
}

/// `round(value, digits)` with CPython's semantics.
///
/// Public because `lattice-ingest` rounds the watch snapshot's mtimes to three
/// places and must round them the *same* way: half-away-from-zero would report
/// a spurious change on every file whose mtime lands on a tie.
pub fn round_to(value: f64, digits: usize) -> f64 {
    if !value.is_finite() {
        return value;
    }
    format!("{value:.digits$}").parse::<f64>().unwrap_or(value)
}

/// True for every character Python's `re` `\s` and `str.strip()` treat as space.
///
/// `char::is_whitespace` is the Unicode White_Space property; Python's is that
/// plus the C0 separators `\x1c`–`\x1f`, which `str.isspace()` reports as space.
pub fn is_py_space(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

/// `lattice_brain.graph._kg_common.text._clean_text` — collapse runs, strip.
pub fn clean_text(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut pending_space = false;
    for c in text.chars() {
        if is_py_space(c) {
            pending_space = true;
            continue;
        }
        if pending_space && !out.is_empty() {
            out.push(' ');
        }
        pending_space = false;
        out.push(c);
    }
    out
}

/// Python's `text[:n]` — n **characters**, not bytes.
pub fn truncate_chars(text: &str, n: usize) -> String {
    text.chars().take(n).collect()
}

/// Python's `str.rstrip()` — trailing whitespace by *Python's* definition of it,
/// which is why it goes through [`is_py_space`] rather than `str::trim_end`.
pub fn rstrip(text: &str) -> String {
    text.trim_end_matches(is_py_space).to_string()
}

/// Python's `str.strip()`, for the same reason.
pub fn strip(text: &str) -> String {
    text.trim_matches(is_py_space).to_string()
}

/// `lattice_brain.graph.json_utils._safe_loads` — object or `{}`, never an error.
pub fn safe_loads(raw: Option<&str>) -> Map<String, Value> {
    let Some(raw) = raw else { return Map::new() };
    if raw.is_empty() {
        return Map::new();
    }
    match serde_json::from_str::<Value>(raw) {
        Ok(Value::Object(map)) => map,
        _ => Map::new(),
    }
}

/// `lattice_brain.utils.parse_iso`, as naive seconds since the Unix epoch.
///
/// Naive on purpose: every timestamp the graph writes comes from
/// `datetime.now().isoformat(...)` with no offset, and `_recency_score`
/// subtracts it from a naive `datetime.now()`. A stamp that *does* carry an
/// offset makes Python raise `TypeError` out of the middle of `hybrid_search`;
/// this returns `None` (→ "unknown age", never dampened) instead, which is the
/// one deliberate divergence in this function.
pub fn parse_iso(value: Option<&str>) -> Option<f64> {
    let raw = value?.trim();
    if raw.is_empty() {
        return None;
    }
    let bytes: Vec<char> = raw.chars().collect();
    if bytes.len() < 10 {
        return None;
    }
    let date: String = bytes[..10].iter().collect();
    let mut parts = date.split('-');
    let year: i64 = parts.next()?.parse().ok()?;
    let month: u32 = parts.next()?.parse().ok()?;
    let day: u32 = parts.next()?.parse().ok()?;
    if parts.next().is_some() || !(1..=12).contains(&month) || !(1..=31).contains(&day) {
        return None;
    }
    let rest: String = bytes[10..].iter().collect();
    let (hour, minute, second, micros) = parse_clock(&rest)?;
    let days = days_from_civil(year, month, day);
    Some(
        days as f64 * 86_400.0
            + hour as f64 * 3600.0
            + minute as f64 * 60.0
            + second as f64
            + micros,
    )
}

fn parse_clock(rest: &str) -> Option<(u32, u32, u32, f64)> {
    if rest.is_empty() {
        return Some((0, 0, 0, 0.0));
    }
    let head = rest.chars().next()?;
    if head != 'T' && head != 't' && head != ' ' {
        return None;
    }
    let time = &rest[head.len_utf8()..];
    // An offset would make Python return an aware datetime; see the doc comment.
    if time.contains('+') || time.contains('Z') || time.contains('z') || time.contains('-') {
        return None;
    }
    let mut fields = time.split(':');
    let hour: u32 = fields.next()?.parse().ok()?;
    let minute: u32 = fields.next().unwrap_or("0").parse().ok()?;
    let seconds_field = fields.next().unwrap_or("0");
    if fields.next().is_some() {
        return None;
    }
    let (second, micros) = match seconds_field.split_once('.') {
        Some((whole, frac)) => {
            let scaled: f64 = format!("0.{frac}").parse().ok()?;
            (whole.parse::<u32>().ok()?, scaled)
        }
        None => (seconds_field.parse::<u32>().ok()?, 0.0),
    };
    if hour > 23 || minute > 59 || second > 59 {
        return None;
    }
    Some((hour, minute, second, micros))
}

/// Days from 1970-01-01 for a proleptic Gregorian date (Hinnant's algorithm).
fn days_from_civil(year: i64, month: u32, day: u32) -> i64 {
    let y = if month <= 2 { year - 1 } else { year };
    let era = if y >= 0 { y } else { y - 399 } / 400;
    let yoe = y - era * 400;
    let m = month as i64;
    let doy = (153 * (if m > 2 { m - 3 } else { m + 9 }) + 2) / 5 + day as i64 - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146_097 + doe - 719_468
}

/// `lattice_brain.graph._kg_fsutil._recency_score` with an explicit `now`.
///
/// `now` is a parameter rather than a call to the clock because a golden file
/// that depends on wall-clock time is not a golden file.
pub fn recency_score(updated_at: Option<&str>, now_secs: f64, half_life_days: f64) -> f64 {
    let Some(stamp) = parse_iso(updated_at) else {
        return 0.0;
    };
    let age_days = ((now_secs - stamp) / 86_400.0).max(0.0);
    let decay = std::f64::consts::LN_2 / half_life_days.max(0.1);
    (-decay * age_days).exp()
}

/// `lattice_brain.graph._kg_common.text.citation_locator`.
pub fn citation_locator(chunk_metadata: &Map<String, Value>) -> String {
    let mut parts: Vec<String> = Vec::new();
    let heading = py_str_or_empty(chunk_metadata.get("heading_path"));
    let heading = heading.trim();
    if !heading.is_empty() {
        parts.push(heading.to_string());
    }
    let page = py_int_or_zero(chunk_metadata.get("page"));
    if page > 0 {
        let page_end = py_int_or_zero(chunk_metadata.get("page_end"));
        parts.push(if page_end > page {
            format!("p.{page}–{page_end}")
        } else {
            format!("p.{page}")
        });
    }
    parts.join(" · ")
}

/// `str(value or "")` for the JSON values a metadata blob can hold.
fn py_str_or_empty(value: Option<&Value>) -> String {
    match value {
        Some(Value::String(text)) => text.clone(),
        Some(Value::Number(number)) if number.as_f64() != Some(0.0) => number.to_string(),
        Some(Value::Bool(true)) => "True".to_string(),
        _ => String::new(),
    }
}

/// `int(value) if value is not None else 0`, with Python's failure modes → 0.
fn py_int_or_zero(value: Option<&Value>) -> i64 {
    match value {
        Some(Value::Number(number)) => number.as_f64().map(|v| v.trunc() as i64).unwrap_or(0),
        Some(Value::String(text)) => text.trim().parse::<i64>().unwrap_or(0),
        Some(Value::Bool(flag)) => i64::from(*flag),
        _ => 0,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Expectations produced by CPython 3.14 `round(x, 6)`; the hex forms pin the
    /// exact doubles so no decimal literal can drift them.
    #[test]
    fn round6_matches_cpython() {
        let cases: [(f64, f64); 10] = [
            (5e-07, 0.0),
            (1.5e-06, 2e-06),
            (2.5e-06, 3e-06),
            (2.6535895, 2.653589),
            (1.0 / 3.0, 0.333333),
            (0.1234565, 0.123456),
            (0.1234575, 0.123457),
            (0.5 * 0.35 + 0.5 * 0.16666666666666666, 0.258333),
            (0.6 * 0.9999995, 0.6),
            (0.9999999999, 1.0),
        ];
        for (input, expected) in cases {
            assert_eq!(
                round6(input).to_bits(),
                expected.to_bits(),
                "round6({input:?}) = {} want {expected:?}",
                round6(input)
            );
        }
        assert_eq!(round6(0.0).to_bits(), 0.0f64.to_bits());
        assert!(round6(f64::NAN).is_nan());
        assert_eq!(round6(f64::INFINITY), f64::INFINITY);
        assert_eq!(round6(123456.7890625), 123456.789062);
        // `round(x, 4)` — the document-generation scores' precision.
        assert_eq!(
            round4(0.65975),
            0.6597,
            "half-even, not half-away-from-zero"
        );
        assert_eq!(round4(0.5 + 0.3 * (1.0 / 3.0) + 0.2), 0.8);
        assert_eq!(round4(1.0 / 3.0), 0.3333);
        assert!(round4(f64::NAN).is_nan());
    }

    #[test]
    fn rstrip_and_strip_follow_pythons_definition_of_space() {
        assert_eq!(rstrip("a b \n\t"), "a b");
        assert_eq!(rstrip("a\u{1c}"), "a", "a C0 separator is space to Python");
        assert_eq!(rstrip("   "), "");
        assert_eq!(strip("  회의록  "), "회의록");
        assert_eq!(strip(""), "");
    }

    #[test]
    fn clean_text_collapses_and_strips() {
        assert_eq!(clean_text("  a \n\t b  "), "a b");
        assert_eq!(clean_text(""), "");
        assert_eq!(clean_text("   "), "");
        assert_eq!(clean_text("회의\u{1c}록"), "회의 록");
        assert_eq!(truncate_chars("회의록입니다", 3), "회의록");
        assert_eq!(truncate_chars("ab", 9), "ab");
    }

    #[test]
    fn safe_loads_only_accepts_objects() {
        assert!(safe_loads(None).is_empty());
        assert!(safe_loads(Some("")).is_empty());
        assert!(safe_loads(Some("[1,2]")).is_empty());
        assert!(safe_loads(Some("{oops")).is_empty());
        let map = safe_loads(Some(r#"{"a": 1}"#));
        assert_eq!(map.get("a").and_then(Value::as_i64), Some(1));
    }

    #[test]
    fn parse_iso_handles_the_shapes_the_graph_writes() {
        assert_eq!(parse_iso(Some("1970-01-01T00:00:00")), Some(0.0));
        assert_eq!(parse_iso(Some("1970-01-02")), Some(86_400.0));
        assert_eq!(parse_iso(Some("1970-01-01 00:01")), Some(60.0));
        assert_eq!(parse_iso(Some("1970-01-01T00:00:01.500000")), Some(1.5));
        assert_eq!(
            parse_iso(Some("2026-05-01T09:00:00")),
            Some(1_777_626_000.0)
        );
        for bad in [
            "",
            "   ",
            "nope",
            "2026-13-01T00:00:00",
            "2026-05-01T25:00:00",
            "2026-05-01X00:00:00",
            "2026-05-01T00:00:00+09:00",
            "2026-05-01T00:00:00Z",
            "2026-05-01T00:00:00:00",
            "20xx-05-01T00:00:00",
            "2026-05-01T0x:00:00",
        ] {
            assert_eq!(parse_iso(Some(bad)), None, "{bad} should not parse");
        }
        assert_eq!(parse_iso(None), None);
    }

    #[test]
    fn recency_score_decays_by_half_life() {
        let now = parse_iso(Some("2026-05-15T00:00:00")).unwrap();
        let fresh = recency_score(Some("2026-05-15T00:00:00"), now, 14.0);
        assert!((fresh - 1.0).abs() < 1e-15);
        let half = recency_score(Some("2026-05-01T00:00:00"), now, 14.0);
        assert!((half - 0.5).abs() < 1e-12, "{half}");
        // Unparseable → 0.0, future → clamped to "now".
        assert_eq!(recency_score(Some("nope"), now, 14.0), 0.0);
        assert_eq!(recency_score(None, now, 14.0), 0.0);
        assert_eq!(recency_score(Some("2027-01-01T00:00:00"), now, 14.0), 1.0);
        assert!(recency_score(Some("2026-05-01T00:00:00"), now, 0.0) < 1e-12);
    }

    #[test]
    fn citation_locator_only_claims_what_it_knows() {
        let mut meta = Map::new();
        assert_eq!(citation_locator(&meta), "");
        meta.insert("page".into(), Value::from(3));
        assert_eq!(citation_locator(&meta), "p.3");
        meta.insert("page_end".into(), Value::from(5));
        assert_eq!(citation_locator(&meta), "p.3–5");
        meta.insert("heading_path".into(), Value::from(" 1. 배경 "));
        assert_eq!(citation_locator(&meta), "1. 배경 · p.3–5");
        meta.insert("page".into(), Value::from("nope"));
        assert_eq!(citation_locator(&meta), "1. 배경");
        meta.insert("page".into(), Value::from(true));
        meta.insert("page_end".into(), Value::Null);
        assert_eq!(citation_locator(&meta), "1. 배경 · p.1");
        meta.insert("heading_path".into(), Value::from(7));
        assert_eq!(citation_locator(&meta), "7 · p.1");
        meta.insert("heading_path".into(), Value::Bool(true));
        assert_eq!(citation_locator(&meta), "True · p.1");
        meta.insert("heading_path".into(), Value::from(0));
        assert_eq!(citation_locator(&meta), "p.1");
    }
}
