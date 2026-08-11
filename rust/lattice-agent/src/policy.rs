//! Tool policy — **data**, not a table.
//!
//! `latticeai.core.tool_registry.TOOL_GOVERNANCE` is the single source of truth
//! for "how risky is this tool", and `ToolRegistry.policy_for` is the single
//! source of truth for the one args-dependent rewrite (a write aimed at a
//! blocked system prefix becomes a destructive policy). Re-declaring either in
//! Rust would create a second copy that drifts silently the first time a tool is
//! added. So the kernel takes policies as input: callers pass the real policy,
//! or a table of them, and the goldens carry the real table verbatim.

use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

/// One tool's governance record — the Python `ToolPolicy` TypedDict.
///
/// Fields default rather than fail: a caller that knows only `risk` still gets
/// a decidable policy, and `Default` is exactly Python's
/// `TOOL_GOVERNANCE_DEFAULT` (write / workspace / no rollback / gated).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ToolPolicy {
    /// `read` | `write` | `write_scoped` | `exec` | `destructive`.
    #[serde(default)]
    pub risk: String,
    #[serde(default)]
    pub destructive: bool,
    #[serde(default)]
    pub shell: bool,
    #[serde(default)]
    pub network: bool,
    #[serde(default)]
    pub auto_approve: bool,
    /// `workspace` | `home` | `system`. Empty reads as `workspace`, which is
    /// Python's `str(policy.get("sandbox") or "workspace")`.
    #[serde(default)]
    pub sandbox: String,
    #[serde(default)]
    pub rollback: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub capability: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub scope: Option<String>,
}

impl Default for ToolPolicy {
    fn default() -> Self {
        Self {
            risk: "write".into(),
            destructive: false,
            shell: false,
            network: false,
            auto_approve: false,
            sandbox: "workspace".into(),
            rollback: "none".into(),
            capability: None,
            scope: None,
        }
    }
}

impl ToolPolicy {
    /// `str(policy.get("risk") or "")` — a missing risk is the empty string,
    /// which matches none of the risk branches rather than defaulting into one.
    pub fn risk(&self) -> &str {
        &self.risk
    }

    /// `str(policy.get("sandbox") or "workspace")`.
    pub fn sandbox(&self) -> &str {
        if self.sandbox.is_empty() {
            "workspace"
        } else {
            &self.sandbox
        }
    }

    /// Whether this policy is destructive by flag or by risk level. The two
    /// spellings are checked together everywhere Python checks them together.
    pub fn is_destructive(&self) -> bool {
        self.destructive || self.risk == "destructive"
    }

    /// A read-only policy for tests and callers that only need a shape.
    pub fn read_only() -> Self {
        Self {
            risk: "read".into(),
            auto_approve: true,
            rollback: "none".into(),
            ..Self::default()
        }
    }
}

/// A name → policy table plus the fallback for names it does not carry.
///
/// This mirrors `ToolRegistry.governance` + `ToolRegistry.default_policy`. What
/// it deliberately does *not* mirror is `policy_for`'s blocked-prefix rewrite:
/// that rewrite is a function of the call arguments, and the caller that has
/// the arguments (the worker, or the fixture generator) passes the rewritten
/// policy in. Guessing it here would mean re-deriving
/// `LOCAL_WRITE_BLOCKED_PREFIXES` in a second place.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PolicyTable {
    #[serde(default)]
    pub tools: BTreeMap<String, ToolPolicy>,
    #[serde(default)]
    pub default: ToolPolicy,
    /// `LOCAL_WRITE_BLOCKED_PREFIXES` — **data**, like the rest of the table.
    ///
    /// The loop cannot pre-compute a policy for a tool the model has not chosen
    /// yet, so [`PolicyTable::policy_for`] has to make the one args-dependent
    /// rewrite `ToolRegistry.policy_for` makes. The prefixes come in with the
    /// table rather than being hard-coded a second time, and the parity goldens
    /// carry the real tuple so a change in Python fails the contract.
    #[serde(default = "default_blocked_write_prefixes")]
    pub blocked_write_prefixes: Vec<String>,
}

/// The tuple `latticeai.core.tool_registry` ships, for callers that send none.
fn default_blocked_write_prefixes() -> Vec<String> {
    [
        "/etc/",
        "/usr/",
        "/bin/",
        "/sbin/",
        "/System/",
        "/private/etc/",
        "/Library/LaunchDaemons/",
        "/Library/LaunchAgents/",
    ]
    .into_iter()
    .map(String::from)
    .collect()
}

impl Default for PolicyTable {
    fn default() -> Self {
        Self {
            tools: BTreeMap::new(),
            default: ToolPolicy::default(),
            blocked_write_prefixes: default_blocked_write_prefixes(),
        }
    }
}

/// Tools whose `path` argument is checked against the blocked prefixes.
const PREFIX_CHECKED_WRITERS: [&str; 3] = ["edit_file", "local_write", "write_file"];

/// `RISK_LEVEL_MAP` — policy risk → the coarse label the transcript records.
pub fn risk_level(policy: &ToolPolicy) -> &'static str {
    match policy.risk() {
        "read" => "low",
        "write" => "medium",
        "exec" | "destructive" => "high",
        _ => "medium",
    }
}

impl PolicyTable {
    /// The policy for `name`, falling back to the table's default.
    pub fn get(&self, name: &str) -> &ToolPolicy {
        self.tools.get(name).unwrap_or(&self.default)
    }

    /// Whether the table carries an entry of its own for `name`.
    pub fn has(&self, name: &str) -> bool {
        self.tools.contains_key(name)
    }

    /// `ToolRegistry.policy_for`: the table entry, unless this is a write aimed
    /// at a blocked system prefix — which is a destructive policy instead.
    pub fn policy_for(&self, name: &str, args: &Map<String, Value>) -> ToolPolicy {
        if PREFIX_CHECKED_WRITERS.binary_search(&name).is_ok() {
            // `str(args.get("path", ""))`: a *present* null stringifies to
            // `"None"`, which matches no prefix — the default `""` does not.
            let path = match args.get("path") {
                Some(value) => crate::pystr::py_str(value),
                None => String::new(),
            }
            .replace('\\', "/");
            for prefix in &self.blocked_write_prefixes {
                let normalized = prefix.trim_end_matches('/');
                if path == normalized || path.starts_with(&format!("{normalized}/")) {
                    return ToolPolicy {
                        risk: "destructive".into(),
                        destructive: true,
                        shell: false,
                        network: false,
                        auto_approve: false,
                        sandbox: "system".into(),
                        rollback: "none".into(),
                        capability: None,
                        scope: None,
                    };
                }
            }
        }
        self.get(name).clone()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_default_policy_is_pythons_tool_governance_default() {
        let policy = ToolPolicy::default();
        assert_eq!(policy.risk(), "write");
        assert_eq!(policy.sandbox(), "workspace");
        assert_eq!(policy.rollback, "none");
        assert!(!policy.auto_approve && !policy.destructive);
        assert!(!policy.shell && !policy.network);
    }

    #[test]
    fn an_empty_sandbox_reads_as_workspace() {
        let policy = ToolPolicy {
            sandbox: String::new(),
            ..ToolPolicy::default()
        };
        assert_eq!(policy.sandbox(), "workspace");
    }

    #[test]
    fn destructive_is_either_spelling() {
        assert!(ToolPolicy {
            destructive: true,
            ..ToolPolicy::default()
        }
        .is_destructive());
        assert!(ToolPolicy {
            risk: "destructive".into(),
            ..ToolPolicy::default()
        }
        .is_destructive());
        assert!(!ToolPolicy::read_only().is_destructive());
    }

    #[test]
    fn a_table_falls_back_to_its_own_default() {
        let mut table = PolicyTable::default();
        table
            .tools
            .insert("read_file".into(), ToolPolicy::read_only());
        assert_eq!(table.get("read_file").risk(), "read");
        assert!(table.has("read_file"));
        assert_eq!(table.get("not_a_tool").risk(), "write");
        assert!(!table.has("not_a_tool"));
    }

    #[test]
    fn a_write_to_a_blocked_system_prefix_becomes_a_destructive_policy() {
        let table = PolicyTable::default();
        let args = |path: &str| {
            serde_json::json!({"path": path})
                .as_object()
                .expect("object")
                .clone()
        };
        for blocked in [
            "/etc/hosts",
            "/etc",
            "\\etc\\hosts",
            "/Library/LaunchAgents/x.plist",
        ] {
            let policy = table.policy_for("write_file", &args(blocked));
            assert!(policy.is_destructive(), "{blocked}");
            assert_eq!(policy.sandbox(), "system");
            assert!(!policy.auto_approve);
        }
        for allowed in ["notes/a.md", "/etcetera/a.md", "", "/home/u/etc/a"] {
            assert!(
                !table
                    .policy_for("write_file", &args(allowed))
                    .is_destructive(),
                "{allowed}"
            );
        }
        // The rewrite only applies to the three writers that take a path.
        assert!(!table
            .policy_for("read_file", &args("/etc/hosts"))
            .is_destructive());
        assert!(!table.policy_for("write_file", &Map::new()).is_destructive());
    }

    #[test]
    fn the_prefix_list_is_data_the_caller_can_replace() {
        let table = PolicyTable {
            blocked_write_prefixes: vec!["/srv/".into()],
            ..PolicyTable::default()
        };
        let args = serde_json::json!({"path": "/srv/x"})
            .as_object()
            .expect("object")
            .clone();
        assert!(table.policy_for("write_file", &args).is_destructive());
        let etc = serde_json::json!({"path": "/etc/hosts"})
            .as_object()
            .expect("object")
            .clone();
        assert!(!table.policy_for("write_file", &etc).is_destructive());
        assert_eq!(default_blocked_write_prefixes().len(), 8);
    }

    #[test]
    fn risk_levels_are_the_python_map_with_its_fallback() {
        let of = |risk: &str| {
            risk_level(&ToolPolicy {
                risk: risk.into(),
                ..ToolPolicy::default()
            })
        };
        assert_eq!(of("read"), "low");
        assert_eq!(of("write"), "medium");
        assert_eq!(of("exec"), "high");
        assert_eq!(of("destructive"), "high");
        assert_eq!(
            of("write_scoped"),
            "medium",
            "unmapped falls back to medium"
        );
        assert_eq!(of(""), "medium");
    }

    #[test]
    fn a_policy_round_trips_through_json_without_inventing_fields() {
        let json = r#"{"risk":"exec","shell":true,"sandbox":"system"}"#;
        let policy: ToolPolicy = serde_json::from_str(json).expect("partial policies parse");
        assert_eq!(policy.risk(), "exec");
        assert!(policy.shell && !policy.network);
        assert_eq!(policy.sandbox(), "system");
        let encoded = serde_json::to_value(&policy).expect("serialises");
        assert!(encoded.get("capability").is_none(), "absent stays absent");
    }
}
