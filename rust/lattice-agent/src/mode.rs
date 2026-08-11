//! Permission modes — a 1:1 port of `latticeai.core.permission_mode`.
//!
//! Three modes (`strict` → `trusted` → `bypass`) decide whether a tool call may
//! run without a human in the loop. Two properties are load-bearing and are
//! asserted as such by the goldens:
//!
//! * **Unknown input is strict.** `normalize_mode` never fails and never
//!   escalates: anything it does not recognise — a typo, `null`, an empty
//!   string — resolves to the most restrictive mode.
//! * **Circuit breakers are mode-invariant.** [`is_circuit_breaker`] does not
//!   read the mode. `bypass` skips approval prompts; it does not unlock a
//!   destructive tool, a root/home path, or an `rm -rf /` shell string.

use serde_json::{json, Value};

use crate::policy::ToolPolicy;

/// The autonomy dial.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum PermissionMode {
    Strict,
    Trusted,
    Bypass,
}

/// Unknown input resolves here, and so does the absence of any input.
pub const DEFAULT_MODE: PermissionMode = PermissionMode::Strict;

/// Every mode, in dial order.
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

/// Computer-use split: observing the screen is low friction under `trusted`,
/// driving it is not. Both lists stay sorted — `contains` is a binary search.
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

/// Sandboxes that are blocked whatever the mode says. Kept for parity with the
/// Python constant; the enforcement lives in [`effective_auto_approve`]'s
/// bypass branch and in the upstream blocked-prefix guard.
pub const HARD_BLOCK_SANDBOXES: [&str; 1] = ["system"];

fn in_set(set: &[&str], name: &str) -> bool {
    set.binary_search(&name).is_ok()
}

/// Parse user/API/env input into a mode; unknown → strict.
///
/// Python lowercases and strips first, so `"  TRUSTED  "` and `"acceptEdits"`
/// both land. The alias table is copied verbatim, including the two names that
/// exist to be shouted (`yolo`, `dangerously-skip-permissions`).
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

/// Parse a JSON value the way Python's `str(value or "")` does.
///
/// Falsy JSON (`null`, `false`, `0`, `""`, `[]`, `{}`) collapses to the empty
/// string and therefore to strict. Truthy non-strings stringify to something no
/// alias matches (`"True"`, `"1"`, a list repr), so they are strict as well —
/// this shortcut is behaviourally identical without emulating `repr`.
pub fn normalize_value(value: &Value) -> PermissionMode {
    match value {
        Value::String(text) => normalize_mode(text),
        _ => DEFAULT_MODE,
    }
}

/// Whether this call may run without an extra human approval prompt.
///
/// Does **not** override circuit breakers: callers check [`is_circuit_breaker`]
/// first and deny when it answers. Python's signature takes an `args` argument
/// it never reads; the port drops it rather than carry a parameter that lies
/// about influencing the decision.
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
                // Additive and mutation both auto under trusted; destructive is
                // already denied by the breaker above.
                let by_risk = matches!(risk, "write" | "write_scoped" | "read");
                let by_class = matches!(
                    change_class,
                    None | Some("additive") | Some("mutation") | Some("read")
                );
                if (by_risk || by_class) && risk != "destructive" && !policy.destructive {
                    return true;
                }
            }
            // Reads gated for consent (local_list and friends) stay gated.
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
                // Non-desktop system tools stay gated even in bypass.
                return false;
            }
            true
        }
    }
}

/// Whether a mutation should become a Review proposal instead of an apply.
pub fn should_stage_proposal(mode: PermissionMode, proposal_required: bool) -> bool {
    proposal_required && mode == PermissionMode::Strict
}

/// Plan-level gate: `bypass` never pauses, the other two pause on any non-auto
/// step or on the plan's own flag.
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

/// The label, risk and ack flag `mode_catalog` carries for one mode.
///
/// Only the four fields `mode_contract` reads are ported. The catalog's UI copy
/// (`summary`, `warning`, and their Korean twins) stays in Python, where the
/// surface that renders it lives.
fn catalog_entry(mode: PermissionMode) -> (&'static str, &'static str, &'static str, bool) {
    match mode {
        PermissionMode::Strict => ("Strict", "엄격", "low", false),
        PermissionMode::Trusted => ("Trusted", "신뢰", "medium", false),
        PermissionMode::Bypass => ("Bypass", "바이패스", "high", true),
    }
}

/// The serialisable contract an API or agent response carries.
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
        // A workspace writer whose sandbox is *not* the workspace stays gated.
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
        // risk says no, change_class says yes.
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
        // risk says yes, change_class says no — still approved.
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
        // Desktop control is the exception the split exists for.
        assert!(effective_auto_approve(
            PermissionMode::Bypass,
            "computer_click",
            &system_exec,
            None
        ));
        // A system *read* is not a write or an exec, so it passes.
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
