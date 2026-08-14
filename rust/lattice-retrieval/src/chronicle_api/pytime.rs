//! The date arithmetic `latticeai/services/chronicle.py` does, ported.
//!
//! Three Python functions decide which day a row belongs to, and every count
//! the chronicle reports is downstream of them:
//!
//! * `lattice_brain.utils.parse_iso` — `datetime.fromisoformat`, with every
//!   failure (including an empty stamp) flattened to `None`;
//! * `_local` — an offset-aware stamp is moved into the configured timezone and
//!   stripped of its offset; a naive one is taken as already being in it,
//!   because that is what the store writes;
//! * `parse_day` / `parse_timestamp` — the two 422 gates on the router.
//!
//! **The configured timezone.** Python reads `LATTICE_TZ` / `LTCAI_TZ` and
//! falls back to the system zone. There is no IANA database in this workspace,
//! so the conversion here goes through `localtime_r(3)` — the system zone,
//! which is Python's answer whenever those two variables are unset (the
//! default) or name the machine's own zone. It only matters for a stamp that
//! *carries* an offset, and nothing in the product writes one: every stored
//! stamp comes from `now_iso()`, which is naive. The one reachable input is the
//! `ts` query parameter of `GET /api/chronicle/as-of`.
//!
//! **What `fromisoformat` accepts here.** CPython 3.11 widened it to most of
//! ISO 8601. This port covers what a stored stamp or a client can plausibly
//! carry — extended and basic dates (`2026-08-11`, `20260811`), any single
//! separator character, extended and basic times (`09`, `09:00`, `09:00:00`,
//! `090000`) with a `.`/`,` fraction truncated to microseconds, and `Z` or
//! `±HH[:MM[:SS]]` offsets. Week dates (`2026-W32-1`) and ordinal dates are
//! **not** accepted; Python would parse them, this answers `None`, which the
//! router renders as the same 422 a client gets for any other unreadable
//! stamp. Recorded rather than hidden.

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
/// A naive civil timestamp — Python's `datetime` with `tzinfo=None`.
///
/// The field order is the derive order, so `Ord` is chronological and
/// `min()`/`max()` over a lane of moments answer what Python's do.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub struct Naive {
    /// Proleptic Gregorian year, `1..=9999` as `datetime` allows.
    pub year: i32,
    /// `1..=12`.
    pub month: u32,
    /// `1..=31`, validated against the month.
    pub day: u32,
    /// `0..=23`.
    pub hour: u32,
    /// `0..=59`.
    pub minute: u32,
    /// `0..=59`.
    pub second: u32,
    /// `0..=999_999`.
    pub micro: u32,
}

impl Naive {
    /// `moment.date().isoformat()`.
    pub fn date_iso(&self) -> String {
        format!("{:04}-{:02}-{:02}", self.year, self.month, self.day)
    }

    /// `moment.isoformat(timespec="seconds")` — microseconds are dropped.
    pub fn iso_seconds(&self) -> String {
        format!(
            "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}",
            self.year, self.month, self.day, self.hour, self.minute, self.second
        )
    }

    /// Seconds since the Unix epoch, reading the fields as UTC.
    fn epoch_secs(&self) -> i64 {
        days_from_civil(self.year as i64, self.month, self.day) * 86_400
            + self.hour as i64 * 3_600
            + self.minute as i64 * 60
            + self.second as i64
    }

    fn from_epoch_utc(secs: i64, micro: u32) -> Self {
        let days = secs.div_euclid(86_400);
        let rest = secs.rem_euclid(86_400);
        let (year, month, day) = civil_from_days(days);
        Self {
            year: year as i32,
            month,
            day,
            hour: (rest / 3_600) as u32,
            minute: ((rest % 3_600) / 60) as u32,
            second: (rest % 60) as u32,
            micro,
        }
    }
}

/// What `datetime.fromisoformat` returns: a civil timestamp, maybe with an
/// offset attached.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Parsed {
    /// The wall-clock fields as written.
    pub naive: Naive,
    /// `tzinfo.utcoffset()` in seconds, or `None` for a naive stamp.
    pub offset_secs: Option<i32>,
}

/// `lattice_brain.utils.parse_iso` — every failure is `None`, never an error.
pub fn parse_iso(value: &str) -> Option<Parsed> {
    if value.is_empty() {
        // `if not value: return None` — the falsy check, before the parse.
        return None;
    }
    fromisoformat(value)
}

/// `_local(moment)` — the moment as naive wall-clock in the configured zone.
pub fn local(parsed: Parsed) -> Naive {
    let Some(offset) = parsed.offset_secs else {
        return parsed.naive;
    };
    let utc = parsed.naive.epoch_secs() - offset as i64;
    // `localtime_r` only fails on an instant the platform cannot represent;
    // reading it as UTC is the honest remainder, not a silent shift.
    let fields = system_local(utc).unwrap_or_else(|| Naive::from_epoch_utc(utc, 0));
    Naive {
        micro: parsed.naive.micro,
        ..fields
    }
}

/// `_moment(value)` — a stored stamp as local wall-clock, or `None`.
pub fn moment(value: &str) -> Option<Naive> {
    parse_iso(strip(value).as_str()).map(local)
}

/// `_day_of(value)` — `YYYY-MM-DD` in the configured zone, or `None`.
pub fn day_of(value: &str) -> Option<String> {
    moment(value).map(|moment| moment.date_iso())
}

/// `str.strip()` with Python's whitespace set.
pub fn strip(text: &str) -> String {
    lattice_core::pytext::strip(text)
}

/// `chronicle.parse_day` — `YYYY-MM-DD` normalized, or `Err` for the 422.
///
/// Python matches `\d{4}-\d{2}-\d{2}` with `re`, whose `\d` is Unicode-aware,
/// then hands the text to `date.fromisoformat`, which is ASCII-only. A
/// non-ASCII digit therefore passes the regex and fails the constructor —
/// `ValueError` either way, which is why this checks ASCII digits directly.
pub fn parse_day(value: &str) -> Result<String, ()> {
    let text = strip(value);
    let chars: Vec<char> = text.chars().collect();
    if chars.len() != 10 || chars[4] != '-' || chars[7] != '-' {
        return Err(());
    }
    let (year, month, day) = date_fields(&chars, true).ok_or(())?;
    check_date(year, month, day).ok_or(())?;
    Ok(format!("{year:04}-{month:02}-{day:02}"))
}

/// `chronicle.parse_timestamp` — the store's own stamp format, or the 422.
pub fn parse_timestamp(value: &str) -> Result<String, ()> {
    let parsed = parse_iso(strip(value).as_str()).ok_or(())?;
    Ok(local(parsed).iso_seconds())
}

// ── datetime.fromisoformat ──────────────────────────────────────────────────

fn fromisoformat(text: &str) -> Option<Parsed> {
    let chars: Vec<char> = text.chars().collect();
    let extended = chars.len() >= 5 && chars[4] == '-';
    let date_len = if extended { 10 } else { 8 };
    if chars.len() < date_len {
        return None;
    }
    let (year, month, day) = date_fields(&chars[..date_len], extended)?;
    check_date(year, month, day)?;
    let rest = &chars[date_len..];
    if rest.is_empty() {
        return Some(Parsed {
            naive: Naive {
                year,
                month,
                day,
                hour: 0,
                minute: 0,
                second: 0,
                micro: 0,
            },
            offset_secs: None,
        });
    }
    // Any single character separates date from time (`T`, `t`, a space, and in
    // 3.11 anything else); an empty remainder is a ValueError.
    let time_text: String = rest[1..].iter().collect();
    if time_text.is_empty() {
        return None;
    }
    let (body, offset_secs) = split_offset(&time_text)?;
    let (hour, minute, second, micro) = clock_fields(&body)?;
    let naive = Naive {
        year,
        month,
        day,
        hour,
        minute,
        second,
        micro,
    };
    if hour != 24 {
        return Some(Parsed { naive, offset_secs });
    }
    // `datetime` accepts 24:00:00 and rolls into the next day; anything else at
    // hour 24 is a ValueError.
    if minute != 0 || second != 0 || micro != 0 {
        return None;
    }
    let midnight = Naive { hour: 0, ..naive };
    Some(Parsed {
        naive: Naive::from_epoch_utc(midnight.epoch_secs() + 86_400, 0),
        offset_secs,
    })
}

fn date_fields(chars: &[char], extended: bool) -> Option<(i32, u32, u32)> {
    let digits = |slice: &[char]| -> Option<u32> {
        if slice.iter().any(|c| !c.is_ascii_digit()) {
            return None;
        }
        slice.iter().collect::<String>().parse::<u32>().ok()
    };
    if extended {
        if chars.len() != 10 || chars[4] != '-' || chars[7] != '-' {
            return None;
        }
        Some((
            digits(&chars[0..4])? as i32,
            digits(&chars[5..7])?,
            digits(&chars[8..10])?,
        ))
    } else {
        if chars.len() != 8 {
            return None;
        }
        Some((
            digits(&chars[0..4])? as i32,
            digits(&chars[4..6])?,
            digits(&chars[6..8])?,
        ))
    }
}

/// `datetime(year, month, day)`'s own range checks.
fn check_date(year: i32, month: u32, day: u32) -> Option<()> {
    if !(1..=9999).contains(&year) || !(1..=12).contains(&month) {
        return None;
    }
    if day < 1 || day > days_in_month(year, month) {
        return None;
    }
    Some(())
}

fn days_in_month(year: i32, month: u32) -> u32 {
    match month {
        1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
        4 | 6 | 9 | 11 => 30,
        2 if is_leap(year) => 29,
        2 => 28,
        _ => 0,
    }
}

fn is_leap(year: i32) -> bool {
    (year % 4 == 0 && year % 100 != 0) || year % 400 == 0
}

/// Split a trailing `Z` or `±HH[:MM[:SS]]` off the time text.
fn split_offset(time_text: &str) -> Option<(String, Option<i32>)> {
    if let Some(body) = time_text.strip_suffix('Z') {
        // Lowercase `z` is not accepted by CPython, and neither is it here.
        return Some((body.to_string(), Some(0)));
    }
    match time_text.find(['+', '-']) {
        None => Some((time_text.to_string(), None)),
        Some(index) => {
            let sign = if time_text[index..].starts_with('+') {
                1
            } else {
                -1
            };
            let offset = offset_seconds(&time_text[index + 1..])?;
            Some((time_text[..index].to_string(), Some(sign * offset)))
        }
    }
}

fn offset_seconds(text: &str) -> Option<i32> {
    let (hour, minute, second, micro) = clock_fields(text)?;
    if micro != 0 {
        return None;
    }
    let total = hour as i32 * 3_600 + minute as i32 * 60 + second as i32;
    // `timezone` refuses an offset of 24 hours or more.
    (total < 86_400).then_some(total)
}

/// `HH`, `HH:MM`, `HH:MM:SS[.ffffff]`, `HHMM`, `HHMMSS[.ffffff]`.
fn clock_fields(text: &str) -> Option<(u32, u32, u32, u32)> {
    let (clock, micro) = match text.find(['.', ',']) {
        None => (text, 0u32),
        Some(index) => {
            let digits = &text[index + 1..];
            if digits.is_empty() || digits.chars().any(|c| !c.is_ascii_digit()) {
                return None;
            }
            let mut padded: String = digits.chars().take(6).collect();
            while padded.len() < 6 {
                padded.push('0');
            }
            (&text[..index], padded.parse::<u32>().ok()?)
        }
    };
    let fields: Vec<&str> = if clock.contains(':') {
        clock.split(':').collect()
    } else {
        if clock.len() % 2 != 0 || clock.is_empty() || clock.len() > 6 {
            return None;
        }
        clock
            .as_bytes()
            .chunks(2)
            .map(|pair| std::str::from_utf8(pair).unwrap_or(""))
            .collect()
    };
    if fields.is_empty() || fields.len() > 3 {
        return None;
    }
    // A fraction is only legal once the seconds field is present.
    if micro != 0 && fields.len() != 3 {
        return None;
    }
    let mut parts = [0u32; 3];
    for (slot, field) in parts.iter_mut().zip(fields.iter()) {
        if field.len() != 2 || field.chars().any(|c| !c.is_ascii_digit()) {
            return None;
        }
        *slot = field.parse().ok()?;
    }
    let (hour, minute, second) = (parts[0], parts[1], parts[2]);
    if hour > 24 || minute > 59 || second > 59 {
        return None;
    }
    Some((hour, minute, second, micro))
}

// ── civil calendar arithmetic (Hinnant) ─────────────────────────────────────

fn days_from_civil(year: i64, month: u32, day: u32) -> i64 {
    let y = if month <= 2 { year - 1 } else { year };
    let era = if y >= 0 { y } else { y - 399 } / 400;
    let yoe = y - era * 400;
    let m = month as i64;
    let doy = (153 * (if m > 2 { m - 3 } else { m + 9 }) + 2) / 5 + day as i64 - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146_097 + doe - 719_468
}

fn civil_from_days(days: i64) -> (i64, u32, u32) {
    let z = days + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1_460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    (if m <= 2 { y + 1 } else { y }, m as u32, d as u32)
}

/// The system zone's wall clock for a UTC instant, via `localtime_r(3)`.
#[cfg(unix)]
fn system_local(utc_secs: i64) -> Option<Naive> {
    let stamp = utc_secs as libc::time_t;
    let mut broken: libc::tm = unsafe { std::mem::zeroed() };
    // SAFETY: `localtime_r` fills the caller-owned `tm` we just zeroed and
    // returns a pointer to it (or null on failure); nothing else is touched.
    if unsafe { libc::localtime_r(&stamp, &mut broken) }.is_null() {
        return None;
    }
    Some(Naive {
        year: broken.tm_year + 1900,
        month: broken.tm_mon as u32 + 1,
        day: broken.tm_mday as u32,
        hour: broken.tm_hour as u32,
        minute: broken.tm_min as u32,
        second: broken.tm_sec as u32,
        micro: 0,
    })
}

/// Non-unix hosts have no `localtime_r`; this crate targets macOS and Linux.
#[cfg(not(unix))]
fn system_local(_utc_secs: i64) -> Option<Naive> {
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    fn naive(text: &str) -> Naive {
        parse_iso(text).expect("stamp parses").naive
    }

    #[test]
    fn the_stamps_the_store_writes_parse_the_way_python_parses_them() {
        assert_eq!(
            naive("2026-08-11T09:00:00").iso_seconds(),
            "2026-08-11T09:00:00"
        );
        assert_eq!(naive("2026-08-11").date_iso(), "2026-08-11");
        assert_eq!(naive("2026-08-11 09:00:00").hour, 9);
        assert_eq!(naive("20260811T090000").day, 11);
        assert_eq!(naive("2026-08-11T09").minute, 0);
        assert_eq!(naive("2026-08-11T0900").hour, 9);
        assert_eq!(naive("2026-08-11T09:00:00.1234567").micro, 123_456);
        assert_eq!(naive("2026-08-11T09:00:00,123").micro, 123_000);
        // 24:00 rolls the day, which is the one clock value that moves a row
        // into the *next* bucket.
        assert_eq!(naive("2026-08-11T24").date_iso(), "2026-08-12");
        assert_eq!(
            parse_iso("2026-08-11T09:00:00Z")
                .expect("aware")
                .offset_secs,
            Some(0)
        );
        assert_eq!(
            parse_iso("2026-08-11T09:00:00+09:00:30")
                .expect("aware")
                .offset_secs,
            Some(32_430)
        );
        assert_eq!(
            parse_iso("2026-08-11T09:00:00-05:00")
                .expect("aware")
                .offset_secs,
            Some(-18_000)
        );
    }

    #[test]
    fn every_shape_python_refuses_answers_none() {
        for text in [
            "",
            "not-a-timestamp",
            "2026-08-1",
            "2026-8-11",
            "2026-13-01",
            "2026-02-30",
            "0000-01-01",
            "2026-08-11T",
            "2026-08-11T09:0",
            "2026-08-11T23:60",
            "2026-08-11T09:00:60",
            "2026-08-11T24:00:01",
            "2026-08-11T09:00:00z",
            "2026-08-11T09:00:00.",
            "2026-08-11T09:00.5",
            "2026-08-11T09:00:00+24:00",
            "2026-08-11T09:00:00Z+09:00",
            "2026-08-11T09:00:00+0",
            "+2026-08-11",
            "2026",
        ] {
            assert!(parse_iso(text).is_none(), "{text} must not parse");
        }
    }

    #[test]
    fn an_offset_is_moved_into_the_configured_zone_and_a_naive_stamp_is_not() {
        let naive = parse_iso("2026-08-11T09:00:00").expect("naive");
        assert_eq!(local(naive).iso_seconds(), "2026-08-11T09:00:00");
        // Two spellings of the same instant land on the same wall clock,
        // whatever this machine's zone is — that is the property `_local` has.
        let utc = parse_iso("2026-08-11T00:00:00Z").expect("aware");
        let plus_nine = parse_iso("2026-08-11T09:00:00+09:00").expect("aware");
        assert_eq!(local(utc), local(plus_nine));
    }

    #[test]
    fn the_router_gates_normalize_or_refuse() {
        assert_eq!(parse_day("2026-08-11").expect("real date"), "2026-08-11");
        assert_eq!(parse_day(" 2026-08-11 ").expect("stripped"), "2026-08-11");
        for bad in [
            "not-a-date",
            "2026-13-45",
            "2026-02-30",
            "20260811",
            "",
            "٢٠٢٦-٠٨-١١",
        ] {
            assert!(parse_day(bad).is_err(), "{bad} must be a 422");
        }
        assert_eq!(
            parse_timestamp("2026-08-11T09:00:00.987654").expect("stamp"),
            "2026-08-11T09:00:00"
        );
        assert_eq!(
            parse_timestamp("2020-01-01").expect("date only"),
            "2020-01-01T00:00:00"
        );
        for bad in ["", "   ", "not-a-timestamp"] {
            assert!(parse_timestamp(bad).is_err(), "{bad} must be a 422");
        }
    }

    #[test]
    fn the_day_bucket_is_the_local_calendar_day() {
        assert_eq!(day_of("2026-08-11T23:59:59").as_deref(), Some("2026-08-11"));
        assert_eq!(
            day_of("  2026-08-11T00:00:00  ").as_deref(),
            Some("2026-08-11")
        );
        assert_eq!(day_of(""), None);
        assert_eq!(day_of("garbage"), None);
    }

    #[test]
    fn the_calendar_round_trips_across_eras() {
        for (secs, expected) in [
            (0_i64, "1970-01-01T00:00:00"),
            (-86_400, "1969-12-31T00:00:00"),
            (951_782_400, "2000-02-29T00:00:00"),
            (1_785_999_999, "2026-08-06T07:06:39"),
        ] {
            assert_eq!(Naive::from_epoch_utc(secs, 0).iso_seconds(), expected);
        }
        let stamp = naive("2026-08-11T09:00:00");
        assert_eq!(Naive::from_epoch_utc(stamp.epoch_secs(), 0), stamp);
    }
}
