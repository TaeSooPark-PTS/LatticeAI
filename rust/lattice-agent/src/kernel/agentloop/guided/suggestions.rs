use serde_json::Value;

use super::*;
use crate::kernel::agentloop::{RunRequest, Runtime};
use crate::kernel::state::{AgentRunContext, AgentState};
use crate::parse::pystr::{is_truthy, py_str};
use crate::tools::catalog::{ArgKind, CatalogEntry};

impl Runtime {
    pub(in crate::kernel::agentloop::guided) fn suggested_arg(
        &self,
        ctx: &AgentRunContext,
        req: &RunRequest,
        entry: &CatalogEntry,
        arg: &str,
    ) -> Option<String> {
        if arg == "message" && entry.name == "final" {
            return crate::kernel::transcript::attributed_count(&req.message, &ctx.transcript);
        }
        if arg == "url" {
            return url_named_in_request(&req.message)
                .or_else(|| {
                    crate::kernel::agentloop::execution::url_from_transcript(&ctx.transcript)
                })
                .or_else(|| self.plan_arg(ctx, entry, arg));
        }
        let bare = entry
            .name
            .strip_prefix(crate::tools::catalog::MCP_PREFIX)
            .unwrap_or(entry.name.as_str());
        if matches!(bare, "computer_click" | "computer_scroll" | "computer_move")
            && matches!(arg, "x" | "y")
        {
            if let Some((_, cx, cy)) =
                crate::kernel::agentloop::execution::find_request_ui_element_center(
                    &ctx.transcript,
                    &req.message,
                )
            {
                return Some(if arg == "x" {
                    cx.to_string()
                } else {
                    cy.to_string()
                });
            }
        }
        if bare == "computer_type" && arg == "text" {
            return typed_text_in_request(&req.message).or_else(|| self.plan_arg(ctx, entry, arg));
        }
        if bare == "computer_key" && arg == "key" {
            return key_named_in_request(&req.message).or_else(|| self.plan_arg(ctx, entry, arg));
        }
        if arg != "path" {
            let line = entry
                .required
                .iter()
                .any(|spec| spec.name == arg && spec.kind == ArgKind::Line);
            return line.then(|| self.plan_arg(ctx, entry, arg)).flatten();
        }
        if !entry.name.contains("write") {
            if let Some(unread) =
                unread_existing_path_in_request(ctx, &req.message, &self.deps.workspace)
            {
                return Some(self.workspace_relative(&unread));
            }
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
        let keyworded = |source: &serde_json::Map<String, Value>| -> Option<String> {
            std::iter::once(arg)
                .chain(crate::tools::catalog::arg_synonyms(arg).iter().copied())
                .find_map(|key| named(source.get(key)))
        };
        for step in ctx.steps() {
            let action = step.get("action").and_then(Value::as_str);
            let args = step.get("args");
            if action == Some(entry.name.as_str()) {
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

    pub(in crate::kernel::agentloop::guided) fn workspace_relative(&self, path: &str) -> String {
        let root = self.deps.workspace.root();
        std::path::Path::new(path)
            .strip_prefix(root)
            .ok()
            .map(|relative| relative.to_string_lossy().into_owned())
            .filter(|relative| !relative.is_empty())
            .unwrap_or_else(|| path.to_string())
    }

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
