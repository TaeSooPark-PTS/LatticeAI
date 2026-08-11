//! When the tool-call protocol runs out: corrections, then the direct path.
//!
//! Two halves of `latticeai.core.agent.execution`'s answer to a model that
//! cannot hold the JSON contract. [`Runtime::note_parse_failure`] spends the
//! profile's slip budget and escalates the hint before it runs out;
//! [`Runtime::direct_file_path`] is the compact profile's escape hatch, and it
//! is the one place in the loop that stops asking for JSON entirely.
//!
//! Neither ever fabricates evidence. A hint that does not land, a plan with no
//! paths, a staged proposal or a tool error all leave the run to end exactly as
//! it would have.

use serde_json::{json, Map, Value};

use super::gates::Call;
use super::{RunRequest, Runtime};
use crate::inference::infer_file_target;
use crate::profile::AgentProfile;
use crate::pystr::{char_slice, is_truthy, py_str};
use crate::state::{AgentRunContext, AgentState};
use crate::worker::{Completion, WorkerError};

/// The fixed part of the correction hint a format slip earns.
pub(super) const FORMAT_HINT: &str = "Your last reply was not a single JSON action \
object. Reply with EXACTLY one JSON object like {\"thoughts\": \"...\", \"action\": \
\"tool_name\", \"args\": {...}} and nothing else.";

impl Runtime {
    /// Record one executor parse slip; `true` when the run should stop retrying.
    pub(super) fn note_parse_failure(
        &mut self,
        ctx: &mut AgentRunContext,
        raw: &str,
        error: &str,
        parse_failures: u32,
        profile: AgentProfile,
    ) -> bool {
        ctx.transcript.push(json!({
            "state": AgentState::Executing.as_str(),
            "action": "parse_error",
            "raw": char_slice(raw, 400),
            "error": error,
        }));
        if parse_failures >= profile.parse_failure_budget {
            ctx.trace.parse_error("execute", error, false);
            self.emit_step("execute", "parse_error", &[("recovered", json!(false))]);
            return true;
        }
        ctx.trace.parse_error("execute", error, true);
        self.emit_step("execute", "parse_error", &[("recovered", json!(true))]);
        let mut hint = FORMAT_HINT.to_string();
        if parse_failures >= profile.escalate_after {
            // Escalate: name the valid tools so the model stops inventing
            // action names or prose. The compact profile escalates earlier.
            hint = format!(
                "{hint} Valid action values are: {}, final. \
Use {{\"action\": \"final\", \"message\": \"...\"}} to finish.",
                self.deps.tool_names.join(", ")
            );
        }
        if !ctx.corrections.iter().any(|known| py_str(known) == hint) {
            ctx.corrections.push(json!(hint));
            ctx.trace.correction("execute", &hint);
        }
        false
    }

    /// Write the plan's file steps without asking the model for JSON.
    ///
    /// Deviation, stated rather than hidden: Python routes the content through
    /// `generate_file_content`, whose extract → validate → repair pipeline lives
    /// with the document generators in the worker. The native fallback asks the
    /// worker for content over `/agent/llm` and writes it over `/agent/tool`,
    /// where `sanitize_write_content` still applies — so the bytes that land are
    /// validated, but the `generation.repaired` flag is only as good as what the
    /// seam reports back.
    pub(super) async fn direct_file_path(
        &mut self,
        ctx: &mut AgentRunContext,
        req: &RunRequest,
        model_id: Option<&str>,
    ) -> Result<bool, WorkerError> {
        let mut planned: Vec<String> = Vec::new();
        for step in ctx.steps() {
            let action = step.get("action").and_then(Value::as_str).unwrap_or("");
            if !self.deps.file_create_actions.contains(action) {
                continue;
            }
            let path = step
                .get("args")
                .and_then(|args| args.get("path"))
                .filter(|value| is_truthy(value))
                .map(py_str)
                .unwrap_or_default();
            let path = path.trim().to_string();
            if !path.is_empty() && !planned.contains(&path) {
                planned.push(path);
            }
        }
        if planned.is_empty() {
            if let Some(inferred) = infer_file_target(&req.message) {
                planned.push(inferred);
            }
        }
        if planned.is_empty() {
            return Ok(false);
        }
        let goal = match ctx.plan.get("goal") {
            Some(value) if is_truthy(value) => py_str(value),
            _ => req.message.clone(),
        };

        let mut wrote = false;
        for path in planned.iter().take(6) {
            let context = format!(
                "Write the complete contents of {path} for this request, and nothing else \
(no prose, no code fences).\n\nRequest: {goal}"
            );
            let content = self
                .deps
                .worker
                .llm(Completion {
                    model_id,
                    message: "Write the file content.",
                    context: &context,
                    max_tokens: self.deps.phase_budgets.execute_tokens,
                    temperature: 0.2,
                })
                .await?;
            ctx.trace.llm_call("execute", model_id);
            ctx.trace
                .repair("execute", &["direct_path_fallback".to_string()]);
            let mut args = Map::new();
            args.insert("path".into(), json!(path));
            args.insert("content".into(), json!(content));
            let policy = self.deps.policies.policy_for("write_file", &args);
            let before = ctx.transcript.len();
            self.dispatch_step(
                ctx,
                req,
                Call {
                    name: "write_file",
                    thoughts: "direct path fallback",
                    args: &args,
                    policy: &policy,
                },
            )
            .await;
            if ctx.transcript.len() <= before {
                continue;
            }
            let last = ctx.transcript.last_mut().expect("just appended");
            let applied = last
                .get("result")
                .filter(|result| result.is_object())
                .is_some_and(|result| !result.get("proposed").is_some_and(is_truthy));
            if applied {
                wrote = true;
                last["direct_path"] = json!(true);
                let repaired = last
                    .get("content_sanitize")
                    .and_then(|meta| meta.get("repaired"))
                    .is_some_and(is_truthy);
                last["generation"] = json!({"repaired": repaired});
            }
        }
        if wrote {
            ctx.trace.decision(
                "execute",
                "direct_path_fallback",
                &[("files", json!(planned.len()))],
            );
            self.emit_step("execute", "direct_path", &[("files", json!(planned.len()))]);
            ctx.final_message = "도구 호출 형식을 계속 벗어나서, 계획에 있던 파일을 직접 \
생성했습니다. 내용을 확인해 주세요."
                .into();
        }
        Ok(wrote)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::agentloop::harness::harness;

    const FINAL: &str = r#"{"action": "final", "message": "done"}"#;

    #[tokio::test]
    async fn the_compact_fallback_writes_the_planned_files_without_json() {
        let mut harness = harness(&["prose", "prose", "prose", "prose", "# the file body"]).await;
        harness.runtime.deps.agent_profile = Some(crate::profile::COMPACT);
        harness.request.permission_mode = Some("trusted".into());
        let mut ctx = harness.context();
        ctx.plan = json!({"goal": "make a note", "steps": [
            {"action": "write_file", "args": {"path": "note.md"}}
        ]})
        .as_object()
        .expect("plan")
        .clone();
        harness
            .runtime
            .execute(&mut ctx, &harness.request)
            .await
            .expect("execute");
        assert_eq!(ctx.state, AgentState::Verifying);
        assert_eq!(
            std::fs::read_to_string(harness.root.join("note.md")).expect("file"),
            "# the file body"
        );
        let written = ctx.transcript.last().expect("step");
        assert_eq!(written["direct_path"], true);
        assert_eq!(written["generation"], json!({"repaired": false}));
        assert!(ctx.final_message.contains("직접 생성했습니다"));
    }

    #[tokio::test]
    async fn the_fallback_writes_nothing_when_the_plan_names_nothing() {
        let mut harness = harness(&["prose", "prose", "prose", "prose"]).await;
        harness.runtime.deps.agent_profile = Some(crate::profile::COMPACT);
        let mut ctx = harness.context();
        harness
            .runtime
            .execute(&mut ctx, &harness.request)
            .await
            .expect("execute");
        assert!(
            harness.tool_calls().is_empty(),
            "no path, no fabricated write"
        );
        assert_eq!(ctx.final_message, "");
    }

    #[tokio::test]
    async fn parse_failures_burn_the_budget_and_escalate_the_hint() {
        let mut harness = harness(&["prose one", "prose two", "prose three", FINAL]).await;
        let mut ctx = harness.context();
        harness
            .runtime
            .execute(&mut ctx, &harness.request)
            .await
            .expect("execute");

        // standard profile: budget 3 → three parse_error steps, then the loop
        // breaks without reaching the fourth (valid) completion.
        assert_eq!(ctx.transcript.len(), 3);
        let raws: Vec<&str> = ctx
            .transcript
            .iter()
            .map(|step| {
                assert_eq!(step["action"], "parse_error");
                assert!(step["error"]
                    .as_str()
                    .expect("error")
                    .starts_with("Agent did not return valid JSON: "));
                step["raw"].as_str().expect("raw")
            })
            .collect();
        assert_eq!(raws, vec!["prose one", "prose two", "prose three"]);
        assert_eq!(
            ctx.state,
            AgentState::Verifying,
            "the run still gets verified"
        );
        // Two corrections: the plain hint, then the escalated one naming tools.
        assert_eq!(ctx.corrections.len(), 2);
        assert!(py_str(&ctx.corrections[0]).starts_with("Your last reply was not"));
        assert!(py_str(&ctx.corrections[1]).contains("read_file, write_file, final"));
        let summary = ctx.trace.summary();
        assert_eq!(summary["parse_errors"], 3);
        assert_eq!(
            summary["parse_recovered"], 2,
            "the last one is not recovered"
        );
    }
}
