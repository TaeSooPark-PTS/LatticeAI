//! The gates one executor step passes, in the order that decides it.
//!
//! Split out of [`super::execution`] because the order *is* the policy and it
//! deserves to be readable in one screen:
//!
//! 1. [`Runtime::governor_review`] — change-class governance. Under a mode that
//!    does not stage proposals the decision is made **before** the governor is
//!    consulted, because reviewing persists a proposal as a side effect;
//!    reviewing first and discarding the verdict would apply the change *and*
//!    leave an orphan pending in the Review Center.
//! 2. [`Runtime::blocked_by_gates`] — circuit breaker, then destructive policy,
//!    then the fail-closed overwrite guard, then the approval gate. The first
//!    three are **mode-invariant**: `bypass` skips the approval prompt, it never
//!    removes an existence check or unlocks a breaker.
//! 3. [`Runtime::dispatch_step`] — pre-write snapshot, then the worker seam,
//!    recorded on the transcript either way.

use serde_json::{json, Map, Value};

use super::{RunRequest, Runtime, SNAPSHOT_MAX_BYTES};
use crate::breaker::is_circuit_breaker;
use crate::governor::classify_tool_call;
use crate::mode::should_stage_proposal;
use crate::permission::block_reason_for_tool;
use crate::policy::{risk_level, ToolPolicy};
use crate::pystr::{is_truthy, py_str};
use crate::state::{AgentRunContext, AgentState};
use crate::worker::ToolOutcome;

/// `{k: v for k, v in args.items() if k != "content"}` — the decision is worth
/// replaying into the transcript; the payload never is.
fn without_content(args: &Map<String, Value>) -> Value {
    Value::Object(
        args.iter()
            .filter(|(key, _)| key.as_str() != "content")
            .map(|(key, value)| (key.clone(), value.clone()))
            .collect(),
    )
}

fn path_arg(args: &Map<String, Value>) -> Option<String> {
    args.get("path")
        .filter(|value| is_truthy(value))
        .map(py_str)
}

/// One tool call as the gates see it: what the model asked for, why it says it
/// asked, and the policy the registry gave that call. Bundled because all three
/// gates take exactly this set and a seven-argument signature hides which
/// argument is which at the call site.
#[derive(Debug, Clone, Copy)]
pub(super) struct Call<'a> {
    pub name: &'a str,
    pub thoughts: &'a str,
    pub args: &'a Map<String, Value>,
    pub policy: &'a ToolPolicy,
}

impl Runtime {
    /// Central change-class governance.
    ///
    /// Returns `(proposed, governor_allows_additive)`: `proposed` means the step
    /// was staged and execution must be skipped; `allows_additive` lets an
    /// additive create pass the classic approval gate.
    pub(super) async fn governor_review(
        &mut self,
        ctx: &mut AgentRunContext,
        req: &RunRequest,
        call: Call<'_>,
    ) -> (bool, bool) {
        let Call {
            name,
            thoughts,
            args,
            policy,
        } = call;
        if !self.deps.governor_enabled {
            return (false, false);
        }
        let mode = self.resolve_permission_mode(ctx, req);
        let workspace_id = req.workspace_id.as_deref();
        if !should_stage_proposal(mode, true) {
            if !self.deps.governed_tools.contains(name) {
                return (false, false);
            }
            if policy.is_destructive() {
                // Let the destructive gate downstream own the block + transcript.
                return (false, false);
            }
            self.audit(
                "agent_change_auto_applied",
                &[
                    ("user_email", json!(req.user_email)),
                    ("workspace_id", json!(workspace_id)),
                    ("action", json!(name)),
                    ("path", json!(path_arg(args))),
                    ("permission_mode", json!(mode.as_str())),
                    (
                        "note",
                        json!("permission mode auto-applies mutation with audit"),
                    ),
                ],
            );
            return (false, true);
        }

        let verdict = self
            .deps
            .worker
            .change_proposal(
                name,
                args,
                &serde_json::to_value(policy).unwrap_or(Value::Null),
                workspace_id,
                req.conversation_id.as_deref(),
            )
            .await;
        let Some(verdict) = verdict else {
            return (false, false);
        };
        let risk = risk_level(policy);
        if verdict.get("decision").and_then(Value::as_str) == Some("proposed") {
            let proposal = verdict.get("proposal").cloned().unwrap_or(json!({}));
            ctx.trace.tool("execute", name, "proposed", Some(risk));
            self.emit_step("execute", "proposed", &[("action", json!(name))]);
            ctx.transcript.push(json!({
                "state": AgentState::Executing.as_str(),
                "action": name,
                "thoughts": thoughts,
                "args": without_content(args),
                "risk": risk,
                "governance": policy,
                "result": {
                    "proposed": true,
                    "proposal_id": proposal.get("id").cloned().unwrap_or(Value::Null),
                    "note": "기존 내용을 바꾸는 작업이라 변경 제안으로 저장했습니다. \
            검토함에서 승인하면 적용됩니다.",
                },
            }));
            self.audit(
                "agent_change_proposed",
                &[
                    ("user_email", json!(req.user_email)),
                    ("action", json!(name)),
                    (
                        "proposal_id",
                        proposal.get("id").cloned().unwrap_or(Value::Null),
                    ),
                    (
                        "change_class",
                        verdict
                            .get("classification")
                            .and_then(|classification| classification.get("change_class"))
                            .cloned()
                            .unwrap_or(Value::Null),
                    ),
                ],
            );
            return (true, false);
        }
        (
            false,
            verdict.get("decision").and_then(Value::as_str) == Some("allow_additive"),
        )
    }

    /// Destructive / circuit-breaker / fail-closed-overwrite / approval gates.
    /// True when the step was blocked.
    pub(super) fn blocked_by_gates(
        &mut self,
        ctx: &mut AgentRunContext,
        req: &RunRequest,
        call: Call<'_>,
        governor_allows_additive: bool,
    ) -> bool {
        let Call {
            name,
            thoughts,
            args,
            policy,
        } = call;
        let mode = self.resolve_permission_mode(ctx, req);
        let risk = risk_level(policy);
        let source = req.source.clone().unwrap_or_else(|| "agent".into());

        // Hard denials first, mode-invariant.
        let breaker = is_circuit_breaker(name, policy, args);
        let hard_deny = match (&breaker, policy.is_destructive()) {
            (Some(reason), _) => Some(format!("BLOCKED: {reason}")),
            (None, true) => Some(format!(
                "BLOCKED: destructive action '{name}' not permitted in agent mode."
            )),
            (None, false) => None,
        };
        if let Some(error) = hard_deny {
            ctx.trace
                .tool("execute", name, "blocked_destructive", Some(risk));
            self.emit_step(
                "execute",
                "blocked",
                &[("action", json!(name)), ("reason", json!("destructive"))],
            );
            ctx.transcript.push(json!({
                "state": AgentState::Executing.as_str(),
                "action": name, "thoughts": thoughts,
                "args": Value::Object(args.clone()), "risk": risk,
                "governance": policy,
                "permission_mode": mode.as_str(),
                "error": error,
            }));
            self.audit(
                "agent_blocked",
                &[
                    ("user_email", json!(req.user_email)),
                    ("source", json!(source)),
                    ("action", json!(name)),
                    ("reason", json!("destructive")),
                    ("governance", json!(policy)),
                ],
            );
            return true;
        }

        // Fail-closed overwrite guard — also mode-invariant. A call that
        // rewrites existing content but cannot be staged as a reviewable
        // proposal has no safe apply path in ANY mode.
        let exists = |candidate: &str| self.governed_path_exists(name, candidate);
        let overwrite = classify_tool_call(name, args, policy, &exists);
        if overwrite.fail_closed {
            let target = args
                .get("path")
                .filter(|value| is_truthy(value))
                .or_else(|| args.get("filename").filter(|value| is_truthy(value)))
                .map(py_str)
                .unwrap_or_default();
            let error = format!(
                "NEEDS_REVIEW: '{name}' 은(는) 이미 있는 파일 '{target}' 을(를) 덮어씁니다. \
이 도구의 변경은 검토 가능한 제안으로 만들 수 없어 실행하지 않았습니다. \
새 파일 이름으로 만들거나 write_file/edit_file 로 수정하세요."
            );
            ctx.trace
                .tool("execute", name, "blocked_overwrite", Some(risk));
            self.emit_step(
                "execute",
                "blocked",
                &[("action", json!(name)), ("reason", json!("overwrite"))],
            );
            ctx.transcript.push(json!({
                "state": AgentState::Executing.as_str(),
                "action": name, "thoughts": thoughts,
                "args": without_content(args),
                "risk": risk,
                "governance": policy,
                "permission_mode": mode.as_str(),
                "change_class": overwrite.change_class,
                "error": error,
            }));
            self.audit(
                "agent_blocked",
                &[
                    ("user_email", json!(req.user_email)),
                    ("source", json!(source)),
                    ("action", json!(name)),
                    ("reason", json!("overwrite_fail_closed")),
                    (
                        "path",
                        if target.is_empty() {
                            Value::Null
                        } else {
                            json!(target)
                        },
                    ),
                    ("change_class", json!(overwrite.change_class)),
                    ("permission_mode", json!(mode.as_str())),
                    ("governance", json!(policy)),
                ],
            );
            return true;
        }

        let Some(reason) = block_reason_for_tool(
            mode,
            name,
            policy,
            args,
            ctx.approved_by_human,
            governor_allows_additive,
        ) else {
            return false;
        };
        self.audit(
            "agent_exec",
            &[
                ("user_email", json!(req.user_email)),
                ("source", json!(source)),
                ("state", json!(AgentState::Executing.as_str())),
                ("action", json!(name)),
                ("risk", json!(risk)),
                ("shell", json!(policy.shell)),
                ("network", json!(policy.network)),
                ("destructive", json!(policy.destructive)),
                ("sandbox", json!(policy.sandbox)),
                ("rollback", json!(policy.rollback)),
                ("permission_mode", json!(mode.as_str())),
                ("args", without_content(args)),
            ],
        );
        ctx.trace
            .tool("execute", name, "blocked_approval", Some(risk));
        self.emit_step(
            "execute",
            "blocked",
            &[("action", json!(name)), ("reason", json!("approval"))],
        );
        ctx.transcript.push(json!({
            "state": AgentState::Executing.as_str(),
            "action": name, "thoughts": thoughts,
            "args": Value::Object(args.clone()), "risk": risk,
            "governance": policy,
            "permission_mode": mode.as_str(),
            "error": reason,
        }));
        true
    }

    /// Pre-write snapshot + the worker seam, recorded on the transcript either
    /// way. Returns the appended step's index.
    pub(super) async fn dispatch_step(
        &mut self,
        ctx: &mut AgentRunContext,
        req: &RunRequest,
        call: Call<'_>,
    ) {
        let Call {
            name,
            thoughts,
            args,
            policy,
        } = call;
        let risk = risk_level(policy);
        // `write_file`'s content sanitation is the worker's: the seam's execute
        // runs `sanitize_write_content`, so there is no second copy here that
        // could disagree with the one that actually writes the bytes.
        let step_index = 1 + ctx
            .transcript
            .iter()
            .filter(|step| {
                step.get("state").and_then(Value::as_str) == Some(AgentState::Executing.as_str())
                    && !matches!(
                        step.get("action").and_then(Value::as_str),
                        None | Some("final") | Some("parse_error")
                    )
            })
            .count();

        if self.deps.file_create_actions.contains(name) {
            if let Some(path) = path_arg(args) {
                // The first capture per path is the true pre-run state; later
                // writes to the same path must not overwrite it.
                let already = ctx
                    .rollback_log
                    .iter()
                    .any(|entry| entry.get("path").and_then(Value::as_str) == Some(path.as_str()));
                if !already {
                    let mut entry = json!({"path": path});
                    if let Some(map) = self.snapshot_file(&path).as_object() {
                        for (key, value) in map {
                            entry[key] = value.clone();
                        }
                    }
                    ctx.rollback_log.push(entry);
                }
            }
        }

        let outcome = self
            .deps
            .worker
            .tool(name, args, req.workspace_id.as_deref())
            .await;
        let path_detail = path_arg(args).map(Value::from).unwrap_or(Value::Null);
        match outcome {
            ToolOutcome::Result(result) => {
                ctx.trace.tool("execute", name, "ok", Some(risk));
                let mut step = json!({
                    "state": AgentState::Executing.as_str(),
                    "action": name, "thoughts": thoughts,
                    "args": Value::Object(args.clone()),
                    "risk": risk, "governance": policy, "result": result,
                });
                // The worker owns sanitation; when it reports what it did, the
                // flag is hoisted onto the step so the critic's artifact
                // checklist sees it. When it does not, the checklist honestly
                // says "written as produced".
                if let Some(meta) = result.get("content_sanitize") {
                    step["content_sanitize"] = meta.clone();
                }
                ctx.transcript.push(step);
                self.emit_step(
                    "execute",
                    "tool",
                    &[
                        ("action", json!(name)),
                        ("ok", json!(true)),
                        ("step", json!(step_index)),
                        ("path", path_detail),
                    ],
                );
            }
            ToolOutcome::Error(error) => {
                ctx.trace.tool("execute", name, "error", Some(risk));
                ctx.transcript.push(json!({
                    "state": AgentState::Executing.as_str(),
                    "action": name, "thoughts": thoughts,
                    "args": Value::Object(args.clone()),
                    "risk": risk, "governance": policy, "error": error,
                }));
                self.emit_step(
                    "execute",
                    "tool",
                    &[
                        ("action", json!(name)),
                        ("ok", json!(false)),
                        ("step", json!(step_index)),
                        ("path", path_detail),
                    ],
                );
            }
        }
    }

    /// `ToolDispatchService.snapshot_file`, natively.
    pub(super) fn snapshot_file(&self, path: &str) -> Value {
        let Ok(target) = self.deps.workspace.resolve(path) else {
            return json!({
                "existed": false, "content": null, "too_large": false,
                "error": "path escapes the agent workspace",
            });
        };
        let Ok(metadata) = std::fs::metadata(&target) else {
            return json!({"existed": false, "content": null, "too_large": false});
        };
        if !metadata.is_file() {
            return json!({"existed": false, "content": null, "too_large": false});
        }
        if metadata.len() > SNAPSHOT_MAX_BYTES {
            return json!({"existed": true, "content": null, "too_large": true});
        }
        match std::fs::read(&target) {
            // `errors="replace"`: an unreadable byte is a replacement character,
            // not a refused snapshot.
            Ok(bytes) => json!({
                "existed": true,
                "content": String::from_utf8_lossy(&bytes),
                "too_large": false,
            }),
            Err(error) => json!({
                "existed": true, "content": null, "too_large": true,
                "error": error.to_string(),
            }),
        }
    }

    /// `ToolDispatchService.restore_snapshot`: rewrite prior content, or delete
    /// a file the run created (`content = None`).
    pub(super) fn restore_snapshot(&self, path: &str, content: Option<&str>) -> Value {
        let Ok(target) = self.deps.workspace.resolve(path) else {
            return json!({"path": path, "ok": false, "error": "path escapes the agent workspace"});
        };
        let outcome = match content {
            None => match std::fs::remove_file(&target) {
                Ok(()) => Ok("deleted"),
                // `missing_ok=True`: an absent file is a successful deletion.
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok("deleted"),
                Err(error) => Err(error),
            },
            Some(content) => target
                .parent()
                .map_or(Ok(()), std::fs::create_dir_all)
                .and_then(|()| std::fs::write(&target, content))
                .map(|()| "restored"),
        };
        match outcome {
            Ok(action) => json!({"path": path, "ok": true, "action": action}),
            Err(error) => json!({"path": path, "ok": false, "error": error.to_string()}),
        }
    }
}
