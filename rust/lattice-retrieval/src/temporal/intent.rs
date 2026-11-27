use std::sync::OnceLock;

use fancy_regex::Regex;
use serde_json::{json, Value};

use super::calendar::{
    date_epoch, iso, month_start_offset, next_month_epoch, today_start, week_start,
    year_start_offset, DAY_SECS,
};
use crate::build_pattern as build;

pub const RECENT_WINDOW_DAYS: f64 = 30.0;

#[derive(Debug, Clone, PartialEq)]
pub struct TimeWindow {
    pub since: Option<f64>,
    pub until: Option<f64>,
    pub label: String,
}

impl TimeWindow {
    pub fn since_iso(&self) -> Option<String> {
        self.since.map(iso)
    }

    pub fn until_iso(&self) -> Option<String> {
        self.until.map(iso)
    }

    pub fn contains(&self, epoch: f64) -> bool {
        self.since.is_none_or(|start| epoch >= start) && self.until.is_none_or(|end| epoch < end)
    }

    pub fn is_closed_before(&self, now: f64) -> bool {
        self.until.is_some_and(|end| end <= now)
    }

    pub fn as_json(&self) -> Value {
        json!({
            "label": self.label,
            "since": self.since_iso(),
            "until": self.until_iso(),
        })
    }
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct TimeIntent {
    pub window: Option<TimeWindow>,
    pub as_of: Option<TimeWindow>,
    pub version: Option<String>,
    pub latest: bool,
    pub historical: bool,
    pub phrases: Vec<String>,
    pub search_query: String,
}

impl TimeIntent {
    pub fn has_time_signal(&self) -> bool {
        self.window.is_some() || self.version.is_some() || self.latest
    }
}

struct Patterns {
    abs_since_en: Regex,
    abs_before_en: Regex,
    abs_ko: Regex,
    as_of_en: Regex,
    as_of_ko: Regex,
    in_year_en: Regex,
    year_ko: Regex,
    version: Regex,
    on_date: Regex,
    ko_date: Regex,
    bare_date: Regex,
    relative_n_ko: Regex,
    relative_n_en: Regex,
    named: Vec<(&'static str, Regex)>,
    latest: Regex,
    recent: Regex,
    historical: Regex,
    whitespace: Regex,
}

fn patterns() -> &'static Patterns {
    static PATTERNS: OnceLock<Patterns> = OnceLock::new();
    PATTERNS.get_or_init(|| Patterns {
        abs_since_en: build(
            r"(?i)\b(?:since|after|from)\s+(\d{4})[-./](\d{1,2})(?:[-./](\d{1,2}))?\b",
        ),
        abs_before_en: build(
            r"(?i)\b(?:before|until|till)\s+(\d{4})[-./](\d{1,2})(?:[-./](\d{1,2}))?\b",
        ),
        abs_ko: build(
            r"(?:(\d{4})[-./](\d{1,2})(?:[-./](\d{1,2}))?|(\d{4})년\s*(\d{1,2})월(?:\s*(\d{1,2})일)?)\s*(이후|부터|이전|까지)",
        ),
        as_of_en: build(
            r"(?i)\bas\s+of\s+((?:19|20|21)\d{2})(?:[-./](\d{1,2})(?:[-./](\d{1,2}))?)?(?![\d])",
        ),
        as_of_ko: build(
            r"(?<!\d)((?:19|20|21)\d{2})(?:[-./](\d{1,2})(?:[-./](\d{1,2}))?|년(?:\s*(\d{1,2})월(?:\s*(\d{1,2})일)?)?)?\s*기준",
        ),
        in_year_en: build(r"(?i)\b(?:in|during)\s+((?:19|20|21)\d{2})\b(?![-./]\d)"),
        year_ko: build(
            r"(?<!\d)((?:19|20|21)\d{2})년(?!\s*\d{1,2}\s*월)(?!\s*(?:이후|부터|이전|까지))",
        ),
        version: build(
            r"(?i)\b(?:since|after|from)\s+(?:v|version\s*)(\d+(?:\.\d+){0,2})\b|\bv(\d+(?:\.\d+){0,2})\s*(?:버전\s*)?(?:이후|부터)",
        ),
        on_date: build(r"(?i)\b(?:in|on|during)\s+(\d{4})-(\d{1,2})(?:-(\d{1,2}))?\b"),
        ko_date: build(r"(\d{4})년\s*(\d{1,2})월(?:\s*(\d{1,2})일)?"),
        bare_date: build(r"(?<![\d.])(\d{4})-(\d{2})-(\d{2})(?![\d.])"),
        relative_n_ko: build(r"(?:최근|지난)\s*(\d{1,3})\s*(일|주|개월|달)"),
        relative_n_en: build(r"(?i)\b(?:last|past|previous)\s+(\d{1,3})\s+(days?|weeks?|months?)\b"),
        named: vec![
            ("day_before_yesterday", build(r"(?i)그저께|그제|\bday\s+before\s+yesterday\b")),
            ("today", build(r"(?i)오늘|\btoday\b")),
            ("yesterday", build(r"(?i)어제|\byesterday\b")),
            ("this_week", build(r"(?i)이번\s*주|금주|\bthis\s+week\b")),
            ("past_week", build(r"(?i)\bpast\s+week\b")),
            ("last_week", build(r"(?i)지난\s*주|저번\s*주|\blast\s+week\b")),
            ("this_month", build(r"(?i)이번\s*달|\bthis\s+month\b")),
            ("last_month", build(r"(?i)지난\s*달|저번\s*달|\blast\s+month\b")),
            ("this_year", build(r"(?i)올해|금년|\bthis\s+year\b")),
            ("last_year", build(r"(?i)작년|\blast\s+year\b")),
        ],
        latest: build(r"(?i)최신|가장\s*최근|\blatest\b|\bnewest\b|\bmost\s+recent\b"),
        recent: build(r"(?i)최근|요즘|근래|\brecent(?:ly)?\b|\blately\b"),
        historical: build(
            r"(?i)이전\s*(?:버전|내용|정책|결정|방식)|예전|과거|옛날|변경\s*(?:이력|내역)|히스토리|바뀌기\s*전|원래|\bhistor(?:y|ical)\b|\bprevious(?:ly)?\b|\bused\s+to\b|\bold(?:er)?\s+versions?\b|\bformerly\b|\boriginally\b",
        ),
        whitespace: build(r"\s+"),
    })
}

type Groups = Vec<Option<String>>;

fn group_num(groups: &Groups, index: usize) -> Option<i64> {
    groups.get(index)?.as_deref()?.parse::<i64>().ok()
}

fn date_parts(groups: &Groups, first: usize) -> Option<(i64, u32, Option<u32>)> {
    let year = group_num(groups, first)?;
    let month = u32::try_from(group_num(groups, first + 1)?).ok()?;
    let day = group_num(groups, first + 2).and_then(|d| u32::try_from(d).ok());
    Some((year, month, day))
}

fn span_of(year: i64, month: u32, day: Option<u32>) -> Option<(f64, f64)> {
    match day {
        Some(day) => {
            let start = date_epoch(year, month, day)?;
            Some((start, start + DAY_SECS))
        }
        None => Some((date_epoch(year, month, 1)?, next_month_epoch(year, month)?)),
    }
}

struct Scan {
    text: String,
    phrases: Vec<String>,
    strip: Vec<String>,
}

impl Scan {
    fn take(&mut self, re: &Regex, strip: bool) -> Option<Groups> {
        let caps = re.captures(&self.text).ok().flatten()?;
        let whole = caps.get(0)?;
        let phrase = whole.as_str().to_string();
        let groups = (0..caps.len())
            .map(|i| caps.get(i).map(|m| m.as_str().to_string()))
            .collect();
        let range = whole.range();
        self.text.replace_range(range, &" ".repeat(phrase.len()));
        if strip {
            self.strip.push(phrase.clone());
        }
        self.phrases.push(phrase.trim().to_string());
        Some(groups)
    }
}

fn absolute_window(scan: &mut Scan, p: &Patterns) -> Option<TimeWindow> {
    if let Some(groups) = scan.take(&p.abs_since_en, true) {
        let (y, m, d) = date_parts(&groups, 1)?;
        let (start, _) = span_of(y, m, d)?;
        return Some(window(Some(start), None, "since_date"));
    }
    if let Some(groups) = scan.take(&p.abs_before_en, true) {
        let (y, m, d) = date_parts(&groups, 1)?;
        let (start, _) = span_of(y, m, d)?;
        return Some(window(None, Some(start), "before_date"));
    }
    if let Some(groups) = scan.take(&p.abs_ko, true) {
        let (y, m, d) = date_parts(&groups, 1).or_else(|| date_parts(&groups, 4))?;
        let (start, end) = span_of(y, m, d)?;
        let suffix = groups.get(7).cloned().flatten().unwrap_or_default();
        return Some(match suffix.as_str() {
            "이후" | "부터" => window(Some(start), None, "since_date"),
            "까지" => window(None, Some(end), "until_date"),
            _ => window(None, Some(start), "before_date"),
        });
    }
    for pattern in [&p.on_date, &p.ko_date, &p.bare_date] {
        if let Some(groups) = scan.take(pattern, true) {
            let (y, m, d) = date_parts(&groups, 1)?;
            let (start, end) = span_of(y, m, d)?;
            return Some(window(Some(start), Some(end), "on_date"));
        }
    }
    None
}

fn year_span(year: i64) -> Option<(f64, f64)> {
    Some((date_epoch(year, 1, 1)?, date_epoch(year + 1, 1, 1)?))
}

fn calendar_span(year: i64, month: Option<u32>, day: Option<u32>) -> Option<(f64, f64)> {
    match month {
        Some(month) => span_of(year, month, day),
        None => year_span(year),
    }
}

fn as_of_span(groups: &Groups, first: usize) -> Option<(f64, f64)> {
    let year = group_num(groups, first)?;
    let month = group_num(groups, first + 1).and_then(|m| u32::try_from(m).ok());
    let day = group_num(groups, first + 2).and_then(|d| u32::try_from(d).ok());
    calendar_span(year, month, day)
}

fn explicit_as_of(scan: &mut Scan, p: &Patterns) -> Option<TimeWindow> {
    if let Some(groups) = scan.take(&p.as_of_en, true) {
        let (since, until) = as_of_span(&groups, 1)?;
        return Some(window(Some(since), Some(until), "as_of"));
    }
    if let Some(groups) = scan.take(&p.as_of_ko, true) {
        let month_group = if group_num(&groups, 2).is_some() {
            2
        } else {
            4
        };
        let year = group_num(&groups, 1)?;
        let month = group_num(&groups, month_group).and_then(|m| u32::try_from(m).ok());
        let day = group_num(&groups, month_group + 1).and_then(|d| u32::try_from(d).ok());
        let (since, until) = calendar_span(year, month, day)?;
        return Some(window(Some(since), Some(until), "as_of"));
    }
    None
}

fn year_as_of(scan: &mut Scan, p: &Patterns) -> Option<TimeWindow> {
    for pattern in [&p.in_year_en, &p.year_ko] {
        if let Some(groups) = scan.take(pattern, true) {
            let (since, until) = year_span(group_num(&groups, 1)?)?;
            return Some(window(Some(since), Some(until), "as_of_year"));
        }
    }
    None
}

fn window(since: Option<f64>, until: Option<f64>, label: &str) -> TimeWindow {
    TimeWindow {
        since,
        until,
        label: label.to_string(),
    }
}

fn relative_window(scan: &mut Scan, p: &Patterns, now: f64) -> Option<TimeWindow> {
    for pattern in [&p.relative_n_ko, &p.relative_n_en] {
        if let Some(groups) = scan.take(pattern, false) {
            let count = groups.get(1).cloned().flatten()?.parse::<f64>().ok()?;
            let unit = groups.get(2).cloned().flatten()?.to_lowercase();
            let days = match unit.as_str() {
                "주" | "week" | "weeks" => count * 7.0,
                "개월" | "달" | "month" | "months" => count * 30.0,
                _ => count,
            };
            return Some(window(
                Some(now - days.max(1.0) * DAY_SECS),
                None,
                "last_n_days",
            ));
        }
    }
    let today = today_start(now);
    let week = week_start(now);
    for (label, pattern) in &p.named {
        if scan.take(pattern, false).is_none() {
            continue;
        }
        let (since, until) = match *label {
            "day_before_yesterday" => (today - 2.0 * DAY_SECS, Some(today - DAY_SECS)),
            "today" => (today, None),
            "yesterday" => (today - DAY_SECS, Some(today)),
            "this_week" => (week, None),
            "past_week" => (now - 7.0 * DAY_SECS, None),
            "last_week" => (week - 7.0 * DAY_SECS, Some(week)),
            "this_month" => (month_start_offset(now, 0), None),
            "last_month" => (month_start_offset(now, 1), Some(month_start_offset(now, 0))),
            "this_year" => (year_start_offset(now, 0), None),
            _ => (year_start_offset(now, 1), Some(year_start_offset(now, 0))),
        };
        return Some(window(Some(since), until, label));
    }
    None
}

pub fn parse_time_intent(query: &str, now: f64) -> TimeIntent {
    let p = patterns();
    let original = query.trim().to_string();
    let mut scan = Scan {
        text: original.clone(),
        phrases: Vec::new(),
        strip: Vec::new(),
    };
    let mut intent = TimeIntent::default();
    if let Some(groups) = scan.take(&p.version, true) {
        intent.version = groups
            .get(1)
            .cloned()
            .flatten()
            .or_else(|| groups.get(2).cloned().flatten());
    }
    intent.as_of = explicit_as_of(&mut scan, p);
    intent.window = absolute_window(&mut scan, p).or_else(|| relative_window(&mut scan, p, now));
    if intent.as_of.is_none() {
        intent.as_of = year_as_of(&mut scan, p);
    }
    intent.latest = scan.take(&p.latest, false).is_some();
    if intent.window.is_none() && !intent.latest && scan.take(&p.recent, false).is_some() {
        intent.window = Some(window(
            Some(now - RECENT_WINDOW_DAYS * DAY_SECS),
            None,
            "recent",
        ));
    }
    intent.historical = scan.take(&p.historical, false).is_some();
    let mut stripped = original.clone();
    for phrase in &scan.strip {
        stripped = stripped.replacen(phrase.as_str(), " ", 1);
    }
    let stripped = p.whitespace.replace_all(&stripped, " ").trim().to_string();
    intent.search_query = if stripped.chars().count() >= 2 {
        stripped
    } else {
        original
    };
    intent.phrases = scan.phrases;
    intent
}
