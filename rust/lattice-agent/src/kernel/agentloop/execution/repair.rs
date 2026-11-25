use std::collections::BTreeSet;

use serde_json::{json, Map, Value};

use super::*;
use crate::kernel::agentloop::guided;
use crate::kernel::profile::AgentProfile;
use crate::kernel::state::AgentState;
use crate::kernel::transcript::{compact_transcript, files_written};
use crate::parse::pystr::{is_truthy, py_str};
use crate::seam::worker::ToolOutcome;

#[path = "ui_resolve.rs"]
mod ui_resolve;
use ui_resolve::find_ui_element_center;
pub(in crate::kernel::agentloop) use ui_resolve::{
    cap_lean_action_names_for, find_request_ui_element_center, url_from_transcript,
};
pub(super) const WRITE_TARGET_STEER: &str = "그 파일은 아직 없습니다";

pub(super) fn not_found_error(error: &str) -> bool {
    let lowered = error.to_lowercase();
    lowered.contains("does not exist")
        || lowered.contains("no such file")
        || lowered.contains("not found")
        || error.contains("존재하지 않")
        || error.contains("찾을 수 없")
}

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

pub(super) fn copies_the_example(args: &Map<String, Value>) -> bool {
    let field = |key: &str| args.get(key).and_then(Value::as_str).unwrap_or_default();
    field("content") == crate::prompts::WRITE_EXAMPLE_CONTENT
        || field("path") == crate::prompts::WRITE_EXAMPLE_PATH
        || crate::prompts::guided::contains_owned_instruction(field("content"))
}

pub(super) fn last_step_unknown_action(ctx: &AgentRunContext) -> bool {
    ctx.transcript.last().is_some_and(|step| {
        step.get("error")
            .and_then(Value::as_str)
            .is_some_and(|error| error.starts_with("Unknown action"))
    })
}

pub(super) fn last_step_copied_the_example(ctx: &AgentRunContext) -> bool {
    ctx.transcript.last().is_some_and(|step| {
        step.get("error")
            .and_then(Value::as_str)
            .is_some_and(|error| error.starts_with("COPIED_"))
    })
}

impl Runtime {
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

    pub(super) fn refuse_incomplete_call(
        &mut self,
        ctx: &mut AgentRunContext,
        entry: &crate::tools::catalog::CatalogEntry,
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
        let signature = entry.signature();
        let param_hint = entry
            .required
            .iter()
            .find(|spec| spec.name == missing)
            .map(|spec| spec.hint.trim())
            .filter(|text| !text.is_empty());
        let detail = match param_hint {
            Some(ph) => format!("Signature: {signature} ({missing}: {ph})."),
            None => format!("Signature: {signature}."),
        };
        let hint = format!(
            "Your last call to {action} carried no {missing}. {detail} \
Send it again with that argument's value."
        );
        if !ctx.corrections.iter().any(|known| py_str(known) == hint) {
            ctx.corrections.push(json!(hint));
            ctx.trace.correction("execute", &hint);
        }
        ctx.trace.decision(
            "execute",
            "missing_required_arg",
            &[
                ("action", json!(action)),
                ("arg", json!(missing)),
                ("signature", json!(signature)),
            ],
        );
        self.emit_step(
            "execute",
            "blocked",
            &[("action", json!(action)), ("reason", json!("missing_arg"))],
        );
        StepFlow::Continue
    }

    pub(super) fn resolve_ui_element_coords(
        &self,
        ctx: &AgentRunContext,
        req: &RunRequest,
        action: &str,
        args: &mut Map<String, Value>,
    ) -> Option<(String, i64, i64)> {
        let bare = action
            .strip_prefix(crate::tools::catalog::MCP_PREFIX)
            .unwrap_or(action);
        if !matches!(bare, "computer_click" | "computer_scroll" | "computer_move") {
            return None;
        }
        let has_numeric_xy = args.get("x").is_some_and(Value::is_number)
            && args.get("y").is_some_and(Value::is_number);
        let explicit_needle = [
            "element_id",
            "element",
            "label",
            "target",
            "id",
            "name",
            "text",
        ]
        .into_iter()
        .find_map(|key| {
            args.get(key).and_then(|v| match v {
                Value::String(s) if !s.trim().is_empty() => Some(s.trim().to_string()),
                Value::Number(n) => Some(n.to_string()),
                _ => None,
            })
        })
        .or_else(|| {
            if has_numeric_xy {
                return None;
            }
            args.get("x")
                .and_then(Value::as_str)
                .map(str::trim)
                .filter(|s| !s.is_empty() && s.parse::<f64>().is_err())
                .map(String::from)
        });
        if let Some(needle) = explicit_needle {
            if let Some((cx, cy)) = find_ui_element_center(&ctx.transcript, &needle) {
                args.insert("x".into(), json!(cx));
                args.insert("y".into(), json!(cy));
                return Some((needle, cx, cy));
            }
        }
        if let Some((matched, cx, cy)) =
            find_request_ui_element_center(&ctx.transcript, &req.message)
        {
            let cur_x = args.get("x").and_then(Value::as_i64);
            let cur_y = args.get("y").and_then(Value::as_i64);
            if cur_x != Some(cx) || cur_y != Some(cy) {
                args.insert("x".into(), json!(cx));
                args.insert("y".into(), json!(cy));
                return Some((matched, cx, cy));
            }
        }
        None
    }

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

    pub(super) fn executor_context(
        &self,
        ctx: &AgentRunContext,
        req: &RunRequest,
        profile: AgentProfile,
    ) -> String {
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
                &self.prompt_action_names_with_ctx(ctx, &req.message, profile),
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

    pub fn prompt_action_names(&self, request: &str) -> Vec<String> {
        self.prompt_action_names_ranked(request, false)
    }

    pub(in crate::kernel::agentloop) fn prompt_action_names_ranked(
        &self,
        request: &str,
        ui_observed: bool,
    ) -> Vec<String> {
        let catalog = self.run_catalog(request);
        let domain = guided::domain_priority_tools(request, ui_observed);
        let mut ordered: Vec<String> = Vec::with_capacity(catalog.len());
        if guided::request_wants_computer_use(request) && !ui_observed {
            for obs in [
                "computer_ui_inspect",
                "computer_screenshot",
                "mcp.computer_ui_inspect",
                "mcp.computer_screenshot",
            ] {
                if catalog.iter().any(|entry| entry.name == obs)
                    && !ordered.iter().any(|name| name == obs)
                {
                    ordered.push(obs.to_string());
                }
            }
        }
        for entry in &catalog {
            if entry.name != "final"
                && guided::request_names(request, entry)
                && !ordered.contains(&entry.name)
            {
                ordered.push(entry.name.clone());
            }
        }
        for candidate in &domain {
            if catalog.iter().any(|entry| entry.name == *candidate)
                && !ordered.iter().any(|name| name == candidate)
            {
                ordered.push((*candidate).to_string());
            }
        }
        for entry in catalog {
            if !ordered.contains(&entry.name) {
                ordered.push(entry.name);
            }
        }
        ordered
    }

    pub(super) fn fill_stated_request_args(
        &self,
        ctx: &AgentRunContext,
        entry: &crate::tools::catalog::CatalogEntry,
        args: &mut Map<String, Value>,
        req: &crate::kernel::agentloop::RunRequest,
        catalog: &[crate::tools::catalog::CatalogEntry],
    ) -> Vec<String> {
        let mut filled = Vec::new();
        for spec in &entry.required {
            let existing_str = args
                .get(&spec.name)
                .and_then(Value::as_str)
                .map(str::trim)
                .unwrap_or("");
            let is_missing = matches!(args.get(&spec.name), None | Some(Value::Null))
                || (args.get(&spec.name).is_some_and(Value::is_string) && existing_str.is_empty());
            if spec.name == "url" {
                let valid_http =
                    existing_str.starts_with("http://") || existing_str.starts_with("https://");
                if is_missing || !valid_http {
                    if let Some(url) = guided::url_named_in_request(&req.message)
                        .or_else(|| url_from_transcript(&ctx.transcript))
                    {
                        args.insert(spec.name.clone(), json!(url));
                        filled.push(spec.name.clone());
                    }
                }
                continue;
            }
            if crate::tools::catalog::is_search_arg(&spec.name) {
                if is_missing {
                    if let Some(term) = guided::term_named_in_request(&req.message, catalog) {
                        args.insert(spec.name.clone(), json!(term));
                        filled.push(spec.name.clone());
                    }
                }
                continue;
            }
            if spec.name == "path" {
                if is_missing {
                    let chosen_path = if !entry.name.contains("write") {
                        guided::unread_existing_path_in_request(
                            ctx,
                            &req.message,
                            &self.deps.workspace,
                        )
                    } else {
                        None
                    }
                    .or_else(|| {
                        guided::path_named_in_request(&req.message, entry, &self.deps.workspace)
                    });
                    if let Some(path) = chosen_path {
                        args.insert(spec.name.clone(), json!(path));
                        filled.push(spec.name.clone());
                    }
                }
                continue;
            }
            if spec.name == "text" && entry.name.ends_with("computer_type") {
                if let Some(typed) = guided::typed_text_in_request(&req.message) {
                    if is_missing || existing_str != typed {
                        args.insert(spec.name.clone(), json!(typed));
                        filled.push(spec.name.clone());
                    }
                }
                continue;
            }
            if spec.name == "key" && entry.name.ends_with("computer_key") {
                if let Some(key) = guided::key_named_in_request(&req.message) {
                    if is_missing {
                        args.insert(spec.name.clone(), json!(key));
                        filled.push(spec.name.clone());
                    }
                }
            }
        }
        filled
    }

    pub fn prompt_action_names_for(
        &self,
        request: &str,
        profile: crate::kernel::profile::AgentProfile,
    ) -> Vec<String> {
        let names = self.prompt_action_names_ranked(request, false);
        if !profile.lean_context {
            return names;
        }
        cap_lean_action_names_for(
            request,
            false,
            names,
            crate::kernel::agentloop::guided::MENU_LIMIT,
        )
    }

    pub(in crate::kernel::agentloop) fn prompt_action_names_with_ctx(
        &self,
        ctx: &AgentRunContext,
        request: &str,
        profile: crate::kernel::profile::AgentProfile,
    ) -> Vec<String> {
        let ui_observed = guided::has_ui_observation(ctx);
        let names = self.prompt_action_names_ranked(request, ui_observed);
        if !profile.lean_context {
            return names;
        }
        cap_lean_action_names_for(
            request,
            ui_observed,
            names,
            crate::kernel::agentloop::guided::MENU_LIMIT,
        )
    }

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
