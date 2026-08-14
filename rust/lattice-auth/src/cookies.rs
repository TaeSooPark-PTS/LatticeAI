//! Two cookie readers, and the difference between them is the point.
//!
//! * [`has_session_cookie`] is the CSRF guard's, ported from `core/csrf.py`:
//!   the raw header is split by hand so a malformed pair cannot make the whole
//!   jar unreadable. A request whose cookies we cannot parse must still count
//!   as cookie-bearing — a strict parser here **fails open**, because the guard
//!   short-circuits to "allow, no session cookie" the moment it decides there
//!   is nothing to forge.
//! * [`cookie_value`] is Starlette's `cookie_parser`, which is what
//!   `request.cookies.get("session_token")` reaches in `access_runtime`'s
//!   `extract_bearer_token`. It is a *different* parser in the original, so it
//!   is a different function here.

/// The cookie the session rides in (`SESSION_COOKIE_NAME`).
pub const SESSION_COOKIE_NAME: &str = "session_token";

/// Whether the raw `Cookie` header carries `session_token`, at all, in any
/// shape. Deliberately loose: a pair with no `=` still names a cookie.
pub fn has_session_cookie(cookie_header: Option<&str>) -> bool {
    let Some(header) = cookie_header else {
        return false;
    };
    if header.is_empty() {
        return false;
    }
    header.split(';').any(|pair| {
        let name = pair.split('=').next().unwrap_or(pair);
        name.trim() == SESSION_COOKIE_NAME
    })
}

/// Starlette's `cookie_parser` for one name: last occurrence wins, values are
/// unquoted, and a chunk with no `=` is stored under the empty name.
pub fn cookie_value(cookie_header: Option<&str>, name: &str) -> Option<String> {
    let header = cookie_header?;
    let mut found: Option<String> = None;
    for chunk in header.split(';') {
        let (key, value) = match chunk.split_once('=') {
            Some((key, value)) => (key, value),
            None => ("", chunk),
        };
        let (key, value) = (key.trim(), value.trim());
        if key.is_empty() && value.is_empty() {
            continue;
        }
        if key == name {
            found = Some(unquote(value));
        }
    }
    found
}

/// `http.cookies._unquote` for the shapes a session token can take.
fn unquote(value: &str) -> String {
    let bytes = value.as_bytes();
    if bytes.len() < 2 || bytes[0] != b'"' || bytes[bytes.len() - 1] != b'"' {
        return value.to_string();
    }
    let inner = &value[1..value.len() - 1];
    let mut out = String::with_capacity(inner.len());
    let mut chars = inner.chars();
    while let Some(character) = chars.next() {
        if character == '\\' {
            match chars.next() {
                Some(escaped) => out.push(escaped),
                None => out.push('\\'),
            }
        } else {
            out.push(character);
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_malformed_pair_still_counts_as_cookie_bearing() {
        assert!(has_session_cookie(Some("bad;;session_token")));
        assert!(has_session_cookie(Some("session_token")));
        assert!(has_session_cookie(Some("a=1; session_token=xyz; b=2")));
        assert!(has_session_cookie(Some("  session_token  =  x")));
    }

    #[test]
    fn absent_or_unrelated_cookies_are_not_session_bearing() {
        assert!(!has_session_cookie(None));
        assert!(!has_session_cookie(Some("")));
        assert!(!has_session_cookie(Some("other=1; lattice_invite=2")));
        assert!(!has_session_cookie(Some("session_tokenx=1")));
    }

    #[test]
    fn starlette_parser_takes_the_last_occurrence() {
        assert_eq!(
            cookie_value(
                Some("session_token=one; session_token=two"),
                "session_token"
            ),
            Some("two".to_string())
        );
        assert_eq!(cookie_value(None, "session_token"), None);
        assert_eq!(cookie_value(Some("other=1"), "session_token"), None);
    }

    #[test]
    fn quoted_values_are_unquoted() {
        assert_eq!(
            cookie_value(Some(r#"session_token="""#), "session_token"),
            Some(String::new())
        );
        assert_eq!(
            cookie_value(Some(r#"session_token="a\"b""#), "session_token"),
            Some("a\"b".to_string())
        );
        assert_eq!(
            cookie_value(Some(r#"session_token="trailing\"#), "session_token"),
            Some(r#""trailing\"#.to_string())
        );
    }

    #[test]
    fn a_chunk_without_equals_lands_under_the_empty_name() {
        assert_eq!(cookie_value(Some("orphan"), ""), Some("orphan".to_string()));
        assert_eq!(cookie_value(Some(" ; "), "session_token"), None);
    }
}
