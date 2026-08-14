use std::collections::HashMap;
use std::sync::{Mutex, OnceLock};

use serde_json::{json, Value};

// ── token guard ─────────────────────────────────────────────────────────────

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

/// `TokenBudget` — soft product guardrails, not a billing meter.
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
    /// `check_turn` — the refusal reason, or `None` when the turn may run.
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

    /// `record` — negative and unusable values count as zero.
    pub fn record(&mut self, tokens: i64) {
        self.session_used += tokens.max(0);
    }

    /// `snapshot`.
    pub fn snapshot(&self) -> Value {
        json!({
            "max_tokens_per_turn": self.max_tokens_per_turn,
            "max_tokens_per_session": self.max_tokens_per_session,
            "session_used": self.session_used,
            "session_remaining": (self.max_tokens_per_session - self.session_used).max(0),
        })
    }
}

fn budgets() -> &'static Mutex<HashMap<String, TokenBudget>> {
    static BUDGETS: OnceLock<Mutex<HashMap<String, TokenBudget>>> = OnceLock::new();
    BUDGETS.get_or_init(|| Mutex::new(HashMap::new()))
}

/// `_scope_key(user_email, workspace_id)`.
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

/// `budget_for(scope_key)` — a copy of the process-local budget.
pub fn budget_for(key: &str) -> TokenBudget {
    let key = if key.is_empty() { "global" } else { key };
    budgets()
        .lock()
        .expect("cloud token budget lock")
        .entry(key.to_string())
        .or_default()
        .clone()
}

/// Charge the process-local budget and hand back the new snapshot.
pub fn record_budget(key: &str, tokens: i64) -> TokenBudget {
    let key = if key.is_empty() { "global" } else { key };
    let mut guard = budgets().lock().expect("cloud token budget lock");
    let budget = guard.entry(key.to_string()).or_default();
    budget.record(tokens);
    budget.clone()
}

/// `reset_budget(scope_key)`.
pub fn reset_budget(key: &str) {
    let key = if key.is_empty() { "global" } else { key };
    budgets()
        .lock()
        .expect("cloud token budget lock")
        .remove(key);
}
