//! `Set-Cookie` exactly as Starlette renders it.
//!
//! `Response.set_cookie` builds a `http.cookies.SimpleCookie` and takes its
//! `OutputString()`, which emits `key=value` and then every set attribute in
//! **alphabetical** order, skipping the ones left empty. That is why the
//! rendered header reads `HttpOnly; Max-Age; Path; SameSite; Secure` and not
//! the order the keyword arguments were written in — and why `delete_cookie`,
//! which passes `expires=0`, puts an `expires=` first and carries a *live*
//! HTTP date computed at send time.
//!
//! The logout cookie has to match the login cookie's flags or the browser
//! keeps the old one, so both are rendered here from one function.

use crate::clock::Clock;

/// `http.cookies._LegalChars` — a value made only of these is not quoted.
const LEGAL_CHARS: &str =
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!#$%&'*+-.^_`|~:";

/// The attributes `set_cookie` / `delete_cookie` ever set here.
#[derive(Debug, Clone)]
pub struct CookieSpec<'a> {
    /// Cookie name.
    pub name: &'a str,
    /// Cookie value; the empty string renders as `""`.
    pub value: &'a str,
    /// `Max-Age`, always set by both callers.
    pub max_age: i64,
    /// Whether to emit the `expires=` attribute (deletion only).
    pub expires_now: bool,
    /// `Secure`, from `secure_cookies`.
    pub secure: bool,
}

/// Render one `Set-Cookie` header value.
///
/// `path="/"`, `httponly=True` and `samesite="lax"` are constants at both call
/// sites in `api/auth.py`, so they are constants here.
pub fn render(spec: &CookieSpec<'_>, clock: &Clock) -> String {
    let mut parts = vec![format!("{}={}", spec.name, quote(spec.value))];
    if spec.expires_now {
        parts.push(format!("expires={}", http_date(clock.now())));
    }
    parts.push("HttpOnly".to_string());
    parts.push(format!("Max-Age={}", spec.max_age));
    parts.push("Path=/".to_string());
    parts.push("SameSite=lax".to_string());
    if spec.secure {
        parts.push("Secure".to_string());
    }
    parts.join("; ")
}

/// The login cookie: the issued token, `Max-Age = session_ttl`.
pub fn session_cookie(token: &str, max_age: i64, secure: bool, clock: &Clock) -> String {
    render(
        &CookieSpec {
            name: crate::cookies::SESSION_COOKIE_NAME,
            value: token,
            max_age,
            expires_now: false,
            secure,
        },
        clock,
    )
}

/// The logout cookie: empty value, `Max-Age=0`, and a live `expires`.
pub fn delete_session_cookie(secure: bool, clock: &Clock) -> String {
    render(
        &CookieSpec {
            name: crate::cookies::SESSION_COOKIE_NAME,
            value: "",
            max_age: 0,
            expires_now: true,
            secure,
        },
        clock,
    )
}

/// `http.cookies._quote`.
fn quote(value: &str) -> String {
    if !value.is_empty() && value.chars().all(|c| LEGAL_CHARS.contains(c)) {
        return value.to_string();
    }
    let escaped: String = value
        .chars()
        .map(|c| match c {
            '"' => "\\\"".to_string(),
            '\\' => "\\\\".to_string(),
            other => other.to_string(),
        })
        .collect();
    format!("\"{escaped}\"")
}

const WEEKDAYS: [&str; 7] = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const MONTHS: [&str; 12] = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];

/// `http.cookies._getdate(future=0)`: `"Wdy, DD Mon YYYY HH:MM:SS GMT"`.
pub fn http_date(epoch_seconds: f64) -> String {
    let total = epoch_seconds.floor() as i64;
    let days = total.div_euclid(86_400);
    let seconds = total.rem_euclid(86_400);
    let (year, month, day) = civil_from_days(days);
    // 1970-01-01 was a Thursday, index 3 in a Monday-first table.
    let weekday = (days.rem_euclid(7) + 3) % 7;
    format!(
        "{}, {:02} {} {:04} {:02}:{:02}:{:02} GMT",
        WEEKDAYS[weekday as usize],
        day,
        MONTHS[(month - 1) as usize],
        year,
        seconds / 3600,
        (seconds % 3600) / 60,
        seconds % 60
    )
}

/// Howard Hinnant's `civil_from_days`, the standard days→(y, m, d) algorithm.
fn civil_from_days(days: i64) -> (i64, i64, i64) {
    let z = days + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146_096) / 365;
    let year = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1;
    let month = if mp < 10 { mp + 3 } else { mp - 9 };
    (if month <= 2 { year + 1 } else { year }, month, day)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_login_cookie_matches_starlette_byte_for_byte() {
        let clock = Clock::frozen(1_786_000_000.0);
        assert_eq!(
            session_cookie("QCKtf1KFfJ-FMg1G-wkjggZ", 86_400, false, &clock),
            "session_token=QCKtf1KFfJ-FMg1G-wkjggZ; HttpOnly; Max-Age=86400; Path=/; SameSite=lax"
        );
        assert_eq!(
            session_cookie("abc", 3_600, true, &clock),
            "session_token=abc; HttpOnly; Max-Age=3600; Path=/; SameSite=lax; Secure"
        );
    }

    #[test]
    fn the_logout_cookie_carries_an_empty_quoted_value_and_a_date() {
        let clock = Clock::frozen(1_786_000_000.0);
        assert_eq!(
            delete_session_cookie(false, &clock),
            "session_token=\"\"; expires=Thu, 06 Aug 2026 07:06:40 GMT; \
             HttpOnly; Max-Age=0; Path=/; SameSite=lax"
        );
    }

    #[test]
    fn illegal_characters_force_quoting() {
        assert_eq!(quote("plain-value_1:2"), "plain-value_1:2");
        assert_eq!(quote(""), "\"\"");
        assert_eq!(quote("a b"), "\"a b\"");
        assert_eq!(quote("a\"b"), "\"a\\\"b\"");
        assert_eq!(quote("a\\b"), "\"a\\\\b\"");
    }

    #[test]
    fn http_dates_match_python_for_known_instants() {
        // `time.strftime` over `time.gmtime(t)` for each of these.
        assert_eq!(http_date(0.0), "Thu, 01 Jan 1970 00:00:00 GMT");
        assert_eq!(http_date(1_000_000_000.0), "Sun, 09 Sep 2001 01:46:40 GMT");
        assert_eq!(http_date(1_755_000_000.5), "Tue, 12 Aug 2025 12:00:00 GMT");
        assert_eq!(http_date(1_767_225_599.0), "Wed, 31 Dec 2025 23:59:59 GMT");
        assert_eq!(http_date(1_709_164_800.0), "Thu, 29 Feb 2024 00:00:00 GMT");
    }

    #[test]
    fn the_generic_renderer_covers_both_shapes() {
        let clock = Clock::frozen(0.0);
        let rendered = render(
            &CookieSpec {
                name: "x",
                value: "y",
                max_age: 5,
                expires_now: true,
                secure: true,
            },
            &clock,
        );
        assert_eq!(
            rendered,
            "x=y; expires=Thu, 01 Jan 1970 00:00:00 GMT; HttpOnly; Max-Age=5; \
             Path=/; SameSite=lax; Secure"
        );
    }
}
