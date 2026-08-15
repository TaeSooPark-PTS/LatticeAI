//! The gates one executor step passes, in the order that decides it.
//!
//! Split out of [`super::execution`] because the order *is* the policy and it
//! deserves to be readable in one screen:
//!
//! 1. [`Runtime::governor_review`] — change-class governance, native since
//!    v11.6.0 §P1c ([`crate::proposals`]). Under a mode that does not stage
//!    proposals the decision is made **before** the governor is consulted,
//!    because reviewing persists a proposal as a side effect; reviewing first
//!    and discarding the verdict would apply the change *and* leave an orphan
//!    pending in the Review Center.
//! 2. [`Runtime::blocked_by_gates`] — circuit breaker, then destructive policy,
//!    then the fail-closed overwrite guard, then the approval gate. The first
//!    three are **mode-invariant**: `bypass` skips the approval prompt, it never
//!    removes an existence check or unlocks a breaker.
//! 3. [`Runtime::dispatch_step`] — pre-write snapshot, then execution: the
//!    native tool set for anything that mutates, the worker seam for the
//!    compute-only handlers. Recorded on the transcript either way.

use std::sync::Arc;

use serde_json::{json, Map, Value};

use super::{RunRequest, Runtime, SNAPSHOT_MAX_BYTES};
use crate::breaker::is_circuit_breaker;
use crate::governor::classify_tool_call;
use crate::mode::should_stage_proposal;
use crate::permission::block_reason_for_tool;
use crate::policy::{risk_level, ToolPolicy};
use crate::proposals::{Governor, Verdict};
use crate::pystr::{is_truthy, py_str};
use crate::state::{AgentRunContext, AgentState};
use crate::tools::{CallScope, NativeCall};
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

        // Staging is a blocking read (the base snapshot), a diff and a store
        // write, so it runs off the reactor — as the retired seam did with
        // `asyncio.to_thread`. The verdict is the whole answer; there is no
        // "the governor could not be reached" any more, because the governor
        // is this process.
        let workspace = self.deps.workspace.clone();
        let store = Arc::clone(&self.deps.proposals);
        let user_email = req.user_email.clone();
        let workspace_id = workspace_id.map(str::to_string);
        let conversation_id = req.conversation_id.clone();
        let tool = name.to_string();
        let call_args = args.clone();
        let call_policy = policy.clone();
        let staged = tokio::task::spawn_blocking(move || {
            Governor {
                workspace: &workspace,
                store: store.as_ref(),
                user_email: user_email.as_deref(),
                workspace_id: workspace_id.as_deref(),
                conversation_id: conversation_id.as_deref(),
            }
            .review(&tool, &call_args, &call_policy)
        })
        .await;
        let verdict = match staged {
            Ok(verdict) => verdict,
            Err(join) => Verdict::Failed(format!("change proposal staging task failed: {join}")),
        };

        let risk = risk_level(policy);
        match verdict {
            Verdict::Silent => (false, false),
            Verdict::AllowAdditive(_) => (false, true),
            Verdict::Proposed {
                classification,
                proposal,
            } => {
                let proposal_id = proposal.get("id").cloned().unwrap_or(Value::Null);
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
                        "proposal_id": proposal_id.clone(),
                        "note": "기존 내용을 바꾸는 작업이라 변경 제안으로 저장했습니다. \
                검토함에서 승인하면 적용됩니다.",
                    },
                }));
                self.audit(
                    "agent_change_proposed",
                    &[
                        ("user_email", json!(req.user_email)),
                        ("action", json!(name)),
                        ("proposal_id", proposal_id),
                        ("change_class", json!(classification.change_class)),
                    ],
                );
                (true, false)
            }
            // The change had to be reviewed and was not. Falling through to the
            // gates here would leave the run's only record of that a blocked
            // step with an unrelated reason, so it is its own error step.
            Verdict::Failed(detail) => {
                let error = format!(
                    "PROPOSAL_FAILED: 변경 제안을 저장하지 못해 '{name}' 을(를) 실행하지 \
않았습니다: {detail}"
                );
                ctx.trace.tool("execute", name, "error", Some(risk));
                ctx.transcript.push(json!({
                    "state": AgentState::Executing.as_str(),
                    "action": name,
                    "thoughts": thoughts,
                    "args": without_content(args),
                    "risk": risk,
                    "governance": policy,
                    "error": error,
                }));
                self.emit_step(
                    "execute",
                    "tool",
                    &[
                        ("action", json!(name)),
                        ("ok", json!(false)),
                        ("reason", json!("proposal_staging_failed")),
                    ],
                );
                self.audit(
                    "agent_change_proposal_failed",
                    &[
                        ("user_email", json!(req.user_email)),
                        ("action", json!(name)),
                        ("path", json!(path_arg(args))),
                        ("detail", json!(detail)),
                    ],
                );
                (true, false)
            }
        }
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

    /// Pre-write snapshot + execution (native, or the worker seam), recorded on
    /// the transcript either way.
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
        // ArtifactWritePipeline (`_dispatch_step`): the executor's `args.content`
        // is untrusted model output, so the same extract → validate → repair
        // guarantee as the direct chat path applies here and a weak model
        // driving the JSON loop can never persist a fenced, chatty or truncated
        // payload. Content that already validates is returned byte-for-byte, so
        // only a rewrite is recorded — an untouched write carries no
        // `content_sanitize` key at all, exactly as Python's does not.
        let mut cleaned_args: Option<Map<String, Value>> = None;
        let mut sanitize_meta: Option<Value> = None;
        if name == "write_file" {
            if let Some(content) = args.get("content").and_then(Value::as_str) {
                let target = path_arg(args).unwrap_or_default();
                // `str(ctx.plan.get("goal") or thoughts or name)`.
                let request = ctx
                    .plan
                    .get("goal")
                    .filter(|goal| is_truthy(goal))
                    .map(py_str)
                    .unwrap_or_else(|| {
                        if thoughts.is_empty() {
                            name.to_string()
                        } else {
                            thoughts.to_string()
                        }
                    });
                let (cleaned, meta) =
                    crate::sanitize::sanitize_write_content(&target, content, &request);
                if meta.sanitized {
                    let mut rewritten = args.clone();
                    rewritten.insert("content".into(), json!(cleaned));
                    cleaned_args = Some(rewritten);
                    sanitize_meta = Some(meta.to_value());
                    ctx.trace.repair(
                        "execute",
                        &[if meta.repaired {
                            "artifact_repair".to_string()
                        } else {
                            "artifact_sanitize".to_string()
                        }],
                    );
                }
            }
        }
        // Everything below reads the rewritten arguments, so the transcript
        // records the bytes that were actually written.
        let args: &Map<String, Value> = cleaned_args.as_ref().unwrap_or(args);
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

        // Native first: a mutating tool is executed here, in this process, with
        // the same workspace the snapshot above just read. Everything else is
        // still one `POST /agent/tool` to the compute worker.
        let outcome = if self.deps.native.handles(name) {
            let scope = CallScope {
                user_email: req.user_email.clone(),
                workspace_id: req.workspace_id.clone(),
            };
            self.deps
                .native
                .execute(NativeCall {
                    tool: name,
                    args,
                    // `check_role` reads the *registry* policy, not the
                    // argument-rewritten one — see `NativeCall::policy`.
                    policy: self.deps.policies.get(name),
                    scope: &scope,
                })
                .await
        } else {
            self.deps
                .worker
                .tool(name, args, req.workspace_id.as_deref())
                .await
        };
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
                // A worker handler that sanitizes reports it in its result; the
                // flag is hoisted onto the step so the critic's artifact
                // checklist sees it either way. The loop's own pass above wins,
                // because it is the pass that produced the bytes on disk.
                if let Some(meta) = result.get("content_sanitize") {
                    step["content_sanitize"] = meta.clone();
                }
                if let Some(meta) = &sanitize_meta {
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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::agentloop::{default_file_create_actions, harness::harness};
    use crate::runbody::collect_artifacts;
    use crate::transcript::artifact_checklist;

    const FINAL: &str = r#"{"action": "final", "message": "done"}"#;

    fn write_action(path: &str, content: &str) -> String {
        json!({"thoughts": "writing", "action": "write_file",
               "args": {"path": path, "content": content}})
        .to_string()
    }

    /// One scripted write, run through the loop, with what landed on disk.
    async fn wrote(path: &str, content: &str) -> (String, AgentRunContext, Value) {
        let mut harness = harness(&[&write_action(path, content), FINAL]).await;
        harness.request.permission_mode = Some("trusted".into());
        let mut ctx = harness.context();
        ctx.state = AgentState::Executing;
        harness
            .runtime
            .execute(&mut ctx, &harness.request)
            .await
            .expect("execute");
        let on_disk = std::fs::read_to_string(harness.root.join(path)).expect("file");
        let summary = ctx.trace.summary();
        (on_disk, ctx, summary)
    }

    #[tokio::test]
    async fn a_fenced_payload_is_cleaned_before_the_disk_and_the_artifacts_say_so() {
        let dirty = "Sure! Here you go:\n```html\n\
<!DOCTYPE html><html><body>ok</body></html>\n```\nLet me know!";
        let (on_disk, ctx, summary) = wrote("page.html", dirty).await;
        assert_eq!(
            on_disk, "<!DOCTYPE html><html><body>ok</body></html>",
            "the fences and the chat never reach the file"
        );
        let step = &ctx.transcript[0];
        assert_eq!(
            step["args"]["content"],
            json!(on_disk),
            "the transcript records the bytes that were written"
        );
        assert_eq!(step["content_sanitize"]["sanitized"], json!(true));
        assert_eq!(step["content_sanitize"]["repaired"], json!(false));
        assert_eq!(
            step["content_sanitize"]["reason"],
            json!("HTML document is wrapped in prose or fences")
        );
        assert_eq!(summary["repairs"]["artifact_sanitize"], json!(1));

        let actions = default_file_create_actions();
        let artifacts = collect_artifacts(&ctx.transcript, &actions);
        assert_eq!(
            artifacts[0]["repaired"],
            json!(false),
            "extraction is not a repair"
        );
        assert_eq!(artifacts[0]["previewable"], json!(true));
        let checklist = artifact_checklist(&ctx.transcript, &actions);
        assert_eq!(
            checklist,
            vec![json!({"path": "page.html", "sanitized": true, "repaired": false})],
            "the critic sees a real flag rather than a default"
        );
    }

    #[tokio::test]
    async fn a_truncated_document_is_repaired_and_the_artifact_is_flagged() {
        let truncated = "<!DOCTYPE html><html><head><title>t</title></head><body><p>hi</p>";
        let (on_disk, ctx, summary) = wrote("report.html", truncated).await;
        assert!(on_disk.ends_with("</body>\n</html>"), "{on_disk}");
        assert_eq!(
            ctx.transcript[0]["content_sanitize"]["repaired"],
            json!(true)
        );
        assert_eq!(summary["repairs"]["artifact_repair"], json!(1));
        let artifacts = collect_artifacts(&ctx.transcript, &default_file_create_actions());
        assert_eq!(
            artifacts[0]["repaired"],
            json!(true),
            "a deterministic scaffold is never presented as model output"
        );
    }

    #[tokio::test]
    async fn clean_content_is_written_verbatim_and_carries_no_sanitize_key() {
        // Python's FG-06b: an untouched write records nothing, so a UI that
        // badges `content_sanitize` badges only the writes that needed it.
        let clean = "<!DOCTYPE html><html><head><title>t</title></head><body>ok</body></html>";
        let (on_disk, ctx, summary) = wrote("page.html", clean).await;
        assert_eq!(on_disk, clean);
        assert!(ctx.transcript[0].get("content_sanitize").is_none());
        assert_eq!(summary["repairs"], json!({}));
        let artifacts = collect_artifacts(&ctx.transcript, &default_file_create_actions());
        assert_eq!(artifacts[0]["repaired"], json!(false));
    }

    #[tokio::test]
    async fn only_write_file_content_is_sanitized() {
        // Python sanitized `write_file` and nothing else: an `edit_file`
        // replacement is a diff against a file the user already has, not a
        // model's idea of a whole document.
        let mut harness = harness(&[
            &write_action("note.md", "# Title\n\nBody.\n"),
            &json!({"thoughts": "editing", "action": "edit_file",
                    "args": {"path": "note.md", "old_string": "Body.",
                             "new_string": "Sure! Here you go:"}})
            .to_string(),
            FINAL,
        ])
        .await;
        harness.request.permission_mode = Some("trusted".into());
        let mut ctx = harness.context();
        ctx.state = AgentState::Executing;
        harness
            .runtime
            .execute(&mut ctx, &harness.request)
            .await
            .expect("execute");
        assert_eq!(
            std::fs::read_to_string(harness.root.join("note.md")).expect("file"),
            "# Title\n\nSure! Here you go:\n"
        );
        assert!(ctx.transcript[1].get("content_sanitize").is_none());
    }
}
