pub const QUARTERS_PER_TOKEN: u32 = 4;

pub fn char_token_quarters(c: char) -> u32 {
    match c {
        ' ' | '\r' => 0,
        '\t' => 1,
        '\n' => 2,
        _ if c.is_ascii_alphanumeric() => 1,
        _ if c.is_ascii() => 4,
        '\u{AC00}'..='\u{D7A3}'
        | '\u{1100}'..='\u{11FF}'
        | '\u{3130}'..='\u{318F}'
        | '\u{3040}'..='\u{30FF}'
        | '\u{3400}'..='\u{4DBF}'
        | '\u{4E00}'..='\u{9FFF}'
        | '\u{F900}'..='\u{FAFF}'
        | '\u{FF00}'..='\u{FFEF}' => 4,
        _ if c.is_whitespace() => 0,
        _ if c.is_alphanumeric() => 2,
        _ => 4,
    }
}

pub fn quarters_to_tokens(quarters: u64) -> usize {
    quarters.div_ceil(QUARTERS_PER_TOKEN as u64) as usize
}

pub fn estimate_tokens_chars(chars: &[char]) -> usize {
    quarters_to_tokens(chars.iter().map(|c| char_token_quarters(*c) as u64).sum())
}

pub fn estimate_tokens(text: &str) -> usize {
    quarters_to_tokens(text.chars().map(|c| char_token_quarters(c) as u64).sum())
}

pub fn truncate_to_tokens(text: &str, max_tokens: usize) -> String {
    let limit = max_tokens as u64 * QUARTERS_PER_TOKEN as u64;
    let mut used = 0u64;
    let mut out = String::new();
    for c in text.chars() {
        let next = used + char_token_quarters(c) as u64;
        if next > limit {
            break;
        }
        used = next;
        out.push(c);
    }
    out
}

#[derive(Debug, Clone)]
pub struct TokenPrefix {
    sums: Vec<u64>,
}

impl TokenPrefix {
    pub fn new(chars: &[char]) -> Self {
        let mut sums = Vec::with_capacity(chars.len() + 1);
        let mut running = 0u64;
        sums.push(0);
        for c in chars {
            running += char_token_quarters(*c) as u64;
            sums.push(running);
        }
        Self { sums }
    }

    pub fn len(&self) -> usize {
        self.sums.len() - 1
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }

    pub fn quarters(&self, start: usize, end: usize) -> u64 {
        let end = end.min(self.len());
        let start = start.min(end);
        self.sums[end] - self.sums[start]
    }

    pub fn tokens(&self, start: usize, end: usize) -> usize {
        quarters_to_tokens(self.quarters(start, end))
    }

    pub fn advance(&self, start: usize, tokens: usize) -> usize {
        let goal = self.sums[start.min(self.len())] + tokens as u64 * QUARTERS_PER_TOKEN as u64;
        match self.sums.binary_search(&goal) {
            Ok(mut at) => {
                while at > start && at > 0 && self.sums[at - 1] == goal {
                    at -= 1;
                }
                at.max(start)
            }
            Err(at) => at.saturating_sub(1).max(start).min(self.len()),
        }
    }

    pub fn retreat(&self, end: usize, tokens: usize) -> usize {
        let end = end.min(self.len());
        let goal = self.sums[end].saturating_sub(tokens as u64 * QUARTERS_PER_TOKEN as u64);
        match self.sums.binary_search(&goal) {
            Ok(at) => at.min(end),
            Err(at) => at.min(end),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn english_prose_lands_near_four_characters_per_token() {
        let sentence = "The quick brown fox jumps over the lazy dog.";
        assert_eq!(estimate_tokens(sentence), 10);
        assert_eq!(estimate_tokens(""), 0);
        assert_eq!(estimate_tokens("   "), 0);
    }

    #[test]
    fn korean_and_cjk_count_one_token_per_syllable() {
        assert_eq!(estimate_tokens("지난주 회의록"), 6);
        assert_eq!(estimate_tokens("東京タワー"), 5);
        assert!(estimate_tokens("가나다라") > estimate_tokens("abcd"));
    }

    #[test]
    fn code_punctuation_is_denser_than_prose() {
        let code = "fn foo(a: u32) -> Result<(), E> {";
        let prose = "fn foo a u32 Result E";
        assert!(estimate_tokens(code) > estimate_tokens(prose) * 2);
    }

    #[test]
    fn truncation_never_exceeds_the_budget() {
        let text = "가나다 abc def ghi";
        for budget in 0..10 {
            let cut = truncate_to_tokens(text, budget);
            assert!(estimate_tokens(&cut) <= budget, "{budget}: {cut:?}");
            assert!(text.starts_with(&cut));
        }
        assert_eq!(truncate_to_tokens(text, 100), text);
    }

    #[test]
    fn the_prefix_table_answers_span_counts_and_walks_both_ways() {
        let chars: Vec<char> = "abcd 가나 efgh".chars().collect();
        let prefix = TokenPrefix::new(&chars);
        assert_eq!(prefix.len(), chars.len());
        assert!(!prefix.is_empty());
        assert!(TokenPrefix::new(&[]).is_empty());
        assert_eq!(prefix.tokens(0, chars.len()), estimate_tokens_chars(&chars));
        assert_eq!(prefix.tokens(0, 4), 1);
        let forward = prefix.advance(0, 2);
        assert!(prefix.tokens(0, forward) <= 2);
        assert!(forward > 4);
        let back = prefix.retreat(chars.len(), 1);
        assert!(prefix.tokens(back, chars.len()) >= 1);
        assert!(back < chars.len());
        assert_eq!(prefix.advance(chars.len(), 5), chars.len());
        assert_eq!(prefix.retreat(0, 5), 0);
        assert_eq!(prefix.quarters(5, 2), 0);
    }
}
