use serde_json::{json, Value};

use crate::kernel::policy::in_set;
use crate::kernel::policy::ToolPolicy;

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum PermissionMode {
    Strict,
    Trusted,
    Bypass,
}

pub const DEFAULT_MODE: PermissionMode = PermissionMode::Strict;

pub const ALL_MODES: [PermissionMode; 3] = [
    PermissionMode::Strict,
    PermissionMode::Trusted,
    PermissionMode::Bypass,
];

impl PermissionMode {
    pub fn as_str(self) -> &'static str {
        match self {
            PermissionMode::Strict => "strict",
            PermissionMode::Trusted => "trusted",
            PermissionMode::Bypass => "bypass",
        }
    }
}

pub const COMPUTER_OBSERVATION_TOOLS: [&str; 5] = [
    "chrome_status",
    "computer_screenshot",
    "computer_status",
    "computer_use_status",
    "vision_analyze",
];

pub const COMPUTER_CONTROL_TOOLS: [&str; 8] = [
    "computer_click",
    "computer_drag",
    "computer_key",
    "computer_move",
    "computer_open_app",
    "computer_open_url",
    "computer_scroll",
    "computer_type",
];

pub const KNOWLEDGE_READ_TOOLS: [&str; 7] = [
    "knowledge_graph_context",
    "knowledge_graph_graph",
    "knowledge_graph_search",
    "knowledge_search",
    "knowledge_tree",
    "obsidian_search",
    "obsidian_tree",
];

pub const WORKSPACE_WRITE_TOOLS: [&str; 11] = [
    "create_docx",
    "create_pdf",
    "create_pptx",
    "create_web_project",
    "create_xlsx",
    "edit_file",
    "knowledge_graph_ingest",
    "knowledge_save",
    "obsidian_save",
    "todo_write",
    "write_file",
];

pub const HARD_BLOCK_SANDBOXES: [&str; 1] = ["system"];

pub fn normalize_mode(value: &str) -> PermissionMode {
    match value.trim().to_ascii_lowercase().as_str() {
        "strict" | "default" | "manual" => PermissionMode::Strict,
        "trusted" | "acceptedits" | "accept_edits" | "workspace" => PermissionMode::Trusted,
        "bypass"
        | "bypasspermissions"
        | "bypass_permissions"
        | "yolo"
        | "dangerously-skip-permissions" => PermissionMode::Bypass,
        _ => DEFAULT_MODE,
    }
}

pub fn normalize_value(value: &Value) -> PermissionMode {
    match value {
        Value::String(text) => normalize_mode(text),
        _ => DEFAULT_MODE,
    }
}

pub fn effective_auto_approve(
    mode: PermissionMode,
    tool_name: &str,
    policy: &ToolPolicy,
    change_class: Option<&str>,
) -> bool {
    if policy.auto_approve {
        return true;
    }
    let risk = policy.risk();
    let sandbox = policy.sandbox();

    match mode {
        PermissionMode::Strict => false,
        PermissionMode::Trusted => {
            if in_set(&KNOWLEDGE_READ_TOOLS, tool_name)
                || in_set(&COMPUTER_OBSERVATION_TOOLS, tool_name)
            {
                return true;
            }
            if in_set(&WORKSPACE_WRITE_TOOLS, tool_name) && sandbox == "workspace" {
                let by_risk = matches!(risk, "write" | "write_scoped" | "read");
                let by_class = matches!(
                    change_class,
                    None | Some("additive") | Some("mutation") | Some("read")
                );
                if (by_risk || by_class) && risk != "destructive" && !policy.destructive {
                    return true;
                }
            }
            false
        }
        PermissionMode::Bypass => {
            if risk == "destructive" || policy.destructive {
                return false;
            }
            if sandbox == "system"
                && !in_set(&COMPUTER_OBSERVATION_TOOLS, tool_name)
                && !in_set(&COMPUTER_CONTROL_TOOLS, tool_name)
                && matches!(risk, "write" | "exec")
            {
                return false;
            }
            true
        }
    }
}

pub fn should_stage_proposal(mode: PermissionMode, proposal_required: bool) -> bool {
    proposal_required && mode == PermissionMode::Strict
}

pub fn plan_requires_approval(
    mode: PermissionMode,
    non_auto_steps: &[String],
    plan_flag: bool,
) -> bool {
    if mode == PermissionMode::Bypass {
        return false;
    }
    !non_auto_steps.is_empty() || plan_flag
}

fn catalog_entry(mode: PermissionMode) -> (&'static str, &'static str, &'static str, bool) {
    match mode {
        PermissionMode::Strict => ("Strict", "엄격", "low", false),
        PermissionMode::Trusted => ("Trusted", "신뢰", "medium", false),
        PermissionMode::Bypass => ("Bypass", "바이패스", "high", true),
    }
}

pub fn mode_contract(mode: PermissionMode) -> Value {
    let (label, label_ko, risk, requires_ack) = catalog_entry(mode);
    let trusted_or_bypass = matches!(mode, PermissionMode::Trusted | PermissionMode::Bypass);
    json!({
        "mode": mode.as_str(),
        "label": label,
        "label_ko": label_ko,
        "risk": risk,
        "requires_ack": requires_ack,
        "proposal_first": mode == PermissionMode::Strict,
        "workspace_writes_auto": trusted_or_bypass,
        "knowledge_reads_auto": trusted_or_bypass,
        "exec_auto": mode == PermissionMode::Bypass,
        "computer_observation_auto": trusted_or_bypass,
        "computer_control_auto": mode == PermissionMode::Bypass,
        "circuit_breakers": true,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn every_tool_set_is_sorted_so_binary_search_is_valid() {
        for set in [
            &COMPUTER_OBSERVATION_TOOLS[..],
            &COMPUTER_CONTROL_TOOLS[..],
            &KNOWLEDGE_READ_TOOLS[..],
            &WORKSPACE_WRITE_TOOLS[..],
            &HARD_BLOCK_SANDBOXES[..],
        ] {
            let mut sorted = set.to_vec();
            sorted.sort_unstable();
            assert_eq!(set, &sorted[..], "tool sets must stay sorted");
        }
    }

    #[test]
    fn unknown_input_is_strict_and_never_escalates() {
        for input in ["", "   ", "junk", "strictly", "read-only", "Bypasss"] {
            assert_eq!(normalize_mode(input), PermissionMode::Strict, "{input}");
        }
        assert_eq!(normalize_value(&Value::Null), PermissionMode::Strict);
        assert_eq!(normalize_value(&json!(true)), PermissionMode::Strict);
        assert_eq!(normalize_value(&json!(1)), PermissionMode::Strict);
        assert_eq!(normalize_value(&json!([])), PermissionMode::Strict);
        assert_eq!(normalize_value(&json!("yolo")), PermissionMode::Bypass);
    }

    #[test]
    fn the_shouty_aliases_are_the_bypass_aliases() {
        for alias in [
            "bypass",
            "bypasspermissions",
            "bypass_permissions",
            "yolo",
            "dangerously-skip-permissions",
            "  DANGEROUSLY-SKIP-PERMISSIONS  ",
        ] {
            assert_eq!(normalize_mode(alias), PermissionMode::Bypass, "{alias}");
        }
    }

    #[test]
    fn auto_approve_short_circuits_even_under_strict() {
        let policy = ToolPolicy::read_only();
        assert!(effective_auto_approve(
            PermissionMode::Strict,
            "read_file",
            &policy,
            None
        ));
    }

    #[test]
    fn strict_approves_nothing_that_is_not_already_auto() {
        let policy = ToolPolicy::default();
        for tool in [
            "write_file",
            "knowledge_search",
            "computer_screenshot",
            "run_command",
        ] {
            assert!(!effective_auto_approve(
                PermissionMode::Strict,
                tool,
                &policy,
                None
            ));
        }
    }

    #[test]
    fn trusted_auto_runs_workspace_writes_but_not_exec_or_control() {
        let write = ToolPolicy {
            risk: "write".into(),
            ..ToolPolicy::default()
        };
        assert!(effective_auto_approve(
            PermissionMode::Trusted,
            "write_file",
            &write,
            None
        ));
        let home_write = ToolPolicy {
            sandbox: "home".into(),
            ..write.clone()
        };
        assert!(!effective_auto_approve(
            PermissionMode::Trusted,
            "write_file",
            &home_write,
            None
        ));
        let exec = ToolPolicy {
            risk: "exec".into(),
            ..ToolPolicy::default()
        };
        assert!(!effective_auto_approve(
            PermissionMode::Trusted,
            "run_command",
            &exec,
            None
        ));
        assert!(!effective_auto_approve(
            PermissionMode::Trusted,
            "computer_click",
            &exec,
            None
        ));
        assert!(effective_auto_approve(
            PermissionMode::Trusted,
            "computer_screenshot",
            &exec,
            None
        ));
    }

    #[test]
    fn trusted_accepts_a_workspace_write_on_either_axis() {
        let odd_risk = ToolPolicy {
            risk: "unclassified".into(),
            ..ToolPolicy::default()
        };
        assert!(effective_auto_approve(
            PermissionMode::Trusted,
            "write_file",
            &odd_risk,
            Some("mutation")
        ));
        assert!(!effective_auto_approve(
            PermissionMode::Trusted,
            "write_file",
            &odd_risk,
            Some("exec")
        ));
        let write = ToolPolicy {
            risk: "write".into(),
            ..ToolPolicy::default()
        };
        assert!(effective_auto_approve(
            PermissionMode::Trusted,
            "write_file",
            &write,
            Some("destructive")
        ));
    }

    #[test]
    fn bypass_still_refuses_destructive_and_system_writes() {
        let destructive = ToolPolicy {
            risk: "destructive".into(),
            destructive: true,
            ..ToolPolicy::default()
        };
        assert!(!effective_auto_approve(
            PermissionMode::Bypass,
            "delete_file",
            &destructive,
            None
        ));
        let system_exec = ToolPolicy {
            risk: "exec".into(),
            sandbox: "system".into(),
            ..ToolPolicy::default()
        };
        assert!(!effective_auto_approve(
            PermissionMode::Bypass,
            "some_system_tool",
            &system_exec,
            None
        ));
        assert!(effective_auto_approve(
            PermissionMode::Bypass,
            "computer_click",
            &system_exec,
            None
        ));
        let system_read = ToolPolicy {
            risk: "read".into(),
            sandbox: "system".into(),
            ..ToolPolicy::default()
        };
        assert!(effective_auto_approve(
            PermissionMode::Bypass,
            "some_system_reader",
            &system_read,
            None
        ));
    }

    #[test]
    fn proposals_are_staged_only_under_strict() {
        assert!(should_stage_proposal(PermissionMode::Strict, true));
        assert!(!should_stage_proposal(PermissionMode::Trusted, true));
        assert!(!should_stage_proposal(PermissionMode::Bypass, true));
        assert!(!should_stage_proposal(PermissionMode::Strict, false));
    }

    #[test]
    fn plan_approval_is_skipped_only_by_bypass() {
        let steps = vec!["run_command".to_string()];
        assert!(plan_requires_approval(
            PermissionMode::Strict,
            &steps,
            false
        ));
        assert!(plan_requires_approval(
            PermissionMode::Trusted,
            &steps,
            false
        ));
        assert!(!plan_requires_approval(
            PermissionMode::Bypass,
            &steps,
            false
        ));
        assert!(!plan_requires_approval(PermissionMode::Bypass, &[], true));
        assert!(plan_requires_approval(PermissionMode::Strict, &[], true));
        assert!(!plan_requires_approval(PermissionMode::Strict, &[], false));
    }

    #[test]
    fn the_contract_names_the_mode_it_describes() {
        for mode in ALL_MODES {
            let contract = mode_contract(mode);
            assert_eq!(contract["mode"], mode.as_str());
            assert_eq!(contract["circuit_breakers"], json!(true));
        }
        assert_eq!(
            mode_contract(PermissionMode::Bypass)["requires_ack"],
            json!(true)
        );
        assert_eq!(
            mode_contract(PermissionMode::Strict)["proposal_first"],
            json!(true)
        );
    }
}
