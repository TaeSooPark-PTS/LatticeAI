use serde_json::{json, Map, Value};

use super::execution::{Chosen, StepFlow};
use super::gates::Call;
use super::{RunRequest, Runtime};
use crate::kernel::profile::AgentProfile;
use crate::kernel::state::{AgentRunContext, AgentState};
use crate::parse::inference::infer_file_target;
use crate::parse::pystr::{char_slice, is_truthy, py_str};
use crate::seam::worker::{Completion, WorkerError};
use crate::tools::catalog::EntryKind;

pub(super) const FORMAT_HINT: &str = "Your last reply was not a single JSON action \
object. Reply with EXACTLY one JSON object like {\"thoughts\": \"...\", \"action\": \
\"tool_name\", \"args\": {...}} and nothing else.";

pub(super) const REPEAT_HINT: &str = " You already sent exactly this reply and it was \
rejected for the same reason — do not repeat it. Send a different reply, starting at \
the opening brace.";

const DIRECT_PLAN_LIMIT: usize = 4;

const FINDING_CHARS: usize = 1_200;

const DIRECT_PLAN_THOUGHTS: &str = "direct plan path";

#[derive(Debug, Clone, Copy)]
pub(super) struct ParseSlip {
    pub failures: u32,
    pub budget: u32,
    pub repeated: bool,
}

impl Runtime {
    pub(super) fn note_parse_failure(
        &mut self,
        ctx: &mut AgentRunContext,
        raw: &str,
        error: &str,
        slip: ParseSlip,
        profile: AgentProfile,
    ) -> bool {
        let mut step = json!({
            "state": AgentState::Executing.as_str(),
            "action": "parse_error",
            "raw": char_slice(raw, 400),
            "error": error,
        });
        if slip.repeated {
            step["repeated"] = json!(true);
        }
        ctx.transcript.push(step);
        if slip.failures >= slip.budget {
            ctx.trace.parse_error("execute", error, false);
            self.emit_step("execute", "parse_error", &[("recovered", json!(false))]);
            return true;
        }
        ctx.trace.parse_error("execute", error, true);
        self.emit_step("execute", "parse_error", &[("recovered", json!(true))]);
        let mut hint = FORMAT_HINT.to_string();
        if slip.failures >= profile.escalate_after {
            hint = format!(
                "{hint} Valid action values are: {}. \
Use {{\"action\": \"final\", \"message\": \"...\"}} to finish.",
                crate::prompts::action_list(&self.prompt_action_names("")).join(", ")
            );
        }
        if slip.repeated {
            hint.push_str(REPEAT_HINT);
        }
        if !ctx.corrections.iter().any(|known| py_str(known) == hint) {
            ctx.corrections.push(json!(hint));
            ctx.trace.correction("execute", &hint);
        }
        false
    }

    pub(super) fn pending_plan_paths(&self, ctx: &AgentRunContext) -> Vec<String> {
        let written = crate::kernel::transcript::files_written(
            &ctx.transcript,
            &self.deps.file_create_actions,
        );
        let mut pending: Vec<String> = Vec::new();
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
            if path.is_empty() || pending.contains(&path) {
                continue;
            }
            let name = crate::kernel::transcript::path_name(&path);
            if written
                .iter()
                .any(|done| done == &path || crate::kernel::transcript::path_name(done) == name)
            {
                continue;
            }
            pending.push(path);
        }
        pending
    }

    pub(super) fn fence_rescue(
        &self,
        ctx: &AgentRunContext,
        raw: &str,
    ) -> Option<Map<String, Value>> {
        let (language, body) = fenced_code(raw)?;
        if body.trim_start().starts_with('{') {
            return None;
        }
        let pending = self.pending_plan_paths(ctx);
        let path = match fence_extensions(&language) {
            Some(extensions) => pending.into_iter().find(|path| {
                extensions.contains(&crate::content::sanitize::ext_of(path).as_str())
            })?,
            None => pending.into_iter().next()?,
        };
        let mut args = Map::new();
        args.insert("path".into(), json!(path));
        args.insert("content".into(), json!(body));
        let mut action = Map::new();
        action.insert(
            "thoughts".into(),
            json!("recovered from a fenced code block"),
        );
        action.insert("action".into(), json!("write_file"));
        action.insert("args".into(), Value::Object(args));
        Some(action)
    }

    pub(super) async fn direct_fallback(
        &mut self,
        ctx: &mut AgentRunContext,
        req: &RunRequest,
        model_id: Option<&str>,
        profile: AgentProfile,
    ) -> Result<bool, WorkerError> {
        if !profile.direct_path_fallback {
            return Ok(false);
        }
        let ran = self.direct_plan_path(ctx, req).await;
        let wrote = self.direct_file_path(ctx, req, model_id, profile).await?;
        Ok(ran || wrote)
    }

    pub(super) async fn direct_plan_path(
        &mut self,
        ctx: &mut AgentRunContext,
        req: &RunRequest,
    ) -> bool {
        let calls = self.direct_plan_calls(ctx, req);
        if calls.is_empty() {
            return false;
        }
        let mut ran: Vec<String> = Vec::new();
        for (name, args) in calls {
            let before = ctx.transcript.len();
            let flow = self
                .perform_action(
                    ctx,
                    req,
                    Chosen {
                        name: &name,
                        thoughts: DIRECT_PLAN_THOUGHTS,
                        args,
                        final_message: None,
                    },
                )
                .await;
            if ctx.transcript.len() > before {
                let step = ctx.transcript.last_mut().expect("just appended");
                step["direct_plan"] = json!(true);
                let applied = step
                    .get("result")
                    .filter(|result| result.is_object())
                    .is_some_and(|result| !result.get("proposed").is_some_and(is_truthy));
                if applied {
                    ran.push(name.clone());
                }
            }
            if matches!(flow, StepFlow::Break | StepFlow::Finished) {
                break;
            }
        }
        if !ran.is_empty() {
            ctx.trace.decision(
                "execute",
                "direct_plan_path",
                &[("steps", json!(ran.len()))],
            );
            self.emit_step("execute", "direct_plan", &[("steps", json!(ran.len()))]);
            if ctx.final_message.trim().is_empty() {
                ctx.final_message = format!(
                    "도구 호출 형식을 계속 벗어나서, 계획에 있던 단계를 직접 실행했습니다: {}. \
결과를 확인해 주세요.",
                    ran.join(", ")
                );
            }
        }
        !ran.is_empty()
    }

    fn direct_plan_calls(
        &self,
        ctx: &AgentRunContext,
        req: &RunRequest,
    ) -> Vec<(String, Map<String, Value>)> {
        let catalog = self.run_catalog(&req.message);
        let mut calls: Vec<(String, Map<String, Value>)> = Vec::new();
        if let Some(skill) = catalog.iter().find(|entry| {
            entry.kind == EntryKind::Skill
                && super::guided::request_names(&req.message, entry)
                && !super::guided::action_succeeded(ctx, &entry.name)
        }) {
            calls.push((skill.name.clone(), Map::new()));
        }
        let failed: std::collections::BTreeSet<String> = ctx
            .transcript
            .iter()
            .filter(|step| step.get("error").is_some())
            .filter_map(|step| {
                let action = step.get("action").and_then(Value::as_str)?;
                let args = step.get("args").cloned().unwrap_or_else(|| json!({}));
                Some(format!("{action}|{args}"))
            })
            .collect();
        for step in ctx.steps() {
            let action = step
                .get("action")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .trim()
                .to_string();
            if action.is_empty() || action == "final" {
                continue;
            }
            if self.deps.file_create_actions.contains(&action) {
                continue;
            }
            let Some(entry) = crate::tools::catalog::entry_for(&catalog, &action) else {
                continue;
            };
            if super::guided::action_succeeded(ctx, &action) {
                continue;
            }
            if calls.iter().any(|(known, _)| known == &action) {
                continue;
            }
            let planned = step
                .get("args")
                .and_then(Value::as_object)
                .cloned()
                .unwrap_or_default();
            let mut args = planned.clone();
            crate::tools::catalog::adopt_named_args(entry, &planned, &mut args);
            crate::tools::catalog::fill_documented_defaults(entry, &mut args);
            if crate::tools::catalog::missing_required(entry, &args).is_some() {
                continue;
            }
            if failed.contains(&format!("{action}|{}", Value::Object(args.clone()))) {
                continue;
            }
            calls.push((action, args));
        }
        calls.truncate(DIRECT_PLAN_LIMIT);
        calls
    }

    fn run_findings(&self, ctx: &AgentRunContext) -> String {
        let rows: Vec<String> = ctx
            .transcript
            .iter()
            .filter(|step| {
                step.get("state").and_then(Value::as_str) == Some(AgentState::Executing.as_str())
            })
            .filter_map(|step| {
                let action = step.get("action").and_then(Value::as_str)?;
                let result = step.get("result").filter(|value| value.is_object())?;
                let rendered = serde_json::to_string(result).unwrap_or_default();
                let body = match result
                    .get("content")
                    .or_else(|| result.get("text"))
                    .and_then(Value::as_str)
                {
                    Some(text) if !text.trim().is_empty() => char_slice(text.trim(), FINDING_CHARS),
                    _ => char_slice(&rendered, 400),
                };
                let path = step
                    .get("args")
                    .and_then(|args| args.get("path"))
                    .and_then(Value::as_str)
                    .unwrap_or_default();
                Some(if path.is_empty() {
                    format!("[{action}]\n{body}")
                } else {
                    format!("[{action} {path}]\n{body}")
                })
            })
            .collect();
        if rows.is_empty() {
            String::new()
        } else {
            format!(
                "\n\nWhat this run already found (use it; do not invent \
anything it does not say):\n{}",
                rows.join("\n\n")
            )
        }
    }

    pub(super) async fn direct_file_path(
        &mut self,
        ctx: &mut AgentRunContext,
        req: &RunRequest,
        model_id: Option<&str>,
        profile: AgentProfile,
    ) -> Result<bool, WorkerError> {
        let mut planned = self.pending_plan_paths(ctx);
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
            let content = self
                .generate_direct_content(ctx, model_id, path, &goal, profile)
                .await?;
            if content.trim().is_empty() {
                ctx.trace.decision(
                    "execute",
                    "direct_path_empty_content",
                    &[("path", json!(path))],
                );
                continue;
            }
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

    async fn generate_direct_content(
        &mut self,
        ctx: &mut AgentRunContext,
        model_id: Option<&str>,
        path: &str,
        goal: &str,
        profile: AgentProfile,
    ) -> Result<String, WorkerError> {
        let mut feedback = String::new();
        let mut best: Option<(u8, usize, String)> = None;
        let mut seen: Vec<String> = Vec::new();
        let mut budget = profile.regeneration_retries;
        let mut escalations_left = 1u32;
        let mut attempt = 0u32;
        let findings = self.run_findings(ctx);
        loop {
            let context = format!(
                "Write the complete contents of {path} for this request, and nothing else \
(no prose, no code fences).\n\nRequest: {goal}{findings}{feedback}"
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
                    stop: &[],
                    prefix: "",
                })
                .await?;
            ctx.trace.llm_call("execute", model_id);
            let (_, meta) = crate::content::sanitize::sanitize_write_content(path, &content, goal);
            if !meta.repaired {
                return Ok(content);
            }
            let reason = meta.reason;
            let (tier, length) = crate::content::sanitize::salvage_score(&content, path);
            if best
                .as_ref()
                .is_none_or(|(best_tier, best_len, _)| (tier, length) > (*best_tier, *best_len))
            {
                best = Some((tier, length, content.clone()));
            }
            let fingerprint = content.trim().to_string();
            let repeated = !fingerprint.is_empty() && seen.contains(&fingerprint);
            seen.push(fingerprint);
            if repeated && escalations_left > 0 && attempt >= budget {
                escalations_left -= 1;
                budget += 1;
            }
            if attempt >= budget {
                break;
            }
            attempt += 1;
            ctx.trace.repair(
                "execute",
                &[if repeated {
                    "direct_path_repeated".to_string()
                } else {
                    "direct_path_regenerate".to_string()
                }],
            );
            feedback = if repeated {
                format!("\n\nYour last reply was rejected: {reason}.{REPEAT_HINT}")
            } else {
                format!(
                    "\n\nYour last reply was rejected: {reason}. Output the file itself, \
starting at its first character."
                )
            };
        }
        Ok(best.map(|(_, _, content)| content).unwrap_or_default())
    }
}

fn fenced_code(raw: &str) -> Option<(String, String)> {
    let start = raw.find("```")?;
    let after = &raw[start + 3..];
    let body_start = after.find('\n')? + 1;
    let language = after[..body_start].trim().to_lowercase();
    let body = &after[body_start..];
    let body = match body.find("```") {
        Some(end) => &body[..end],
        None => body,
    };
    let body = body.trim_matches(['\r', '\n']);
    if body.trim().is_empty() {
        None
    } else {
        Some((language, body.to_string()))
    }
}

fn fence_extensions(language: &str) -> Option<&'static [&'static str]> {
    Some(match language {
        "html" | "htm" => &[".html", ".htm"],
        "css" => &[".css"],
        "js" | "javascript" | "jsx" => &[".js", ".jsx", ".mjs"],
        "ts" | "typescript" | "tsx" => &[".ts", ".tsx"],
        "json" => &[".json"],
        "md" | "markdown" => &[".md", ".markdown"],
        "py" | "python" => &[".py"],
        "sh" | "bash" | "shell" => &[".sh"],
        "sql" => &[".sql"],
        "yaml" | "yml" => &[".yaml", ".yml"],
        _ => return None,
    })
}

#[cfg(test)]
mod tests;
