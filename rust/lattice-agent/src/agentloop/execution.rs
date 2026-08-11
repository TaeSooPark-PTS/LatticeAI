//! EXECUTE — one tool call at a time, through every gate, on the record.
//!
//! A port of `latticeai.core.agent.execution`. The longest phase and the only
//! one that changes anything, so it is also where all the governance lives (see
//! [`super::gates`]). The per-iteration order is the contract:
//!
//! budget → completion → parse (with its own failure budget and escalation) →
//! `final` → repeated-create guard → scoped-arg forcing → `clear_history` →
//! policy/risk → governor → gates → dispatch.
//!
//! The direct-path fallback at the bottom is the escape hatch for a model too
//! small to hold the tool-call protocol. It never fabricates evidence: no
//! planned paths, a staged proposal, or a tool error all leave the run to end
//! as it would have.

use serde_json::{json, Map, Value};

use super::gates::Call;
use super::{RunRequest, Runtime};
use crate::action::extract_action_details;
use crate::profile::AgentProfile;
use crate::pystr::{char_slice, is_truthy, py_str, py_str_or_empty};
use crate::state::{AgentRunContext, AgentState};
use crate::transcript::{compact_transcript, files_written};
use crate::worker::{Completion, ToolOutcome, WorkerError};

impl Runtime {
    /// EXECUTE: the executor role calls tools one at a time until `final` or
    /// the budget is exhausted.
    pub async fn execute(
        &mut self,
        ctx: &mut AgentRunContext,
        req: &RunRequest,
    ) -> Result<(), WorkerError> {
        let model_id = ctx.executing_model.clone().or(req.executing_model.clone());
        let profile = self.profile(model_id.as_deref());
        let executed = ctx
            .transcript
            .iter()
            .filter(|step| {
                step.get("state").and_then(Value::as_str) == Some(AgentState::Executing.as_str())
            })
            .count() as u32;
        let budget = req.max_steps.saturating_sub(executed).max(1);
        let mut parse_failures = 0u32;

        for _ in 0..budget {
            let context = self.executor_context(ctx, req, profile);
            let raw = self
                .deps
                .worker
                .llm(Completion {
                    model_id: model_id.as_deref(),
                    message: "Execute the next step.",
                    context: &context,
                    max_tokens: self.deps.phase_budgets.execute_tokens,
                    temperature: req.temperature,
                })
                .await?;
            ctx.trace.llm_call("execute", model_id.as_deref());

            let action = match extract_action_details(&raw) {
                Ok((action, repairs)) => {
                    ctx.trace.repair("execute", &repairs);
                    action
                }
                Err(error) => {
                    parse_failures += 1;
                    if self.note_parse_failure(ctx, &raw, &error.0, parse_failures, profile) {
                        if profile.direct_path_fallback
                            && self.direct_file_path(ctx, req, model_id.as_deref()).await?
                        {
                            ctx.state = AgentState::Verifying;
                            return Ok(());
                        }
                        break;
                    }
                    continue;
                }
            };

            let name = py_str_or_empty(action.get("action"));
            let thoughts = char_slice(&py_str_or_empty(action.get("thoughts")), 600).to_string();
            let mut args: Map<String, Value> = action
                .get("args")
                .filter(|value| is_truthy(value))
                .and_then(Value::as_object)
                .cloned()
                .unwrap_or_default();

            if self.deps.scoped_knowledge_tools.contains(&name) {
                // Scope is server-owned, never model-owned. Overwrite any
                // claimed values before policy evaluation, audit and dispatch.
                args.insert(
                    "workspace_id".into(),
                    json!(req
                        .workspace_id
                        .clone()
                        .unwrap_or_else(|| "personal".into())),
                );
                args.insert(
                    "user_email".into(),
                    json!(req.user_email.clone().unwrap_or_else(|| "local".into())),
                );
            }

            if name == "final" {
                ctx.final_message = match action.get("message") {
                    None => "작업을 완료했습니다.".into(),
                    Some(message) => py_str_or_empty(Some(message)),
                };
                ctx.transcript.push(json!({
                    "state": AgentState::Executing.as_str(),
                    "action": "final", "thoughts": thoughts,
                }));
                ctx.trace.decision("execute", "final", &[]);
                self.emit_step("execute", "final", &[]);
                ctx.state = AgentState::Verifying;
                return Ok(());
            }

            if self.is_repeated_create(ctx, &name, &args) {
                ctx.transcript.push(json!({
                    "state": AgentState::Executing.as_str(),
                    "action": name,
                    "error": "LOOP_DETECTED: identical action+args repeated — halted.",
                }));
                ctx.trace
                    .decision("execute", "loop_detected", &[("tool", json!(name))]);
                self.emit_step(
                    "execute",
                    "blocked",
                    &[("action", json!(name)), ("reason", json!("loop_detected"))],
                );
                break;
            }

            if name == "clear_history" {
                let keep_last = args.get("keep_last").cloned().unwrap_or(json!(0));
                let mut call = Map::new();
                call.insert("keep_last".into(), keep_last);
                let outcome = self
                    .deps
                    .worker
                    .tool("clear_history", &call, req.workspace_id.as_deref())
                    .await;
                let mut step = json!({
                    "state": AgentState::Executing.as_str(),
                    "action": name, "thoughts": thoughts,
                    "args": Value::Object(args.clone()),
                });
                // Python calls the port directly and would raise on failure;
                // the seam reports refusals, and a refusal is recorded rather
                // than crashing the run.
                let ok = match outcome {
                    ToolOutcome::Result(result) => {
                        step["result"] = result;
                        true
                    }
                    ToolOutcome::Error(error) => {
                        step["error"] = json!(error);
                        false
                    }
                };
                ctx.transcript.push(step);
                self.emit_step(
                    "execute",
                    "tool",
                    &[("action", json!(name)), ("ok", json!(ok))],
                );
                continue;
            }

            let policy = self.deps.policies.policy_for(&name, &args);
            let call = Call {
                name: &name,
                thoughts: &thoughts,
                args: &args,
                policy: &policy,
            };
            let (proposed, allows_additive) = self.governor_review(ctx, req, call).await;
            if proposed {
                continue;
            }
            if self.blocked_by_gates(ctx, req, call, allows_additive) {
                continue;
            }
            self.dispatch_step(ctx, req, call).await;
        }

        ctx.state = AgentState::Verifying;
        Ok(())
    }

    /// Assemble one executor turn's prompt.
    ///
    /// `executor_prompt_for`'s profile-aware composition stays in the worker,
    /// which owns the prompt library; what the loop contributes — the plan, the
    /// files this run has already written, the latest corrections, the bounded
    /// transcript — is assembled here, in the original's order.
    fn executor_context(
        &self,
        ctx: &AgentRunContext,
        req: &RunRequest,
        profile: AgentProfile,
    ) -> String {
        // Only the latest corrections steer the next attempt; stale hints from
        // earlier retries dilute weak models.
        let active: Vec<String> = ctx
            .corrections
            .iter()
            .rev()
            .take(3)
            .rev()
            .map(py_str)
            .collect();
        let corrections_hint = if active.is_empty() {
            String::new()
        } else {
            format!(
                "\n\nCritic corrections from previous attempt:\n{}",
                active
                    .iter()
                    .map(|hint| format!("- {hint}"))
                    .collect::<Vec<_>>()
                    .join("\n")
            )
        };
        let recent = req
            .recent_conversation
            .as_deref()
            .filter(|text| !text.is_empty())
            .unwrap_or("(none)");
        let budget = self.deps.transcript_budget;
        let window = budget.window.min(profile.transcript_window);
        let bounded = compact_transcript(&ctx.transcript, window, budget.result_chars);
        let written = files_written(&ctx.transcript, &self.deps.file_create_actions);
        let written_hint = if written.is_empty() {
            String::new()
        } else {
            format!(
                "\n\nFiles written by this run so far (they exist in the workspace now):\n{}",
                written
                    .iter()
                    .map(|path| format!("- {path}"))
                    .collect::<Vec<_>>()
                    .join("\n")
            )
        };
        format!(
            "{}\n\n[LANGUAGE HINT: {}]\nWorkspace root: {}{}\n\nPLAN:\n{}{}\n\n\
Recent conversation:\n{}\n\nUser request: {}{}\n\nExecution transcript:\n{}",
            self.deps.prompts.executor,
            req.language_hint,
            self.deps.workspace.root().display(),
            self.project_block(ctx),
            serde_json::to_string(&Value::Object(ctx.plan.clone())).unwrap_or_default(),
            written_hint,
            recent,
            req.message,
            corrections_hint,
            serde_json::to_string_pretty(&Value::Array(bounded)).unwrap_or_default(),
        )
    }

    /// Loop guard: the same file-create action+args re-issued right after a result.
    fn is_repeated_create(
        &self,
        ctx: &AgentRunContext,
        name: &str,
        args: &Map<String, Value>,
    ) -> bool {
        if !self.deps.file_create_actions.contains(name) {
            return false;
        }
        let Some(last) = ctx.transcript.iter().rev().find(|step| {
            step.get("state").and_then(Value::as_str) == Some(AgentState::Executing.as_str())
        }) else {
            return false;
        };
        last.get("action").and_then(Value::as_str) == Some(name)
            && last
                .get("args")
                .filter(|value| is_truthy(value))
                .cloned()
                .unwrap_or(json!({}))
                == Value::Object(args.clone())
            && last.get("result").is_some()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::agentloop::harness::harness;
    use crate::policy::ToolPolicy;

    fn write_action(path: &str, content: &str) -> String {
        json!({"thoughts": "writing", "action": "write_file",
               "args": {"path": path, "content": content}})
        .to_string()
    }

    pub(super) const FINAL: &str = r#"{"action": "final", "message": "done"}"#;

    #[tokio::test]
    async fn a_write_then_final_is_two_steps_and_a_real_file() {
        let mut harness = harness(&[&write_action("note.md", "hello"), FINAL]).await;
        harness.request.permission_mode = Some("trusted".into());
        let mut ctx = harness.context();
        ctx.state = AgentState::Executing;
        harness
            .runtime
            .execute(&mut ctx, &harness.request)
            .await
            .expect("execute");

        assert_eq!(ctx.state, AgentState::Verifying);
        assert_eq!(ctx.transcript.len(), 2);
        assert_eq!(ctx.transcript[0]["action"], "write_file");
        assert_eq!(ctx.transcript[0]["result"]["path"], "note.md");
        assert_eq!(ctx.transcript[0]["risk"], "medium");
        assert_eq!(ctx.transcript[0]["governance"]["risk"], "write");
        assert_eq!(ctx.transcript[1]["action"], "final");
        assert_eq!(ctx.final_message, "done");
        assert_eq!(
            std::fs::read_to_string(harness.root.join("note.md")).expect("file"),
            "hello"
        );
        // The pre-write snapshot recorded that the file did not exist yet.
        assert_eq!(
            ctx.rollback_log,
            vec![json!({"path": "note.md", "existed": false,
                                                 "content": null, "too_large": false})]
        );
    }

    #[tokio::test]
    async fn the_budget_is_what_is_left_of_max_steps_not_max_steps() {
        // One executing step already on the transcript, max_steps 2 → one turn.
        let mut harness = harness(&[&write_action("a.md", "1"), &write_action("b.md", "2")]).await;
        harness.request.permission_mode = Some("trusted".into());
        harness.request.max_steps = 2;
        let mut ctx = harness.context();
        ctx.transcript
            .push(json!({"state": "EXECUTING", "action": "read_file",
                                   "result": {"ok": true}}));
        harness
            .runtime
            .execute(&mut ctx, &harness.request)
            .await
            .expect("execute");
        assert_eq!(harness.tool_calls().len(), 1, "one turn of budget was left");
        assert_eq!(ctx.state, AgentState::Verifying);
    }

    #[tokio::test]
    async fn the_budget_never_drops_below_one_turn() {
        let mut harness = harness(&[FINAL]).await;
        harness.request.max_steps = 1;
        let mut ctx = harness.context();
        for _ in 0..5 {
            ctx.transcript
                .push(json!({"state": "EXECUTING", "action": "read_file"}));
        }
        harness
            .runtime
            .execute(&mut ctx, &harness.request)
            .await
            .expect("execute");
        assert_eq!(ctx.final_message, "done", "one turn still ran");
    }

    #[tokio::test]
    async fn the_repeated_create_guard_halts_on_an_identical_reissue() {
        let action = write_action("a.md", "same");
        let mut harness = harness(&[&action, &action, FINAL]).await;
        harness.request.permission_mode = Some("trusted".into());
        let mut ctx = harness.context();
        harness
            .runtime
            .execute(&mut ctx, &harness.request)
            .await
            .expect("execute");
        assert_eq!(ctx.transcript.len(), 2);
        assert_eq!(
            ctx.transcript[1]["error"],
            "LOOP_DETECTED: identical action+args repeated — halted."
        );
        assert_eq!(harness.tool_calls().len(), 1, "the second write never ran");
        assert_eq!(ctx.state, AgentState::Verifying);
    }

    #[tokio::test]
    async fn a_different_payload_is_not_a_repeat() {
        let mut harness = harness(&[
            &write_action("a.md", "one"),
            &write_action("a.md", "two"),
            FINAL,
        ])
        .await;
        harness.request.permission_mode = Some("trusted".into());
        let mut ctx = harness.context();
        harness
            .runtime
            .execute(&mut ctx, &harness.request)
            .await
            .expect("execute");
        assert_eq!(harness.tool_calls().len(), 2);
        assert_eq!(
            ctx.rollback_log.len(),
            1,
            "the first snapshot per path wins"
        );
    }

    #[tokio::test]
    async fn scoped_knowledge_arguments_are_overwritten_by_the_server() {
        let mut harness = harness(&[&json!({"action": "knowledge_save",
             "args": {"content": "x", "workspace_id": "someone-elses",
                      "user_email": "attacker@example.com"}})
        .to_string()])
        .await;
        harness.request.permission_mode = Some("bypass".into());
        harness.request.workspace_id = Some("mine".into());
        harness.request.user_email = Some("owner@example.com".into());
        let mut ctx = harness.context();
        harness
            .runtime
            .execute(&mut ctx, &harness.request)
            .await
            .expect("execute");
        let sent = &harness.tool_calls()[0];
        assert_eq!(sent["args"]["workspace_id"], "mine");
        assert_eq!(sent["args"]["user_email"], "owner@example.com");
    }

    #[tokio::test]
    async fn scoped_arguments_fall_back_to_personal_and_local() {
        let mut harness =
            harness(&[&json!({"action": "knowledge_search", "args": {}}).to_string()]).await;
        harness.request.permission_mode = Some("bypass".into());
        let mut ctx = harness.context();
        harness
            .runtime
            .execute(&mut ctx, &harness.request)
            .await
            .expect("execute");
        let sent = &harness.tool_calls()[0];
        assert_eq!(sent["args"]["workspace_id"], "personal");
        assert_eq!(sent["args"]["user_email"], "local");
    }

    #[tokio::test]
    async fn clear_history_goes_through_the_seam_with_only_keep_last() {
        let mut harness = harness(&[
            &json!({"action": "clear_history", "args": {"keep_last": 5}}).to_string(),
            FINAL,
        ])
        .await;
        let mut ctx = harness.context();
        harness
            .runtime
            .execute(&mut ctx, &harness.request)
            .await
            .expect("execute");
        assert_eq!(harness.tool_calls()[0]["args"], json!({"keep_last": 5}));
        assert_eq!(ctx.transcript[0]["action"], "clear_history");
        assert!(ctx.transcript[0]["result"].is_object());
        assert!(ctx.transcript[0].get("governance").is_none(), "no gate ran");
    }

    #[tokio::test]
    async fn a_final_with_no_message_gets_the_default_and_an_explicit_null_does_not() {
        let mut absent = harness(&[r#"{"action": "final"}"#]).await;
        let mut ctx = absent.context();
        absent
            .runtime
            .execute(&mut ctx, &absent.request)
            .await
            .expect("execute");
        assert_eq!(ctx.final_message, "작업을 완료했습니다.");

        let mut null = harness(&[r#"{"action": "final", "message": null}"#]).await;
        let mut ctx = null.context();
        null.runtime
            .execute(&mut ctx, &null.request)
            .await
            .expect("execute");
        assert_eq!(ctx.final_message, "", "a present null is not an absent key");
    }

    #[tokio::test]
    async fn a_seam_refusal_is_an_error_step_not_a_crash() {
        let mut harness = harness(&[&write_action("a.md", "x"), FINAL]).await;
        harness.request.permission_mode = Some("trusted".into());
        harness.worker.tool_bodies.lock().expect("lock").insert(
            "write_file".into(),
            json!({"error": "Path escapes the agent workspace."}),
        );
        let mut ctx = harness.context();
        harness
            .runtime
            .execute(&mut ctx, &harness.request)
            .await
            .expect("execute");
        assert_eq!(
            ctx.transcript[0]["error"],
            "Path escapes the agent workspace."
        );
        assert!(ctx.transcript[0].get("result").is_none());
        assert_eq!(ctx.trace.summary()["tool_outcomes"], json!({"error": 1}));
    }

    #[tokio::test]
    async fn a_destructive_policy_is_blocked_in_every_mode() {
        for mode in ["strict", "trusted", "bypass"] {
            let mut harness = harness(&[
                &json!({"action": "delete_file", "args": {"path": "a.md"}}).to_string(),
                FINAL,
            ])
            .await;
            harness.runtime.deps.policies.tools.insert(
                "delete_file".into(),
                ToolPolicy {
                    risk: "destructive".into(),
                    destructive: true,
                    ..ToolPolicy::default()
                },
            );
            harness.request.permission_mode = Some(mode.into());
            let mut ctx = harness.context();
            harness
                .runtime
                .execute(&mut ctx, &harness.request)
                .await
                .expect("execute");
            assert_eq!(
                ctx.transcript[0]["error"], "BLOCKED: destructive action is always blocked",
                "mode {mode}"
            );
            assert_eq!(ctx.transcript[0]["permission_mode"], mode);
            assert!(
                harness.tool_calls().is_empty(),
                "mode {mode} must not dispatch"
            );
        }
    }

    #[tokio::test]
    async fn strict_blocks_an_ungoverned_write_at_the_approval_gate() {
        let mut harness = harness(&[
            &json!({"action": "run_command", "args": {"command": "ls"}}).to_string(),
            FINAL,
        ])
        .await;
        harness.runtime.deps.policies.tools.insert(
            "run_command".into(),
            ToolPolicy {
                risk: "exec".into(),
                ..ToolPolicy::default()
            },
        );
        let mut ctx = harness.context();
        harness
            .runtime
            .execute(&mut ctx, &harness.request)
            .await
            .expect("execute");
        assert_eq!(
            ctx.transcript[0]["error"],
            "BLOCKED: action 'run_command' requires explicit approval (mode=strict)."
        );
        assert!(harness.tool_calls().is_empty());
        assert_eq!(harness.runtime.audit[0]["event"], "agent_exec");
    }

    #[tokio::test]
    async fn an_unstageable_overwrite_fails_closed_even_under_bypass() {
        let mut harness = harness(&[
            &json!({"action": "create_docx", "args": {"filename": "report", "body": "x"}})
                .to_string(),
            FINAL,
        ])
        .await;
        std::fs::create_dir_all(harness.root.join("generated_documents")).expect("dir");
        std::fs::write(harness.root.join("generated_documents/report.docx"), b"old").expect("file");
        harness.runtime.deps.policies.tools.insert(
            "create_docx".into(),
            ToolPolicy {
                risk: "write".into(),
                ..ToolPolicy::default()
            },
        );
        harness.request.permission_mode = Some("bypass".into());
        let mut ctx = harness.context();
        harness
            .runtime
            .execute(&mut ctx, &harness.request)
            .await
            .expect("execute");
        let error = ctx.transcript[0]["error"].as_str().expect("error");
        assert!(error.starts_with("NEEDS_REVIEW: 'create_docx'"), "{error}");
        assert_eq!(ctx.transcript[0]["change_class"], "mutation");
        assert!(harness.tool_calls().is_empty(), "nothing was overwritten");
    }

    #[tokio::test]
    async fn strict_stages_a_governed_mutation_as_a_proposal() {
        let mut harness = harness(&[&write_action("a.md", "new"), FINAL]).await;
        std::fs::write(harness.root.join("a.md"), b"old").expect("file");
        *harness.worker.proposal.lock().expect("lock") = Some(json!({
            "decision": "proposed",
            "proposal": {"id": "prop-1"},
            "classification": {"change_class": "mutation"},
        }));
        let mut ctx = harness.context();
        harness
            .runtime
            .execute(&mut ctx, &harness.request)
            .await
            .expect("execute");
        let staged = &ctx.transcript[0];
        assert_eq!(staged["result"]["proposed"], true);
        assert_eq!(staged["result"]["proposal_id"], "prop-1");
        assert!(
            staged["args"].get("content").is_none(),
            "the payload is stripped"
        );
        assert_eq!(
            std::fs::read_to_string(harness.root.join("a.md")).expect("read"),
            "old"
        );
        assert_eq!(harness.runtime.audit[0]["event"], "agent_change_proposed");
        assert_eq!(harness.runtime.audit[0]["change_class"], "mutation");
    }

    #[tokio::test]
    async fn trusted_applies_a_governed_mutation_and_audits_it_instead() {
        let mut harness = harness(&[&write_action("a.md", "new"), FINAL]).await;
        std::fs::write(harness.root.join("a.md"), b"old").expect("file");
        harness.request.permission_mode = Some("trusted".into());
        let mut ctx = harness.context();
        harness
            .runtime
            .execute(&mut ctx, &harness.request)
            .await
            .expect("execute");
        assert_eq!(
            std::fs::read_to_string(harness.root.join("a.md")).expect("read"),
            "new"
        );
        assert_eq!(
            harness.runtime.audit[0]["event"],
            "agent_change_auto_applied"
        );
        // The governor was never consulted, so no orphan proposal was left.
        let proposals = harness
            .worker
            .calls
            .lock()
            .expect("lock")
            .iter()
            .filter(|call| call["seam"] == json!("proposal"))
            .count();
        assert_eq!(proposals, 0, "reviewing first would persist an orphan");
        // The snapshot captured the pre-run bytes, so rollback has something.
        assert_eq!(ctx.rollback_log[0]["content"], "old");
        assert_eq!(ctx.rollback_log[0]["existed"], true);
    }

    #[tokio::test]
    async fn a_write_to_a_blocked_prefix_is_rewritten_into_a_destructive_denial() {
        let mut harness = harness(&[
            &json!({"action": "write_file", "args": {"path": "/etc/hosts", "content": "x"}})
                .to_string(),
            FINAL,
        ])
        .await;
        harness.request.permission_mode = Some("bypass".into());
        let mut ctx = harness.context();
        harness
            .runtime
            .execute(&mut ctx, &harness.request)
            .await
            .expect("execute");
        assert_eq!(ctx.transcript[0]["governance"]["risk"], "destructive");
        assert!(ctx.transcript[0]["error"]
            .as_str()
            .expect("error")
            .starts_with("BLOCKED: "));
        assert!(harness.tool_calls().is_empty());
    }
}
