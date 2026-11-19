//! Argument signatures, synonyms, defaults, and missing-argument helpers for
//! [`super`] (`catalog`).

use serde_json::{Map, Value};

use super::{ArgSpec, CatalogEntry};

/// Argument shapes for the tools this crate reads arguments for.
///
/// Deliberately the **same table** the prompt signatures come from, in the same
/// order — a guided run that asks for `path` then `content` and a standard run
/// that is shown `write_file{path, content}` must not disagree about what the
/// tool takes. A tool that is not here is offered with no required arguments
/// and is asked for none, which is the honest answer when we do not know its
/// shape: better an empty call the tool refuses with its own message than a
/// guessed argument the model has to invent.
pub(super) fn native_args(tool: &str) -> Vec<ArgSpec> {
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
        "read_file" | "local_read" | "read_document" | "inspect_html" => {
            vec![ArgSpec::line("path", "workspace-relative path")]
        }
        "preview_url" => vec![ArgSpec::line_defaulting(
            "path",
            "workspace-relative HTML file path",
            "index.html",
        )],
        "grep" | "search_files" => vec![ArgSpec::line("pattern", "the text or regex to look for")],
        "knowledge_search"
        | "obsidian_search"
        | "knowledge_graph_search"
        | "knowledge_graph_context" => {
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
        "web_search" => vec![ArgSpec::line("query", "what to search the web for")],
        "web_fetch" => vec![ArgSpec::line("url", "the page address to fetch")],
        // The frontmost application is inspected when none is named, so the
        // app is asked for and never insisted on.
        "computer_ui_inspect" => vec![ArgSpec::line_defaulting(
            "app",
            "the application whose UI to inspect",
            "",
        )],
        "computer_click" | "computer_move" | "computer_scroll" => vec![
            ArgSpec::line("x", "horizontal screen coordinate in pixels"),
            ArgSpec::line("y", "vertical screen coordinate in pixels"),
        ],
        "computer_drag" => vec![
            ArgSpec::line("x1", "start x coordinate in pixels"),
            ArgSpec::line("y1", "start y coordinate in pixels"),
            ArgSpec::line("x2", "end x coordinate in pixels"),
            ArgSpec::line("y2", "end y coordinate in pixels"),
        ],
        "computer_type" => vec![ArgSpec::text("text", "the text to type")],
        "computer_key" => vec![ArgSpec::line(
            "key",
            "the key or combination to press, e.g. return or command+s",
        )],
        "computer_screenshot"
        | "computer_status"
        | "knowledge_tree"
        | "obsidian_tree"
        | "git_status"
        | "git_diff" => Vec::new(),
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
            "q",
            "search",
            "search_query",
            "searchQuery",
        ],
        "path" => &[
            "file",
            "filename",
            "file_path",
            "filePath",
            "directory",
            "dir",
        ],
        "content" => &["body", "contents", "text", "html", "code", "source", "data"],
        "command" => &["cmd", "command_line", "commandLine"],
        "url" => &["address", "link", "href", "uri", "website"],
        "app" => &["application", "name", "app_name", "appName"],
        "key" => &[
            "keys",
            "combo",
            "key_combo",
            "keyCombo",
            "shortcut",
            "hotkey",
        ],
        "text" => &["content", "input", "value", "string"],
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

const WRAPPER_KEYS: [&str; 3] = ["args", "parameters", "arguments"];

const INT_COERCE_KEYS: [&str; 11] = [
    "x",
    "y",
    "x1",
    "y1",
    "x2",
    "y2",
    "clicks",
    "max_results",
    "offset",
    "limit",
    "hops",
];

const FLOAT_COERCE_KEYS: [&str; 1] = ["interval"];

const BOOL_COERCE_KEYS: [&str; 3] = ["double", "include_graph", "replace_all"];

fn parse_bool_str(raw: &str) -> Option<bool> {
    match raw.trim().to_ascii_lowercase().as_str() {
        "true" | "yes" | "1" => Some(true),
        "false" | "no" | "0" => Some(false),
        _ => None,
    }
}

fn normalize_computer_key(raw: &str, notes: &mut Vec<String>) -> Option<String> {
    let trimmed = raw.trim();
    if trimmed.is_empty() {
        return None;
    }
    let mut parts: Vec<String> = trimmed
        .split('+')
        .map(|part| part.trim().to_string())
        .collect();
    let len = parts.len();
    let has_plus = len > 1;
    let mut changed = false;

    for (idx, part) in parts.iter_mut().enumerate() {
        let lower = part.to_ascii_lowercase();
        let (replacement, note) = match lower.as_str() {
            "enter" => ("return", "key_alias:enter->return"),
            "esc" => ("escape", "key_alias:esc->escape"),
            "cmd" if has_plus && idx + 1 < len => ("command", "key_alias:cmd+->command+"),
            "ctrl" if has_plus && idx + 1 < len => ("control", "key_alias:ctrl+->control+"),
            _ => continue,
        };
        *part = replacement.to_string();
        notes.push(note.to_string());
        changed = true;
    }

    changed.then(|| parts.join("+"))
}

/// Unwrap nested argument envelopes, coerce stringified scalars, and normalize
/// `computer_key` key aliases in place.
///
/// Returns a deterministic list of notes describing each change applied, or an
/// empty `Vec` when `args` required no normalization.
pub fn coerce_and_unwrap_args(entry: &CatalogEntry, args: &mut Map<String, Value>) -> Vec<String> {
    let mut notes = Vec::new();

    for wrapper in WRAPPER_KEYS {
        if !matches!(args.get(wrapper), Some(Value::Object(_))) {
            continue;
        }
        let Some(Value::Object(nested)) = args.remove(wrapper) else {
            continue;
        };
        for (key, value) in nested {
            args.entry(key).or_insert(value);
        }
        notes.push(format!("unwrapped:{wrapper}"));
    }

    for key in INT_COERCE_KEYS {
        let Some(Value::String(raw)) = args.get(key) else {
            continue;
        };
        if let Ok(parsed) = raw.trim().parse::<i64>() {
            args.insert(key.to_string(), Value::Number(parsed.into()));
            notes.push(format!("coerced:{key}"));
        }
    }

    for key in FLOAT_COERCE_KEYS {
        let Some(Value::String(raw)) = args.get(key) else {
            continue;
        };
        if let Some(number) = raw
            .trim()
            .parse::<f64>()
            .ok()
            .and_then(serde_json::Number::from_f64)
        {
            args.insert(key.to_string(), Value::Number(number));
            notes.push(format!("coerced:{key}"));
        }
    }

    for key in BOOL_COERCE_KEYS {
        let Some(Value::String(raw)) = args.get(key) else {
            continue;
        };
        if let Some(parsed) = parse_bool_str(raw) {
            args.insert(key.to_string(), Value::Bool(parsed));
            notes.push(format!("coerced:{key}"));
        }
    }

    let bare_name = entry
        .name
        .strip_prefix(super::MCP_PREFIX)
        .unwrap_or(&entry.name);
    if bare_name == "computer_key" {
        if let Some(Value::String(raw)) = args.get("key") {
            if let Some(normalized) = normalize_computer_key(raw, &mut notes) {
                args.insert("key".to_string(), Value::String(normalized));
            }
        }
    }

    notes
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
pub(super) fn native_summary(tool: &str) -> &'static str {
    match tool {
        "write_file" => "create or overwrite a file in the workspace",
        "edit_file" => "replace exact text inside an existing file",
        "read_file" => "read a file",
        "list_dir" => "list a directory",
        "grep" => "search the workspace for text",
        "knowledge_search" | "obsidian_search" => {
            "hybrid BM25F + vector + multi-hop knowledge graph search"
        }
        "knowledge_save" | "obsidian_save" => "save a markdown note into the knowledge garden",
        "knowledge_tree" | "obsidian_tree" => "list markdown notes in the knowledge garden",
        "knowledge_graph_search" => {
            "search knowledge graph nodes, entities, concepts, and 1-2 hop edges"
        }
        "knowledge_graph_context" => {
            "extract multi-hop entity/concept subgraph context for GraphRAG"
        }
        "run_command" => "run a shell command",
        "web_search" => "search the web and return result titles, links and snippets",
        "web_fetch" => "fetch a web page and return its readable text",
        "computer_ui_inspect" => "list an application's visible UI elements and their positions",
        "computer_screenshot" => "capture the screen",
        "computer_status" => "report whether desktop control is available",
        "computer_click" => "click at a screen coordinate",
        "computer_type" => "type text at the current focus",
        "computer_key" => "press a key or key combination",
        "computer_scroll" => "scroll at a screen coordinate",
        "computer_move" => "move the pointer to a screen coordinate",
        "computer_drag" => "drag from one screen coordinate to another",
        "computer_open_app" => "open an application",
        "computer_open_url" => "open a URL in the browser",
        "git_status" => "show the workspace's git status",
        "git_diff" => "show the workspace's uncommitted git diff",
        "final" => "the work is done — finish the run",
        _ => "",
    }
}
