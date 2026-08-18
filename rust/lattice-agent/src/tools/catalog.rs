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

/// Argument shapes for the tools this crate reads arguments for.
///
/// Deliberately the **same table** the prompt signatures come from, in the same
/// order — a guided run that asks for `path` then `content` and a standard run
/// that is shown `write_file{path, content}` must not disagree about what the
/// tool takes. A tool that is not here is offered with no required arguments
/// and is asked for none, which is the honest answer when we do not know its
/// shape: better an empty call the tool refuses with its own message than a
/// guessed argument the model has to invent.
fn native_args(tool: &str) -> Vec<ArgSpec> {
    match tool {
        "write_file" | "local_write" => vec![
            ArgSpec::line("path", "workspace-relative file path, e.g. notes/hello.md"),
            ArgSpec::text("content", "the file's whole content, written out plainly"),
        ],
        "edit_file" => vec![
            ArgSpec::line("path", "the file to change"),
            ArgSpec::text("old_string", "the exact text to replace"),
            ArgSpec::text("new_string", "what it becomes"),
        ],
        // `def list_dir(path: str = ".")` / `def workspace_tree(path: str = ".")`
        // — the two read-only listings the tool layer documents a default for.
        // Everything else below takes a path it cannot invent.
        "list_dir" | "workspace_tree" => vec![ArgSpec::line_defaulting(
            "path",
            "workspace-relative path",
            ".",
        )],
        "read_file" | "local_read" | "read_document" => {
            vec![ArgSpec::line("path", "workspace-relative path")]
        }
        "grep" | "search_files" => vec![ArgSpec::line("pattern", "the text or regex to look for")],
        "knowledge_search" | "obsidian_search" => {
            vec![ArgSpec::line("query", "what to look for")]
        }
        // `safe_title(None, content)` derives the title from the note's first
        // line, so the title is asked for and never insisted on.
        "knowledge_save" | "obsidian_save" => vec![
            ArgSpec::line_defaulting("title", "the note's title", ""),
            ArgSpec::text("content", "the note's body"),
        ],
        "run_command" => vec![ArgSpec::line("command", "the command line to run")],
        // `build_project(cwd=None, script="build")` and its deploy twin take no
        // `command` at all — the catalog advertised one until v12.0.0, which is
        // a signature the tool would have refused had a model ever produced it.
        "build_project" | "deploy_project" => Vec::new(),
        "create_web_project" => vec![ArgSpec::line("path", "the project directory")],
        "computer_open_url" => vec![ArgSpec::line("url", "the address to open")],
        // `coerced_str(args, "app", "Google Chrome")` — the tool names its own.
        "computer_open_app" => vec![ArgSpec::line_defaulting(
            "app",
            "the application to open",
            "",
        )],
        "final" => vec![ArgSpec::line("message", "one line: what you did")],
        _ => Vec::new(),
    }
}

/// Other spellings one catalog argument is written under — **the keyword
/// rule** (v12.0.0).
///
/// A planner names an argument whatever it likes. Three live plans in one
/// matrix pass carried the right value under the wrong key: `mcp.grep` with
/// `{"search_term": "LatticeAI"}`, a nested `{"text": "LatticeAI"}`, and a
/// `write_file` step whose body sat under `body`. The value the run computed was
/// on the transcript in every case and the harness asked a 0.5B to invent
/// another one.
///
/// So this is a *reading* rule, never a writing one: it only ever recovers a
/// value some other part of the run already produced, and the argument's own
/// name always wins when it is present. Each list excludes the argument itself,
/// because the caller has tried that first by definition. An argument with no
/// row here is read under its own name and nothing else, which is the honest
/// answer when we do not know what else it could be called.
pub fn arg_synonyms(arg: &str) -> &'static [&'static str] {
    match arg {
        "pattern" => &[
            "query",
            "search_term",
            "searchTerm",
            "term",
            "text",
            "keyword",
        ],
        "query" => &[
            "pattern",
            "search_term",
            "searchTerm",
            "term",
            "text",
            "keyword",
        ],
        "path" => &[
            "file",
            "filename",
            "file_path",
            "filePath",
            "directory",
            "dir",
        ],
        "content" => &["body", "contents", "text"],
        "command" => &["cmd", "command_line", "commandLine"],
        "url" => &["address", "link", "href"],
        "app" => &["application", "name"],
        "title" => &["name", "heading"],
        _ => &[],
    }
}

/// Whether this argument is the *term a search looks for*.
///
/// The one argument kind whose value is stated by the user rather than chosen
/// by the model — see [`crate::kernel::agentloop::guided`]'s term rule, which
/// is why this predicate lives beside the keyword rule rather than inside one
/// dial.
pub fn is_search_arg(name: &str) -> bool {
    matches!(
        name,
        "pattern" | "query" | "search_term" | "term" | "keyword"
    )
}

/// Whether this call **states** a value for `name` at all.
///
/// Deliberately "stated", not "usable". A blank string is a value the caller
/// chose to send, and every one of these tools has its own answer for one —
/// `computer_open_app` refuses `"   "` in the user's language, `create_web_project`
/// says "Project path is required." Intercepting those would replace a tool's
/// own message with the harness's for no gain. What the harness answers for is
/// the argument that is not there at all, which is the shape every live defect
/// took: `write_file` with a path and no `content` key, `mcp.grep` with a path
/// and no `pattern` key.
fn carries(args: &Map<String, Value>, name: &str) -> bool {
    !matches!(args.get(name), None | Some(Value::Null))
}

/// The catalog row for one chosen name, if this run offers it.
pub fn entry_for<'a>(catalog: &'a [CatalogEntry], name: &str) -> Option<&'a CatalogEntry> {
    catalog.iter().find(|entry| entry.name == name)
}

/// Fill every required argument the **tool itself** documents a default for.
///
/// Returns what it filled, for the trace. Nothing is invented: the value comes
/// from [`ArgSpec::default`], which is the tool's own signature written down
/// once. A call that already carries the argument is untouched.
pub fn fill_documented_defaults(
    entry: &CatalogEntry,
    args: &mut Map<String, Value>,
) -> Vec<String> {
    let mut filled = Vec::new();
    for spec in &entry.required {
        let Some(default) = spec.default.as_deref().filter(|value| !value.is_empty()) else {
            continue;
        };
        if carries(args, &spec.name) {
            continue;
        }
        args.insert(spec.name.clone(), Value::String(default.to_string()));
        filled.push(spec.name.clone());
    }
    filled
}

/// The first required argument the **tool itself** documents a literal default
/// for, as `(name, default)`.
///
/// [`fill_documented_defaults`]' sibling, for the two callers that need the
/// default *before* there is a call to fill: the guided dial offers it as a
/// numbered choice rather than asking a 0.5B to type a path
/// ([`crate::kernel::agentloop::guided`]), and the dispatch tail repairs one
/// call with it when the value that *was* stated turned out not to be there
/// ([`crate::kernel::agentloop::execution`]). `Some("")` — the omissible
/// argument with no literal — is not a default anybody can offer or fill, so it
/// is skipped here exactly as it is there.
pub fn defaulted_arg(entry: &CatalogEntry) -> Option<(&str, &str)> {
    entry.required.iter().find_map(|spec| {
        spec.default
            .as_deref()
            .filter(|value| !value.is_empty())
            .map(|value| (spec.name.as_str(), value))
    })
}

/// Recover missing arguments from a map that spells them differently.
///
/// [`arg_synonyms`] applied to one source map — a plan step's `args`, or the
/// nested `arguments` object a weak planner wraps a call in. Only string values
/// are adopted, only into arguments the call is missing, and the source is
/// never modified: this reads what the run already computed and copies it under
/// the name the tool actually takes.
pub fn adopt_named_args(
    entry: &CatalogEntry,
    source: &Map<String, Value>,
    args: &mut Map<String, Value>,
) -> Vec<String> {
    let mut adopted = Vec::new();
    for spec in &entry.required {
        if carries(args, &spec.name) {
            continue;
        }
        let found = std::iter::once(spec.name.as_str())
            .chain(arg_synonyms(&spec.name).iter().copied())
            .find_map(|key| {
                source
                    .get(key)
                    .and_then(Value::as_str)
                    .map(str::trim)
                    .filter(|text| !text.is_empty())
            });
        if let Some(value) = found {
            args.insert(spec.name.clone(), Value::String(value.to_string()));
            adopted.push(spec.name.clone());
        }
    }
    adopted
}

/// The first required argument this call has no value and **no documented
/// default** for.
///
/// The one question the dispatch guard asks, on every dial. Run
/// [`fill_documented_defaults`] first: an argument the tool defaults is not
/// missing, it is simply unstated, and refusing it is refusing a call the tool
/// would have serviced.
pub fn missing_required(entry: &CatalogEntry, args: &Map<String, Value>) -> Option<String> {
    entry
        .required
        .iter()
        .find(|spec| spec.default.is_none() && !carries(args, &spec.name))
        .map(|spec| spec.name.clone())
}

/// One line of help per native tool, for a menu row that has to explain itself.
///
/// Absent is fine: the row is then the signature alone, which is what a name
/// like `git_status` already says.
fn native_summary(tool: &str) -> &'static str {
    match tool {
        "write_file" => "create or overwrite a file in the workspace",
        "edit_file" => "replace exact text inside an existing file",
        "read_file" => "read a file",
        "list_dir" => "list a directory",
        "grep" => "search the workspace for text",
        "run_command" => "run a shell command",
        "final" => "the work is done — finish the run",
        _ => "",
    }
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
mod tests {
    use super::*;
    use serde_json::json;

    fn names(values: &[&str]) -> Vec<String> {
        values.iter().map(|value| (*value).to_string()).collect()
    }

    fn external(name: &str, kind: EntryKind) -> CatalogEntry {
        CatalogEntry {
            name: name.to_string(),
            kind,
            summary: String::new(),
            required: Vec::new(),
        }
    }

    #[test]
    fn a_prefixed_name_the_run_governs_is_routed_to_the_stricter_chain() {
        let known = |name: &str| name == "read_file";
        assert_eq!(
            resolve("mcp.read_file", known),
            Resolved::Native("read_file")
        );
        assert_eq!(
            resolve("mcp.remote_thing", known),
            Resolved::External("mcp.remote_thing")
        );
        assert_eq!(
            resolve("skill.code_review", known),
            Resolved::External("skill.code_review")
        );
        // An unprefixed name is never external, even if a host offers one.
        assert_eq!(resolve("write_file", known), Resolved::Native("write_file"));
        assert!(is_external_name("mcp.x") && is_external_name("skill.x"));
        assert!(!is_external_name("write_file"));
    }

    #[test]
    fn the_native_entries_carry_the_arguments_the_prompt_advertises() {
        let entries = native_entries(&names(&["write_file", "read_file"]));
        let write = &entries[0];
        assert_eq!(write.name, "write_file");
        assert_eq!(write.kind, EntryKind::Native);
        assert_eq!(write.signature(), "write_file{path, content}");
        assert_eq!(write.required[0].kind, ArgKind::Line);
        assert_eq!(
            write.required[1].kind,
            ArgKind::Text,
            "file content is free-form text, never a JSON string"
        );
        assert_eq!(entries.last().expect("final").name, "final");
        // An unknown host tool is offered without invented arguments.
        let unknown = native_entries(&names(&["host_thing"]));
        assert!(unknown[0].required.is_empty());
        assert_eq!(unknown[0].signature(), "host_thing");
    }

    #[test]
    fn an_empty_inventory_still_offers_the_actions_that_exist() {
        let entries = native_entries(&[]);
        let listed: Vec<&str> = entries.iter().map(|entry| entry.name.as_str()).collect();
        assert_eq!(
            listed,
            vec!["write_file", "read_file", "edit_file", "list_dir", "final"]
        );
    }

    #[test]
    fn merging_keeps_final_last_and_drops_a_duplicate_capability() {
        let merged = merge(
            native_entries(&names(&["write_file", "read_file"])),
            vec![
                external("skill.code_review", EntryKind::Skill),
                // Already native under its bare name — one capability, one row.
                external("mcp.read_file", EntryKind::Mcp),
                external("mcp.remote_search", EntryKind::Mcp),
                external("skill.code_review", EntryKind::Skill),
            ],
        );
        let listed: Vec<&str> = merged.iter().map(|entry| entry.name.as_str()).collect();
        assert_eq!(
            listed,
            vec![
                "write_file",
                "read_file",
                "mcp.remote_search",
                "skill.code_review",
                "final"
            ],
            "natives first, then mcp, then skills, `final` last"
        );
        // And the order is stable: the same inputs number the same way.
        let again = merge(
            native_entries(&names(&["write_file", "read_file"])),
            vec![
                external("mcp.remote_search", EntryKind::Mcp),
                external("skill.code_review", EntryKind::Skill),
            ],
        );
        assert_eq!(
            again.iter().map(|e| e.name.clone()).collect::<Vec<_>>(),
            merged.iter().map(|e| e.name.clone()).collect::<Vec<_>>()
        );
    }

    /// The `gemma2b:S3` regression, at the level it was caused: the catalog
    /// said `path` was required, the tool says `path: str = "."`, and nine live
    /// dispatches were refused for want of an argument nobody had to supply.
    #[test]
    fn an_argument_the_tool_documents_a_default_for_is_filled_and_never_refused() {
        let listing = &native_entries(&names(&["list_dir"]))[0];
        assert_eq!(listing.name, "list_dir");
        let mut args = Map::new();
        assert_eq!(
            missing_required(listing, &args),
            None,
            "not missing — unstated"
        );
        assert_eq!(fill_documented_defaults(listing, &mut args), vec!["path"]);
        assert_eq!(args["path"], json!("."), "the tool's own default, verbatim");
        assert_eq!(missing_required(listing, &args), None);
        // A value already there is never overwritten by the default.
        let mut given = Map::new();
        given.insert("path".into(), json!("notes"));
        assert!(fill_documented_defaults(listing, &mut given).is_empty());
        assert_eq!(given["path"], json!("notes"));
        // A value the caller *stated*, blank or not, is the caller's: the tool
        // has its own answer for one, and replacing it here would replace the
        // tool's message with ours.
        let mut blank = Map::new();
        blank.insert("path".into(), json!("   "));
        assert!(fill_documented_defaults(listing, &mut blank).is_empty());
        assert_eq!(blank["path"], json!("   "));
        assert_eq!(missing_required(listing, &blank), None);
    }

    /// `knowledge_save` derives a title from the note's first line, and
    /// `build_project` has no `command` argument at all — two claims the
    /// catalog made that the tools never agreed with.
    #[test]
    fn an_argument_the_tool_derives_is_asked_for_and_never_insisted_on() {
        let entries = native_entries(&names(&["knowledge_save", "build_project", "run_command"]));
        let save = entry_for(&entries, "knowledge_save").expect("knowledge_save");
        assert_eq!(
            save.signature(),
            "knowledge_save{title, content}",
            "still asked for"
        );
        let mut args = Map::new();
        args.insert("content".into(), json!("a note"));
        assert!(
            fill_documented_defaults(save, &mut args).is_empty(),
            "there is no literal to fill a derived title with"
        );
        assert_eq!(args.len(), 1, "and nothing was added: {args:?}");
        assert_eq!(missing_required(save, &args), None);
        // A body it cannot derive anything from is still refused.
        assert_eq!(
            missing_required(save, &Map::new()).as_deref(),
            Some("content")
        );
        let build = entry_for(&entries, "build_project").expect("build_project");
        assert_eq!(build.signature(), "build_project");
        assert_eq!(missing_required(build, &Map::new()), None);
        // …and the tool that really does take one still takes it.
        let run = entry_for(&entries, "run_command").expect("run_command");
        assert_eq!(
            missing_required(run, &Map::new()).as_deref(),
            Some("command")
        );
    }

    #[test]
    fn an_argument_with_no_documented_default_is_still_refused() {
        let entries = native_entries(&names(&["write_file", "read_file", "grep"]));
        let missing_of = |name: &str| {
            let entry = entry_for(&entries, name).expect(name);
            missing_required(entry, &Map::new())
        };
        assert_eq!(missing_of("write_file").as_deref(), Some("path"));
        assert_eq!(missing_of("read_file").as_deref(), Some("path"));
        assert_eq!(missing_of("grep").as_deref(), Some("pattern"));
        // Filling defaults changes none of them: none of these document one.
        for name in ["write_file", "read_file", "grep"] {
            let entry = entry_for(&entries, name).expect(name);
            let mut args = Map::new();
            assert!(
                fill_documented_defaults(entry, &mut args).is_empty(),
                "{name}"
            );
        }
        assert_eq!(entry_for(&entries, "no_such_tool"), None);
    }

    /// The keyword rule: three live plans carried the right value under the
    /// wrong key, and the harness asked a 0.5B to invent another one.
    #[test]
    fn a_value_the_run_already_computed_is_read_under_the_name_it_was_written_with() {
        let entries = native_entries(&names(&["grep", "write_file"]));
        let grep = entry_for(&entries, "grep").expect("grep");
        let source: Map<String, Value> = json!({"search_term": "LatticeAI", "output": "output"})
            .as_object()
            .expect("object")
            .clone();
        let mut args = Map::new();
        assert_eq!(adopt_named_args(grep, &source, &mut args), vec!["pattern"]);
        assert_eq!(args["pattern"], json!("LatticeAI"));
        // The argument's own name wins wherever it is present…
        let both: Map<String, Value> = json!({"pattern": "own", "search_term": "other"})
            .as_object()
            .expect("object")
            .clone();
        let mut args = Map::new();
        adopt_named_args(grep, &both, &mut args);
        assert_eq!(args["pattern"], json!("own"));
        // …and a call that already carries a value is never overwritten.
        let mut given = Map::new();
        given.insert("pattern".into(), json!("kept"));
        assert!(adopt_named_args(grep, &source, &mut given).is_empty());
        assert_eq!(given["pattern"], json!("kept"));
        // Nothing is adopted for an argument with no other spelling in reach.
        let write = entry_for(&entries, "write_file").expect("write_file");
        let mut args = Map::new();
        assert_eq!(
            adopt_named_args(
                write,
                &json!({"body": "hi"}).as_object().cloned().expect("object"),
                &mut args
            ),
            vec!["content"],
            "`body` is a spelling of `content`"
        );
        assert!(is_search_arg("pattern") && is_search_arg("query"));
        assert!(!is_search_arg("path") && !is_search_arg("content"));
        assert!(arg_synonyms("no_such_argument").is_empty());
    }

    #[test]
    fn the_kind_labels_are_the_words_the_menu_prints() {
        assert_eq!(EntryKind::Native.label(), "tool");
        assert_eq!(EntryKind::Mcp.label(), "mcp");
        assert_eq!(EntryKind::Skill.label(), "skill");
        assert_eq!(MCP_PREFIX, "mcp.");
        assert_eq!(SKILL_PREFIX, "skill.");
    }
}
