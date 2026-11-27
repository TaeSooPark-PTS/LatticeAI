use serde_json::{Map, Value};

pub const FRONTMATTER_FIELD: &str = "frontmatter";
pub const TRUST_KEYS: [&str; 5] = [
    "status",
    "trust",
    "verified",
    "review_status",
    "verification",
];
const MAX_FRONTMATTER_LINES: usize = 200;

fn scalar(raw: &str) -> Option<Value> {
    let trimmed = raw.trim();
    let unquoted = trimmed
        .strip_prefix('"')
        .and_then(|rest| rest.strip_suffix('"'))
        .or_else(|| {
            trimmed
                .strip_prefix('\'')
                .and_then(|rest| rest.strip_suffix('\''))
        })
        .unwrap_or(trimmed)
        .trim();
    if unquoted.is_empty() {
        return None;
    }
    match unquoted.to_ascii_lowercase().as_str() {
        "true" | "yes" => Some(Value::Bool(true)),
        "false" | "no" => Some(Value::Bool(false)),
        _ => Some(Value::String(unquoted.to_string())),
    }
}

pub fn trust_frontmatter(text: &str) -> Map<String, Value> {
    let mut found = Map::new();
    let body = text.strip_prefix('\u{feff}').unwrap_or(text);
    let mut lines = body.lines();
    if lines.next().map(str::trim_end) != Some("---") {
        return found;
    }
    let mut closed = false;
    let mut pending = Map::new();
    for line in lines.take(MAX_FRONTMATTER_LINES) {
        let trimmed = line.trim_end();
        if trimmed == "---" || trimmed == "..." {
            closed = true;
            break;
        }
        if line.starts_with([' ', '\t']) {
            continue;
        }
        let Some((key, value)) = trimmed.split_once(':') else {
            continue;
        };
        let key = key.trim().to_ascii_lowercase();
        if !TRUST_KEYS.contains(&key.as_str()) {
            continue;
        }
        if let Some(parsed) = scalar(value) {
            pending.insert(key, parsed);
        }
    }
    if closed {
        found = pending;
    }
    found
}

pub fn stamp_frontmatter(metadata: &mut Map<String, Value>, text: &str) {
    if metadata.contains_key(FRONTMATTER_FIELD) {
        return;
    }
    let parsed = trust_frontmatter(text);
    if !parsed.is_empty() {
        metadata.insert(FRONTMATTER_FIELD.into(), Value::Object(parsed));
    }
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    #[test]
    fn reads_only_trust_keys_from_a_closed_leading_block() {
        let text = "---\ntitle: Plan\nstatus: Draft\nverified: \"false\"\ntags:\n  - a\n---\nBody";
        assert_eq!(
            Value::Object(trust_frontmatter(text)),
            json!({"status": "Draft", "verified": false})
        );
        let bom = "\u{feff}---\ntrust: 'verified'\n...\nBody";
        assert_eq!(
            Value::Object(trust_frontmatter(bom)),
            json!({"trust": "verified"})
        );
    }

    #[test]
    fn ignores_unclosed_misplaced_nested_and_empty_values() {
        assert!(trust_frontmatter("status: draft\n").is_empty());
        assert!(trust_frontmatter("---\nstatus: draft\nno closing fence").is_empty());
        assert!(trust_frontmatter("Intro\n---\nstatus: draft\n---").is_empty());
        assert!(trust_frontmatter("---\n  status: draft\nstatus:\nnot a pair\n---").is_empty());
        assert_eq!(
            Value::Object(trust_frontmatter("---\nreview_status: yes\n---")),
            json!({"review_status": true})
        );
    }

    #[test]
    fn stamping_never_overrides_caller_metadata() {
        let mut metadata = Map::new();
        stamp_frontmatter(&mut metadata, "plain text");
        assert!(metadata.is_empty());
        stamp_frontmatter(&mut metadata, "---\nstatus: draft\n---\n");
        assert_eq!(metadata[FRONTMATTER_FIELD], json!({"status": "draft"}));
        stamp_frontmatter(&mut metadata, "---\nstatus: verified\n---\n");
        assert_eq!(metadata[FRONTMATTER_FIELD], json!({"status": "draft"}));
    }
}
