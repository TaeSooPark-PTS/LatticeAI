//! Reading what a micro-turn came back with (v12.0.0).
//!
//! Seven pure functions, and one rule they all obey: **the harness never
//! invents an answer the model did not give.** Every one of them either returns
//! what the model said (with our own decoration removed) or returns nothing, at
//! which point [`super`] retries or the run stops. None of them supplies a
//! path, a number or a payload of its own.
//!
//! They live apart from the loop that calls them because they are the part
//! worth reading on its own: each is a small, testable answer to "what did a
//! very small model just do to our question", and every one of them exists
//! because a live run did exactly that. The comments name which.

use super::{ECHO_PREFIX_FLOOR, TRAILING_PUNCTUATION};
use crate::tools::catalog::CatalogEntry;

/// Read a menu answer: the first number in `1..=count`, or a named row.
///
/// Deliberately forgiving in one direction only. `"2"`, `"2."`, `" 2)"`,
/// `"I choose 2"` and `"2 — write a file"` are all two; `"0"`, `"12"` when the
/// menu has three rows, and `"write a file"` (handled by the caller against the
/// row names) are not silently coerced into a number. A reply with no usable
/// number returns `None` and the caller retries or gives up — the harness never
/// invents a choice the model did not make.
pub fn parse_choice(reply: &str, count: usize) -> Option<usize> {
    if count == 0 {
        return None;
    }
    let mut digits = String::new();
    for character in reply.chars() {
        if character.is_ascii_digit() {
            digits.push(character);
            continue;
        }
        if !digits.is_empty() {
            break;
        }
    }
    let chosen = digits.parse::<usize>().ok()?;
    (1..=count).contains(&chosen).then_some(chosen)
}

/// Read a one-line answer: the first non-empty line, unquoted and unfenced.
///
/// Weak models wrap a path in backticks, quotes or a `path:` label about a
/// third of the time, and every one of those is a file the sandbox would refuse
/// to write. Stripping them is not guessing — nothing here changes *which*
/// path was named, only the decoration around it.
pub fn parse_line(reply: &str) -> String {
    for raw in reply.lines() {
        let line = raw.trim();
        if line.is_empty() || line == "```" {
            continue;
        }
        let line = line.trim_start_matches("```").trim();
        // A `key: value` echo of the question keeps the value.
        let line = match line.split_once(": ") {
            Some((label, value))
                if !label.contains(' ') && !label.contains('/') && !value.trim().is_empty() =>
            {
                value.trim()
            }
            _ => line,
        };
        let line = line
            .trim_matches(|c| c == '`' || c == '"' || c == '\'')
            .trim();
        if !line.is_empty() {
            return line.to_string();
        }
    }
    String::new()
}

/// Read a closed PASS/FAIL answer.
///
/// Both words must not appear (a reply saying "not FAIL, so PASS" is not an
/// answer), and neither must appear only as part of a longer word. `None` sends
/// the run to the ordinary critic chain, which is the fail-closed direction.
pub fn parse_verdict_word(reply: &str) -> Option<bool> {
    let upper = reply.to_uppercase();
    let has = |word: &str| {
        upper.match_indices(word).any(|(index, _)| {
            let before = upper[..index].chars().next_back();
            let after = upper[index + word.len()..].chars().next();
            !before.is_some_and(char::is_alphanumeric) && !after.is_some_and(char::is_alphanumeric)
        })
    };
    match (has("PASS"), has("FAIL")) {
        (true, false) => Some(true),
        (false, true) => Some(false),
        _ => None,
    }
}

/// Drop leading lines that are our own instructions read back.
///
/// A small model continues the nearest text, and in a guided turn the nearest
/// text is the question we just asked. The first live 0.5B run produced, in
/// three successive fixes: `내용을 그대로 쓰세요.` (the question), then
/// `- path: notes/hello.md` (a decided-values line), then
/// `/ Write only the resulting file body for the request below — …` (a line of
/// the instruction block) — each one written to disk as a file's contents.
///
/// The rule is **line equality against the text we sent**, applied only to
/// *leading* lines. That is tight enough that a real document is never touched
/// (its first line is not one of our sentences) and loose enough to strip a
/// preamble a model prefixed to a real answer. What is left after stripping is
/// judged by the caller's ordinary empty check, so a reply that was *nothing
/// but* echo becomes no answer at all rather than a file.
pub fn strip_echoed_lines(answer: &str, sent: &[&str]) -> String {
    /// Collapsed whitespace, lowercased, and with a leading list marker
    /// removed — `[1] `, `1. `, `- ` and `* `. A model that echoes an
    /// instruction often numbers it first, and `[1] / Write only the …` is the
    /// same echo as `/ Write only the …`.
    fn squash(text: &str) -> String {
        let text = text.trim_start();
        let text = match text.find(']') {
            Some(close)
                if text.starts_with('[')
                    && text[1..close].chars().all(|c| c.is_ascii_digit())
                    && close > 1 =>
            {
                &text[close + 1..]
            }
            _ => text,
        };
        let text = text.trim_start_matches(['-', '*', '•']).trim_start();
        let text = match text.find(". ") {
            Some(dot) if dot > 0 && text[..dot].chars().all(|c| c.is_ascii_digit()) => {
                &text[dot + 1..]
            }
            _ => text,
        };
        text.split_whitespace()
            .collect::<Vec<_>>()
            .join(" ")
            .to_lowercase()
    }
    let asked: Vec<String> = sent
        .iter()
        .flat_map(|text| text.lines())
        .map(squash)
        .filter(|line| line.chars().count() >= 4)
        .collect();
    let mut kept: Vec<&str> = Vec::new();
    let mut still_leading = true;
    for line in answer.lines() {
        if still_leading {
            // Blankness is judged on the *raw* line: `---` squashes to nothing
            // once a list marker is stripped, and a horizontal rule is a
            // document, not an echo.
            if line.trim().is_empty() {
                continue;
            }
            let squashed = squash(line);
            // Equal, or a **prefix** of one of our lines: a small model that
            // starts copying an instruction and stops early has still copied
            // it, and the live 0.5B did exactly that with the English half of a
            // bilingual sentence. Twenty characters is the floor, so no real
            // document's opening line can collide by accident.
            if !squashed.is_empty()
                && asked.iter().any(|line| {
                    // The prefix test drops trailing punctuation from the
                    // answer: a model that stopped mid-sentence puts a full
                    // stop where our sentence carried on, and `below.` would
                    // otherwise not be a prefix of `below — no explanation…`.
                    let opening = squashed.trim_end_matches(TRAILING_PUNCTUATION);
                    *line == squashed
                        || (opening.chars().count() >= ECHO_PREFIX_FLOOR
                            && line.starts_with(opening))
                })
            {
                continue;
            }
            still_leading = false;
        }
        kept.push(line);
    }
    let kept = kept.join("\n");
    drop_owned_instruction_lines(&kept)
}

/// Drop any line that still carries a phrase we own, even mid-file.
///
/// Leading-line equality misses a garbled echo (`이미지·머리말·코드블록 금지…`).
/// The markers are sentences this crate wrote; a real document does not
/// contain them.
fn drop_owned_instruction_lines(answer: &str) -> String {
    answer
        .lines()
        .filter(|line| !crate::prompts::guided::contains_owned_instruction(line))
        .collect::<Vec<_>>()
        .join("\n")
}

/// Longest control-token name this will skip over, in bytes.
///
/// A ceiling, not a guess: the longest frame any shipped template uses is
/// `<|start_header_id|>` at seventeen characters inside the delimiters. The
/// bound is what makes [`strip_control_tokens`] safe on prose — a `<|` that is
/// really text cannot swallow the sentence after it, because a token name is
/// short and has no spaces.
const CONTROL_TOKEN_NAME_MAX: usize = 32;

/// Remove chat-template control tokens from a free-form answer.
///
/// `<|im_end|>`, `<|eot_id|>`, `<|channel|>` and friends are the tokenizer's
/// punctuation, not the model's words, and a small model whose stop handling
/// slipped emits them inside the answer. The JSON parse chain already strips
/// them from actions ([`crate::parse::channel`]); a guided turn has no parse
/// chain, so it strips them here rather than writing them to a file — the live
/// 2B wrote `안녕하세요!<|im_end|>`.
///
/// **A frame closed with a bare `>` is one too** (v12.0.0). Until now the
/// closer had to be `|>`, and gemma-4-e2b emits `<|channel>thought` — one pipe,
/// not two. Twelve of that model's sixteen recorded cells carried it: it
/// reached `mcp.grep` as the *pattern to search for*, it reached the guided
/// critic as its `reason`, and it reached the user as the whole answer. Nothing
/// about that is specific to one model — a frame the template teaches and the
/// model reproduces imperfectly is the general case, and the cleaner has to
/// read the malformed spelling as what it plainly is.
///
/// Safety is the bound, not the spelling: a token name is short
/// ([`CONTROL_TOKEN_NAME_MAX`]), carries no whitespace and opens no second
/// frame. A `<|` followed by anything else is text and is kept, which is what
/// keeps a genuine document containing `<|` intact.
pub fn strip_control_tokens(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut rest = text;
    while let Some(open) = rest.find("<|") {
        out.push_str(&rest[..open]);
        let after = &rest[open + 2..];
        match control_token_len(after) {
            Some(len) => rest = &after[len..],
            // Not a frame: `<|` is text, and so is whatever follows it. Emit
            // the two characters and carry on looking past them, so a real
            // token later in the same answer is still found.
            None => {
                out.push_str("<|");
                rest = after;
            }
        }
    }
    out.push_str(rest);
    out
}

/// Bytes to skip past a control token's name and closer, or `None` for text.
///
/// Called with everything after a `<|`. Accepts `name|>` and `name>`; a name is
/// ASCII word characters or `-`, at most [`CONTROL_TOKEN_NAME_MAX`] of them,
/// and may be empty (`<|>` is a frame some templates emit).
fn control_token_len(after: &str) -> Option<usize> {
    let mut used = 0usize;
    for byte in after.bytes() {
        match byte {
            b'>' => return Some(used + 1),
            b'|' => {
                return (after.as_bytes().get(used + 1) == Some(&b'>')).then_some(used + 2);
            }
            b'_' | b'-' => used += 1,
            _ if byte.is_ascii_alphanumeric() => used += 1,
            _ => return None,
        }
        if used > CONTROL_TOKEN_NAME_MAX {
            return None;
        }
    }
    None
}

/// Whether an answer to a `path` argument looks like a path at all.
///
/// A separator or an extension. Used only to decide whether to prefer a
/// default the plan already supplied — never to reject an answer when there is
/// no default, because then the model's word is all there is and the sandbox
/// is the thing that judges it.
pub fn looks_like_a_path(value: &str) -> bool {
    let value = value.trim();
    !value.is_empty()
        && (value.contains('/')
            || value.contains('\\')
            || value.rsplit_once('.').is_some_and(|(stem, extension)| {
                !stem.is_empty()
                    && !extension.is_empty()
                    && extension.chars().all(|c| c.is_ascii_alphanumeric())
            }))
}

/// Longest reply [`named_choice`] will read a row name out of, in characters.
///
/// Eighty, and the number is the whole guard (v12.0.0). A reply that *names* a
/// row is an answer — `write_file`, `I'll use write_file`, `2 — write a file`
/// — and every one of those is short. A reply that runs on past this is not an
/// answer to "reply with ONE number"; it is the model thinking out loud, and
/// reading a row name out of prose is how a live 0.5B ended three runs on turn
/// one. Its re-asked menu turn (sixty-four tokens, no line stop) came back as a
/// paragraph, the paragraph contained the ordinary English word **final**, and
/// `final` is a row. Nothing was written and the run reported itself finished.
///
/// The cost is stated plainly: a model that answers in a long sentence gets no
/// choice read out of it and the turn is re-asked. That is the right trade —
/// the alternative is a harness that finishes runs because a word appeared.
const NAMED_ANSWER_MAX_CHARS: usize = 80;

/// A reply that named a row instead of numbering it.
///
/// Matched as a **whole token** ([`super::named_in`]) so `read_file` never
/// matches `write_file`'s row and `final` never matches `finalize`; only on the
/// reply's first line, decoration removed, and only when that line is short
/// enough to be an answer ([`NAMED_ANSWER_MAX_CHARS`]); and only when exactly
/// one row matches — an ambiguous reply is no choice.
pub fn named_choice(reply: &str, catalog: &[CatalogEntry]) -> Option<usize> {
    let answer = parse_line(reply);
    if answer.is_empty() || answer.chars().count() > NAMED_ANSWER_MAX_CHARS {
        return None;
    }
    let mut found: Option<usize> = None;
    for (index, entry) in catalog.iter().enumerate() {
        if super::named_in(&answer, &entry.name) {
            if found.is_some() {
                return None;
            }
            found = Some(index + 1);
        }
    }
    found
}
