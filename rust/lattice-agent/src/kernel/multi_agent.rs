use std::collections::{BTreeMap, HashMap, HashSet};

use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum AgentRole {
    Supervisor,
    Planner,
    Researcher,
    Executor,
    Reviewer,
}

impl AgentRole {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Supervisor => "supervisor",
            Self::Planner => "planner",
            Self::Researcher => "researcher",
            Self::Executor => "executor",
            Self::Reviewer => "reviewer",
        }
    }

    pub fn parse(raw: &str) -> Option<Self> {
        match raw.trim().to_ascii_lowercase().as_str() {
            "supervisor" => Some(Self::Supervisor),
            "planner" => Some(Self::Planner),
            "researcher" => Some(Self::Researcher),
            "executor" => Some(Self::Executor),
            "reviewer" => Some(Self::Reviewer),
            _ => None,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RoleToolScope {
    pub role: AgentRole,
    pub can_write_files: bool,
    pub can_exec_commands: bool,
    pub allowed_tools: HashSet<String>,
}

impl RoleToolScope {
    pub fn for_role(role: AgentRole) -> Self {
        let read_only = [
            "read_file",
            "list_dir",
            "grep",
            "search_files",
            "knowledge_search",
            "workspace_tree",
        ];
        match role {
            AgentRole::Researcher => {
                let mut tools: HashSet<String> = read_only.iter().map(|s| s.to_string()).collect();
                tools.insert("web_search".to_string());
                tools.insert("web_fetch".to_string());
                Self {
                    role,
                    can_write_files: false,
                    can_exec_commands: false,
                    allowed_tools: tools,
                }
            }
            AgentRole::Planner | AgentRole::Reviewer | AgentRole::Supervisor => Self {
                role,
                can_write_files: false,
                can_exec_commands: false,
                allowed_tools: read_only.iter().map(|s| s.to_string()).collect(),
            },
            AgentRole::Executor => {
                let mut tools: HashSet<String> = read_only.iter().map(|s| s.to_string()).collect();
                for w in [
                    "write_file",
                    "edit_file",
                    "todo_write",
                    "create_web_project",
                    "create_document",
                    "run_command",
                    "run_script",
                    "knowledge_save",
                ] {
                    tools.insert(w.to_string());
                }
                Self {
                    role,
                    can_write_files: true,
                    can_exec_commands: true,
                    allowed_tools: tools,
                }
            }
        }
    }

    pub fn check_tool(&self, tool_name: &str) -> Result<(), String> {
        if self.allowed_tools.contains(tool_name) {
            Ok(())
        } else {
            Err(format!(
                "least-privilege violation: role '{}' is not permitted to invoke tool '{tool_name}'",
                self.role.as_str()
            ))
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct BlackboardArtifact {
    pub artifact_id: String,
    pub version: u32,
    pub author_role: AgentRole,
    pub kind: String,
    pub content: Value,
    pub provenance_sources: Vec<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ConflictSeverity {
    Low,
    Medium,
    High,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ConflictResolution {
    Open,
    AdjudicatedBySupervisor,
    EscalatedNeedsReview,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ConflictRecord {
    pub conflict_id: String,
    pub role_a: AgentRole,
    pub role_b: AgentRole,
    pub claim_a: String,
    pub claim_b: String,
    pub sources: Vec<String>,
    pub severity: ConflictSeverity,
    pub resolution: ConflictResolution,
    pub rationale: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OrchestrationBudget {
    pub max_total_handoffs: usize,
    pub max_total_tool_calls: usize,
    pub max_total_tokens: usize,
    pub max_per_role_tool_calls: usize,
    pub max_edge_transitions: usize,
}

impl Default for OrchestrationBudget {
    fn default() -> Self {
        Self {
            max_total_handoffs: 12,
            max_total_tool_calls: 40,
            max_total_tokens: 120_000,
            max_per_role_tool_calls: 15,
            max_edge_transitions: 2,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum OrchestrationStatus {
    Running,
    Completed,
    NeedsReview,
    BudgetExhausted,
    CycleHalted,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MultiAgentSession {
    pub session_id: String,
    pub workspace_id: String,
    pub status: OrchestrationStatus,
    pub budget: OrchestrationBudget,
    pub total_handoffs: usize,
    pub total_tool_calls: usize,
    pub total_tokens: usize,
    pub tool_calls_by_role: BTreeMap<String, usize>,
    private_scratchpads: BTreeMap<String, Vec<String>>,
    pub blackboard: Vec<BlackboardArtifact>,
    pub conflicts: Vec<ConflictRecord>,
    handoff_counts: HashMap<(AgentRole, AgentRole, String), usize>,
    pub handoff_log: Vec<(AgentRole, AgentRole, String)>,
}

impl MultiAgentSession {
    pub fn new(
        session_id: impl Into<String>,
        workspace_id: impl Into<String>,
        budget: OrchestrationBudget,
    ) -> Self {
        Self {
            session_id: session_id.into(),
            workspace_id: workspace_id.into(),
            status: OrchestrationStatus::Running,
            budget,
            total_handoffs: 0,
            total_tool_calls: 0,
            total_tokens: 0,
            tool_calls_by_role: BTreeMap::new(),
            private_scratchpads: BTreeMap::new(),
            blackboard: Vec::new(),
            conflicts: Vec::new(),
            handoff_counts: HashMap::new(),
            handoff_log: Vec::new(),
        }
    }

    pub fn append_private_note(&mut self, role: AgentRole, note: &str) {
        self.private_scratchpads
            .entry(role.as_str().to_string())
            .or_default()
            .push(lattice_core::redact_pii_and_secrets(note));
    }

    pub fn private_notes(&self, role: AgentRole) -> &[String] {
        self.private_scratchpads
            .get(role.as_str())
            .map(Vec::as_slice)
            .unwrap_or(&[])
    }

    pub fn authorize_tool_call(
        &mut self,
        role: AgentRole,
        tool_name: &str,
        est_tokens: usize,
    ) -> Result<(), String> {
        let scope = RoleToolScope::for_role(role);
        scope.check_tool(tool_name)?;
        if self.total_tool_calls + 1 > self.budget.max_total_tool_calls
            || self.total_tokens + est_tokens > self.budget.max_total_tokens
        {
            self.status = OrchestrationStatus::BudgetExhausted;
            return Err("multi-agent global execution budget exhausted".to_string());
        }
        let per_role = self
            .tool_calls_by_role
            .entry(role.as_str().to_string())
            .or_insert(0);
        if *per_role + 1 > self.budget.max_per_role_tool_calls {
            self.status = OrchestrationStatus::BudgetExhausted;
            return Err(format!(
                "per-role tool budget exhausted for '{}'",
                role.as_str()
            ));
        }
        *per_role += 1;
        self.total_tool_calls += 1;
        self.total_tokens += est_tokens;
        Ok(())
    }

    pub fn publish_artifact(
        &mut self,
        author_role: AgentRole,
        artifact_id: &str,
        kind: &str,
        content: Value,
        provenance_sources: Vec<String>,
    ) -> BlackboardArtifact {
        let next_ver = self
            .blackboard
            .iter()
            .filter(|a| a.artifact_id == artifact_id)
            .map(|a| a.version)
            .max()
            .unwrap_or(0)
            + 1;
        let artifact = BlackboardArtifact {
            artifact_id: artifact_id.to_string(),
            version: next_ver,
            author_role,
            kind: kind.to_string(),
            content: lattice_core::redact_pii_and_secrets_value(&content),
            provenance_sources,
        };
        self.blackboard.push(artifact.clone());
        artifact
    }

    pub fn record_handoff(
        &mut self,
        from: AgentRole,
        to: AgentRole,
        artifact_fingerprint: &str,
    ) -> Result<(), String> {
        if self.total_handoffs + 1 > self.budget.max_total_handoffs {
            self.status = OrchestrationStatus::BudgetExhausted;
            return Err("multi-agent handoff budget exhausted".to_string());
        }
        let key = (from, to, artifact_fingerprint.to_string());
        let seen = self.handoff_counts.entry(key).or_insert(0);
        *seen += 1;
        if *seen > self.budget.max_edge_transitions {
            self.status = OrchestrationStatus::CycleHalted;
            return Err(format!(
                "handoff cycle/ping-pong halted: {} -> {} repeated {} times on '{}'",
                from.as_str(),
                to.as_str(),
                *seen,
                artifact_fingerprint
            ));
        }
        self.total_handoffs += 1;
        self.handoff_log
            .push((from, to, artifact_fingerprint.to_string()));
        Ok(())
    }

    pub fn surface_conflict(&mut self, conflict: ConflictRecord) {
        self.conflicts.push(conflict);
    }

    pub fn finalize_by_supervisor(&mut self) -> OrchestrationStatus {
        if matches!(
            self.status,
            OrchestrationStatus::BudgetExhausted | OrchestrationStatus::CycleHalted
        ) {
            return self.status;
        }
        let has_unresolved_high = self.conflicts.iter().any(|c| {
            c.severity == ConflictSeverity::High && c.resolution == ConflictResolution::Open
        });
        if has_unresolved_high {
            for c in &mut self.conflicts {
                if c.severity == ConflictSeverity::High && c.resolution == ConflictResolution::Open
                {
                    c.resolution = ConflictResolution::EscalatedNeedsReview;
                }
            }
            self.status = OrchestrationStatus::NeedsReview;
        } else {
            self.status = OrchestrationStatus::Completed;
        }
        self.status
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn enforces_least_privilege_isolation_conflicts_budgets_and_cycle_prevention() {
        let mut session = MultiAgentSession::new("sess-1", "ws-1", OrchestrationBudget::default());

        assert!(session
            .authorize_tool_call(AgentRole::Researcher, "knowledge_search", 200)
            .is_ok());
        assert!(session
            .authorize_tool_call(AgentRole::Researcher, "write_file", 100)
            .is_err());
        assert!(session
            .authorize_tool_call(AgentRole::Reviewer, "run_command", 100)
            .is_err());
        assert!(session
            .authorize_tool_call(AgentRole::Executor, "write_file", 300)
            .is_ok());

        session.append_private_note(
            AgentRole::Researcher,
            "found secret sk-proj-abcdefghijklmnop1234",
        );
        assert_eq!(session.private_notes(AgentRole::Executor).len(), 0);
        assert!(session.private_notes(AgentRole::Researcher)[0].contains("[REDACTED_SECRET]"));

        let art = session.publish_artifact(
            AgentRole::Planner,
            "plan-1",
            "plan",
            json!({"steps": ["inspect", "patch"], "owner_email": "alice@example.com"}),
            vec!["doc-1".to_string()],
        );
        assert_eq!(art.version, 1);
        assert_eq!(art.content["owner_email"], "[REDACTED_EMAIL]");

        assert!(session
            .record_handoff(AgentRole::Executor, AgentRole::Reviewer, "hash-a")
            .is_ok());
        assert!(session
            .record_handoff(AgentRole::Executor, AgentRole::Reviewer, "hash-a")
            .is_ok());
        assert!(session
            .record_handoff(AgentRole::Executor, AgentRole::Reviewer, "hash-a")
            .is_err());
        assert_eq!(session.status, OrchestrationStatus::CycleHalted);

        let mut session2 = MultiAgentSession::new("sess-2", "ws-1", OrchestrationBudget::default());
        session2.surface_conflict(ConflictRecord {
            conflict_id: "conf-1".to_string(),
            role_a: AgentRole::Researcher,
            role_b: AgentRole::Reviewer,
            claim_a: "API uses port 8080".to_string(),
            claim_b: "Config pins port 4825".to_string(),
            sources: vec!["doc-a".to_string(), "doc-b".to_string()],
            severity: ConflictSeverity::High,
            resolution: ConflictResolution::Open,
            rationale: None,
        });
        assert_eq!(
            session2.finalize_by_supervisor(),
            OrchestrationStatus::NeedsReview
        );
        assert_eq!(
            session2.conflicts[0].resolution,
            ConflictResolution::EscalatedNeedsReview
        );
    }
}
