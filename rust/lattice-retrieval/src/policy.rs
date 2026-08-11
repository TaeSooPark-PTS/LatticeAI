//! Port of `lattice_brain/graph/retrieval_policy.py` + the query-class
//! heuristics and weight table it composes from `lattice_brain/graph/fusion.py`.
//!
//! Two details decide behaviour and are easy to get backwards: classification
//! runs on the **original** query (so a rewrite can never change the class), and
//! `recency_half_life_days` is non-`None` for exactly one class — it is the
//! signal that age decay applies there and nowhere else.

use std::sync::OnceLock;

use fancy_regex::Regex;

/// `LATTICEAI_QUERY_REWRITE` — anything falsy disables rewriting entirely.
pub const QUERY_REWRITE_ENV: &str = "LATTICEAI_QUERY_REWRITE";
/// `retrieval_policy.RECENCY_HALF_LIFE_DAYS`.
pub const RECENCY_HALF_LIFE_DAYS: f64 = 14.0;
/// A filler strip only applies when at least this many characters survive.
const MIN_REMAINDER_CHARS: usize = 4;

/// The resolved policy for one query.
#[derive(Debug, Clone, PartialEq)]
pub struct Policy {
    pub query_class: String,
    pub alpha: f64,
    pub fusion_strategy: String,
    pub original_query: String,
    pub search_query: String,
    pub rewrite_rules: Vec<String>,
    pub recency_half_life_days: Option<f64>,
}

struct Patterns {
    code_fence: Regex,
    code_ident: Regex,
    code_word: Regex,
    person: Regex,
    recency: Regex,
    whitespace: Regex,
    ko_filler_tail: Regex,
    en_filler_lead: Regex,
    en_filler_tail: Regex,
}

fn patterns() -> &'static Patterns {
    static PATTERNS: OnceLock<Patterns> = OnceLock::new();
    PATTERNS.get_or_init(|| Patterns {
        code_fence: build(r"```|`[^`\n]+`"),
        code_ident: build(
            r"[A-Za-z_][A-Za-z0-9_]*\(\)|(?<![A-Za-z0-9_])[a-z0-9]+_[a-z0-9_]+|(?<![A-Za-z0-9_])[a-z]+[A-Z][A-Za-z0-9]*|(?<![A-Za-z0-9_])[\w-]+\.(?:py|js|jsx|ts|tsx|json|yaml|yml|css|html|sql|sh|go|rs|java|rb)(?![A-Za-z0-9])",
        ),
        code_word: build(
            r"(?i)\b(?:def|class|import|function|traceback|exception|stack\s*trace|bug|error|null|undefined|api|sql|regex)\b|코드|함수|버그|에러|오류|스택|컴파일|빌드\s*실패|구현",
        ),
        person: build(
            r"(?i)\b(?:who|whom|whose)\b|누구|어떤\s*사람|담당자|팀원|동료|만난\s*사람|[\w.+-]+@[\w-]+\.[\w.]+|[가-힣]{2,4}\s*(?:님|씨)(?:\s|$|[을를이가은는의??.!,])",
        ),
        recency: build(
            r"(?i)최근|어제|오늘|그저께|방금|아까|지난\s*주|지난주|지난\s*달|지난달|이번\s*주|이번주|이번\s*달|이번달|\b(?:recent|recently|yesterday|today|latest|last\s+(?:week|month|night|meeting))\b",
        ),
        whitespace: build(r"\s+"),
        ko_filler_tail: build(
            r"(?:^|(?<=\s))(?:좀\s+)?(?:알려\s*줘요?|알려\s*주세요|알려\s*줄래요?|말해\s*줘요?|말해\s*주세요|설명해\s*줘요?|설명해\s*주세요|궁금해요?|궁금합니다|뭐였지|뭐였더라|뭐더라|뭐지|뭐야)\s*[?!.…~]*\s*$",
        ),
        en_filler_lead: build(
            r"(?i)^(?:please\s+)?(?:tell\s+me\s+about|what\s+is|what\s+was|what\s+are|what's)\s+",
        ),
        en_filler_tail: build(r"(?i)[,\s]*\bplease\b\s*[?!.]*\s*$"),
    })
}

fn build(pattern: &str) -> Regex {
    Regex::new(pattern).expect("ported pattern must compile")
}

/// `fusion.classify_query` — code → recency → person → fact, in that order.
pub fn classify_query(query: &str) -> &'static str {
    let text = query.trim();
    if text.is_empty() {
        return "fact";
    }
    let p = patterns();
    let hit = |re: &Regex| re.is_match(text).unwrap_or(false);
    if hit(&p.code_fence) || hit(&p.code_ident) || hit(&p.code_word) {
        return "code";
    }
    if hit(&p.recency) {
        return "recency";
    }
    if hit(&p.person) {
        return "person";
    }
    "fact"
}

/// The vector share for a class — `fusion.DEFAULT_FUSION_WEIGHTS[cls]["alpha"]`.
pub fn class_alpha(query_class: &str) -> f64 {
    match query_class {
        "code" => 0.35,
        "person" => 0.45,
        "recency" => 0.50,
        _ => 0.60,
    }
}

fn rewrite_enabled() -> bool {
    let raw = std::env::var(QUERY_REWRITE_ENV)
        .unwrap_or_default()
        .trim()
        .to_lowercase();
    !matches!(raw.as_str(), "0" | "false" | "no" | "off")
}

/// `retrieval_policy.rewrite_query` → `(rewritten, rules)`.
pub fn rewrite_query(query: &str) -> (String, Vec<String>) {
    let text = query.trim();
    if text.is_empty() {
        return (String::new(), Vec::new());
    }
    let original = text.to_string();
    if !rewrite_enabled() {
        return (original, Vec::new());
    }
    let p = patterns();
    let mut rules: Vec<String> = Vec::new();
    let mut rewritten = p.whitespace.replace_all(text, " ").trim().to_string();
    if rewritten != original {
        rules.push("collapse_whitespace".to_string());
    }
    if classify_query(&original) == "code" {
        // Exact identifiers and filenames ARE the retrieval signal for code
        // questions; whitespace collapse is the only permitted normalization.
        return (rewritten, rules);
    }
    for (name, pattern) in [
        ("strip_filler_ko", &p.ko_filler_tail),
        ("strip_filler_en_leading", &p.en_filler_lead),
        ("strip_filler_en_trailing", &p.en_filler_tail),
    ] {
        let candidate = pattern.replace_all(&rewritten, "").trim().to_string();
        if candidate != rewritten && candidate.chars().count() >= MIN_REMAINDER_CHARS {
            rewritten = candidate;
            rules.push(name.to_string());
        }
    }
    (rewritten, rules)
}

/// `retrieval_policy.resolve_policy` for the default (un-overridden) tables.
pub fn resolve_policy(query: &str) -> Policy {
    let original = query.trim().to_string();
    let (rewritten, rules) = rewrite_query(query);
    let query_class = classify_query(&original).to_string();
    let search_query = if rewritten.is_empty() {
        original.clone()
    } else {
        rewritten
    };
    Policy {
        alpha: class_alpha(&query_class),
        fusion_strategy: "alpha".to_string(),
        recency_half_life_days: if query_class == "recency" {
            Some(RECENCY_HALF_LIFE_DAYS)
        } else {
            None
        },
        query_class,
        original_query: original,
        search_query,
        rewrite_rules: rules,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn precedence_is_code_then_recency_then_person() {
        assert_eq!(classify_query(""), "fact");
        assert_eq!(classify_query("   "), "fact");
        assert_eq!(classify_query("hybrid retrieval ranking"), "fact");
        assert_eq!(classify_query("vector_search() returns"), "code");
        assert_eq!(classify_query("빌드 실패 원인"), "code");
        assert_eq!(classify_query("`inline code`"), "code");
        assert_eq!(classify_query("see main.py"), "code");
        assert_eq!(classify_query("camelCase name"), "code");
        assert_eq!(classify_query("recent decisions last week"), "recency");
        assert_eq!(classify_query("지난주 회의 기록"), "recency");
        assert_eq!(
            classify_query("who owns the onboarding checklist"),
            "person"
        );
        assert_eq!(classify_query("담당자 누구"), "person");
        assert_eq!(classify_query("김지원 님이"), "person");
        assert_eq!(classify_query("a@b.co"), "person");
        // A code signal beats a recency word, and recency beats a person word.
        assert_eq!(classify_query("어제 버그"), "code");
        assert_eq!(classify_query("어제 누구"), "recency");
    }

    #[test]
    fn alpha_comes_from_the_class_table() {
        assert_eq!(class_alpha("fact"), 0.60);
        assert_eq!(class_alpha("code"), 0.35);
        assert_eq!(class_alpha("person"), 0.45);
        assert_eq!(class_alpha("recency"), 0.50);
        assert_eq!(class_alpha("nonsense"), 0.60);
    }

    #[test]
    fn rewrite_applies_the_three_conservative_rules() {
        let (text, rules) = rewrite_query("  what is   the retrieval specification please  ");
        assert_eq!(text, "the retrieval specification");
        assert_eq!(
            rules,
            vec![
                "collapse_whitespace",
                "strip_filler_en_leading",
                "strip_filler_en_trailing"
            ]
        );
        let (text, rules) = rewrite_query("온보딩 체크리스트 좀 알려줘");
        assert_eq!(text, "온보딩 체크리스트");
        assert_eq!(rules, vec!["strip_filler_ko"]);
        // Code queries only ever get whitespace collapse.
        let (text, rules) = rewrite_query("vector_search()   please");
        assert_eq!(text, "vector_search() please");
        assert_eq!(rules, vec!["collapse_whitespace"]);
        // A strip that would leave under four characters is refused.
        let (text, rules) = rewrite_query("abc 좀 알려줘");
        assert_eq!(text, "abc 좀 알려줘");
        assert!(rules.is_empty());
        assert_eq!(rewrite_query("   "), (String::new(), Vec::new()));
    }

    #[test]
    fn policy_classifies_the_original_and_flags_recency_only() {
        let policy = resolve_policy("  지난주에 회의 뭐였지  ");
        assert_eq!(policy.query_class, "recency");
        assert_eq!(policy.recency_half_life_days, Some(14.0));
        assert_eq!(policy.alpha, 0.5);
        assert_eq!(policy.fusion_strategy, "alpha");
        assert_eq!(policy.original_query, "지난주에 회의 뭐였지");
        assert!(policy.search_query.starts_with("지난주에 회의"));
        assert!(policy
            .rewrite_rules
            .contains(&"strip_filler_ko".to_string()));

        let fact = resolve_policy("hybrid retrieval ranking");
        assert_eq!(fact.recency_half_life_days, None);
        assert_eq!(fact.search_query, "hybrid retrieval ranking");
        assert!(fact.rewrite_rules.is_empty());
        assert_ne!(fact, policy);
        assert!(format!("{fact:?}").contains("query_class"));

        // An all-filler query rewrites to nothing, so the original is searched.
        let empty = resolve_policy("");
        assert_eq!(empty.search_query, "");
        assert_eq!(empty.query_class, "fact");
    }
}
