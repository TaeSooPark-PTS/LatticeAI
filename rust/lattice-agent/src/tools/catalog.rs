//! **One catalog** — native tools, skills and MCP tools, said the same way.
//!
//! Until v12.0.0 a run had three different vocabularies for "things the model
//! may do". Native tools were names in a policy table; skills were three-line
//! briefs rendered into the bottom of a prompt and never callable; MCP tools
//! were a surface the *user's* editor could reach and the loop could not. A
//! model was therefore told about capabilities it had no way to invoke, which
//! is worse than not mentioning them.
//!
//! This module is the single vocabulary. A [`CatalogEntry`] is one thing the
//! model may choose, whatever provides it, carrying what the harness needs to
//! ask for it one argument at a time ([`crate::kernel::agentloop::guided`]) and
//! what the prompt needs to name it.
//!
//! ## The three kinds, and what each one *is*
//!
//! * [`EntryKind::Native`] — a tool this run's policy table governs. Dispatch
//!   is the loop's ordinary path: gates, then [`ToolHost`] or the worker seam.
//! * [`EntryKind::Mcp`] — a tool the host's MCP surface exposes, named
//!   `mcp.<tool>`. Where the bare name is *also* in the run's policy table,
//!   [`resolve`] hands it to the native path — that is deliberately the
//!   **stricter** of the two governance chains (the kernel's breaker,
//!   destructive, overwrite and approval gates are a superset of the MCP
//!   surface's role check), and it means one tool is never governed two ways
//!   depending on which name the model happened to pick.
//! * [`EntryKind::Skill`] — installed guidance, named `skill.<name>`. **A skill
//!   is not an executable**, and the harness says so in the menu rather than
//!   letting a model discover it: selecting one returns its `SKILL.md`
//!   instructions, which the loop puts in front of the model for the following
//!   steps. Nothing runs, nothing is written, and the run still has to choose a
//!   real tool afterwards.
//!
//! ## What must never go here
//!
//! * **A hard-coded tool list.** The entries are built from the run's own
//!   policy table and from what the host injects. A second inventory here would
//!   be the drift `crate::kernel` refuses for policies, in a new place.
//! * **A permission decision.** [`resolve`] routes; it never allows. The gate
//!   chain is [`crate::kernel::agentloop::gates`]', for every kind.

use std::collections::BTreeSet;

use serde_json::{Map, Value};

use super::host::{CallScope, ToolFuture};

#[path = "catalog_args.rs"]
mod catalog_args;
pub use catalog_args::{
    adopt_named_args, arg_synonyms, defaulted_arg, entry_for, fill_documented_defaults,
    is_search_arg, missing_required,
};
use catalog_args::{native_args, native_summary};

/// The prefix an MCP-surface tool is offered under.
pub const MCP_PREFIX: &str = "mcp.";

/// The prefix an installed skill is offered under — the same spelling
/// `lattice-platform`'s MCP `tools/list` uses, so one name means one thing on
/// both surfaces.
pub const SKILL_PREFIX: &str = "skill.";

/// What provides a catalog entry.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum EntryKind {
    Native,
    Mcp,
    Skill,
}

impl EntryKind {
    /// The word the menu prints beside the entry.
    pub fn label(self) -> &'static str {
        match self {
            EntryKind::Native => "tool",
            EntryKind::Mcp => "mcp",
            EntryKind::Skill => "skill",
        }
    }
}

/// How one argument is asked for when the step is decomposed.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ArgKind {
    /// One line: a path, a name, a query. The reply is trimmed to its first
    /// non-empty line.
    Line,
    /// Free-form text: a file body, a note, a message. **Not** JSON — no
    /// escaping, no quoting, no braces. This is the argument weak models are
    /// good at and the one that breaks them inside a JSON object.
    Text,
}

/// One argument of one entry.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ArgSpec {
    pub name: String,
    pub kind: ArgKind,
    /// One line of help, shown when the argument is asked for.
    pub hint: String,
    /// What **the tool itself documents** for an omitted value.
    ///
    /// Not a harness guess and never a value invented here: `list_dir` is
    /// `def list_dir(path: str = ".")`, so a `list_dir` with no `path` is a
    /// call the tool services rather than a call the tool refuses. Until
    /// v12.0.0 the catalog carried only "required", the harness read that as
    /// "the model must produce one", and a live 2B spent nine dispatches and
    /// three `LOOP_DETECTED` halts being refused a listing the tool would have
    /// returned.
    ///
    /// Three states, and each says something different:
    ///
    /// * `None` — genuinely default-less. Omitting it is a call the tool
    ///   cannot service, so [`missing_required`] refuses it here, with a
    ///   sentence, rather than letting the seam answer `'pattern'`;
    /// * `Some(value)` — the tool documents this literal.
    ///   [`fill_documented_defaults`] supplies it and nothing is refused;
    /// * `Some("")` — the tool documents the argument as omissible and derives
    ///   its own (`knowledge_save`'s title comes from the note's first line).
    ///   Nothing is filled — there is no literal to fill with — and nothing is
    ///   refused. The argument is still *asked for*, because a title the user
    ///   chose is better than one derived, and still optional, because a run
    ///   that cannot phrase one must not be blocked on it.
    pub default: Option<String>,
}

impl ArgSpec {
    /// A one-line argument the caller must supply.
    pub fn line(name: &str, hint: &str) -> Self {
        Self {
            name: name.to_string(),
            kind: ArgKind::Line,
            hint: hint.to_string(),
            default: None,
        }
    }

    /// A one-line argument the tool documents a default for.
    pub fn line_defaulting(name: &str, hint: &str, default: &str) -> Self {
        Self {
            default: Some(default.to_string()),
            ..Self::line(name, hint)
        }
    }

    /// A free-form body argument.
    pub fn text(name: &str, hint: &str) -> Self {
        Self {
            name: name.to_string(),
            kind: ArgKind::Text,
            hint: hint.to_string(),
            default: None,
        }
    }
}

/// One thing the model may choose this step.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CatalogEntry {
    /// The fully qualified name: `write_file`, `mcp.grep`, `skill.code_review`.
    pub name: String,
    pub kind: EntryKind,
    /// One line: what choosing it does.
    pub summary: String,
    /// Arguments the harness must collect before it can dispatch.
    pub required: Vec<ArgSpec>,
}

impl CatalogEntry {
    /// `name{arg, arg}` — the signature the prompt renders.
    pub fn signature(&self) -> String {
        if self.required.is_empty() {
            return self.name.clone();
        }
        let args: Vec<&str> = self
            .required
            .iter()
            .map(|spec| spec.name.as_str())
            .collect();
        format!("{}{{{}}}", self.name, args.join(", "))
    }
}

/// Where a chosen name has to be dispatched.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Resolved<'a> {
    /// The run's own tool of this (bare) name, through the ordinary gates.
    Native(&'a str),
    /// The host's catalog port, under this (qualified) name.
    External(&'a str),
}

/// Route one chosen name.
///
/// `native_known` is the run's own inventory — the policy table's keys plus the
/// tools this crate executes. The rule is one line and it is the whole
/// governance story for `mcp.*`: **a prefixed name whose bare form the run
/// already governs is the run's own tool**, so it passes the kernel's gate
/// chain rather than a shorter one. Only names the run has no policy for reach
/// the external port, where the host applies the platform's own check.
pub fn resolve<'a>(name: &'a str, native_known: impl Fn(&str) -> bool) -> Resolved<'a> {
    if let Some(bare) = name.strip_prefix(MCP_PREFIX) {
        if native_known(bare) {
            return Resolved::Native(bare);
        }
        return Resolved::External(name);
    }
    if name.starts_with(SKILL_PREFIX) {
        return Resolved::External(name);
    }
    Resolved::Native(name)
}

/// Whether this name addresses the external catalog at all.
pub fn is_external_name(name: &str) -> bool {
    name.starts_with(MCP_PREFIX) || name.starts_with(SKILL_PREFIX)
}

/// The host's catalog of everything the loop does not implement itself.
///
/// Injected, never constructed here, for the reason every other port in this
/// crate is injected: the registry that knows what is installed lives above
/// this crate in the dependency graph. `None` is the standalone contract — no
/// MCP surface and no skills in reach, so the catalog is the run's own tools
/// and nothing is silently missing.
pub trait ToolCatalog: std::fmt::Debug + Send + Sync {
    /// What this host offers, already qualified with its prefix.
    ///
    /// Called once per executor step, so it must be cheap or cached; a scan of
    /// a skills directory per micro-turn would be paid dozens of times a run.
    fn entries(&self) -> Vec<CatalogEntry>;

    /// Run one qualified name. Every failure is an outcome, never a panic —
    /// the same contract as [`ToolHost::execute`].
    ///
    /// [`ToolHost::execute`]: super::host::ToolHost::execute
    fn execute<'a>(
        &'a self,
        name: &'a str,
        args: &'a Map<String, Value>,
        scope: &'a CallScope,
    ) -> ToolFuture<'a>;
}

/// The run's own tools as catalog entries, `final` last.
///
/// `tool_names` is the run's inventory, exactly as the prompt uses it; nothing
/// is added that the caller did not offer, and [`crate::prompts::action_list`]
/// owns the "an empty table still names the native actions" rule so the two
/// surfaces cannot drift.
pub fn native_entries(tool_names: &[String]) -> Vec<CatalogEntry> {
    crate::prompts::action_list(tool_names)
        .into_iter()
        .map(|name| CatalogEntry {
            required: native_args(&name),
            summary: native_summary(&name).to_string(),
            kind: EntryKind::Native,
            name,
        })
        .collect()
}

/// The run's whole catalog: its own tools, then the host's, `final` last.
///
/// Order is deterministic and stated because it is what the model sees numbered
/// on the menu: a run that asks the same question twice must number the answers
/// the same way, or a cached verdict and a live one disagree about what "3"
/// meant. External entries are sorted by name and de-duplicated against the
/// native set — an `mcp.read_file` beside `read_file` is two rows for one
/// capability, and two rows for one capability is how a 0.5B loses a step.
pub fn merge(native: Vec<CatalogEntry>, external: Vec<CatalogEntry>) -> Vec<CatalogEntry> {
    let bare: BTreeSet<String> = native.iter().map(|entry| entry.name.clone()).collect();
    let mut extra: Vec<CatalogEntry> = external
        .into_iter()
        .filter(|entry| {
            let unprefixed = entry
                .name
                .strip_prefix(MCP_PREFIX)
                .unwrap_or(&entry.name)
                .to_string();
            !bare.contains(&entry.name) && !bare.contains(&unprefixed)
        })
        .collect();
    extra.sort_by(|left, right| (left.kind, &left.name).cmp(&(right.kind, &right.name)));
    extra.dedup_by(|left, right| left.name == right.name);

    let mut merged: Vec<CatalogEntry> = Vec::with_capacity(native.len() + extra.len());
    let mut last: Option<CatalogEntry> = None;
    for entry in native {
        if entry.name == "final" {
            last = Some(entry);
            continue;
        }
        merged.push(entry);
    }
    merged.extend(extra);
    merged.extend(last);
    merged
}

#[cfg(test)]
#[path = "catalog_tests.rs"]
mod tests;
