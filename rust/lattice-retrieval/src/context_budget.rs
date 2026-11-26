pub const CONTEXT_WINDOW_ENV: &str = "LATTICEAI_CONTEXT_WINDOW_TOKENS";
pub const DEFAULT_CONTEXT_WINDOW: usize = 8192;
pub const PROMPT_RESERVE_TOKENS: usize = 1024;
pub const CONTEXT_SHARE_PERCENT: usize = 45;
pub const MIN_CONTEXT_BUDGET: usize = 1000;
pub const MAX_CONTEXT_BUDGET: usize = 24_000;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ContextBudget {
    pub window_tokens: usize,
    pub reply_tokens: usize,
    pub budget_tokens: usize,
}

impl ContextBudget {
    pub fn for_window(window_tokens: usize, reply_tokens: usize) -> Self {
        let window = window_tokens.max(MIN_CONTEXT_BUDGET);
        let reply = reply_tokens.min(window / 2);
        let available = window
            .saturating_sub(reply)
            .saturating_sub(PROMPT_RESERVE_TOKENS);
        let budget =
            (available * CONTEXT_SHARE_PERCENT / 100).clamp(MIN_CONTEXT_BUDGET, MAX_CONTEXT_BUDGET);
        Self {
            window_tokens: window,
            reply_tokens: reply,
            budget_tokens: budget,
        }
    }

    pub fn window_from_lookup(lookup: impl Fn(&str) -> Option<String>) -> usize {
        lookup(CONTEXT_WINDOW_ENV)
            .and_then(|raw| raw.trim().parse::<usize>().ok())
            .filter(|window| *window > 0)
            .unwrap_or(DEFAULT_CONTEXT_WINDOW)
    }

    pub fn from_env(reply_tokens: i64) -> Self {
        Self::for_window(
            Self::window_from_lookup(|key| std::env::var(key).ok()),
            reply_tokens.max(0) as usize,
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_default_window_keeps_roughly_the_old_two_thousand_token_budget() {
        let budget = ContextBudget::for_window(DEFAULT_CONTEXT_WINDOW, 2048);
        assert_eq!(budget.budget_tokens, 2304);
        assert_eq!(budget.reply_tokens, 2048);
    }

    #[test]
    fn larger_windows_scale_up_and_tiny_or_huge_windows_are_clamped() {
        let small = ContextBudget::for_window(4096, 2048).budget_tokens;
        let large = ContextBudget::for_window(32_768, 2048).budget_tokens;
        assert!(large > ContextBudget::for_window(DEFAULT_CONTEXT_WINDOW, 2048).budget_tokens);
        assert_eq!(small, MIN_CONTEXT_BUDGET);
        assert_eq!(
            ContextBudget::for_window(1_000_000, 2048).budget_tokens,
            MAX_CONTEXT_BUDGET
        );
        assert_eq!(
            ContextBudget::for_window(0, 0).budget_tokens,
            MIN_CONTEXT_BUDGET
        );
        assert_eq!(ContextBudget::for_window(8192, 100_000).reply_tokens, 4096);
    }

    #[test]
    fn the_window_comes_from_the_environment_or_the_default() {
        assert_eq!(
            ContextBudget::window_from_lookup(|_| None),
            DEFAULT_CONTEXT_WINDOW
        );
        assert_eq!(
            ContextBudget::window_from_lookup(|_| Some(" 32768 ".into())),
            32768
        );
        assert_eq!(
            ContextBudget::window_from_lookup(|_| Some("0".into())),
            DEFAULT_CONTEXT_WINDOW
        );
        assert_eq!(
            ContextBudget::window_from_lookup(|_| Some("lots".into())),
            DEFAULT_CONTEXT_WINDOW
        );
        assert!(ContextBudget::from_env(-5).budget_tokens >= MIN_CONTEXT_BUDGET);
    }
}
