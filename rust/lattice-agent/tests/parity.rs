//! Python↔Rust safety-kernel parity, over the committed decision grid.
//!
//! Same tools, same real policies, same argument variants, same goldens the
//! Python kernel produced. Comparison is **exact** — a `null` that became
//! `false`, a message whose punctuation drifted, or a breaker that stopped
//! firing all fail the same way.
//!
//! ## The goldens are frozen (v11.8.0)
//!
//! There is no generator any more: the Python kernel these were captured from
//! is gone, and `rust/fixtures/agent/FROZEN.md` records what each file is and
//! what was removed. Two things changed with the freeze, both recorded there:
//!
//! * **`decisions__trusted.json` and `decisions__bypass.json` are deleted.**
//!   Between them they were 1,404 rows over 11 distinct verdicts, because the
//!   grid is a cross product of tools and argument variants and the same
//!   verdict repeats for every row in a class. The mode gating they proved now
//!   has in-crate unit tests that name one case per verdict — see
//!   `permission::tests::{trusted,bypass}_reproduces_every_verdict_class_of_the_retired_grid`
//!   and the `effective_auto_approve` tests in `mode`.
//! * **`calls.json` and `decisions__strict.json` are trimmed to 171 rows**
//!   from 702 — one row per distinct
//!   `(tool, breaker, classification, policy, auto_approve, block_reason,
//!   stage_proposal)` class. Rows were deleted, never rewritten: the surviving
//!   171 are byte-identical to the ones the generator emitted.
//!
//! `strict` is the mode kept whole because it is the one that gates
//! everything: its grid is where a missing refusal shows up.

mod common;

use std::collections::BTreeMap;

use common::{assert_no_failures, cases, manifest, read_golden};
use lattice_agent::breaker::is_circuit_breaker;
use lattice_agent::command::{
    validate, ALLOWED_COMMANDS, ALLOWED_GIT_SUBCOMMANDS, BLOCKED_COMMANDS, BLOCKED_FIND_FLAGS,
    BLOCKED_RG_FLAGS, SHELL_OPERATORS,
};
use lattice_agent::exec::{sandbox_env, SAFE_EXECUTABLE_PATH};
use lattice_agent::governor::{
    classify_tool_call, MUTATING_TOOL_INVENTORY, PROPOSAL_CAPABLE_TOOLS,
};
use lattice_agent::mode::{
    effective_auto_approve, mode_contract, normalize_mode, normalize_value, plan_requires_approval,
    should_stage_proposal, COMPUTER_CONTROL_TOOLS, COMPUTER_OBSERVATION_TOOLS,
    HARD_BLOCK_SANDBOXES, KNOWLEDGE_READ_TOOLS, WORKSPACE_WRITE_TOOLS,
};
use lattice_agent::permission::{block_reason_for_tool, non_auto_plan_steps, PlanStep};
use lattice_agent::policy::{PolicyTable, ToolPolicy};
use lattice_agent::pyshlex;
use lattice_agent::sandbox::{MAX_COMMAND_OUTPUT, MAX_COMMAND_SECONDS, MAX_FILE_BYTES};
use serde_json::{json, Map, Value};

/// The modes whose decision grid is still committed.
///
/// The manifest still lists all three, because it records what the Python
/// generator ran; `the_committed_grids_are_a_subset_of_the_manifests_modes`
/// keeps the two from drifting apart in the direction that would matter.
const GOLDEN_MODES: &[&str] = &["strict"];

fn args_of(variant: &str) -> Map<String, Value> {
    manifest()["arg_variants"][variant]
        .as_object()
        .unwrap_or_else(|| panic!("unknown arg variant {variant}"))
        .clone()
}

fn policy_of(policies: &Value, key: &str) -> ToolPolicy {
    let raw = if key == "@default" {
        &policies["default"]
    } else if key.contains('|') {
        &policies["overrides"][key]
    } else {
        &policies["tools"][key]
    };
    serde_json::from_value(raw.clone()).unwrap_or_else(|err| panic!("policy {key}: {err}"))
}

/// `(tool, variant)` → the real policy, taken from `calls.json`.
fn policy_index() -> BTreeMap<(String, String), ToolPolicy> {
    let policies = read_golden("policies.json");
    let calls = read_golden("calls.json");
    cases(&calls, "cases")
        .iter()
        .map(|case| {
            let tool = case["tool"].as_str().expect("tool").to_string();
            let variant = case["variant"].as_str().expect("variant").to_string();
            let policy = policy_of(&policies, case["policy"].as_str().expect("policy key"));
            ((tool, variant), policy)
        })
        .collect()
}

fn existing_paths() -> Vec<String> {
    manifest()["existing_paths"]
        .as_array()
        .expect("existing_paths")
        .iter()
        .map(|value| value.as_str().expect("path").to_string())
        .collect()
}

fn policy_table() -> PolicyTable {
    serde_json::from_value(read_golden("policies.json")).expect("policy table")
}

#[test]
fn the_constants_match_the_python_tables() {
    let constants = &manifest()["constants"];
    assert_eq!(constants["max_file_bytes"], json!(MAX_FILE_BYTES));
    assert_eq!(constants["max_command_seconds"], json!(MAX_COMMAND_SECONDS));
    assert_eq!(constants["max_command_output"], json!(MAX_COMMAND_OUTPUT));
    assert_eq!(
        constants["safe_executable_path"],
        json!(SAFE_EXECUTABLE_PATH)
    );
    for (key, ours) in [
        ("allowed_commands", ALLOWED_COMMANDS.to_vec()),
        ("blocked_commands", BLOCKED_COMMANDS.to_vec()),
        ("allowed_git_subcommands", ALLOWED_GIT_SUBCOMMANDS.to_vec()),
        ("blocked_find_flags", BLOCKED_FIND_FLAGS.to_vec()),
        ("blocked_rg_flags", BLOCKED_RG_FLAGS.to_vec()),
        ("shell_operators", SHELL_OPERATORS.to_vec()),
        ("hard_block_sandboxes", HARD_BLOCK_SANDBOXES.to_vec()),
        ("knowledge_read_tools", KNOWLEDGE_READ_TOOLS.to_vec()),
        ("workspace_write_tools", WORKSPACE_WRITE_TOOLS.to_vec()),
        (
            "computer_observation_tools",
            COMPUTER_OBSERVATION_TOOLS.to_vec(),
        ),
        ("computer_control_tools", COMPUTER_CONTROL_TOOLS.to_vec()),
        ("proposal_capable_tools", PROPOSAL_CAPABLE_TOOLS.to_vec()),
    ] {
        assert_eq!(constants[key], json!(ours), "{key} drifted");
    }
    let inventory: BTreeMap<String, String> = MUTATING_TOOL_INVENTORY
        .iter()
        .map(|(name, category)| ((*name).to_string(), (*category).to_string()))
        .collect();
    assert_eq!(
        constants["mutating_tool_inventory"],
        json!(inventory),
        "the mutating-tool inventory is the coverage proof — it may not drift"
    );
    // Every allow-listed binary is looked up on the fixed PATH and nowhere else.
    assert_eq!(
        manifest()["which_paths"],
        json!([SAFE_EXECUTABLE_PATH]),
        "the validator searched some other PATH"
    );
}

#[test]
fn every_call_matches_its_breaker_and_classification_golden() {
    let policies = read_golden("policies.json");
    let golden = read_golden("calls.json");
    let existing = existing_paths();
    let exists = |path: &str| existing.iter().any(|known| known == path);

    let mut failures = Vec::new();
    let rows = cases(&golden, "cases");
    for case in &rows {
        let tool = case["tool"].as_str().expect("tool");
        let variant = case["variant"].as_str().expect("variant");
        let args = args_of(variant);
        let policy = policy_of(&policies, case["policy"].as_str().expect("policy"));

        let breaker = json!(is_circuit_breaker(tool, &policy, &args));
        if breaker != case["circuit_breaker"] {
            failures.push(format!(
                "  {tool}/{variant} breaker: python={} rust={breaker}",
                case["circuit_breaker"]
            ));
        }
        let classification =
            serde_json::to_value(classify_tool_call(tool, &args, &policy, &exists))
                .expect("classification");
        if classification != case["classification"] {
            failures.push(format!(
                "  {tool}/{variant} classification: python={} rust={classification}",
                case["classification"]
            ));
        }
    }
    assert_no_failures(rows.len(), failures, "calls");
    assert!(
        rows.len() >= 170,
        "the decision grid is the coverage — one row per equivalence class, \
         and rows may only be deleted when a class is genuinely gone"
    );
}

#[test]
fn every_mode_matches_its_decision_golden() {
    let index = policy_index();
    let table = policy_table();
    let mut checked = 0usize;
    let mut failures = Vec::new();

    for mode_name in GOLDEN_MODES {
        let mode_name = *mode_name;
        let mode = normalize_mode(mode_name);
        let golden = read_golden(&format!("decisions__{mode_name}.json"));
        assert_eq!(golden["mode"], json!(mode_name));

        for case in cases(&golden, "cases") {
            let tool = case["tool"].as_str().expect("tool");
            let variant = case["variant"].as_str().expect("variant");
            let args = args_of(variant);
            let policy = index
                .get(&(tool.to_string(), variant.to_string()))
                .unwrap_or_else(|| panic!("no policy for {tool}/{variant}"));

            let auto = json!(effective_auto_approve(mode, tool, policy, None));
            if auto != case["auto_approve"] {
                failures.push(format!(
                    "  {mode_name}/{tool}/{variant} auto_approve: python={} rust={auto}",
                    case["auto_approve"]
                ));
            }
            let reason = json!(block_reason_for_tool(
                mode, tool, policy, &args, false, false
            ));
            if reason != case["block_reason"] {
                failures.push(format!(
                    "  {mode_name}/{tool}/{variant} block_reason: python={} rust={reason}",
                    case["block_reason"]
                ));
            }
            checked += 1;
        }

        for case in cases(&golden, "change_class_cases") {
            let tool = case["tool"].as_str().expect("tool");
            let change_class = case["change_class"].as_str();
            let auto = json!(effective_auto_approve(
                mode,
                tool,
                table.get(tool),
                change_class
            ));
            if auto != case["auto_approve"] {
                failures.push(format!(
                    "  {mode_name}/{tool}/{change_class:?} auto_approve: python={} rust={auto}",
                    case["auto_approve"]
                ));
            }
            checked += 1;
        }
    }
    assert_no_failures(checked, failures, "decisions");
}

#[test]
fn proposal_staging_matches_the_golden_for_every_mode() {
    let index = policy_index();
    let existing = existing_paths();
    let exists = |path: &str| existing.iter().any(|known| known == path);
    let mut checked = 0usize;
    let mut failures = Vec::new();

    for mode_name in GOLDEN_MODES {
        let mode_name = *mode_name;
        let mode = normalize_mode(mode_name);
        for case in cases(
            &read_golden(&format!("decisions__{mode_name}.json")),
            "cases",
        ) {
            let tool = case["tool"].as_str().expect("tool");
            let variant = case["variant"].as_str().expect("variant");
            let policy = &index[&(tool.to_string(), variant.to_string())];
            let classification = classify_tool_call(tool, &args_of(variant), policy, &exists);
            let staged = json!(should_stage_proposal(
                mode,
                classification.proposal_required
            ));
            if staged != case["stage_proposal"] {
                failures.push(format!(
                    "  {mode_name}/{tool}/{variant} stage_proposal: python={} rust={staged}",
                    case["stage_proposal"]
                ));
            }
            checked += 1;
        }
    }
    assert_no_failures(checked, failures, "proposal staging");
}

#[test]
fn every_plan_matches_its_approval_golden() {
    let table = policy_table();
    let plans = manifest()["plans"].as_array().expect("plans").clone();
    let mut checked = 0usize;
    let mut failures = Vec::new();

    for mode_name in GOLDEN_MODES {
        let mode_name = *mode_name;
        let mode = normalize_mode(mode_name);
        let golden = read_golden(&format!("decisions__{mode_name}.json"));
        for case in cases(&golden, "plan_cases") {
            let key = case["key"].as_str().expect("key");
            let spec = plans
                .iter()
                .find(|plan| plan["key"] == json!(key))
                .unwrap_or_else(|| panic!("no plan named {key}"));
            let steps: Vec<PlanStep> = spec["steps"]
                .as_array()
                .expect("steps")
                .iter()
                .map(|step| PlanStep {
                    action: step["action"].as_str().unwrap_or_default().to_string(),
                })
                .collect();
            let governed: Vec<String> = spec["governed"]
                .as_array()
                .expect("governed")
                .iter()
                .map(|tool| tool.as_str().expect("tool").to_string())
                .collect();

            let non_auto = non_auto_plan_steps(mode, &steps, &table, &governed);
            if json!(non_auto) != case["non_auto_steps"] {
                failures.push(format!(
                    "  {mode_name}/{key} non_auto_steps: python={} rust={}",
                    case["non_auto_steps"],
                    json!(non_auto)
                ));
            }
            let requires = json!(plan_requires_approval(
                mode,
                &non_auto,
                spec["plan_flag"].as_bool().expect("plan_flag")
            ));
            if requires != case["requires_approval"] {
                failures.push(format!(
                    "  {mode_name}/{key} requires_approval: python={} rust={requires}",
                    case["requires_approval"]
                ));
            }
            checked += 1;
        }
    }
    assert_no_failures(checked, failures, "plans");
}

#[test]
fn the_committed_grids_are_a_subset_of_the_manifests_modes() {
    // The manifest records what the Python generator ran; `GOLDEN_MODES` records
    // which of those grids is still committed. A mode may leave the second list
    // (its coverage moves in-crate); a mode that is in neither has quietly lost
    // both, and a `GOLDEN_MODES` entry with no manifest mode would be a typo.
    let modes: Vec<&str> = manifest()["modes"]
        .as_array()
        .expect("modes")
        .iter()
        .map(|value| value.as_str().expect("mode"))
        .collect();
    for name in GOLDEN_MODES {
        assert!(modes.contains(name), "{name} is not a mode the kernel has");
        assert!(
            common::golden_path(&format!("decisions__{name}.json")).exists(),
            "{name} is listed as committed but its grid is missing"
        );
    }
    for name in &modes {
        if GOLDEN_MODES.contains(name) {
            continue;
        }
        // Retired grids: the mode's gating lives in `lattice_agent::permission`
        // and `lattice_agent::mode` unit tests now, one case per verdict class.
        assert!(
            !common::golden_path(&format!("decisions__{name}.json")).exists(),
            "decisions__{name}.json is back — add {name} to GOLDEN_MODES"
        );
    }
}

#[test]
fn mode_normalisation_matches_the_alias_table() {
    let golden = read_golden("normalize.json");
    let rows = cases(&golden, "cases");
    let mut failures = Vec::new();
    for case in &rows {
        let got = normalize_value(&case["input"]);
        if json!(got.as_str()) != case["mode"] {
            failures.push(format!(
                "  {}: python={} rust={}",
                case["input"],
                case["mode"],
                got.as_str()
            ));
        }
    }
    assert_no_failures(rows.len(), failures, "mode aliases");
}

#[test]
fn the_mode_contract_is_byte_identical() {
    let golden = read_golden("contract.json");
    assert_eq!(
        golden["default_mode"],
        json!(lattice_agent::mode::DEFAULT_MODE.as_str())
    );
    for (name, expected) in golden["contracts"].as_object().expect("contracts") {
        assert_eq!(&mode_contract(normalize_mode(name)), expected, "{name}");
    }
}

#[test]
fn the_splitter_matches_python_shlex() {
    let golden = read_golden("shlex.json");
    let rows = cases(&golden, "cases");
    let mut failures = Vec::new();
    for case in &rows {
        let input = case["input"].as_str().expect("input");
        match (pyshlex::split(input), case.get("error")) {
            (Ok(tokens), None) => {
                if json!(tokens) != case["tokens"] {
                    failures.push(format!(
                        "  {input:?}: python={} rust={}",
                        case["tokens"],
                        json!(tokens)
                    ));
                }
            }
            (Err(err), Some(expected)) => {
                if json!(err.message()) != *expected {
                    failures.push(format!("  {input:?}: python={expected} rust={err}"));
                }
            }
            (Ok(tokens), Some(expected)) => failures.push(format!(
                "  {input:?}: python errored ({expected}), rust split into {tokens:?}"
            )),
            (Err(err), None) => failures.push(format!(
                "  {input:?}: python split it, rust errored ({err})"
            )),
        }
    }
    assert_no_failures(rows.len(), failures, "shlex splits");
}

#[test]
fn every_command_matches_its_validation_golden() {
    let dir = tempfile::tempdir().expect("tempdir");
    let workspace = common::build_tree(dir.path());
    let golden = read_golden("commands.json");

    // The environment the validator hands the child, with the fixture's own
    // LANG/LC_ALL — the two variables Python inherits rather than invents.
    let env: BTreeMap<String, String> = sandbox_env(
        workspace.root(),
        manifest()["pinned_env"]["LANG"]
            .as_str()
            .map(str::to_string),
        manifest()["pinned_env"]["LC_ALL"]
            .as_str()
            .map(str::to_string),
    )
    .into_iter()
    .map(|(key, value)| {
        (
            key,
            value.replace(&workspace.root().display().to_string(), "<AGENT_ROOT>"),
        )
    })
    .collect();
    assert_eq!(
        json!(env),
        golden["spawn_env"],
        "the child environment drifted"
    );

    let rows = cases(&golden, "cases");
    let mut failures = Vec::new();
    for case in &rows {
        let key = case["key"].as_str().expect("key");
        let command = case["command"].as_str().expect("command");
        let cwd = case["cwd"].as_str();
        let got = match validate(&workspace, command, cwd) {
            Ok(validated) => json!({
                "outcome": "spawn",
                "executable": validated.executable,
                "args": validated.args,
                "workdir": workspace.relative(&validated.workdir),
            }),
            Err(err) => json!({
                "outcome": "error",
                "error": {"kind": err.kind.as_str(), "message": err.message},
            }),
        };
        let expected = json!({
            "outcome": case["outcome"],
            "executable": case.get("executable").cloned().unwrap_or(Value::Null),
            "args": case.get("args").cloned().unwrap_or(Value::Null),
            "workdir": case.get("workdir").cloned().unwrap_or(Value::Null),
            "error": case.get("error").cloned().unwrap_or(Value::Null),
        });
        let normalised = json!({
            "outcome": got["outcome"],
            "executable": got.get("executable").cloned().unwrap_or(Value::Null),
            "args": got.get("args").cloned().unwrap_or(Value::Null),
            "workdir": got.get("workdir").cloned().unwrap_or(Value::Null),
            "error": got.get("error").cloned().unwrap_or(Value::Null),
        });
        if normalised != expected {
            failures.push(format!(
                "  {key} ({command:?}): python={expected} rust={normalised}"
            ));
        }
    }
    assert_no_failures(rows.len(), failures, "command validations");
}

#[test]
fn every_path_matches_its_sandbox_golden() {
    let dir = tempfile::tempdir().expect("tempdir");
    let workspace = common::build_tree(dir.path());
    let golden = read_golden("paths.json");
    let rows = cases(&golden, "cases");
    let mut failures = Vec::new();

    for case in &rows {
        let input = common::with_root(case["input"].as_str().expect("input"), &workspace);
        let got = match workspace.resolve(&input) {
            Ok(resolved) => json!({"outcome": "ok", "relative": workspace.relative(&resolved)}),
            Err(err) => json!({
                "outcome": "error",
                "error": {"kind": err.kind.as_str(), "message": err.message},
            }),
        };
        let expected = json!({
            "outcome": case["outcome"],
            "relative": case.get("relative").cloned().unwrap_or(Value::Null),
            "error": case.get("error").cloned().unwrap_or(Value::Null),
        });
        let normalised = json!({
            "outcome": got["outcome"],
            "relative": got.get("relative").cloned().unwrap_or(Value::Null),
            "error": got.get("error").cloned().unwrap_or(Value::Null),
        });
        if normalised != expected {
            failures.push(format!(
                "  {}: python={expected} rust={normalised}",
                case["key"]
            ));
        }
    }
    assert_no_failures(rows.len(), failures, "sandbox paths");
}
