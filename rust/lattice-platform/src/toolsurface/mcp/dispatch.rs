//! Shared governed dispatch for `POST /mcp` (`tools/call`) and `POST /mcp/call`.

use axum::http::HeaderMap;
use lattice_auth::Identity;
use serde_json::{json, Value};

use crate::toolsurface::tools::fs::{run_grep, run_list_dir, run_read_file, run_workspace_tree};
use crate::toolsurface::tools::knowledge::{run_knowledge_search, run_knowledge_tree};
use crate::toolsurface::tools::shell::run_git_status;
use crate::toolsurface::tools::{check_governance, ToolExecError, ToolsState};
use crate::workspaceos::workspace::skills::{self, InstalledSkill};

/// The curated, read-oriented native tools MCP exposes.
pub const NATIVE_TOOLS: &[NativeTool] = &[
    NativeTool {
        name: "list_dir",
        description:
            "List files in the agent workspace. Paths are sandboxed to the workspace root.",
        input_schema: r#"{"type":"object","properties":{"path":{"type":"string","description":"Workspace-relative directory. Defaults to the workspace root.","default":"."}}}"#,
    },
    NativeTool {
        name: "read_file",
        description: "Read a UTF-8 file from the workspace with optional offset/limit slicing.",
        input_schema: r#"{"type":"object","properties":{"path":{"type":"string","description":"Workspace-relative file path."},"offset":{"type":"integer","minimum":0},"limit":{"type":"integer","minimum":0}},"required":["path"]}"#,
    },
    NativeTool {
        name: "workspace_tree",
        description: "Return a recursive workspace tree (depth-capped).",
        input_schema: r#"{"type":"object","properties":{"path":{"type":"string","default":"."},"max_depth":{"type":"integer","minimum":1,"maximum":8,"default":3}}}"#,
    },
    NativeTool {
        name: "grep",
        description: "Regex search across workspace text files. Binary directories are skipped.",
        input_schema: r#"{"type":"object","properties":{"pattern":{"type":"string"},"path":{"type":"string","default":"."},"max_results":{"type":"integer","minimum":1,"maximum":500,"default":50}},"required":["pattern"]}"#,
    },
    NativeTool {
        name: "knowledge_search",
        description: "Search the local knowledge garden for matching notes (workspace-scoped).",
        input_schema: r#"{"type":"object","properties":{"query":{"type":"string"},"max_results":{"type":"integer","minimum":1,"maximum":20,"default":5}},"required":["query"]}"#,
    },
    NativeTool {
        name: "knowledge_tree",
        description: "List markdown files in the local knowledge garden (workspace-scoped).",
        input_schema: r#"{"type":"object","properties":{}}"#,
    },
    NativeTool {
        name: "git_status",
        description: "Read-only `git status --short` inside the workspace.",
        input_schema: r#"{"type":"object","properties":{}}"#,
    },
];

#[derive(Clone, Copy)]
pub struct NativeTool {
    pub name: &'static str,
    pub description: &'static str,
    pub input_schema: &'static str,
}

impl NativeTool {
    pub fn input_schema_value(&self) -> Value {
        serde_json::from_str(self.input_schema).unwrap_or_else(|_| json!({"type": "object"}))
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DispatchError {
    Unknown(String),
    Governance(String),
    Missing(&'static str),
    Message(String),
    Unavailable(&'static str),
}

impl From<ToolExecError> for DispatchError {
    fn from(error: ToolExecError) -> Self {
        match error {
            ToolExecError::Missing(field) => Self::Missing(field),
            ToolExecError::Message(message) => Self::Message(message),
        }
    }
}

impl std::fmt::Display for DispatchError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Unknown(name) => write!(f, "unknown MCP tool '{name}'"),
            Self::Governance(message) => write!(f, "{message}"),
            Self::Missing(field) => write!(f, "missing field '{field}'"),
            Self::Message(message) => write!(f, "{message}"),
            Self::Unavailable(message) => write!(f, "{message}"),
        }
    }
}

pub fn is_native_tool(name: &str) -> bool {
    NATIVE_TOOLS.iter().any(|tool| tool.name == name)
}

pub fn skill_tool_name(skill: &str) -> String {
    format!("skill.{skill}")
}

pub fn parse_skill_name(name: &str) -> Option<&str> {
    name.strip_prefix("skill.")
        .filter(|rest| !rest.is_empty() && !rest.contains('/') && !rest.contains('\\'))
}

/// Dispatch one `mcp.*` (or bare MCP-native) name for the agent loop.
///
/// # Governance contract
///
/// This is the same [`check_governance`] `POST /mcp` runs — not a second,
/// shorter check written for the loop. The kernel's gate chain is a
/// **superset** of that check, so a name the run already governs never
/// reaches here (`lattice_agent::tools::catalog::resolve` rewrites it to
/// the native path). A name the run does *not* govern still cannot bypass
/// MCP's own check: a governance refusal, an unknown tool, or a missing
/// [`ToolsState`] all return `Err`, which the catalog turns into a tool
/// error. There is no ungoverned success path.
///
/// Governance is evaluated **before** the `ToolsState` requirement, so a
/// catalog that has not been handed a tool surface still refuses on the
/// same grounds `/mcp` would, rather than looking like a configuration
/// miss.
pub fn dispatch_for_agent(
    tools: Option<&ToolsState>,
    skills_dir: &std::path::Path,
    identity: &Identity,
    name: &str,
    args: &Value,
) -> Result<Value, DispatchError> {
    if let Some(skill) = parse_skill_name(name) {
        return dispatch_skill(skills_dir, skill, args);
    }
    let bare = name.strip_prefix("mcp.").unwrap_or(name);
    if let Some(skill) = parse_skill_name(bare) {
        return dispatch_skill(skills_dir, skill, args);
    }
    if !is_native_tool(bare) {
        return Err(DispatchError::Unknown(name.to_string()));
    }
    check_governance(bare, identity, false).map_err(DispatchError::Governance)?;
    dispatch(tools, skills_dir, identity, &HeaderMap::new(), bare, args)
}

/// Run a native tool or return a skill prompt asset.
pub fn dispatch(
    tools: Option<&ToolsState>,
    skills_dir: &std::path::Path,
    identity: &Identity,
    headers: &HeaderMap,
    name: &str,
    args: &Value,
) -> Result<Value, DispatchError> {
    if let Some(skill) = parse_skill_name(name) {
        return dispatch_skill(skills_dir, skill, args);
    }
    if !is_native_tool(name) {
        return Err(DispatchError::Unknown(name.to_string()));
    }
    let tools = tools.ok_or(DispatchError::Unavailable(
        "MCP tool dispatch is not configured on this process",
    ))?;
    check_governance(name, identity, false).map_err(DispatchError::Governance)?;
    match name {
        "list_dir" => Ok(run_list_dir(tools, args)?),
        "read_file" => Ok(run_read_file(tools, args)?),
        "workspace_tree" => Ok(run_workspace_tree(tools, args)?),
        "grep" => Ok(run_grep(tools, args)?),
        "knowledge_search" => Ok(run_knowledge_search(tools, identity, headers, args)?),
        "knowledge_tree" => Ok(run_knowledge_tree(tools, identity, headers)?),
        "git_status" => Ok(run_git_status(tools)?),
        other => Err(DispatchError::Unknown(other.to_string())),
    }
}

pub fn dispatch_skill(
    skills_dir: &std::path::Path,
    name: &str,
    args: &Value,
) -> Result<Value, DispatchError> {
    let skill = skills::scan_installed_skills(skills_dir)
        .into_iter()
        .find(|skill| skill.name == name)
        .ok_or_else(|| DispatchError::Unknown(skill_tool_name(name)))?;
    Ok(skill_result(&skill, args))
}

pub fn skill_result(skill: &InstalledSkill, args: &Value) -> Value {
    let echoed = serde_json::to_string_pretty(args).unwrap_or_else(|_| "{}".into());
    let text = format!(
        "{}\n\n## Invoked with\n```json\n{echoed}\n```\n",
        skill.body
    );
    json!({
        "kind": "skill",
        "name": skill.name,
        "description": skill.description,
        "text": text,
    })
}

pub fn mcp_text_content(result: &Value) -> String {
    if let Some(text) = result.get("text").and_then(Value::as_str) {
        if result.get("kind").and_then(Value::as_str) == Some("skill") {
            return text.to_string();
        }
    }
    serde_json::to_string_pretty(result).unwrap_or_else(|_| result.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn an_unknown_mcp_name_is_refused_before_any_execution() {
        let err = dispatch_for_agent(
            None,
            std::path::Path::new("."),
            &Identity::local_owner(),
            "mcp.some_future_server",
            &json!({}),
        )
        .expect_err("unknown names must not run");
        assert!(
            matches!(err, DispatchError::Unknown(_)),
            "expected Unknown, got {err:?}"
        );
    }

    #[test]
    fn a_curated_tool_still_hits_governance_without_a_tool_surface() {
        // knowledge_search is auto-approve=false; a non-owner is refused by
        // the same check POST /mcp runs, even when ToolsState is absent.
        let member = Identity {
            email: "member@example.com".into(),
            role: "user".into(),
        };
        let err = dispatch_for_agent(
            None,
            std::path::Path::new("."),
            &member,
            "mcp.knowledge_search",
            &json!({"query": "notes"}),
        )
        .expect_err("governance must refuse");
        match err {
            DispatchError::Governance(message) => {
                assert!(message.contains("명시 승인이 필요"), "{message}");
            }
            other => panic!("expected Governance, got {other:?}"),
        }
    }
}
