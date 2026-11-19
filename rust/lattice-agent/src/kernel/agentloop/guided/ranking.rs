use serde_json::Value;

use super::*;
use crate::kernel::agentloop::Runtime;
use crate::kernel::state::AgentRunContext;
use crate::kernel::transcript::{attributed_count, request_asks_for_a_count};
use crate::tools::catalog::{CatalogEntry, EntryKind};

/// The tools nearly every request needs, in the order a file task needs them.
///
/// A *ranking*, not an inventory: a run that does not offer one of these simply
/// does not show it. [`crate::prompts::CORE_TOOL_CATALOG`] owns the inventory.
const CORE_ORDER: [&str; 4] = ["write_file", "read_file", "edit_file", "list_dir"];

/// Whether the request names this catalog row.
///
/// Whole-token match, so `mcp.grep` does not light up the native `grep`
/// row, and the words "skill" / "mcp" alone do not boost every host row.
///
/// `pub(super)` since v12.0.0: the JSON dials rank their action list with the
/// same rule ([`super::execution`]), because a request that names a row must
/// reach the top of the list the model reads whichever list that is.
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

/// `name` as a whole token in `request` (case-insensitive).
///
/// A preceding `.` is part of a qualifier (`mcp.grep` is not the native
/// `grep`); a following alphanumeric is a longer word (`final` is not
/// `final_message`).
///
/// `pub(super)` since v12.0.0: [`super::fallback`]'s plan path asks the same
/// question before it re-runs a plan step, and "has this action already worked
/// this run" must have one answer in the crate rather than two.
pub(in crate::kernel::agentloop) fn action_succeeded(ctx: &AgentRunContext, name: &str) -> bool {
    ctx.transcript.iter().any(|step| {
        step.get("action").and_then(Value::as_str) == Some(name)
            && step.get("result").is_some()
            && step.get("error").is_none()
    })
}

/// Whether this run has already read a skill's instructions.
///
/// Any skill, because what the menu does with the answer is the same either
/// way: guidance is in force, so the work is what comes next. `pub(super)` is
/// not needed — the one caller is the ranking below.
fn consulted_a_skill(ctx: &AgentRunContext) -> bool {
    ctx.transcript.iter().any(|step| {
        step.get("action")
            .and_then(Value::as_str)
            .is_some_and(|action| action.starts_with(crate::tools::catalog::SKILL_PREFIX))
            && step.get("result").is_some()
            && step.get("error").is_none()
    })
}

/// A mentioned row that has already done its job this run is not what
/// comes next — otherwise a 0.5B that answers "1" repeats the skill or
/// the read until the budget runs out.
fn already_done(ctx: &AgentRunContext, entry: &CatalogEntry) -> bool {
    if entry.kind == EntryKind::Skill {
        return action_succeeded(ctx, &entry.name);
    }
    action_succeeded(ctx, &entry.name)
}

/// The file paths a request names — and never a tool it names (v12.0.0).
///
/// [`looks_like_a_path`] accepts `stem.extension`, and a qualified catalog row
/// is spelled exactly that way: `mcp.grep` is a stem, a dot and four
/// alphanumerics. So a request that said `mcp.grep으로 검색해줘` handed
/// `suggested_arg` a "path" of `mcp.grep`, the model took the offered default,
/// and a live 0.5B wrote a *file called `mcp.grep`* — then read it, then wrote
/// it again, until the loop guard stopped the run. The request had named the
/// tool it wanted, and the harness read it as the file to write.
///
/// The rule is the catalog's own two qualifiers, so it needs no catalog
/// threaded through and cannot drift from one: a token beginning
/// [`crate::tools::catalog::MCP_PREFIX`] or
/// [`crate::tools::catalog::SKILL_PREFIX`] is a tool name. Nothing else changes
/// — a real file may not be named `mcp.…` or `skill.…` in the first place,
/// because those are the two spellings this crate reserves.
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

/// The **one** term a request unambiguously names as the thing to search for.
///
/// [`paths_named_in`]'s sibling, for the other argument the user states
/// themselves (v12.0.0). Two live cells are why: handed `워크스페이스에서
/// LatticeAI라는 단어를 mcp.grep으로 찾아주고, 찾은 개수를 알려줘`, a 0.5B
/// answered the `pattern` turn with `LatticeAI mcp.grep` and a 2B answered it
/// with **the entire request sentence**. Both searches ran, both found nothing
/// over a workspace whose README contains `LatticeAI`, and both runs reported
/// `0개` and `DONE`. The term was in the user's own words the whole time.
///
/// Unambiguous means one of exactly two shapes, and nothing looser:
///
/// 1. a **quoted** string — `"LatticeAI"`, `'LatticeAI'`, `「LatticeAI」` — which
///    is the user spelling out the literal in any language;
/// 2. a request in which exactly **one** token is a searchable literal at all:
///    a catalog row the request names is a tool and not a term
///    ([`CatalogEntry::name`], bare form included), a path is a place and not a
///    term ([`looks_like_a_path`]), and a token with no ASCII core is a
///    particle of the surrounding language rather than a string to look for.
///
/// Anything else returns `None` and the model is asked, because "find the word
/// in the file" names two candidates and picking one of them would be the
/// guessing this function exists to avoid. An all-English request usually has
/// many candidates and so is left alone by construction.
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

/// The first quoted literal in a request, if it quoted one.
///
/// Multi-word search queries and typed literals (up to 8 words / 80 chars) are
/// accepted while URLs (`http://`, `https://`) are skipped and file-path tokens
/// yield to non-path quoted literals when both appear.
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

/// Extract a quoted literal specifically following `"type "` or `"입력"` for `computer_type`,
/// falling back to [`quoted_term`].
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

/// Extract an explicit keyboard key name stated in `request` for `computer_key`.
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

/// A token in the request that resolves to a file already on disk.
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

/// One implementation, in [`crate::kernel::transcript::names_token`], since
/// v12.0.0: the count-attribution rule asks the same question of the same
/// sentence, and two copies of "does the user's words name this row" is how the
/// menu and the answer come to disagree about what the user asked for.
pub(in crate::kernel::agentloop) fn named_in(request: &str, name: &str) -> bool {
    crate::kernel::transcript::names_token(request, name)
}

#[path = "domain.rs"]
mod domain;
pub use domain::*;

/// Whether this run's request declared files, and whether they are all written.
///
/// Two bits rather than a verdict, because the three states mean different
/// things to a menu: a request that declared nothing (`!declared`) is never
/// held open, one that declared and is missing something must not be offered
/// the exit, and one that declared and has delivered should be offered it
/// first. See [`Runtime::file_obligation`].
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(in crate::kernel::agentloop) struct FileObligation {
    pub declared: bool,
    pub missing: bool,
}

impl FileObligation {
    /// A file the request asked for is not on disk.
    pub(in crate::kernel::agentloop) fn owes(self) -> bool {
        self.declared && self.missing
    }

    /// Every file the request asked for is on disk.
    pub(in crate::kernel::agentloop) fn satisfied(self) -> bool {
        self.declared && !self.missing
    }
}

impl Runtime {
    /// What this run still owes the user in files, judged the way VERIFY
    /// will judge it (v12.0.0).
    ///
    /// One authority — [`crate::kernel::transcript::requirement_coverage`] —
    /// for two questions the loop used to answer separately and therefore
    /// inconsistently: the menu's *may this run finish* and verification's *is
    /// this run complete*. They were the same question all along, and answering
    /// it twice is what let a run be told "yes, finish" by the menu and "no,
    /// `notes/hello.md` is missing" by the critic three calls later.
    ///
    /// Deliberately about **declared** files and nothing else. A request that
    /// named no destination declares nothing, owes nothing and is never held
    /// open by this: `이 폴더에 파일이 몇 개야` is finished when the count is.
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

    /// The numbered menu this step offers, **most likely answer first**.
    ///
    /// Order is the whole design of this function, and the first live 0.5B run
    /// is why. The catalog arrives in the policy table's order, which is
    /// alphabetical, so row 1 was `build_project` — and a model that answers
    /// "1" because it does not know what else to say ran `build_project`
    /// fifteen times against a plan that said `write_file`. Alphabet is not a
    /// ranking, and on a menu the top row is a *suggestion*.
    ///
    /// So the rows are ranked by what this run is actually for:
    ///
    /// 1. **the plan's own actions**, in plan order — the single best guess at
    ///    what comes next, and the run already computed it;
    /// 2. **the file-work core** (`write_file`, `read_file`, `edit_file`,
    ///    `list_dir`), the tools nearly every request needs;
    /// 3. **the host's catalog** — MCP tools and skills, so they never vanish
    ///    behind forty alphabetically-luckier native names;
    /// 4. whatever still fits, in the catalog's own order;
    /// 5. `final`, always last and always present.
    ///
    /// The cap ([`MENU_LIMIT`]) is honest about what it costs: a run needing a
    /// tool that is neither planned nor core may not find it on the menu. The
    /// plan is what puts a tool in reach, which is a reason for the planner to
    /// be right rather than a reason to show a small model forty rows.
    #[cfg(test)]
    pub(in crate::kernel::agentloop) fn step_catalog(
        &self,
        ctx: &AgentRunContext,
    ) -> Vec<CatalogEntry> {
        self.rank_catalog(ctx, "")
    }

    /// Everything this run may choose, in the catalog's own order.
    ///
    /// The run's native tools merged with the host's, plus the one thing merge
    /// alone gets wrong: a prefixed row whose bare name the run also governs is
    /// dropped as a duplicate capability — right, until the *request names the
    /// prefixed form*. `mcp.grep으로 찾아줘` asks for a row that merge just
    /// deleted, and a catalog with no `mcp.grep` on it is a catalog that cannot
    /// answer the request as asked.
    ///
    /// Shared by both dials since v12.0.0 (the guided menu ranks it, the JSON
    /// prompt lists it), because "what may this run choose" must have one
    /// answer — a row a model can see on the menu and not in the prompt is a
    /// capability that appears and disappears with the profile.
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

    /// [`step_catalog`], ranked against the user's own words.
    ///
    /// A row the request *names* (a tool, `skill.code_review`, `mcp.grep`)
    /// is offered first: a 0.5B that answers "1" then does the thing it was
    /// asked to do, and a skill or MCP row that merge dropped as a duplicate
    /// of a native tool is put back so the prefixed name can be chosen. The
    /// empty-request path is what the unit tests exercise and is unchanged.
    pub fn rank_catalog(&self, ctx: &AgentRunContext, request: &str) -> Vec<CatalogEntry> {
        let merged = self.run_catalog(request);
        // The plan's **pending** actions, in plan order. A step whose file this
        // run has already written is not what comes next: the first live 0.5B
        // run wrote `notes/hello.md`, was offered `write_file` as row one again,
        // answered "1" again, and spent the rest of its budget being stopped by
        // the loop guard instead of finishing.
        let done = crate::kernel::transcript::files_written(
            &ctx.transcript,
            &self.deps.file_create_actions,
        );
        let obligation = self.file_obligation(ctx, request);
        // **The user's declared outputs retire the plan's own** (v12.0.0). A
        // weak planner invents a path: handed `인사말을 notes/hello.md 파일로
        // 저장해줘`, a live 2B planned `write_file notes.md`. The run then wrote
        // the file the *request* named, and the plan's invented path stayed
        // "pending" for ever — so `write_file` stayed row one, the model
        // answered "1" again, and the loop guard stopped a run whose
        // deliverable had been on disk since step one. Once every file the
        // request declared exists there is nothing left to write, whatever a
        // planner wrote down, so nothing is pending.
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
                    // A planned write whose file is not pending has happened.
                    if !path.is_empty()
                        && !pending_paths
                            .iter()
                            .any(|left| left.ends_with(path) || path.ends_with(left.as_str()))
                    {
                        continue;
                    }
                } else if action_succeeded(ctx, action) {
                    // **And a planned read that has happened has happened too**
                    // (v12.0.0). The pending-path test above retired a finished
                    // write; nothing retired a finished read, so a plan that
                    // said `read_file` then `write_file` kept offering
                    // `read_file` as row one for the rest of the run. A live 2B
                    // read `README.md`, was offered the read again, and never
                    // reached the write it was asked for. One rule, both halves
                    // of a plan: a step that has already succeeded is not what
                    // comes next.
                    continue;
                }
                seen.push(action.to_string());
            }
            seen
        };
        // Every file the plan asked for is written: finishing is the most
        // likely next action, so it is row one rather than row nine. `final` is
        // otherwise last, and either way it is always on the menu.
        //
        // A plan that named **no** files is never "complete" here — with no
        // steps to finish there is nothing to conclude from, and putting
        // `final` at row one would make "1" mean *stop* for every run whose
        // planner produced nothing.
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
            // **And no write the *user* asked for may still be waiting**
            // (v12.0.0). Without that clause a plan of `read_file` then
            // `write_file` declared itself complete the moment the read
            // returned, `final` took row one, and a live 2B answered "1" —
            // reporting success over `notes/summary.md`, which it had never
            // written.
            //
            // The test is the request's own declared outputs, not the plan's
            // pending paths, and the difference decides two live runs in
            // opposite directions. A request that names a destination file is
            // not finished until that file exists. A request that names none —
            // "find the word and tell me how many" — is finished when the
            // search is, and judging *that* one by the plan's paths kept a
            // 0.5B on a `write_file` row its planner invented until the step
            // budget ran out, for a request that never asked for a file.
            let user_wants_files =
                !crate::parse::inference::requested_output_paths(request).is_empty();
            !planned_reads.is_empty()
                && (!user_wants_files || pending_paths.is_empty())
                && planned_reads
                    .iter()
                    .all(|action| action_succeeded(ctx, action))
        };
        // …and a run that owes nothing is finished whatever its planner wrote
        // down. Without this clause a plan with **no** steps at all (a live 2B
        // produced one for the skill request) left `final` at row nine over a
        // workspace where every declared file already existed.
        // **A count question whose count is in hand is finished** (v12.0.0).
        // The deliverable of `파일 개수를 알려줘` is the number, and once a tool
        // the request named has returned one there is nothing else to do with
        // it but say it. Without this the menu offered `write_file` as row one
        // to a 0.5B that had just counted the folder, and "1" spent the rest of
        // the run writing a file nobody asked for. It cannot fire for a request
        // that declared a file — `final` is not on that menu at all while one is
        // missing — and it reads the same attribution VERIFY does, so the menu
        // and the answer agree about whether the run has counted.
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
        // A request that names a file already in the workspace is a read
        // first, then a write — S2 is this shape. Boosting `read_file`
        // only when the file *exists* keeps a create-only request (S1)
        // on `write_file`. Once every existing file named in the request
        // has been read, drop the boost so the next "1" is the write or final.
        if merged.iter().any(|entry| entry.name == "read_file")
            && !mentioned.iter().any(|name| name == "read_file")
            && (unread_existing.is_some()
                || (!action_succeeded(ctx, "read_file")
                    && request_points_at_existing_file(request, &self.deps.workspace)))
        {
            mentioned.insert(0, "read_file".into());
        }
        // **Guidance read, work next** (v12.0.0). A skill is instructions, and
        // the thing to do with instructions is the job they are about. A live
        // 0.5B consulted `skill.code_review` and then spent the rest of its run
        // on `read_file notes/review_note.md` — the file the request had asked
        // it to *create* — because its planner had named that read and the menu
        // had nothing better to offer. Once the guidance is in force and a file
        // the request declared is still missing, writing that file is what
        // comes next, and it is row one.
        //
        // Both halves of the condition matter. Before the consult the skill row
        // itself is row one (the request names it), so this cannot talk a run
        // out of reading the guidance it was told to read; and a request that
        // declared no file owes none, so nothing is boosted for
        // `이 폴더에 파일이 몇 개야`.
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
            // Named in the request first: "list_dir로 확인하고" or
            // "code_review 스킬" must be row one, or a 0.5B that answers "1"
            // never reaches the row the task actually named.
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
                // **A run that still owes a file cannot be offered the exit**
                // (v12.0.0). Ranking `final` last was not enough: it is a row,
                // and a weak model picks rows. Three live 0.5B cells — every
                // one of the three whose request named a destination file —
                // resolved their first menu turn to `final`, wrote nothing, and
                // reported themselves finished; verification then had to catch
                // it with the missing-file gate and apologise.
                //
                // The condition is the one verification already enforces
                // ([`crate::kernel::transcript::requirement_coverage`]), so the
                // menu now refuses what the critic would refuse anyway, one
                // phase earlier and without spending the run. A request that
                // declared no destination file is untouched: "how many files
                // are there" is finished when the count is, so `final` stays on
                // its menu from the first turn.
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
        // `position` breaks every tie, so the order is a total one: the same
        // catalog numbers the same way on every turn of the same run, which is
        // what makes "answer 2" mean one thing.
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
