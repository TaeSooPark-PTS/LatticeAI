//! The prompts the loop falls back on when the caller supplies none.
//!
//! Until v11.9.0 [`crate::agentloop::Prompts`] defaulted to **empty strings**
//! and nothing in the tree filled them. Every executor turn therefore opened
//! with a blank line: the model was handed a plan, a transcript and a workspace
//! root, and was never told that the reply had to be one JSON object, never
//! shown one, and never told which actions existed. A large model guesses the
//! contract from the transcript's shape. A 2B model does not, and the loop's
//! whole compact profile — the shorter window, the earlier escalation, the
//! direct-path fallback — was spending its budget correcting a model that had
//! not been asked anything in the first place.
//!
//! `FEATURE_STATUS.md` still advertises `core/agent_prompts.executor_prompt_for`
//! for this. That module was deleted with the Python loop; this is where the
//! behaviour it described actually lives now.
//!
//! ## Two rules the composition follows
//!
//! **A caller-supplied prompt always wins.** These constants fill a gap; they
//! never override a host that has its own prompt library.
//! [`crate::agentloop::Prompts`]'s three resolvers reach the built-in only when
//! the caller's string is blank.
//!
//! **The tool list is the run's, not a second copy.** The names come from the
//! run's own policy table / `tool_names`, so a host that narrows what may run
//! narrows what the model is told about in the same breath. Only the *argument
//! signatures* are static here, and only for tools whose arguments this crate
//! actually reads ([`crate::tools`]) — a signature nobody implements would be
//! an invitation to call something that does not exist.

use crate::profile::AgentProfile;

/// The actions any run has, in the order a file task needs them.
///
/// Used verbatim in two places: the executor prompt when the run named no tools,
/// and the escalation hint in [`crate::agentloop::fallback`], which used to
/// render `Valid action values are: , final.` against an empty table.
pub const CORE_TOOL_CATALOG: [(&str, &str); 5] = [
    ("write_file", "path, content"),
    ("read_file", "path"),
    ("edit_file", "path, old_string, new_string"),
    ("list_dir", "path"),
    ("final", "message"),
];

/// Argument signatures for the tools this crate reads arguments for.
///
/// Sorted by name so the lookup is a binary search, and deliberately partial:
/// a tool the run offers that is not here renders as a bare name rather than a
/// guessed signature.
const TOOL_SIGNATURES: [(&str, &str); 9] = [
    ("create_web_project", "path"),
    ("edit_file", "path, old_string, new_string"),
    ("final", "message"),
    ("list_dir", "path"),
    ("local_write", "path, content"),
    ("read_file", "path"),
    ("run_command", "command"),
    ("todo_write", "todos"),
    ("write_file", "path, content"),
];

/// One skill the host has installed, as the run body declares it (v11.9.0).
///
/// A **contract only** on this side: the loop renders what it is given into the
/// executor prompt and never looks a skill up, loads one, or executes one. The
/// registry that knows what a skill *is* lives in `lattice-platform`.
#[derive(Debug, Clone, Default, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct SkillBrief {
    /// The skill's name, as the model should refer to it.
    pub name: String,
    /// One line: what it does.
    #[serde(default)]
    pub brief: String,
    /// One line: when to reach for it.
    #[serde(default)]
    pub when: String,
}

/// `name{args}` for one tool, or just `name` when no signature is known.
fn signature(name: &str) -> String {
    match TOOL_SIGNATURES.binary_search_by(|(known, _)| (*known).cmp(name)) {
        Ok(index) => format!("{name}{{{}}}", TOOL_SIGNATURES[index].1),
        Err(_) => name.to_string(),
    }
}

/// The action list for a prompt: the run's tools, `final` always last.
///
/// An empty list is never rendered. A run whose policy table happens to be
/// empty still has [`CORE_TOOL_CATALOG`] — the tools this crate executes
/// natively exist whether or not the caller described them.
pub fn action_list(tool_names: &[String]) -> Vec<String> {
    let mut names: Vec<String> = tool_names
        .iter()
        .map(|name| name.trim().to_string())
        .filter(|name| !name.is_empty() && name != "final")
        .collect();
    if names.is_empty() {
        names = CORE_TOOL_CATALOG
            .iter()
            .map(|(name, _)| (*name).to_string())
            .filter(|name| name != "final")
            .collect();
    }
    names.push("final".into());
    names
}

/// The action list rendered with signatures, one per line.
fn action_lines(tool_names: &[String]) -> String {
    action_list(tool_names)
        .iter()
        .map(|name| format!("- {}", signature(name)))
        .collect::<Vec<_>>()
        .join("\n")
}

/// The `Available skills` block, or `""` when the run declared none.
fn skills_block(skills: &[SkillBrief]) -> String {
    let lines: Vec<String> = skills
        .iter()
        .filter(|skill| !skill.name.trim().is_empty())
        .map(|skill| {
            let mut line = format!("- {}", skill.name.trim());
            if !skill.brief.trim().is_empty() {
                line.push_str(&format!(": {}", skill.brief.trim()));
            }
            if !skill.when.trim().is_empty() {
                line.push_str(&format!(" (use when: {})", skill.when.trim()));
            }
            line
        })
        .collect();
    if lines.is_empty() {
        String::new()
    } else {
        format!("\n\nAvailable skills:\n{}", lines.join("\n"))
    }
}

/// The one worked example. Escaping is the thing weak models get wrong, so the
/// example shows a real escaped document rather than `"..."`.
const WRITE_EXAMPLE: &str = r#"{"thoughts": "write the page", "action": "write_file", "args": {"path": "index.html", "content": "<!doctype html>\n<html lang=\"en\">\n<head><meta charset=\"utf-8\"><title>Hi</title></head>\n<body><h1>Hi</h1></body>\n</html>\n"}}"#;

/// The rule every reply obeys, stated once and reused by both variants.
const ONE_OBJECT_RULE: &str = "Reply with EXACTLY ONE JSON object and nothing else — no prose \
before it, no prose after it, no markdown fences, never two objects. \
Never emit <|channel|>, <|message|>, <|start|>, or <|end|> tokens; start at the opening brace.";

/// A complete critic object, shown so a weak model copies the shape.
pub const VERDICT_EXAMPLE: &str = r#"{"action": "verdict", "verdict": "PASS", "next_state": "DONE", "reason": "the requested file was written", "corrections": []}"#;

/// What each file type has to look like, so the write is not rejected later.
const EXTENSION_ANCHORS: &str = "File content rules:
- .html: a complete document starting with <!doctype html> and ending with </html>.
- .css: real rule blocks with { and }, no markdown fences.
- .js / .ts: runnable source with balanced brackets, no markdown fences.
- .json: one valid JSON value and nothing else.
- .md: the document itself, not a description of it.
- .py: importable source, no markdown fences.";

/// `PLANNER_PROMPT` — the shape [`crate::plan::normalize_plan`] expects.
pub const DEFAULT_PLANNER_PROMPT: &str = r#"You are the planner of an agent loop. Reply with EXACTLY ONE JSON object and nothing else.

{"action": "plan", "goal": "<the user's request, restated in one line>", "steps": [{"action": "<tool name>", "args": {"path": "<file>"}, "description": "<what this step does>"}], "estimated_steps": <integer>, "requires_approval": <true|false>, "rollback_strategy": "none"}

Rules:
- Every file the request asks for gets its own write_file step, with its real path in args.path.
- Keep the plan to the steps that actually change something; do not plan to "review" or "confirm".
- requires_approval is true only when a step runs a command or touches something outside the workspace."#;

/// `CRITIC_PROMPT` — the shape [`crate::agentloop::verification`] expects.
pub const DEFAULT_CRITIC_PROMPT: &str = concat!(
    "You are the critic of an agent loop. Reply with EXACTLY ONE JSON object and nothing else — ",
    "no prose before it, no prose after it, no markdown fences, never two objects. ",
    "Never emit <|channel|>, <|message|>, <|start|>, or <|end|> tokens; start at the opening brace.\n\n",
    "{\"action\": \"verdict\", \"verdict\": \"PASS\"|\"FAIL\", \"next_state\": \"DONE\"|\"EXECUTING\"|\"ROLLBACK\"|\"FAILED\", ",
    "\"reason\": \"<one or two lines>\", \"corrections\": [\"<what to do differently>\"], \"confidence\": <0.0-1.0>}\n\n",
    "Example:\n",
    r#"{"action": "verdict", "verdict": "PASS", "next_state": "DONE", "reason": "the requested file was written", "corrections": []}"#,
    "\n\nRules:\n",
    "- PASS with next_state DONE only when the transcript shows the work was actually done — a file that was requested and never written is a FAIL.\n",
    "- FAIL with next_state EXECUTING when another attempt could fix it; put the specific instruction in corrections.\n",
    "- FAIL with next_state ROLLBACK only when a change made things worse and should be undone.\n",
    "- Judge the transcript in front of you. Do not assume steps that are not in it."
);

/// The executor prompt for one run: rules, the example, this run's actions.
///
/// The compact variant is the same contract with the prose removed — a model
/// that cannot hold a tool-call format cannot hold three paragraphs about it
/// either, and every sentence it does not need is context the transcript could
/// have used.
pub fn executor_prompt(
    profile: AgentProfile,
    tool_names: &[String],
    skills: &[SkillBrief],
) -> String {
    let actions = action_lines(tool_names);
    let skills = skills_block(skills);
    // Worked example first, short rules after. A 2B copies the nearest shape;
    // ONE_OBJECT_RULE on top (critic-lane rewording) buried the example and
    // the model spent the turn inside <|channel>thought instead.
    if profile.lean_context {
        return format!(
            "Example:\n{WRITE_EXAMPLE}\n\n\
{ONE_OBJECT_RULE}\n\n\
{{\"thoughts\": \"<short>\", \"action\": \"<name>\", \"args\": {{...}}}}\n\n\
To make a file: action write_file, with the whole file in args.content \
(escape newlines as \\n, quotes as \\\").\n\
When the work is done: {{\"action\": \"final\", \"message\": \"<what you did>\"}}\n\n\
Actions:\n{actions}\n\n\
{EXTENSION_ANCHORS}{skills}"
        );
    }
    format!(
        "You are the executor of an agent loop.\n\n\
Example — creating a page:\n{WRITE_EXAMPLE}\n\n\
{ONE_OBJECT_RULE}\n\n\
The object is: {{\"thoughts\": \"<one short line>\", \"action\": \"<action name>\", \
\"args\": {{<that action's arguments>}}}}\n\n\
Rules:\n\
- One action per reply. Work through the plan one step at a time.\n\
- To create or overwrite a file, call write_file — do not describe the file, write it.\n\
- Put the file's entire content in args.content as one JSON string: escape newlines \
as \\n and quotes as \\\".\n\
- Read before you edit: edit_file needs old_string to match the file exactly.\n\
- When the work is done, reply {{\"action\": \"final\", \"message\": \"<what you did>\"}}.\n\n\
Available actions:\n{actions}\n\n\
{EXTENSION_ANCHORS}{skills}"
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::profile::{COMPACT, STANDARD};

    fn names(values: &[&str]) -> Vec<String> {
        values.iter().map(|value| (*value).to_string()).collect()
    }

    #[test]
    fn the_signature_table_is_sorted_so_the_lookup_is_valid() {
        let mut sorted: Vec<&str> = TOOL_SIGNATURES.iter().map(|(name, _)| *name).collect();
        let original = sorted.clone();
        sorted.sort_unstable();
        assert_eq!(original, sorted);
    }

    #[test]
    fn an_empty_tool_table_still_names_the_actions_that_exist() {
        // The bug this guards: `Valid action values are: , final.`
        let list = action_list(&[]);
        assert_eq!(
            list,
            vec!["write_file", "read_file", "edit_file", "list_dir", "final"]
        );
        assert!(!list.iter().any(|name| name.trim().is_empty()));
    }

    #[test]
    fn the_runs_own_tools_are_what_the_prompt_names() {
        let list = action_list(&names(&["read_file", "grep", " ", "final"]));
        assert_eq!(list, vec!["read_file", "grep", "final"]);
        assert_eq!(
            list.iter().filter(|name| *name == "final").count(),
            1,
            "final is added once, not twice"
        );
    }

    #[test]
    fn a_known_tool_carries_its_arguments_and_an_unknown_one_does_not() {
        assert_eq!(signature("write_file"), "write_file{path, content}");
        assert_eq!(
            signature("edit_file"),
            "edit_file{path, old_string, new_string}"
        );
        assert_eq!(signature("some_host_tool"), "some_host_tool");
    }

    #[test]
    fn the_executor_default_carries_the_four_things_a_weak_model_needs() {
        let prompt = executor_prompt(STANDARD, &names(&["write_file", "read_file"]), &[]);
        assert!(prompt.contains("EXACTLY ONE JSON object"), "the rule");
        assert!(prompt.contains(WRITE_EXAMPLE), "the worked example");
        assert!(
            prompt.contains("Never emit <|channel|>"),
            "the channel-tag prohibition"
        );
        assert!(prompt.contains("- write_file{path, content}"), "the tools");
        assert!(prompt.contains("<!doctype html>"), "the extension anchors");
        assert!(prompt.contains("- final{message}"));
        assert!(!prompt.contains("Available skills"));
    }

    #[test]
    fn the_compact_variant_is_the_same_contract_without_the_prose() {
        let standard = executor_prompt(STANDARD, &names(&["write_file"]), &[]);
        let compact = executor_prompt(COMPACT, &names(&["write_file"]), &[]);
        assert!(compact.contains("EXACTLY ONE JSON object"));
        assert!(compact.contains(WRITE_EXAMPLE));
        assert!(compact.contains("- write_file{path, content}"));
        assert!(compact.contains("<!doctype html>"));
        assert!(
            compact.len() < standard.len(),
            "compact {} !< standard {}",
            compact.len(),
            standard.len()
        );
        assert!(!compact.contains("You are the executor"));
    }

    #[test]
    fn the_worked_example_comes_before_the_rules() {
        // verify3 tapes ran against example-first. The critic-lane rewording
        // put ONE_OBJECT_RULE on top; restore the shape that actually wrote.
        let compact = executor_prompt(COMPACT, &names(&["write_file"]), &[]);
        let example_at = compact.find(WRITE_EXAMPLE).expect("example");
        let rule_at = compact.find(ONE_OBJECT_RULE).expect("rule");
        assert!(
            example_at < rule_at,
            "example at {example_at} must precede the rule at {rule_at}"
        );
        assert!(compact.starts_with("Example:"));
        let standard = executor_prompt(STANDARD, &names(&["write_file"]), &[]);
        let example_at = standard.find(WRITE_EXAMPLE).expect("example");
        let rule_at = standard.find(ONE_OBJECT_RULE).expect("rule");
        assert!(example_at < rule_at);
    }

    #[test]
    fn declared_skills_are_rendered_and_nothing_else_is() {
        let skills = vec![
            SkillBrief {
                name: "release-manager".into(),
                brief: "prepare a release".into(),
                when: "the user asks to ship".into(),
            },
            SkillBrief {
                name: "bare".into(),
                ..SkillBrief::default()
            },
            SkillBrief::default(),
        ];
        let prompt = executor_prompt(STANDARD, &names(&["write_file"]), &skills);
        assert!(prompt
            .contains("- release-manager: prepare a release (use when: the user asks to ship)"));
        assert!(prompt.contains("\n- bare"), "a name alone is enough");
        assert_eq!(
            prompt.matches("Available skills").count(),
            1,
            "the nameless entry adds no section of its own"
        );
        assert!(
            executor_prompt(COMPACT, &names(&["write_file"]), &skills).contains("Available skills")
        );
    }

    #[test]
    fn the_planner_and_critic_defaults_name_the_shapes_their_phases_parse() {
        // planning.rs reads these five keys off the planner's object.
        for key in [
            "\"action\": \"plan\"",
            "\"goal\"",
            "\"steps\"",
            "\"estimated_steps\"",
            "\"requires_approval\"",
            "\"rollback_strategy\"",
        ] {
            assert!(DEFAULT_PLANNER_PROMPT.contains(key), "{key}");
        }
        // verification.rs reads these, and maps exactly these four next states.
        for key in [
            "\"verdict\"",
            "\"next_state\"",
            "\"reason\"",
            "\"corrections\"",
            "DONE",
            "EXECUTING",
            "ROLLBACK",
            "FAILED",
        ] {
            assert!(DEFAULT_CRITIC_PROMPT.contains(key), "{key}");
        }
        assert!(
            DEFAULT_CRITIC_PROMPT.contains(VERDICT_EXAMPLE),
            "the worked verdict example"
        );
        assert!(
            DEFAULT_CRITIC_PROMPT.contains("Never emit <|channel|>"),
            "the channel-tag prohibition"
        );
    }

    #[test]
    fn the_worked_example_is_itself_a_valid_action() {
        // A prompt that shows an example the loop's own parser would reject is
        // worse than no example at all.
        let (action, repairs) =
            crate::action::extract_action_details(WRITE_EXAMPLE).expect("the example must parse");
        assert_eq!(repairs, Vec::<String>::new(), "and needs no repair");
        assert_eq!(action["action"], "write_file");
        let content = action["args"]["content"].as_str().expect("content");
        assert!(content.starts_with("<!doctype html>"));
        // And the content it shows passes the write-side validator, so a model
        // that copies the shape is not handed a repair.
        let (ok, reason) = crate::sanitize::validate_file_content(content, "index.html");
        assert!(ok, "{reason}");
        let (verdict, repairs) =
            crate::action::extract_action_details(VERDICT_EXAMPLE).expect("verdict example");
        assert_eq!(repairs, Vec::<String>::new());
        assert_eq!(verdict["action"], "verdict");
        assert_eq!(verdict["verdict"], "PASS");
    }
}
