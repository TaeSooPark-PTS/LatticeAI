//! Change-class governor — a 1:1 port of `latticeai.core.tool_governor`.
//!
//! The policy table answers "how risky is this tool". The governor answers the
//! question users actually care about: **does this call create something new,
//! or change/remove something that already exists?** Mutations and deletions
//! are proposal-first, and a change that must be reviewed but cannot be staged
//! is *fail-closed* — `fail_closed = proposal_required and not
//! proposal_supported` is the whole reason this module exists.

use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

use crate::in_set;
use crate::policy::ToolPolicy;

pub const CHANGE_READ: &str = "read";
pub const CHANGE_ADDITIVE: &str = "additive";
pub const CHANGE_MUTATION: &str = "mutation";
pub const CHANGE_DESTRUCTIVE: &str = "destructive";
pub const CHANGE_EXEC: &str = "exec";

/// Inventory categories.
pub const NEW_ARTIFACT: &str = "new_artifact";
pub const EXISTING_CONTENT_UPDATE: &str = "existing_content_update";
pub const DELETE: &str = "delete";
pub const EXTERNAL_SIDE_EFFECT: &str = "external_side_effect";
pub const INTERNAL_STATE: &str = "internal_state";

/// Tools whose effect depends on whether the target already exists.
const TARGET_WRITE_TOOLS: [&str; 6] = [
    "create_docx",
    "create_pdf",
    "create_pptx",
    "create_xlsx",
    "local_write",
    "write_file",
];
/// Tools that always rewrite existing content.
const ALWAYS_MUTATION_TOOLS: [&str; 1] = ["edit_file"];
/// Tools that remove existing content.
const DESTRUCTIVE_TOOLS: [&str; 3] = ["clear_history", "delete_file", "remove_file"];
/// Append-style knowledge writes that never rewrite.
const ADDITIVE_TOOLS: [&str; 5] = [
    "create_web_project",
    "knowledge_graph_ingest",
    "knowledge_save",
    "obsidian_save",
    "todo_write",
];

/// Every side-effecting tool, classified into exactly one category.
///
/// `assert_governance_coverage` in Python fails CI when a registry tool is
/// missing from here, so this list is the proof that nothing mutating ships
/// ungoverned. Sorted by name; the parity suite asserts it equals the Python
/// dictionary entry for entry.
pub const MUTATING_TOOL_INVENTORY: [(&str, &str); 26] = [
    ("build_project", EXTERNAL_SIDE_EFFECT),
    ("clear_history", DELETE),
    ("computer_click", EXTERNAL_SIDE_EFFECT),
    ("computer_drag", EXTERNAL_SIDE_EFFECT),
    ("computer_key", EXTERNAL_SIDE_EFFECT),
    ("computer_move", EXTERNAL_SIDE_EFFECT),
    ("computer_open_app", EXTERNAL_SIDE_EFFECT),
    ("computer_open_url", EXTERNAL_SIDE_EFFECT),
    ("computer_scroll", EXTERNAL_SIDE_EFFECT),
    ("computer_type", EXTERNAL_SIDE_EFFECT),
    ("create_docx", EXISTING_CONTENT_UPDATE),
    ("create_pdf", EXISTING_CONTENT_UPDATE),
    ("create_pptx", EXISTING_CONTENT_UPDATE),
    ("create_web_project", NEW_ARTIFACT),
    ("create_xlsx", EXISTING_CONTENT_UPDATE),
    ("delete_file", DELETE),
    ("deploy_project", EXTERNAL_SIDE_EFFECT),
    ("edit_file", EXISTING_CONTENT_UPDATE),
    ("knowledge_graph_ingest", NEW_ARTIFACT),
    ("knowledge_save", NEW_ARTIFACT),
    ("local_write", EXISTING_CONTENT_UPDATE),
    ("obsidian_save", NEW_ARTIFACT),
    ("remove_file", DELETE),
    ("run_command", EXTERNAL_SIDE_EFFECT),
    ("todo_write", INTERNAL_STATE),
    ("write_file", EXISTING_CONTENT_UPDATE),
];

/// Tools whose existing-content update the proposal service can stage *and*
/// apply. A `proposal_required` tool outside this set is blocked, not applied.
pub const PROPOSAL_CAPABLE_TOOLS: [&str; 2] = ["edit_file", "write_file"];

/// One call's change class and proposal requirement.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Classification {
    pub tool: String,
    pub change_class: String,
    pub proposal_required: bool,
    pub proposal_supported: bool,
    pub fail_closed: bool,
    pub reason: String,
}

/// Classify one tool call.
///
/// `path_exists` is injected for the same reason Python injects it: the agent
/// loop, the chat path and the tests must share exactly one policy, and a
/// classifier that stats the disk itself cannot be one of them.
pub fn classify_tool_call(
    name: &str,
    args: &Map<String, Value>,
    policy: &ToolPolicy,
    path_exists: &dyn Fn(&str) -> bool,
) -> Classification {
    let risk = policy.risk();
    let (change, reason) = if in_set(&DESTRUCTIVE_TOOLS, name) || policy.destructive {
        (CHANGE_DESTRUCTIVE, "removes existing content")
    } else if in_set(&ALWAYS_MUTATION_TOOLS, name) {
        (CHANGE_MUTATION, "edits existing content in place")
    } else if in_set(&TARGET_WRITE_TOOLS, name) {
        let path = text_arg(args, "path").or_else(|| text_arg(args, "filename"));
        let exists = path.as_deref().is_some_and(path_exists);
        if exists {
            (CHANGE_MUTATION, "overwrites an existing file")
        } else {
            (CHANGE_ADDITIVE, "creates a new file")
        }
    } else if in_set(&ADDITIVE_TOOLS, name) {
        (CHANGE_ADDITIVE, "adds new content only")
    } else if risk.starts_with("read") {
        (CHANGE_READ, "read-only tool")
    } else if risk == "exec" {
        (
            CHANGE_EXEC,
            "executes an action (approval-gated, not proposal-based)",
        )
    } else if matches!(risk, "write" | "write_scoped") {
        (CHANGE_ADDITIVE, "write tool without an existing target")
    } else {
        (CHANGE_READ, "read-only tool")
    };

    let proposal_required = matches!(change, CHANGE_MUTATION | CHANGE_DESTRUCTIVE);
    let proposal_supported = in_set(&PROPOSAL_CAPABLE_TOOLS, name);
    Classification {
        tool: name.to_string(),
        change_class: change.to_string(),
        proposal_required,
        proposal_supported,
        // A change that must be reviewed but that we cannot stage is
        // fail-closed: callers block it instead of applying it silently.
        fail_closed: proposal_required && !proposal_supported,
        reason: reason.to_string(),
    }
}

/// `str(args.get(key) or "")` restricted to the one falsy case that matters:
/// an empty string is no path at all.
fn text_arg(args: &Map<String, Value>, key: &str) -> Option<String> {
    match args.get(key) {
        Some(Value::String(text)) if !text.is_empty() => Some(text.clone()),
        Some(Value::Number(number)) if number.as_f64() != Some(0.0) => Some(number.to_string()),
        Some(Value::Bool(true)) => Some("True".into()),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn args(value: Value) -> Map<String, Value> {
        value.as_object().expect("object").clone()
    }

    fn never(_: &str) -> bool {
        false
    }

    fn always(_: &str) -> bool {
        true
    }

    #[test]
    fn every_inventory_and_tool_set_is_sorted() {
        for set in [
            &TARGET_WRITE_TOOLS[..],
            &ALWAYS_MUTATION_TOOLS[..],
            &DESTRUCTIVE_TOOLS[..],
            &ADDITIVE_TOOLS[..],
            &PROPOSAL_CAPABLE_TOOLS[..],
        ] {
            let mut sorted = set.to_vec();
            sorted.sort_unstable();
            assert_eq!(set, &sorted[..]);
        }
        let names: Vec<&str> = MUTATING_TOOL_INVENTORY.iter().map(|(n, _)| *n).collect();
        let mut sorted = names.clone();
        sorted.sort_unstable();
        assert_eq!(names, sorted);
    }

    #[test]
    fn a_write_is_additive_until_the_target_exists() {
        let policy = ToolPolicy::default();
        let call = args(json!({"path": "notes/a.md"}));
        let new = classify_tool_call("write_file", &call, &policy, &never);
        assert_eq!(new.change_class, CHANGE_ADDITIVE);
        assert!(!new.proposal_required && !new.fail_closed);
        let overwrite = classify_tool_call("write_file", &call, &policy, &always);
        assert_eq!(overwrite.change_class, CHANGE_MUTATION);
        assert!(overwrite.proposal_required && overwrite.proposal_supported);
        assert!(!overwrite.fail_closed, "write_file can be staged");
    }

    #[test]
    fn a_binary_overwrite_is_fail_closed_because_it_cannot_be_staged() {
        let policy = ToolPolicy::default();
        let call = args(json!({"filename": "report.docx"}));
        let result = classify_tool_call("create_docx", &call, &policy, &always);
        assert_eq!(result.change_class, CHANGE_MUTATION);
        assert!(result.proposal_required);
        assert!(!result.proposal_supported);
        assert!(
            result.fail_closed,
            "an unstageable rewrite must fail closed"
        );
    }

    #[test]
    fn deletions_are_destructive_whoever_asks() {
        let policy = ToolPolicy::default();
        for tool in ["delete_file", "remove_file", "clear_history"] {
            let result = classify_tool_call(tool, &Map::new(), &policy, &never);
            assert_eq!(result.change_class, CHANGE_DESTRUCTIVE, "{tool}");
            assert!(result.fail_closed, "{tool}");
        }
        // A destructive *policy* is enough, even for a tool with a benign name.
        let destructive = ToolPolicy {
            destructive: true,
            ..ToolPolicy::default()
        };
        let result = classify_tool_call("write_file", &Map::new(), &destructive, &never);
        assert_eq!(result.change_class, CHANGE_DESTRUCTIVE);
    }

    #[test]
    fn risk_decides_when_the_name_is_unknown() {
        let cases = [
            ("read", CHANGE_READ),
            ("read_scoped", CHANGE_READ),
            ("exec", CHANGE_EXEC),
            ("write", CHANGE_ADDITIVE),
            ("write_scoped", CHANGE_ADDITIVE),
            ("", CHANGE_READ),
        ];
        for (risk, expected) in cases {
            let policy = ToolPolicy {
                risk: risk.into(),
                ..ToolPolicy::default()
            };
            let result = classify_tool_call("mystery_tool", &Map::new(), &policy, &never);
            assert_eq!(result.change_class, expected, "risk={risk}");
        }
    }

    #[test]
    fn edit_file_is_a_mutation_with_no_target_lookup_at_all() {
        let result = classify_tool_call("edit_file", &Map::new(), &ToolPolicy::default(), &|_| {
            panic!("edit_file must not consult the filesystem")
        });
        assert_eq!(result.change_class, CHANGE_MUTATION);
    }

    #[test]
    fn an_empty_path_is_no_path_and_stays_additive() {
        let call = args(json!({"path": ""}));
        let result = classify_tool_call("write_file", &call, &ToolPolicy::default(), &always);
        assert_eq!(result.change_class, CHANGE_ADDITIVE);
    }
}
