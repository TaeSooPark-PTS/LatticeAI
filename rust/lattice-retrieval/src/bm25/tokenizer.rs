//! CJK bigram, Korean particle stripping, identifier subword, and markdown
//! wikilink/tag/heading extraction for BM25F indexing and GraphRAG expansion.

const KO_PARTICLES: [&str; 28] = [
    "에서", "으로", "에게", "부터", "까지", "처럼", "대한", "관한", "하는", "하고", "하며", "이고",
    "이나", "이라", "에는", "에도", "은", "는", "이", "가", "을", "를", "의", "에", "로", "와",
    "과", "도",
];

const EN_STOPWORDS: [&str; 25] = [
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "is", "it", "of", "on",
    "or", "that", "the", "this", "to", "was", "were", "what", "how", "with",
];

const KO_STOPWORDS: [&str; 12] = [
    "있는",
    "없는",
    "하는",
    "되는",
    "그리고",
    "그러나",
    "따라서",
    "경우",
    "통해",
    "위해",
    "대한",
    "관한",
];

/// A normalized query term with its specificity boost and kind.
#[derive(Debug, Clone, PartialEq)]
pub struct QueryTerm {
    pub token: String,
    pub boost: f64,
    pub is_subword_or_bigram: bool,
}

pub(crate) fn is_hangul(c: char) -> bool {
    ('\u{AC00}'..='\u{D7A3}').contains(&c)
}

pub(crate) fn is_cjk(c: char) -> bool {
    ('\u{4E00}'..='\u{9FFF}').contains(&c) || ('\u{3040}'..='\u{30FF}').contains(&c)
}

fn strip_ko_particle(token: &str) -> &str {
    let char_len = token.chars().count();
    for suffix in KO_PARTICLES {
        if let Some(stripped) = token.strip_suffix(suffix) {
            let s_len = suffix.chars().count();
            if char_len >= s_len + 2 {
                return stripped;
            }
        }
    }
    if let Some(stripped) = token.strip_suffix("만") {
        if char_len >= 3 {
            return stripped;
        }
    }
    token
}

/// Split camelCase or PascalCase ASCII identifier into lowercase words before lowercasing.
fn split_camel_case(text: &str) -> Vec<String> {
    let mut parts = Vec::new();
    let mut current = String::new();
    let chars: Vec<char> = text.chars().collect();
    for i in 0..chars.len() {
        let c = chars[i];
        if c.is_ascii_alphanumeric() {
            if c.is_ascii_uppercase()
                && !current.is_empty()
                && (chars[i - 1].is_ascii_lowercase()
                    || (i + 1 < chars.len() && chars[i + 1].is_ascii_lowercase()))
            {
                if current.len() >= 2 {
                    parts.push(current.to_ascii_lowercase());
                }
                current = String::new();
            }
            current.push(c);
        } else if !current.is_empty() {
            if current.len() >= 2 {
                parts.push(current.to_ascii_lowercase());
            }
            current = String::new();
        }
    }
    if current.len() >= 2 {
        parts.push(current.to_ascii_lowercase());
    }
    parts
}

/// Tokenize text for BM25F indexing and querying, producing primary word/stem tokens
/// AND CJK character bigrams + compound subwords so Korean and mixed queries match
/// reliably across spacing and morphological variations.
pub fn tokenize_bm25_cjk(text: &str) -> Vec<String> {
    extract_query_terms(text)
        .into_iter()
        .map(|qt| qt.token)
        .collect()
}

/// Extract weighted query/document terms (primary words/stems + CJK character bigrams + subwords).
pub fn extract_query_terms(text: &str) -> Vec<QueryTerm> {
    if text.trim().is_empty() {
        return Vec::new();
    }
    let camel_parts = split_camel_case(text);
    let lowered = text.to_lowercase();
    let chars: Vec<char> = lowered.chars().collect();
    let mut terms: Vec<QueryTerm> = Vec::new();

    let mut i = 0;
    while i < chars.len() {
        let c = chars[i];
        if c.is_ascii_alphanumeric() {
            let start = i;
            i += 1;
            while i < chars.len()
                && (chars[i].is_ascii_alphanumeric()
                    || matches!(chars[i], '_' | '-' | '.' | '/' | ':'))
            {
                i += 1;
            }
            let raw: String = chars[start..i]
                .iter()
                .collect::<String>()
                .trim_matches(|ch: char| !ch.is_ascii_alphanumeric())
                .to_string();
            if raw.is_empty() {
                continue;
            }
            let subparts: Vec<&str> = raw
                .split(['_', '-', '.', '/', ':'])
                .filter(|s| !s.is_empty())
                .collect();
            if subparts.len() > 1 {
                terms.push(QueryTerm {
                    token: raw.clone(),
                    boost: 1.15,
                    is_subword_or_bigram: false,
                });
            }
            for part in subparts {
                if part.len() < 2 || EN_STOPWORDS.contains(&part) {
                    continue;
                }
                terms.push(QueryTerm {
                    token: part.to_string(),
                    boost: 1.0,
                    is_subword_or_bigram: false,
                });
                if part.len() >= 5 && part.chars().any(|ch| ch.is_ascii_alphabetic()) {
                    let pchars: Vec<char> = part.chars().collect();
                    // Add 4-gram stem prefixes/subwords for English morphological matching
                    for j in 0..pchars.len().saturating_sub(3) {
                        let sub: String = pchars[j..j + 4].iter().collect();
                        terms.push(QueryTerm {
                            token: format!("sub:{sub}"),
                            boost: 0.45,
                            is_subword_or_bigram: true,
                        });
                    }
                }
            }
        } else if is_hangul(c) {
            let start = i;
            i += 1;
            while i < chars.len() && is_hangul(chars[i]) {
                i += 1;
            }
            let raw: String = chars[start..i].iter().collect();
            let raw_chars = i - start;
            if raw_chars < 2 || KO_STOPWORDS.contains(&raw.as_str()) {
                continue;
            }
            let stem = strip_ko_particle(&raw);
            terms.push(QueryTerm {
                token: stem.to_string(),
                boost: 1.10,
                is_subword_or_bigram: false,
            });
            if stem != raw {
                terms.push(QueryTerm {
                    token: raw.clone(),
                    boost: 0.85,
                    is_subword_or_bigram: false,
                });
            }
            let stem_chars: Vec<char> = stem.chars().collect();
            if stem_chars.len() >= 2 {
                for j in 0..stem_chars.len() - 1 {
                    let bg: String = stem_chars[j..j + 2].iter().collect();
                    terms.push(QueryTerm {
                        token: format!("ko_bi:{bg}"),
                        boost: 0.65,
                        is_subword_or_bigram: true,
                    });
                }
            }
        } else if is_cjk(c) {
            let start = i;
            i += 1;
            while i < chars.len() && is_cjk(chars[i]) {
                i += 1;
            }
            let raw: String = chars[start..i].iter().collect();
            let cjk_chars: Vec<char> = raw.chars().collect();
            if !raw.is_empty() {
                terms.push(QueryTerm {
                    token: raw,
                    boost: 1.05,
                    is_subword_or_bigram: false,
                });
            }
            if cjk_chars.len() >= 2 {
                for j in 0..cjk_chars.len() - 1 {
                    let bg: String = cjk_chars[j..j + 2].iter().collect();
                    terms.push(QueryTerm {
                        token: format!("cjk_bi:{bg}"),
                        boost: 0.70,
                        is_subword_or_bigram: true,
                    });
                }
            }
        } else {
            i += 1;
        }
    }

    for cp in camel_parts {
        if cp.len() >= 2
            && !EN_STOPWORDS.contains(&cp.as_str())
            && !terms.iter().any(|t| t.token == cp)
        {
            terms.push(QueryTerm {
                token: cp,
                boost: 0.85,
                is_subword_or_bigram: true,
            });
        }
    }

    terms
}

/// Extract wikilinks `[[target]]` and `#hashtags` from markdown/text.
pub fn extract_wikilinks_and_tags(text: &str) -> (Vec<String>, Vec<String>) {
    let mut wikilinks = Vec::new();
    let mut tags = Vec::new();

    // Extract [[wikilink]] or [[target|alias]]
    let mut rest = text;
    while let Some(open) = rest.find("[[") {
        let after_open = &rest[open + 2..];
        if let Some(close) = after_open.find("]]") {
            let inner = &after_open[..close];
            let target = inner.split('|').next().unwrap_or(inner).trim();
            if !target.is_empty() {
                let norm = target.to_lowercase();
                if !wikilinks.contains(&norm) {
                    wikilinks.push(norm);
                }
            }
            rest = &after_open[close + 2..];
        } else {
            break;
        }
    }

    // Extract #tags
    for line in text.lines() {
        let trimmed = line.trim();
        for token in trimmed.split_whitespace() {
            if let Some(tag) = token.strip_prefix('#') {
                let clean: String = tag
                    .chars()
                    .take_while(|c| {
                        c.is_alphanumeric()
                            || is_hangul(*c)
                            || is_cjk(*c)
                            || matches!(*c, '_' | '-' | '/')
                    })
                    .collect();
                if clean.chars().count() >= 2 {
                    let norm = clean.to_lowercase();
                    if !tags.contains(&norm) {
                        tags.push(norm);
                    }
                }
            }
        }
    }

    (wikilinks, tags)
}

/// Extract markdown headings (`# Heading`, `## Subheading`) from text.
pub(crate) fn extract_markdown_headings(text: &str) -> String {
    let mut headings = Vec::new();
    for line in text.lines() {
        let trimmed = line.trim();
        if trimmed.starts_with('#') {
            let without_hashes = trimmed.trim_start_matches('#').trim();
            if !without_hashes.is_empty() {
                headings.push(without_hashes);
            }
        }
    }
    headings.join(" ")
}
