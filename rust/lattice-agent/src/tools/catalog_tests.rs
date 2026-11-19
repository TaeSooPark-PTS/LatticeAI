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
