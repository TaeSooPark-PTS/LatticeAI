use serde_json::{Map, Value};

use crate::kernel::breaker::is_circuit_breaker;
use crate::kernel::mode::{effective_auto_approve, PermissionMode};
use crate::kernel::policy::{PolicyTable, ToolPolicy};

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
        return Some(format!(
            "BLOCKED: destructive action '{name}' not permitted in agent mode."
        ));
    }
    if approved_by_human || governor_allows_additive {
        return None;
    }
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

#[derive(Debug, Clone)]
pub struct PlanStep {
    pub action: String,
}

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

    fn workspace_write() -> ToolPolicy {
        ToolPolicy {
            risk: "write".into(),
            ..ToolPolicy::default()
        }
    }

    fn shell_exec() -> ToolPolicy {
        ToolPolicy {
            risk: "exec".into(),
            shell: true,
            ..ToolPolicy::default()
        }
    }

    fn destructive() -> ToolPolicy {
        ToolPolicy {
            risk: "destructive".into(),
            destructive: true,
            ..ToolPolicy::default()
        }
    }

    fn verdict(
        mode: PermissionMode,
        tool: &str,
        policy: &ToolPolicy,
        call: &Map<String, Value>,
    ) -> (bool, Option<String>) {
        (
            effective_auto_approve(mode, tool, policy, None),
            block_reason_for_tool(mode, tool, policy, call, false, false),
        )
    }

    const PATH_BREAKER: &str = "BLOCKED: circuit breaker: refusing path '/'";
    const SHELL_BREAKER: &str = "BLOCKED: circuit breaker: refusing destructive shell command";
    const ALWAYS_BLOCKED: &str = "BLOCKED: destructive action is always blocked";

    #[test]
    fn trusted_reproduces_every_verdict_class_of_the_retired_grid() {
        let mode = PermissionMode::Trusted;
        let write = workspace_write();
        let exec = shell_exec();
        let none = Map::new();
        let root = args(json!({"path": "/"}));
        let rm_rf = args(json!({"command": "rm -rf /"}));
        let rm_rf_home = args(json!({"cmd": "RM -RF $HOME"}));

        assert_eq!(verdict(mode, "write_file", &write, &none), (true, None));
        assert_eq!(
            verdict(mode, "knowledge_search", &exec, &none),
            (true, None)
        );
        assert_eq!(
            verdict(mode, "write_file", &write, &root),
            (true, Some(PATH_BREAKER.into()))
        );
        assert_eq!(
            verdict(mode, "run_command", &exec, &none),
            (
                false,
                Some(
                    "BLOCKED: action 'run_command' requires explicit approval (mode=trusted)."
                        .into()
                )
            )
        );
        assert_eq!(
            verdict(mode, "run_command", &exec, &root),
            (false, Some(PATH_BREAKER.into()))
        );
        assert_eq!(
            verdict(mode, "write_file", &write, &rm_rf),
            (true, Some(SHELL_BREAKER.into()))
        );
        assert_eq!(
            verdict(mode, "run_command", &exec, &rm_rf_home),
            (false, Some(SHELL_BREAKER.into()))
        );
        assert_eq!(
            verdict(mode, "write_file", &destructive(), &none),
            (false, Some(ALWAYS_BLOCKED.into()))
        );
        for required in [true, false] {
            assert!(!crate::kernel::mode::should_stage_proposal(mode, required));
        }
    }

    #[test]
    fn bypass_reproduces_every_verdict_class_of_the_retired_grid() {
        let mode = PermissionMode::Bypass;
        let exec = shell_exec();
        let none = Map::new();
        let root = args(json!({"path": "/"}));
        let rm_rf = args(json!({"command": "rm -rf /"}));

        assert_eq!(verdict(mode, "run_command", &exec, &none), (true, None));
        assert_eq!(
            verdict(mode, "run_command", &exec, &root),
            (true, Some(PATH_BREAKER.into()))
        );
        assert_eq!(
            verdict(mode, "run_command", &exec, &rm_rf),
            (true, Some(SHELL_BREAKER.into()))
        );
        assert_eq!(
            verdict(mode, "delete_file", &destructive(), &none),
            (false, Some(ALWAYS_BLOCKED.into()))
        );
        for class in [
            None,
            Some("read"),
            Some("additive"),
            Some("mutation"),
            Some("destructive"),
            Some("exec"),
        ] {
            assert!(
                effective_auto_approve(mode, "run_command", &exec, class),
                "{class:?}"
            );
        }
        assert!(!crate::kernel::mode::plan_requires_approval(
            mode,
            &["run_command".to_string()],
            true
        ));
    }
}
