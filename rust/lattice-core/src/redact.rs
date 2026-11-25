use std::sync::OnceLock;

use fancy_regex::{Captures, Regex};
use serde_json::{json, Map, Value};

pub const REDACTION: &str = "[REDACTED_SECRET]";
pub const PII_REDACTION: &str = "[REDACTED_PII]";

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

const PII_PATTERNS: &[(&str, &str)] = &[
    ("rrn", r"\b\d{6}-[1-4]\d{6}\b"),
    ("ssn", r"\b\d{3}-\d{2}-\d{4}\b"),
    ("card", r"\b(?:\d{4}[-\s]?){3}\d{4}\b"),
    (
        "email",
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
    ),
    (
        "phone",
        r"(?<!\d)(?:\+?\d{1,3}[-\s]?)?(?:01[016789]|02|0[3-6]\d|\(\d{3}\)|\d{3})[-\s]\d{3,4}[-\s]\d{4}(?!\d)",
    ),
    (
        "home_path",
        r"(?:/Users/[A-Za-z0-9._-]+|/home/[A-Za-z0-9._-]+|[A-Za-z]:\\Users\\[A-Za-z0-9._-]+)",
    ),
];

const TWO_GROUP_PATTERN: usize = 0;

struct CompiledPatterns {
    telegram_bot: Regex,
    telegram_bare: Regex,
    secrets: Vec<Regex>,
    pii: Vec<(&'static str, Regex)>,
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
        pii: PII_PATTERNS
            .iter()
            .map(|(kind, source)| (*kind, Regex::new(source).expect("pii pattern regex")))
            .collect(),
    })
}

#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct SensitiveFinding {
    pub category: String,
    pub count: usize,
}

pub fn detect_sensitive_findings(text: &str) -> Vec<SensitiveFinding> {
    if text.is_empty() {
        return Vec::new();
    }
    let set = patterns();
    let mut findings = Vec::new();
    let mut secret_hits =
        set.telegram_bot.find_iter(text).count() + set.telegram_bare.find_iter(text).count();
    for regex in &set.secrets {
        secret_hits += regex.find_iter(text).count();
    }
    if secret_hits > 0 {
        findings.push(SensitiveFinding {
            category: "secret".to_string(),
            count: secret_hits,
        });
    }
    for (kind, regex) in &set.pii {
        let count = regex.find_iter(text).count();
        if count > 0 {
            findings.push(SensitiveFinding {
                category: (*kind).to_string(),
                count,
            });
        }
    }
    findings
}

pub fn redact_pii_text(text: &str) -> String {
    if text.is_empty() {
        return String::new();
    }
    let set = patterns();
    let mut out = text.to_string();
    for (kind, regex) in &set.pii {
        let replacement = format!("[REDACTED_{}]", kind.to_uppercase());
        out = regex.replace_all(&out, replacement.as_str()).into_owned();
    }
    out
}

pub fn redact_pii_and_secrets(text: &str) -> String {
    redact_pii_text(&redact_secret_text(text))
}

pub fn redact_pii_and_secrets_value(value: &Value) -> Value {
    match value {
        Value::String(s) => json!(redact_pii_and_secrets(s)),
        Value::Object(map) => {
            let mut out = Map::new();
            for (key, item) in map {
                if is_secret_key(key) {
                    out.insert(key.clone(), json!(REDACTION));
                } else {
                    out.insert(key.clone(), redact_pii_and_secrets_value(item));
                }
            }
            Value::Object(out)
        }
        Value::Array(items) => {
            Value::Array(items.iter().map(redact_pii_and_secrets_value).collect())
        }
        other => other.clone(),
    }
}

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

    #[test]
    fn redacts_pii_and_detects_findings_without_echoing_raw_values() {
        let sample = "Contact alice@example.com or 010-1234-5678 at /Users/alice/notes with sk-proj-abcdefghijklmnop1234 and 900101-1234567";
        let findings = detect_sensitive_findings(sample);
        let cats: Vec<&str> = findings.iter().map(|f| f.category.as_str()).collect();
        assert!(cats.contains(&"secret"));
        assert!(cats.contains(&"email"));
        assert!(cats.contains(&"phone"));
        assert!(cats.contains(&"rrn"));
        assert!(cats.contains(&"home_path"));

        let masked = redact_pii_and_secrets(sample);
        assert!(!masked.contains("alice@example.com"));
        assert!(!masked.contains("010-1234-5678"));
        assert!(!masked.contains("/Users/alice"));
        assert!(!masked.contains("900101-1234567"));
        assert!(!masked.contains("sk-proj-abcdefghijklmnop1234"));
        assert!(masked.contains("[REDACTED_EMAIL]"));
        assert!(masked.contains("[REDACTED_HOME_PATH]"));
        assert!(masked.contains(REDACTION));
    }
}
