use std::collections::BTreeSet;

use serde_json::{json, Value};

use crate::temporal::parse_time_intent;

const PHRASE_CLOCK_SECS: f64 = 1_000_000_000.0;
pub const MAX_SUB_QUERIES: usize = 2;
pub const MAX_EXPANDED_QUERIES: usize = 5;
const MAX_CARRIED_TERMS: usize = 8;
const HISTORY_LOOKBACK: usize = 6;
const FOLLOW_UP_MAX_TERMS: usize = 3;

const STOPWORDS: &[&str] = &[
    "a",
    "about",
    "again",
    "also",
    "an",
    "and",
    "any",
    "are",
    "as",
    "at",
    "be",
    "been",
    "but",
    "by",
    "can",
    "could",
    "did",
    "do",
    "does",
    "else",
    "explain",
    "for",
    "from",
    "give",
    "had",
    "has",
    "have",
    "he",
    "her",
    "him",
    "his",
    "how",
    "i",
    "in",
    "is",
    "it",
    "its",
    "list",
    "me",
    "more",
    "my",
    "now",
    "of",
    "on",
    "one",
    "ones",
    "or",
    "other",
    "our",
    "please",
    "same",
    "she",
    "should",
    "show",
    "so",
    "tell",
    "than",
    "that",
    "the",
    "their",
    "them",
    "then",
    "there",
    "these",
    "they",
    "this",
    "those",
    "to",
    "was",
    "we",
    "were",
    "what",
    "what's",
    "when",
    "where",
    "which",
    "who",
    "whom",
    "why",
    "will",
    "with",
    "would",
    "you",
    "your",
    "그",
    "이",
    "저",
    "더",
    "또",
    "좀",
    "위",
    "것",
    "거",
    "수",
    "때",
    "등",
    "그럼",
    "그러면",
    "그리고",
    "그래서",
    "근데",
    "그런데",
    "또는",
    "그건",
    "그거",
    "그게",
    "그것",
    "이건",
    "이거",
    "이게",
    "이것",
    "저건",
    "거기",
    "여기",
    "그때",
    "해당",
    "자세히",
    "대해",
    "대한",
    "대해서",
    "관련",
    "관해",
    "어떤",
    "어때",
    "어떻게",
    "어디",
    "언제",
    "누구",
    "무엇",
    "뭐",
    "뭐야",
    "뭐지",
    "뭐였지",
    "뭔데",
    "왜",
    "얼마",
    "얼마야",
    "있어",
    "있나",
    "있나요",
    "있었어",
    "해줘",
    "알려",
    "알려줘",
    "알려주세요",
    "설명",
    "설명해",
    "설명해줘",
    "말해줘",
    "보여줘",
    "주세요",
    "줄래",
];

const TIME_WORDS: &[&str] = &[
    "latest",
    "newest",
    "recent",
    "recently",
    "today",
    "yesterday",
    "last",
    "week",
    "month",
    "year",
    "since",
    "before",
    "after",
    "current",
    "currently",
    "최근",
    "최신",
    "오늘",
    "어제",
    "지난",
    "지난주",
    "지난달",
    "이번",
    "이번주",
    "이번달",
    "요즘",
];

const SMALL_TALK: &[&str] = &[
    "hi",
    "hello",
    "hey",
    "thanks",
    "thank you",
    "thank you so much",
    "ok",
    "okay",
    "cool",
    "great",
    "nice",
    "bye",
    "good morning",
    "good night",
    "yes",
    "no",
    "안녕",
    "안녕하세요",
    "고마워",
    "고마워요",
    "고맙습니다",
    "감사",
    "감사합니다",
    "감사해요",
    "좋아",
    "좋아요",
    "네",
    "응",
    "아니",
    "아니요",
    "알겠어",
    "알겠습니다",
    "그래",
    "ㅋㅋ",
    "ㅎㅎ",
    "수고했어",
];

const KOREAN_PARTICLES: &[&str] = &[
    "에서는",
    "에서",
    "에게",
    "으로",
    "부터",
    "까지",
    "이랑",
    "하고",
    "은요",
    "는요",
    "은",
    "는",
    "을",
    "를",
];

const KOREAN_LEADERS: &[&str] = &["그럼", "그러면", "그리고", "그래서", "근데", "그런데", "또"];

const ENGLISH_LEADERS: &[&str] = &[
    "and what about ",
    "what about ",
    "how about ",
    "and ",
    "also ",
    "then ",
    "so ",
    "but ",
];

const KOREAN_FOLLOW_UP_STARTS: &[&str] = &[
    "그럼",
    "그러면",
    "그리고",
    "그건",
    "그거",
    "그게",
    "그것",
    "그래서",
    "근데",
    "그런데",
    "그 ",
    "그때",
    "거기",
    "이건",
    "이거",
    "이게",
    "이것",
    "해당 ",
    "또 ",
    "더 ",
];

const KOREAN_REFERENCES: &[&str] = &["그거", "그것", "그건", "그게", "거기", "해당 "];

const ENGLISH_FOLLOW_UP_STARTS: &[&str] = &[
    "and ",
    "what about",
    "how about",
    "also ",
    "then ",
    "so ",
    "but ",
    "why not",
    "tell me more",
    "more ",
];

const ENGLISH_PRONOUNS: &[&str] = &[
    "it", "its", "they", "them", "their", "those", "these", "he", "she", "him", "her", "his",
];

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct QueryPlan {
    pub original: String,
    pub standalone: String,
    pub sub_queries: Vec<String>,
    pub follow_up: bool,
    pub carried_terms: Vec<String>,
}

impl QueryPlan {
    pub fn as_json(&self) -> Value {
        json!({
            "strategy": "rules",
            "original": self.original,
            "standalone": self.standalone,
            "sub_queries": self.sub_queries,
            "follow_up": self.follow_up,
        })
    }

    pub fn expanded_queries(&self) -> Vec<String> {
        let mut out = self.sub_queries.clone();
        let terms = content_terms(&self.standalone);
        for term in terms.iter().filter(|term| !is_time_word(term)) {
            push_distinct(&mut out, term.clone(), &self.standalone);
        }
        if !self.carried_terms.is_empty() {
            push_distinct(&mut out, self.carried_terms.join(" "), &self.standalone);
        }
        out.truncate(MAX_EXPANDED_QUERIES);
        out
    }
}

fn is_hangul(c: char) -> bool {
    ('\u{AC00}'..='\u{D7A3}').contains(&c)
}

fn strip_particle(token: &str) -> &str {
    if !token.chars().any(is_hangul) {
        return token;
    }
    for particle in KOREAN_PARTICLES {
        if let Some(stem) = token.strip_suffix(particle) {
            if stem.chars().count() >= 2 {
                return stem;
            }
        }
    }
    token
}

fn raw_tokens(text: &str) -> impl Iterator<Item = &str> {
    text.split(|c: char| !(c.is_alphanumeric() || matches!(c, '_' | '-' | '.' | '\'' | '/')))
        .map(|token| token.trim_matches(['.', '-', '\'', '/', '_']))
        .filter(|token| !token.is_empty())
}

pub fn content_terms(text: &str) -> Vec<String> {
    let mut seen = BTreeSet::new();
    let mut out = Vec::new();
    for token in raw_tokens(text) {
        let lower = token.to_lowercase();
        if STOPWORDS.contains(&lower.as_str()) {
            continue;
        }
        let stem = strip_particle(token);
        let stem_lower = stem.to_lowercase();
        if STOPWORDS.contains(&stem_lower.as_str()) {
            continue;
        }
        if stem.chars().count() < 2 && !stem.chars().all(|c| c.is_ascii_digit()) {
            continue;
        }
        if seen.insert(stem_lower) {
            out.push(stem.to_string());
        }
    }
    out
}

fn is_time_word(term: &str) -> bool {
    TIME_WORDS.contains(&term.to_lowercase().as_str())
}

fn normalized(message: &str) -> String {
    message
        .trim()
        .trim_end_matches(['?', '!', '.', '~', '？'])
        .trim()
        .to_lowercase()
}

pub fn is_small_talk(message: &str) -> bool {
    let text = normalized(message);
    text.is_empty() || SMALL_TALK.contains(&text.as_str())
}

pub fn is_follow_up(message: &str) -> bool {
    if is_small_talk(message) {
        return false;
    }
    let lower = message.trim().to_lowercase();
    let bare = normalized(message);
    if KOREAN_FOLLOW_UP_STARTS
        .iter()
        .chain(ENGLISH_FOLLOW_UP_STARTS)
        .any(|start| lower.starts_with(start))
        || KOREAN_REFERENCES.iter().any(|word| lower.contains(word))
    {
        return true;
    }
    let words: Vec<&str> = raw_tokens(&lower).collect();
    if words.iter().any(|word| ENGLISH_PRONOUNS.contains(word)) {
        return true;
    }
    let terms = content_terms(message).len();
    if terms <= 2 && words.iter().any(|word| matches!(*word, "that" | "this")) {
        return true;
    }
    let korean_ellipsis = ["은", "는", "은요", "는요"]
        .iter()
        .any(|tail| bare.ends_with(tail))
        && bare.chars().any(is_hangul);
    if korean_ellipsis && terms <= FOLLOW_UP_MAX_TERMS {
        return true;
    }
    lower.ends_with(['?', '？']) && terms <= 1
}

fn strip_leader(message: &str) -> String {
    let mut text = message.trim();
    let lower = text.to_lowercase();
    for leader in ENGLISH_LEADERS {
        if lower.starts_with(leader) {
            text = text[leader.len()..].trim_start();
            break;
        }
    }
    let mut words = text.split_whitespace().peekable();
    while let Some(word) = words.peek() {
        if KOREAN_LEADERS.contains(&word.trim_end_matches(',')) {
            words.next();
        } else {
            break;
        }
    }
    let rest = words.collect::<Vec<_>>().join(" ");
    if rest.is_empty() {
        message.trim().to_string()
    } else {
        rest
    }
}

fn without_time_phrases(message: &str) -> String {
    let mut text = message.to_string();
    loop {
        let phrases = parse_time_intent(&text, PHRASE_CLOCK_SECS).phrases;
        let before = text.len();
        for phrase in phrases.iter().filter(|phrase| !phrase.is_empty()) {
            text = text.replacen(phrase.as_str(), " ", 1);
        }
        if text.len() >= before {
            return text;
        }
    }
}

fn topic_terms(message: &str) -> Vec<String> {
    content_terms(&without_time_phrases(message))
        .into_iter()
        .filter(|term| !is_time_word(term))
        .collect()
}

fn carried_terms(message: &str, history: &[String]) -> Vec<String> {
    let recent: Vec<&String> = history
        .iter()
        .rev()
        .filter(|prior| !is_small_talk(prior))
        .take(HISTORY_LOOKBACK)
        .collect();
    let anchor = recent
        .iter()
        .position(|prior| !is_follow_up(prior) && !topic_terms(prior).is_empty())
        .or_else(|| {
            recent
                .iter()
                .position(|prior| !topic_terms(prior).is_empty())
        });
    let Some(anchor) = anchor else {
        return Vec::new();
    };
    let present: BTreeSet<String> = content_terms(message)
        .iter()
        .map(|term| term.to_lowercase())
        .collect();
    let mut sources = vec![recent[anchor]];
    if anchor > 0 {
        sources.push(recent[0]);
    }
    let mut seen = present;
    let mut out = Vec::new();
    for source in sources {
        for term in topic_terms(source) {
            if out.len() < MAX_CARRIED_TERMS && seen.insert(term.to_lowercase()) {
                out.push(term);
            }
        }
    }
    out
}

fn push_distinct(out: &mut Vec<String>, candidate: String, primary: &str) {
    let candidate = candidate.trim().to_string();
    let key = candidate.to_lowercase();
    if candidate.is_empty()
        || key == primary.trim().to_lowercase()
        || out.iter().any(|known| known.to_lowercase() == key)
    {
        return;
    }
    out.push(candidate);
}

fn comparison_parts(text: &str) -> Vec<String> {
    let lower = format!(" {} ", text.to_lowercase());
    let compares = ["비교", "차이", "compare", "difference between"]
        .iter()
        .any(|cue| lower.contains(cue));
    let mut separators = vec![" vs ", " vs. ", " versus ", " and ", " 그리고 ", " 및 "];
    if compares {
        separators.extend(["랑 ", "와 ", "과 ", " with "]);
    }
    let Some((at, separator)) = separators
        .iter()
        .filter_map(|separator| lower.find(separator).map(|at| (at, *separator)))
        .min_by_key(|(at, _)| *at)
    else {
        return Vec::new();
    };
    let (left, right) = (&lower[..at], &lower[at + separator.len()..]);
    let cues = [
        "비교",
        "차이",
        "차이점",
        "compare",
        "difference",
        "between",
        "비교해줘",
    ];
    let side = |part: &str| {
        content_terms(part)
            .into_iter()
            .filter(|term| !cues.contains(&term.as_str()))
            .collect::<Vec<_>>()
            .join(" ")
    };
    let (left, right) = (side(left), side(right));
    if left.is_empty() || right.is_empty() {
        return Vec::new();
    }
    vec![left, right]
}

fn sub_queries(standalone: &str, message: &str, follow_up: bool) -> Vec<String> {
    let mut out = Vec::new();
    for part in comparison_parts(standalone) {
        push_distinct(&mut out, part, standalone);
    }
    let terms = content_terms(standalone);
    if terms.len() >= 2 {
        push_distinct(&mut out, terms.join(" "), standalone);
    }
    if follow_up {
        push_distinct(&mut out, content_terms(message).join(" "), standalone);
    }
    out.truncate(MAX_SUB_QUERIES);
    out
}

pub fn plan_query(message: &str, history: &[String]) -> QueryPlan {
    let original = message.trim().to_string();
    let carried = if is_follow_up(&original) {
        carried_terms(&original, history)
    } else {
        Vec::new()
    };
    let follow_up = !carried.is_empty();
    let standalone = if follow_up {
        format!("{} {}", carried.join(" "), strip_leader(&original))
    } else {
        original.clone()
    };
    let sub_queries = if is_small_talk(&original) {
        Vec::new()
    } else {
        sub_queries(&standalone, &original, follow_up)
    };
    QueryPlan {
        original,
        standalone,
        sub_queries,
        follow_up,
        carried_terms: carried,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn history(items: &[&str]) -> Vec<String> {
        items.iter().map(|item| item.to_string()).collect()
    }

    #[test]
    fn korean_follow_ups_carry_the_previous_topic() {
        let prior = history(&["피닉스 배포 계획 알려줘"]);
        let plan = plan_query("그럼 롤백은?", &prior);
        assert!(plan.follow_up);
        assert_eq!(plan.standalone, "피닉스 배포 계획 롤백은?");
        assert!(!plan.sub_queries.is_empty());
        let chained = plan_query(
            "그건 누가 담당해?",
            &history(&["피닉스 배포 계획 알려줘", "그럼 롤백은?"]),
        );
        assert!(chained.standalone.contains("피닉스"), "{chained:?}");
        assert!(chained.standalone.contains("롤백"), "{chained:?}");
    }

    #[test]
    fn english_follow_ups_carry_the_previous_topic() {
        let prior = history(&["What is the Phoenix deployment plan?"]);
        let plan = plan_query("what about its rollback?", &prior);
        assert!(plan.follow_up);
        assert_eq!(plan.standalone, "Phoenix deployment plan its rollback?");
        assert!(plan.sub_queries.len() <= MAX_SUB_QUERIES);
        assert!(plan_query("and the port?", &prior)
            .standalone
            .starts_with("Phoenix deployment plan"));
        assert!(plan_query("why?", &prior).follow_up);
    }

    #[test]
    fn standalone_questions_and_small_talk_are_left_alone() {
        let prior = history(&["What is the Phoenix deployment plan?"]);
        for message in [
            "How do transformers compute attention weights?",
            "고마워",
            "thanks!",
            "파이썬에서 리스트 정렬하는 방법 알려줘",
        ] {
            let plan = plan_query(message, &prior);
            assert!(!plan.follow_up, "{message}");
            assert_eq!(plan.standalone, message);
        }
        assert!(plan_query("hello", &prior).sub_queries.is_empty());
        let lonely = plan_query("그럼 롤백은?", &[]);
        assert!(!lonely.follow_up);
        assert_eq!(lonely.standalone, "그럼 롤백은?");
    }

    #[test]
    fn comparisons_split_into_both_sides_and_planning_is_deterministic() {
        let plan = plan_query("compare Postgres and SQLite", &[]);
        assert_eq!(plan.sub_queries, vec!["postgres", "sqlite"]);
        let korean = plan_query("알파 버전과 베타 버전 차이", &[]);
        assert_eq!(korean.sub_queries, vec!["알파 버전", "베타 버전"]);
        assert_eq!(plan, plan_query("compare Postgres and SQLite", &[]));
        let expanded = plan.expanded_queries();
        assert!(expanded.len() <= MAX_EXPANDED_QUERIES);
        assert!(expanded
            .iter()
            .any(|query| query == "Postgres" || query == "postgres"));
    }

    #[test]
    fn carried_terms_drop_old_time_words() {
        let plan = plan_query("그럼 담당자는?", &history(&["지난주 회의 결정 사항"]));
        assert!(!plan.standalone.contains("지난주"), "{plan:?}");
        assert!(plan.standalone.contains("회의"));
    }

    #[test]
    fn earlier_dates_and_history_words_never_become_a_filter_on_the_follow_up() {
        for prior in [
            "since 2026-01-01 what changed in the Phoenix budget",
            "2026년 3월 5일 이후 Phoenix budget 결정",
            "그저께 올해 작년 Phoenix budget 회의",
            "the previous version of the Phoenix budget since v14.1",
        ] {
            let plan = plan_query("what about its owner?", &history(&[prior]));
            assert!(plan.follow_up, "{prior}");
            assert!(plan.standalone.contains("Phoenix"), "{plan:?}");
            let intent = parse_time_intent(&plan.standalone, PHRASE_CLOCK_SECS);
            assert!(!intent.has_time_signal(), "{plan:?} {intent:?}");
            assert!(!intent.historical, "{plan:?}");
        }
        let kept = plan_query(
            "그럼 지난주 담당자는?",
            &history(&["Phoenix budget 알려줘"]),
        );
        assert!(parse_time_intent(&kept.standalone, PHRASE_CLOCK_SECS)
            .window
            .is_some());
    }
}
