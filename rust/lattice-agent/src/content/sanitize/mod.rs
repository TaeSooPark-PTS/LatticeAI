//! `sanitize_write_content` — the write-side guarantee, natively.
//!
//! A port of `latticeai.core.file_generation.{sanitize,validation,extraction,
//! repair}` (deleted in 6a03294; read back out of `659de86`). It answers one
//! question about model-produced file content before it reaches a disk:
//!
//! 1. content that already **validates** is returned byte-for-byte unchanged —
//!    trusted or user-authored content is never mangled;
//! 2. otherwise the **extraction** pass strips reasoning blocks, fences and
//!    chat framing, and is used only when what it recovered validates;
//! 3. otherwise **repair** guarantees a structurally valid file of the target
//!    type, honestly labelled (`repaired: true`).
//!
//! Empty content is left untouched: creating an empty file is a legitimate,
//! intentional act (`__init__.py`).
//!
//! ## Where it lives, and why not `lattice-core`
//!
//! The v11.7.0 brief suggested `lattice-core::sanitize`. It is here instead,
//! for two reasons that are about parity rather than taste: the `.json` branch
//! reports **CPython's own decoder text** through [`crate::parse::pyjson`], and the
//! character-exact slicing this port needs is [`crate::parse::pystr`] — both live in
//! this crate, and copying either into `lattice-core` would create the second
//! copy that v10.0.1 spent a release removing. The dependency graph already
//! makes this crate the shared home: `lattice-platform` (the `/tools/write_file`
//! endpoint) depends on `lattice-agent`, so both write surfaces reach one
//! implementation, and no new edge was added to reach it.
//!
//! ## Two named deviations from CPython
//!
//! * **`.py` validation is a structural tokenizer, not `ast.parse`.** There is
//!   no CPython in this process. [`python_parses`] rejects only what is
//!   *definitely* broken — unbalanced brackets, an unterminated string, a
//!   character that cannot occur in Python source outside a string or comment.
//!   It errs toward "valid", which is the conservative direction: the failure
//!   mode is content written exactly as produced, never content mangled by a
//!   repair it did not need.
//! * **`json.loads` accepts `NaN`/`Infinity`; [`crate::parse::pyjson`] does not.**
//!
//! ## Layout
//!
//! One pipeline, one file per stage of it, because the stages are the Python
//! modules this is a port of: [`validate`] is `validation.py`, [`extract`] is
//! `extraction.py`, [`repair`] is `repair.py`, [`python`] is the `ast.parse`
//! call `validation.py` makes, [`salvage`] is `orchestration.py`'s
//! `_salvage_score` (v11.9.0 — the driver's last unported piece, and the one
//! that decides *which* rejected candidate repair works from), and [`text`] is
//! the regex table and the case-insensitive search they share. Everything
//! callers use is re-exported here, so `crate::content::sanitize::sanitize_write_content`
//! and the rest are where they always were.

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

/// What one sanitize pass did, mirroring Python's `meta` dict.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SanitizeMeta {
    /// The content that will be written differs from the content handed in.
    pub sanitized: bool,
    /// Deterministic repair ran — the file is a scaffold, not model output.
    pub repaired: bool,
    /// Why validation refused the original: `empty`, `ok`, or the reason text.
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

    /// The transcript's `content_sanitize`, in Python's key order.
    pub fn to_value(&self) -> Value {
        json!({
            "sanitized": self.sanitized,
            "repaired": self.repaired,
            "reason": self.reason,
        })
    }
}

/// `sanitize_write_content(target_path, content, user_request="")`.
///
/// Returns the content to write and what happened to it. Idempotent on
/// everything it produces that validates, which is every path but the two
/// documented in the module header.
pub fn sanitize_write_content(
    target_path: &str,
    content: &str,
    user_request: &str,
) -> (String, SanitizeMeta) {
    if content.trim().is_empty() {
        return (content.to_string(), SanitizeMeta::untouched("empty"));
    }
    // Channel frames must come off before validate: a thought preamble with
    // balanced `<>` still fails `node --check` on the leading `<|`.
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
    // `user_request or f"content for {target_path}"`.
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
