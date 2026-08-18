//! The loop's state vocabulary and the object one run carries between phases.
//!
//! Ports of `latticeai.core.agent_state` and `latticeai.core.agent.context`.
//! The loop itself is stateless: a run is exactly what its [`AgentRunContext`]
//! says it is, which is also what makes the pause/resume store possible —
//! `serialize` / `restore` here are the field contract
//! `latticeai.core.run_store` persists, key for key.

use serde_json::{json, Map, Value};

use crate::kernel::trace::LoopTrace;

/// Every state the single-agent loop can be in.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum AgentState {
    Idle,
    Planning,
    WaitingApproval,
    Executing,
    Verifying,
    Failed,
    Rollback,
    /// Terminal, non-success: the run ended but completion could not be
    /// verified. Never presented as success.
    NeedsReview,
    Done,
}

/// Declaration order, which is what `[state.value for state in AgentState]` is.
pub const ALL_STATES: [AgentState; 9] = [
    AgentState::Idle,
    AgentState::Planning,
    AgentState::WaitingApproval,
    AgentState::Executing,
    AgentState::Verifying,
    AgentState::Failed,
    AgentState::Rollback,
    AgentState::NeedsReview,
    AgentState::Done,
];

/// The loop exits when it reaches one of these.
pub const TERMINAL_STATES: [AgentState; 3] = [
    AgentState::Done,
    AgentState::Failed,
    AgentState::NeedsReview,
];

impl AgentState {
    pub fn as_str(self) -> &'static str {
        match self {
            AgentState::Idle => "IDLE",
            AgentState::Planning => "PLANNING",
            AgentState::WaitingApproval => "WAITING_APPROVAL",
            AgentState::Executing => "EXECUTING",
            AgentState::Verifying => "VERIFYING",
            AgentState::Failed => "FAILED",
            AgentState::Rollback => "ROLLBACK",
            AgentState::NeedsReview => "NEEDS_REVIEW",
            AgentState::Done => "DONE",
        }
    }

    /// `AgentState(value)` — `None` where Python raises `ValueError`.
    pub fn parse(value: &str) -> Option<Self> {
        ALL_STATES.into_iter().find(|state| state.as_str() == value)
    }

    pub fn is_terminal(self) -> bool {
        TERMINAL_STATES.contains(&self)
    }
}

/// Mutable state carrier passed through all agent phases.
#[derive(Debug, Clone)]
pub struct AgentRunContext {
    pub state: AgentState,
    pub trace: LoopTrace,
    pub plan: Map<String, Value>,
    pub transcript: Vec<Value>,
    pub retry_count: u32,
    pub state_history: Vec<String>,
    pub corrections: Vec<Value>,
    pub final_message: String,
    pub rollback_log: Vec<Value>,
    pub executing_model: Option<String>,
    pub reviewing_model: Option<String>,
    pub approved_by_human: bool,
    /// Autonomy dial stamped for this run; `None` falls back to the resolver.
    pub permission_mode: Option<String>,
    /// Project-session prompt block, `""` for a standalone run.
    pub project_context: String,
    /// Resolved once per run; `None` means "not resolved yet".
    pub self_model_summary: Option<String>,
}

impl Default for AgentRunContext {
    fn default() -> Self {
        Self {
            state: AgentState::Idle,
            trace: LoopTrace::default(),
            plan: Map::new(),
            transcript: Vec::new(),
            retry_count: 0,
            state_history: Vec::new(),
            corrections: Vec::new(),
            final_message: String::new(),
            rollback_log: Vec::new(),
            executing_model: None,
            reviewing_model: None,
            approved_by_human: false,
            permission_mode: None,
            project_context: String::new(),
            self_model_summary: None,
        }
    }
}

impl AgentRunContext {
    pub fn new() -> Self {
        Self::default()
    }

    /// The plan's `goal`, or `""`.
    pub fn goal(&self) -> String {
        self.plan
            .get("goal")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string()
    }

    /// The plan's `steps` array.
    pub fn steps(&self) -> Vec<Value> {
        self.plan
            .get("steps")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default()
    }

    /// `serialize_run_context` — plain JSON-safe data, key for key.
    pub fn serialize(&self) -> Value {
        json!({
            "state": self.state.as_str(),
            "plan": Value::Object(self.plan.clone()),
            "transcript": self.transcript,
            "retry_count": self.retry_count,
            "state_history": self.state_history,
            "corrections": self.corrections,
            "final_message": self.final_message,
            "rollback_log": self.rollback_log,
            "executing_model": self.executing_model,
            "reviewing_model": self.reviewing_model,
            "approved_by_human": self.approved_by_human,
            // A paused run resumes under the dial it was planned with.
            "permission_mode": self.permission_mode,
            "trace": {"events": self.trace.events, "truncated": self.trace.truncated},
        })
    }

    /// `restore_run_context`. An unknown state is `WAITING_APPROVAL`, never a
    /// guess at something further along.
    pub fn restore(payload: &Value) -> Self {
        let text = |key: &str| payload.get(key).and_then(Value::as_str);
        let list = |key: &str| {
            payload
                .get(key)
                .and_then(Value::as_array)
                .cloned()
                .unwrap_or_default()
        };
        let mut trace = LoopTrace::default();
        trace.events = payload
            .get("trace")
            .and_then(|trace| trace.get("events"))
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        trace.truncated = payload
            .get("trace")
            .and_then(|trace| trace.get("truncated"))
            .and_then(Value::as_u64)
            .unwrap_or(0);
        Self {
            state: text("state")
                .and_then(AgentState::parse)
                .unwrap_or(AgentState::WaitingApproval),
            trace,
            plan: payload
                .get("plan")
                .and_then(Value::as_object)
                .cloned()
                .unwrap_or_default(),
            transcript: list("transcript"),
            retry_count: payload
                .get("retry_count")
                .and_then(Value::as_u64)
                .unwrap_or(0) as u32,
            state_history: list("state_history")
                .iter()
                .map(crate::parse::pystr::py_str)
                .collect(),
            corrections: list("corrections"),
            final_message: text("final_message").unwrap_or_default().to_string(),
            rollback_log: list("rollback_log"),
            executing_model: text("executing_model").map(String::from),
            reviewing_model: text("reviewing_model").map(String::from),
            approved_by_human: payload
                .get("approved_by_human")
                .is_some_and(crate::parse::pystr::is_truthy),
            permission_mode: text("permission_mode")
                .filter(|mode| !mode.is_empty())
                .map(String::from),
            project_context: String::new(),
            self_model_summary: None,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_state_names_are_the_python_enum_values() {
        assert_eq!(
            ALL_STATES.map(AgentState::as_str).to_vec(),
            vec![
                "IDLE",
                "PLANNING",
                "WAITING_APPROVAL",
                "EXECUTING",
                "VERIFYING",
                "FAILED",
                "ROLLBACK",
                "NEEDS_REVIEW",
                "DONE"
            ]
        );
    }

    #[test]
    fn exactly_three_states_are_terminal() {
        for state in ALL_STATES {
            assert_eq!(
                state.is_terminal(),
                matches!(
                    state,
                    AgentState::Done | AgentState::Failed | AgentState::NeedsReview
                ),
                "{}",
                state.as_str()
            );
        }
        assert!(!AgentState::Rollback.is_terminal(), "rollback continues");
    }

    #[test]
    fn parsing_is_exact_and_never_guesses() {
        assert_eq!(AgentState::parse("DONE"), Some(AgentState::Done));
        assert_eq!(AgentState::parse("done"), None);
        assert_eq!(AgentState::parse("COMPLETE"), None);
    }

    #[test]
    fn a_context_round_trips_through_the_store_contract() {
        let mut ctx = AgentRunContext::new();
        ctx.state = AgentState::WaitingApproval;
        ctx.plan = json!({"goal": "g", "steps": [{"action": "write_file"}]})
            .as_object()
            .expect("plan")
            .clone();
        ctx.transcript = vec![json!({"state": "PLANNING"})];
        ctx.retry_count = 2;
        ctx.state_history = vec!["PLANNING".into()];
        ctx.corrections = vec![json!("reply with JSON")];
        ctx.final_message = "paused".into();
        ctx.rollback_log = vec![json!({"path": "a.md", "existed": false})];
        ctx.executing_model = Some("m-exec".into());
        ctx.reviewing_model = Some("m-review".into());
        ctx.approved_by_human = true;
        ctx.permission_mode = Some("trusted".into());
        ctx.trace = LoopTrace::pinned("t");
        ctx.trace.llm_call("plan", Some("m-exec"));

        let restored = AgentRunContext::restore(&ctx.serialize());
        assert_eq!(restored.serialize(), ctx.serialize());
        assert_eq!(restored.goal(), "g");
        assert_eq!(restored.steps().len(), 1);
        assert_eq!(restored.trace.events.len(), 1);
    }

    #[test]
    fn an_unknown_or_missing_state_restores_as_waiting_approval() {
        for payload in [
            json!({}),
            json!({"state": "SOMETHING_NEW"}),
            json!({"state": null}),
        ] {
            assert_eq!(
                AgentRunContext::restore(&payload).state,
                AgentState::WaitingApproval,
                "{payload}"
            );
        }
    }

    #[test]
    fn a_blank_permission_mode_restores_as_absent() {
        let ctx = AgentRunContext::restore(&json!({"permission_mode": ""}));
        assert_eq!(ctx.permission_mode, None, "falsy stays unstamped");
        let ctx = AgentRunContext::restore(&json!({"permission_mode": "strict"}));
        assert_eq!(ctx.permission_mode.as_deref(), Some("strict"));
    }
}
