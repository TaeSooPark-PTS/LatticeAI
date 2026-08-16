//! When the tool-call protocol runs out: rescue, corrections, then the direct
//! path.
//!
//! Everything `latticeai.core.agent.execution` does with a model that cannot
//! hold the JSON contract, in the order the loop tries it:
//!
//! 1. [`Runtime::fence_rescue`] (v11.9.0) — the reply is not an action but it
//!    contains the *file* the plan is waiting for, so it becomes the write;
//! 2. [`Runtime::note_parse_failure`] spends the profile's slip budget,
//!    escalates the hint before it runs out, and lets a byte-identical repeat
//!    buy exactly one extra attempt with a prompt that names the repetition;
//! 3. [`Runtime::direct_file_path`] is the compact profile's escape hatch, and
//!    the one place in the loop that stops asking for JSON entirely.
//!
//! None of them ever fabricates evidence. A fence with no plan step to write it
//! to, a hint that does not land, a plan with no paths, a staged proposal or a
//! tool error all leave the run to end exactly as it would have — and every
//! write here goes through the same policy, gates and sanitize pass as one the
//! model asked for correctly.

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

/// The extra sentence a **byte-identical** rejected reply earns.
///
/// From the deleted `file_generation.orchestration`: a small model handed the
/// same corrective feedback replays the same reply verbatim, and the only
/// signal left that has any chance of moving it is saying so.
pub(super) const REPEAT_HINT: &str = " You already sent exactly this reply and it was \
rejected for the same reason — do not repeat it. Send a different reply, starting at \
the opening brace.";

/// One executor parse slip, with the run-level facts the response depends on.
#[derive(Debug, Clone, Copy)]
pub(super) struct ParseSlip {
    /// Slips so far, this one included.
    pub failures: u32,
    /// The budget in force, which a repeat may already have widened by one.
    pub budget: u32,
    /// Whether this reply is byte-identical to one already rejected.
    pub repeated: bool,
}

impl Runtime {
    /// Record one executor parse slip; `true` when the run should stop retrying.
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
            // Visible in the trace, so two rounds of the same reply do not read
            // as two genuine attempts.
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
            // Escalate: name the valid tools so the model stops inventing
            // action names or prose. The compact profile escalates earlier.
            //
            // Never an empty list: a run whose caller sent no policy table used
            // to be told `Valid action values are: , final.`, which names
            // nothing and reads as a bug the model then imitates.
            hint = format!(
                "{hint} Valid action values are: {}. \
Use {{\"action\": \"final\", \"message\": \"...\"}} to finish.",
                crate::prompts::action_list(&self.deps.tool_names).join(", ")
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

    /// The plan's file steps, in order, minus what this run already wrote.
    fn pending_plan_paths(&self, ctx: &AgentRunContext) -> Vec<String> {
        let written =
            crate::transcript::files_written(&ctx.transcript, &self.deps.file_create_actions);
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
            let name = crate::transcript::path_name(&path);
            if written
                .iter()
                .any(|done| done == &path || crate::transcript::path_name(done) == name)
            {
                continue;
            }
            pending.push(path);
        }
        pending
    }

    /// Turn a reply that fenced the *file* instead of the tool call into one.
    ///
    /// The ~2B failure this catches is not a format slip — it is the model
    /// doing the work and framing it as chat: `Here is your page:` followed by
    /// a ```html block holding the whole document. The parse chain cannot help
    /// (there is no JSON to find), and the ordinary answer is a `parse_error`
    /// step that throws a finished file away.
    ///
    /// Four conditions, all required, so this can never invent a write:
    /// the reply has a fenced block, the block is not itself JSON (which the
    /// parse chain owns), the **plan** names a file that has not been written
    /// yet, and — when the fence carries a language tag — that file is of the
    /// kind the fence holds. The last one is what stops the useful case from
    /// becoming a destructive one: a plan for `index.html` and `style.css`
    /// answered with a ```css block must not write the stylesheet into the
    /// page, where `sanitize_write_content` would then "repair" the CSS into an
    /// HTML scaffold and lose it. An untagged fence has nothing to disagree
    /// with, so it takes the first pending path.
    ///
    /// The synthesized call goes through [`Runtime::dispatch_step`] like any
    /// other — same policy, same gates, same sanitize pass — so nothing here is
    /// a shortcut past governance, only past the JSON.
    pub(super) fn fence_rescue(
        &self,
        ctx: &AgentRunContext,
        raw: &str,
    ) -> Option<Map<String, Value>> {
        let (language, body) = fenced_code(raw)?;
        if body.trim_start().starts_with('{') {
            // JSON in a fence is the parse chain's rung, not this one's.
            return None;
        }
        let pending = self.pending_plan_paths(ctx);
        let path = match fence_extensions(&language) {
            Some(extensions) => pending
                .into_iter()
                .find(|path| extensions.contains(&crate::sanitize::ext_of(path).as_str()))?,
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

    /// Write the plan's file steps without asking the model for JSON.
    ///
    /// Deviation, stated rather than hidden: Python routed the content through
    /// `generate_file_content`, whose generation *loop* lived with the document
    /// generators. v11.7.0 ported that pipeline's pure stages and left the loop
    /// behind; v11.9.0 brings back the two parts of it that were load-bearing
    /// for weak models, and only for the profile that needs them
    /// ([`AgentProfile::regeneration_retries`], zero on `standard`):
    ///
    /// * **candidate scoring.** Content that would have to be repaired is asked
    ///   for again, and the *better* of the two is written — better by
    ///   [`crate::sanitize::salvage_score`], which ranks a short real document
    ///   above a long apology.
    /// * **repeated-reply detection.** A byte-identical second answer is told
    ///   so, exactly as [`Runtime::note_parse_failure`] tells the executor.
    ///
    /// The write itself is unchanged: [`Runtime::dispatch_step`] runs the same
    /// extract → validate → repair pass ([`crate::sanitize::sanitize_write_content`])
    /// and `generation.repaired` stays this loop's own verdict.
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

    /// Ask the worker for one file's contents, spending the profile's
    /// regeneration budget on content that would otherwise be repaired.
    ///
    /// The first answer that **validates** is returned immediately — a run that
    /// gets it right the first time costs exactly one call, which is what
    /// `standard` (`regeneration_retries: 0`) always does. Otherwise the retry
    /// carries the reason the last answer was rejected, and the best candidate
    /// by [`crate::sanitize::salvage_score`] is what gets written.
    ///
    /// One call **beyond** the budget is spent, at most once per file, when the
    /// model has returned a byte-identical rejected reply — Python's rule, kept
    /// because the reason for it is unchanged: the corrective feedback did not
    /// move the model, so a third identical round trip is worth less than one
    /// prompt that says "you already sent this". A model that never repeats
    /// itself is never charged for it, and `standard` cannot reach the branch
    /// at all: the first reply has nothing to be identical to.
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
        loop {
            let context = format!(
                "Write the complete contents of {path} for this request, and nothing else \
(no prose, no code fences).\n\nRequest: {goal}{feedback}"
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
                })
                .await?;
            ctx.trace.llm_call("execute", model_id);
            // The predicate is `sanitize_write_content`'s own, run here rather
            // than re-derived: content it can *extract* into something valid
            // (a fenced document, a chat-wrapped one) is not content that has
            // to be repaired, and spending a regeneration on it would be a
            // second opinion about a question already answered. Only the
            // verdict is used — [`Runtime::dispatch_step`] does the real pass,
            // and its meta is what the transcript reports.
            let (_, meta) = crate::sanitize::sanitize_write_content(path, &content, goal);
            if !meta.repaired {
                return Ok(content);
            }
            let reason = meta.reason;
            let (tier, length) = crate::sanitize::salvage_score(&content, path);
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

/// The language tag and body of the first fenced code block, closed or not.
///
/// A reply that ran out of tokens mid-file has an opening fence and no closing
/// one, and that half-file is still the most useful thing in the reply — the
/// write-side validator will say whether it survived, and repair will finish it
/// if it did not.
fn fenced_code(raw: &str) -> Option<(String, String)> {
    let start = raw.find("```")?;
    let after = &raw[start + 3..];
    // The language tag is the rest of the opening line.
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

/// The file extensions a fence language tag claims, or `None` for a tag that
/// says nothing about the file type (including no tag at all).
///
/// Deliberately short: only the tags whose meaning is unambiguous. An unknown
/// tag is treated as no tag rather than as a mismatch, so a model writing
/// ```jinja into an `.html` plan step is not refused over vocabulary.
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
mod tests {
    use super::*;
    use crate::agentloop::harness::harness;

    const FINAL: &str = r#"{"action": "final", "message": "done"}"#;

    #[tokio::test]
    async fn the_compact_fallback_writes_the_planned_files_without_json() {
        // Four *different* prose replies: the budget is spent the ordinary way,
        // with no repeat escalation — that path has its own test below.
        let mut harness = harness(&[
            "prose one",
            "prose two",
            "prose three",
            "prose four",
            "# the file body",
        ])
        .await;
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
    async fn a_repeated_reply_buys_exactly_one_extra_attempt() {
        // The same rejected reply four times, then a valid action. Under the
        // ported budget the run stopped at the fourth and never saw the fifth;
        // the repeat escalation buys one more turn, and the run finishes.
        let mut harness = harness(&["prose", "prose", "prose", "prose", FINAL]).await;
        harness.runtime.deps.agent_profile = Some(crate::profile::COMPACT);
        let mut ctx = harness.context();
        harness
            .runtime
            .execute(&mut ctx, &harness.request)
            .await
            .expect("execute");
        assert_eq!(ctx.final_message, "done", "the fifth reply was reached");
        let slips: Vec<&Value> = ctx
            .transcript
            .iter()
            .filter(|step| step["action"] == json!("parse_error"))
            .collect();
        assert_eq!(slips.len(), 4, "budget 4, all spent");
        assert_eq!(
            slips
                .iter()
                .filter(|step| step.get("repeated").is_some())
                .count(),
            3,
            "the first sighting is not a repeat"
        );
        // The correction says so, and only once.
        let repeated_hints = ctx
            .corrections
            .iter()
            .filter(|hint| py_str(hint).contains("You already sent exactly this reply"))
            .count();
        assert_eq!(repeated_hints, 1);
    }

    #[tokio::test]
    async fn the_extra_attempt_is_bought_once_and_never_again() {
        // Nine identical replies against a budget of four. Without a ceiling
        // the escalation would renew itself forever; with one, the run stops
        // after five and falls through to the direct path.
        let mut harness = harness(&["prose"; 9]).await;
        harness.runtime.deps.agent_profile = Some(crate::profile::COMPACT);
        let mut ctx = harness.context();
        harness
            .runtime
            .execute(&mut ctx, &harness.request)
            .await
            .expect("execute");
        assert_eq!(
            ctx.transcript
                .iter()
                .filter(|step| step["action"] == json!("parse_error"))
                .count(),
            5,
            "four plus the one extra, and no more"
        );
    }

    #[tokio::test]
    async fn a_fenced_file_in_a_chat_reply_becomes_the_write_the_plan_asked_for() {
        let reply = "Here is your page:\n\n```html\n<!doctype html>\n<html><body>\
<h1>Hi</h1></body></html>\n```\n\nLet me know if you want changes.";
        let mut harness = harness(&[reply, FINAL]).await;
        harness.request.permission_mode = Some("trusted".into());
        let mut ctx = harness.context();
        ctx.plan = json!({"goal": "make a page", "steps": [
            {"action": "write_file", "args": {"path": "index.html"}}
        ]})
        .as_object()
        .expect("plan")
        .clone();
        harness
            .runtime
            .execute(&mut ctx, &harness.request)
            .await
            .expect("execute");
        assert_eq!(
            std::fs::read_to_string(harness.root.join("index.html")).expect("file"),
            "<!doctype html>\n<html><body><h1>Hi</h1></body></html>"
        );
        assert_eq!(ctx.transcript[0]["action"], "write_file");
        assert!(
            ctx.transcript
                .iter()
                .all(|step| step["action"] != json!("parse_error")),
            "a rescued reply is not a parse failure"
        );
        assert!(ctx
            .trace
            .events
            .iter()
            .any(|event| event["kind"] == json!("repair")
                && event["repairs"] == json!(["fence_rescue"])));
    }

    #[tokio::test]
    async fn the_fence_rescue_refuses_where_it_would_have_to_guess() {
        // No pending plan path: the fence may be an example, a snippet, an
        // apology in disguise. There is nothing to write it to, so nothing is
        // written — the reply is an ordinary parse failure.
        let reply = "Something like:\n\n```html\n<!doctype html>\n<html></html>\n```";
        let mut harness = harness(&[reply, reply, reply, FINAL]).await;
        let mut ctx = harness.context();
        harness
            .runtime
            .execute(&mut ctx, &harness.request)
            .await
            .expect("execute");
        assert!(harness.tool_calls().is_empty(), "nothing was written");
        assert_eq!(ctx.transcript[0]["action"], "parse_error");
    }

    #[tokio::test]
    async fn a_tagged_fence_is_written_to_the_file_of_its_own_kind() {
        // The plan wants a page and a stylesheet, in that order, and the model
        // answers with the stylesheet. Taking "the first pending path" would
        // put CSS into index.html, where the sanitize pass would then repair it
        // into an HTML scaffold and the stylesheet would be gone.
        let reply = "스타일은 이렇습니다:\n\n```css\nbody { color: red; }\n```";
        let mut harness = harness(&[reply, FINAL]).await;
        harness.request.permission_mode = Some("trusted".into());
        let mut ctx = harness.context();
        ctx.plan = json!({"goal": "g", "steps": [
            {"action": "write_file", "args": {"path": "index.html"}},
            {"action": "write_file", "args": {"path": "style.css"}}
        ]})
        .as_object()
        .expect("plan")
        .clone();
        harness
            .runtime
            .execute(&mut ctx, &harness.request)
            .await
            .expect("execute");
        assert_eq!(
            std::fs::read_to_string(harness.root.join("style.css")).expect("file"),
            "body { color: red; }"
        );
        assert!(
            !harness.root.join("index.html").exists(),
            "the page was not overwritten with a stylesheet"
        );
    }

    #[tokio::test]
    async fn a_tagged_fence_with_no_matching_plan_step_is_not_rescued() {
        let reply = "```python\nprint('hi')\n```";
        let mut harness = harness(&[reply, reply, reply, FINAL]).await;
        let mut ctx = harness.context();
        ctx.plan = json!({"goal": "g", "steps": [
            {"action": "write_file", "args": {"path": "index.html"}}
        ]})
        .as_object()
        .expect("plan")
        .clone();
        harness
            .runtime
            .execute(&mut ctx, &harness.request)
            .await
            .expect("execute");
        assert!(harness.tool_calls().is_empty(), "nothing was written");
        assert_eq!(ctx.transcript[0]["action"], "parse_error");
    }

    #[tokio::test]
    async fn a_plan_path_already_written_is_not_rescued_over() {
        let reply = "Here you go:\n\n```md\n# second thoughts\n```";
        let mut harness = harness(&[reply, reply, reply, FINAL]).await;
        let mut ctx = harness.context();
        ctx.plan = json!({"goal": "g", "steps": [
            {"action": "write_file", "args": {"path": "note.md"}}
        ]})
        .as_object()
        .expect("plan")
        .clone();
        ctx.transcript.push(json!({
            "state": "EXECUTING", "action": "write_file", "args": {"path": "note.md"},
            "result": {"path": "note.md", "bytes": 4},
        }));
        harness
            .runtime
            .execute(&mut ctx, &harness.request)
            .await
            .expect("execute");
        assert_eq!(
            ctx.transcript[1]["action"], "parse_error",
            "the only pending path was already written"
        );
    }

    #[tokio::test]
    async fn the_direct_path_regenerates_once_and_writes_the_better_candidate() {
        // Attempt one is an apology (tier 0), attempt two is a real document
        // that is merely unfinished (tier 2). Longest-wins would have written
        // the apology; the salvage score writes the document.
        let apology = "I'm sorry, but I can't create that file for you.";
        let document = "<!doctype html>\n<html><body><h1>Hi</h1></body>";
        let mut harness = harness(&[
            "prose one",
            "prose two",
            "prose three",
            "prose four",
            apology,
            document,
        ])
        .await;
        harness.runtime.deps.agent_profile = Some(crate::profile::COMPACT);
        harness.request.permission_mode = Some("trusted".into());
        let mut ctx = harness.context();
        ctx.plan = json!({"goal": "make a page", "steps": [
            {"action": "write_file", "args": {"path": "index.html"}}
        ]})
        .as_object()
        .expect("plan")
        .clone();
        harness
            .runtime
            .execute(&mut ctx, &harness.request)
            .await
            .expect("execute");
        let written = std::fs::read_to_string(harness.root.join("index.html")).expect("file");
        assert!(written.contains("<h1>Hi</h1>"), "{written}");
        assert!(!written.contains("I'm sorry"), "{written}");
        // The unfinished document was repaired rather than regenerated again —
        // the retry budget is one, and the transcript says what happened.
        let step = ctx.transcript.last().expect("step");
        assert_eq!(step["direct_path"], true);
        assert_eq!(step["generation"], json!({"repaired": true}));
        assert!(ctx
            .trace
            .events
            .iter()
            .any(|event| event["kind"] == json!("repair")
                && event["repairs"] == json!(["direct_path_regenerate"])));
    }

    #[tokio::test]
    async fn content_the_sanitize_pass_can_extract_costs_no_regeneration() {
        // A fenced document does not validate as-is, but `sanitize_write_content`
        // recovers it without repairing — so there is nothing to regenerate,
        // and asking again would be a second opinion about a settled question.
        let fenced = "```html\n<!doctype html>\n<html><body><h1>Hi</h1></body></html>\n```";
        let mut harness = harness(&[fenced]).await;
        harness.request.permission_mode = Some("trusted".into());
        let mut ctx = harness.context();
        ctx.plan = json!({"goal": "g", "steps": [
            {"action": "write_file", "args": {"path": "index.html"}}
        ]})
        .as_object()
        .expect("plan")
        .clone();
        harness
            .runtime
            .direct_file_path(&mut ctx, &harness.request, None, crate::profile::COMPACT)
            .await
            .expect("direct path");
        assert_eq!(harness.runtime_llm_calls(&ctx), 1, "one call was enough");
        let written = std::fs::read_to_string(harness.root.join("index.html")).expect("file");
        assert!(written.starts_with("<!doctype html>"), "{written}");
        assert!(!written.contains("```"), "the fence came off: {written}");
        let step = ctx.transcript.last().expect("step");
        assert_eq!(step["generation"], json!({"repaired": false}));
    }

    #[tokio::test]
    async fn a_repeated_direct_path_reply_buys_one_more_generation_and_names_it() {
        // Two identical rejected answers, then a real document. Without the
        // escalation the run would write the first apology; with it, the third
        // call — the only one whose prompt says "you already sent this" — is
        // the one that lands.
        let apology = "I'm sorry, I can't help with that.";
        let document = "<!doctype html>\n<html><body><h1>Hi</h1></body></html>\n";
        let mut harness = harness(&[apology, apology, document]).await;
        harness.request.permission_mode = Some("trusted".into());
        let mut ctx = harness.context();
        ctx.plan = json!({"goal": "g", "steps": [
            {"action": "write_file", "args": {"path": "index.html"}}
        ]})
        .as_object()
        .expect("plan")
        .clone();
        harness
            .runtime
            .direct_file_path(&mut ctx, &harness.request, None, crate::profile::COMPACT)
            .await
            .expect("direct path");
        assert_eq!(
            harness.runtime_llm_calls(&ctx),
            3,
            "one call, one retry, one bought by the repeat"
        );
        assert_eq!(
            std::fs::read_to_string(harness.root.join("index.html")).expect("file"),
            document
        );
        let repairs: Vec<Value> = ctx
            .trace
            .events
            .iter()
            .filter(|event| event["kind"] == json!("repair"))
            .map(|event| event["repairs"].clone())
            .collect();
        assert!(repairs.contains(&json!(["direct_path_regenerate"])));
        assert!(
            repairs.contains(&json!(["direct_path_repeated"])),
            "the repetition is named in the trace, not just in the prompt: {repairs:?}"
        );
        // And the prompt that bought the extra call says so.
        let asks = harness.worker.calls.lock().expect("lock").clone();
        let third = asks
            .iter()
            .filter(|call| call["seam"] == json!("llm"))
            .nth(2)
            .expect("a third ask")["body"]["context"]
            .as_str()
            .expect("context")
            .to_string();
        assert!(
            third.contains("You already sent exactly this reply"),
            "{third}"
        );
    }

    #[tokio::test]
    async fn the_standard_profile_never_spends_a_regeneration() {
        // `regeneration_retries` is zero there, so the first answer is written
        // whatever it looks like — no behaviour change for a capable model.
        let mut harness = harness(&["not a valid html document at all"]).await;
        harness.request.permission_mode = Some("trusted".into());
        let mut ctx = harness.context();
        ctx.plan = json!({"goal": "g", "steps": [
            {"action": "write_file", "args": {"path": "index.html"}}
        ]})
        .as_object()
        .expect("plan")
        .clone();
        harness
            .runtime
            .direct_file_path(&mut ctx, &harness.request, None, crate::profile::STANDARD)
            .await
            .expect("direct path");
        assert_eq!(
            harness.runtime_llm_calls(&ctx),
            1,
            "one call, no regeneration"
        );
    }

    #[tokio::test]
    async fn the_fallback_writes_nothing_when_the_plan_names_nothing() {
        let mut harness = harness(&["prose one", "prose two", "prose three", "prose four"]).await;
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
