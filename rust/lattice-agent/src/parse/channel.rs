//! gpt-oss / gemma-4 / harmony special-token frames.
//!
//! Small instruct models wrap the payload in `<|channel|>…<|message|>` or
//! `<|start|>…<|end|>` (and the same names without the second `|`). The
//! action parser and the file-content extractor both need the **final**
//! channel's body, never the thought preamble.

use std::sync::OnceLock;

use fancy_regex::Regex;

fn compiled(cell: &'static OnceLock<Regex>, pattern: &str) -> &'static Regex {
    cell.get_or_init(|| Regex::new(pattern).expect("channel pattern must compile"))
}

fn message_tag() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    compiled(&RE, r"<\|message\|?>")
}

fn channel_header() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    compiled(&RE, r"<\|channel\|?>[^\n<]*")
}

fn special_token() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    // `<|name|>` / `<|name>` and the leftover `<channel|>` some 2B replies emit.
    compiled(&RE, r"(?:<\|[A-Za-z][A-Za-z0-9_]*\|?>|<channel\|>)[^\n<]*")
}

/// Whether `text` carries a channel / start / end / message special token.
pub fn looks_like_channel_frame(text: &str) -> bool {
    text.contains("<|channel")
        || text.contains("<|message")
        || text.contains("<|start")
        || text.contains("<|end")
}

/// What a framed reply actually said — **including when that is nothing**.
///
/// `None` means *there is no frame here*: the text carries no channel/message
/// marker at all, or it carries the letters of one without ever closing it, so
/// nothing about it was framing. `Some(payload)` means the reply **was** framed
/// and this is what its last channel carried — and `Some("")` is a real answer
/// to a real question: a reply that is nothing but a frame header said nothing.
///
/// That empty case is why this exists beside [`strip_channel_frames`]
/// (v12.0.0). A live gemma-4-e2b answered whole micro-turns with `<|channel>`
/// followed by the single word `thought` and no payload. Collapsing that to
/// "no frame" hands the caller the *label* — and `thought` was then written
/// into `mcp.grep`'s `pattern`, shown to the user as a critic's reason, and
/// counted as the model's answer. A frame with an empty body is a frame, and
/// saying so is what lets a caller retry or fall back to a value it already
/// has instead of using the tokenizer's furniture as data.
pub fn channel_payload(text: &str) -> Option<String> {
    if !looks_like_channel_frame(text) {
        return None;
    }
    let (after, framed) = if let Some(end) = last_match_end(text, message_tag()) {
        (&text[end..], true)
    } else if let Some(end) = last_match_end(text, channel_header()) {
        (&text[end..], true)
    } else {
        (text, false)
    };
    let cleaned = special_token().replace_all(after, "").trim().to_string();
    // No header, and stripping changed nothing: the `<|end` this matched on was
    // a run of characters in a document, not a token.
    if !framed && cleaned == text.trim() {
        return None;
    }
    Some(cleaned)
}

/// The final channel's payload, with the framing tokens removed.
///
/// `None` when there is no frame, when the frame carried no payload, or when
/// stripping would leave the text unchanged. Callers treat `None` as "leave
/// this alone", which is why an empty payload is `None` here: a parser that has
/// been handed nothing should try its other rungs on the original text.
/// [`channel_payload`] is the same read for a caller that needs the emptiness
/// itself.
pub fn strip_channel_frames(text: &str) -> Option<String> {
    channel_payload(text).filter(|cleaned| !cleaned.is_empty() && cleaned != text.trim())
}

fn last_match_end(text: &str, pattern: &Regex) -> Option<usize> {
    let mut last = None;
    for found in pattern.find_iter(text).flatten() {
        last = Some(found.end());
    }
    last
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_message_tag_keeps_only_what_follows_it() {
        let raw =
            "<|channel|>thought\nI should read first.\n<|message|>{\"action\": \"final\"}<|end|>";
        assert_eq!(
            strip_channel_frames(raw).as_deref(),
            Some(r#"{"action": "final"}"#)
        );
    }

    #[test]
    fn the_last_channel_wins_when_there_is_no_message_tag() {
        let raw = "<|channel>thought\nnot this {\"action\": \"no\"}\n<|channel>commentary\n{\"action\": \"yes\"}";
        assert_eq!(
            strip_channel_frames(raw).as_deref(),
            Some(r#"{"action": "yes"}"#)
        );
    }

    #[test]
    fn start_end_wrappers_without_a_channel_still_unwrap() {
        let raw = "<|start|>assistant\n{\"action\": \"final\"}\n<|end|>";
        assert_eq!(
            strip_channel_frames(raw).as_deref(),
            Some(r#"{"action": "final"}"#)
        );
    }

    #[test]
    fn ordinary_text_is_left_alone() {
        assert_eq!(strip_channel_frames(r#"{"action": "final"}"#), None);
        assert_eq!(strip_channel_frames("just prose"), None);
    }

    #[test]
    fn a_frame_with_no_payload_is_a_frame_that_said_nothing() {
        // The live gemma-4-e2b micro-turn: a channel header, its label, and no
        // body. `strip_channel_frames` declines it (a parser should try its
        // other rungs on the original), but the *reply* carried no answer and
        // `channel_payload` says so — which is what stops `thought` from being
        // used as a search pattern, a critic's reason and a user's answer.
        assert_eq!(channel_payload("<|channel>thought").as_deref(), Some(""));
        assert_eq!(channel_payload("<|channel|>thought\n").as_deref(), Some(""));
        assert_eq!(strip_channel_frames("<|channel>thought"), None);
        // A payload still reads as the payload, on both spellings of the closer.
        assert_eq!(
            channel_payload("<|channel>thought\nLatticeAI").as_deref(),
            Some("LatticeAI")
        );
        assert_eq!(
            channel_payload("<|channel|>thought<|message|>real body").as_deref(),
            Some("real body")
        );
        // And text that merely contains the letters of a marker is not framed.
        assert_eq!(channel_payload("endless <|ends here"), None);
        assert_eq!(channel_payload("just prose"), None);
    }
}
