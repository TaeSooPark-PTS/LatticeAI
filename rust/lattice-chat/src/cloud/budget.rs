use std::collections::HashMap;
use std::sync::{Mutex, OnceLock};

use serde_json::{json, Value};

fn env_int(name: &str, default: i64) -> i64 {
    match std::env::var(name) {
        Ok(raw) => raw
            .trim()
            .parse::<i64>()
            .map(|value| value.max(0))
            .unwrap_or(default),
        Err(_) => default,
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TokenBudget {
    pub max_tokens_per_turn: i64,
    pub max_tokens_per_session: i64,
    pub session_used: i64,
}

impl Default for TokenBudget {
    fn default() -> Self {
        Self {
            max_tokens_per_turn: env_int("LATTICEAI_CLOUD_MAX_TOKENS_PER_TURN", 2500),
            max_tokens_per_session: env_int("LATTICEAI_CLOUD_MAX_TOKENS_PER_SESSION", 50_000),
            session_used: 0,
        }
    }
}

impl TokenBudget {
    pub fn check_turn(&self, estimated_input_tokens: i64) -> Option<String> {
        if estimated_input_tokens > self.max_tokens_per_turn {
            return Some(format!(
                "estimated input tokens {estimated_input_tokens} exceed per-turn limit {}",
                self.max_tokens_per_turn
            ));
        }
        if self.session_used + estimated_input_tokens > self.max_tokens_per_session {
            return Some(format!(
                "session budget would exceed {} (used={}, turn={estimated_input_tokens})",
                self.max_tokens_per_session, self.session_used
            ));
        }
        None
    }

    pub fn record(&mut self, tokens: i64) {
        self.session_used += tokens.max(0);
    }

    pub fn snapshot(&self) -> Value {
        json!({
            "max_tokens_per_turn": self.max_tokens_per_turn,
            "max_tokens_per_session": self.max_tokens_per_session,
            "session_used": self.session_used,
            "session_remaining": (self.max_tokens_per_session - self.session_used).max(0),
        })
    }
}

pub const MAX_BUDGET_SCOPES: usize = 4_096;

#[derive(Debug, Default)]
struct BudgetStore {
    entries: HashMap<String, (u64, TokenBudget)>,
    clock: u64,
}

impl BudgetStore {
    fn touch_and_get_mut(&mut self, key: &str) -> &mut TokenBudget {
        self.clock = self.clock.saturating_add(1);
        let tick = self.clock;
        if !self.entries.contains_key(key) && self.entries.len() >= MAX_BUDGET_SCOPES {
            if let Some(oldest_key) = self
                .entries
                .iter()
                .min_by_key(|(_, (last_tick, _))| *last_tick)
                .map(|(k, _)| k.clone())
            {
                self.entries.remove(&oldest_key);
            }
        }
        let entry = self
            .entries
            .entry(key.to_string())
            .or_insert_with(|| (tick, TokenBudget::default()));
        entry.0 = tick;
        &mut entry.1
    }
}

fn budgets() -> &'static Mutex<BudgetStore> {
    static BUDGETS: OnceLock<Mutex<BudgetStore>> = OnceLock::new();
    BUDGETS.get_or_init(|| Mutex::new(BudgetStore::default()))
}

pub fn scope_key(user_email: Option<&str>, workspace_id: Option<&str>) -> String {
    format!(
        "{}|{}",
        user_email
            .filter(|value| !value.is_empty())
            .unwrap_or("anon"),
        workspace_id
            .filter(|value| !value.is_empty())
            .unwrap_or("global"),
    )
}

pub fn budget_for(key: &str) -> TokenBudget {
    let key = if key.is_empty() { "global" } else { key };
    let mut guard = budgets()
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    guard.touch_and_get_mut(key).clone()
}

pub fn record_budget(key: &str, tokens: i64) -> TokenBudget {
    let key = if key.is_empty() { "global" } else { key };
    let mut guard = budgets()
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let budget = guard.touch_and_get_mut(key);
    budget.record(tokens);
    budget.clone()
}

pub fn reset_budget(key: &str) {
    let key = if key.is_empty() { "global" } else { key };
    budgets()
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
        .entries
        .remove(key);
}
