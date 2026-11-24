//! End-to-end and unit verification suite for the Phase 1 Universal Adaptive
//! Harness (`probe`, `parse`, `tools::catalog`, `prompts`, `execution`,
//! `guided`, `verification`, `transcript`, and `rag_grounding`).

mod common;

use std::path::PathBuf;

use common::{loop_deps, loop_request, start_replay_worker};
use lattice_agent::kernel::agentloop::execution::{
    request_wants_computer_use, request_wants_knowledge, request_wants_web, url_named_in_request,
};
use lattice_agent::kernel::agentloop::Runtime;
use lattice_agent::kernel::policy::ToolPolicy;
use lattice_agent::kernel::probe::{
    cached, measure, score_action, store, verdict_for, ActionScore, ProbeConfig,
};
use lattice_agent::kernel::profile::{COMPACT, GUIDED, STANDARD};
use lattice_agent::kernel::state::{AgentRunContext, AgentState};
use lattice_agent::kernel::transcript::rag_grounding::{
    build_evidence_card, extract_evidence_passages, verify_grounded_answer, GroundingDefect,
};
use lattice_agent::kernel::transcript::{compact_transcript, distill_step_result};
use lattice_agent::parse::action::extract_action_details;
use lattice_agent::prompts::signature;
use lattice_agent::seam::worker::WorkerClient;
use lattice_agent::tools::catalog::{coerce_and_unwrap_args, entry_for, native_entries};
use lattice_agent::tools::sandbox::Workspace;
use serde_json::{json, Map, Value};

fn scratch(name: &str) -> PathBuf {
    let dir = PathBuf::from(env!("CARGO_TARGET_TMPDIR"))
        .join("adaptive_harness")
        .join(name);
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).expect("scratch dir");
    dir
}

#[tokio::test]
async fn probe_measures_reasoning_framing_and_grounding_with_legacy_cache_compatibility() {
    let dir = scratch("probe_measure");
    let config = ProbeConfig::new(dir.join("probe_cache"));

    // Legacy cache entry without `reasoning_framed` or `grounding_ok` deserializes safely.
    std::fs::create_dir_all(&config.cache_dir).expect("cache dir");
    std::fs::write(
        config.cache_path(),
        r#"{"verdicts":{"13.3.1|legacy-model":{"profile":"compact","action":"repaired","menu_ok":true,"harness":"13.3.1"}}}"#,
    )
    .expect("write legacy cache");
    let legacy = cached(&config, "legacy-model", "13.3.1").expect("legacy hit");
    assert_eq!(legacy.profile, COMPACT);
    assert_eq!(legacy.action, ActionScore::Repaired);
    assert!(legacy.menu_ok);
    assert!(!legacy.reasoning_framed);
    assert!(!legacy.grounding_ok);

    // Live 3-turn micro-probe against ReplayServer:
    // 1. Action probe reply wrapped in `<think>` framing -> Repaired + reasoning_framed = true
    // 2. Menu probe reply -> "2" -> menu_ok = true
    // 3. Grounding micro-probe reply -> "8443" (active value, not superseded 4040) -> grounding_ok = true
    let server = start_replay_worker(&dir).await;
    server.worker.push_completions(&[
        "<think>Need to write probe.txt</think>\n{\"thoughts\": \"write it\", \"action\": \"write_file\", \"args\": {\"path\": \"probe.txt\", \"content\": \"hello\\nshe said \\\"hi\\\"\\n\"}}".to_string(),
        "2".to_string(),
        "8443".to_string(),
    ]);
    let client = WorkerClient::new(&server.origin);
    let verdict = measure(&client, &config, Some("measured-model"), "13.3.1")
        .await
        .expect("measured verdict");
    assert_eq!(verdict.profile, COMPACT);
    assert!(verdict.menu_ok);
    assert!(verdict.reasoning_framed);
    assert!(verdict.grounding_ok);

    let detail = verdict.to_detail();
    assert_eq!(detail["reasoning_framed"], json!(true));
    assert_eq!(detail["grounding_ok"], json!(true));

    let cached_hit = cached(&config, "measured-model", "13.3.1").expect("cached hit");
    assert!(cached_hit.reasoning_framed);
    assert!(cached_hit.grounding_ok);
    assert_eq!(score_action(""), ActionScore::Failed);
    let stored = verdict_for(ActionScore::Clean, true);
    store(&config, "clean-model", "13.3.1", &stored);
    assert_eq!(
        cached(&config, "clean-model", "13.3.1")
            .expect("clean")
            .profile,
        STANDARD
    );
}

#[test]
fn parser_fallback_rungs_recover_unescaped_newlines_react_python_calls_and_xml_tags() {
    // 1. unescaped_newline before truncated_close / python_literal
    let raw_newline = "{\"thoughts\": \"line one\nline two\", \"action\": \"web_search\", \"args\": {\"query\": \"lattice\nrag\"}}";
    let (parsed, repairs) = extract_action_details(raw_newline).expect("unescaped_newline");
    assert_eq!(parsed["action"], json!("web_search"));
    assert_eq!(parsed["args"]["query"], json!("lattice\nrag"));
    assert!(
        repairs.contains(&"unescaped_newline".to_string()),
        "{repairs:?}"
    );
    assert!(
        !repairs.contains(&"truncated_close".to_string()),
        "{repairs:?}"
    );

    // 2. react_format (Action/Action Input and Tool/Input)
    let react = "Thought: I should search the web.\nAction: web_search\nAction Input: {\"query\": \"LatticeAI 13\", \"max_results\": 5}";
    let (parsed_react, repairs_react) = extract_action_details(react).expect("react_format");
    assert_eq!(parsed_react["action"], json!("web_search"));
    assert_eq!(parsed_react["args"]["query"], json!("LatticeAI 13"));
    assert!(
        repairs_react.contains(&"react_format".to_string()),
        "{repairs_react:?}"
    );

    let react_tool = "Tool: computer_click\nInput: x=420, y=310, button=\"left\"";
    let (parsed_tool, repairs_tool) = extract_action_details(react_tool).expect("react Tool/Input");
    assert_eq!(parsed_tool["action"], json!("computer_click"));
    assert_eq!(parsed_tool["args"]["x"], json!(420));
    assert_eq!(parsed_tool["args"]["y"], json!(310));
    assert_eq!(parsed_tool["args"]["button"], json!("left"));
    assert!(
        repairs_tool.contains(&"react_format".to_string()),
        "{repairs_tool:?}"
    );

    // 3. python_call syntax
    for (input, expected_action, expected_check) in [
        (
            r#"web_fetch(url="https://example.com/docs")"#,
            "web_fetch",
            json!({"url": "https://example.com/docs"}),
        ),
        (
            r#"computer_click(x=420, y=310, button="left", double=False)"#,
            "computer_click",
            json!({"x": 420, "y": 310, "button": "left", "double": false}),
        ),
        (
            r#"knowledge_search(query='bitemporal graph', max_results=5, include_graph=True)"#,
            "knowledge_search",
            json!({"query": "bitemporal graph", "max_results": 5, "include_graph": true}),
        ),
    ] {
        let (parsed_py, repairs_py) = extract_action_details(input).expect("python_call");
        assert_eq!(parsed_py["action"], json!(expected_action));
        assert_eq!(parsed_py["args"], expected_check);
        assert!(
            repairs_py.contains(&"python_call".to_string()),
            "{repairs_py:?}"
        );
    }

    // 4. Expanded xml_arg_tag + KNOWN_ACTIONS
    let xml = "<tool_call><name>computer_drag</name><x1>10</x1><y1>20</y1><x2>300</x2><y2>400</y2></tool_call>";
    let (parsed_xml, repairs_xml) = extract_action_details(xml).expect("xml_call");
    assert_eq!(parsed_xml["action"], json!("computer_drag"));
    assert_eq!(parsed_xml["args"]["x1"], json!("10"));
    assert_eq!(parsed_xml["args"]["y2"], json!("400"));
    assert!(repairs_xml.contains(&"xml_call".to_string()));
}

#[test]
fn catalog_coerces_and_unwraps_args_and_exposes_all_signatures() {
    let required_tools: Vec<String> = [
        "web_search",
        "web_fetch",
        "computer_ui_inspect",
        "computer_screenshot",
        "computer_status",
        "computer_click",
        "computer_type",
        "computer_key",
        "computer_scroll",
        "computer_move",
        "computer_drag",
        "computer_open_app",
        "computer_open_url",
        "knowledge_search",
        "knowledge_graph_search",
        "knowledge_graph_context",
        "knowledge_tree",
        "grep",
        "git_status",
        "git_diff",
    ]
    .into_iter()
    .map(String::from)
    .collect();

    let entries = native_entries(&required_tools);
    for tool in &required_tools {
        assert!(
            entry_for(&entries, tool).is_some(),
            "missing catalog entry for {tool}"
        );
    }
    assert_eq!(signature("web_search"), "web_search{query}");
    assert_eq!(signature("web_fetch"), "web_fetch{url}");
    assert_eq!(signature("computer_ui_inspect"), "computer_ui_inspect{app}");
    assert_eq!(signature("computer_click"), "computer_click{x, y}");
    assert_eq!(signature("computer_drag"), "computer_drag{x1, y1, x2, y2}");
    assert_eq!(
        signature("knowledge_graph_context"),
        "knowledge_graph_context{query}"
    );
    assert_eq!(signature("computer_screenshot"), "computer_screenshot");

    // Unwrap nested `arguments` + coerce stringified ints/float/bools.
    let click_entry = entry_for(&entries, "computer_click").expect("computer_click");
    let mut click_args: Map<String, Value> = serde_json::from_value(json!({
        "arguments": {
            "x": "420",
            "y": "310",
            "clicks": "2",
            "interval": "0.25",
            "double": "true"
        }
    }))
    .expect("map");
    let notes = coerce_and_unwrap_args(click_entry, &mut click_args);
    assert!(notes.contains(&"unwrapped:arguments".to_string()));
    assert_eq!(click_args["x"], json!(420));
    assert_eq!(click_args["y"], json!(310));
    assert_eq!(click_args["clicks"], json!(2));
    assert_eq!(click_args["interval"], json!(0.25));
    assert_eq!(click_args["double"], json!(true));

    // Normalize `computer_key` aliases.
    let key_entry = entry_for(&entries, "computer_key").expect("computer_key");
    for (raw_key, expected_key) in [
        ("Enter", "return"),
        ("ESC", "escape"),
        ("cmd+s", "command+s"),
        ("Ctrl+Shift+P", "control+Shift+P"),
    ] {
        let mut kargs: Map<String, Value> =
            serde_json::from_value(json!({"key": raw_key})).expect("map");
        let knotes = coerce_and_unwrap_args(key_entry, &mut kargs);
        assert!(!knotes.is_empty(), "expected key alias note for {raw_key}");
        assert_eq!(kargs["key"], json!(expected_key));
    }
}

#[test]
fn bilingual_domain_classifiers_and_observation_first_ranking_work() {
    assert!(request_wants_web("Search the web for LatticeAI benchmarks"));
    assert!(request_wants_web("웹에서 최신 LatticeAI 릴리스를 검색해줘"));
    assert!(request_wants_web(
        "https://example.com/docs 페이지를 요약해줘"
    ));
    assert!(!request_wants_web(
        "워크스페이스에서 LatticeAI를 mcp.grep으로 검색해줘"
    ));

    assert!(request_wants_computer_use(
        "Click the Submit button on the screen"
    ));
    assert!(request_wants_computer_use(
        "화면에서 로그인 버튼을 클릭해줘"
    ));
    assert!(!request_wants_computer_use(
        "notes/hello.md 파일을 만들어줘"
    ));

    assert!(request_wants_knowledge(
        "Search the knowledge graph for entity resolution"
    ));
    assert!(request_wants_knowledge("지식 그래프에서 엔티티를 검색해줘"));
    assert!(!request_wants_knowledge("make a note"));

    assert_eq!(
        url_named_in_request("https://docs.lattice.ai/v13/guide를 web_fetch로 읽어줘").as_deref(),
        Some("https://docs.lattice.ai/v13/guide")
    );

    // Verify observation-before-actuation ranking in guided & lean lists.
    let dir = scratch("ranking_obs");
    let workspace = Workspace::new(dir.join("ws")).expect("workspace");
    let mut deps = lattice_agent::kernel::agentloop::LoopDeps::new(
        WorkerClient::new("http://127.0.0.1:9"),
        workspace,
    );
    deps.tool_names = vec![
        "computer_click".into(),
        "computer_type".into(),
        "computer_ui_inspect".into(),
        "computer_screenshot".into(),
        "write_file".into(),
        "read_file".into(),
    ];
    let runtime = Runtime::new(deps);
    let mut ctx = AgentRunContext::new();
    let request = "화면에서 확인 버튼을 클릭해줘";

    // Before observation step: `computer_ui_inspect` and `computer_screenshot` precede `computer_click`.
    let ranked_before: Vec<String> = runtime
        .rank_catalog(&ctx, request)
        .into_iter()
        .map(|e| e.name)
        .collect();
    let pos_inspect = ranked_before
        .iter()
        .position(|n| n == "computer_ui_inspect")
        .expect("inspect");
    let pos_shot = ranked_before
        .iter()
        .position(|n| n == "computer_screenshot")
        .expect("screenshot");
    let pos_click = ranked_before
        .iter()
        .position(|n| n == "computer_click")
        .expect("click");
    assert!(pos_inspect < pos_click, "{ranked_before:?}");
    assert!(pos_shot < pos_click, "{ranked_before:?}");

    // After `computer_ui_inspect` observation step on transcript: `computer_click` precedes `computer_ui_inspect`.
    ctx.transcript.push(json!({
        "state": "EXECUTING",
        "action": "computer_ui_inspect",
        "args": {"app": "Finder"},
        "result": {"elements": [{"id": "btn_ok", "label": "확인", "x": 100, "y": 200, "width": 80, "height": 40}]}
    }));
    let ranked_after: Vec<String> = runtime
        .rank_catalog(&ctx, request)
        .into_iter()
        .map(|e| e.name)
        .collect();
    assert_eq!(
        ranked_after.first().map(String::as_str),
        Some("computer_click"),
        "{ranked_after:?}"
    );
}

#[tokio::test]
async fn execution_resolves_ui_element_coords_fills_url_coerces_args_and_upgrades_refusal_hint() {
    let dir = scratch("exec_ui_url");
    let workspace = Workspace::new(dir.join("ws")).expect("workspace");
    let server = start_replay_worker(workspace.root()).await;
    server.worker.load_tool_calls(&[
        json!({
            "tool": "web_fetch",
            "result": {"url": "https://example.com/status", "content": "Service port is 8443."}
        }),
        json!({
            "tool": "computer_ui_inspect",
            "result": {
                "elements": [
                    {"id": "btn_submit", "label": "Submit", "bounds": {"x": 400, "y": 300, "width": 40, "height": 20}}
                ]
            }
        }),
        json!({
            "tool": "computer_click",
            "result": {"clicked": true, "x": 420, "y": 310}
        }),
    ]);
    // 1. Call `web_fetch` with empty args -> URL extracted from request.
    // 2. Call `computer_ui_inspect`.
    // 3. Call `computer_click` with nested `arguments` + `element_id: "btn_submit"` + stringified `"clicks": "1"` -> unwrapped, coerced, and resolved to center (420, 310).
    // 4. Call `write_file` with missing required `content` -> refusal includes signature `write_file{path, content}` and parameter hint.
    // 5. Final.
    server.worker.push_completions(&[
        r#"{"thoughts": "fetch page", "action": "web_fetch", "args": {}}"#.to_string(),
        r#"{"thoughts": "inspect ui", "action": "computer_ui_inspect", "args": {}}"#.to_string(),
        r#"{"thoughts": "click submit", "action": "computer_click", "args": {"arguments": {"element_id": "btn_submit", "clicks": "1"}}}"#.to_string(),
        r#"{"thoughts": "incomplete write", "action": "write_file", "args": {"path": "notes/out.md"}}"#.to_string(),
        r#"{"thoughts": "done", "action": "final", "message": "Service port is 8443."}"#.to_string(),
    ]);

    let mut deps = loop_deps(&server, workspace);
    for tool in ["web_fetch", "computer_ui_inspect", "computer_click", "grep"] {
        if !deps.tool_names.contains(&tool.to_string()) {
            deps.tool_names.push(tool.to_string());
        }
        deps.policies
            .tools
            .insert(tool.to_string(), ToolPolicy::read_only());
    }
    let mut runtime = Runtime::new(deps);
    let mut req = loop_request("https://example.com/status 확인하고 Submit 버튼을 클릭해줘");
    req.permission_mode = Some("bypass".into());

    let mut ctx = AgentRunContext::new();
    ctx.state = AgentState::Executing;
    runtime.execute(&mut ctx, &req).await.expect("execute");

    let calls = server.worker.observed_calls();
    assert_eq!(calls[0]["tool"], json!("web_fetch"));
    assert_eq!(calls[0]["args"]["url"], json!("https://example.com/status"));
    assert_eq!(calls[2]["tool"], json!("computer_click"));
    assert_eq!(calls[2]["args"]["x"], json!(420));
    assert_eq!(calls[2]["args"]["y"], json!(310));
    assert_eq!(calls[2]["args"]["clicks"], json!(1));

    assert!(ctx
        .trace
        .events
        .iter()
        .any(|e| e["decision"] == json!("request_arg_filled")));
    assert!(ctx
        .trace
        .events
        .iter()
        .any(|e| e["decision"] == json!("arg_coerced")));
    assert!(ctx
        .trace
        .events
        .iter()
        .any(|e| e["decision"] == json!("ui_element_resolved")));
    assert!(
        ctx.corrections.iter().any(|c| {
            let s = c.as_str().unwrap_or_default();
            s.contains("write_file{path, content}") && s.contains("the file's whole content")
        }),
        "correction must include tool signature and param hint: {:?}",
        ctx.corrections
    );
}

#[tokio::test]
async fn mid_run_adaptive_escalation_enables_json_prefix_then_demotes_to_guided_keeping_transcript()
{
    let dir = scratch("mid_run_escalation");
    let workspace = Workspace::new(dir.join("ws")).expect("workspace");
    let server = start_replay_worker(workspace.root()).await;

    // Seed probe cache as `STANDARD` for `flaky-model` so it starts on measured `standard`.
    let probe_cfg = ProbeConfig::new(dir.join("probe"));
    store(
        &probe_cfg,
        "flaky-model",
        lattice_agent::VERSION,
        &verdict_for(ActionScore::Clean, true),
    );

    server.worker.load_tool_calls(&[json!({
        "tool": "list_dir",
        "result": {"path": ".", "items": ["a.md", "b.md"], "count": 2}
    })]);
    // Step 1 (standard): valid tool call `list_dir` succeeds -> execution evidence now exists on transcript!
    // Steps 2, 3, 4 (standard): 3 parse failures -> after 2nd failure `adaptive_prefix_enabled` fires;
    // after 3rd failure (`parse_failures >= STANDARD.parse_failure_budget`), `profile_demoted_mid_run`
    // fires even though step 1 succeeded, and the run finishes in `guided` mode!
    server.worker.push_completions(&[
        r#"{"thoughts": "list dir", "action": "list_dir", "args": {"path": "."}}"#.to_string(),
        "not valid json 1".to_string(),
        "not valid json 2".to_string(),
        "not valid json 3".to_string(),
        // Guided mode menu choice for `final` (row 1 because count_in_hand is true!) + final message
        "1".to_string(),
        "폴더에 파일이 2개 있습니다.".to_string(),
    ]);

    let mut deps = loop_deps(&server, workspace);
    deps.probe = Some(probe_cfg);
    deps.agent_profile = None;
    let mut runtime = Runtime::new(deps);
    let mut req = loop_request("이 폴더에 파일이 몇 개야?");
    req.executing_model = Some("flaky-model".into());

    let mut ctx = AgentRunContext::new();
    ctx.state = AgentState::Executing;
    runtime.execute(&mut ctx, &req).await.expect("execute");

    assert_eq!(ctx.state, AgentState::Verifying);
    assert!(
        ctx.trace
            .events
            .iter()
            .any(|e| e["decision"] == json!("adaptive_prefix_enabled")),
        "expected adaptive_prefix_enabled in trace: {:?}",
        ctx.trace.events
    );
    assert!(
        ctx.trace
            .events
            .iter()
            .any(|e| e["decision"] == json!("profile_demoted_mid_run")),
        "expected profile_demoted_mid_run in trace: {:?}",
        ctx.trace.events
    );
    // Prior transcript step (`list_dir`) is preserved intact!
    assert!(ctx
        .transcript
        .iter()
        .any(|s| s["action"] == json!("list_dir") && s["result"]["count"] == json!(2)));
    assert!(ctx.final_message.contains('2'), "{}", ctx.final_message);
}

#[tokio::test]
async fn rag_grounding_distills_context_enforces_abstention_self_repairs_and_verifies_fail_closed()
{
    // 1. Context distillation in `transcript.rs`
    let shot = json!({
        "state": "EXECUTING",
        "action": "computer_screenshot",
        "result": {"ok": true, "screenshot_b64": "iVBORw0KGgoAAAANSUhEUg=="}
    });
    let ksearch = json!({
        "state": "EXECUTING",
        "action": "knowledge_search",
        "result": {
            "results": [
                {"path": "notes/old.md", "text": "Legacy port was 4040.", "is_superseded": true},
                {"path": "notes/active.md", "text": "Active port is 8443.", "is_superseded": false}
            ]
        }
    });
    let distilled = distill_step_result(&ksearch);
    assert_eq!(distilled["result"]["superseded_filtered"], json!(1));
    assert_eq!(distilled["result"]["results"].as_array().unwrap().len(), 1);
    let compacted = compact_transcript(&[shot, ksearch.clone()], 4, 700);
    assert_eq!(
        compacted[0]["result"]["screenshot_b64"],
        json!("[screenshot omitted: 24 bytes]")
    );

    // 2. Unit verification of all 5 GroundingDefect types + build_evidence_card
    let passages = extract_evidence_passages(&[ksearch]);
    assert_eq!(passages.len(), 2);
    let card = build_evidence_card(&passages, 1000);
    assert!(card.starts_with("[ACTIVE GROUNDED EVIDENCE]"));
    assert!(card.contains("8443"));
    assert!(card.contains("do not use"));

    let leak_report = verify_grounded_answer("The port is 4040 [1].", &passages);
    assert!(leak_report.has(GroundingDefect::SupersededFactLeak));

    let num_report = verify_grounded_answer("The port is 9999 [1].", &passages);
    assert!(num_report.has(GroundingDefect::NumericMismatch));

    let pol_report = verify_grounded_answer("Active port is not 8443 [1].", &passages);
    assert!(pol_report.has(GroundingDefect::PolarityMismatch));

    // 3. Zero active evidence hallucination -> grounded_abstention_enforced on `final`
    let dir = scratch("rag_abstention");
    let workspace = Workspace::new(dir.join("ws")).expect("workspace");
    let server = start_replay_worker(workspace.root()).await;
    server.worker.load_tool_calls(&[json!({
        "tool": "knowledge_search",
        "result": {
            "results": [
                {"path": "notes/old.md", "text": "Port was 4040.", "is_superseded": true}
            ]
        }
    })]);
    server.worker.push_completions(&[
        r#"{"thoughts": "search", "action": "knowledge_search", "args": {"query": "port"}}"#
            .to_string(),
        r#"{"thoughts": "hallucinate", "action": "final", "message": "포트를 4040으로 사용합니다."}"#
            .to_string(),
    ]);
    let mut deps = loop_deps(&server, workspace);
    deps.policies
        .tools
        .insert("knowledge_search".into(), ToolPolicy::read_only());
    let mut runtime = Runtime::new(deps);
    let req = loop_request("현재 서비스 포트 번호를 지식 베이스에서 찾아줘");
    let mut ctx = AgentRunContext::new();
    ctx.state = AgentState::Executing;
    runtime.execute(&mut ctx, &req).await.expect("execute");
    assert!(
        ctx.trace
            .events
            .iter()
            .any(|e| e["decision"] == json!("grounded_abstention_enforced")),
        "{:?}",
        ctx.trace.events
    );
    assert!(
        ctx.final_message.contains("찾을 수 없습니다"),
        "{}",
        ctx.final_message
    );

    // 4. Superseded / numeric defect on `final` -> ONE targeted self-repair completion (`grounded_answer_repaired`)
    //    and if defect persists at `verify`, fail-closed `NEEDS_REVIEW` (`needs_review_rag_grounding`).
    let dir2 = scratch("rag_self_repair");
    let workspace2 = Workspace::new(dir2.join("ws")).expect("workspace");
    let server2 = start_replay_worker(workspace2.root()).await;
    server2.worker.load_tool_calls(&[json!({
        "tool": "knowledge_search",
        "result": {
            "results": [
                {"path": "notes/old.md", "text": "Service port is 4040.", "is_superseded": true},
                {"path": "notes/new.md", "text": "Service port is 8443.", "is_superseded": false}
            ]
        }
    })]);
    server2.worker.push_completions(&[
        r#"{"thoughts": "search", "action": "knowledge_search", "args": {"query": "port"}}"#
            .to_string(),
        // Defective final answer leaking superseded 4040
        r#"{"thoughts": "answer", "action": "final", "message": "Service port is 4040."}"#
            .to_string(),
        // Self-repair completion still leaks 4040 so we also test fail-closed verification!
        "Service port is 4040.".to_string(),
        // Critic says PASS, but verification.rs::settle_answer must fail closed to NEEDS_REVIEW!
        r#"{"action": "verdict", "verdict": "PASS", "next_state": "DONE", "reason": "ok", "corrections": []}"#
            .to_string(),
    ]);
    let mut deps2 = loop_deps(&server2, workspace2);
    deps2
        .policies
        .tools
        .insert("knowledge_search".into(), ToolPolicy::read_only());
    let mut runtime2 = Runtime::new(deps2);
    let req2 = loop_request("What is the current service port?");
    let mut ctx2 = AgentRunContext::new();
    ctx2.state = AgentState::Executing;
    runtime2.execute(&mut ctx2, &req2).await.expect("execute");
    assert!(
        ctx2.trace
            .events
            .iter()
            .any(|e| e["decision"] == json!("grounded_answer_repaired")),
        "{:?}",
        ctx2.trace.events
    );
    runtime2.verify(&mut ctx2, &req2).await.expect("verify");
    assert_eq!(ctx2.state, AgentState::NeedsReview);
    assert!(
        ctx2.trace
            .events
            .iter()
            .any(|e| e["decision"] == json!("needs_review_rag_grounding")),
        "{:?}",
        ctx2.trace.events
    );

    // 5. Guided mode `final` with evidence injects `[ACTIVE GROUNDED EVIDENCE]` and uses execute_tokens.min(512).
    let dir3 = scratch("rag_guided_final");
    let workspace3 = Workspace::new(dir3.join("ws")).expect("workspace");
    let server3 = start_replay_worker(workspace3.root()).await;
    server3.worker.push_completions(&[
        // Menu choice 3 (`final` in read_file, write_file, final)
        "3".to_string(),
        // Guided final message grounded in active evidence [1]
        "Service port is 8443 [1].".to_string(),
    ]);
    let mut deps3 = loop_deps(&server3, workspace3);
    deps3.agent_profile = Some(GUIDED);
    deps3.tool_names = vec!["write_file".into(), "read_file".into()];
    let mut runtime3 = Runtime::new(deps3);
    let req3 = loop_request("What is the service port?");
    let mut ctx3 = AgentRunContext::new();
    ctx3.state = AgentState::Executing;
    ctx3.transcript.push(json!({
        "state": "EXECUTING",
        "action": "web_fetch",
        "args": {"url": "https://example.com/port"},
        "result": {"url": "https://example.com/port", "content": "Service port is 8443."}
    }));
    runtime3
        .execute(&mut ctx3, &req3)
        .await
        .expect("guided execute");
    let prompts = server3.worker.observed_prompts();
    assert!(
        prompts
            .iter()
            .any(|p| p.contains("[ACTIVE GROUNDED EVIDENCE]") && p.contains("8443")),
        "guided final prompt must inject evidence card: {prompts:?}"
    );
    assert_eq!(ctx3.final_message, "Service port is 8443 [1].");
}
