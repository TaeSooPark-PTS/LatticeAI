//! The regex table and the string search every stage shares.
//!
//! Split out of [`super`] so the ported patterns sit together: each one is a
//! transcription of a `re.compile` in `latticeai.core.file_generation`, with
//! Python's flags spelled out inline.

use std::sync::OnceLock;

use fancy_regex::Regex;

// ── regex table (Python's, with Python's flags) ─────────────────────────────

fn compiled(cell: &'static OnceLock<Regex>, pattern: &str) -> &'static Regex {
    cell.get_or_init(|| Regex::new(pattern).expect("ported pattern must compile"))
}

/// `re.search`, with a backtracking failure read as "no match" — the
/// conservative answer for every caller here.
pub(super) fn matched(re: &Regex, text: &str) -> bool {
    re.is_match(text).unwrap_or(false)
}

pub(super) fn refusal() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    compiled(
        &RE,
        concat!(
            r"(?i)(i can('|no)?t|i'?m (sorry|unable)|as an ai|cannot assist",
            r"|죄송(하지만|합니다)|할 수 없|불가능합니다|도와드릴 수 없)",
        ),
    )
}

pub(super) fn think_block() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    compiled(&RE, r"(?is)<(think|thinking|reasoning|reflection)>.*?</\1>")
}

pub(super) fn think_open() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    compiled(&RE, r"(?is)<(think|thinking|reasoning)>.*\z")
}

pub(super) fn fence() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    compiled(&RE, "(?s)```([\\w.+-]*)[ \\t]*\\n(.*?)```")
}

pub(super) fn chat_line() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    compiled(
        &RE,
        concat!(
            r"(?i)^\s*((sure|of course|certainly|okay|ok|alright|great|absolutely)\b[^\n]*",
            r"|here('s| is| are)\b[^\n]*",
            r"|i('ve| have) (created|written|generated|made)\b[^\n]*",
            r"|(below|following) is\b[^\n]*",
            r"|let me know\b[^\n]*",
            r"|hope (this|that) helps[^\n]*",
            r"|feel free\b[^\n]*",
            r"|물론(입니다|이죠|이에요)?[!., ]*[^\n]*",
            r"|네[,!. ][^\n]*",
            r"|알겠습니다[^\n]*",
            r"|다음은[^\n]*(입니다|합니다)[:.]?[^\n]*",
            r"|아래는?[^\n]*(입니다|내용)[^\n]*",
            r"|(요청하신|원하시는)[^\n]*(입니다|만들었습니다|작성했습니다)[^\n]*",
            r"|(파일|내용|코드)[을를]?\s*(생성|작성|만들)[^\n]*",
            r"|도움이 (필요하|되)[^\n]*",
            r"|추가로[^\n]*(말씀|요청)[^\n]*)\s*$",
        ),
    )
}

pub(super) fn commentary() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    compiled(
        &RE,
        concat!(
            r"(?i)^\s*((sure|of course|certainly|okay|ok|alright|here|below|the following)\b",
            r"|i('ve| have| will|'ll)\b",
            r"|(물론|네[,!. ]|알겠|다음은|아래(는|의)?|요청하신|원하시는))",
        ),
    )
}

pub(super) fn body_structure() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    compiled(&RE, r"(?m)^\s*(#{1,6}\s|[-*+]\s|\d+[.)]\s|\||>)")
}

pub(super) fn html_tag() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    compiled(&RE, r"<\w+[^>]*>")
}

pub(super) fn block_tag(tag: &str) -> Regex {
    Regex::new(&format!(r"<{tag}(?:\s[^>]*)?>")).expect("ported pattern must compile")
}

// ── ASCII-insensitive search ────────────────────────────────────────────────
//
// Python searches `content.lower()` and indexes the *original* with the offset
// it found. That is only sound while `str.lower()` preserves length, which it
// does not for a handful of code points (`İ`). Searching the original
// case-insensitively is the same answer for every ASCII needle and cannot
// desynchronise the index, so it is what this port does.

pub(super) fn find_ci(haystack: &str, needle: &str) -> Option<usize> {
    let (hay, pin) = (haystack.as_bytes(), needle.as_bytes());
    if pin.is_empty() || hay.len() < pin.len() {
        return pin.is_empty().then_some(0);
    }
    (0..=hay.len() - pin.len()).find(|&at| hay[at..at + pin.len()].eq_ignore_ascii_case(pin))
}

pub(super) fn rfind_ci(haystack: &str, needle: &str) -> Option<usize> {
    let (hay, pin) = (haystack.as_bytes(), needle.as_bytes());
    if pin.is_empty() || hay.len() < pin.len() {
        return pin.is_empty().then_some(hay.len());
    }
    (0..=hay.len() - pin.len())
        .rev()
        .find(|&at| hay[at..at + pin.len()].eq_ignore_ascii_case(pin))
}

pub(super) fn contains_ci(haystack: &str, needle: &str) -> bool {
    find_ci(haystack, needle).is_some()
}

pub(super) fn starts_with_ci(haystack: &str, needle: &str) -> bool {
    let (hay, pin) = (haystack.as_bytes(), needle.as_bytes());
    hay.len() >= pin.len() && hay[..pin.len()].eq_ignore_ascii_case(pin)
}

/// `_ext(path)` — the last suffix, lowercased, or `""`.
pub fn ext_of(path: &str) -> String {
    match path.rfind('.') {
        Some(dot) => path[dot..].to_lowercase(),
        None => String::new(),
    }
}
