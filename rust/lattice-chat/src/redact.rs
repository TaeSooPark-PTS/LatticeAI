use std::sync::OnceLock;

use fancy_regex::Regex;
pub use lattice_core::redact::redact_secret_text;

pub const BRAND_NAME: &str = "Lattice AI";

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
