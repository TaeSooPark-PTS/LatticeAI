//! ReAct, Python-call, and XML action salvage for [`super`].

use std::sync::OnceLock;

use fancy_regex::Regex;
use serde_json::{Map, Number, Value};

use super::{
    accepted_action, compiled, escape_unescaped_newlines, fenced_content_block,
    first_balanced_object, labeled_action_line, pyjson, trailing_comma, truncation_repairs,
    usable_labeled,
};

fn qwen_function_open() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    compiled(
        &RE,
        r#"(?is)<function\s*(?:=\s*["']?([A-Za-z_][A-Za-z0-9_.]*)["']?|name\s*=\s*["']([A-Za-z_][A-Za-z0-9_.]*)["'])"#,
    )
}

fn qwen_parameter() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    compiled(
        &RE,
        r#"(?is)<parameter\s*=\s*["']?([A-Za-z_][A-Za-z0-9_]*)["']?>(.*?)</parameter>"#,
    )
}

fn xml_arg_tag() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    compiled(
        &RE,
        r"(?is)<(path|pattern|query|content|command|url|title|message|x|y|x1|y1|x2|y2|text|key|app|button|direction|clicks|element_id|max_results|old_string|new_string)>(.*?)</\1>",
    )
}

pub(super) fn react_action_input_line() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    compiled(&RE, r"(?im)^\s*Action\s+Input:[ \t]*")
}

fn react_tool_line() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    compiled(&RE, r"(?im)^\s*Tool:\s*`?([A-Za-z_][A-Za-z0-9_.]*)`?")
}

fn react_tool_input_line() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    compiled(&RE, r"(?im)^\s*Input:[ \t]*")
}

fn python_call_open() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    compiled(&RE, r"(?<![A-Za-z0-9_.])([A-Za-z_][A-Za-z0-9_.]*)\(")
}

/// Character cursor shared by the ReAct KV and Python-call parsers.
struct Cursor {
    chars: Vec<char>,
    at: usize,
}

impl Cursor {
    fn new(text: &str) -> Self {
        Self {
            chars: text.chars().collect(),
            at: 0,
        }
    }

    fn peek(&self) -> Option<char> {
        self.chars.get(self.at).copied()
    }

    fn peek_at(&self, offset: usize) -> Option<char> {
        self.chars.get(self.at + offset).copied()
    }

    fn rest_is_empty(&self) -> bool {
        self.at >= self.chars.len()
    }

    fn skip_ws(&mut self) {
        while matches!(self.peek(), Some(c) if c.is_whitespace()) {
            self.at += 1;
        }
    }

    fn skip_hspace(&mut self) {
        while matches!(self.peek(), Some(' ' | '\t')) {
            self.at += 1;
        }
    }

    fn eat(&mut self, expected: char) -> bool {
        if self.peek() == Some(expected) {
            self.at += 1;
            true
        } else {
            false
        }
    }

    fn starts_with_str(&self, s: &str) -> bool {
        for (idx, ch) in (self.at..).zip(s.chars()) {
            if self.chars.get(idx).copied() != Some(ch) {
                return false;
            }
        }
        true
    }

    fn parse_ident(&mut self) -> Option<String> {
        let first = self.peek()?;
        if !(first.is_ascii_alphabetic() || first == '_') {
            return None;
        }
        let start = self.at;
        self.at += 1;
        while matches!(self.peek(), Some(c) if c.is_ascii_alphanumeric() || c == '_') {
            self.at += 1;
        }
        Some(self.chars[start..self.at].iter().collect())
    }

    fn code_point(&mut self, width: usize) -> Option<char> {
        let end = self.at.checked_add(width)?;
        let digits: String = self.chars.get(self.at..end)?.iter().collect();
        self.at = end;
        char::from_u32(u32::from_str_radix(&digits, 16).ok()?)
    }

    fn parse_escape(&mut self) -> Option<char> {
        let esc = self.peek()?;
        self.at += 1;
        match esc {
            'n' => Some('\n'),
            'r' => Some('\r'),
            't' => Some('\t'),
            '0' => Some('\0'),
            '\\' | '\'' | '"' => Some(esc),
            'x' => self.code_point(2),
            'u' => self.code_point(4),
            'U' => self.code_point(8),
            other => Some(other),
        }
    }

    fn parse_quoted_string(&mut self) -> Option<String> {
        let quote = self.peek()?;
        if quote != '"' && quote != '\'' {
            return None;
        }
        let triple = self.peek_at(1) == Some(quote) && self.peek_at(2) == Some(quote);
        self.at += if triple { 3 } else { 1 };
        let mut out = String::new();
        loop {
            let ch = self.peek()?;
            if triple {
                if ch == quote && self.peek_at(1) == Some(quote) && self.peek_at(2) == Some(quote) {
                    self.at += 3;
                    return Some(out);
                }
            } else if ch == quote {
                self.at += 1;
                return Some(out);
            }
            self.at += 1;
            if ch == '\\' {
                let next = self.peek()?;
                if matches!(
                    next,
                    'n' | 'r' | 't' | '0' | '\\' | '\'' | '"' | 'x' | 'u' | 'U'
                ) {
                    out.push(self.parse_escape()?);
                } else {
                    self.at += 1;
                    out.push('\\');
                    out.push(next);
                }
            } else {
                out.push(ch);
            }
        }
    }

    fn keyword(&mut self, word: &str) -> bool {
        if !self.starts_with_str(word) {
            return false;
        }
        let len = word.chars().count();
        let after = self.peek_at(len);
        if matches!(after, Some(c) if c.is_ascii_alphanumeric() || c == '_') {
            return false;
        }
        self.at += len;
        true
    }

    fn parse_number(&mut self) -> Option<Value> {
        let start = self.at;
        if matches!(self.peek(), Some('-' | '+')) {
            self.at += 1;
        }
        let mut digits = 0usize;
        let mut is_float = false;
        while let Some(ch) = self.peek() {
            match ch {
                '0'..='9' => digits += 1,
                '_' => {}
                '.' if !is_float => is_float = true,
                'e' | 'E' => {
                    is_float = true;
                    self.at += 1;
                    if matches!(self.peek(), Some('-' | '+')) {
                        self.at += 1;
                    }
                    continue;
                }
                _ => break,
            }
            self.at += 1;
        }
        if digits == 0 || matches!(self.peek(), Some(c) if c.is_ascii_alphabetic() || c == '_') {
            self.at = start;
            return None;
        }
        let raw: String = self.chars[start..self.at]
            .iter()
            .filter(|c| **c != '_')
            .collect();
        if is_float {
            Number::from_f64(raw.parse::<f64>().ok()?).map(Value::Number)
        } else {
            raw.parse::<i64>()
                .ok()
                .map(|n| Value::Number(Number::from(n)))
                .or_else(|| Number::from_f64(raw.parse::<f64>().ok()?).map(Value::Number))
        }
    }

    fn parse_balanced_container(&mut self, open: char, close: char) -> Option<Value> {
        if self.peek() != Some(open) {
            return None;
        }
        let start = self.at;
        let mut depth = 0usize;
        let mut quote: Option<char> = None;
        let mut escaped = false;
        while let Some(ch) = self.peek() {
            self.at += 1;
            if let Some(q) = quote {
                if escaped {
                    escaped = false;
                } else if ch == '\\' {
                    escaped = true;
                } else if ch == q {
                    quote = None;
                }
                continue;
            }
            if ch == '"' || ch == '\'' {
                quote = Some(ch);
            } else if ch == open {
                depth += 1;
            } else if ch == close {
                depth -= 1;
                if depth == 0 {
                    let snippet: String = self.chars[start..self.at].iter().collect();
                    return pyjson::loads(&snippet)
                        .ok()
                        .or_else(|| crate::parse::pyliteral::literal_eval(&snippet));
                }
            }
        }
        self.at = start;
        None
    }

    fn parse_python_value(&mut self) -> Option<Value> {
        match self.peek()? {
            '"' | '\'' => self.parse_quoted_string().map(Value::String),
            '[' => self.parse_balanced_container('[', ']'),
            '{' => self.parse_balanced_container('{', '}'),
            _ => {
                if self.keyword("True") || self.keyword("true") {
                    return Some(Value::Bool(true));
                }
                if self.keyword("False") || self.keyword("false") {
                    return Some(Value::Bool(false));
                }
                if self.keyword("None") || self.keyword("null") {
                    return Some(Value::Null);
                }
                self.parse_number()
            }
        }
    }

    /// Look ahead from current position to see if `key:` or `key=` starts here.
    /// Avoids misreading `https://` as a key-value pair.
    fn starts_kv_pair(&self) -> bool {
        let mut idx = self.at;
        let Some(first) = self.chars.get(idx).copied() else {
            return false;
        };
        if !(first.is_ascii_alphabetic() || first == '_') {
            return false;
        }
        idx += 1;
        while matches!(self.chars.get(idx), Some(c) if c.is_ascii_alphanumeric() || *c == '_') {
            idx += 1;
        }
        while matches!(self.chars.get(idx), Some(' ' | '\t')) {
            idx += 1;
        }
        match self.chars.get(idx).copied() {
            Some('=') => self.chars.get(idx + 1).copied() != Some('='),
            Some(':') => {
                !(self.chars.get(idx + 1).copied() == Some('/')
                    && self.chars.get(idx + 2).copied() == Some('/'))
            }
            _ => false,
        }
    }
}

fn scalar_from_unquoted(token: &str) -> Value {
    match token {
        "true" | "True" => Value::Bool(true),
        "false" | "False" => Value::Bool(false),
        "null" | "None" => Value::Null,
        _ => {
            if let Ok(n) = token.parse::<i64>() {
                Value::Number(Number::from(n))
            } else if (token.contains('.') || token.contains('e') || token.contains('E'))
                && token.parse::<f64>().is_ok()
            {
                Number::from_f64(token.parse::<f64>().expect("checked"))
                    .map(Value::Number)
                    .unwrap_or_else(|| Value::String(token.to_string()))
            } else {
                Value::String(token.to_string())
            }
        }
    }
}

fn parse_react_kv(line: &str) -> Option<Map<String, Value>> {
    let mut cursor = Cursor::new(line);
    let mut args = Map::new();
    loop {
        cursor.skip_hspace();
        while cursor.eat(',') {
            cursor.skip_hspace();
        }
        if cursor.rest_is_empty() {
            break;
        }
        let key = cursor.parse_ident()?;
        cursor.skip_hspace();
        if !(cursor.eat(':') || cursor.eat('=')) {
            return None;
        }
        cursor.skip_hspace();
        let value = match cursor.peek() {
            Some('"' | '\'') => Value::String(cursor.parse_quoted_string()?),
            Some('`') => {
                cursor.at += 1;
                let start = cursor.at;
                while let Some(ch) = cursor.peek() {
                    if ch == '`' {
                        break;
                    }
                    cursor.at += 1;
                }
                if cursor.peek() != Some('`') {
                    return None;
                }
                let text: String = cursor.chars[start..cursor.at].iter().collect();
                cursor.at += 1;
                Value::String(text)
            }
            _ => {
                let start = cursor.at;
                while let Some(ch) = cursor.peek() {
                    if ch == ',' {
                        break;
                    }
                    if ch.is_whitespace() {
                        let mark = cursor.at;
                        cursor.skip_hspace();
                        let next_is_kv = cursor.starts_kv_pair();
                        cursor.at = mark;
                        if next_is_kv {
                            break;
                        }
                    }
                    cursor.at += 1;
                }
                let raw: String = cursor.chars[start..cursor.at].iter().collect();
                let trimmed = raw.trim();
                if trimmed.is_empty() {
                    return None;
                }
                scalar_from_unquoted(trimmed)
            }
        };
        args.insert(key, value);
    }
    if args.is_empty() {
        None
    } else {
        Some(args)
    }
}

fn parse_json_object_tolerant(text: &str) -> Option<Map<String, Value>> {
    let span = first_balanced_object(text)
        .map(|(start, end)| &text[start..end])
        .unwrap_or(text);
    let value = pyjson::loads(span)
        .ok()
        .or_else(|| {
            let no_comma = trailing_comma().replace_all(span, "$1");
            pyjson::loads(&no_comma).ok()
        })
        .or_else(|| {
            let escaped = escape_unescaped_newlines(span);
            pyjson::loads(&escaped).ok()
        })
        .or_else(|| crate::parse::pyliteral::literal_eval(span))
        .or_else(|| {
            truncation_repairs(span)
                .into_iter()
                .find_map(|cand| pyjson::loads(&cand).ok())
        })?;
    match value {
        Value::Object(map) => Some(map),
        _ => None,
    }
}

fn parse_react_input_args(tail: &str) -> Option<Map<String, Value>> {
    let first_line = tail.lines().next().unwrap_or("").trim();
    if first_line.is_empty() {
        // Multiline fenced/JSON block on subsequent lines.
        let rest = tail.trim_start();
        if let Some(fenced) = fenced_content_block()
            .captures(rest)
            .ok()
            .flatten()
            .and_then(|c| c.get(1).map(|g| g.as_str().trim()))
        {
            if fenced.starts_with('{') {
                return parse_json_object_tolerant(fenced);
            }
        }
        if rest.starts_with('{') {
            return parse_json_object_tolerant(rest);
        }
        return Some(Map::new());
    }
    let unwrapped = if first_line.starts_with('`')
        && first_line.ends_with('`')
        && first_line.len() >= 2
        && !first_line.starts_with("```")
    {
        first_line[1..first_line.len() - 1].trim()
    } else {
        first_line
    };
    if unwrapped.starts_with("```") {
        if let Some(fenced) = fenced_content_block()
            .captures(tail.trim_start())
            .ok()
            .flatten()
            .and_then(|c| c.get(1).map(|g| g.as_str().trim()))
        {
            if fenced.starts_with('{') {
                return parse_json_object_tolerant(fenced);
            }
            return parse_react_kv(fenced);
        }
    }
    if unwrapped.starts_with('{') {
        let slice_src = if first_line.starts_with('`') && first_line.ends_with('`') {
            unwrapped
        } else {
            tail.trim_start()
        };
        return parse_json_object_tolerant(slice_src);
    }
    if matches!(unwrapped, "None" | "none" | "null" | "{}") {
        return Some(Map::new());
    }
    parse_react_kv(unwrapped)
}

fn match_react_pair<'a>(
    text: &'a str,
    header_re: &Regex,
    input_re: &Regex,
) -> Option<(String, &'a str)> {
    let header = header_re.captures(text).ok().flatten()?;
    let whole_header = header.get(0)?;
    let name = header.get(1)?.as_str();
    let action = accepted_action(name)?;
    let after_header = &text[whole_header.end()..];
    let input_match = input_re.find(after_header).ok().flatten()?;
    Some((action, &after_header[input_match.end()..]))
}

/// ReAct `Action: <tool>\nAction Input: <json-or-kv>` and `Tool: <tool>\nInput: <json-or-kv>`.
pub(super) fn salvage_react_format(text: &str) -> Option<Map<String, Value>> {
    let (action, tail) =
        match_react_pair(text, labeled_action_line(), react_action_input_line())
            .or_else(|| match_react_pair(text, react_tool_line(), react_tool_input_line()))?;
    let args = parse_react_input_args(tail)?;
    usable_labeled(&action, args)
}

/// Python keyword-call syntax: `web_fetch(url="...")`, `computer_click(x=420, y=310, button="left")`.
pub(super) fn salvage_python_call(text: &str) -> Option<Map<String, Value>> {
    for captures in python_call_open().captures_iter(text).flatten() {
        let Some(whole) = captures.get(0) else {
            continue;
        };
        let Some(name) = captures.get(1).map(|m| m.as_str()) else {
            continue;
        };
        let Some(action) = accepted_action(name) else {
            continue;
        };
        let mut cursor = Cursor::new(&text[whole.end()..]);
        let mut args = Map::new();
        let mut ok = true;
        loop {
            cursor.skip_ws();
            if cursor.eat(')') {
                break;
            }
            let Some(key) = cursor.parse_ident() else {
                ok = false;
                break;
            };
            cursor.skip_ws();
            if !cursor.eat('=') {
                ok = false;
                break;
            }
            cursor.skip_ws();
            let Some(value) = cursor.parse_python_value() else {
                ok = false;
                break;
            };
            args.insert(key, value);
            cursor.skip_ws();
            if cursor.eat(',') {
                continue;
            }
            if cursor.eat(')') {
                break;
            }
            ok = false;
            break;
        }
        if !ok {
            continue;
        }
        if let Some(map) = usable_labeled(&action, args) {
            return Some(map);
        }
    }
    None
}

fn collect_xml_args(pattern: &Regex, text: &str) -> Map<String, Value> {
    let mut args = Map::new();
    for captures in pattern.captures_iter(text).flatten() {
        let Some(key) = captures.get(1).map(|found| found.as_str()) else {
            continue;
        };
        let value = captures
            .get(2)
            .map(|found| found.as_str().trim().to_string())
            .unwrap_or_default();
        if !key.is_empty() && !value.is_empty() {
            args.insert(key.to_string(), Value::String(value));
        }
    }
    args
}

fn xml_name_tag() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    compiled(
        &RE,
        r"(?is)<(name|tool|action)>\s*([A-Za-z_][A-Za-z0-9_.]*)\s*</\1>",
    )
}

/// A tool call written as XML tags, with no JSON object.
pub(super) fn salvage_xml_call(text: &str) -> Option<Map<String, Value>> {
    if let Some(captures) = qwen_function_open().captures(text).ok().flatten() {
        let name = captures
            .get(1)
            .or_else(|| captures.get(2))
            .map(|found| found.as_str())
            .unwrap_or("");
        if let Some(action) = accepted_action(name) {
            let args = collect_xml_args(qwen_parameter(), text);
            return usable_labeled(&action, args);
        }
    }
    let first = text.lines().next()?.trim();
    let action = accepted_action(first).or_else(|| {
        let captures = xml_name_tag().captures(text).ok().flatten()?;
        accepted_action(captures.get(2)?.as_str())
    })?;
    let args = collect_xml_args(xml_arg_tag(), text);
    if args.is_empty() && !action.starts_with("skill.") {
        return None;
    }
    usable_labeled(&action, args)
}
