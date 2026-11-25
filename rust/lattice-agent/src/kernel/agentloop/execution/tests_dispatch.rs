use super::*;
use crate::kernel::agentloop::harness::harness;
use crate::kernel::agentloop::Runtime;
use crate::kernel::policy::ToolPolicy;
use crate::kernel::state::AgentState;

const FINAL: &str = r#"{"action": "final", "message": "done"}"#;

#[derive(Debug)]
struct CountingCatalog {
    calls: std::sync::Mutex<u32>,
}

impl crate::tools::catalog::ToolCatalog for CountingCatalog {
    fn entries(&self) -> Vec<crate::tools::catalog::CatalogEntry> {
        Vec::new()
    }

    fn execute<'a>(
        &'a self,
        _name: &'a str,
        _args: &'a Map<String, Value>,
        _scope: &'a crate::tools::CallScope,
    ) -> crate::tools::ToolFuture<'a> {
        Box::pin(async move {
            *self.calls.lock().expect("lock") += 1;
            crate::seam::worker::ToolOutcome::Error("catalog must not run".into())
        })
    }
}

#[tokio::test]
async fn a_governed_mcp_name_takes_the_kernel_chain_and_keeps_its_prefix() {
    let catalog = std::sync::Arc::new(CountingCatalog {
        calls: std::sync::Mutex::new(0),
    });
    let mut harness = harness(&[]).await;
    harness.runtime.deps.external = Some(catalog.clone());
    let mut ctx = harness.context();
    std::fs::write(harness.root.join("README.md"), "hello from readme\n").expect("readme");
    let mut args = Map::new();
    args.insert("path".into(), json!("README.md"));
    let flow = harness
        .runtime
        .perform_action(
            &mut ctx,
            &harness.request,
            Chosen {
                name: "mcp.read_file",
                thoughts: "t",
                args,
                final_message: None,
            },
        )
        .await;
    assert!(matches!(flow, StepFlow::Continue));
    assert_eq!(
        *catalog.calls.lock().expect("lock"),
        0,
        "a governed name must not go through the host catalog"
    );
    let step = ctx.transcript.last().expect("a step");
    assert_eq!(
        step["action"],
        json!("mcp.read_file"),
        "the catalog name is what was chosen: {step:?}"
    );
    assert!(
        step.get("result").is_some() || step.get("error").is_some(),
        "{step:?}"
    );
}

#[derive(Debug)]
struct NamingCatalog;

impl crate::tools::ToolCatalog for NamingCatalog {
    fn entries(&self) -> Vec<crate::tools::catalog::CatalogEntry> {
        use crate::tools::catalog::{CatalogEntry, EntryKind};
        vec![
            CatalogEntry {
                name: "skill.code_review".into(),
                kind: EntryKind::Skill,
                summary: "review code for defects".into(),
                required: Vec::new(),
            },
            CatalogEntry {
                name: "mcp.remote_search".into(),
                kind: EntryKind::Mcp,
                summary: "search a remote index".into(),
                required: vec![crate::tools::catalog::ArgSpec::line("query", "what")],
            },
        ]
    }

    fn execute<'a>(
        &'a self,
        _name: &'a str,
        _args: &'a Map<String, Value>,
        _scope: &'a crate::tools::CallScope,
    ) -> crate::tools::ToolFuture<'a> {
        Box::pin(async move { crate::seam::worker::ToolOutcome::Error("not run".into()) })
    }
}

#[tokio::test]
async fn the_json_prompt_names_the_whole_catalog_and_leads_with_what_was_asked_for() {
    let mut harness = harness(&[]).await;
    harness.runtime.deps.external = Some(std::sync::Arc::new(NamingCatalog));
    harness.request.message =
        "code_review 스킬을 참고해서 notes/review_note.md에 리뷰 체크리스트를 써줘".into();
    let ctx = harness.context();
    let context =
        harness
            .runtime
            .executor_context(&ctx, &harness.request, crate::kernel::profile::COMPACT);
    assert!(
        context.contains("- skill.code_review"),
        "a skill the run can actually call must be a named action: {context}"
    );
    assert!(context.contains("- mcp.remote_search"));
    let names = harness
        .runtime
        .prompt_action_names(&harness.request.message);
    assert_eq!(
        names.first().map(String::as_str),
        Some("skill.code_review"),
        "the row the request names leads: {names:?}"
    );
    assert_eq!(
        names.iter().filter(|name| *name == "final").count(),
        1,
        "{names:?}"
    );
    assert_eq!(names.last().map(String::as_str), Some("final"), "{names:?}");
    let plain = harness.runtime.prompt_action_names("");
    assert_eq!(plain.first().map(String::as_str), Some("read_file"));
    assert_eq!(plain.last().map(String::as_str), Some("final"));
}

#[test]
fn an_mcp_row_is_rendered_with_the_arguments_of_the_tool_it_resolves_to() {
    let lines = crate::prompts::executor_prompt(
        crate::kernel::profile::COMPACT,
        &["mcp.read_file".to_string(), "skill.code_review".to_string()],
        &[],
    );
    assert!(lines.contains("- mcp.read_file{path}"), "{lines}");
    assert!(
        lines.contains("- skill.code_review\n") || lines.contains("- skill.code_review"),
        "a skill takes no argument the harness could ask for: {lines}"
    );
}

#[tokio::test]
async fn a_json_dial_call_with_no_path_takes_the_default_the_tool_documents() {
    let mut harness = harness(&[
        r#"{"thoughts": "list it", "action": "list_dir", "args": {}}"#,
        FINAL,
    ])
    .await;
    harness.runtime.deps.tool_names = vec!["list_dir".into(), "write_file".into()];
    harness
        .runtime
        .deps
        .policies
        .tools
        .insert("list_dir".into(), ToolPolicy::read_only());
    harness.runtime.deps.agent_profile = Some(crate::kernel::profile::COMPACT);
    harness.request.message =
        "이 폴더에 어떤 파일이 있는지 list_dir로 확인하고 파일 개수를 알려줘".into();
    let mut ctx = harness.context();
    ctx.state = AgentState::Executing;
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("worker");

    let call = harness
        .tool_calls()
        .into_iter()
        .find(|call| call["tool"] == json!("list_dir"))
        .expect("the listing was dispatched, not refused");
    assert_eq!(call["args"]["path"], json!("."), "the tool's own default");
    assert!(
        !ctx.transcript
            .iter()
            .any(|step| step.get("error").is_some()),
        "nothing was refused: {:?}",
        ctx.transcript
    );
}

#[tokio::test]
async fn a_json_dial_call_missing_a_default_less_argument_is_refused_with_a_sentence() {
    let mut harness = harness(&[
        r#"{"thoughts": "search", "action": "grep", "args": {"path": "."}}"#,
        FINAL,
    ])
    .await;
    harness.runtime.deps.tool_names = vec!["grep".into(), "write_file".into()];
    harness
        .runtime
        .deps
        .policies
        .tools
        .insert("grep".into(), ToolPolicy::read_only());
    harness.runtime.deps.agent_profile = Some(crate::kernel::profile::COMPACT);
    let mut ctx = harness.context();
    ctx.state = AgentState::Executing;
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("worker");

    assert!(
        harness
            .tool_calls()
            .iter()
            .all(|call| call["tool"] != json!("grep")),
        "a call with no pattern is not a call: {:?}",
        harness.tool_calls()
    );
    let refusal = ctx
        .transcript
        .iter()
        .find(|step| step.get("error").is_some())
        .expect("a refusal step");
    assert_eq!(
        refusal["error"],
        json!("grep needs args.pattern. Nothing was done."),
        "the harness's own sentence, never the seam's exception repr"
    );
    assert!(
        ctx.corrections
            .iter()
            .any(|hint| hint.as_str().unwrap_or_default().contains("no pattern")),
        "and the next turn is told which argument: {:?}",
        ctx.corrections
    );
}

#[tokio::test]
async fn the_probe_chooses_how_a_turn_is_phrased_and_never_which_guards_exist() {
    let request = "code_review 스킬을 참고해서 notes/review_note.md에 리뷰 체크리스트를 써줘";
    let mut rows = harness(&[]).await;
    rows.runtime.deps.external = Some(std::sync::Arc::new(NamingCatalog));
    rows.request.message = request.into();
    let ctx = rows.context();

    let menu: Vec<String> = rows
        .runtime
        .rank_catalog(&ctx, request)
        .into_iter()
        .map(|entry| entry.name)
        .collect();
    let listed = rows.runtime.prompt_action_names(request);
    assert_eq!(
        menu.first().map(String::as_str),
        Some("skill.code_review"),
        "guided menu: {menu:?}"
    );
    assert_eq!(
        listed.first().map(String::as_str),
        Some("skill.code_review"),
        "JSON action list: {listed:?}"
    );
    for row in &menu {
        assert!(
            listed.contains(row),
            "{row} is on the menu and not the list"
        );
    }

    for profile in [
        crate::kernel::profile::COMPACT,
        crate::kernel::profile::GUIDED,
        crate::kernel::profile::STANDARD,
    ] {
        let mut dial = harness(&[]).await;
        dial.runtime.deps.agent_profile = Some(profile);
        dial.runtime.deps.tool_names = vec!["grep".into(), "list_dir".into()];
        for read_only in ["grep", "list_dir"] {
            dial.runtime
                .deps
                .policies
                .tools
                .insert(read_only.into(), ToolPolicy::read_only());
        }
        let mut ctx = dial.context();
        ctx.state = AgentState::Executing;
        let flow = dial
            .runtime
            .perform_action(
                &mut ctx,
                &dial.request,
                Chosen {
                    name: "grep",
                    thoughts: "t",
                    args: Map::new(),
                    final_message: None,
                },
            )
            .await;
        assert!(matches!(flow, StepFlow::Continue), "{}", profile.name);
        assert_eq!(
            ctx.transcript.last().expect("step")["error"],
            json!("grep needs args.pattern. Nothing was done."),
            "dial {}",
            profile.name
        );
        let _ = dial
            .runtime
            .perform_action(
                &mut ctx,
                &dial.request,
                Chosen {
                    name: "list_dir",
                    thoughts: "t",
                    args: Map::new(),
                    final_message: None,
                },
            )
            .await;
        assert_eq!(
            ctx.transcript.last().expect("step")["args"]["path"],
            json!("."),
            "dial {}",
            profile.name
        );
    }
}

#[tokio::test]
async fn a_final_turn_over_a_count_question_carries_the_number() {
    let mut harness = harness(&[r#"{"action": "final", "message": "폴더를 확인했습니다"}"#]).await;
    harness.request.message = "이 폴더의 파일 개수를 알려줘".into();
    let mut ctx = harness.context();
    ctx.transcript.push(json!({
        "state": "EXECUTING", "action": "list_dir", "args": {"path": "."},
        "result": {"items": [{"name": "a"}, {"name": "b"}]},
    }));
    ctx.state = crate::kernel::state::AgentState::Executing;
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("worker");
    assert_eq!(ctx.final_message, "폴더를 확인했습니다 (2개)");
}

#[tokio::test]
async fn a_final_turn_over_a_created_file_restores_the_artifact_fact_and_drops_negation() {
    let mut h1 = harness(&[r#"{"action": "final", "message": "I did nothing."}"#]).await;
    h1.request.message = "notes/hello.md에 인사말을 저장해줘".into();
    let mut ctx = h1.context();
    ctx.transcript.push(json!({
        "state": "EXECUTING", "action": "write_file", "args": {"path": "notes/hello.md", "content": "notes/hello.md"},
        "result": {"path": "notes/hello.md", "bytes": 14},
    }));
    ctx.state = crate::kernel::state::AgentState::Executing;
    h1.runtime
        .execute(&mut ctx, &h1.request)
        .await
        .expect("worker");
    assert_eq!(
        ctx.final_message,
        "notes/hello.md 파일을 작성했습니다 (14B)."
    );

    let mut h2 = harness(&[r#"{"action": "final", "message": "The file notes/hello.md was successfully written to disk."}"#]).await;
    h2.request.message = "notes/hello.md에 인사말을 저장해줘".into();
    let mut ctx2 = h2.context();
    ctx2.transcript.push(json!({
        "state": "EXECUTING", "action": "write_file", "args": {"path": "notes/hello.md", "content": "notes/hello.md"},
        "result": {"path": "notes/hello.md", "bytes": 14},
    }));
    ctx2.state = crate::kernel::state::AgentState::Executing;
    h2.runtime
        .execute(&mut ctx2, &h2.request)
        .await
        .expect("worker");
    assert_eq!(
        ctx2.final_message,
        "The file notes/hello.md was successfully written to disk."
    );
}

#[tokio::test]
async fn a_weak_dial_that_finishes_before_it_runs_anything_is_told_to_run_something() {
    let mut harness = harness(&[
        r#"{"thoughts": "plan the workflow", "action": "plan", "args": {}}"#,
        FINAL,
        r#"{"thoughts": "search", "action": "read_file", "args": {"path": "README.md"}}"#,
        FINAL,
    ])
    .await;
    harness.runtime.deps.agent_profile = Some(crate::kernel::profile::COMPACT);
    let mut ctx = harness.context();
    ctx.state = crate::kernel::state::AgentState::Executing;
    ctx.plan = json!({"steps": [{"action": "read_file", "args": {"path": "README.md"}}]})
        .as_object()
        .expect("plan")
        .clone();
    std::fs::write(harness.root.join("README.md"), "hello").expect("seed");
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("worker");

    assert!(
        ctx.transcript.iter().any(|step| step["error"]
            .as_str()
            .unwrap_or_default()
            .starts_with("PREMATURE_FINAL")),
        "{:?}",
        ctx.transcript
    );
    assert!(
        Runtime::has_execution_evidence(&ctx),
        "the run went on and did the work: {:?}",
        ctx.transcript
    );
}

#[tokio::test]
async fn a_standard_dial_and_a_planless_run_still_finish_on_the_first_word() {
    for (profile, plan) in [
        (
            crate::kernel::profile::STANDARD,
            json!({"steps": [{"action": "read_file", "args": {"path": "README.md"}}]}),
        ),
        (crate::kernel::profile::COMPACT, json!({"steps": []})),
    ] {
        let mut harness = harness(&[FINAL]).await;
        harness.runtime.deps.agent_profile = Some(profile);
        let mut ctx = harness.context();
        ctx.state = crate::kernel::state::AgentState::Executing;
        ctx.plan = plan.as_object().expect("plan").clone();
        harness
            .runtime
            .execute(&mut ctx, &harness.request)
            .await
            .expect("worker");
        assert_eq!(ctx.final_message, "done", "{}", profile.name);
        assert!(
            !ctx.transcript.iter().any(|step| step["error"]
                .as_str()
                .unwrap_or_default()
                .starts_with("PREMATURE_FINAL")),
            "{}: {:?}",
            profile.name,
            ctx.transcript
        );
    }
}

const SKILL_REQUEST: &str =
    "code_review 스킬을 참고해서 notes/review_note.md에 리뷰 체크리스트를 써줘";

#[derive(Debug, Default)]
struct SkillCatalog {
    calls: std::sync::Mutex<Vec<String>>,
}

impl crate::tools::ToolCatalog for SkillCatalog {
    fn entries(&self) -> Vec<crate::tools::catalog::CatalogEntry> {
        use crate::tools::catalog::{CatalogEntry, EntryKind};
        vec![CatalogEntry {
            name: "skill.code_review".into(),
            kind: EntryKind::Skill,
            summary: "review code for defects".into(),
            required: Vec::new(),
        }]
    }

    fn execute<'a>(
        &'a self,
        name: &'a str,
        _args: &'a Map<String, Value>,
        _scope: &'a crate::tools::CallScope,
    ) -> crate::tools::ToolFuture<'a> {
        Box::pin(async move {
            self.calls.lock().expect("lock").push(name.to_string());
            crate::seam::worker::ToolOutcome::Result(json!({
                "kind": "skill", "name": name,
                "text": "# Skill: code_review\n\nCheck bugs, security, performance, style.",
                "note": "A skill is guidance, not an executable.",
            }))
        })
    }
}

#[tokio::test]
async fn a_json_dial_path_the_tool_says_is_not_there_is_repaired_with_the_default() {
    let mut harness = harness(&[
        r#"{"thoughts": "list it", "action": "list_dir", "args": {"path": "file_list.txt"}}"#,
        FINAL,
    ])
    .await;
    harness.runtime.deps.tool_names = vec!["list_dir".into(), "write_file".into()];
    harness
        .runtime
        .deps
        .policies
        .tools
        .insert("list_dir".into(), ToolPolicy::read_only());
    harness.runtime.deps.agent_profile = Some(crate::kernel::profile::COMPACT);
    harness.request.message =
        "이 폴더에 어떤 파일이 있는지 list_dir로 확인하고 파일 개수를 알려줘".into();
    {
        let mut bodies = harness.worker.tool_bodies.lock().expect("lock");
        bodies.insert(
            "list_dir:file_list.txt".into(),
            json!({"error": "Directory does not exist."}),
        );
        bodies.insert(
            "list_dir:.".into(),
            json!({"result": {"path": ".", "items": [{"name": "a.md"}, {"name": "b.md"}]}}),
        );
    }
    let mut ctx = harness.context();
    ctx.state = AgentState::Executing;
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("worker");

    let paths: Vec<Value> = harness
        .tool_calls()
        .into_iter()
        .filter(|call| call["tool"] == json!("list_dir"))
        .map(|call| call["args"]["path"].clone())
        .collect();
    assert_eq!(paths, vec![json!("file_list.txt"), json!(".")]);
    let repaired = ctx
        .transcript
        .iter()
        .find(|step| step.get("arg_repair").is_some())
        .expect("the repair is on the record");
    assert_eq!(repaired["arg_repair"]["default"], json!("."));
    assert_eq!(
        repaired["result"]["items"].as_array().map(Vec::len),
        Some(2)
    );
    assert!(ctx.final_message.contains('2'), "{}", ctx.final_message);
}

#[tokio::test]
async fn a_gate_refusal_is_never_repaired_as_if_it_were_an_argument() {
    let mut harness = harness(&[
        r#"{"thoughts": "list it", "action": "list_dir", "args": {"path": "somewhere"}}"#,
        FINAL,
    ])
    .await;
    harness.runtime.deps.tool_names = vec!["list_dir".into()];
    harness
        .runtime
        .deps
        .policies
        .tools
        .insert("list_dir".into(), ToolPolicy::read_only());
    harness.worker.tool_bodies.lock().expect("lock").insert(
        "list_dir:somewhere".into(),
        json!({"error": "Permission denied by policy."}),
    );
    let mut ctx = harness.context();
    ctx.state = AgentState::Executing;
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("worker");
    assert_eq!(
        harness
            .tool_calls()
            .iter()
            .filter(|call| call["tool"] == json!("list_dir"))
            .count(),
        1,
        "one dispatch: a refusal is not a wrong argument"
    );
    assert!(!ctx
        .transcript
        .iter()
        .any(|step| step.get("arg_repair").is_some()));
}

#[tokio::test]
async fn a_read_of_the_file_the_request_asks_us_to_write_is_steered_to_the_write() {
    let mut harness = harness(&[
        r#"{"thoughts": "read it", "action": "read_file", "args": {"path": "notes/review_note.md"}}"#,
        FINAL,
    ])
    .await;
    harness.request.message = SKILL_REQUEST.into();
    harness
        .worker
        .tool_bodies
        .lock()
        .expect("lock")
        .insert("read_file".into(), json!({"error": "File does not exist."}));
    let mut ctx = harness.context();
    ctx.state = AgentState::Executing;
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("worker");

    let step = ctx
        .transcript
        .iter()
        .find(|step| step.get("action") == Some(&json!("read_file")))
        .expect("the read");
    let error = step["error"].as_str().unwrap_or_default();
    assert!(
        error.starts_with("File does not exist."),
        "the tool's own words are kept: {error}"
    );
    assert!(
        error.contains("그 파일은 아직 없습니다")
            && error.contains("write_file(notes/review_note.md)"),
        "and the steer names the tool and the path: {error}"
    );
    assert!(
        ctx.corrections
            .iter()
            .any(|hint| hint.as_str().unwrap_or_default().contains("write_file")),
        "the next turn is steered too: {:?}",
        ctx.corrections
    );
    assert!(
        !harness.root.join("notes/review_note.md").exists(),
        "steering is not writing: the model still authors the file"
    );

    let mut plain = harness_with_missing_read("README.md", "이 파일 읽어줘").await;
    let error = plain.remove(0);
    assert_eq!(error, "File does not exist.");
}

async fn harness_with_missing_read(path: &str, request: &str) -> Vec<String> {
    let action =
        json!({"thoughts": "read", "action": "read_file", "args": {"path": path}}).to_string();
    let mut harness = harness(&[&action, FINAL]).await;
    harness.request.message = request.into();
    harness
        .worker
        .tool_bodies
        .lock()
        .expect("lock")
        .insert("read_file".into(), json!({"error": "File does not exist."}));
    let mut ctx = harness.context();
    ctx.state = AgentState::Executing;
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("worker");
    ctx.transcript
        .iter()
        .filter_map(|step| step.get("error").and_then(Value::as_str))
        .map(str::to_string)
        .collect()
}

#[tokio::test]
async fn a_skill_the_request_names_is_consulted_first_and_chooses_nothing() {
    let catalog = std::sync::Arc::new(SkillCatalog::default());
    let write = json!({"thoughts": "write it", "action": "write_file",
                       "args": {"path": "notes/review_note.md",
                                "content": "# 체크리스트\n\n- 버그\n- 보안\n"}})
    .to_string();
    let mut harness = harness(&[&write, FINAL]).await;
    harness.runtime.deps.external = Some(catalog.clone());
    harness.runtime.deps.agent_profile = Some(crate::kernel::profile::COMPACT);
    harness.request.permission_mode = Some("trusted".into());
    harness.request.message = SKILL_REQUEST.into();
    let mut ctx = harness.context();
    ctx.state = AgentState::Executing;
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("worker");

    let actions: Vec<&str> = ctx
        .transcript
        .iter()
        .filter_map(|step| step.get("action").and_then(Value::as_str))
        .collect();
    assert_eq!(actions, vec!["skill.code_review", "write_file", "final"]);
    assert_eq!(ctx.transcript[0]["result"]["kind"], json!("skill"));
    assert_eq!(catalog.calls.lock().expect("lock").len(), 1, "read once");
    let written =
        std::fs::read_to_string(harness.root.join("notes/review_note.md")).expect("the file");
    assert!(written.contains("체크리스트"), "{written}");
    assert_eq!(ctx.transcript[1]["thoughts"], json!("write it"));
    let first_execute_context = harness
        .worker
        .calls
        .lock()
        .expect("lock")
        .iter()
        .find(|call| call["seam"] == json!("llm"))
        .expect("an execute turn")["body"]["context"]
        .as_str()
        .unwrap_or_default()
        .to_string();
    assert!(
        first_execute_context.contains("Check bugs, security"),
        "{first_execute_context}"
    );
}

#[tokio::test]
async fn a_skill_the_request_does_not_name_is_never_consulted_for_it() {
    let catalog = std::sync::Arc::new(SkillCatalog::default());
    let mut harness = harness(&[FINAL]).await;
    harness.runtime.deps.external = Some(catalog.clone());
    harness.request.message = "notes/hello.md에 인사말을 써줘".into();
    let mut ctx = harness.context();
    ctx.state = AgentState::Executing;
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("worker");
    assert!(catalog.calls.lock().expect("lock").is_empty());
    assert_eq!(
        ctx.transcript
            .iter()
            .filter_map(|step| step.get("action").and_then(Value::as_str))
            .collect::<Vec<_>>(),
        vec!["final"]
    );
}

#[tokio::test]
async fn a_named_skill_is_consulted_once_and_never_again_on_a_retry() {
    let catalog = std::sync::Arc::new(SkillCatalog::default());
    let mut harness = harness(&[FINAL, FINAL]).await;
    harness.runtime.deps.external = Some(catalog.clone());
    harness.request.message = SKILL_REQUEST.into();
    let mut ctx = harness.context();
    ctx.state = AgentState::Executing;
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("worker");
    ctx.state = AgentState::Executing;
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("worker");
    assert_eq!(catalog.calls.lock().expect("lock").len(), 1);
}

#[tokio::test]
async fn a_skill_when_clause_is_consulted_without_naming_the_skill() {
    let catalog = std::sync::Arc::new(SkillCatalog::default());
    let mut harness = harness(&[FINAL]).await;
    harness.runtime.deps.external = Some(catalog.clone());
    harness.request.message = "reviewing a diff, write notes/review.md".into();
    harness.request.skills = vec![crate::prompts::SkillBrief {
        name: "code_review".into(),
        brief: "review code for defects".into(),
        when: "reviewing a diff".into(),
    }];
    let mut ctx = harness.context();
    ctx.state = AgentState::Executing;
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("worker");
    assert_eq!(
        catalog.calls.lock().expect("lock").as_slice(),
        ["skill.code_review"],
        "the when-clause is the request naming the skill"
    );
}
