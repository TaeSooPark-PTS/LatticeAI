use super::*;
use serde_json::Value;
use std::collections::BTreeSet;

pub fn complete_created_files(
    said: &str,
    transcript: &[Value],
    file_create_actions: &BTreeSet<String>,
) -> String {
    let proven = proven_created_files(transcript, file_create_actions);
    if proven.is_empty() {
        return said.to_string();
    }
    if proven.iter().all(|f| answer_names_file(said, &f.path)) {
        return said.to_string();
    }
    let trimmed = said.trim();
    if is_bare_negation(trimmed) {
        let facts: Vec<String> = proven
            .iter()
            .map(|f| match f.bytes {
                Some(b) => format!("{} 파일을 작성했습니다 ({}B).", f.path, b),
                None => format!("{} 파일을 작성했습니다.", f.path),
            })
            .collect();
        return facts.join("\n");
    }
    if is_negation_sentence(trimmed) {
        let missing_files: Vec<&ProvenFile> = proven
            .iter()
            .filter(|f| !answer_names_file(said, &f.path))
            .collect();
        let facts: Vec<String> = missing_files
            .iter()
            .map(|f| match f.bytes {
                Some(b) => format!("{} 파일을 작성했습니다 ({}B).", f.path, b),
                None => format!("{} 파일을 작성했습니다.", f.path),
            })
            .collect();
        let facts_text = facts.join("\n");
        return format!("{facts_text}\n\n{trimmed}");
    }
    said.to_string()
}

pub fn complete_a_count(said: &str, request: &str, transcript: &[Value]) -> String {
    if !request_asks_for_a_count(request) {
        return said.to_string();
    }
    let Some(count) = attributed_count(request, transcript) else {
        return said.to_string();
    };
    if answer_carries(said, &count) {
        return said.to_string();
    }
    if said.trim().is_empty() {
        count
    } else {
        format!("{} ({count})", said.trim())
    }
}

pub fn answer_owes_a_count(answer: &str, request: &str, transcript: &[Value]) -> bool {
    if !request_asks_for_a_count(request) {
        return false;
    }
    match attributed_count(request, transcript) {
        Some(count) => !answer_carries(answer, &count),
        None => standalone_numbers(answer).next().is_none(),
    }
}

pub fn attributed_count(request: &str, transcript: &[Value]) -> Option<String> {
    let named: Vec<&str> = transcript
        .iter()
        .filter_map(|step| step.get("action").and_then(Value::as_str))
        .filter(|action| !action.is_empty() && names_action(request, action))
        .collect();
    if named.is_empty() {
        return count_from_transcript(transcript);
    }
    transcript
        .iter()
        .rev()
        .filter(|step| {
            step.get("error").is_none()
                && step
                    .get("action")
                    .and_then(Value::as_str)
                    .is_some_and(|action| named.contains(&action))
        })
        .find_map(|step| step.get("result").and_then(countable))
}

pub fn names_action(request: &str, action: &str) -> bool {
    if names_token(request, action) {
        return true;
    }
    action
        .split_once('.')
        .is_some_and(|(_, bare)| !bare.is_empty() && names_token(request, bare))
}

pub fn names_token(text: &str, token: &str) -> bool {
    let hay = text.to_lowercase();
    let needle = token.to_lowercase();
    if needle.is_empty() {
        return false;
    }
    let mut start = 0;
    while let Some(offset) = hay[start..].find(&needle) {
        let index = start + offset;
        let before_ok = match hay[..index].chars().next_back() {
            None => true,
            Some(character)
                if character.is_ascii_alphanumeric() || character == '_' || character == '.' =>
            {
                false
            }
            Some(_) => true,
        };
        let after_ok = match hay[index + needle.len()..].chars().next() {
            None => true,
            Some(character) if character.is_ascii_alphanumeric() || character == '_' => false,
            Some(_) => true,
        };
        if before_ok && after_ok {
            return true;
        }
        start = index + 1;
    }
    false
}

fn countable(result: &Value) -> Option<String> {
    if let Some(items) = result.get("items").and_then(Value::as_array) {
        return Some(format!("{}개", items.len()));
    }
    let count = result
        .get("count")
        .or_else(|| result.get("matches"))
        .or_else(|| result.get("hits"))?;
    if let Some(number) = count.as_u64() {
        return Some(format!("{number}개"));
    }
    count.as_array().map(|rows| format!("{}개", rows.len()))
}

pub(super) fn standalone_numbers(text: &str) -> impl Iterator<Item = &str> {
    let bytes = text.as_bytes();
    let edge = |index: usize| -> bool {
        match bytes.get(index) {
            None => true,
            Some(byte) => {
                !(byte.is_ascii_alphanumeric() || matches!(byte, b'/' | b'-' | b'_' | b'.'))
            }
        }
    };
    let mut start = 0usize;
    std::iter::from_fn(move || {
        while start < bytes.len() {
            if !bytes[start].is_ascii_digit() {
                start += 1;
                continue;
            }
            let from = start;
            while start < bytes.len() && bytes[start].is_ascii_digit() {
                start += 1;
            }
            let leading = from == 0 || edge(from - 1);
            if leading && edge(start) {
                return Some(&text[from..start]);
            }
        }
        None
    })
}

fn answer_carries(answer: &str, count: &str) -> bool {
    let digits: String = count.chars().filter(char::is_ascii_digit).collect();
    !digits.is_empty() && standalone_numbers(answer).any(|number| number == digits)
}

pub fn count_from_transcript(transcript: &[Value]) -> Option<String> {
    transcript
        .iter()
        .rev()
        .find_map(|step| step.get("result").and_then(countable))
}
