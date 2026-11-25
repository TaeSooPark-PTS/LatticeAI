use lattice_agent::content::sanitize::{
    looks_like_refusal, sanitize_write_content, validate_file_content,
};
use serde_json::{json, Value};

use crate::pipeline::model::collect_completion;
use crate::state::ChatState;

use super::prompts;

pub const MAX_ATTEMPTS: usize = 2;

pub const TEMPERATURE: f64 = 0.2;

pub const MIN_TOKENS: i64 = 4096;

const REFUSAL_REASON: &str = "the reply was a refusal/chat message, not file content";

const COMMENTARY_REASON: &str = "the reply talks about the file instead of being the file";

pub(crate) struct Authored {
    pub content: String,
    pub valid: bool,
    pub repaired: bool,
    pub attempts: Vec<Value>,
}

pub(crate) struct Unauthored {
    pub attempts: Vec<Value>,
}

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

fn prose_not_file(reply: &str, reason: &str) -> bool {
    looks_like_refusal(reply) || reason == REFUSAL_REASON || reason == COMMENTARY_REASON
}

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
    let (content, meta) = sanitize_write_content(target, reply, request);
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
        if better {
            best = Some(judged);
        }
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
        let refusal = "I'm sorry, but I can't create that file for you.";
        let (ok, reason) = validate_file_content(refusal, "page.html");
        assert!(!ok);
        assert!(prose_not_file(refusal, &reason));
        assert!(!judge(refusal, None, "page.html", "html 파일 만들어줘").usable);

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
