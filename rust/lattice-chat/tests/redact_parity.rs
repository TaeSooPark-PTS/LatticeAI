use std::path::PathBuf;

use lattice_chat::redact::{normalize_branding, redact_for_role, redact_secret_text};
use serde_json::Value;

fn fixture() -> Value {
    let path: PathBuf = [env!("CARGO_MANIFEST_DIR"), "..", "fixtures", "redact.json"]
        .iter()
        .collect();
    serde_json::from_str(&std::fs::read_to_string(&path).expect("rust/fixtures/redact.json"))
        .expect("json")
}

fn strings(value: &Value, key: &str) -> Vec<String> {
    value[key]
        .as_array()
        .expect(key)
        .iter()
        .map(|item| item.as_str().expect("pattern").to_string())
        .collect()
}

#[test]
fn every_corpus_case_redacts_to_the_same_bytes_python_produced() {
    let fixture = fixture();
    let cases = fixture["cases"].as_array().expect("cases");
    assert!(cases.len() >= 60, "the corpus must reach every rule branch");
    for case in cases {
        let name = case["case"].as_str().expect("case");
        let text = case["text"].as_str().expect("text");
        assert_eq!(
            redact_secret_text(text),
            case["redacted"].as_str().expect("redacted"),
            "redact_secret_text diverged on {name}"
        );
        assert_eq!(
            normalize_branding(text),
            case["branded"].as_str().expect("branded"),
            "normalize_branding diverged on {name}"
        );
        assert_eq!(
            redact_for_role("assistant", text),
            case["assistant"].as_str().expect("assistant"),
            "the assistant chain diverged on {name}"
        );
        assert_eq!(
            redact_for_role("user", text),
            case["redacted"].as_str().expect("redacted"),
            "the user chain diverged on {name}"
        );
    }
}

#[test]
fn the_ported_pattern_list_is_the_one_python_still_compiles() {
    let fixture = fixture();
    assert_eq!(fixture["brand_name"], "Lattice AI");
    assert_eq!(
        strings(&fixture, "secret_patterns"),
        vec![
            r#"(?i)\b(api[_ -]?key|secret|token|password|passwd|authorization|bearer|client[_ -]?secret|webhook|dsn)\s*[:=]\s*['\"]?([^\s'\",;]{8,})['\"]?"#,
            r"\b(sk-[A-Za-z0-9_\-]{16,})\b",
            r"\b(xai-[A-Za-z0-9_\-]{16,})\b",
            r"\b(gsk_[A-Za-z0-9_\-]{16,})\b",
            r"\b(ghp_[A-Za-z0-9_]{30,})\b",
            r"\b(xox[baprs]-[A-Za-z0-9-]{10,})\b",
            r"\b(AKIA[0-9A-Z]{16})\b",
            r"(?i)\b(postgres(?:ql)?://[^@\s]+:[^@\s]+@[^\s]+)",
            r"-----BEGIN [A-Z ]+PRIVATE KEY-----[\s\S]+?-----END [A-Z ]+PRIVATE KEY-----",
        ]
    );
    assert_eq!(
        strings(&fixture, "telegram_patterns"),
        vec![
            r"\bbot(\d{5,20}):[A-Za-z0-9_-]{8,}\b",
            r"(?<![A-Za-z0-9_:-])(\d{5,20}):[A-Za-z0-9_-]{8,}(?![A-Za-z0-9_-])",
        ]
    );
    assert_eq!(
        strings(&fixture, "brand_patterns"),
        vec![
            r"\bconnect\s+ai\b",
            r"\bconnect-ai\b",
            r"\bconnectai\b",
            r"커넥트\s*AI",
        ]
    );
}
