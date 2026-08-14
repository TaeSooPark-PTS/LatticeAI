//! `redact_secret_text` — the last gate before bytes leave the machine.
//!
//! Port of `latticeai/core/security.py`'s text redactor. The Python original is
//! applied to logs, audit rows and API previews everywhere; the one path that
//! *matters* here is the hybrid cloud lane, where `build_minimal_context` runs
//! it over the compact payload. A token pasted into a note is not marked
//! sensitive by anybody, so pattern redaction is the only thing between it and
//! the provider.
//!
//! `fancy_regex` again, and here it is not optional: `TELEGRAM_TOKEN_BARE_RE`
//! opens with the lookbehind `(?<![A-Za-z0-9_:-])` and closes with the negative
//! lookahead `(?![A-Za-z0-9_-])`. Only the lookbehind ever refuses anything —
//! `[A-Za-z0-9_-]{8,}` is greedy, so it swallows the tail and the lookahead is
//! satisfied at end of input (`telegram_lookahead_is_greedy` in the fixture).
//!
//! One faithful oddity worth naming: the Python replacement for the keyed
//! patterns writes `key=[REDACTED_SECRET]` with an `=` **whatever separator the
//! text used** — `secret = 'x'` becomes `secret=[REDACTED_SECRET]`. (The
//! `Authorization: Bearer …` header does *not* redact, because the value after
//! the colon is `Bearer`, six characters, below the eight the pattern demands;
//! `rust/fixtures/redact.json::keyed_authorization` pins that.)
//!
//! [`normalize_branding`] lives here too, because `write_chat_turn` step 1 is
//! the pair: every turn is redacted, and an **assistant** turn is then rewritten
//! from the product's legacy aliases. Both are pure regular expressions — no
//! model, no tokenizer, nothing to ask the worker for — which is what makes the
//! whole step portable. (The *ingest* step's concept extraction is LLM-first and
//! therefore is not; see the W3a wiring note.)

use std::sync::OnceLock;

use fancy_regex::{Captures, Regex};

/// The keyed and shaped secret patterns, in `SECRET_TEXT_PATTERNS` order.
const SECRET_PATTERNS: &[&str] = &[
    r"(?i)\b(api[_ -]?key|secret|token|password|passwd|authorization|bearer|client[_ -]?secret|webhook|dsn)\s*[:=]\s*['\x22]?([^\s'\x22,;]{8,})['\x22]?",
    r"\b(sk-[A-Za-z0-9_\-]{16,})\b",
    r"\b(xai-[A-Za-z0-9_\-]{16,})\b",
    r"\b(gsk_[A-Za-z0-9_\-]{16,})\b",
    r"\b(ghp_[A-Za-z0-9_]{30,})\b",
    r"\b(xox[baprs]-[A-Za-z0-9-]{10,})\b",
    r"\b(AKIA[0-9A-Z]{16})\b",
    r"(?i)\b(postgres(?:ql)?://[^@\s]+:[^@\s]+@[^\s]+)",
    r"-----BEGIN [A-Z ]+PRIVATE KEY-----[\s\S]+?-----END [A-Z ]+PRIVATE KEY-----",
];

/// How many capture groups each pattern declares — the Python `repl` branches
/// on `len(match.groups()) >= 2`, and a group count is a property of the
/// pattern, not of the match.
const TWO_GROUP_PATTERN: usize = 0;

fn compiled() -> &'static Vec<Regex> {
    static SET: OnceLock<Vec<Regex>> = OnceLock::new();
    SET.get_or_init(|| {
        SECRET_PATTERNS
            .iter()
            .map(|pattern| Regex::new(pattern).expect("ported pattern must compile"))
            .collect()
    })
}

fn telegram_with_bot() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(r"\bbot(\d{5,20}):[A-Za-z0-9_-]{8,}\b").expect("ported pattern must compile")
    })
}

fn telegram_bare() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(r"(?<![A-Za-z0-9_:-])(\d{5,20}):[A-Za-z0-9_-]{8,}(?![A-Za-z0-9_-])")
            .expect("ported pattern must compile")
    })
}

/// `redact_secret_text(text)`.
pub fn redact_secret_text(text: &str) -> String {
    if text.is_empty() {
        return String::new();
    }
    let mut redacted = telegram_with_bot()
        .replace_all(text, |captures: &Captures| {
            format!("bot{}:REDACTED", &captures[1])
        })
        .into_owned();
    redacted = telegram_bare()
        .replace_all(&redacted, |captures: &Captures| {
            format!("bot{}:REDACTED", &captures[1])
        })
        .into_owned();
    for (index, pattern) in compiled().iter().enumerate() {
        redacted = pattern
            .replace_all(&redacted, |captures: &Captures| {
                if index == TWO_GROUP_PATTERN {
                    format!("{}=[REDACTED_SECRET]", &captures[1])
                } else {
                    "[REDACTED_SECRET]".to_string()
                }
            })
            .into_owned();
    }
    redacted
}

/// `models.router.branding.BRAND_NAME`.
pub const BRAND_NAME: &str = "Lattice AI";

/// `LEGACY_BRAND_PATTERNS`, in order — the order is the rule, because the first
/// rewrite changes the text the next one sees.
const LEGACY_BRAND_PATTERNS: &[&str] = &[
    r"(?i)\bconnect\s+ai\b",
    r"(?i)\bconnect-ai\b",
    r"(?i)\bconnectai\b",
    r"(?i)커넥트\s*AI",
];

fn brand_patterns() -> &'static Vec<Regex> {
    static SET: OnceLock<Vec<Regex>> = OnceLock::new();
    SET.get_or_init(|| {
        LEGACY_BRAND_PATTERNS
            .iter()
            .map(|pattern| Regex::new(pattern).expect("ported pattern must compile"))
            .collect()
    })
}

/// `normalize_branding(text)` — the legacy-alias rewrite assistant turns get.
///
/// Applied **after** redaction (that is `write_chat_turn`'s order) and only to
/// the assistant's own words: a user who typed "Connect AI" is quoted, not
/// corrected.
pub fn normalize_branding(text: &str) -> String {
    if text.is_empty() {
        return String::new();
    }
    let mut normalized = text.to_string();
    for pattern in brand_patterns() {
        normalized = pattern.replace_all(&normalized, BRAND_NAME).into_owned();
    }
    normalized
}

/// `write_chat_turn` step 1, whole: redact, then brand-normalise an assistant.
pub fn redact_for_role(role: &str, message: &str) -> String {
    let redacted = redact_secret_text(message);
    if role == "assistant" {
        normalize_branding(&redacted)
    } else {
        redacted
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn an_empty_string_stays_empty() {
        assert_eq!(redact_secret_text(""), "");
        assert_eq!(
            redact_secret_text("nothing secret here"),
            "nothing secret here"
        );
    }

    #[test]
    fn keyed_secrets_keep_their_key_and_lose_their_value() {
        assert_eq!(
            redact_secret_text("api_key: abcdefgh12345678"),
            "api_key=[REDACTED_SECRET]"
        );
        assert_eq!(
            redact_secret_text("password=hunter2hunter2"),
            "password=[REDACTED_SECRET]"
        );
        // Under 8 characters is not a secret shape.
        assert_eq!(redact_secret_text("token: short"), "token: short");
    }

    #[test]
    fn shaped_secrets_are_replaced_whole() {
        for secret in [
            "sk-abcdefghijklmnop01",
            "xai-abcdefghijklmnop01",
            "gsk_abcdefghijklmnop01",
            "xoxb-1234567890-abc",
            "AKIAABCDEFGHIJKLMNOP",
        ] {
            let redacted = redact_secret_text(&format!("value {secret} end"));
            assert_eq!(redacted, "value [REDACTED_SECRET] end", "{secret}");
        }
        assert_eq!(
            redact_secret_text(&format!("ghp_{}", "a".repeat(30))),
            "[REDACTED_SECRET]"
        );
        assert_eq!(
            redact_secret_text("postgres://u:p@host/db"),
            "[REDACTED_SECRET]"
        );
        assert_eq!(
            redact_secret_text("-----BEGIN RSA PRIVATE KEY-----\nx\n-----END RSA PRIVATE KEY-----"),
            "[REDACTED_SECRET]"
        );
    }

    #[test]
    fn telegram_tokens_are_normalised_to_the_bot_shape() {
        assert_eq!(
            redact_secret_text("bot123456:abcdefghij"),
            "bot123456:REDACTED"
        );
        assert_eq!(
            redact_secret_text("123456:abcdefghij"),
            "bot123456:REDACTED",
            "a bare token is rewritten with the bot prefix"
        );
        assert_eq!(redact_secret_text("12:34"), "12:34");
    }

    #[test]
    fn the_legacy_aliases_become_the_product_name() {
        assert_eq!(normalize_branding(""), "");
        assert_eq!(normalize_branding("connect   ai here"), "Lattice AI here");
        assert_eq!(normalize_branding("connect-ai notes"), "Lattice AI notes");
        assert_eq!(normalize_branding("ConnectAI old"), "Lattice AI old");
        assert_eq!(
            normalize_branding("저는 커넥트AI 입니다"),
            "저는 Lattice AI 입니다"
        );
        assert_eq!(
            normalize_branding("disconnectai stays"),
            "disconnectai stays",
            "the word boundary is the rule, not a substring search"
        );
    }

    #[test]
    fn only_the_assistant_is_brand_normalised() {
        assert_eq!(redact_for_role("assistant", "Connect AI"), BRAND_NAME);
        assert_eq!(redact_for_role("user", "Connect AI"), "Connect AI");
        assert_eq!(
            redact_for_role("assistant", "Connect AI: api_key: abcdefgh12345678"),
            "Lattice AI: api_key=[REDACTED_SECRET]",
            "redaction runs first, branding second"
        );
    }
}
