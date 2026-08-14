//! `core/security.py::redact_secret_text`, pattern for pattern.
//!
//! One route in this family stores user-supplied text: the VS Code bridge
//! keeps a 500-character preview of whatever the editor sent, and an editor
//! buffer is exactly where a key lives. Python redacts before storing; so does
//! this. A partial port would be a silent security regression, so the eleven
//! patterns are transcribed rather than approximated, and a test asserts each
//! one still bites.
//!
//! `fancy-regex` (already a crate dependency) is what makes the transcription
//! possible: the bare-Telegram-token pattern uses a lookbehind *and* a
//! lookahead, neither of which the `regex` crate supports.

#![allow(
    dead_code,
    unused_imports,
    unused_variables,
    unused_assignments,
    unused_mut,
    private_interfaces,
    clippy::result_large_err,
    clippy::needless_lifetimes,
    clippy::too_many_arguments,
    clippy::type_complexity,
    clippy::collapsible_if,
    clippy::needless_as_bytes,
    clippy::redundant_closure,
    clippy::needless_return,
    clippy::manual_clamp,
    clippy::ptr_arg,
    clippy::unnecessary_sort_by,
    clippy::result_unit_err,
    clippy::useless_vec,
    clippy::uninlined_format_args,
    clippy::manual_contains,
    clippy::needless_borrows_for_generic_args,
    clippy::implicit_clone,
    clippy::unnecessary_map_or,
    clippy::match_like_matches_macro,
    clippy::manual_range_contains,
    clippy::derivable_impls,
    clippy::needless_pass_by_ref_mut,
    clippy::redundant_guards,
    clippy::map_identity,
    clippy::iter_overeager_cloned,
    clippy::explicit_auto_deref,
    clippy::bool_comparison,
    clippy::nonminimal_bool,
    clippy::if_same_then_else,
    clippy::question_mark,
    clippy::single_char_pattern,
    clippy::manual_pattern_char_comparison,
    clippy::manual_is_ascii_check,
    clippy::repeat_once,
    clippy::unused_self,
    clippy::module_inception
)]
use std::sync::OnceLock;

use fancy_regex::{Captures, Regex};

/// What a redacted value is replaced with.
pub const REDACTION: &str = "[REDACTED_SECRET]";

struct Patterns {
    telegram_with_bot: Regex,
    telegram_bare: Regex,
    secrets: Vec<Regex>,
}

fn patterns() -> &'static Patterns {
    static PATTERNS: OnceLock<Patterns> = OnceLock::new();
    PATTERNS.get_or_init(|| Patterns {
        telegram_with_bot: compile(r"\bbot(\d{5,20}):[A-Za-z0-9_-]{8,}\b"),
        telegram_bare: compile(
            r"(?<![A-Za-z0-9_:-])(\d{5,20}):[A-Za-z0-9_-]{8,}(?![A-Za-z0-9_-])",
        ),
        secrets: [
            r"(?i)\b(api[_ -]?key|secret|token|password|passwd|authorization|bearer|client[_ -]?secret|webhook|dsn)\s*[:=]\s*['\x22]?([^\s'\x22,;]{8,})['\x22]?",
            r"\b(sk-[A-Za-z0-9_\-]{16,})\b",
            r"\b(xai-[A-Za-z0-9_\-]{16,})\b",
            r"\b(gsk_[A-Za-z0-9_\-]{16,})\b",
            r"\b(ghp_[A-Za-z0-9_]{30,})\b",
            r"\b(xox[baprs]-[A-Za-z0-9-]{10,})\b",
            r"\b(AKIA[0-9A-Z]{16})\b",
            r"(?i)\b(postgres(?:ql)?://[^@\s]+:[^@\s]+@[^\s]+)",
            r"-----BEGIN [A-Z ]+PRIVATE KEY-----[\s\S]+?-----END [A-Z ]+PRIVATE KEY-----",
        ]
        .iter()
        .map(|source| compile(source))
        .collect(),
    })
}

/// Every pattern here is a literal in this file, so a failure is a build bug.
fn compile(source: &str) -> Regex {
    Regex::new(source).unwrap_or_else(|error| panic!("redaction pattern {source}: {error}"))
}

/// Redact known secret shapes from text that is about to be stored or shown.
///
/// The two-group rule is Python's: a pattern that captured both a *name* and a
/// *value* keeps the name (`api_key=[REDACTED_SECRET]`), because knowing which
/// kind of credential leaked is the useful half; a single-group pattern
/// replaces the whole match.
pub fn redact_secret_text(text: &str) -> String {
    if text.is_empty() {
        return String::new();
    }
    let patterns = patterns();
    let mut redacted = patterns
        .telegram_with_bot
        .replace_all(text, |caps: &Captures<'_>| {
            format!("bot{}:REDACTED", &caps[1])
        })
        .into_owned();
    redacted = patterns
        .telegram_bare
        .replace_all(&redacted, |caps: &Captures<'_>| {
            format!("bot{}:REDACTED", &caps[1])
        })
        .into_owned();
    for pattern in &patterns.secrets {
        redacted = pattern
            .replace_all(&redacted, |caps: &Captures<'_>| {
                if caps.len() >= 3 {
                    format!("{}={REDACTION}", &caps[1])
                } else {
                    REDACTION.to_string()
                }
            })
            .into_owned();
    }
    redacted
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn an_empty_string_stays_empty() {
        assert_eq!(redact_secret_text(""), "");
    }

    #[test]
    fn the_named_form_keeps_its_name() {
        assert_eq!(
            redact_secret_text("API key: sk-fixture1234567890abcdefghij"),
            "API key=[REDACTED_SECRET]"
        );
        assert_eq!(
            redact_secret_text("password = hunter2hunter2"),
            "password=[REDACTED_SECRET]"
        );
    }

    #[test]
    fn the_bare_provider_prefixes_are_replaced_whole() {
        // The VS Code fixture's exact input and its recorded preview.
        assert_eq!(
            redact_secret_text("let key = \"sk-fixture1234567890abcdefghij\";"),
            "let key = \"[REDACTED_SECRET]\";"
        );
        for sample in [
            "xai-abcdefghijklmnopqrstuvwx",
            "gsk_abcdefghijklmnopqrstuvwx",
            "ghp_abcdefghijklmnopqrstuvwxyz01234567",
            "xoxb-1234567890-abcdefg",
            "AKIAIOSFODNN7EXAMPLE",
        ] {
            let redacted = redact_secret_text(&format!("value {sample} end"));
            assert_eq!(redacted, format!("value {REDACTION} end"), "{sample}");
        }
    }

    #[test]
    fn a_database_url_and_a_private_key_block_are_redacted() {
        assert_eq!(
            redact_secret_text("dsn postgres://u:p@host/db"),
            format!("dsn {REDACTION}")
        );
        let key = "-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----";
        assert_eq!(redact_secret_text(key), REDACTION);
    }

    #[test]
    fn telegram_tokens_keep_their_bot_id_and_lose_the_secret() {
        assert_eq!(
            redact_secret_text("bot123456789:AAEEabcdefgh"),
            "bot123456789:REDACTED"
        );
        assert_eq!(
            redact_secret_text("use 123456789:AAEEabcdefgh now"),
            "use bot123456789:REDACTED now"
        );
        // The lookbehind keeps an already-prefixed token from being doubled.
        assert_eq!(
            redact_secret_text("bot123456789:AAEEabcdefgh")
                .matches("bot")
                .count(),
            1
        );
    }

    #[test]
    fn ordinary_text_is_left_alone() {
        let plain = "fn main() {} // 결정: 유지한다";
        assert_eq!(redact_secret_text(plain), plain);
        assert_eq!(redact_secret_text("sk-short"), "sk-short");
    }
}
