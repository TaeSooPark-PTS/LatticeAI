//! `ast.parse`, structurally — the one call this port cannot make.

// ── `ast.parse`, structurally ───────────────────────────────────────────────

/// A refusal from [`python_parses`], shaped like `SyntaxError.{msg,lineno}`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SyntaxFault {
    pub msg: String,
    pub line: usize,
}

/// Whether `source` is *structurally* Python: brackets balanced, strings
/// terminated, no character that cannot occur outside a string or a comment.
///
/// Deliberately not a parser. Everything it accepts, `ast.parse` might still
/// reject; everything it rejects, `ast.parse` rejects too.
pub fn python_parses(source: &str) -> Result<(), SyntaxFault> {
    let chars: Vec<char> = source.chars().collect();
    let count = chars.len();
    let mut stack: Vec<(char, usize)> = Vec::new();
    let (mut index, mut line) = (0usize, 1usize);
    // Whether the next non-blank character opens a *logical* line: not inside
    // brackets, not after a backslash continuation, not mid-expression.
    let mut opens_statement = true;
    let fault = |msg: &str, line: usize| SyntaxFault {
        msg: msg.to_string(),
        line,
    };
    while index < count {
        let ch = chars[index];
        // A statement can never begin with a binary operator or a separator.
        // This is the rule that catches prose written into a `.py` file
        // (`<think>…`, `= 결과`), which the bracket and string checks below
        // would happily accept.
        if opens_statement && "<>=/%&|^,:;".contains(ch) {
            return Err(fault("invalid syntax", line));
        }
        if !ch.is_whitespace() && ch != '#' {
            opens_statement = false;
        }
        match ch {
            '\n' => {
                line += 1;
                index += 1;
                if stack.is_empty() {
                    opens_statement = true;
                }
            }
            '#' => {
                while index < count && chars[index] != '\n' {
                    index += 1;
                }
            }
            '\\' => {
                // A line continuation, or an escape this scanner does not judge.
                if index + 1 < count && chars[index + 1] == '\n' {
                    line += 1;
                }
                index += 2;
            }
            '\'' | '"' => {
                let opened_at = line;
                let triple = index + 2 < count && chars[index + 1] == ch && chars[index + 2] == ch;
                index += if triple { 3 } else { 1 };
                let mut closed = false;
                while index < count {
                    let current = chars[index];
                    if current == '\\' {
                        if index + 1 < count && chars[index + 1] == '\n' {
                            line += 1;
                        }
                        index += 2;
                        continue;
                    }
                    if current == '\n' {
                        if !triple {
                            return Err(fault(
                                &format!("unterminated string literal (detected at line {line})"),
                                line,
                            ));
                        }
                        line += 1;
                        index += 1;
                        continue;
                    }
                    if current == ch {
                        if !triple {
                            index += 1;
                            closed = true;
                            break;
                        }
                        if index + 2 < count && chars[index + 1] == ch && chars[index + 2] == ch {
                            index += 3;
                            closed = true;
                            break;
                        }
                    }
                    index += 1;
                }
                if !closed {
                    let kind = if triple {
                        "triple-quoted string"
                    } else {
                        "string"
                    };
                    // CPython reports the last line that *has* content, so a
                    // source ending in a newline does not add one.
                    let detected = if chars.last() == Some(&'\n') {
                        line - 1
                    } else {
                        line
                    };
                    return Err(fault(
                        &format!("unterminated {kind} literal (detected at line {detected})"),
                        opened_at,
                    ));
                }
            }
            '(' | '[' | '{' => {
                stack.push((ch, line));
                index += 1;
            }
            ')' | ']' | '}' => {
                let opener = match ch {
                    ')' => '(',
                    ']' => '[',
                    _ => '{',
                };
                match stack.pop() {
                    None => return Err(fault(&format!("unmatched '{ch}'"), line)),
                    Some((open, _)) if open != opener => {
                        return Err(fault(
                            &format!(
                                "closing parenthesis '{ch}' does not match opening parenthesis '{open}'"
                            ),
                            line,
                        ));
                    }
                    Some(_) => {}
                }
                index += 1;
            }
            // Characters that cannot occur in Python source outside a string
            // or a comment. `!` is the one that catches prose ("Sure! Here…"),
            // because its only legal use is `!=`.
            '!' if !(index + 1 < count && chars[index + 1] == '=') => {
                return Err(fault("invalid syntax", line));
            }
            '$' | '?' | '`' => return Err(fault("invalid syntax", line)),
            _ => index += 1,
        }
    }
    match stack.first() {
        Some((opener, at)) => Err(fault(&format!("'{opener}' was never closed"), *at)),
        None => Ok(()),
    }
}
