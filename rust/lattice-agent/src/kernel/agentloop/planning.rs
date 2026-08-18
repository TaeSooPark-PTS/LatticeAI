//! PLAN and APPROVAL — deciding what to do, and whether it may be done.
//!
//! A port of `latticeai.core.agent.planning`. [`Runtime::approval_requirements`]
//! is the read-only half of exactly the predicate [`Runtime::approve`] enforces,
//! which is what lets the HTTP layer pause a run as `awaiting_approval` instead
//! of failing it closed — without weakening the gate.

use serde_json::{json, Map, Value};

use super::{RunRequest, Runtime};
use crate::kernel::mode::plan_requires_approval;
use crate::kernel::permission::{non_auto_plan_steps, PlanStep};
use crate::kernel::plan::normalize_plan;
use crate::kernel::state::{AgentRunContext, AgentState};
use crate::parse::action::extract_action_details;
use crate::parse::pystr::{char_slice, is_truthy, py_str};
use crate::surface::worker::{Completion, WorkerError};

impl Runtime {
    /// PLAN: the planner role produces a structured plan JSON.
    pub async fn plan(
        &mut self,
        ctx: &mut AgentRunContext,
        req: &RunRequest,
    ) -> Result<(), WorkerError> {
        let model_id = req.planning_model.as_deref();
        let context = format!(
            "{}\n\n[LANGUAGE HINT: {}]\nWorkspace root: {}{}\n\nUser request: {}",
            self.deps.prompts.planner_prompt(),
            req.language_hint,
            self.deps.workspace.root().display(),
            self.project_block(ctx),
            req.message,
        );
        let raw = self
            .deps
            .worker
            .llm(Completion {
                model_id,
                message: "Produce a JSON execution plan for this request.",
                context: &context,
                max_tokens: self.deps.phase_budgets.plan_tokens,
                temperature: 0.1,
                stop: &[],
                prefix: "",
            })
            .await?;
        ctx.trace.llm_call("plan", model_id);

        let parsed = match extract_action_details(&raw) {
            Ok((action, repairs)) => {
                ctx.trace.repair("plan", &repairs);
                Value::Object(action)
            }
            Err(error) => {
                // A planner that cannot answer in JSON does not stop the run —
                // the empty plan is normalised into something executable.
                ctx.trace.parse_error("plan", &error.0, true);
                json!({
                    "action": "plan", "state": "PLAN",
                    "goal": req.message, "steps": [],
                    "requires_approval": false, "rollback_strategy": "none",
                    "estimated_steps": 1,
                })
            }
        };

        let (plan, fixes) = normalize_plan(&parsed, &req.message);
        ctx.trace.repair("plan", &fixes);
        ctx.plan = plan;

        let field = |key: &str, fallback: Value| ctx.plan.get(key).cloned().unwrap_or(fallback);
        let mut step = Map::new();
        step.insert("state".into(), json!(AgentState::Planning.as_str()));
        step.insert("goal".into(), field("goal", json!(req.message)));
        step.insert("steps".into(), field("steps", json!([])));
        step.insert(
            "requires_approval".into(),
            field("requires_approval", json!(false)),
        );
        step.insert(
            "rollback_strategy".into(),
            field("rollback_strategy", json!("none")),
        );
        step.insert("estimated_steps".into(), field("estimated_steps", json!(1)));
        if !fixes.is_empty() {
            step.insert("plan_fixes".into(), json!(fixes));
        }
        ctx.transcript.push(Value::Object(step));

        let goal = match ctx.plan.get("goal") {
            Some(value) if is_truthy(value) => py_str(value),
            _ => String::new(),
        };
        self.emit_step(
            "plan",
            "planned",
            &[
                ("goal", json!(char_slice(&goal, 200))),
                ("steps", json!(ctx.steps().len())),
                (
                    "requires_approval",
                    json!(ctx.plan.get("requires_approval").is_some_and(is_truthy)),
                ),
            ],
        );
        ctx.state = AgentState::WaitingApproval;
        Ok(())
    }

    /// Read-only preview of the approval gate for a planned run.
    pub fn approval_requirements(&self, ctx: &AgentRunContext, req: &RunRequest) -> Value {
        let mode = self.resolve_permission_mode(ctx, req);
        let steps = ctx.steps();
        let plan_steps: Vec<PlanStep> = steps
            .iter()
            .map(|step| PlanStep {
                action: step
                    .get("action")
                    .filter(|value| is_truthy(value))
                    .map(py_str)
                    .unwrap_or_default(),
            })
            .collect();
        let governed: Vec<String> = if self.deps.governor_enabled {
            self.deps.governed_tools.iter().cloned().collect()
        } else {
            Vec::new()
        };
        let non_auto = non_auto_plan_steps(mode, &plan_steps, &self.deps.policies, &governed);
        let requires = plan_requires_approval(
            mode,
            &non_auto,
            ctx.plan.get("requires_approval").is_some_and(is_truthy),
        );

        let lines: Vec<String> = steps
            .iter()
            .enumerate()
            .map(|(index, step)| {
                let label = step
                    .get("description")
                    .filter(|value| is_truthy(value))
                    .or_else(|| step.get("action").filter(|value| is_truthy(value)))
                    .map(py_str)
                    .unwrap_or_else(|| "?".into());
                format!("{}. {label}", index + 1)
            })
            .collect();
        let goal = match ctx.plan.get("goal") {
            Some(value) if is_truthy(value) => py_str(value).trim().to_string(),
            _ => String::new(),
        };
        let summary = if lines.is_empty() {
            goal
        } else if goal.is_empty() {
            lines.join("\n")
        } else {
            format!("{goal}\n{}", lines.join("\n"))
        };

        json!({
            "requires_approval": requires,
            "non_auto_steps": non_auto,
            "permission_mode": mode.as_str(),
            "plan_summary": summary,
        })
    }

    /// APPROVAL: check governance, record the decision, and either proceed to
    /// EXECUTING or terminate the run as FAILED.
    pub fn approve(
        &mut self,
        ctx: &mut AgentRunContext,
        req: &RunRequest,
        approved_by_human: bool,
    ) {
        let requirements = self.approval_requirements(ctx, req);
        let non_auto = requirements["non_auto_steps"].clone();
        let requires = requirements["requires_approval"] == json!(true);
        let decision = if requires && approved_by_human {
            "human_approved"
        } else if requires {
            "blocked_pending_approval"
        } else {
            "auto_approved"
        };

        ctx.transcript.push(json!({
            "state": AgentState::WaitingApproval.as_str(),
            "requires_approval": requires,
            "non_auto_approve_steps": non_auto,
            "decision": decision,
        }));
        ctx.trace.decision(
            "approve",
            decision,
            &[(
                "non_auto_steps",
                json!(non_auto.as_array().map_or(0, Vec::len)),
            )],
        );
        self.emit_step("approval", "decision", &[("decision", json!(decision))]);
        self.audit(
            "agent_approval",
            &[
                ("user_email", json!(req.user_email)),
                ("requires_approval", json!(requires)),
                ("non_auto_steps", non_auto),
                ("decision", json!(decision)),
            ],
        );

        if requires && !approved_by_human {
            ctx.final_message = "이 작업에는 명시 승인이 필요한 도구가 포함되어 있어 자동 \
실행을 중단했습니다. human_in_loop 승인 흐름으로 다시 실행해 주세요."
                .into();
            ctx.state = AgentState::Failed;
            return;
        }
        ctx.approved_by_human = approved_by_human;
        ctx.state = AgentState::Executing;
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::kernel::agentloop::harness::harness;
    use crate::kernel::policy::ToolPolicy;

    #[tokio::test]
    async fn a_planner_answer_becomes_the_plan_step_and_waits_for_approval() {
        let mut harness = harness(&[r#"{"action": "plan", "goal": "write a note",
             "steps": [{"action": "write_file", "args": {"path": "a.md"},
                        "description": "make the note"}],
             "estimated_steps": 1}"#])
        .await;
        let mut ctx = harness.context();
        harness
            .runtime
            .plan(&mut ctx, &harness.request)
            .await
            .expect("plan");
        assert_eq!(ctx.state, AgentState::WaitingApproval);
        assert_eq!(ctx.goal(), "write a note");
        assert_eq!(ctx.transcript.len(), 1);
        assert_eq!(ctx.transcript[0]["state"], "PLANNING");
        assert_eq!(ctx.transcript[0]["estimated_steps"], 1);
        assert!(
            ctx.transcript[0].get("plan_fixes").is_none(),
            "nothing repaired"
        );
        assert_eq!(harness.runtime_llm_calls(&ctx), 1);
    }

    #[tokio::test]
    async fn an_unparseable_planner_still_produces_an_executable_plan() {
        let mut harness = harness(&["I think we should start by reading the notes."]).await;
        harness.request.message = "html 파일 만들어줘".into();
        let mut ctx = harness.context();
        harness
            .runtime
            .plan(&mut ctx, &harness.request)
            .await
            .expect("plan");
        // The parse failure is recovered by the fallback plan — whose goal is
        // already the request, so only the heuristic step is a repair.
        assert_eq!(
            ctx.transcript[0]["plan_fixes"],
            json!(["heuristic_file_step"])
        );
        assert_eq!(ctx.transcript[0]["goal"], "html 파일 만들어줘");
        assert_eq!(
            ctx.transcript[0]["steps"][0]["args"]["path"],
            "generated_page.html"
        );
        let kinds: Vec<&str> = ctx
            .trace
            .events
            .iter()
            .map(|event| event["kind"].as_str().expect("kind"))
            .collect();
        assert_eq!(kinds, vec!["llm_call", "parse_error", "repair"]);
    }

    #[tokio::test]
    async fn the_approval_preview_and_the_gate_agree() {
        let mut harness = harness(&[]).await;
        harness.runtime.deps.policies.tools.insert(
            "run_command".into(),
            ToolPolicy {
                risk: "exec".into(),
                shell: true,
                ..ToolPolicy::default()
            },
        );
        let mut ctx = harness.context();
        ctx.plan = json!({"goal": "ship it", "steps": [
            {"action": "run_command", "args": {"command": "ls"}, "description": "list"},
            {"action": "write_file", "args": {"path": "a.md"}}
        ]})
        .as_object()
        .expect("plan")
        .clone();

        let preview = harness
            .runtime
            .approval_requirements(&ctx, &harness.request);
        assert_eq!(preview["requires_approval"], true);
        assert_eq!(preview["non_auto_steps"], json!(["run_command"]));
        assert_eq!(preview["permission_mode"], "strict");
        assert_eq!(preview["plan_summary"], "ship it\n1. list\n2. write_file");

        harness.runtime.approve(&mut ctx, &harness.request, false);
        assert_eq!(ctx.state, AgentState::Failed);
        assert_eq!(
            ctx.transcript.last().expect("step")["decision"],
            "blocked_pending_approval"
        );
        assert!(ctx.final_message.contains("명시 승인"));
    }

    #[tokio::test]
    async fn a_human_approval_carries_the_run_into_executing() {
        let mut harness = harness(&[]).await;
        harness.runtime.deps.policies.tools.insert(
            "run_command".into(),
            ToolPolicy {
                risk: "exec".into(),
                ..ToolPolicy::default()
            },
        );
        let mut ctx = harness.context();
        ctx.plan = json!({"goal": "g", "steps": [{"action": "run_command"}]})
            .as_object()
            .expect("plan")
            .clone();
        harness.runtime.approve(&mut ctx, &harness.request, true);
        assert_eq!(ctx.state, AgentState::Executing);
        assert!(ctx.approved_by_human);
        assert_eq!(ctx.transcript[0]["decision"], "human_approved");
        assert_eq!(harness.runtime.audit.len(), 1);
        assert_eq!(harness.runtime.audit[0]["event"], "agent_approval");
    }

    #[tokio::test]
    async fn an_auto_approvable_plan_never_asks() {
        let mut harness = harness(&[]).await;
        let mut ctx = harness.context();
        ctx.permission_mode = Some("trusted".into());
        ctx.plan = json!({"goal": "g", "steps": [{"action": "write_file"}]})
            .as_object()
            .expect("plan")
            .clone();
        let preview = harness
            .runtime
            .approval_requirements(&ctx, &harness.request);
        assert_eq!(preview["requires_approval"], false);
        harness.runtime.approve(&mut ctx, &harness.request, false);
        assert_eq!(ctx.state, AgentState::Executing);
        assert!(
            !ctx.approved_by_human,
            "auto-approval is not a human approval"
        );
        assert_eq!(ctx.transcript[0]["decision"], "auto_approved");
    }

    #[tokio::test]
    async fn under_strict_a_governed_tool_is_decided_per_call_not_per_plan() {
        let mut harness = harness(&[]).await;
        let mut ctx = harness.context();
        ctx.plan = json!({"goal": "g", "steps": [{"action": "write_file"}]})
            .as_object()
            .expect("plan")
            .clone();
        let preview = harness
            .runtime
            .approval_requirements(&ctx, &harness.request);
        assert_eq!(
            preview["non_auto_steps"],
            json!([]),
            "governor tools are skipped"
        );
        assert_eq!(preview["requires_approval"], false);
        // With no governor wired, the same plan does block.
        harness.runtime.deps.governor_enabled = false;
        let preview = harness
            .runtime
            .approval_requirements(&ctx, &harness.request);
        assert_eq!(preview["non_auto_steps"], json!(["write_file"]));
    }

    #[tokio::test]
    async fn a_goal_less_plan_summary_is_just_the_numbered_steps() {
        let harness = harness(&[]).await;
        let mut ctx = harness.context();
        ctx.plan = json!({"steps": [{"action": "read_file"}]})
            .as_object()
            .expect("plan")
            .clone();
        let preview = harness
            .runtime
            .approval_requirements(&ctx, &harness.request);
        assert_eq!(preview["plan_summary"], "1. read_file");
        ctx.plan = json!({"goal": "just this"})
            .as_object()
            .expect("plan")
            .clone();
        let preview = harness
            .runtime
            .approval_requirements(&ctx, &harness.request);
        assert_eq!(preview["plan_summary"], "just this");
    }
}
