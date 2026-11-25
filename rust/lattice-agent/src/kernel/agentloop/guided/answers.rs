use super::{ECHO_PREFIX_FLOOR, TRAILING_PUNCTUATION};
use crate::tools::catalog::CatalogEntry;

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

pub fn parse_line(reply: &str) -> String {
    for raw in reply.lines() {
        let line = raw.trim();
        if line.is_empty() || line == "```" {
            continue;
        }
        let line = line.trim_start_matches("```").trim();
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

pub fn strip_echoed_lines(answer: &str, sent: &[&str]) -> String {
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
            if line.trim().is_empty() {
                continue;
            }
            let squashed = squash(line);
            if !squashed.is_empty()
                && asked.iter().any(|line| {
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

fn drop_owned_instruction_lines(answer: &str) -> String {
    answer
        .lines()
        .filter(|line| !crate::prompts::guided::contains_owned_instruction(line))
        .collect::<Vec<_>>()
        .join("\n")
}

const CONTROL_TOKEN_NAME_MAX: usize = 32;

pub fn strip_control_tokens(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut rest = text;
    while let Some(open) = rest.find("<|") {
        out.push_str(&rest[..open]);
        let after = &rest[open + 2..];
        match control_token_len(after) {
            Some(len) => rest = &after[len..],
            None => {
                out.push_str("<|");
                rest = after;
            }
        }
    }
    out.push_str(rest);
    out
}

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

const NAMED_ANSWER_MAX_CHARS: usize = 80;

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
