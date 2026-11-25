use serde_json::{json, Map, Value};

use super::*;
use crate::kernel::agentloop::{RunRequest, Runtime};
use crate::kernel::profile::AgentProfile;
use crate::kernel::state::AgentRunContext;
use crate::seam::worker::{Completion, WorkerError};
use crate::tools::catalog::{ArgKind, CatalogEntry};

pub(super) fn clean_reply(text: &str) -> String {
    match crate::parse::channel::channel_payload(text) {
        Some(payload) => strip_control_tokens(&payload),
        None => strip_control_tokens(text),
    }
}

pub(super) fn usable_body(value: &str) -> bool {
    !crate::content::sanitize::looks_like_reasoning_preamble(value)
        && !crate::content::sanitize::looks_like_refusal(value)
}

pub(super) fn usable_line(value: &str, action: &str, arg: &str, request: &str) -> bool {
    let value = value.trim();
    if value.is_empty() {
        return false;
    }
    let head = value
        .split_whitespace()
        .next()
        .unwrap_or_default()
        .trim_matches(|character: char| {
            !character.is_ascii_alphanumeric()
                && character != '.'
                && character != '_'
                && character != '-'
        });
    if !action.is_empty() && head.eq_ignore_ascii_case(action) {
        return false;
    }
    if arg != "command" {
        if value
            .split_whitespace()
            .any(|token| token.starts_with("--"))
        {
            return false;
        }
        if value.matches('"').count() % 2 == 1 {
            return false;
        }
    }
    !restates_the_request(value, request)
}

pub(super) fn restates_the_request(value: &str, request: &str) -> bool {
    if looks_like_a_path(value) {
        return false;
    }
    let squash = |text: &str| {
        text.split_whitespace()
            .collect::<Vec<_>>()
            .join(" ")
            .to_lowercase()
    };
    let (value, request) = (squash(value), squash(request));
    if !request.is_empty()
        && value.trim_end_matches(TRAILING_PUNCTUATION)
            == request.trim_end_matches(TRAILING_PUNCTUATION)
    {
        return true;
    }
    if value.is_empty() || request.starts_with(value.trim_end_matches(TRAILING_PUNCTUATION)) {
        return false;
    }
    value
        .chars()
        .zip(request.chars())
        .take_while(|(left, right)| left == right)
        .count()
        >= ECHO_PREFIX_FLOOR
}

impl Runtime {
    pub(super) async fn choose_action(
        &mut self,
        ctx: &mut AgentRunContext,
        req: &RunRequest,
        model_id: Option<&str>,
        catalog: &[CatalogEntry],
        profile: AgentProfile,
    ) -> Result<Option<usize>, WorkerError> {
        if catalog.is_empty() {
            return Ok(None);
        }
        let context = format!(
            "{}\n\n{}",
            self.guided_brief(ctx, req),
            crate::prompts::guided::menu_block(catalog),
        );
        for attempt in 0..profile.micro_turn_cap.max(1) {
            let (max_tokens, stop): (u32, &[&str]) = if attempt == 0 {
                (MENU_TOKENS, &LINE_STOP)
            } else {
                (MENU_RETRY_TOKENS, &[])
            };
            let reply = self
                .deps
                .worker
                .llm(Completion {
                    model_id,
                    message: crate::prompts::guided::MENU_QUESTION,
                    context: &context,
                    max_tokens,
                    temperature: MICRO_TEMPERATURE,
                    stop,
                    prefix: crate::prompts::guided::MENU_ANSWER_PREFIX,
                })
                .await?;
            ctx.trace.llm_call("execute", model_id);
            let reply = clean_reply(&reply);
            let reply = reply
                .trim_start()
                .strip_prefix(crate::prompts::guided::MENU_ANSWER_PREFIX.trim_end())
                .unwrap_or(&reply)
                .to_string();
            if let Some(chosen) = parse_choice(&reply, catalog.len()) {
                return Ok(Some(chosen));
            }
            if let Some(index) = named_choice(&reply, catalog) {
                return Ok(Some(index));
            }
        }
        Ok(None)
    }

    pub(super) async fn collect_args(
        &mut self,
        ctx: &mut AgentRunContext,
        req: &RunRequest,
        model_id: Option<&str>,
        entry: &CatalogEntry,
        profile: AgentProfile,
    ) -> Result<Map<String, Value>, WorkerError> {
        let mut args = Map::new();
        let has_rag_evidence = entry.name == "final"
            && crate::kernel::transcript::rag_grounding::transcript_has_evidence(&ctx.transcript);
        let brief = if has_rag_evidence {
            let passages = crate::kernel::transcript::rag_grounding::extract_evidence_passages(
                &ctx.transcript,
            );
            let card =
                crate::kernel::transcript::rag_grounding::build_evidence_card(&passages, 1200);
            format!("{}\n\n{card}", self.argument_brief(ctx, req))
        } else {
            self.argument_brief(ctx, req)
        };
        let catalog = if entry
            .required
            .iter()
            .any(|spec| crate::tools::catalog::is_search_arg(&spec.name))
        {
            self.run_catalog(&req.message)
        } else {
            Vec::new()
        };
        let goal = req.message.trim().to_string();
        for spec in &entry.required {
            let suggestion = self.suggested_arg(ctx, req, entry, &spec.name);
            if suggestion.is_none() {
                if let Some(default) = spec.default.as_deref().filter(|value| !value.is_empty()) {
                    let default = default.to_string();
                    if let Some(picked) = self
                        .choose_defaulted_arg(ctx, model_id, entry, spec, &default, &brief)
                        .await?
                    {
                        args.insert(spec.name.clone(), json!(picked));
                        continue;
                    }
                }
            }
            let target = args
                .get("path")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_string();
            let mut value = String::new();
            let attempts = if suggestion.is_some() {
                1
            } else {
                profile.micro_turn_cap.max(1)
            };
            let question = match spec.kind {
                ArgKind::Line => crate::prompts::guided::argument_question(spec),
                ArgKind::Text => crate::prompts::guided::body_question(&goal),
            };
            let mut rejected: Option<String> = None;
            for attempt in 0..attempts {
                let context = match spec.kind {
                    ArgKind::Line if attempt == 0 => crate::prompts::guided::argument_block(
                        &brief,
                        entry,
                        spec,
                        suggestion.as_deref(),
                        &args,
                    ),
                    ArgKind::Line => crate::prompts::guided::argument_retry_block(
                        &req.message,
                        spec,
                        suggestion.as_deref(),
                    ),
                    ArgKind::Text if attempt == 0 => {
                        crate::prompts::guided::body_block(&target, &goal, &self.skill_notes(ctx))
                    }
                    ArgKind::Text => crate::prompts::guided::body_retry_block(&target, &goal),
                };
                let (max_tokens, stop): (u32, &[&str]) = if has_rag_evidence {
                    (self.deps.phase_budgets.execute_tokens.min(512), &[])
                } else {
                    match spec.kind {
                        ArgKind::Line if attempt == 0 => (LINE_TOKENS, &LINE_STOP),
                        ArgKind::Line => (LINE_TOKENS, &[]),
                        ArgKind::Text => (self.deps.phase_budgets.execute_tokens, &[]),
                    }
                };
                let reply = self
                    .deps
                    .worker
                    .llm(Completion {
                        model_id,
                        message: &question,
                        context: &context,
                        max_tokens,
                        temperature: MICRO_TEMPERATURE,
                        stop,
                        prefix: "",
                    })
                    .await?;
                ctx.trace.llm_call("execute", model_id);
                let reply = clean_reply(&reply);
                let answered = if has_rag_evidence && reply.trim().contains('\n') {
                    reply.trim().to_string()
                } else {
                    match spec.kind {
                        ArgKind::Line => parse_line(&reply),
                        ArgKind::Text => crate::content::sanitize::extract_file_content(
                            &reply,
                            args.get("path").and_then(Value::as_str).unwrap_or_default(),
                        ),
                    }
                };
                let cleaned = strip_echoed_lines(&answered, &[&question, &context]);
                if cleaned.trim().is_empty() && !answered.trim().is_empty() {
                    ctx.trace.decision(
                        "execute",
                        "guided_echo_rejected",
                        &[("arg", json!(spec.name))],
                    );
                }
                if spec.kind == ArgKind::Text
                    && !cleaned.trim().is_empty()
                    && !usable_body(&cleaned)
                {
                    ctx.trace.decision(
                        "execute",
                        "guided_body_rejected",
                        &[("arg", json!(spec.name))],
                    );
                    if rejected.is_none() {
                        rejected = Some(cleaned);
                    }
                    continue;
                }
                if spec.kind == ArgKind::Line
                    && entry.name != "final"
                    && !cleaned.trim().is_empty()
                    && !usable_line(&cleaned, &entry.name, &spec.name, &req.message)
                {
                    ctx.trace.decision(
                        "execute",
                        "guided_line_rejected",
                        &[
                            ("arg", json!(spec.name)),
                            (
                                "answer",
                                json!(crate::parse::pystr::char_slice(&cleaned, 60)),
                            ),
                        ],
                    );
                    if rejected.is_none() {
                        rejected = Some(cleaned);
                    }
                    continue;
                }
                value = cleaned;
                if !value.trim().is_empty() {
                    break;
                }
            }
            if value.trim().is_empty() && suggestion.is_none() {
                if let Some(fallback) = rejected {
                    ctx.trace.decision(
                        "execute",
                        match spec.kind {
                            ArgKind::Text => "guided_body_fallback",
                            ArgKind::Line => "guided_line_fallback",
                        },
                        &[("arg", json!(spec.name))],
                    );
                    value = fallback;
                }
            }
            if let Some(suggested) = &suggestion {
                if spec.name == "path" && !value.trim().is_empty() && !looks_like_a_path(&value) {
                    ctx.trace.decision(
                        "execute",
                        "guided_path_defaulted",
                        &[("answer", json!(crate::parse::pystr::char_slice(&value, 60)))],
                    );
                    value = suggested.clone();
                }
                if spec.name == "path"
                    && !value.trim().is_empty()
                    && !path_agrees_with_request(&value, &req.message)
                {
                    ctx.trace.decision(
                        "execute",
                        "guided_path_from_request",
                        &[("answer", json!(crate::parse::pystr::char_slice(&value, 60)))],
                    );
                    value = suggested.clone();
                }
                if spec.name == "message"
                    && crate::kernel::transcript::request_asks_for_a_count(&req.message)
                    && !value.chars().any(|character| character.is_ascii_digit())
                    && suggested
                        .chars()
                        .any(|character| character.is_ascii_digit())
                {
                    ctx.trace.decision("execute", "guided_count_defaulted", &[]);
                    value = suggested.clone();
                }
                if !value.trim().is_empty()
                    && value.trim().len() < suggested.len()
                    && suggested.starts_with(value.trim())
                {
                    ctx.trace.decision(
                        "execute",
                        "guided_truncated_default",
                        &[("arg", json!(spec.name))],
                    );
                    value = suggested.clone();
                }
            }
            if crate::tools::catalog::is_search_arg(&spec.name) {
                if let Some(term) = term_named_in_request(&req.message, &catalog) {
                    if value.trim() != term {
                        ctx.trace.decision(
                            "execute",
                            "guided_term_from_request",
                            &[("answer", json!(crate::parse::pystr::char_slice(&value, 60)))],
                        );
                        value = term;
                    }
                }
            }
            if entry.name == "write_file" && spec.name == "content" {
                if let Some(literal) = quoted_term(&req.message) {
                    if !value.contains(&literal) {
                        value = if value.trim().is_empty() {
                            literal
                        } else {
                            format!("{}\n{literal}", value.trim())
                        };
                    }
                }
            }
            if value.trim().is_empty() {
                if let Some(suggested) = suggestion {
                    value = suggested;
                } else {
                    continue;
                }
            }
            args.insert(spec.name.clone(), json!(value));
        }
        Ok(args)
    }

    pub(super) async fn choose_defaulted_arg(
        &mut self,
        ctx: &mut AgentRunContext,
        model_id: Option<&str>,
        entry: &CatalogEntry,
        spec: &crate::tools::catalog::ArgSpec,
        default: &str,
        brief: &str,
    ) -> Result<Option<String>, WorkerError> {
        let rows = vec![
            crate::prompts::guided::default_choice_label(default),
            crate::prompts::guided::ARG_CHOICE_FREE_ROW.to_string(),
        ];
        let context = crate::prompts::guided::default_choice_block(brief, entry, spec, &rows);
        for attempt in 0..2 {
            let (max_tokens, stop): (u32, &[&str]) = if attempt == 0 {
                (MENU_TOKENS, &LINE_STOP)
            } else {
                (MENU_RETRY_TOKENS, &[])
            };
            let reply = self
                .deps
                .worker
                .llm(Completion {
                    model_id,
                    message: crate::prompts::guided::ARG_CHOICE_QUESTION,
                    context: &context,
                    max_tokens,
                    temperature: MICRO_TEMPERATURE,
                    stop,
                    prefix: crate::prompts::guided::MENU_ANSWER_PREFIX,
                })
                .await?;
            ctx.trace.llm_call("execute", model_id);
            let reply = clean_reply(&reply);
            let reply = reply
                .trim_start()
                .strip_prefix(crate::prompts::guided::MENU_ANSWER_PREFIX.trim_end())
                .unwrap_or(&reply)
                .to_string();
            let Some(chosen) = parse_choice(&reply, rows.len()) else {
                continue;
            };
            ctx.trace.decision(
                "execute",
                "guided_default_choice",
                &[("arg", json!(spec.name)), ("chosen", json!(chosen))],
            );
            return Ok((chosen == 1).then(|| default.to_string()));
        }
        Ok(None)
    }
}
