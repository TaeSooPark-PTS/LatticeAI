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
    compiled(&RE, r"(?:<\|[A-Za-z][A-Za-z0-9_]*\|?>|<channel\|>)[^\n<]*")
}

pub fn looks_like_channel_frame(text: &str) -> bool {
    text.contains("<|channel")
        || text.contains("<|message")
        || text.contains("<|start")
        || text.contains("<|end")
}

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
    if !framed && cleaned == text.trim() {
        return None;
    }
    Some(cleaned)
}

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
        assert_eq!(channel_payload("<|channel>thought").as_deref(), Some(""));
        assert_eq!(channel_payload("<|channel|>thought\n").as_deref(), Some(""));
        assert_eq!(strip_channel_frames("<|channel>thought"), None);
        assert_eq!(
            channel_payload("<|channel>thought\nLatticeAI").as_deref(),
            Some("LatticeAI")
        );
        assert_eq!(
            channel_payload("<|channel|>thought<|message|>real body").as_deref(),
            Some("real body")
        );
        assert_eq!(channel_payload("endless <|ends here"), None);
        assert_eq!(channel_payload("just prose"), None);
    }
}
