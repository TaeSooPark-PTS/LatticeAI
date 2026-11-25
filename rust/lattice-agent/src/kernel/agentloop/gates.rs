use std::sync::Arc;

use serde_json::{json, Map, Value};

use super::{RunRequest, Runtime, SNAPSHOT_MAX_BYTES};
use crate::kernel::breaker::is_circuit_breaker;
use crate::kernel::governor::classify_tool_call;
use crate::kernel::mode::should_stage_proposal;
use crate::kernel::permission::block_reason_for_tool;
use crate::kernel::policy::{risk_level, ToolPolicy};
use crate::kernel::proposals::{Governor, Verdict};
use crate::kernel::state::{AgentRunContext, AgentState};
use crate::parse::pystr::{is_truthy, py_str};
use crate::seam::worker::ToolOutcome;
use crate::tools::{CallScope, NativeCall};

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

#[derive(Debug, Clone, Copy)]
pub(super) struct Call<'a> {
    pub name: &'a str,
    pub thoughts: &'a str,
    pub args: &'a Map<String, Value>,
    pub policy: &'a ToolPolicy,
}

impl Runtime {
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
        let mut cleaned_args: Option<Map<String, Value>> = None;
        let mut sanitize_meta: Option<Value> = None;
        if name == "write_file" {
            if let Some(content) = args.get("content").and_then(Value::as_str) {
                let target = path_arg(args).unwrap_or_default();
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
                    crate::content::sanitize::sanitize_write_content(&target, content, &request);
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

    pub(super) fn restore_snapshot(&self, path: &str, content: Option<&str>) -> Value {
        let Ok(target) = self.deps.workspace.resolve(path) else {
            return json!({"path": path, "ok": false, "error": "path escapes the agent workspace"});
        };
        let outcome = match content {
            None => match std::fs::remove_file(&target) {
                Ok(()) => Ok("deleted"),
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
    use crate::kernel::agentloop::{default_file_create_actions, harness::harness};
    use crate::kernel::transcript::artifact_checklist;
    use crate::surface::runbody::collect_artifacts;

    const FINAL: &str = r#"{"action": "final", "message": "done"}"#;

    fn write_action(path: &str, content: &str) -> String {
        json!({"thoughts": "writing", "action": "write_file",
               "args": {"path": path, "content": content}})
        .to_string()
    }

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
