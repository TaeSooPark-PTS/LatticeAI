pub mod extract;
pub mod python;
pub mod repair;
pub mod salvage;
pub mod text;
pub mod validate;

use serde_json::{json, Value};

pub use extract::extract_file_content;
pub use python::{python_parses, SyntaxFault};
pub use repair::repair_file_content;
pub use salvage::salvage_score;
pub use text::ext_of;
pub use validate::{looks_like_reasoning_preamble, looks_like_refusal, validate_file_content};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SanitizeMeta {
    pub sanitized: bool,
    pub repaired: bool,
    pub reason: String,
}

impl SanitizeMeta {
    fn untouched(reason: &str) -> Self {
        Self {
            sanitized: false,
            repaired: false,
            reason: reason.to_string(),
        }
    }

    pub fn to_value(&self) -> Value {
        json!({
            "sanitized": self.sanitized,
            "repaired": self.repaired,
            "reason": self.reason,
        })
    }
}

pub fn sanitize_write_content(
    target_path: &str,
    content: &str,
    user_request: &str,
) -> (String, SanitizeMeta) {
    if content.trim().is_empty() {
        return (content.to_string(), SanitizeMeta::untouched("empty"));
    }
    let (content, framed) = match crate::parse::channel::strip_channel_frames(content) {
        Some(stripped) => (stripped, true),
        None => (content.to_string(), false),
    };
    let (ok, reason) = validate_file_content(&content, target_path);
    if ok {
        if framed {
            return (
                content,
                SanitizeMeta {
                    sanitized: true,
                    repaired: false,
                    reason,
                },
            );
        }
        return (content, SanitizeMeta::untouched("ok"));
    }
    let extracted = extract_file_content(&content, target_path);
    if !extracted.is_empty() {
        let (extracted_ok, _) = validate_file_content(&extracted, target_path);
        if extracted_ok {
            return (
                extracted,
                SanitizeMeta {
                    sanitized: true,
                    repaired: false,
                    reason,
                },
            );
        }
    }
    let request = if user_request.is_empty() {
        format!("content for {target_path}")
    } else {
        user_request.to_string()
    };
    let source = if extracted.is_empty() {
        content.as_str()
    } else {
        &extracted
    };
    let repaired = repair_file_content(source, target_path, &request);
    (
        repaired,
        SanitizeMeta {
            sanitized: true,
            repaired: true,
            reason,
        },
    )
}

#[cfg(test)]
mod tests;
