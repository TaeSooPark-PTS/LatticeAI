//! `shlex.split` — CPython's POSIX lexer, not a lookalike.
//!
//! Every rule the command validator enforces is enforced on the *tokens*, so a
//! splitter that disagrees with Python is a hole in all of them at once: a
//! command that Python splits into `["cat", "/etc/passwd"]` and Rust splits into
//! `["cat /etc/passwd"]` would be waved through by an argument check that never
//! sees an argument.
//!
//! This is `shlex.split(s)`, i.e. `posix=True, comments=False,
//! whitespace_split=True`, transcribed from `shlex.read_token`:
//!
//! * whitespace is exactly `" \t\r\n"`;
//! * outside quotes, `\` escapes the next character and disappears;
//! * inside `'...'` nothing is special but the closing quote — a backslash is
//!   literal;
//! * inside `"..."`, `\` escapes only `"` and `\`; before anything else the
//!   backslash is *kept* (`"a\nb"` stays `a\nb`);
//! * an empty quoted string is a token (`''` → `[""]`), because the lexer
//!   remembers that it was quoted;
//! * an unterminated quote or a trailing backslash is an error, and the message
//!   is Python's own so the goldens can compare it.

/// Why a command string could not be split.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ShlexError {
    /// Python: `ValueError("No closing quotation")`.
    NoClosingQuotation,
    /// Python: `ValueError("No escaped character")`.
    NoEscapedCharacter,
}

impl ShlexError {
    pub fn message(&self) -> &'static str {
        match self {
            ShlexError::NoClosingQuotation => "No closing quotation",
            ShlexError::NoEscapedCharacter => "No escaped character",
        }
    }
}

impl std::fmt::Display for ShlexError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(self.message())
    }
}

impl std::error::Error for ShlexError {}

#[derive(Clone, Copy, PartialEq, Eq)]
enum State {
    /// `shlex`'s `' '` — between tokens.
    Space,
    /// `shlex`'s `'a'` — inside an unquoted word.
    Word,
    /// Inside `'` or `"`.
    Quote(char),
}

const WHITESPACE: [char; 4] = [' ', '\t', '\r', '\n'];

/// Split `input` the way `shlex.split` does.
pub fn split(input: &str) -> Result<Vec<String>, ShlexError> {
    let mut tokens: Vec<String> = Vec::new();
    let mut token = String::new();
    let mut quoted = false;
    let mut state = State::Space;
    // `Some(state)` while the previous character was the escape character; the
    // payload is `escapedstate`, the state to return to.
    let mut escaped: Option<State> = None;

    for ch in input.chars() {
        if let Some(back) = escaped.take() {
            if let State::Quote(quote) = back {
                // Inside a double-quoted string only the quote itself and the
                // escape character may be escaped; anything else keeps the
                // backslash as a literal character.
                if ch != '\\' && ch != quote {
                    token.push('\\');
                }
            }
            token.push(ch);
            state = back;
            continue;
        }
        match state {
            State::Space => {
                if WHITESPACE.contains(&ch) {
                    continue;
                } else if ch == '\\' {
                    escaped = Some(State::Word);
                } else if ch == '\'' || ch == '"' {
                    quoted = true;
                    state = State::Quote(ch);
                } else {
                    token.push(ch);
                    state = State::Word;
                }
            }
            State::Word => {
                if WHITESPACE.contains(&ch) {
                    state = State::Space;
                    if !token.is_empty() || quoted {
                        tokens.push(std::mem::take(&mut token));
                        quoted = false;
                    }
                } else if ch == '\'' || ch == '"' {
                    quoted = true;
                    state = State::Quote(ch);
                } else if ch == '\\' {
                    escaped = Some(State::Word);
                } else {
                    token.push(ch);
                }
            }
            State::Quote(quote) => {
                if ch == quote {
                    state = State::Word;
                } else if ch == '\\' && quote == '"' {
                    escaped = Some(State::Quote(quote));
                } else {
                    token.push(ch);
                }
            }
        }
    }

    if escaped.is_some() {
        return Err(ShlexError::NoEscapedCharacter);
    }
    if let State::Quote(_) = state {
        return Err(ShlexError::NoClosingQuotation);
    }
    if !token.is_empty() || quoted {
        tokens.push(token);
    }
    Ok(tokens)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ok(input: &str) -> Vec<String> {
        split(input).unwrap_or_else(|err| panic!("{input:?} must split: {err}"))
    }

    #[test]
    fn plain_words_split_on_any_whitespace() {
        assert_eq!(ok("ls"), ["ls"]);
        assert_eq!(ok("ls -la"), ["ls", "-la"]);
        assert_eq!(ok("a b  c"), ["a", "b", "c"]);
        assert_eq!(ok("ls\t-la"), ["ls", "-la"]);
        assert_eq!(ok("ls\n-la"), ["ls", "-la"]);
        assert_eq!(
            ok("  leading and trailing  "),
            ["leading", "and", "trailing"]
        );
        assert!(ok("").is_empty());
        assert!(ok("   ").is_empty());
    }

    #[test]
    fn quotes_join_and_disappear() {
        assert_eq!(ok("cat 'a b.txt'"), ["cat", "a b.txt"]);
        assert_eq!(ok("cat \"a b\""), ["cat", "a b"]);
        assert_eq!(ok("a\"b\"c"), ["abc"]);
        assert_eq!(ok("x'y'z"), ["xyz"]);
        assert_eq!(ok("cat \"a\"b'c'"), ["cat", "abc"]);
    }

    #[test]
    fn an_empty_quoted_string_is_still_a_token() {
        assert_eq!(ok("''"), [""]);
        assert_eq!(ok("\"\""), [""]);
        assert_eq!(ok("cat ''"), ["cat", ""]);
        assert_eq!(ok("cat '' ''"), ["cat", "", ""]);
    }

    #[test]
    fn escaping_differs_inside_and_outside_double_quotes() {
        assert_eq!(ok("cat a\\ b"), ["cat", "a b"]);
        assert_eq!(ok("cat a\\\\b"), ["cat", "a\\b"]);
        // Outside quotes the backslash vanishes; inside double quotes it stays
        // in front of anything that is not a quote or a backslash.
        assert_eq!(ok("cat \"a\\nb\""), ["cat", "a\\nb"]);
        assert_eq!(ok("cat \"a\\\"b\""), ["cat", "a\"b"]);
        assert_eq!(ok("cat \"a\\\\b\""), ["cat", "a\\b"]);
        // Single quotes escape nothing at all.
        assert_eq!(ok("cat 'a\\nb'"), ["cat", "a\\nb"]);
        assert_eq!(ok("cat 'a\\'"), ["cat", "a\\"]);
    }

    #[test]
    fn unterminated_input_is_an_error_with_pythons_message() {
        assert_eq!(
            split("cat 'unterminated"),
            Err(ShlexError::NoClosingQuotation)
        );
        assert_eq!(
            split("cat \"unterminated"),
            Err(ShlexError::NoClosingQuotation)
        );
        assert_eq!(split("cat trailing\\"), Err(ShlexError::NoEscapedCharacter));
        assert_eq!(
            split("cat \"trailing\\"),
            Err(ShlexError::NoEscapedCharacter)
        );
        assert_eq!(
            ShlexError::NoClosingQuotation.to_string(),
            "No closing quotation"
        );
        assert_eq!(
            ShlexError::NoEscapedCharacter.to_string(),
            "No escaped character"
        );
    }

    #[test]
    fn shell_metacharacters_are_ordinary_characters_here() {
        // The operator ban is a separate rule over the raw string; the splitter
        // must not pre-empt it by mangling these.
        assert_eq!(ok("cat $(ls)"), ["cat", "$(ls)"]);
        assert_eq!(ok("cat `ls`"), ["cat", "`ls`"]);
        assert_eq!(ok("cat a=b --c=d"), ["cat", "a=b", "--c=d"]);
    }

    #[test]
    fn non_ascii_survives_intact() {
        assert_eq!(ok("cat 노트/메모.txt"), ["cat", "노트/메모.txt"]);
        assert_eq!(ok("cat '노트/메모.txt'"), ["cat", "노트/메모.txt"]);
    }
}
