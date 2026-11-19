use serde_json::Value;

use super::*;
use crate::kernel::agentloop::{RunRequest, Runtime};
use crate::kernel::state::{AgentRunContext, AgentState};
use crate::parse::pystr::{is_truthy, py_str};
use crate::tools::catalog::{ArgKind, CatalogEntry};

impl Runtime {
    /// What the plan already says this argument is, when it says anything.
    ///
    /// Only ever a *default* offered to the model and taken when the model
    /// declines to answer — never a value substituted for one it did give.
    ///
    /// **Every one-line argument, not only `path`** (v12.0.0). The rule was
    /// written for `path` and the reason for it was never about paths: the
    /// planner already computed this call's arguments, and a 0.5B improvising
    /// one is strictly worse than the value the run itself produced. A live
    /// 0.5B asked for `mcp.grep`'s `pattern` — with the plan sitting on
    /// `"pattern": "LatticeAI"` — answered
    /// `mcp.grep --pattern-literal "LatticeAI`, and the search that ran was for
    /// that string. A free-form **body** is deliberately still excluded: a
    /// planner's `content` is a placeholder often enough that offering it would
    /// put "TODO" in a user's file, and the body is the one thing weak models
    /// are actually good at.
    pub(in crate::kernel::agentloop::guided) fn suggested_arg(
        &self,
        ctx: &AgentRunContext,
        req: &RunRequest,
        entry: &CatalogEntry,
        arg: &str,
    ) -> Option<String> {
        if arg == "message" && entry.name == "final" {
            // **Attributed, or not offered at all** (v12.0.0). This used to be
            // the most recent countable field on the transcript with no check
            // that it came from the tool the question was about — see
            // [`crate::kernel::transcript::attributed_count`], which is the
            // whole of that rule and is shared with VERIFY so one run cannot
            // surface a number two ways.
            return crate::kernel::transcript::attributed_count(&req.message, &ctx.transcript);
        }
        if arg != "path" {
            let line = entry
                .required
                .iter()
                .any(|spec| spec.name == arg && spec.kind == ArgKind::Line);
            return line.then(|| self.plan_arg(ctx, entry, arg)).flatten();
        }
        if let Some(path) = path_named_in_request(&req.message, entry, &self.deps.workspace) {
            return Some(self.workspace_relative(&path));
        }
        if let Some(step) = ctx
            .steps()
            .iter()
            .find(|step| step.get("action").and_then(Value::as_str) == Some(entry.name.as_str()))
        {
            let path = step
                .get("args")
                .and_then(|args| args.get("path"))
                .filter(|value| is_truthy(value))
                .map(py_str)
                .unwrap_or_default();
            if !path.trim().is_empty() {
                return Some(self.workspace_relative(path.trim()));
            }
        }
        // **A file another step is going to write is not this call's path**
        // (v12.0.0), where this call's tool documents one of its own. The
        // fallback below reads the plan's *pending write targets* — right for a
        // `write_file` whose path the model has not answered, wrong for a
        // `list_dir`, and a live 2B is the proof: its plan said
        // `write_file file_list.txt`, so the listing turn was offered
        // `file_list.txt` as its directory, took it, and was told
        // `Directory does not exist.` until the run halted. The tool's own
        // signature is the better answer there, and
        // [`Runtime::choose_defaulted_arg`] offers it as row one.
        if entry
            .required
            .iter()
            .any(|spec| spec.name == arg && spec.default.as_deref().is_some_and(|v| !v.is_empty()))
        {
            return None;
        }
        self.pending_plan_paths(ctx)
            .into_iter()
            .next()
            .map(|path| self.workspace_relative(&path))
    }

    /// One argument the plan already computed for this very action.
    ///
    /// **Two shapes, because weak planners write two** (v12.0.0). The first is
    /// the contract: a step whose `action` is this action, carrying the
    /// argument under its own name. The second is what a 0.5B produced live —
    /// `{"action": "command", "args": {"command": "mcp.grep",
    /// "arguments": {"text": "LatticeAI"}}}` — the tool named in a field and
    /// its arguments nested one level under it. The value the run computed is
    /// right there; only the shape is unusual, and reading it is not guessing.
    ///
    /// Inside that nested object the argument's own name is looked for first.
    /// Failing that — the planner called it `text` where the tool calls it
    /// `pattern` — a **single** string in a call that takes a **single** line
    /// argument can only be that argument. Anything less certain than that
    /// returns `None` and the model is asked, which is the honest direction:
    /// this is a default offered to a micro-turn, never a value substituted
    /// for one the model gave.
    pub(in crate::kernel::agentloop::guided) fn plan_arg(
        &self,
        ctx: &AgentRunContext,
        entry: &CatalogEntry,
        arg: &str,
    ) -> Option<String> {
        let named = |value: Option<&Value>| -> Option<String> {
            let text = value.filter(|value| is_truthy(value)).map(py_str)?;
            let text = text.trim();
            (!text.is_empty()).then(|| text.to_string())
        };
        // The keyword rule ([`crate::tools::catalog::arg_synonyms`]), applied to
        // whichever map is in front of us. The argument's own name is always
        // tried first; a synonym is only ever read when the call has nothing.
        let keyworded = |source: &serde_json::Map<String, Value>| -> Option<String> {
            std::iter::once(arg)
                .chain(crate::tools::catalog::arg_synonyms(arg).iter().copied())
                .find_map(|key| named(source.get(key)))
        };
        for step in ctx.steps() {
            let action = step.get("action").and_then(Value::as_str);
            let args = step.get("args");
            if action == Some(entry.name.as_str()) {
                // **The flat shape gets the keyword rule too** (v12.0.0). It
                // only ever ran on the nested one, and a live 0.5B's plan named
                // this very action with the right value one key over —
                // `{"action": "mcp.grep", "args": {"search_term": "LatticeAI"}}`
                // — so the run asked the model to invent a pattern while its own
                // plan carried the user's word.
                if let Some(value) = args.and_then(Value::as_object).and_then(keyworded) {
                    return Some(value);
                }
                continue;
            }
            let wraps = args
                .and_then(|args| args.get("command"))
                .and_then(Value::as_str)
                .is_some_and(|command| command.trim() == entry.name);
            if !wraps {
                continue;
            }
            let Some(nested) = args
                .and_then(|args| args.get("arguments"))
                .and_then(Value::as_object)
            else {
                continue;
            };
            if let Some(value) = keyworded(nested) {
                return Some(value);
            }
            let single_line_call = entry
                .required
                .iter()
                .filter(|spec| spec.kind == ArgKind::Line)
                .count()
                == 1;
            let mut strings = nested.values().filter(|value| value.is_string());
            if let (true, Some(only), None) = (single_line_call, strings.next(), strings.next()) {
                if let Some(value) = named(Some(only)) {
                    return Some(value);
                }
            }
        }
        None
    }

    /// A path under the workspace root, said the short way.
    ///
    /// The same file either way — this changes what the model is asked to
    /// copy, not what is written. A weak planner emits absolute paths, and a
    /// hundred-character default is a hundred characters for a 0.5B to
    /// reproduce exactly; `notes/hello.md` is four tokens.
    pub(in crate::kernel::agentloop::guided) fn workspace_relative(&self, path: &str) -> String {
        let root = self.deps.workspace.root();
        std::path::Path::new(path)
            .strip_prefix(root)
            .ok()
            .map(|relative| relative.to_string_lossy().into_owned())
            .filter(|relative| !relative.is_empty())
            .unwrap_or_else(|| path.to_string())
    }

    /// The standing context an argument turn opens with — no history.
    ///
    /// See [`crate::prompts::guided::argument_brief`] for why a value question
    /// must not be preceded by a list of lines shaped like values.
    pub(in crate::kernel::agentloop::guided) fn argument_brief(
        &self,
        ctx: &AgentRunContext,
        req: &RunRequest,
    ) -> String {
        let goal = ctx
            .plan
            .get("goal")
            .filter(|value| is_truthy(value))
            .map(py_str)
            .unwrap_or_else(|| req.message.clone());
        crate::prompts::guided::argument_brief(
            &req.message,
            &goal,
            &self.deps.workspace.root().display().to_string(),
            &self.skill_notes(ctx),
            &req.language_hint,
        )
    }

    /// The short standing context the menu turn opens with.
    ///
    /// Short is the whole design. A guided step re-sends this on every
    /// micro-turn, so a paragraph here is paid three or four times a step; the
    /// plan goal, the last two results and any skill instructions in force are
    /// what a next action actually depends on.
    pub(in crate::kernel::agentloop::guided) fn guided_brief(
        &self,
        ctx: &AgentRunContext,
        req: &RunRequest,
    ) -> String {
        let goal = ctx
            .plan
            .get("goal")
            .filter(|value| is_truthy(value))
            .map(py_str)
            .unwrap_or_else(|| req.message.clone());
        let recent: Vec<String> = ctx
            .transcript
            .iter()
            .rev()
            .filter(|step| {
                step.get("state").and_then(Value::as_str) == Some(AgentState::Executing.as_str())
            })
            .take(2)
            .map(|step| {
                let action = step.get("action").and_then(Value::as_str).unwrap_or("?");
                let outcome = if step.get("error").is_some() {
                    "failed"
                } else if step.get("result").is_some() {
                    "ok"
                } else {
                    "-"
                };
                format!("- {action}: {outcome}")
            })
            .collect();
        let done = if recent.is_empty() {
            "- (nothing yet)".to_string()
        } else {
            recent.into_iter().rev().collect::<Vec<_>>().join("\n")
        };
        crate::prompts::guided::brief_block(
            &req.message,
            &goal,
            &self.deps.workspace.root().display().to_string(),
            &done,
            &self.skill_notes(ctx),
            &req.language_hint,
        )
    }
}
