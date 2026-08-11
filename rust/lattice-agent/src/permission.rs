//! Agent-loop gates — a 1:1 port of `latticeai.core.agent_permission`.
//!
//! Two entry points, both ordered: [`block_reason_for_tool`] is a priority
//! chain where the earlier rule wins, and [`non_auto_plan_steps`] decides which
//! steps of a plan still need a human before the plan starts.

use serde_json::{Map, Value};

use crate::breaker::is_circuit_breaker;
use crate::mode::{effective_auto_approve, PermissionMode};
use crate::policy::{PolicyTable, ToolPolicy};

/// Why this call must not run, or `None` when it may proceed.
///
/// The order is the policy. A circuit breaker outranks everything, including an
/// explicit human approval — that is what makes it a breaker. Then destructive
/// policies. Only then do consent and mode autonomy get a say.
pub fn block_reason_for_tool(
    mode: PermissionMode,
    name: &str,
    policy: &ToolPolicy,
    args: &Map<String, Value>,
    approved_by_human: bool,
    governor_allows_additive: bool,
) -> Option<String> {
    if let Some(breaker) = is_circuit_breaker(name, policy, args) {
        return Some(format!("BLOCKED: {breaker}"));
    }
    if policy.is_destructive() {
        // Unreachable while the breaker above catches every destructive policy;
        // kept because Python keeps it, and because a future breaker change
        // must not silently open this door.
        return Some(format!(
            "BLOCKED: destructive action '{name}' not permitted in agent mode."
        ));
    }
    if approved_by_human || governor_allows_additive {
        return None;
    }
    // Python passes `args` here; `effective_auto_approve` never reads it.
    if effective_auto_approve(mode, name, policy, None) {
        return None;
    }
    if policy.auto_approve {
        return None;
    }
    Some(format!(
        "BLOCKED: action '{name}' requires explicit approval (mode={}).",
        mode.as_str()
    ))
}

/// One step of a proposed plan.
#[derive(Debug, Clone)]
pub struct PlanStep {
    pub action: String,
}

/// The plan steps that still need approval under the active mode.
///
/// Under `strict`, governor-managed tools are skipped here on purpose: they are
/// decided per call, when the change class is known, and blocking the whole plan
/// on them would ask the user the same question twice.
pub fn non_auto_plan_steps(
    mode: PermissionMode,
    steps: &[PlanStep],
    governance: &PolicyTable,
    governed_tools: &[String],
) -> Vec<String> {
    let mut non_auto = Vec::new();
    for step in steps {
        let name = step.action.as_str();
        if name.is_empty() {
            continue;
        }
        if mode == PermissionMode::Strict && governed_tools.iter().any(|tool| tool == name) {
            continue;
        }
        let policy = governance.get(name);
        if effective_auto_approve(mode, name, policy, None) {
            continue;
        }
        non_auto.push(name.to_string());
    }
    non_auto
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn args(value: Value) -> Map<String, Value> {
        value.as_object().expect("object").clone()
    }

    fn steps(actions: &[&str]) -> Vec<PlanStep> {
        actions
            .iter()
            .map(|action| PlanStep {
                action: (*action).to_string(),
            })
            .collect()
    }

    fn table() -> PolicyTable {
        let mut table = PolicyTable::default();
        table
            .tools
            .insert("read_file".into(), ToolPolicy::read_only());
        table.tools.insert(
            "write_file".into(),
            ToolPolicy {
                risk: "write".into(),
                ..ToolPolicy::default()
            },
        );
        table.tools.insert(
            "run_command".into(),
            ToolPolicy {
                risk: "exec".into(),
                shell: true,
                ..ToolPolicy::default()
            },
        );
        table
    }

    #[test]
    fn a_breaker_outranks_an_explicit_human_approval() {
        let call = args(json!({"path": "/"}));
        let reason = block_reason_for_tool(
            PermissionMode::Bypass,
            "write_file",
            &ToolPolicy::default(),
            &call,
            true,
            true,
        );
        assert_eq!(
            reason.as_deref(),
            Some("BLOCKED: circuit breaker: refusing path '/'")
        );
    }

    #[test]
    fn a_destructive_policy_is_blocked_by_the_breaker_message() {
        let policy = ToolPolicy {
            risk: "destructive".into(),
            destructive: true,
            ..ToolPolicy::default()
        };
        let reason = block_reason_for_tool(
            PermissionMode::Bypass,
            "delete_file",
            &policy,
            &Map::new(),
            true,
            false,
        );
        assert_eq!(
            reason.as_deref(),
            Some("BLOCKED: destructive action is always blocked")
        );
    }

    #[test]
    fn human_approval_unblocks_an_otherwise_gated_call() {
        let policy = ToolPolicy {
            risk: "exec".into(),
            ..ToolPolicy::default()
        };
        assert!(block_reason_for_tool(
            PermissionMode::Strict,
            "run_command",
            &policy,
            &Map::new(),
            true,
            false
        )
        .is_none());
        assert!(block_reason_for_tool(
            PermissionMode::Strict,
            "run_command",
            &policy,
            &Map::new(),
            false,
            true
        )
        .is_none());
    }

    #[test]
    fn the_block_message_names_the_tool_and_the_mode() {
        let policy = ToolPolicy {
            risk: "exec".into(),
            ..ToolPolicy::default()
        };
        let reason = block_reason_for_tool(
            PermissionMode::Trusted,
            "run_command",
            &policy,
            &Map::new(),
            false,
            false,
        );
        assert_eq!(
            reason.as_deref(),
            Some("BLOCKED: action 'run_command' requires explicit approval (mode=trusted).")
        );
    }

    #[test]
    fn an_auto_approve_policy_is_never_blocked() {
        assert!(block_reason_for_tool(
            PermissionMode::Strict,
            "read_file",
            &ToolPolicy::read_only(),
            &Map::new(),
            false,
            false
        )
        .is_none());
    }

    #[test]
    fn strict_leaves_governed_tools_to_the_per_call_gate() {
        let plan = steps(&["write_file", "run_command"]);
        let governed = vec!["write_file".to_string()];
        assert_eq!(
            non_auto_plan_steps(PermissionMode::Strict, &plan, &table(), &governed),
            vec!["run_command".to_string()]
        );
        // Trusted does not skip them — but it auto-approves the write anyway.
        assert_eq!(
            non_auto_plan_steps(PermissionMode::Trusted, &plan, &table(), &governed),
            vec!["run_command".to_string()]
        );
    }

    #[test]
    fn a_step_without_an_action_is_not_a_step() {
        let plan = steps(&["", "read_file", "write_file"]);
        assert_eq!(
            non_auto_plan_steps(PermissionMode::Strict, &plan, &table(), &[]),
            vec!["write_file".to_string()]
        );
    }

    #[test]
    fn an_unknown_tool_falls_back_to_the_gated_default() {
        let plan = steps(&["not_a_tool"]);
        assert_eq!(
            non_auto_plan_steps(PermissionMode::Trusted, &plan, &table(), &[]),
            vec!["not_a_tool".to_string()]
        );
        assert!(non_auto_plan_steps(PermissionMode::Bypass, &plan, &table(), &[]).is_empty());
    }
}
