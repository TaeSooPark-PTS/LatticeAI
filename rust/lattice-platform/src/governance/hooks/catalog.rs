use serde_json::{json, Value};

pub(crate) const NOT_EXECUTED: &str = "not_executed";

pub(crate) struct Implementation {
    pub binding: &'static str,
    pub file: &'static str,
    pub symbol: &'static str,
}

pub(crate) struct BuiltinHook {
    pub id: &'static str,
    pub name: &'static str,
    pub kind: &'static str,
    pub order: i64,
    pub description: &'static str,
    pub implementation: Option<Implementation>,
    pub not_executed_reason: &'static str,
}

pub(crate) const BUILTIN_HOOKS: &[BuiltinHook] = &[
    BuiltinHook {
        id: "builtin:redact-secrets",
        name: "Redact secrets",
        kind: "pre_run",
        order: 10,
        description: "Redact PII and secret-like values from agent loop trace events before they are stored or exported.",
        implementation: Some(Implementation {
            binding: "lattice_agent::kernel::trace::LoopTrace::record",
            file: "lattice-agent/src/kernel/trace.rs",
            symbol: "record",
        }),
        not_executed_reason: "",
    },
    BuiltinHook {
        id: "builtin:research-memory-snapshot",
        name: "Research memory snapshot",
        kind: "agent",
        order: 20,
        description: "Capture a short-term memory snapshot after the researcher stage gathers context.",
        implementation: None,
        not_executed_reason: "No researcher stage runs in this build; memory snapshots are created only through the memory snapshot API.",
    },
    BuiltinHook {
        id: "builtin:tool-permission-gate",
        name: "Tool permission gate",
        kind: "pre_tool",
        order: 10,
        description: "Refuse a native tool call the caller's role is not allowed to make, before the tool runs.",
        implementation: Some(Implementation {
            binding: "lattice_agent::tools::authorize::check_role",
            file: "lattice-agent/src/tools/authorize.rs",
            symbol: "check_role",
        }),
        not_executed_reason: "",
    },
    BuiltinHook {
        id: "builtin:sensitive-data-guard",
        name: "Sensitive-data guard",
        kind: "pre_tool",
        order: 20,
        description: "Classify outgoing content for sensitive data before tool execution.",
        implementation: None,
        not_executed_reason: "Sensitive-data classification runs on chat turns and audit views, not before tool execution.",
    },
    BuiltinHook {
        id: "builtin:audit-agent-run",
        name: "Audit agent run",
        kind: "post_run",
        order: 10,
        description: "Append every completed agent run to the workspace audit log.",
        implementation: None,
        not_executed_reason: "Agent runs are recorded only when a client posts them to the workspace runs API; the loop does not append them on completion.",
    },
    BuiltinHook {
        id: "builtin:workflow-replay-log",
        name: "Workflow replay log",
        kind: "post_workflow",
        order: 10,
        description: "Record each workflow run's timeline so it can be replayed step by step.",
        implementation: Some(Implementation {
            binding: "lattice_platform::governance::workflow_designer::WorkflowStore::record_workflow_run",
            file: "lattice-platform/src/governance/workflow_designer/store.rs",
            symbol: "record_workflow_run",
        }),
        not_executed_reason: "",
    },
    BuiltinHook {
        id: "builtin:pipeline-index-status",
        name: "Pipeline index status",
        kind: "post_index",
        order: 10,
        description: "Publish ingest / embed / graph-build pipeline state to the retrieval index status.",
        implementation: None,
        not_executed_reason: "Index status is computed when the status endpoints are read; nothing is published after indexing.",
    },
];

pub(crate) const BRAIN_EVENT_TRIGGERS_DESCRIPTION: &str = "Listed for visibility only: brain_event workflow triggers fire from the graph writer's ingest listener, not from this hook.";

impl BuiltinHook {
    pub(crate) fn executed(&self) -> bool {
        self.implementation.is_some()
    }

    pub(crate) fn binding(&self) -> &'static str {
        self.implementation
            .as_ref()
            .map(|imp| imp.binding)
            .unwrap_or(NOT_EXECUTED)
    }

    pub(crate) fn to_json(&self) -> Value {
        let implementation = self
            .implementation
            .as_ref()
            .map(|imp| json!({"file": imp.file, "symbol": imp.symbol}))
            .unwrap_or(Value::Null);
        let reason = if self.executed() {
            Value::Null
        } else {
            json!(self.not_executed_reason)
        };
        json!({
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "order": self.order,
            "description": self.description,
            "binding": self.binding(),
            "managed": "platform",
            "executed": self.executed(),
            "implementation": implementation,
            "not_executed_reason": reason,
        })
    }
}

pub(crate) fn builtin(id: &str) -> Option<&'static BuiltinHook> {
    BUILTIN_HOOKS.iter().find(|hook| hook.id == id)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn rust_root() -> std::path::PathBuf {
        std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .expect("rust workspace root")
            .to_path_buf()
    }

    #[test]
    fn every_executed_builtin_names_a_function_that_exists() {
        for hook in BUILTIN_HOOKS {
            let Some(imp) = &hook.implementation else {
                continue;
            };
            let source = std::fs::read_to_string(rust_root().join(imp.file))
                .unwrap_or_else(|err| panic!("{}: cannot read {}: {err}", hook.id, imp.file));
            assert!(
                source.contains(&format!("fn {}(", imp.symbol)),
                "{}: {} does not define fn {}",
                hook.id,
                imp.file,
                imp.symbol
            );
            assert!(
                imp.binding.ends_with(&format!("::{}", imp.symbol)),
                "{}: binding {} does not end in its symbol",
                hook.id,
                imp.binding
            );
            let crate_dir = imp.file.split('/').next().unwrap_or("");
            assert_eq!(
                imp.binding.split("::").next(),
                Some(crate_dir.replace('-', "_").as_str()),
                "{}: binding crate does not match its file",
                hook.id
            );
        }
    }

    #[test]
    fn every_builtin_is_either_real_or_says_why_it_never_runs() {
        for hook in BUILTIN_HOOKS {
            let row = hook.to_json();
            let binding = row["binding"].as_str().unwrap_or("");
            assert!(
                !binding.starts_with("latticeai.")
                    && !binding.starts_with("lattice_brain.")
                    && !binding.starts_with("server_app."),
                "{}: names a retired Python symbol",
                hook.id
            );
            if hook.executed() {
                assert!(row["implementation"].is_object(), "{}", hook.id);
                assert!(row["not_executed_reason"].is_null(), "{}", hook.id);
            } else {
                assert_eq!(binding, NOT_EXECUTED, "{}", hook.id);
                assert_eq!(row["executed"], json!(false), "{}", hook.id);
                assert!(
                    !hook.not_executed_reason.trim().is_empty(),
                    "{}: a hook that never runs must say why",
                    hook.id
                );
            }
        }
    }

    #[test]
    fn the_listed_catalog_reports_execution_honestly() {
        let dir = tempfile::tempdir().expect("tempdir");
        let store = super::super::HooksStore::open(dir.path());
        let hooks = store.list(None);
        let rows = hooks
            .get("hooks")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        assert!(!rows.is_empty());
        for row in rows {
            let id = row["id"].as_str().unwrap_or("");
            let executed = row["executed"].as_bool().unwrap_or(false);
            assert_eq!(row["executable"], json!(executed), "{id}");
            if let Some(hook) = builtin(id) {
                assert_eq!(executed, hook.executed(), "{id}");
            }
            if id == super::super::BRAIN_EVENT_TRIGGERS {
                assert!(!executed);
                assert_eq!(row["description"], json!(BRAIN_EVENT_TRIGGERS_DESCRIPTION));
            }
        }
    }
}
