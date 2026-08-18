//! `ast.literal_eval`, restricted to the values an action object can hold.
//!
//! The last rung of `extract_action_details`'s tolerance chain. Weak local
//! models routinely emit a **Python dict literal** instead of JSON — single
//! quotes, `True`/`False`/`None`, a trailing comma — and Python recovers those
//! deterministically without evaluating code. A port that stopped at JSON would
//! silently fail every run those models drive.
//!
//! Deliberately a subset: dicts, lists, tuples, strings, numbers, `True`,
//! `False`, `None`. Everything else (f-strings, comprehensions, operators,
//! names, complex literals) is a parse failure here exactly as it is a
//! `ValueError` there. The error *text* is never observed — Python raises with
//! the JSON decoder's message, not this one — so this parser reports only
//! success or failure.

use serde_json::{Map, Number, Value};

/// Parse one Python literal. `None` when the text is not one.
pub fn literal_eval(text: &str) -> Option<Value> {
    let chars: Vec<char> = text.chars().collect();
    let mut parser = Parser { chars, at: 0 };
    parser.skip_space();
    let value = parser.value()?;
    parser.skip_space();
    if parser.at == parser.chars.len() {
        Some(value)
    } else {
        None
    }
}

struct Parser {
    chars: Vec<char>,
    at: usize,
}

impl Parser {
    fn peek(&self) -> Option<char> {
        self.chars.get(self.at).copied()
    }

    fn skip_space(&mut self) {
        while matches!(self.peek(), Some(c) if c.is_whitespace()) {
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

    fn keyword(&mut self, word: &str) -> bool {
        let letters: Vec<char> = word.chars().collect();
        if self.chars[self.at..].starts_with(&letters) {
            let after = self.chars.get(self.at + letters.len()).copied();
            // `Nonesuch` is a name, not `None` followed by junk.
            if !matches!(after, Some(c) if c.is_alphanumeric() || c == '_') {
                self.at += letters.len();
                return true;
            }
        }
        false
    }

    fn value(&mut self) -> Option<Value> {
        match self.peek()? {
            '{' => self.dict(),
            '[' => self.sequence(']'),
            '(' => self.sequence(')'),
            '\'' | '"' => self.string().map(Value::String),
            _ => {
                if self.keyword("True") {
                    return Some(Value::Bool(true));
                }
                if self.keyword("False") {
                    return Some(Value::Bool(false));
                }
                if self.keyword("None") {
                    return Some(Value::Null);
                }
                self.number()
            }
        }
    }

    fn dict(&mut self) -> Option<Value> {
        self.at += 1;
        let mut map = Map::new();
        loop {
            self.skip_space();
            if self.eat('}') {
                return Some(Value::Object(map));
            }
            let key = self.value()?;
            // JSON object keys are strings; Python's may be any hashable, so a
            // non-string key is out of the subset rather than coerced.
            let key = match key {
                Value::String(text) => text,
                _ => return None,
            };
            self.skip_space();
            if !self.eat(':') {
                return None;
            }
            self.skip_space();
            let value = self.value()?;
            map.insert(key, value);
            self.skip_space();
            if self.eat(',') {
                continue;
            }
            if self.eat('}') {
                return Some(Value::Object(map));
            }
            return None;
        }
    }

    /// Lists and tuples share a body; both become a JSON array.
    fn sequence(&mut self, close: char) -> Option<Value> {
        self.at += 1;
        let mut items = Vec::new();
        loop {
            self.skip_space();
            if self.eat(close) {
                return Some(Value::Array(items));
            }
            items.push(self.value()?);
            self.skip_space();
            if self.eat(',') {
                continue;
            }
            if self.eat(close) {
                return Some(Value::Array(items));
            }
            return None;
        }
    }

    fn string(&mut self) -> Option<String> {
        let quote = self.peek()?;
        self.at += 1;
        let mut out = String::new();
        loop {
            let character = self.peek()?;
            self.at += 1;
            if character == quote {
                // Adjacent literals concatenate in Python: 'a' 'b' == 'ab'.
                let mark = self.at;
                self.skip_space();
                if matches!(self.peek(), Some('\'') | Some('"')) {
                    out.push_str(&self.string()?);
                } else {
                    self.at = mark;
                }
                return Some(out);
            }
            if character != '\\' {
                out.push(character);
                continue;
            }
            let escape = self.peek()?;
            self.at += 1;
            match escape {
                'n' => out.push('\n'),
                't' => out.push('\t'),
                'r' => out.push('\r'),
                '0' => out.push('\0'),
                'a' => out.push('\u{7}'),
                'b' => out.push('\u{8}'),
                'f' => out.push('\u{c}'),
                'v' => out.push('\u{b}'),
                '\\' | '\'' | '"' => out.push(escape),
                '\n' => {}
                'x' => out.push(self.code_point(2)?),
                'u' => out.push(self.code_point(4)?),
                'U' => out.push(self.code_point(8)?),
                // Python keeps an unrecognised escape verbatim, backslash and all.
                other => {
                    out.push('\\');
                    out.push(other);
                }
            }
        }
    }

    fn code_point(&mut self, width: usize) -> Option<char> {
        let end = self.at.checked_add(width)?;
        let digits: String = self.chars.get(self.at..end)?.iter().collect();
        self.at = end;
        char::from_u32(u32::from_str_radix(&digits, 16).ok()?)
    }

    fn number(&mut self) -> Option<Value> {
        let start = self.at;
        if matches!(self.peek(), Some('-') | Some('+')) {
            self.at += 1;
        }
        let mut digits = 0usize;
        let mut float = false;
        while let Some(character) = self.peek() {
            match character {
                '0'..='9' => digits += 1,
                // Python allows `1_000`; the separators are dropped below.
                '_' => {}
                '.' if !float => float = true,
                'e' | 'E' => {
                    float = true;
                    self.at += 1;
                    if matches!(self.peek(), Some('-') | Some('+')) {
                        self.at += 1;
                    }
                    continue;
                }
                _ => break,
            }
            self.at += 1;
        }
        if digits == 0 {
            self.at = start;
            return None;
        }
        let text: String = self.chars[start..self.at]
            .iter()
            .filter(|c| **c != '_')
            .collect();
        if float {
            Number::from_f64(text.parse::<f64>().ok()?).map(Value::Number)
        } else {
            text.parse::<i64>()
                .ok()
                .map(|value| Value::Number(Number::from(value)))
                .or_else(|| Number::from_f64(text.parse::<f64>().ok()?).map(Value::Number))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn the_weak_model_dict_literal_parses() {
        let parsed = literal_eval("{'action': 'write_file', 'args': {'path': 'a.md'}}");
        assert_eq!(
            parsed,
            Some(json!({"action": "write_file", "args": {"path": "a.md"}}))
        );
    }

    #[test]
    fn python_constants_are_not_json_constants() {
        assert_eq!(
            literal_eval("{'ok': True, 'bad': False, 'nothing': None}"),
            Some(json!({"ok": true, "bad": false, "nothing": null}))
        );
    }

    #[test]
    fn trailing_commas_and_whitespace_are_legal_in_a_literal() {
        assert_eq!(
            literal_eval("{\n  'a': [1, 2, 3,],\n}"),
            Some(json!({"a": [1, 2, 3]}))
        );
        assert_eq!(literal_eval("  [ ]  "), Some(json!([])));
    }

    #[test]
    fn tuples_become_arrays_because_json_has_no_tuple() {
        assert_eq!(literal_eval("(1, 'two')"), Some(json!([1, "two"])));
    }

    #[test]
    fn numbers_cover_the_python_spellings() {
        assert_eq!(literal_eval("-12"), Some(json!(-12)));
        assert_eq!(literal_eval("1_000"), Some(json!(1000)));
        assert_eq!(literal_eval("2.5e2"), Some(json!(250.0)));
        assert_eq!(literal_eval("+7"), Some(json!(7)));
        assert_eq!(literal_eval("."), None);
    }

    #[test]
    fn escapes_follow_python_including_the_unknown_one() {
        assert_eq!(literal_eval(r"'a\nb'"), Some(json!("a\nb")));
        assert_eq!(literal_eval(r"'\x41B'"), Some(json!("AB")));
        assert_eq!(literal_eval(r"'it\'s'"), Some(json!("it's")));
        // `\d` is not an escape: Python keeps both characters.
        assert_eq!(literal_eval(r"'\d'"), Some(json!(r"\d")));
        assert_eq!(literal_eval("'a' 'b'"), Some(json!("ab")));
    }

    #[test]
    fn everything_outside_the_subset_is_a_parse_failure() {
        for text in [
            "__import__('os')",
            "1 + 1",
            "{'a': undefined}",
            "{'a': 1",
            "{1: 'int key'}",
            "Nonesuch",
            "",
            "{'a': 1} trailing",
        ] {
            assert_eq!(literal_eval(text), None, "{text} must not parse");
        }
    }
}
