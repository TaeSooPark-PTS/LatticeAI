//! Empirical CLI harness runner for benchmarking `lattice_agent::agentloop::Runtime`
//! in `standard` (uncalibrated baseline) vs `adaptive` (Universal Adaptive Harness) modes.

use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::Instant;

use lattice_agent::agentloop::{LoopDeps, RunRequest, Runtime};
use lattice_agent::kernel::probe::ProbeConfig;
use lattice_agent::kernel::proposals::{JsonProposalStore, NewReviewItem, ProposalStore};
use lattice_agent::kernel::state::{AgentRunContext, AgentState};
use lattice_agent::profile::STANDARD;
use lattice_agent::sandbox::Workspace;
use lattice_agent::tools::{NativeTools, ToolConfig};
use lattice_agent::worker::WorkerClient;
use lattice_host::gateway::agent_bind::product_policy_table;
use serde_json::{json, Value};

#[derive(Debug)]
struct CountingProposalStore {
    inner: JsonProposalStore,
    staged: Mutex<Vec<Value>>,
}

impl CountingProposalStore {
    fn new(data_dir: PathBuf) -> Self {
        Self {
            inner: JsonProposalStore::new(data_dir),
            staged: Mutex::new(Vec::new()),
        }
    }

    fn staged_items(&self) -> Vec<Value> {
        self.staged
            .lock()
            .map(|guard| guard.clone())
            .unwrap_or_default()
    }
}

impl ProposalStore for CountingProposalStore {
    fn create(&self, item: &NewReviewItem) -> Result<Value, String> {
        let created = self.inner.create(item)?;
        if let Ok(mut guard) = self.staged.lock() {
            guard.push(created.clone());
        }
        Ok(created)
    }
}

struct CliArgs {
    worker_url: String,
    workspace: PathBuf,
    brain_dir: PathBuf,
    profile_cache: PathBuf,
    model: String,
    mode: String,
    message: String,
    max_steps: u32,
    language: String,
    permission_mode: String,
    skip_plan: bool,
}

fn parse_args() -> Result<CliArgs, String> {
    let mut worker_url = None;
    let mut workspace = None;
    let mut brain_dir = None;
    let mut profile_cache = None;
    let mut model = Some("local-small".to_string());
    let mut mode = Some("adaptive".to_string());
    let mut message = None;
    let mut max_steps: u32 = 8;
    let mut language = "English".to_string();
    let mut permission_mode = "bypass".to_string();
    let mut skip_plan = false;

    let mut iter = std::env::args().skip(1);
    while let Some(flag) = iter.next() {
        let next_val =
            |name: &str, it: &mut dyn Iterator<Item = String>| -> Result<String, String> {
                it.next().ok_or_else(|| format!("missing value for {name}"))
            };
        match flag.as_str() {
            "--worker-url" => worker_url = Some(next_val("--worker-url", &mut iter)?),
            "--workspace" => workspace = Some(PathBuf::from(next_val("--workspace", &mut iter)?)),
            "--brain-dir" => brain_dir = Some(PathBuf::from(next_val("--brain-dir", &mut iter)?)),
            "--profile-cache" => {
                profile_cache = Some(PathBuf::from(next_val("--profile-cache", &mut iter)?))
            }
            "--model" => model = Some(next_val("--model", &mut iter)?),
            "--mode" => mode = Some(next_val("--mode", &mut iter)?),
            "--message" => message = Some(next_val("--message", &mut iter)?),
            "--max-steps" => {
                let raw = next_val("--max-steps", &mut iter)?;
                max_steps = raw
                    .parse::<u32>()
                    .map_err(|e| format!("invalid --max-steps '{raw}': {e}"))?;
            }
            "--language" => language = next_val("--language", &mut iter)?,
            "--permission-mode" => permission_mode = next_val("--permission-mode", &mut iter)?,
            "--skip-plan" => skip_plan = true,
            other => return Err(format!("unknown flag: {other}")),
        }
    }

    let ws = workspace.ok_or("--workspace is required")?;
    Ok(CliArgs {
        worker_url: worker_url.ok_or("--worker-url is required")?,
        brain_dir: brain_dir.unwrap_or_else(|| ws.join(".brain")),
        profile_cache: profile_cache.unwrap_or_else(|| ws.join(".probe_cache")),
        workspace: ws,
        model: model.unwrap_or_else(|| "local-small".to_string()),
        mode: mode.unwrap_or_else(|| "adaptive".to_string()),
        message: message.ok_or("--message is required")?,
        max_steps,
        language,
        permission_mode,
        skip_plan,
    })
}

fn build_tool_calls(transcript: &[Value]) -> Vec<Value> {
    transcript
        .iter()
        .filter(|step| step.get("state").and_then(Value::as_str) == Some("EXECUTING"))
        .filter_map(|step| {
            let action = step.get("action").and_then(Value::as_str)?;
            if action == "final" {
                return None;
            }
            Some(json!({
                "action": action,
                "args": step.get("args").cloned().unwrap_or_else(|| json!({})),
                "result": step.get("result").cloned().unwrap_or(Value::Null),
                "error": step.get("error").cloned().unwrap_or(Value::Null),
            }))
        })
        .collect()
}

fn collect_repairs(trace_events: &[Value]) -> Vec<String> {
    let mut out = Vec::new();
    for event in trace_events {
        if event.get("kind").and_then(Value::as_str) == Some("repair") {
            if let Some(items) = event.get("repairs").and_then(Value::as_array) {
                for item in items {
                    if let Some(name) = item.as_str() {
                        out.push(name.to_string());
                    }
                }
            }
        }
    }
    out
}

#[tokio::main]
async fn main() {
    let args = match parse_args() {
        Ok(parsed) => parsed,
        Err(err) => {
            eprintln!("harness_bench error: {err}");
            std::process::exit(2);
        }
    };

    if let Err(err) = std::fs::create_dir_all(&args.workspace) {
        eprintln!("failed to create workspace dir: {err}");
        std::process::exit(1);
    }
    let workspace = match Workspace::new(&args.workspace) {
        Ok(ws) => ws,
        Err(err) => {
            eprintln!("failed to initialize workspace: {err}");
            std::process::exit(1);
        }
    };

    let worker = WorkerClient::new(&args.worker_url);
    let policies = product_policy_table();
    let tool_names: Vec<String> = policies.tools.keys().cloned().collect();
    let native = Arc::new(NativeTools::new(
        workspace.clone(),
        ToolConfig {
            brain_dir: args.brain_dir.clone(),
            role: "owner".to_string(),
            blocked_write_prefixes: policies.blocked_write_prefixes.clone(),
        },
        worker.clone(),
    ));
    let proposals = Arc::new(CountingProposalStore::new(args.brain_dir.join("proposals")));

    let mut deps = LoopDeps::new(worker, workspace);
    deps.native = native;
    deps.policies = policies;
    deps.tool_names = tool_names;
    deps.proposals = proposals.clone();

    if args.mode == "standard" {
        deps.agent_profile = Some(STANDARD);
        deps.probe = None;
    } else {
        deps.agent_profile = None;
        deps.probe = Some(ProbeConfig::new(args.profile_cache.clone()));
    }

    let mut req = RunRequest {
        message: args.message.clone(),
        user_email: Some("bench@lattice.local".to_string()),
        temperature: 0.0,
        max_steps: args.max_steps,
        max_retry: 0,
        planning_model: Some(args.model.clone()),
        executing_model: Some(args.model.clone()),
        reviewing_model: Some(args.model.clone()),
        permission_mode: Some(args.permission_mode.clone()),
        language_hint: args.language.clone(),
        ..RunRequest::default()
    };

    let mut ctx = AgentRunContext::new();
    ctx.executing_model = req.executing_model.clone();
    ctx.reviewing_model = req.reviewing_model.clone();
    ctx.permission_mode = req.permission_mode.clone();

    let started = Instant::now();
    let mut runtime = Runtime::new(deps);

    if args.skip_plan {
        ctx.state = AgentState::Executing;
        ctx.state_history.push(ctx.state.as_str().to_string());
    } else {
        ctx.state = AgentState::Planning;
        ctx.state_history.push(ctx.state.as_str().to_string());
        if let Err(err) = runtime.plan(&mut ctx, &req).await {
            eprintln!("harness_bench plan failed: {err}");
            std::process::exit(1);
        }
        runtime.approve(&mut ctx, &req, false);
    }

    // Clear planning_model override once initialized if unused, keep req intact.
    req.max_steps = args.max_steps;
    if let Err(err) = runtime.run_to_completion(&mut ctx, &req).await {
        eprintln!("harness_bench run_to_completion failed: {err}");
        std::process::exit(1);
    }

    let elapsed_ms = started.elapsed().as_millis() as u64;
    let probe_detail = ctx
        .trace
        .events
        .iter()
        .rev()
        .find(|ev| {
            ev.get("kind").and_then(Value::as_str) == Some("decision")
                && ev.get("decision").and_then(Value::as_str) == Some("profile")
        })
        .and_then(|ev| ev.get("profile"))
        .cloned()
        .unwrap_or(Value::Null);

    let output = json!({
        "status": if ctx.state == AgentState::Done { "ok" } else { "failed" },
        "final_state": ctx.state.as_str(),
        "needs_review": ctx.state == AgentState::NeedsReview,
        "answer": ctx.final_message,
        "profile": runtime.profile(req.executing_model.as_deref()).name,
        "probe_detail": probe_detail,
        "tool_calls": build_tool_calls(&ctx.transcript),
        "repairs": collect_repairs(&ctx.trace.events),
        "transcript": ctx.transcript,
        "loop_trace": ctx.trace.summary(),
        "trace_events": ctx.trace.events,
        "proposals_staged": proposals.staged_items(),
        "elapsed_ms": elapsed_ms,
    });
    println!("{}", serde_json::to_string(&output).unwrap_or_default());
}
