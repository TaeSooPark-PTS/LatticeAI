//! Secret redaction.

use fancy_regex::Regex;
use serde_json::{json, Map, Value};

/// `core.security.redact_secret_text`.
pub fn redact_secret_text(text: &str) -> String {
    if text.is_empty() {
        return String::new();
    }
    let mut redacted = text.to_string();
    if let Ok(re) = Regex::new(r"\bbot(\d{5,20}):[A-Za-z0-9_-]{8,}\b") {
        redacted = re.replace_all(&redacted, "bot${1}:REDACTED").into_owned();
    }
    if let Ok(re) = Regex::new(r"(?<![A-Za-z0-9_:-])(\d{5,20}):[A-Za-z0-9_-]{8,}(?![A-Za-z0-9_-])")
    {
        redacted = re.replace_all(&redacted, "bot${1}:REDACTED").into_owned();
    }
    let patterns = [
        r#"(?i)\b(api[_ -]?key|secret|token|password|passwd|authorization|bearer|client[_ -]?secret|webhook|dsn)\s*[:=]\s*['\"]?([^\s'\",;]{8,})['\"]?"#,
        r"\b(sk-[A-Za-z0-9_\-]{16,})\b",
        r"\b(xai-[A-Za-z0-9_\-]{16,})\b",
        r"\b(gsk_[A-Za-z0-9_\-]{16,})\b",
        r"\b(ghp_[A-Za-z0-9_]{30,})\b",
        r"\b(xox[baprs]-[A-Za-z0-9-]{10,})\b",
        r"\b(AKIA[0-9A-Z]{16})\b",
        r"(?i)\b(postgres(?:ql)?://[^@\s]+:[^@\s]+@[^\s]+)",
        r"-----BEGIN [A-Z ]+PRIVATE KEY-----[\s\S]+?-----END [A-Z ]+PRIVATE KEY-----",
    ];
    for pat in patterns {
        let Ok(re) = Regex::new(pat) else {
            continue;
        };
        redacted = re
            .replace_all(&redacted, |caps: &fancy_regex::Captures| {
                if caps.get(2).is_some() {
                    format!(
                        "{}=[REDACTED_SECRET]",
                        caps.get(1).map(|m| m.as_str()).unwrap_or("")
                    )
                } else {
                    "[REDACTED_SECRET]".into()
                }
            })
            .into_owned();
    }
    redacted
}

/// Recursively redact string leaves. Keys named like secrets become
/// `[REDACTED_SECRET]` (Python `redact_secrets`).
pub fn redact_secrets(value: &Value) -> Value {
    match value {
        Value::String(text) => json!(redact_secret_text(text)),
        Value::Object(map) => {
            let mut out = Map::new();
            for (key, item) in map {
                if is_secret_key(key) {
                    out.insert(key.clone(), json!("[REDACTED_SECRET]"));
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

/// Walk values only — keys stay. Used by the security dashboard (not
/// `redact_secrets`, which blanks secret-*named* fields).
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

fn is_secret_key(key: &str) -> bool {
    let lowered = key.to_lowercase().replace('-', "_");
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
