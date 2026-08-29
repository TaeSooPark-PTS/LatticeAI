use std::collections::BTreeSet;

use serde_json::{json, Map, Value};

use super::*;
use crate::kernel::agentloop::guided;
use crate::kernel::profile::AgentProfile;
use crate::kernel::state::AgentState;
use crate::kernel::transcript::{compact_transcript, files_written};
use crate::parse::pystr::{is_truthy, py_str};
use crate::surface::worker::ToolOutcome;

/// The sentence appended to a not-found error against the request's own output
/// file. Also the idempotence marker: a step already carrying it is not steered
/// twice.
pub(super) const WRITE_TARGET_STEER: &str = "그 파일은 아직 없습니다";

/// Whether a tool error says the thing an argument named **is not there**.
///
/// A class, not a sentence, and deliberately read off the message rather than
/// off a tool table: the tools that answer this way are spread across this
/// crate, `lattice-platform` and the Python worker, and each says it in its own
/// words (`File does not exist.`, `Directory does not exist.`, `Working
/// directory does not exist.`). What they have in common is the only thing the
/// repair below needs to know — the call was well-formed and the *place* was
/// wrong — so the recogniser is the phrase, in the languages the product speaks.
///
/// Nothing acts on this alone: [`Runtime::repair_with_documented_default`] also
/// requires the tool to document a default for the argument, and
/// [`Runtime::steer_to_write_target`] also requires the request to have named
/// the path as an output. A false positive here changes a message, never a
/// permission.
pub(super) fn not_found_error(error: &str) -> bool {
    let lowered = error.to_lowercase();
    lowered.contains("does not exist")
        || lowered.contains("no such file")
        || lowered.contains("not found")
        || error.contains("존재하지 않")
        || error.contains("찾을 수 없")
}

/// The tool error the step just recorded carries, if it failed at all.
pub(super) fn last_step_error(ctx: &AgentRunContext) -> Option<String> {
    let step = ctx.transcript.last()?;
    if step.get("state").and_then(Value::as_str) != Some(AgentState::Executing.as_str()) {
        return None;
    }
    let error = step.get("error").and_then(Value::as_str)?;
    (!error.starts_with("COPIED_")
        && !error.starts_with("Unknown action")
        && !error.starts_with("LOOP_DETECTED"))
    .then(|| error.to_string())
}

/// Every `action|args` this run has already been refused, spelled exactly as
/// [`failing_dispatch_signature`] and [`super::fallback`] spell one.
///
/// One spelling for the whole crate: `serde_json`'s object is a `BTreeMap`, so
/// the rendering is key-order independent and two callers comparing the same
/// call always agree.
pub(super) fn failed_signatures(ctx: &AgentRunContext) -> BTreeSet<String> {
    ctx.transcript
        .iter()
        .filter(|step| step.get("error").is_some())
        .filter_map(|step| {
            let action = step.get("action").and_then(Value::as_str)?;
            let args = step.get("args").cloned().unwrap_or_else(|| json!({}));
            Some(format!("{action}|{args}"))
        })
        .collect()
}

/// `action|args` of the step just recorded, when it was a **tool** failure.
///
/// Deliberately not every failure. A copied example and an invented action name
/// are format failures with an owner already — they spend the parse budget a
/// few lines below — and counting them twice would shorten a run for a reason
/// that is already being counted. What is left is the case nothing was watching:
/// a real action, really dispatched, that the tool or a gate refused.
pub(super) fn failing_dispatch_signature(ctx: &AgentRunContext) -> Option<String> {
    let step = ctx.transcript.last()?;
    if step.get("state").and_then(Value::as_str) != Some(AgentState::Executing.as_str()) {
        return None;
    }
    let action = step.get("action").and_then(Value::as_str)?;
    if action.is_empty() || action == "parse_error" {
        return None;
    }
    let error = step.get("error").and_then(Value::as_str)?;
    if error.starts_with("COPIED_") || error.starts_with("Unknown action") {
        return None;
    }
    let args = step.get("args").cloned().unwrap_or_else(|| json!({}));
    Some(format!("{action}|{args}"))
}

/// Whether these arguments are the prompt's worked example, verbatim.
///
/// Both fields are compared against [`crate::prompts`]' own constants, and the
/// *content* alone is enough — a model that copied the body but invented a path
/// still wrote our text into the user's workspace, which is the harm. The path
/// is checked too so a reply that copied only the path is caught before the
/// sandbox has to answer for it.
pub(super) fn copies_the_example(args: &Map<String, Value>) -> bool {
    let field = |key: &str| args.get(key).and_then(Value::as_str).unwrap_or_default();
    field("content") == crate::prompts::WRITE_EXAMPLE_CONTENT
        || field("path") == crate::prompts::WRITE_EXAMPLE_PATH
        || crate::prompts::guided::contains_owned_instruction(field("content"))
}

/// Whether the step just recorded named a tool this run does not have.
pub(super) fn last_step_unknown_action(ctx: &AgentRunContext) -> bool {
    ctx.transcript.last().is_some_and(|step| {
        step.get("error")
            .and_then(Value::as_str)
            .is_some_and(|error| error.starts_with("Unknown action"))
    })
}

/// Whether the step just recorded was refused for copying our own text.
pub(super) fn last_step_copied_the_example(ctx: &AgentRunContext) -> bool {
    ctx.transcript.last().is_some_and(|step| {
        step.get("error")
            .and_then(Value::as_str)
            .is_some_and(|error| error.starts_with("COPIED_"))
    })
}

impl Runtime {
    /// **The place was wrong, and the tool documents the right one** (v12.0.0).
    ///
    /// The round-3 fill covers the argument that is *absent*; this covers the
    /// argument that is present and wrong, which is the shape two live cells
    /// took. Handed `이 폴더에 … list_dir로 확인하고`, a 0.5B answered the path
    /// turn with `path/scratchpad/…` (our own workspace line, continued) and a
    /// 2B answered it with `file_list.txt` (a filename its planner had invented).
    /// Both dispatched, both were told `Directory does not exist.`, both sent
    /// the identical call again, and both runs ended in `LOOP_DETECTED` over a
    /// listing `list_dir(path=".")` would have returned at step one.
    ///
    /// So: one retry, with the value the tool's own signature documents
    /// ([`crate::tools::catalog::defaulted_arg`]), and four conditions that keep
    /// it off everything else —
    ///
    /// * the call **reached the tool** and the tool answered with a
    ///   not-found-class error ([`not_found_error`]). A gate refusal, a
    ///   proposal, or any other failure is not an argument problem;
    /// * the tool **documents a literal default** for one of its arguments. No
    ///   value is invented here, ever: this is the same string
    ///   `fill_documented_defaults` would have supplied had the model said
    ///   nothing at all;
    /// * the stated value **differs** from that default, so a call that already
    ///   used the default and still failed is never sent twice;
    /// * the repaired `action|args` is **not already on the transcript with an
    ///   error against it** — the round-3 rule from [`super::fallback`], applied
    ///   to the live loop. A repaired call is a different call and runs; a
    ///   second failure is left to the ordinary repeat floor, which halts.
    ///
    /// The repair is recorded on the step it produced (`arg_repair`) and named
    /// in a correction, because a run that silently changed the model's
    /// argument would be a run whose transcript no longer says what happened.
    pub(super) async fn repair_with_documented_default(
        &mut self,
        ctx: &mut AgentRunContext,
        req: &RunRequest,
        catalog: &[crate::tools::catalog::CatalogEntry],
        dispatched: Dispatched<'_>,
        args: Map<String, Value>,
    ) {
        let original = dispatched.original;
        let Some(error) = last_step_error(ctx) else {
            return;
        };
        if !not_found_error(&error) {
            return;
        }
        let Some(entry) = crate::tools::catalog::entry_for(catalog, original) else {
            return;
        };
        let Some((arg, default)) = crate::tools::catalog::defaulted_arg(entry) else {
            return;
        };
        let (arg, default) = (arg.to_string(), default.to_string());
        let stated = args
            .get(&arg)
            .and_then(Value::as_str)
            .unwrap_or_default()
            .trim()
            .to_string();
        if stated == default {
            return;
        }
        let mut repaired = args;
        repaired.insert(arg.clone(), json!(default));
        let signature = format!("{original}|{}", Value::Object(repaired.clone()));
        if failed_signatures(ctx).contains(&signature) {
            return;
        }
        let note = format!(
            "{original} could not use args.{arg} = {stated:?} ({error}). \
Retried once with the value {original} documents for {arg}: {default:?}."
        );
        ctx.trace.decision(
            "execute",
            "documented_default_repair",
            &[
                ("action", json!(original)),
                ("arg", json!(arg)),
                ("default", json!(default)),
            ],
        );
        if !ctx.corrections.iter().any(|known| py_str(known) == note) {
            ctx.corrections.push(json!(note.clone()));
            ctx.trace.correction("execute", &note);
        }
        if self
            .govern_and_dispatch(ctx, req, dispatched, &repaired)
            .await
        {
            if let Some(step) = ctx.transcript.last_mut() {
                step["arg_repair"] = json!({
                    "arg": arg, "stated": stated, "default": default, "note": note,
                });
            }
        }
    }

    /// **The file the request asked us to write is not a file to read**
    /// (v12.0.0).
    ///
    /// A live 0.5B consulted `skill.code_review`, then read
    /// `notes/review_note.md` — the file the request had asked it to *create* —
    /// three times, was told `File does not exist.` three times, and halted.
    /// The tool's message is correct and useless: it answers "is this file
    /// here", when the fact the run needs is "this one is yours to make".
    ///
    /// Only the harness can say that, because only the harness read the
    /// request: the target comes from
    /// [`crate::parse::inference::requested_output_paths`], the same declared
    /// outputs `requirement_coverage` holds the run to at VERIFY, so the menu,
    /// the critic and this sentence cannot disagree about which file was asked
    /// for. The tool's own words are kept and the steer is appended after them
    /// — nothing is hidden, and a read that failed for any other reason, or
    /// against any path the request did not name as an output, is untouched.
    ///
    /// It steers and never acts: no file is created here. The model still
    /// chooses `write_file` and still authors what goes in it.
    pub(super) fn steer_to_write_target(
        &mut self,
        ctx: &mut AgentRunContext,
        req: &RunRequest,
        action: &str,
    ) {
        if self.deps.file_create_actions.contains(action) {
            return;
        }
        let Some(error) = last_step_error(ctx) else {
            return;
        };
        if !not_found_error(&error) || error.contains(WRITE_TARGET_STEER) {
            return;
        }
        let path = ctx
            .transcript
            .last()
            .and_then(|step| step.get("args"))
            .and_then(|args| args.get("path").or_else(|| args.get("file")))
            .and_then(Value::as_str)
            .unwrap_or_default()
            .trim()
            .to_string();
        if path.is_empty() {
            return;
        }
        let declared = crate::parse::inference::requested_output_paths(&req.message);
        let target = declared.iter().any(|wanted| {
            let (wanted, answered) = (
                wanted.trim_start_matches("./"),
                path.trim_start_matches("./"),
            );
            wanted == answered || wanted.ends_with(answered) || answered.ends_with(wanted)
        });
        if !target {
            return;
        }
        let steer = format!(
            "{error} {WRITE_TARGET_STEER} — write_file({path})로 만드세요. / \
That file is the one this request asks you to create: make it with write_file({path})."
        );
        if let Some(step) = ctx.transcript.last_mut() {
            step["error"] = json!(steer);
        }
        let hint = format!(
            "{path} 파일은 아직 없습니다 — 읽지 말고 write_file({path})로 만드세요. \
/ Do not read {path}: it does not exist yet, and creating it is what was asked for. \
Call write_file with that path and the content you write."
        );
        if !ctx.corrections.iter().any(|known| py_str(known) == hint) {
            ctx.corrections.push(json!(hint));
            ctx.trace.correction("execute", &hint);
        }
        ctx.trace.decision(
            "execute",
            "read_of_write_target",
            &[("action", json!(action)), ("path", json!(path))],
        );
    }

    /// **A skill the request names by name is consulted, first** (v12.0.0).
    ///
    /// The contract is stated in `ARCHITECTURE.md` and is one sentence: naming
    /// an installed skill in the request *is* the instruction to read it, so the
    /// harness carries that instruction out deterministically and then hands
    /// control straight back. It is the same shape a slash-command has in any
    /// agent: the user named the thing, so the thing happens.
    ///
    /// This is not the harness choosing. It chooses no tool, writes nothing and
    /// decides nothing about the task — a skill's result is its `SKILL.md`, which
    /// is guidance in front of the model for the following steps. A live
    /// gemma-4-e2b handed `code_review 스킬을 참고해서 …` planned `write_file`
    /// directly and never consulted the named skill on any of three attempts, so
    /// the instruction the user actually gave was the one thing the run ignored.
    ///
    /// Three conditions, all required: the request names a **skill row this run
    /// offers** (so nothing is consulted that was not installed and named),
    /// nothing has executed yet (it is the *first* step or it does not happen —
    /// a resumed or retried run is never re-injected), and the skill has not
    /// already been read this run.
    pub(super) async fn consult_named_skill(
        &mut self,
        ctx: &mut AgentRunContext,
        req: &RunRequest,
    ) {
        if ctx
            .transcript
            .iter()
            .any(|step| step.get("state").and_then(Value::as_str) == Some("EXECUTING"))
        {
            return;
        }
        let Some(entry) = self.run_catalog(&req.message).into_iter().find(|entry| {
            entry.kind == crate::tools::catalog::EntryKind::Skill
                && (guided::request_names(&req.message, entry)
                    || skill_when_invites(&req.message, entry, &req.skills))
                && !guided::action_succeeded(ctx, &entry.name)
        }) else {
            return;
        };
        ctx.trace.decision(
            "execute",
            "named_skill_consulted",
            &[("action", json!(entry.name))],
        );
        self.perform_action(
            ctx,
            req,
            Chosen {
                name: &entry.name,
                thoughts: "요청이 이 스킬을 지정했습니다 / the request named this skill",
                args: Map::new(),
                final_message: None,
            },
        )
        .await;
    }

    /// Refuse one call for a required argument nothing could supply, and say so
    /// in a sentence the next turn can act on.
    ///
    /// The error text is the harness's own and is deliberately the same string
    /// on every dial — a `compact` run and a `guided` run that make the same
    /// mistake must read the same correction, or the probe's choice of dial
    /// silently changes what a model is told. The correction is deduped, so a
    /// model repeating itself does not fill the correction budget with one
    /// sentence; the step stays on the transcript either way, which is what the
    /// repeated-failure floor counts.
    pub(super) fn refuse_incomplete_call(
        &mut self,
        ctx: &mut AgentRunContext,
        action: &str,
        args: &Map<String, Value>,
        missing: &str,
    ) -> StepFlow {
        let error = format!("{action} needs args.{missing}. Nothing was done.");
        ctx.transcript.push(json!({
            "state": AgentState::Executing.as_str(),
            "action": action,
            "args": Value::Object(args.clone()),
            "error": error,
        }));
        let hint = format!(
            "Your last call to {action} carried no {missing}. \
Send it again with that argument's value."
        );
        if !ctx.corrections.iter().any(|known| py_str(known) == hint) {
            ctx.corrections.push(json!(hint));
            ctx.trace.correction("execute", &hint);
        }
        ctx.trace.decision(
            "execute",
            "missing_required_arg",
            &[("action", json!(action)), ("arg", json!(missing))],
        );
        self.emit_step(
            "execute",
            "blocked",
            &[("action", json!(action)), ("reason", json!("missing_arg"))],
        );
        StepFlow::Continue
    }

    /// Run one host-catalog entry: an MCP tool the run does not govern, or a
    /// skill.
    ///
    /// A skill's result is *instructions*, not an effect, and the step records
    /// it as such — [`super::guided`] and the executor prompt both read the
    /// text back out of the transcript on the following steps. With no catalog
    /// injected the answer is a refusal that names the reason, because a model
    /// that was offered nothing cannot have chosen this.
    pub(super) async fn dispatch_external(
        &mut self,
        ctx: &mut AgentRunContext,
        req: &RunRequest,
        name: &str,
        thoughts: &str,
        args: &Map<String, Value>,
    ) -> StepFlow {
        let scope = crate::tools::CallScope {
            user_email: req.user_email.clone(),
            workspace_id: req.workspace_id.clone(),
        };
        let outcome = match &self.deps.external {
            Some(catalog) => catalog.execute(name, args, &scope).await,
            None => ToolOutcome::Error(format!(
                "'{name}' is not available: this run has no external tool catalog."
            )),
        };
        let mut step = json!({
            "state": AgentState::Executing.as_str(),
            "action": name, "thoughts": thoughts,
            "args": Value::Object(args.clone()),
        });
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
        ctx.trace.tool(
            "execute",
            name,
            if ok { "ok" } else { "error" },
            Some("external"),
        );
        ctx.transcript.push(step);
        self.emit_step(
            "execute",
            "tool",
            &[("action", json!(name)), ("ok", json!(ok))],
        );
        StepFlow::Continue
    }

    /// Assemble one executor turn's prompt.
    ///
    /// The header is the run's executor prompt — the caller's when it supplied
    /// one, the profile-shaped built-in ([`crate::prompts`]) when it did not.
    /// What the loop contributes around it — the plan, the files this run has
    /// already written, the latest corrections, the bounded transcript — is
    /// assembled here, in the original's order.
    ///
    /// **The compact profile trims two of those, and shortens a third.** The
    /// transcript window alone never bounded this: by step five a 2B model was
    /// being handed the plan, every path written so far, the whole recent
    /// conversation and four steps of 700-character tool output, and the action
    /// contract at the top was competing with 6–10k tokens of framing. Under
    /// `lean_context` the written-files hint and the conversation go (the plan
    /// and the transcript already carry those facts) and each step's result is
    /// sliced to [`AgentProfile::result_chars`]. Nothing is trimmed for a model
    /// that can hold the contract — `standard` composes exactly what it did.
    pub(super) fn executor_context(
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
        let budget = self.deps.transcript_budget;
        let window = budget.window.min(profile.transcript_window);
        let bounded = compact_transcript(
            &ctx.transcript,
            window,
            budget.result_chars.min(profile.result_chars),
        );
        let conversation_block = if profile.lean_context {
            String::new()
        } else {
            let recent = req
                .recent_conversation
                .as_deref()
                .filter(|text| !text.is_empty())
                .unwrap_or("(none)");
            format!("\n\nRecent conversation:\n{recent}")
        };
        let written = files_written(&ctx.transcript, &self.deps.file_create_actions);
        let written_hint = if written.is_empty() || profile.lean_context {
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
            "{}\n\n[LANGUAGE HINT: {}]\nWorkspace root: {}{}\n\nPLAN:\n{}{}{}\
\n\nUser request: {}{}\n\nExecution transcript:\n{}",
            self.deps.prompts.executor_prompt(
                profile,
                &self.prompt_action_names_for(&req.message, profile),
                &req.skills,
            ),
            req.language_hint,
            self.deps.workspace.root().display(),
            self.project_block(ctx),
            serde_json::to_string(&Value::Object(ctx.plan.clone())).unwrap_or_default(),
            written_hint,
            conversation_block,
            req.message,
            corrections_hint,
            serde_json::to_string_pretty(&Value::Array(bounded)).unwrap_or_default(),
        )
    }

    /// Every action a JSON turn may choose, **most likely first** (v12.0.0).
    ///
    /// Two defects, one list. The executor prompt was built from
    /// `deps.tool_names` alone — the run's *native* table — so a compact model
    /// was told in prose that a skill existed and given no action name to call
    /// it with, and an `mcp.` row it would have seen on the guided menu was not
    /// on this list at all. The catalog was uniform for one dial out of three.
    /// A live 2B handed `code_review 스킬을 참고해서 …` never chose
    /// `skill.code_review`, because as far as its prompt was concerned there was
    /// no such action; it reached for `build_project` and `run_command` instead
    /// and spent sixteen steps failing.
    ///
    /// And the order was the policy table's, which is alphabetical — the same
    /// "alphabet is not a ranking" the guided menu already fixed. A row the
    /// request *names* goes first here for the same reason it goes first there.
    /// Everything else keeps the catalog's own order, so the list a run reads is
    /// stable across its steps, and `final` stays last
    /// ([`crate::prompts::action_list`] owns that).
    pub(in crate::kernel::agentloop) fn prompt_action_names(&self, request: &str) -> Vec<String> {
        let (named, rest): (Vec<_>, Vec<_>) = self
            .run_catalog(request)
            .into_iter()
            .partition(|entry| entry.name != "final" && guided::request_names(request, entry));
        named
            .into_iter()
            .chain(rest)
            .map(|entry| entry.name)
            .collect()
    }

    /// Copy a path or search term the *request already named* into a missing
    /// required argument. Nothing is invented: the same two readers the guided
    /// dial uses ([`guided::term_named_in_request`], [`guided::path_named_in_request`]).
    /// A compact 2B that emits `mcp.grep` with no `pattern` still searched
    /// `"LatticeAI"` when that was the only quoted term in the request.
    pub(super) fn fill_stated_request_args(
        &self,
        entry: &crate::tools::catalog::CatalogEntry,
        args: &mut Map<String, Value>,
        req: &crate::kernel::agentloop::RunRequest,
        catalog: &[crate::tools::catalog::CatalogEntry],
    ) -> Vec<String> {
        let mut filled = Vec::new();
        for spec in &entry.required {
            if !matches!(args.get(&spec.name), None | Some(Value::Null)) {
                continue;
            }
            if crate::tools::catalog::is_search_arg(&spec.name) {
                if let Some(term) = guided::term_named_in_request(&req.message, catalog) {
                    args.insert(spec.name.clone(), json!(term));
                    filled.push(spec.name.clone());
                }
                continue;
            }
            if spec.name == "path" {
                if let Some(path) =
                    guided::path_named_in_request(&req.message, entry, &self.deps.workspace)
                {
                    args.insert(spec.name.clone(), json!(path));
                    filled.push(spec.name.clone());
                }
            }
        }
        filled
    }

    /// [`prompt_action_names`], capped when the dial cannot hold a long list.
    ///
    /// Named rows stay first, `final` stays last, and the cap is the same
    /// [`crate::kernel::agentloop::guided::MENU_LIMIT`] the numbered menu uses
    /// — a 2B that is shown forty MCP rows loses the contract the compact
    /// prefix exists to keep.
    pub(in crate::kernel::agentloop) fn prompt_action_names_for(
        &self,
        request: &str,
        profile: crate::kernel::profile::AgentProfile,
    ) -> Vec<String> {
        let names = self.prompt_action_names(request);
        if !profile.lean_context {
            return names;
        }
        cap_lean_action_names(names, crate::kernel::agentloop::guided::MENU_LIMIT)
    }

    /// Loop guard: the same file-create action+args re-issued right after a result.
    pub(super) fn is_repeated_create(
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

/// Whether the request matches a skill's own "use when" line.
///
/// Naming the skill is [`guided::request_names`]. This is the other way a
/// user asks for one: they describe the situation the skill's `when` field
/// already named, without repeating `skill.code_review`. A 2B that cannot
/// hold the catalog name still said the work.
fn skill_when_invites(
    request: &str,
    entry: &crate::tools::catalog::CatalogEntry,
    skills: &[crate::prompts::SkillBrief],
) -> bool {
    let hay = request.to_lowercase();
    let bare = entry
        .name
        .strip_prefix(crate::tools::catalog::SKILL_PREFIX)
        .unwrap_or(entry.name.as_str());
    let from_brief = skills
        .iter()
        .find(|skill| skill.name.eq_ignore_ascii_case(bare));
    let sources: Vec<&str> = match from_brief {
        Some(skill) => vec![
            skill.when.as_str(),
            skill.brief.as_str(),
            entry.summary.as_str(),
        ],
        None => vec![entry.summary.as_str()],
    };
    sources.into_iter().any(|source| phrase_hits(&hay, source))
}

fn phrase_hits(hay: &str, source: &str) -> bool {
    source
        .split([',', ';', '/', '\n'])
        .map(|chunk| chunk.trim().to_lowercase())
        .filter(|chunk| chunk.chars().count() >= 6)
        .any(|needle| hay.contains(&needle))
}

fn cap_lean_action_names(names: Vec<String>, limit: usize) -> Vec<String> {
    const CORE: [&str; 4] = ["write_file", "read_file", "edit_file", "list_dir"];
    let has_final = names.iter().any(|name| name == "final");
    let present: std::collections::BTreeSet<&str> = names.iter().map(String::as_str).collect();
    let mut kept = Vec::new();
    for name in &names {
        if name == "final" {
            continue;
        }
        if kept.len() + 1 < limit {
            kept.push(name.clone());
        }
    }
    for core in CORE {
        if !present.contains(core) || kept.iter().any(|name| name == core) {
            continue;
        }
        if let Some(index) = kept.iter().rposition(|name| !CORE.contains(&name.as_str())) {
            kept[index] = core.to_string();
        } else if kept.len() + 1 < limit {
            kept.push(core.to_string());
        }
    }
    if has_final {
        kept.push("final".into());
    }
    kept
}

#[cfg(test)]
mod lean_list_tests {
    use super::cap_lean_action_names;

    #[test]
    fn a_long_catalog_keeps_the_head_and_final() {
        let names: Vec<String> = (0..20)
            .map(|index| format!("tool_{index}"))
            .chain(std::iter::once("final".into()))
            .collect();
        let capped = cap_lean_action_names(names, 9);
        assert_eq!(capped.len(), 9);
        assert_eq!(capped[0], "tool_0");
        assert_eq!(capped.last().map(String::as_str), Some("final"));
        assert!(!capped.iter().any(|name| name == "tool_19"));
    }

    #[test]
    fn core_file_tools_survive_an_alphabetical_flood() {
        let mut names: Vec<String> = (0..20).map(|index| format!("computer_{index}")).collect();
        names.push("write_file".into());
        names.push("read_file".into());
        names.push("final".into());
        let capped = cap_lean_action_names(names, 9);
        assert!(capped.iter().any(|name| name == "write_file"), "{capped:?}");
        assert!(capped.iter().any(|name| name == "read_file"), "{capped:?}");
        assert_eq!(capped.last().map(String::as_str), Some("final"));
    }
}
