//! Canonical secret redaction (`core/security.py::redact_secret_text` + `redact_secrets`).
//!
//! Promoted to `lattice-core` in v13.0.0 so `lattice-platform` (`knowledge`,
//! `workspaceos`, `adminops`) and `lattice-chat` share one `OnceLock`-compiled
//! redaction engine downward rather than reaching sideways across domain
//! boundaries or recompiling regexes per call.

use std::sync::OnceLock;

use fancy_regex::{Captures, Regex};
use serde_json::{json, Map, Value};

/// Marker substituted for redacted secret values.
pub const REDACTION: &str = "[REDACTED_SECRET]";

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

const TWO_GROUP_PATTERN: usize = 0;

struct CompiledPatterns {
    telegram_bot: Regex,
    telegram_bare: Regex,
    secrets: Vec<Regex>,
}

fn patterns() -> &'static CompiledPatterns {
    static PATTERNS: OnceLock<CompiledPatterns> = OnceLock::new();
    PATTERNS.get_or_init(|| CompiledPatterns {
        telegram_bot: Regex::new(r"\bbot(\d{5,20}):[A-Za-z0-9_-]{8,}\b")
            .expect("telegram bot regex"),
        telegram_bare: Regex::new(
            r"(?<![A-Za-z0-9_:-])(\d{5,20}):[A-Za-z0-9_-]{8,}(?![A-Za-z0-9_-])",
        )
        .expect("telegram bare regex"),
        secrets: SECRET_PATTERNS
            .iter()
            .map(|source| Regex::new(source).expect("secret pattern regex"))
            .collect(),
    })
}

/// Redact inline API keys, tokens, credentials, DSNs, PEM blocks, and Telegram bot tokens.
pub fn redact_secret_text(text: &str) -> String {
    if text.is_empty() {
        return String::new();
    }
    let set = patterns();
    let mut out = set
        .telegram_bot
        .replace_all(text, |captures: &Captures<'_, str>| {
            format!("bot{}:REDACTED", &captures[1])
        })
        .into_owned();
    out = set
        .telegram_bare
        .replace_all(&out, |captures: &Captures<'_, str>| {
            format!("bot{}:REDACTED", &captures[1])
        })
        .into_owned();
    for (index, regex) in set.secrets.iter().enumerate() {
        out = regex
            .replace_all(&out, |captures: &Captures<'_, str>| {
                if index == TWO_GROUP_PATTERN {
                    format!("{}={REDACTION}", &captures[1])
                } else {
                    REDACTION.to_string()
                }
            })
            .into_owned();
    }
    out
}

/// Recursive JSON structure redactor (`core/security.py::redact_secrets`).
pub fn redact_secrets(value: &Value) -> Value {
    match value {
        Value::String(s) => json!(redact_secret_text(s)),
        Value::Object(map) => {
            let mut out = Map::new();
            for (key, item) in map {
                if is_secret_key(key) {
                    out.insert(key.clone(), json!(REDACTION));
                } else {
                    out.insert(key.clone(), redact_secrets(item));
                }
            }
            Value::Object(out)
        }
        Value::Array(items) => Value::Array(items.iter().map(redact_secrets).collect()),
        other => other.clone(),
    }
}

/// Walk JSON string leaves only, leaving secret-named keys intact unless their value matches a secret pattern.
pub fn redact_structure(value: &Value) -> Value {
    match value {
        Value::String(text) => json!(redact_secret_text(text)),
        Value::Object(map) => {
            let mut out = Map::new();
            for (key, item) in map {
                out.insert(key.clone(), redact_structure(item));
            }
            Value::Object(out)
        }
        Value::Array(items) => Value::Array(items.iter().map(redact_structure).collect()),
        other => other.clone(),
    }
}

/// Return true if a JSON field name denotes a secret/credential key.
pub fn is_secret_key(key: &str) -> bool {
    let lowered = key.trim().to_lowercase().replace('-', "_");
    [
        "password",
        "passwd",
        "secret",
        "token",
        "api_key",
        "apikey",
        "authorization",
        "bearer",
        "private_key",
        "client_secret",
        "webhook",
        "dsn",
        "credential",
    ]
    .iter()
    .any(|hint| lowered.contains(hint))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn redacts_keyed_shaped_and_telegram_secrets() {
        assert_eq!(redact_secret_text(""), "");
        assert_eq!(
            redact_secret_text("api_key: supersecretvalue123"),
            "api_key=[REDACTED_SECRET]"
        );
        assert_eq!(
            redact_secret_text("Bearer sk-proj-abcdefghijklmnop1234"),
            "Bearer [REDACTED_SECRET]"
        );
        assert_eq!(
            redact_secret_text("bot1234567:ABCDEFGH_ijkl"),
            "bot1234567:REDACTED"
        );
        assert_eq!(
            redact_secret_text("url https://t/1234567:ABCDEFGH_ijkl end"),
            "url https://t/bot1234567:REDACTED end"
        );
    }

    #[test]
    fn redacts_json_objects_and_structure() {
        let input = json!({
            "api-key": "sk-1234567890abcdefghij",
            "nested": {
                "note": "postgres://user:pass@localhost/db",
                "safe": "hello"
            }
        });
        let out = redact_secrets(&input);
        assert_eq!(out["api-key"], json!(REDACTION));
        assert_eq!(out["nested"]["note"], json!(REDACTION));
        assert_eq!(out["nested"]["safe"], json!("hello"));

        let struct_only =
            redact_structure(&json!({"token": "short", "dsn": "postgres://u:p@h/db"}));
        assert_eq!(struct_only["token"], json!("short"));
        assert_eq!(struct_only["dsn"], json!(REDACTION));
    }
}
