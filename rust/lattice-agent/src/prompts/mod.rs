pub mod guided;

use crate::kernel::profile::AgentProfile;

pub const CORE_TOOL_CATALOG: [(&str, &str); 5] = [
    ("write_file", "path, content"),
    ("read_file", "path"),
    ("edit_file", "path, old_string, new_string"),
    ("list_dir", "path"),
    ("final", "message"),
];

const TOOL_SIGNATURES: [(&str, &str); 29] = [
    ("computer_click", "x, y"),
    ("computer_drag", "x1, y1, x2, y2"),
    ("computer_key", "key"),
    ("computer_move", "x, y"),
    ("computer_open_app", "app"),
    ("computer_open_url", "url"),
    ("computer_screenshot", ""),
    ("computer_scroll", "x, y"),
    ("computer_status", ""),
    ("computer_type", "text"),
    ("computer_ui_inspect", "app"),
    ("create_web_project", "path"),
    ("edit_file", "path, old_string, new_string"),
    ("final", "message"),
    ("git_diff", ""),
    ("git_status", ""),
    ("grep", "pattern"),
    ("knowledge_graph_context", "query"),
    ("knowledge_graph_search", "query"),
    ("knowledge_search", "query"),
    ("knowledge_tree", ""),
    ("list_dir", "path"),
    ("local_write", "path, content"),
    ("read_file", "path"),
    ("run_command", "command"),
    ("todo_write", "todos"),
    ("web_fetch", "url"),
    ("web_search", "query"),
    ("write_file", "path, content"),
];

#[derive(Debug, Clone, Default, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct SkillBrief {
    pub name: String,
    #[serde(default)]
    pub brief: String,
    #[serde(default)]
    pub when: String,
}

pub const GRAPH_SKILL_BRIEFS: [(&str, &str, &str); 3] = [
    (
        "skill.hybrid_graphrag_search",
        "Run 3-way BM25F + subword vector + Knowledge Graph RRF retrieval (mode='hybrid', hops=2)",
        "answering questions across notes, Korean/English technical docs, or connected concepts",
    ),
    (
        "skill.graph_neighborhood_explore",
        "Traverse 1-hop and 2-hop Knowledge Graph edges (RELATES_TO, MENTIONS, PART_OF, wikilinks, tags)",
        "expanding seed hits to discover structurally related decisions, entities, and linked notes",
    ),
    (
        "skill.entity_subgraph_extract",
        "Extract an entity/concept subgraph (mode='subgraph', include_graph=true) with nodes and edges",
        "synthesizing multi-document architecture or relational context before drafting an answer",
    ),
];

pub fn signature(name: &str) -> String {
    let bare = name
        .strip_prefix(crate::tools::catalog::MCP_PREFIX)
        .unwrap_or(name);
    match TOOL_SIGNATURES.binary_search_by(|(known, _)| (*known).cmp(bare)) {
        Ok(index) if !TOOL_SIGNATURES[index].1.is_empty() => {
            format!("{name}{{{}}}", TOOL_SIGNATURES[index].1)
        }
        _ => name.to_string(),
    }
}

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

fn action_lines(tool_names: &[String]) -> String {
    action_list(tool_names)
        .iter()
        .map(|name| format!("- {}", signature(name)))
        .collect::<Vec<_>>()
        .join("\n")
}

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

const WRITE_EXAMPLE: &str = r#"{"thoughts": "write the file the plan asks for", "action": "write_file", "args": {"path": "example.txt", "content": "first line\nsecond line with a \"quote\"\n"}}"#;

pub const WRITE_EXAMPLE_CONTENT: &str = "first line\nsecond line with a \"quote\"\n";

pub const WRITE_EXAMPLE_PATH: &str = "example.txt";

const ONE_OBJECT_RULE: &str = "Reply with EXACTLY ONE JSON object and nothing else — no prose \
before it, no prose after it, no markdown fences, never two objects. \
Never emit <|channel|>, <|message|>, <|start|>, or <|end|> tokens; start at the opening brace.";

pub const VERDICT_EXAMPLE: &str = r#"{"action": "verdict", "verdict": "PASS", "next_state": "DONE", "reason": "<one line: what the transcript actually shows>", "corrections": []}"#;

pub const VERDICT_REASON_PLACEHOLDER: &str = "<one line: what the transcript actually shows>";

const EXTENSION_ANCHORS: &str = "File content rules:
- .html: a complete document starting with <!doctype html> and ending with </html>.
- .css: real rule blocks with { and }, no markdown fences.
- .js / .ts: runnable source with balanced brackets, no markdown fences.
- .json: one valid JSON value and nothing else.
- .md: the document itself, not a description of it.
- .py: importable source, no markdown fences.";

pub const DEFAULT_PLANNER_PROMPT: &str = r#"You are the planner of an agent loop. Reply with EXACTLY ONE JSON object and nothing else.

{"action": "plan", "goal": "<the user's request, restated in one line>", "steps": [{"action": "<tool name>", "args": {"path": "<file>"}, "description": "<what this step does>"}], "estimated_steps": <integer>, "requires_approval": <true|false>, "rollback_strategy": "none"}

Rules:
- Every file the request asks for gets its own write_file step, with its real path in args.path.
- Keep the plan to the steps that actually change something; do not plan to "review" or "confirm".
- requires_approval is true only when a step runs a command or touches something outside the workspace."#;

pub const DEFAULT_CRITIC_PROMPT: &str = concat!(
    "You are the critic of an agent loop. Reply with EXACTLY ONE JSON object and nothing else — ",
    "no prose before it, no prose after it, no markdown fences, never two objects. ",
    "Never emit <|channel|>, <|message|>, <|start|>, or <|end|> tokens; start at the opening brace.\n\n",
    "{\"action\": \"verdict\", \"verdict\": \"PASS\"|\"FAIL\", \"next_state\": \"DONE\"|\"EXECUTING\"|\"ROLLBACK\"|\"FAILED\", ",
    "\"reason\": \"<one or two lines>\", \"corrections\": [\"<what to do differently>\"], \"confidence\": <0.0-1.0>}\n\n",
    "Example:\n",
    r#"{"action": "verdict", "verdict": "PASS", "next_state": "DONE", "reason": "<one line: what the transcript actually shows>", "corrections": []}"#,
    "\n\nRules:\n",
    "- PASS with next_state DONE only when the transcript shows the work was actually done — a file that was requested and never written is a FAIL.\n",
    "- FAIL with next_state EXECUTING when another attempt could fix it; put the specific instruction in corrections.\n",
    "- FAIL with next_state ROLLBACK only when a change made things worse and should be undone.\n",
    "- Judge the transcript in front of you. Do not assume steps that are not in it."
);

pub fn executor_prompt(
    profile: AgentProfile,
    tool_names: &[String],
    skills: &[SkillBrief],
) -> String {
    let actions = action_lines(tool_names);
    let skills = skills_block(skills);
    if profile.lean_context {
        return format!(
            "Example:\n{WRITE_EXAMPLE}\n\n\
{ONE_OBJECT_RULE}\n\n\
{{\"thoughts\": \"<short>\", \"action\": \"<name>\", \"args\": {{...}}}}\n\
Also accepted: {{\"name\": \"<tool>\", \"arguments\": {{...}}}}\n\n\
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
    use crate::kernel::profile::{COMPACT, STANDARD};

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
    fn tool_signatures_are_strictly_ascii_sorted_and_cover_expanded_catalog() {
        for pair in TOOL_SIGNATURES.windows(2) {
            let left = pair[0].0;
            let right = pair[1].0;
            assert!(left.is_ascii(), "{left} must be ASCII");
            assert!(right.is_ascii(), "{right} must be ASCII");
            assert!(
                left.as_bytes() < right.as_bytes(),
                "TOOL_SIGNATURES out of strict ASCII order: {left:?} before {right:?}"
            );
        }

        let expected = [
            ("web_search", "web_search{query}"),
            ("web_fetch", "web_fetch{url}"),
            ("computer_ui_inspect", "computer_ui_inspect{app}"),
            ("computer_screenshot", "computer_screenshot"),
            ("computer_status", "computer_status"),
            ("computer_click", "computer_click{x, y}"),
            ("computer_type", "computer_type{text}"),
            ("computer_key", "computer_key{key}"),
            ("computer_scroll", "computer_scroll{x, y}"),
            ("computer_move", "computer_move{x, y}"),
            ("computer_drag", "computer_drag{x1, y1, x2, y2}"),
            ("computer_open_app", "computer_open_app{app}"),
            ("computer_open_url", "computer_open_url{url}"),
            ("knowledge_search", "knowledge_search{query}"),
            ("knowledge_graph_search", "knowledge_graph_search{query}"),
            ("knowledge_graph_context", "knowledge_graph_context{query}"),
            ("knowledge_tree", "knowledge_tree"),
            ("grep", "grep{pattern}"),
            ("git_status", "git_status"),
            ("git_diff", "git_diff"),
        ];
        for (name, rendered) in expected {
            assert_eq!(signature(name), rendered, "signature mismatch for {name}");
            let catalog_entries = crate::tools::catalog::native_entries(&[name.to_string()]);
            assert_eq!(
                catalog_entries[0].signature(),
                rendered,
                "catalog signature mismatch for {name}"
            );
        }
    }

    #[test]
    fn an_empty_tool_table_still_names_the_actions_that_exist() {
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
        assert!(prompt.contains(".html: a complete document"), "the anchors");
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
        assert!(compact.contains(".html: a complete document"));
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
        assert!(
            executor_prompt(COMPACT, &names(&["write_file"]), &[]).contains("Also accepted:"),
            "compact names the function-calling envelope 2B models actually emit"
        );
    }

    #[test]
    fn the_planner_and_critic_defaults_name_the_shapes_their_phases_parse() {
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
        let (action, repairs) = crate::parse::action::extract_action_details(WRITE_EXAMPLE)
            .expect("the example must parse");
        assert_eq!(repairs, Vec::<String>::new(), "and needs no repair");
        assert_eq!(action["action"], "write_file");
        let content = action["args"]["content"].as_str().expect("content");
        assert!(
            content.contains('\n') && content.contains('"'),
            "the example must teach the two escapes weak models get wrong"
        );
        assert_eq!(
            action["args"]["path"], WRITE_EXAMPLE_PATH,
            "and must not be a plausible answer to a real request"
        );
        assert_eq!(
            content, WRITE_EXAMPLE_CONTENT,
            "the exported copy must be the bytes the example really shows — \
             it is what the loop refuses to write"
        );
        let (ok, reason) = crate::content::sanitize::validate_file_content(content, "example.txt");
        assert!(ok, "{reason}");
        let (verdict, repairs) =
            crate::parse::action::extract_action_details(VERDICT_EXAMPLE).expect("verdict example");
        assert_eq!(repairs, Vec::<String>::new());
        assert_eq!(verdict["action"], "verdict");
        assert_eq!(verdict["verdict"], "PASS");
    }
}
