use serde_json::{json, Map, Value};

use super::*;
use crate::kernel::agentloop::{RunRequest, Runtime};
use crate::kernel::profile::AgentProfile;
use crate::kernel::state::AgentRunContext;
use crate::seam::worker::{Completion, WorkerError};
use crate::tools::catalog::{ArgKind, CatalogEntry};

/// One micro-turn's raw reply, made readable (v12.0.0).
///
/// Two passes, and **the order between them is the fix**. A frame-aware pass
/// first: `<|channel|>thought … <|message|>real answer` carries the answer in
/// its last channel, and [`crate::parse::channel::strip_channel_frames`] is the
/// crate's one implementation of that rule — the action parser and the
/// file-content extractor already use it. Then the leftovers: `<|im_end|>` is
/// not a channel and has no payload, so [`strip_control_tokens`] removes it.
///
/// Running the scrubber first is the defect this function exists to prevent. It
/// erases the very `<|channel|>` marker the frame reader keys on, so what
/// reaches the reader is a naked reasoning preamble it can no longer recognise
/// as one — and a live gemma-4-e2b wrote 2,515 bytes of `Thinking Process:` into
/// the user's file that way, three attempts out of three. Frames name what is
/// answer and what is not; a scrubber only knows what is punctuation.
///
/// **And a frame with an empty body is still a frame** (v12.0.0). Ordering
/// alone left one hole: `<|channel>thought` and nothing after it is a reply
/// whose payload is empty, the frame reader used to decline it as "no frame",
/// and the scrubber then handed back the *label*. `thought` reached `mcp.grep`
/// as the pattern to search for and the guided critic as its reason, in a
/// live run each. [`crate::parse::channel::channel_payload`] reports the
/// emptiness instead of hiding it, so a turn that said nothing is answered with
/// nothing — which is what makes the caller retry or take the plan's own value.
pub(super) fn clean_reply(text: &str) -> String {
    match crate::parse::channel::channel_payload(text) {
        Some(payload) => strip_control_tokens(&payload),
        None => strip_control_tokens(text),
    }
}

/// Whether a body turn's answer is the file, rather than talk about the file.
///
/// Two refusals, both of them the content layer's own
/// ([`crate::content::sanitize`]) so the guided dial judges a payload by the
/// same rule every other path does: a chat refusal is not a document, and
/// neither is a model's reasoning under its own heading. Everything else is
/// accepted — there is no taste test here, and a short or odd file is still a
/// file the user asked for.
pub(super) fn usable_body(value: &str) -> bool {
    !crate::content::sanitize::looks_like_reasoning_preamble(value)
        && !crate::content::sanitize::looks_like_refusal(value)
}

/// Whether a one-line turn's answer is a **value**, or our own scaffolding
/// carried on (v12.0.0).
///
/// [`usable_body`]'s twin, one argument kind over, and it exists for the same
/// reason: an argument turn puts a labelled shape in front of a model that
/// continues the nearest one. The body turn got that rule; the line turn got
/// only a stop-token change, and two live cells spent their whole `mcp.grep`
/// on it — a 0.5B answered `pattern` with `mcp.grep --pattern "^LatticeAI`
/// (the `[고른 행동 / CHOSEN ACTION]` label, continued into a command line) and
/// a 2B answered it with the request rewritten as a sentence. Both searched
/// for that string and both found nothing.
///
/// Three refusals, each one a shape rather than a taste, and each one a thing
/// this crate put on the model's screen:
///
/// 1. **our label** — the answer opens with the chosen action's own name;
/// 2. **a command line** — a `--flag` token, or a quote opened and not closed,
///    neither of which is part of a value the harness asked for. The one
///    argument this cannot apply to is `command` itself, whose value *is* a
///    command line: `npm run build -- --watch` is the answer there, not the
///    symptom;
/// 3. **the request restated** — the answer opens with the same
///    [`ECHO_PREFIX_FLOOR`] characters the request opens with and then goes its
///    own way. A *fragment* of the request is left alone: a value copied out of
///    what the user wrote (a path, a search term, a whole quoted phrase) is a
///    prefix of it, and prefixes are answers.
///
/// A rejection costs one re-ask ([`crate::prompts::guided::LINE_RETRY_NOTE`]);
/// nothing is refused into nothing, because the floor below still writes the
/// best rejected answer when no attempt does better and the plan offered
/// nothing.
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

/// Whether `value` opens with the request's own opening and then diverges.
///
/// A path is never judged by this — `documents/report-2026.md 파일로 저장해줘`
/// is a request whose first thirty characters *are* the answer — and neither is
/// a value that is wholly a fragment of the request, which is what a search
/// term copied out of the user's words looks like. What is left is the shape
/// no argument ever wants: our sentence started, then finished by the model.
///
/// **With one exception the fragment rule cannot cover** (v12.0.0): the *whole*
/// request. A live 2B answered `mcp.grep`'s `pattern` turn with the user's
/// entire sentence, and "wholly a fragment of the request" was true of it —
/// a request is a prefix of itself. A sentence the user wrote to ask for
/// something is never the value of an argument inside it, whatever else it is.
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
    // Trailing punctuation is dropped for the fragment test, the way
    // [`strip_echoed_lines`] drops it: a model that copied part of the request
    // and put a full stop where the sentence carried on has still copied it.
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
    /// The menu turn: ask for a number, accept a name, retry a nonsense reply.
    ///
    /// Two things about *how* it asks are load-bearing, and both were measured
    /// (v12.0.0). The completion is **prefilled** with
    /// [`crate::prompts::guided::MENU_ANSWER_PREFIX`], so the next token the
    /// model emits is the one after `NUMBER: ` rather than the first token of
    /// however it likes to open a reply — a reasoning-tuned model opens with
    /// `Thinking Process:`, the newline stop fired inside that preamble, and
    /// twelve consecutive live menu turns produced no digit at all. And a
    /// *re-ask* differs from the ask: at temperature zero an identical question
    /// gets an identical answer, so a second identical turn is not a second
    /// attempt. The retry buys [`MENU_RETRY_TOKENS`] and drops the line stop,
    /// which is the only thing left that could still be in the way.
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
            // Cleaned before it is read, like every other guided turn
            // (v12.0.0): a reply of `<|channel>thought` carries no number and
            // no row name, and reading it raw only made the retry budget pay
            // for the template's punctuation twice. The forced opening comes
            // off with it — those characters are ours, not the model's, and a
            // reader that counts them is measuring our own label as if the
            // model had written it.
            let reply = clean_reply(&reply);
            let reply = reply
                .trim_start()
                .strip_prefix(crate::prompts::guided::MENU_ANSWER_PREFIX.trim_end())
                .unwrap_or(&reply)
                .to_string();
            if let Some(chosen) = parse_choice(&reply, catalog.len()) {
                return Ok(Some(chosen));
            }
            // A model that answered with the tool's *name* did the task; only
            // the encoding was wrong, and refusing that would be pedantry.
            if let Some(index) = named_choice(&reply, catalog) {
                return Ok(Some(index));
            }
        }
        Ok(None)
    }

    /// One micro-turn per required argument, assembled into the call.
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
        // Read once, and only where it is read at all: the term rule below asks
        // the catalog what a *tool* name looks like, and a call that takes no
        // search argument never asks. `ToolCatalog::entries` is a scan of the
        // host's skills directory, so this is a per-call cost worth not paying
        // for a `write_file`.
        let catalog = if entry
            .required
            .iter()
            .any(|spec| crate::tools::catalog::is_search_arg(&spec.name))
        {
            self.run_catalog(&req.message)
        } else {
            Vec::new()
        };
        // **The user's own words, not the plan's goal.** A weak planner writes
        // `"goal": "write_file"`, and asking a body turn to write the contents
        // of "write_file" is asking nothing — the first live 0.5B run answered
        // that question with the question. The request is the one string in a
        // run that is never a model artefact.
        let goal = req.message.trim().to_string();
        for spec in &entry.required {
            let suggestion = self.suggested_arg(ctx, req, entry, &spec.name);
            // **An argument the tool documents a default for is offered, not
            // asked for** (v12.0.0). One number instead of a path — see
            // [`Runtime::choose_defaulted_arg`] for the two live answers that
            // are the reason. Only where the run has nothing better: a path the
            // *request* named, or one this action's own plan step carries, is a
            // real suggestion and is asked with the ordinary default block, so
            // this can never talk a run out of a value the user gave it.
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
            // Re-asking is for arguments we have no answer to. When the plan
            // already names one, a blank reply *is* an answer — the prompt told
            // the model the default was there — and spending two more round
            // trips to be told nothing again is how a guided run gets slow for
            // no gain.
            let attempts = if suggestion.is_some() {
                1
            } else {
                profile.micro_turn_cap.max(1)
            };
            let question = match spec.kind {
                ArgKind::Line => crate::prompts::guided::argument_question(spec),
                ArgKind::Text => crate::prompts::guided::body_question(&goal),
            };
            // The best answer that was rejected for *what it was* rather than
            // for being empty. See the fallback below: a body the harness would
            // rather not write still beats writing nothing.
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
                    // A *re-asked* line turn drops the label the rejected
                    // answer was made of, the way a re-asked body turn drops
                    // the instructions its rejected answer was made of — see
                    // [`crate::prompts::guided::argument_retry_block`].
                    ArgKind::Line => crate::prompts::guided::argument_retry_block(
                        &req.message,
                        spec,
                        suggestion.as_deref(),
                    ),
                    // A body question gets no labelled scaffolding in front of
                    // it — see [`crate::prompts::guided::body_block`] — and a
                    // *re-asked* body question gets less than that, because an
                    // identical question at temperature zero is the first ask
                    // replayed rather than a second attempt (v12.0.0).
                    ArgKind::Text if attempt == 0 => {
                        crate::prompts::guided::body_block(&target, &goal, &self.skill_notes(ctx))
                    }
                    ArgKind::Text => crate::prompts::guided::body_retry_block(&target, &goal),
                };
                // **A re-asked line turn drops the newline stop** (v12.0.0),
                // the menu turn's rule ([`MENU_RETRY_TOKENS`]) where it costs
                // the most. A model that opens with `<|channel>thought` and puts
                // its answer on the next line has the stop fire inside the frame
                // header, so the reply is a label and the answer it really
                // generated is thrown away — a live gemma-4-e2b lost `mcp.grep`'s
                // pattern that way thirteen times in one cell, three attempts
                // out of three. The first ask keeps the stop, which is what
                // keeps a one-line answer one line; the re-ask is the one that
                // has already been told a line is not coming.
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
                        // The same extractor the direct chat path uses, so a fenced
                        // answer here and a fenced answer there are unwrapped by one
                        // rule rather than two.
                        ArgKind::Text => crate::content::sanitize::extract_file_content(
                            &reply,
                            args.get("path").and_then(Value::as_str).unwrap_or_default(),
                        ),
                    }
                };
                // A small model continues the nearest text, and the nearest
                // text is what we just asked. Strip our own sentences off the
                // front; what remains is the answer, and nothing at all is no
                // answer — see [`strip_echoed_lines`].
                let cleaned = strip_echoed_lines(&answered, &[&question, &context]);
                if cleaned.trim().is_empty() && !answered.trim().is_empty() {
                    ctx.trace.decision(
                        "execute",
                        "guided_echo_rejected",
                        &[("arg", json!(spec.name))],
                    );
                }
                // **A body that is the model thinking is not the file** — the
                // discipline the JSON path gets from `sanitize_write_content`,
                // applied where a guided run actually produces file text. A live
                // gemma-4-e2b answered three body turns in a row with an
                // enumerated `Thinking Process:` monologue and the run wrote all
                // 2,371 bytes of it into the user's greeting file.
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
                // **And a line that is our own label carried on is not a
                // value** — the same rule, the other argument kind
                // ([`usable_line`]). `final` is exempt and is the one action
                // that can be: its `message` dispatches nothing, it is the
                // run's answer to the user, and an answer that restates the
                // question is an answer.
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
            // Every attempt was a preamble or a refusal. The re-ask is the
            // upside; this is the floor under it — a model that cannot do
            // better on any turn has still produced the only text there is, and
            // silently writing nothing would turn a poor file into a missing
            // one, which is strictly worse for the user and for the run's own
            // requirement coverage.
            //
            // **Unless the run computed one itself** (v12.0.0): where a
            // suggestion exists it is taken below, and the plan's own value
            // beats an answer this turn rejected for what it was. That is the
            // rule `path` and `message` already run on, applied to the one
            // place where "some text beats none" would otherwise send a
            // command line to a search tool.
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
                // A `path` answer that is not a path, when the plan already
                // named one: the live 2B answered the *greeting* it was asked
                // to write and would have created a file called `안녕하세요!`.
                // The plan's own path is the better reading of that turn, and
                // it is never substituted for an answer that *is* a path.
                if spec.name == "path" && !value.trim().is_empty() && !looks_like_a_path(&value) {
                    ctx.trace.decision(
                        "execute",
                        "guided_path_defaulted",
                        &[("answer", json!(crate::parse::pystr::char_slice(&value, 60)))],
                    );
                    value = suggested.clone();
                }
                // The request named a file and the model (or the planner) made
                // up a different one — `인사말/notes/hello.md` against a
                // request that said `notes/hello.md`. The user's path wins.
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
                // A default that was offered and came back **cut off** is the
                // model copying it into a token budget that ran out — the first
                // live run lost `/hello.md` off the end of an absolute path and
                // wrote a directory. A strict prefix of what we offered is that,
                // and taking the whole default back is the only reading of it
                // that is not silent corruption.
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
            // **The term the user named wins, for the argument the user states
            // themselves** (v12.0.0) — `path`'s
            // [`path_agrees_with_request`] rule, one argument kind over, and it
            // runs whether or not the plan offered anything because the two
            // live failures had a plan in one case and none in the other. A
            // 0.5B answered `mcp.grep`'s `pattern` with `LatticeAI mcp.grep`
            // (the term with the tool's name stuck to it) and a 2B answered it
            // with the entire request sentence; both searched, both found
            // nothing over a workspace containing the word, and both runs
            // reported that nothing as `0개`, `DONE`.
            //
            // Nothing is invented: [`term_named_in_request`] is a literal read
            // out of the user's own sentence and only where that sentence names
            // exactly one. A model that answered with the user's word is
            // already equal to it and is untouched.
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
                // An empty answer takes the plan's own value when there is one.
                // Otherwise the argument is simply absent and the tool answers
                // with its own missing-argument message, which is the honest
                // failure: the harness never invents a path or a payload.
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

    /// The numbered choice a defaulting argument is offered as (v12.0.0).
    ///
    /// Returns the value the model picked, or `None` — which means *ask the
    /// ordinary way*: either it chose the free row, or it produced no number at
    /// all and the open question is the honest next move. Nothing is filled in
    /// here on silence; a turn that answers nothing leaves the argument to the
    /// path it always took.
    ///
    /// Rows: the tool's documented default first, the free row last, and
    /// nothing else. Two rows is deliberate — the whole point is an answer
    /// space of `1..=2` for a model that cannot type a path — and the value is
    /// the tool's own signature ([`crate::tools::catalog::defaulted_arg`]),
    /// never a harness guess.
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
            // The menu turn's instrument exactly: a forced answer position, and
            // a re-ask that buys room rather than replaying an identical
            // question at temperature zero.
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
