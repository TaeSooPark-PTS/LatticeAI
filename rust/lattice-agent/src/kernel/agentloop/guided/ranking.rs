use serde_json::Value;

use super::*;
use crate::kernel::agentloop::Runtime;
use crate::kernel::state::AgentRunContext;
use crate::kernel::transcript::{attributed_count, request_asks_for_a_count};
use crate::tools::catalog::{CatalogEntry, EntryKind};

const CORE_ORDER: [&str; 4] = ["write_file", "read_file", "edit_file", "list_dir"];

pub(in crate::kernel::agentloop) fn request_names(request: &str, entry: &CatalogEntry) -> bool {
    if request.trim().is_empty() {
        return false;
    }
    if named_in(request, &entry.name) {
        return true;
    }
    if let Some(bare) = entry
        .name
        .strip_prefix(crate::tools::catalog::MCP_PREFIX)
        .or_else(|| entry.name.strip_prefix(crate::tools::catalog::SKILL_PREFIX))
    {
        return !bare.is_empty() && named_in(request, bare);
    }
    false
}

pub(in crate::kernel::agentloop) fn action_succeeded(ctx: &AgentRunContext, name: &str) -> bool {
    ctx.transcript.iter().any(|step| {
        step.get("action").and_then(Value::as_str) == Some(name)
            && step.get("result").is_some()
            && step.get("error").is_none()
    })
}

fn consulted_a_skill(ctx: &AgentRunContext) -> bool {
    ctx.transcript.iter().any(|step| {
        step.get("action")
            .and_then(Value::as_str)
            .is_some_and(|action| action.starts_with(crate::tools::catalog::SKILL_PREFIX))
            && step.get("result").is_some()
            && step.get("error").is_none()
    })
}

fn already_done(ctx: &AgentRunContext, entry: &CatalogEntry) -> bool {
    if entry.kind == EntryKind::Skill {
        return action_succeeded(ctx, &entry.name);
    }
    action_succeeded(ctx, &entry.name)
}

pub(in crate::kernel::agentloop) fn paths_named_in(request: &str) -> Vec<String> {
    request
        .split_whitespace()
        .map(|raw| {
            raw.trim_matches(|character: char| {
                !character.is_ascii_alphanumeric()
                    && character != '/'
                    && character != '.'
                    && character != '_'
                    && character != '-'
            })
            .to_string()
        })
        .filter(|token| {
            looks_like_a_path(token)
                && !token.starts_with("http")
                && !token.starts_with(crate::tools::catalog::MCP_PREFIX)
                && !token.starts_with(crate::tools::catalog::SKILL_PREFIX)
        })
        .collect()
}

pub(in crate::kernel::agentloop) fn unread_existing_path_in_request(
    ctx: &AgentRunContext,
    request: &str,
    workspace: &crate::tools::sandbox::Workspace,
) -> Option<String> {
    let already_read = |candidate: &str| {
        let cand_clean = candidate.trim().trim_start_matches("./");
        ctx.transcript.iter().any(|step| {
            if step.get("error").is_some() {
                return false;
            }
            let Some(act) = step.get("action").and_then(Value::as_str) else {
                return false;
            };
            let bare = act.strip_prefix("mcp.").unwrap_or(act);
            if bare != "read_file" {
                return false;
            }
            step.get("args")
                .and_then(|a| a.get("path"))
                .or_else(|| step.get("result").and_then(|r| r.get("path")))
                .and_then(Value::as_str)
                .is_some_and(|p| {
                    let p_clean = p.trim().trim_start_matches("./");
                    p_clean == cand_clean
                        || p_clean.ends_with(cand_clean)
                        || cand_clean.ends_with(p_clean)
                })
        })
    };
    for path in paths_named_in(request) {
        if workspace
            .resolve(&path)
            .map(|resolved| resolved.is_file())
            .unwrap_or(false)
            && !already_read(&path)
        {
            return Some(path);
        }
        if workspace
            .resolve(&path)
            .map(|resolved| resolved.is_dir())
            .unwrap_or(false)
        {
            if let Ok(resolved_dir) = workspace.resolve(&path) {
                if let Ok(entries) = std::fs::read_dir(&resolved_dir) {
                    let mut files: Vec<String> = entries
                        .filter_map(|e| e.ok())
                        .filter(|e| e.path().is_file())
                        .map(|e| {
                            let name = e.file_name().to_string_lossy().into_owned();
                            let dir_clean = path.trim_end_matches('/');
                            format!("{dir_clean}/{name}")
                        })
                        .collect();
                    files.sort();
                    if let Some(unread) = files.into_iter().find(|f| !already_read(f)) {
                        return Some(unread);
                    }
                }
            }
        }
    }
    None
}

pub(in crate::kernel::agentloop) fn path_named_in_request(
    request: &str,
    entry: &CatalogEntry,
    workspace: &crate::tools::sandbox::Workspace,
) -> Option<String> {
    let named = paths_named_in(request);
    if named.is_empty() {
        return None;
    }
    let writing = entry.name.contains("write");
    if writing {
        return named.into_iter().next_back();
    }
    named
        .iter()
        .find(|path| {
            workspace
                .resolve(path)
                .map(|resolved| resolved.is_file())
                .unwrap_or(false)
        })
        .cloned()
        .or_else(|| named.into_iter().next())
}

pub(in crate::kernel::agentloop) fn term_named_in_request(
    request: &str,
    catalog: &[CatalogEntry],
) -> Option<String> {
    if let Some(quoted) = quoted_term(request) {
        return Some(quoted);
    }
    let is_row = |token: &str| {
        catalog.iter().any(|entry| {
            entry.name.eq_ignore_ascii_case(token)
                || entry
                    .name
                    .strip_prefix(crate::tools::catalog::MCP_PREFIX)
                    .or_else(|| entry.name.strip_prefix(crate::tools::catalog::SKILL_PREFIX))
                    .is_some_and(|bare| bare.eq_ignore_ascii_case(token))
        })
    };
    let mut candidates = request.split_whitespace().filter_map(|raw| {
        let token = raw.trim_matches(|character: char| {
            !character.is_ascii_alphanumeric()
                && character != '/'
                && character != '.'
                && character != '_'
                && character != '-'
        });
        (token.chars().count() >= 2
            && token.chars().any(|c| c.is_ascii_alphabetic())
            && !looks_like_a_path(token)
            && !is_row(token))
        .then(|| token.to_string())
    });
    match (candidates.next(), candidates.next()) {
        (Some(only), None) => Some(only),
        _ => None,
    }
}

pub(in crate::kernel::agentloop) fn quoted_term(request: &str) -> Option<String> {
    const PAIRS: [(char, char); 5] = [
        ('"', '"'),
        ('\'', '\''),
        ('「', '」'),
        ('“', '”'),
        ('‘', '’'),
    ];
    let mut fallback_path: Option<String> = None;
    for (open, close) in &PAIRS {
        let mut rest = request;
        while let Some(pos) = rest.find(*open) {
            let after_open = &rest[pos + open.len_utf8()..];
            let Some(end) = after_open.find(*close) else {
                break;
            };
            let inner = after_open[..end].trim();
            rest = &after_open[end + close.len_utf8()..];
            if inner.is_empty()
                || inner.contains('\n')
                || inner.starts_with("http://")
                || inner.starts_with("https://")
                || inner.split_whitespace().count() > 8
                || inner.chars().count() > 80
            {
                continue;
            }
            if looks_like_a_path(inner) && !inner.contains(char::is_whitespace) {
                if fallback_path.is_none() {
                    fallback_path = Some(inner.to_string());
                }
                continue;
            }
            return Some(inner.to_string());
        }
    }
    fallback_path
}

pub(in crate::kernel::agentloop) fn typed_text_in_request(request: &str) -> Option<String> {
    let lower = request.to_lowercase();
    for marker in ["type ", "입력"] {
        if let Some(idx) = lower.find(marker) {
            let tail = &request[idx + marker.len()..];
            const PAIRS: [(char, char); 5] = [
                ('\'', '\''),
                ('"', '"'),
                ('「', '」'),
                ('“', '”'),
                ('‘', '’'),
            ];
            for (open, close) in &PAIRS {
                if let Some(s) = tail.find(*open) {
                    if s <= 16 {
                        let after = &tail[s + open.len_utf8()..];
                        if let Some(e) = after.find(*close) {
                            let inner = after[..e].trim();
                            if !inner.is_empty() && !inner.contains('\n') {
                                return Some(inner.to_string());
                            }
                        }
                    }
                }
            }
        }
    }
    quoted_term(request)
}

pub(in crate::kernel::agentloop) fn key_named_in_request(request: &str) -> Option<String> {
    let lower = request.to_lowercase();
    for (cues, canonical) in [
        (
            &["press enter", "hit enter", "enter key", "엔터"][..],
            "return",
        ),
        (&["press return", "return key"][..], "return"),
        (
            &["press escape", "press esc", "escape key", "esc key"][..],
            "escape",
        ),
        (&["press tab", "tab key"][..], "tab"),
        (&["press space", "spacebar"][..], "space"),
    ] {
        if cues.iter().any(|c| lower.contains(c)) {
            return Some(canonical.to_string());
        }
    }
    None
}

pub(in crate::kernel::agentloop) fn path_agrees_with_request(
    answered: &str,
    request: &str,
) -> bool {
    let named = paths_named_in(request);
    if named.is_empty() {
        return true;
    }
    let answered = answered.trim().trim_start_matches("./");
    named.iter().any(|path| {
        let path = path.trim_start_matches("./");
        answered == path || answered.ends_with(path) || path.ends_with(answered)
    })
}

pub(in crate::kernel::agentloop) fn request_points_at_existing_file(
    request: &str,
    workspace: &crate::tools::sandbox::Workspace,
) -> bool {
    for raw in request.split_whitespace() {
        let token = raw.trim_matches(|character: char| {
            !character.is_ascii_alphanumeric()
                && character != '/'
                && character != '.'
                && character != '_'
                && character != '-'
        });
        if token.is_empty() || !token.contains('.') {
            continue;
        }
        if workspace
            .resolve(token)
            .map(|path| path.is_file())
            .unwrap_or(false)
        {
            return true;
        }
    }
    false
}

pub(in crate::kernel::agentloop) fn named_in(request: &str, name: &str) -> bool {
    crate::kernel::transcript::names_token(request, name)
}

#[path = "domain.rs"]
mod domain;
pub use domain::*;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(in crate::kernel::agentloop) struct FileObligation {
    pub declared: bool,
    pub missing: bool,
}

impl FileObligation {
    pub(in crate::kernel::agentloop) fn owes(self) -> bool {
        self.declared && self.missing
    }

    pub(in crate::kernel::agentloop) fn satisfied(self) -> bool {
        self.declared && !self.missing
    }
}

impl Runtime {
    pub(super) fn file_obligation(&self, ctx: &AgentRunContext, request: &str) -> FileObligation {
        let coverage = crate::kernel::transcript::requirement_coverage(
            request,
            &ctx.transcript,
            &self.deps.file_create_actions,
        );
        let count = |at: &str| {
            coverage
                .pointer(at)
                .and_then(Value::as_array)
                .map(Vec::len)
                .unwrap_or_default()
        };
        FileObligation {
            declared: count("/files/declared") > 0,
            missing: count("/missing_files") > 0,
        }
    }

    #[cfg(test)]
    pub(in crate::kernel::agentloop) fn step_catalog(
        &self,
        ctx: &AgentRunContext,
    ) -> Vec<CatalogEntry> {
        self.rank_catalog(ctx, "")
    }

    pub(in crate::kernel::agentloop) fn run_catalog(&self, request: &str) -> Vec<CatalogEntry> {
        let native = crate::tools::catalog::native_entries(&self.deps.tool_names);
        let external = match &self.deps.external {
            Some(catalog) => catalog.entries(),
            None => Vec::new(),
        };
        let mut merged = crate::tools::catalog::merge(native, external.clone());
        if !request.trim().is_empty() {
            let present: std::collections::BTreeSet<String> =
                merged.iter().map(|entry| entry.name.clone()).collect();
            for entry in external {
                if present.contains(&entry.name) {
                    continue;
                }
                if request_names(request, &entry) {
                    merged.push(entry);
                }
            }
        }
        merged
    }

    pub fn rank_catalog(&self, ctx: &AgentRunContext, request: &str) -> Vec<CatalogEntry> {
        let merged = self.run_catalog(request);
        let done = crate::kernel::transcript::files_written(
            &ctx.transcript,
            &self.deps.file_create_actions,
        );
        let obligation = self.file_obligation(ctx, request);
        let pending_paths = if obligation.satisfied() {
            Vec::new()
        } else {
            self.pending_plan_paths(ctx)
        };
        let planned: Vec<String> = {
            let mut seen: Vec<String> = Vec::new();
            for step in ctx.steps() {
                let Some(action) = step.get("action").and_then(Value::as_str) else {
                    continue;
                };
                if action.is_empty() || seen.iter().any(|known| known == action) {
                    continue;
                }
                if self.deps.file_create_actions.contains(action) {
                    let path = step
                        .get("args")
                        .and_then(|args| args.get("path"))
                        .and_then(Value::as_str)
                        .unwrap_or_default()
                        .trim();
                    if !path.is_empty()
                        && !pending_paths
                            .iter()
                            .any(|left| left.ends_with(path) || path.ends_with(left.as_str()))
                    {
                        continue;
                    }
                } else if action_succeeded(ctx, action) {
                    continue;
                }
                seen.push(action.to_string());
            }
            seen
        };
        let plan_wanted_files = ctx.steps().iter().any(|step| {
            step.get("action")
                .and_then(Value::as_str)
                .is_some_and(|action| self.deps.file_create_actions.contains(action))
        });
        let non_write_plan_done = {
            let steps = ctx.steps();
            let planned_reads: Vec<&str> = steps
                .iter()
                .filter_map(|step| step.get("action").and_then(Value::as_str))
                .filter(|action| {
                    !action.is_empty() && !self.deps.file_create_actions.contains(*action)
                })
                .collect();
            let user_wants_files =
                !crate::parse::inference::requested_output_paths(request).is_empty();
            !planned_reads.is_empty()
                && (!user_wants_files || pending_paths.is_empty())
                && planned_reads
                    .iter()
                    .all(|action| action_succeeded(ctx, action))
        };
        let count_in_hand = request_asks_for_a_count(request)
            && attributed_count(request, &ctx.transcript).is_some();
        let explicitly_named: Vec<String> = merged
            .iter()
            .filter(|entry| entry.name != "final" && request_names(request, entry))
            .map(|entry| entry.name.clone())
            .collect();
        let has_non_skill_named = merged.iter().any(|entry| {
            entry.name != "final" && entry.kind != EntryKind::Skill && request_names(request, entry)
        });
        let is_ui_obs = |name: &str| {
            matches!(
                name.strip_prefix("mcp.").unwrap_or(name),
                "computer_ui_inspect" | "computer_screenshot"
            )
        };
        let has_ui_actuation = [
            "computer_click",
            "computer_type",
            "computer_key",
            "computer_scroll",
            "computer_move",
            "computer_drag",
            "computer_open_app",
            "computer_open_url",
        ]
        .iter()
        .any(|act| action_succeeded(ctx, act));
        let only_ui_obs_named =
            !explicitly_named.is_empty() && explicitly_named.iter().all(|n| is_ui_obs(n.as_str()));
        let unread_existing = unread_existing_path_in_request(ctx, request, &self.deps.workspace);
        let existing_reads_done = !obligation.owes()
            && !has_non_skill_named
            && !request_wants_computer_use(request)
            && !request_wants_web(request)
            && !request_wants_knowledge(request)
            && unread_existing.is_none()
            && action_succeeded(ctx, "read_file");
        let named_work_done = has_non_skill_named
            && !obligation.owes()
            && unread_existing.is_none()
            && (!only_ui_obs_named || has_ui_actuation)
            && explicitly_named
                .iter()
                .all(|name| action_succeeded(ctx, name));
        let plan_complete = (plan_wanted_files && pending_paths.is_empty() && !done.is_empty())
            || non_write_plan_done
            || obligation.satisfied()
            || count_in_hand
            || named_work_done
            || existing_reads_done;

        let mut mentioned: Vec<String> = merged
            .iter()
            .filter(|entry| request_names(request, entry))
            .filter(|entry| !already_done(ctx, entry))
            .map(|entry| entry.name.clone())
            .collect();
        if merged.iter().any(|entry| entry.name == "read_file")
            && !mentioned.iter().any(|name| name == "read_file")
            && (unread_existing.is_some()
                || (!action_succeeded(ctx, "read_file")
                    && request_points_at_existing_file(request, &self.deps.workspace)))
        {
            mentioned.insert(0, "read_file".into());
        }
        if obligation.owes()
            && merged.iter().any(|entry| entry.name == "write_file")
            && !mentioned.iter().any(|name| name == "write_file")
            && consulted_a_skill(ctx)
        {
            mentioned.insert(0, "write_file".into());
        }

        let ui_observed = has_ui_observation(ctx);
        if request_wants_computer_use(request) && !ui_observed {
            for obs in [
                "mcp.computer_screenshot",
                "mcp.computer_ui_inspect",
                "computer_screenshot",
                "computer_ui_inspect",
            ] {
                if let Some(entry) = merged.iter().find(|entry| entry.name == obs) {
                    if !already_done(ctx, entry) {
                        mentioned.retain(|name| name != obs);
                        mentioned.insert(0, obs.to_string());
                    }
                }
            }
        }
        if !named_work_done && (!has_non_skill_named || (only_ui_obs_named && !has_ui_actuation)) {
            for candidate in domain_priority_tools(request, ui_observed) {
                if let Some(entry) = merged.iter().find(|entry| entry.name == candidate) {
                    if !already_done(ctx, entry) && !mentioned.iter().any(|name| name == candidate)
                    {
                        mentioned.push(entry.name.clone());
                    }
                }
            }
        }

        let rank = |entry: &CatalogEntry| -> usize {
            if let Some(index) = mentioned.iter().position(|name| name == &entry.name) {
                return index;
            }
            if let Some(index) = planned.iter().position(|name| name == &entry.name) {
                return mentioned.len() + index;
            }
            if let Some(index) = CORE_ORDER.iter().position(|name| *name == entry.name) {
                return mentioned.len() + planned.len() + index;
            }
            if entry.kind != EntryKind::Native {
                return mentioned.len() + planned.len() + CORE_ORDER.len();
            }
            mentioned.len() + planned.len() + CORE_ORDER.len() + 1
        };

        let mut last: Option<CatalogEntry> = None;
        let mut first: Option<CatalogEntry> = None;
        let mut rows: Vec<(usize, usize, CatalogEntry)> = Vec::with_capacity(merged.len());
        for (position, entry) in merged.into_iter().enumerate() {
            if entry.name == "final" {
                if obligation.owes() {
                    continue;
                }
                if plan_complete {
                    first = Some(entry);
                } else {
                    last = Some(entry);
                }
                continue;
            }
            rows.push((rank(&entry), position, entry));
        }
        rows.sort_by_key(|(rank, position, _)| (*rank, *position));
        let mut kept: Vec<CatalogEntry> = first.into_iter().collect();
        kept.extend(
            rows.into_iter()
                .map(|(_, _, entry)| entry)
                .take(MENU_LIMIT.saturating_sub(1)),
        );
        kept.extend(last);
        kept
    }
}
