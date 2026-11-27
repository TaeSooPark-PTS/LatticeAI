use std::collections::HashSet;
use std::sync::OnceLock;

use fancy_regex::{escape, Regex};

use crate::build_pattern as build;

pub const CONCEPT_STOP: [&str; 109] = [
    "all",
    "also",
    "and",
    "any",
    "are",
    "based",
    "been",
    "being",
    "but",
    "can",
    "could",
    "fixme",
    "for",
    "from",
    "get",
    "had",
    "has",
    "have",
    "how",
    "into",
    "its",
    "just",
    "let",
    "like",
    "may",
    "might",
    "must",
    "new",
    "not",
    "note",
    "our",
    "out",
    "per",
    "set",
    "shall",
    "should",
    "such",
    "than",
    "that",
    "the",
    "their",
    "them",
    "then",
    "these",
    "they",
    "this",
    "those",
    "todo",
    "use",
    "used",
    "using",
    "via",
    "warning",
    "was",
    "were",
    "what",
    "when",
    "where",
    "which",
    "why",
    "will",
    "with",
    "would",
    "yes",
    "you",
    "your",
    "거기",
    "결과",
    "경우",
    "그것",
    "그리고",
    "기능",
    "내용",
    "답변",
    "되다",
    "됩니다",
    "모델",
    "방법",
    "버전",
    "부분",
    "사용",
    "사용자",
    "상태",
    "서버",
    "설명",
    "설정",
    "실행",
    "없어",
    "여기",
    "우리",
    "이것",
    "이다",
    "이야",
    "이전",
    "이후",
    "입니다",
    "있어",
    "저것",
    "저기",
    "저희",
    "정도",
    "주의",
    "지원",
    "참고",
    "채팅",
    "처럼",
    "파일",
    "하다",
    "한다",
];

fn stop_set() -> &'static HashSet<&'static str> {
    static SET: OnceLock<HashSet<&'static str>> = OnceLock::new();
    SET.get_or_init(|| CONCEPT_STOP.into_iter().collect())
}

struct Patterns {
    backtick: Regex,
    code_expr: Regex,
    quoted: Regex,
    proper_mixed: Regex,
    proper_caps: Regex,
    single_caps: Regex,
    sentence_start: Regex,
    ko_suffix: Regex,
    ko_particle: Regex,
    hyphenated: Regex,
    fallback_token: Regex,
}

fn patterns() -> &'static Patterns {
    static PATTERNS: OnceLock<Patterns> = OnceLock::new();
    PATTERNS.get_or_init(|| Patterns {
        backtick: build(r"`([^`]{2,40})`"),
        code_expr: build(r"[\(\)\[\]{}]"),
        quoted: build(r#""([^"]{2,40})""#),
        proper_mixed: build(
            r"([A-Z][a-z]{1,20}(?:\s+(?:[A-Z]{2,10}|[A-Z][a-z0-9]{1,20}|\d[\w.]{0,6})){1,3})",
        ),
        proper_caps: build(r"([A-Z]{2,6}(?:\s+(?:[A-Z]{2,10}|[A-Z][a-z0-9]{1,20})){1,2})"),
        single_caps: build(r"(?<![A-Za-z0-9])([A-Z][A-Za-z0-9]{2,24})(?![A-Za-z0-9])"),
        sentence_start: build(r"(?:^|(?<=[.!?])\s+)([A-Z][a-z]+)"),
        ko_suffix: build(
            r"[가-힣]{2,12}(?:AI|LLM|API|UI|RAG|bot|Bot|기능|모델|서버|에이전트|파이프라인|워크플로)",
        ),
        ko_particle: build(r"([가-힣]{2,12})(?:은|는|이|가|을|를|의|에서|으로|와|과)"),
        hyphenated: build(r"\b([a-zA-Z][a-zA-Z0-9]*(?:-[a-zA-Z0-9.]+)+)\b"),
        fallback_token: build(r"[A-Za-z][A-Za-z0-9_.:-]{2,}|[가-힣]{2,12}"),
    })
}

fn findall_group1(re: &Regex, text: &str) -> Vec<String> {
    re.captures_iter(text)
        .filter_map(|caps| caps.ok())
        .filter_map(|caps| caps.get(1).map(|m| m.as_str().to_string()))
        .collect()
}

fn findall_whole(re: &Regex, text: &str) -> Vec<String> {
    re.find_iter(text)
        .filter_map(|m| m.ok())
        .map(|m| m.as_str().to_string())
        .collect()
}

#[derive(Default)]
struct SeenTerms {
    keys: HashSet<String>,
    items: Vec<(String, String)>,
}

impl SeenTerms {
    fn add(&mut self, term: &str) {
        let stripped = term.trim();
        let key = stripped.to_lowercase();
        if key.is_empty() || stop_set().contains(key.as_str()) {
            return;
        }
        if key.chars().all(|c| c.is_ascii_digit()) || key.chars().count() < 2 {
            return;
        }
        if self.keys.insert(key.clone()) {
            self.items.push((key, stripped.to_string()));
        }
    }

    fn values(self) -> Vec<String> {
        self.items.into_iter().map(|(_, value)| value).collect()
    }
}

pub fn extract_concepts_rules(text: &str, limit: usize) -> Vec<String> {
    let p = patterns();
    let mut seen = SeenTerms::default();

    for term in findall_group1(&p.backtick, text) {
        if !p.code_expr.is_match(&term).unwrap_or(false) {
            seen.add(&term);
        }
    }
    for term in findall_group1(&p.quoted, text) {
        seen.add(&term);
    }
    for term in findall_group1(&p.proper_mixed, text) {
        seen.add(&term);
    }
    for term in findall_group1(&p.proper_caps, text) {
        seen.add(&term);
    }
    let mut freq: Vec<(String, usize)> = Vec::new();
    for word in findall_group1(&p.single_caps, text) {
        match freq.iter_mut().find(|(known, _)| *known == word) {
            Some(entry) => entry.1 += 1,
            None => freq.push((word, 1)),
        }
    }
    let sentence_starts: HashSet<String> = findall_group1(&p.sentence_start, text)
        .into_iter()
        .collect();
    for (word, count) in &freq {
        if stop_set().contains(word.to_lowercase().as_str()) {
            continue;
        }
        if *count >= 2 || !sentence_starts.contains(word) {
            seen.add(word);
        }
    }
    for term in findall_whole(&p.ko_suffix, text) {
        seen.add(&term);
    }
    for term in findall_group1(&p.ko_particle, text) {
        if stop_set().contains(term.to_lowercase().as_str()) || term.chars().count() < 2 {
            continue;
        }
        if term.chars().count() >= 3 || text.matches(term.as_str()).count() >= 2 {
            seen.add(&term);
        }
    }
    for term in findall_group1(&p.hyphenated, text) {
        if term.chars().count() >= 4 {
            seen.add(&term);
        }
    }

    let values = seen.values();
    let kept = drop_never_standalone(&values, text);
    values
        .into_iter()
        .zip(kept)
        .filter(|(_, keep)| *keep)
        .map(|(v, _)| v)
        .take(limit)
        .collect()
}

fn drop_never_standalone(values: &[String], text: &str) -> Vec<bool> {
    let lowers: Vec<Vec<char>> = values
        .iter()
        .map(|v| v.to_lowercase().chars().collect())
        .collect();
    let mut keep = vec![true; values.len()];
    for i in 0..values.len() {
        let vl = &lowers[i];
        for j in 0..values.len() {
            if i == j || !keep[j] {
                continue;
            }
            let wl = &lowers[j];
            if wl.len() < vl.len() || !wl.starts_with(vl.as_slice()) {
                continue;
            }
            let suffix: Vec<char> = wl[vl.len()..].to_vec();
            if !matches!(suffix.first(), Some(c) if c.is_whitespace() || *c == '-') {
                continue;
            }
            let stripped: String = suffix
                .iter()
                .skip_while(|c| **c == ' ' || **c == '-')
                .collect();
            let pattern = format!(
                r"(?i){}(?![\s\-]*{})",
                escape(&values[i]),
                escape(&stripped)
            );
            let Ok(alone) = Regex::new(&pattern) else {
                continue;
            };
            if alone.find_iter(text).filter(|m| m.is_ok()).count() == 0 {
                keep[i] = false;
                break;
            }
        }
    }
    keep
}

pub fn topic_candidates(text: &str, limit: usize) -> Vec<String> {
    let candidates = extract_concepts_rules(text, limit);
    if !candidates.is_empty() {
        return candidates.into_iter().take(limit).collect();
    }
    let mut seen = SeenTerms::default();
    for token in findall_whole(&patterns().fallback_token, text) {
        let key = token.to_lowercase();
        if stop_set().contains(key.as_str())
            || (!key.is_empty() && key.chars().all(|c| c.is_ascii_digit()))
        {
            continue;
        }
        if seen.keys.insert(key.clone()) {
            seen.items.push((key, token));
        }
        if seen.items.len() >= limit {
            break;
        }
    }
    seen.values().into_iter().take(limit).collect()
}

pub const KOREAN_PARTICLES: [&str; 19] = [
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
    "이",
    "가",
    "의",
    "와",
    "과",
];

pub fn is_hangul(c: char) -> bool {
    ('\u{AC00}'..='\u{D7A3}').contains(&c)
}

pub fn strip_korean_particle(token: &str) -> &str {
    if !token.chars().last().is_some_and(is_hangul) {
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

fn ranking_key(term: &str) -> Option<String> {
    let key = strip_korean_particle(term.trim()).to_lowercase();
    let countable = key.chars().count() >= 2 && !key.chars().all(|c| c.is_ascii_digit());
    (countable && !stop_set().contains(key.as_str())).then_some(key)
}

pub const CONCEPT_TERM_WEIGHT: f64 = 2.0;
pub const TOKEN_TERM_WEIGHT: f64 = 1.0;

pub fn weighted_ranking_terms(text: &str, limit: usize) -> Vec<(String, f64)> {
    let mut seen: HashSet<String> = HashSet::new();
    let mut out: Vec<(String, f64)> = Vec::new();
    let concepts = extract_concepts_rules(text, limit)
        .into_iter()
        .map(|term| (term, CONCEPT_TERM_WEIGHT));
    let tokens = findall_whole(&patterns().fallback_token, text)
        .into_iter()
        .map(|term| (term, TOKEN_TERM_WEIGHT));
    for (term, weight) in concepts.chain(tokens) {
        if out.len() >= limit {
            break;
        }
        if let Some(key) = ranking_key(&term) {
            if seen.insert(key.clone()) {
                out.push((key, weight));
            }
        }
    }
    out
}

pub fn ranking_terms(text: &str, limit: usize) -> Vec<String> {
    weighted_ranking_terms(text, limit)
        .into_iter()
        .map(|(term, _)| term)
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stop_words_and_digits_never_become_concepts() {
        assert_eq!(CONCEPT_STOP.len(), 109);
        assert!(stop_set().contains("the"));
        let mut seen = SeenTerms::default();
        seen.add("  The  ");
        seen.add("42");
        seen.add("x");
        seen.add("");
        assert!(seen.values().is_empty());
    }

    #[test]
    fn backtick_terms_skip_code_expressions() {
        let got = extract_concepts_rules("use `retrieval mode` not `foo(bar)` here", 12);
        assert!(got.contains(&"retrieval mode".to_string()));
        assert!(!got.iter().any(|v| v.contains('(')));
    }

    #[test]
    fn quoted_and_proper_nouns_are_collected() {
        let got = extract_concepts_rules(r#"The "fusion table" of Lattice AI and VS Code"#, 12);
        assert!(got.contains(&"fusion table".to_string()));
        assert!(got.contains(&"Lattice AI".to_string()));
        assert!(got.contains(&"VS Code".to_string()));
    }

    #[test]
    fn a_short_term_that_never_stands_alone_is_dropped() {
        let got = extract_concepts_rules("Lattice AI ships. Lattice AI again.", 12);
        assert!(got.contains(&"Lattice AI".to_string()));
        assert!(!got.contains(&"Lattice".to_string()));
        let kept = extract_concepts_rules("Claude Sonnet and Claude alone.", 12);
        assert!(kept.contains(&"Claude".to_string()));
    }

    #[test]
    fn korean_compounds_and_particles() {
        assert_eq!(
            extract_concepts_rules("검색기능은 에이전트가 담당합니다", 12),
            vec!["검색기능".to_string(), "에이전트".to_string()]
        );
        assert!(extract_concepts_rules("회의 결정 사항", 12).is_empty());
        assert_eq!(
            topic_candidates("회의 결정 사항", 12),
            vec!["회의".to_string(), "결정".to_string(), "사항".to_string()]
        );
    }

    #[test]
    fn hyphenated_identifiers_need_four_characters() {
        let got = extract_concepts_rules("mlx-vlm and a-b", 12);
        assert!(got.contains(&"mlx-vlm".to_string()));
        assert!(!got.contains(&"a-b".to_string()));
    }

    #[test]
    fn topic_candidates_fall_back_to_bare_tokens() {
        let got = topic_candidates("zzqq wumpus nonsense", 8);
        assert_eq!(got, vec!["zzqq", "wumpus", "nonsense"]);
        assert_eq!(topic_candidates("zzqq wumpus nonsense", 2).len(), 2);
        assert!(topic_candidates("", 8).is_empty());
        assert!(topic_candidates("!!! 123", 8).is_empty());
    }

    #[test]
    fn ranking_terms_union_concepts_with_tokens_and_strip_particles() {
        assert_eq!(
            ranking_terms("What technology powers Atlas?", 12),
            vec!["atlas", "technology", "powers"]
        );
        assert_eq!(
            ranking_terms("프로젝트 아틀라스의 현재 담당자는 누구인가?", 12),
            vec!["아틀라스", "담당자", "누구인", "프로젝트", "현재"]
        );
        assert_eq!(ranking_terms("a b 42", 12), Vec::<String>::new());
        assert_eq!(ranking_terms("alpha beta gamma", 2).len(), 2);
        assert_eq!(strip_korean_particle("문서에서"), "문서");
        assert_eq!(strip_korean_particle("가의"), "가의");
        assert_eq!(strip_korean_particle("Atlas"), "Atlas");
    }

    #[test]
    fn topic_candidates_prefer_the_rules_result() {
        let got = topic_candidates("Lattice AI ranking", 8);
        assert!(got.contains(&"Lattice AI".to_string()));
        assert!(extract_concepts_rules("Lattice AI ranking", 1).len() <= 1);
    }
}
