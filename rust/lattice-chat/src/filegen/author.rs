//! Ask the model for a file's contents, and judge what came back.
//!
//! One routine, used by the single-file branch and by every file of a
//! multi-file project:
//!
//! 1. ask the worker over the **existing** llm seam, collected rather than
//!    streamed ([`collect_completion`] with `document = true`);
//! 2. judge the reply with the write-side validator the agent path uses
//!    ([`lattice_agent::sanitize`]);
//! 3. on a reply that was not a document — one that is *about* the file rather
//!    than being it, or one only deterministic repair could rescue — ask once
//!    more with a corrective prompt that names the fault;
//! 4. accept the best of the attempts — byte-for-byte when it validates,
//!    unwrapped when extraction rescued it, repaired when nothing else was left —
//!    or fail honestly.
//!
//! ## The one thing that is never written
//!
//! [`lattice_agent::sanitize::repair_file_content`] always produces *something*:
//! handed a refusal it throws the refusal away and scaffolds a document out of
//! the user's own request. That scaffold is a legitimate answer to "the model
//! gave me a truncated page" and a lie in answer to "the model refused" — the
//! user would get a file nobody wrote. So a reply the validator scores as a
//! refusal or as commentary is **never** the source of a written file, on either
//! attempt: it is the honest 400 instead.

use lattice_agent::sanitize::{looks_like_refusal, sanitize_write_content, validate_file_content};
use serde_json::{json, Value};

use crate::pipeline::model::collect_completion;
use crate::state::ChatState;

use super::prompts;

/// How many times one file is asked for: the first attempt, then one corrective
/// retry. Two, and not more, because a third ask costs a weak model tens of
/// seconds and the second already carries the validator's own verdict — there
/// is nothing new to say in a third prompt.
pub const MAX_ATTEMPTS: usize = 2;

/// The temperature a file is written at. File content is not a place for
/// sampling variety, and it is the temperature the agent loop's own direct-file
/// fallback uses.
pub const TEMPERATURE: f64 = 0.2;

/// The floor under `max_tokens` for a file.
///
/// The SPA sends `max_tokens: 2048` for a chat turn, which is a sentence budget,
/// not a document budget: a complete HTML page hits it and arrives truncated, and
/// a truncated document is a repaired one. The request's own value still wins
/// when it is larger.
pub const MIN_TOKENS: i64 = 4096;

/// `validate_file_content`'s verdict for a reply that refused.
const REFUSAL_REASON: &str = "the reply was a refusal/chat message, not file content";

/// …and for a reply that talked about the file instead of being it.
const COMMENTARY_REASON: &str = "the reply talks about the file instead of being the file";

/// A file the model wrote, and what had to be done to it.
pub(crate) struct Authored {
    /// The bytes to write.
    pub content: String,
    /// The written content passes the validator for this file type.
    pub valid: bool,
    /// Deterministic repair ran: this is a scaffold, not the model's document.
    pub repaired: bool,
    /// One record per attempt, in order — the honesty trail.
    pub attempts: Vec<Value>,
}

/// Nothing usable came back. Carries the attempts so the caller can still say
/// what was tried.
pub(crate) struct Unauthored {
    pub attempts: Vec<Value>,
}

/// One reply, judged.
struct Judged {
    content: String,
    valid: bool,
    repaired: bool,
    sanitized: bool,
    reason: String,
    usable: bool,
}

impl Judged {
    fn unusable(reason: String) -> Self {
        Self {
            content: String::new(),
            valid: false,
            repaired: false,
            sanitized: false,
            reason,
            usable: false,
        }
    }

    /// Lower is better: clean < unwrapped < repaired < unusable.
    fn rank(&self) -> u8 {
        if !self.usable {
            3
        } else if self.repaired {
            2
        } else if self.sanitized {
            1
        } else {
            0
        }
    }

    fn outcome(&self) -> &'static str {
        match self.rank() {
            0 => "clean",
            1 => "sanitized",
            2 => "repaired",
            _ => "unusable",
        }
    }

    fn record(&self, attempt: usize) -> Value {
        json!({
            "attempt": attempt,
            "outcome": self.outcome(),
            "reason": self.reason,
        })
    }
}

/// A reply that is *about* the file rather than *being* it.
///
/// Two of the validator's verdicts mean exactly that, and both are checked by
/// text because the reason string is the only place the verdict is reported.
/// [`looks_like_refusal`] is the same predicate the validator itself consults,
/// so a refusal is caught even when the extension's own branch fails first.
fn prose_not_file(reply: &str, reason: &str) -> bool {
    looks_like_refusal(reply) || reason == REFUSAL_REASON || reason == COMMENTARY_REASON
}

/// Judge one reply for one target.
fn judge(reply: &str, stream_error: Option<&str>, target: &str, request: &str) -> Judged {
    if reply.trim().is_empty() {
        return Judged::unusable(match stream_error {
            Some(error) => format!("the model produced nothing ({error})"),
            None => "the model produced nothing".to_string(),
        });
    }
    let (ok, reason) = validate_file_content(reply, target);
    if ok {
        return Judged {
            content: reply.to_string(),
            valid: true,
            repaired: false,
            sanitized: false,
            reason,
            usable: true,
        };
    }
    if prose_not_file(reply, &reason) {
        return Judged::unusable(reason);
    }
    // The same pass `/tools/write_file` and the agent loop run, with the user's
    // request as the repair's brief.
    let (content, meta) = sanitize_write_content(target, reply, request);
    // `valid` is re-read from the bytes that will actually land, rather than
    // assumed from the fact that repair ran: repair *intends* to produce a
    // structurally valid file, and the artifact should report what it produced.
    let (valid, _) = validate_file_content(&content, target);
    Judged {
        content,
        valid,
        repaired: meta.repaired,
        sanitized: meta.sanitized,
        reason,
        usable: true,
    }
}

/// Ask the model for `target`'s contents, retrying once if the first reply was
/// not a document.
///
/// `request` is the user's own message: it is the user turn's opening and the
/// brief a repair would scaffold from. `brief` is the manifest's line about this
/// file, present only for a multi-file project.
pub(crate) async fn author_file(
    state: &ChatState,
    model_id: &str,
    request: &str,
    target: &str,
    brief: Option<&str>,
    max_tokens: i64,
) -> Result<Authored, Unauthored> {
    let user_turn = prompts::user_turn(request, target, brief);
    let max_tokens = max_tokens.max(MIN_TOKENS);
    let mut attempts: Vec<Value> = Vec::new();
    let mut best: Option<Judged> = None;
    let mut last_reason: Option<String> = None;

    for attempt in 1..=MAX_ATTEMPTS {
        let system = match last_reason.as_deref() {
            None => prompts::instructions(target),
            Some(reason) => prompts::correction(target, reason),
        };
        let (reply, stream_error) = collect_completion(
            state.worker.as_ref(),
            Some(model_id),
            &user_turn,
            &system,
            max_tokens,
            TEMPERATURE,
            None,
            true,
        )
        .await;
        let judged = judge(&reply, stream_error.as_deref(), target, request);
        attempts.push(judged.record(attempt));
        last_reason = Some(judged.reason.clone());
        let better = best
            .as_ref()
            .is_none_or(|current| judged.rank() < current.rank());
        // A retry that came back worse leaves the first attempt standing: the
        // user gets the best of what the model produced, never the latest.
        if better {
            best = Some(judged);
        }
        // The retry is for a reply that was not a document: unusable, or
        // salvaged only by repair. A clean reply and an unwrapped one are both
        // the model's own document, and asking a local model again for a file
        // it already wrote costs the user a minute for nothing.
        if best.as_ref().is_some_and(|judged| judged.rank() <= 1) {
            break;
        }
    }

    match best {
        Some(judged) if judged.usable => Ok(Authored {
            content: judged.content,
            valid: judged.valid,
            repaired: judged.repaired,
            attempts,
        }),
        _ => Err(Unauthored { attempts }),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const CLEAN_HTML: &str = "<!doctype html>\n<html><head><meta charset=\"utf-8\"><title>t</title></head><body><p>hi</p></body></html>";

    #[test]
    fn a_document_that_validates_is_written_byte_for_byte() {
        let judged = judge(CLEAN_HTML, None, "page.html", "html 파일 만들어줘");
        assert_eq!(judged.content, CLEAN_HTML);
        assert_eq!(judged.outcome(), "clean");
        assert!(judged.valid && !judged.repaired && !judged.sanitized);
    }

    #[test]
    fn a_fenced_reply_with_prose_around_it_is_unwrapped_not_repaired() {
        let messy = format!("좋아요! 요청하신 페이지입니다:\n\n```html\n{CLEAN_HTML}\n```\n\n필요하면 더 고쳐드릴게요.");
        let judged = judge(&messy, None, "page.html", "html 파일 만들어줘");
        assert_eq!(judged.outcome(), "sanitized");
        assert_eq!(judged.content, CLEAN_HTML, "the model's own document");
        assert!(judged.valid);
        assert!(!judged.repaired, "nothing was invented, only unwrapped");
    }

    #[test]
    fn a_truncated_document_is_repaired_and_says_so() {
        let truncated = "<!doctype html>\n<html><head><title>t</title></head><body><p>half";
        let judged = judge(truncated, None, "page.html", "html 파일 만들어줘");
        assert_eq!(judged.outcome(), "repaired");
        assert!(judged.repaired && judged.sanitized);
        assert!(
            judged.valid,
            "repair produces a structurally valid document"
        );
        assert!(judged.content.contains("</html>"));
    }

    #[test]
    fn a_refusal_is_never_a_file() {
        // The scaffold `repair_file_content` would build here is a document
        // nobody wrote; the branch that would have written it is the one this
        // predicate closes.
        let refusal = "I'm sorry, but I can't create that file for you.";
        let (ok, reason) = validate_file_content(refusal, "page.html");
        assert!(!ok);
        assert!(prose_not_file(refusal, &reason));
        assert!(!judge(refusal, None, "page.html", "html 파일 만들어줘").usable);

        // The prose-type verdict is the other half of the same rule.
        let commentary = "Here is the note you asked for.";
        let (_, prose_reason) = validate_file_content(commentary, "note.md");
        assert_eq!(prose_reason, COMMENTARY_REASON);
        assert!(prose_not_file(commentary, &prose_reason));
    }

    #[test]
    fn an_empty_reply_reports_the_stream_error_it_came_with() {
        let judged = judge("", Some("upstream closed"), "note.md", "메모 만들어줘");
        assert!(!judged.usable);
        assert!(judged.reason.contains("upstream closed"));
        assert!(!judge("   ", None, "note.md", "x").usable);
        assert_eq!(judge("", None, "note.md", "x").outcome(), "unusable");
    }

    #[test]
    fn the_ranking_prefers_clean_then_unwrapped_then_repaired() {
        let clean = judge(CLEAN_HTML, None, "page.html", "x");
        let messy = judge(
            &format!("here:\n```html\n{CLEAN_HTML}\n```"),
            None,
            "page.html",
            "x",
        );
        let broken = judge("<!doctype html><html><body>", None, "page.html", "x");
        let nothing = judge("", None, "page.html", "x");
        assert!(clean.rank() < messy.rank());
        assert!(messy.rank() < broken.rank());
        assert!(broken.rank() < nothing.rank());
        assert_eq!(nothing.outcome(), "unusable");
    }

    #[test]
    fn an_attempt_record_names_the_attempt_and_the_verdict() {
        let judged = judge("<!doctype html><html><body>", None, "page.html", "x");
        let record = judged.record(2);
        assert_eq!(record["attempt"], 2);
        assert_eq!(record["outcome"], "repaired");
        assert!(record["reason"]
            .as_str()
            .unwrap_or_default()
            .contains("</html>"));
    }
}
