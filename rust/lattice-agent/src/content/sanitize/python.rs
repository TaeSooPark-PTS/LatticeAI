#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SyntaxFault {
    pub msg: String,
    pub line: usize,
}

pub fn python_parses(source: &str) -> Result<(), SyntaxFault> {
    let chars: Vec<char> = source.chars().collect();
    let count = chars.len();
    let mut stack: Vec<(char, usize)> = Vec::new();
    let (mut index, mut line) = (0usize, 1usize);
    let mut opens_statement = true;
    let fault = |msg: &str, line: usize| SyntaxFault {
        msg: msg.to_string(),
        line,
    };
    while index < count {
        let ch = chars[index];
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
