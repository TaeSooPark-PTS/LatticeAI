//! `json.loads`, reported the way CPython reports it.
//!
//! The parse-failure transcript step records `str(exc)` for a
//! `json.JSONDecodeError`, so the loop's own output carries the decoder's
//! message text and character position. `serde_json` does the parsing here —
//! it is the parser that has been proved right — and this module supplies the
//! *diagnosis*: a scanner that walks the same document and names the first
//! structural fault in CPython's vocabulary, with CPython's
//! `line L column C (char N)` suffix.
//!
//! Two honest limits, both out of contract for an action object and both
//! recorded as deviations rather than hidden:
//!
//! * `NaN` / `Infinity` / `-Infinity` are accepted by CPython's decoder and
//!   have no `serde_json::Value` representation, so they are a parse failure
//!   here.
//! * The decoder's message *wording* is CPython-version-specific (3.14 renamed
//!   several and moved two positions), which is why the parity goldens compare
//!   the `Agent did not return valid JSON:` prefix and normalise the detail.

use serde_json::Value;

/// A CPython-shaped decode failure.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DecodeError {
    /// The bare message, e.g. `Expecting value`.
    pub msg: String,
    /// Character (not byte) offset the fault was reported at.
    pub pos: usize,
    line: usize,
    column: usize,
}

impl std::fmt::Display for DecodeError {
    /// `JSONDecodeError.__str__`: `{msg}: line {lineno} column {colno} (char {pos})`.
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(
            formatter,
            "{}: line {} column {} (char {})",
            self.msg, self.line, self.column, self.pos
        )
    }
}

impl DecodeError {
    /// `JSONDecodeError.__init__`'s two derived coordinates.
    fn locate(msg: &str, pos: usize, doc: &[char]) -> Self {
        let before = &doc[..pos.min(doc.len())];
        let last_newline = before.iter().rposition(|character| *character == '\n');
        Self {
            msg: msg.to_string(),
            pos,
            line: before.iter().filter(|c| **c == '\n').count() + 1,
            // `pos - doc.rfind('\n', 0, pos)`; a missing newline is `-1` there.
            column: match last_newline {
                Some(at) => pos - at,
                None => pos + 1,
            },
        }
    }

    fn fallback(doc: &[char]) -> Self {
        Self::locate("Expecting value", 0, doc)
    }

    /// 1-based line, as CPython computes it.
    pub fn line_of(&self) -> usize {
        self.line
    }

    /// 1-based column, as CPython computes it.
    pub fn column_of(&self) -> usize {
        self.column
    }
}

/// `json.loads` — the value, or the failure CPython would have raised.
pub fn loads(text: &str) -> Result<Value, DecodeError> {
    match serde_json::from_str::<Value>(text) {
        Ok(value) => Ok(value),
        Err(_) => {
            let doc: Vec<char> = text.chars().collect();
            Err(diagnose(&doc).unwrap_or_else(|| DecodeError::fallback(&doc)))
        }
    }
}

/// The first fault a CPython-shaped scan finds, if any.
fn diagnose(doc: &[char]) -> Option<DecodeError> {
    let mut scanner = Scanner { doc };
    let start = scanner.skip_ws(0);
    match scanner.scan_once(start) {
        Err(fault) => Some(fault.into_error(doc)),
        Ok(end) => {
            let end = scanner.skip_ws(end);
            (end != doc.len()).then(|| DecodeError::locate("Extra data", end, doc))
        }
    }
}

/// A fault before it knows the document — `msg` plus a character offset.
struct Fault(&'static str, usize);

impl Fault {
    fn into_error(self, doc: &[char]) -> DecodeError {
        DecodeError::locate(self.0, self.1, doc)
    }
}

type Scan = Result<usize, Fault>;

struct Scanner<'a> {
    doc: &'a [char],
}

impl Scanner<'_> {
    fn at(&self, index: usize) -> Option<char> {
        self.doc.get(index).copied()
    }

    fn skip_ws(&self, mut index: usize) -> usize {
        while matches!(
            self.at(index),
            Some(' ') | Some('\t') | Some('\n') | Some('\r')
        ) {
            index += 1;
        }
        index
    }

    fn starts_with(&self, index: usize, word: &str) -> bool {
        let letters: Vec<char> = word.chars().collect();
        self.doc.len() >= index + letters.len() && self.doc[index..index + letters.len()] == letters
    }

    /// `_scan_once`: the value at `index`, or where a value was expected.
    fn scan_once(&mut self, index: usize) -> Scan {
        match self.at(index) {
            Some('"') => self.string(index + 1),
            Some('{') => self.object(index + 1),
            Some('[') => self.array(index + 1),
            Some('n') if self.starts_with(index, "null") => Ok(index + 4),
            Some('t') if self.starts_with(index, "true") => Ok(index + 4),
            Some('f') if self.starts_with(index, "false") => Ok(index + 5),
            _ => self.number(index).ok_or(Fault("Expecting value", index)),
        }
    }

    /// `NUMBER_RE`: `-?(0|[1-9]\d*)(\.\d+)?([eE][-+]?\d+)?`.
    fn number(&self, start: usize) -> Option<usize> {
        let mut index = start;
        if self.at(index) == Some('-') {
            index += 1;
        }
        match self.at(index) {
            Some('0') => index += 1,
            Some('1'..='9') => {
                while matches!(self.at(index), Some('0'..='9')) {
                    index += 1;
                }
            }
            _ => return None,
        }
        if self.at(index) == Some('.') && matches!(self.at(index + 1), Some('0'..='9')) {
            index += 1;
            while matches!(self.at(index), Some('0'..='9')) {
                index += 1;
            }
        }
        if matches!(self.at(index), Some('e') | Some('E')) {
            let mut ahead = index + 1;
            if matches!(self.at(ahead), Some('+') | Some('-')) {
                ahead += 1;
            }
            if matches!(self.at(ahead), Some('0'..='9')) {
                index = ahead;
                while matches!(self.at(index), Some('0'..='9')) {
                    index += 1;
                }
            }
        }
        Some(index)
    }

    /// `py_scanstring`, entered just after the opening quote.
    fn string(&self, after_quote: usize) -> Scan {
        let begin = after_quote - 1;
        let mut index = after_quote;
        loop {
            let Some(character) = self.at(index) else {
                return Err(Fault("Unterminated string starting at", begin));
            };
            match character {
                '"' => return Ok(index + 1),
                '\\' => {
                    let escape = index + 1;
                    match self.at(escape) {
                        None => return Err(Fault("Unterminated string starting at", begin)),
                        Some('u') => {
                            let digits = escape + 1;
                            let hex_ok = (0..4).all(|offset| {
                                self.at(digits + offset)
                                    .is_some_and(|c| c.is_ascii_hexdigit())
                            });
                            if !hex_ok {
                                return Err(Fault("Invalid \\uXXXX escape", escape));
                            }
                            index = digits + 4;
                        }
                        Some('"' | '\\' | '/' | 'b' | 'f' | 'n' | 'r' | 't') => index = escape + 1,
                        Some(_) => return Err(Fault("Invalid \\escape", index)),
                    }
                }
                control if (control as u32) < 0x20 => {
                    return Err(Fault("Invalid control character at", index))
                }
                _ => index += 1,
            }
        }
    }

    /// `JSONObject`, entered just after `{`.
    fn object(&mut self, after_brace: usize) -> Scan {
        let mut end = self.skip_ws(after_brace);
        if self.at(end) == Some('}') {
            return Ok(end + 1);
        }
        if self.at(end) != Some('"') {
            return Err(Fault(
                "Expecting property name enclosed in double quotes",
                end,
            ));
        }
        loop {
            end = self.string(end + 1)?;
            end = self.skip_ws(end);
            if self.at(end) != Some(':') {
                return Err(Fault("Expecting ':' delimiter", end));
            }
            end = self.skip_ws(end + 1);
            end = self.scan_once(end)?;
            end = self.skip_ws(end);
            let next = self.at(end);
            end += 1;
            match next {
                Some('}') => return Ok(end),
                Some(',') => {}
                _ => return Err(Fault("Expecting ',' delimiter", end - 1)),
            }
            let comma = end - 1;
            end = self.skip_ws(end);
            if self.at(end) == Some('}') {
                return Err(Fault("Illegal trailing comma before end of object", comma));
            }
            if self.at(end) != Some('"') {
                return Err(Fault(
                    "Expecting property name enclosed in double quotes",
                    comma,
                ));
            }
        }
    }

    /// `JSONArray`, entered just after `[`.
    fn array(&mut self, after_bracket: usize) -> Scan {
        let mut end = self.skip_ws(after_bracket);
        if self.at(end) == Some(']') {
            return Ok(end + 1);
        }
        loop {
            end = self.scan_once(end)?;
            end = self.skip_ws(end);
            let next = self.at(end);
            end += 1;
            match next {
                Some(']') => return Ok(end),
                Some(',') => {}
                _ => return Err(Fault("Expecting ',' delimiter", end - 1)),
            }
            let comma = end - 1;
            end = self.skip_ws(end);
            if self.at(end) == Some(']') {
                return Err(Fault("Illegal trailing comma before end of array", comma));
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    /// Every string here was read off CPython 3.14 rather than reasoned about.
    #[test]
    fn the_messages_are_the_messages_cpython_prints() {
        let cases = [
            ("not json", "Expecting value: line 1 column 1 (char 0)"),
            (r#"{"a": }"#, "Expecting value: line 1 column 7 (char 6)"),
            (
                "{a: 1}",
                "Expecting property name enclosed in double quotes: line 1 column 2 (char 1)",
            ),
            (
                r#"{"a" 1}"#,
                "Expecting ':' delimiter: line 1 column 6 (char 5)",
            ),
            (
                r#"{"a":1,}"#,
                "Illegal trailing comma before end of object: line 1 column 7 (char 6)",
            ),
            (
                r#"{"a":1"#,
                "Expecting ',' delimiter: line 1 column 7 (char 6)",
            ),
            (
                "[1,]",
                "Illegal trailing comma before end of array: line 1 column 3 (char 2)",
            ),
            (r#"{"a":"x"}}"#, "Extra data: line 1 column 10 (char 9)"),
            ("[", "Expecting value: line 1 column 2 (char 1)"),
            (
                "{",
                "Expecting property name enclosed in double quotes: line 1 column 2 (char 1)",
            ),
            ("", "Expecting value: line 1 column 1 (char 0)"),
            ("   ", "Expecting value: line 1 column 4 (char 3)"),
            ("[1 2]", "Expecting ',' delimiter: line 1 column 4 (char 3)"),
            (
                r#"{"a":1 "b":2}"#,
                "Expecting ',' delimiter: line 1 column 8 (char 7)",
            ),
            ("nul", "Expecting value: line 1 column 1 (char 0)"),
            (
                r#"{"a": "unterminated}"#,
                "Unterminated string starting at: line 1 column 7 (char 6)",
            ),
            (
                r#"{"a": "bad\qescape"}"#,
                "Invalid \\escape: line 1 column 11 (char 10)",
            ),
            (
                r#"{"a": "\uZZZZ"}"#,
                "Invalid \\uXXXX escape: line 1 column 9 (char 8)",
            ),
        ];
        for (text, expected) in cases {
            let error = loads(text).expect_err(text);
            assert_eq!(error.to_string(), expected, "input {text:?}");
        }
    }

    #[test]
    fn a_control_character_is_located_at_the_character() {
        let error = loads("{\"a\": \"ctl\u{1}char\"}").expect_err("control");
        assert_eq!(
            error.to_string(),
            "Invalid control character at: line 1 column 11 (char 10)"
        );
    }

    #[test]
    fn line_and_column_count_from_the_last_newline() {
        let error = loads("{\"a\":1}\n\n{\"b\":2}").expect_err("extra data");
        assert_eq!(error.to_string(), "Extra data: line 3 column 1 (char 9)");
        assert_eq!(error.line_of(), 3);
    }

    #[test]
    fn the_position_is_a_character_offset_not_a_byte_offset() {
        // Two Korean characters are six bytes and two positions.
        let error = loads("{\"작업\": }").expect_err("value");
        assert_eq!(error.pos, 7);
        assert_eq!(
            error.to_string(),
            "Expecting value: line 1 column 8 (char 7)"
        );
    }

    #[test]
    fn valid_documents_parse_to_the_value_serde_would_give() {
        assert_eq!(
            loads(r#"{"action":"final"}"#),
            Ok(json!({"action": "final"}))
        );
        assert_eq!(
            loads("[1, 2.5, true, null]"),
            Ok(json!([1, 2.5, true, null]))
        );
        assert_eq!(loads(r#" "text" "#), Ok(json!("text")));
        assert_eq!(loads("-0.5e2"), Ok(json!(-50.0)));
    }

    #[test]
    fn a_disagreement_between_parser_and_scanner_still_answers() {
        // CPython accepts the non-finite constants; serde_json has no value for
        // them, so the port fails closed with a locatable message rather than
        // pretending to have parsed something.
        let error = loads("NaN").expect_err("NaN is out of the value space");
        assert_eq!(error.msg, "Expecting value");
        assert_eq!(error.pos, 0);
    }
}
