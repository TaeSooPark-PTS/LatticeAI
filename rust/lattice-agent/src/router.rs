//! `/rust/agent/*` — the kernel as three routes.
//!
//! * `POST /rust/agent/preflight` — pure decision. A mode plus a list of tool
//!   calls (and optionally a plan) in, one verdict per call out. Nothing is
//!   executed, nothing is written; this route is safe to call for a plan the
//!   user has not approved, which is the point of a preflight.
//! * `POST /rust/agent/exec` — validate a command string, and run it **only**
//!   when the kernel permits it *and* it is one of the read-only executables
//!   [`crate::exec::NATIVE_EXECUTABLES`] names. Everything else comes back as a
//!   verdict with `executed: false`.
//! * `GET /rust/agent/contract` — the serialised mode contract, byte-compatible
//!   with Python's `mode_contract`.
//!
//! Policies are **input**. The registry lives in Python; a caller passes the
//! real policy per call (or a table for the request), and the only fallback is
//! the deliberately conservative default.

use std::sync::Arc;
use std::time::Duration;

use axum::extract::{Query, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use serde::Deserialize;
use serde_json::{json, Map, Value};

use crate::breaker::is_circuit_breaker;
use crate::command::validate;
use crate::exec::{execute, is_natively_executable};
use crate::governor::classify_tool_call;
use crate::mode::{
    effective_auto_approve, mode_contract, normalize_mode, plan_requires_approval,
    should_stage_proposal, PermissionMode, ALL_MODES,
};
use crate::permission::{block_reason_for_tool, non_auto_plan_steps, PlanStep};
use crate::policy::{PolicyTable, ToolPolicy};
use crate::sandbox::{Workspace, MAX_COMMAND_SECONDS};

/// What the host holds for these routes: one workspace, nothing else.
#[derive(Debug)]
pub struct AgentState {
    workspace: Workspace,
}

impl AgentState {
    pub fn new(workspace: Workspace) -> Self {
        Self { workspace }
    }

    pub fn workspace(&self) -> &Workspace {
        &self.workspace
    }
}

/// Mount the kernel routes. Returns a `Router<()>` so any host can merge it.
pub fn router(workspace: Workspace) -> Router {
    Router::new()
        .route("/rust/agent/preflight", post(preflight))
        .route("/rust/agent/exec", post(exec_command))
        .route("/rust/agent/contract", get(contract))
        .with_state(Arc::new(AgentState::new(workspace)))
}

// ── requests ─────────────────────────────────────────────────────────────────
#[derive(Debug, Default, Deserialize)]
struct PreflightRequest {
    #[serde(default)]
    mode: Option<String>,
    #[serde(default)]
    policies: Option<PolicyTable>,
    #[serde(default)]
    calls: Vec<CallRequest>,
    #[serde(default)]
    plan: Option<PlanRequest>,
}

#[derive(Debug, Deserialize)]
struct CallRequest {
    tool: String,
    #[serde(default)]
    args: Map<String, Value>,
    /// The real policy for this call. Absent means "look it up in `policies`",
    /// and an absent table means the conservative default.
    #[serde(default)]
    policy: Option<ToolPolicy>,
    #[serde(default)]
    change_class: Option<String>,
    #[serde(default)]
    approved_by_human: bool,
    #[serde(default)]
    governor_allows_additive: bool,
    /// Overrides the workspace lookup that decides additive vs mutation.
    #[serde(default)]
    target_exists: Option<bool>,
}

#[derive(Debug, Default, Deserialize)]
struct PlanRequest {
    #[serde(default)]
    steps: Vec<PlanStepRequest>,
    #[serde(default)]
    requires_approval: bool,
    #[serde(default)]
    governed_tools: Vec<String>,
}

#[derive(Debug, Deserialize)]
struct PlanStepRequest {
    #[serde(default)]
    action: String,
}

#[derive(Debug, Default, Deserialize)]
struct ExecRequest {
    #[serde(default)]
    mode: Option<String>,
    #[serde(default)]
    command: String,
    #[serde(default)]
    cwd: Option<String>,
    #[serde(default)]
    timeout: Option<u64>,
    /// The `run_command` policy from the real registry, when the caller has it.
    #[serde(default)]
    policy: Option<ToolPolicy>,
}

#[derive(Debug, Default, Deserialize)]
struct ContractQuery {
    #[serde(default)]
    mode: Option<String>,
}

/// The fallback used when a caller does not send `run_command`'s real policy:
/// a workspace shell execution, gated everywhere but `bypass`. It is the
/// conservative shape, not a copy of the registry — callers that have the
/// registry should send it.
fn run_command_fallback_policy() -> ToolPolicy {
    ToolPolicy {
        risk: "exec".into(),
        shell: true,
        sandbox: "workspace".into(),
        rollback: "none".into(),
        ..ToolPolicy::default()
    }
}

fn bad_request(detail: impl Into<String>) -> Response {
    (
        StatusCode::BAD_REQUEST,
        Json(json!({"error": "invalid_request", "detail": detail.into()})),
    )
        .into_response()
}

// ── handlers ─────────────────────────────────────────────────────────────────
async fn preflight(State(state): State<Arc<AgentState>>, Json(body): Json<Value>) -> Response {
    let request: PreflightRequest = match serde_json::from_value(body) {
        Ok(request) => request,
        Err(err) => return bad_request(format!("could not read the preflight request: {err}")),
    };
    let mode = normalize_mode(request.mode.as_deref().unwrap_or(""));
    let table = request.policies.unwrap_or_default();

    let calls: Vec<Value> = request
        .calls
        .iter()
        .map(|call| decide(state.workspace(), mode, call, &table))
        .collect();

    let mut payload = json!({
        "mode": mode.as_str(),
        "contract": mode_contract(mode),
        "calls": calls,
    });
    if let Some(plan) = request.plan {
        let steps: Vec<PlanStep> = plan
            .steps
            .iter()
            .map(|step| PlanStep {
                action: step.action.clone(),
            })
            .collect();
        let non_auto = non_auto_plan_steps(mode, &steps, &table, &plan.governed_tools);
        payload["plan"] = json!({
            "non_auto_steps": non_auto,
            "requires_approval": plan_requires_approval(mode, &non_auto, plan.requires_approval),
        });
    }
    Json(payload).into_response()
}

/// One call's verdict. The order mirrors the kernel: breaker, then class, then
/// autonomy — and the breaker is reported even when the call is blocked for
/// another reason, because "which rule stopped this" is the useful answer.
fn decide(
    workspace: &Workspace,
    mode: PermissionMode,
    call: &CallRequest,
    table: &PolicyTable,
) -> Value {
    let policy = call
        .policy
        .clone()
        .unwrap_or_else(|| table.get(&call.tool).clone());
    let exists = |path: &str| match call.target_exists {
        Some(known) => known,
        None => workspace
            .resolve(path)
            .map(|resolved| resolved.exists())
            .unwrap_or(false),
    };
    let classification = classify_tool_call(&call.tool, &call.args, &policy, &exists);
    json!({
        "tool": call.tool,
        "policy": policy,
        "circuit_breaker": is_circuit_breaker(&call.tool, &policy, &call.args),
        "auto_approve": effective_auto_approve(
            mode, &call.tool, &policy, call.change_class.as_deref(),
        ),
        "block_reason": block_reason_for_tool(
            mode,
            &call.tool,
            &policy,
            &call.args,
            call.approved_by_human,
            call.governor_allows_additive,
        ),
        "stage_proposal": should_stage_proposal(mode, classification.proposal_required),
        "classification": classification,
    })
}

async fn exec_command(State(state): State<Arc<AgentState>>, Json(body): Json<Value>) -> Response {
    let request: ExecRequest = match serde_json::from_value(body) {
        Ok(request) => request,
        Err(err) => return bad_request(format!("could not read the exec request: {err}")),
    };
    let mode = normalize_mode(request.mode.as_deref().unwrap_or(""));
    let policy = request.policy.unwrap_or_else(run_command_fallback_policy);
    let mut args = Map::new();
    args.insert("command".into(), Value::String(request.command.clone()));
    let block_reason = block_reason_for_tool(mode, "run_command", &policy, &args, false, false);

    let mut payload = json!({
        "mode": mode.as_str(),
        "command": request.command,
        "permitted": block_reason.is_none(),
        "block_reason": block_reason,
        "executed": false,
    });

    let validated = match validate(state.workspace(), &request.command, request.cwd.as_deref()) {
        Ok(validated) => {
            payload["validation"] = json!({
                "ok": true,
                "executable": validated.executable,
                "args": validated.args,
                "cwd": state.workspace().relative(&validated.workdir),
                "natively_executable": is_natively_executable(&validated.executable),
            });
            validated
        }
        Err(err) => {
            payload["validation"] = json!({
                "ok": false,
                "error": {"kind": err.kind.as_str(), "message": err.message},
            });
            return Json(payload).into_response();
        }
    };

    if block_reason.is_some() {
        return Json(payload).into_response();
    }
    if !is_natively_executable(&validated.executable) {
        // Validated, permitted, and still not run: mutation and build/deploy
        // belong to the worker. Saying so is the honest answer.
        payload["detail"] = json!(format!(
            "'{}' is validated but never executed natively",
            validated.executable
        ));
        return Json(payload).into_response();
    }

    let seconds = request
        .timeout
        .unwrap_or(MAX_COMMAND_SECONDS)
        .clamp(1, MAX_COMMAND_SECONDS);
    match execute(state.workspace(), &validated, Duration::from_secs(seconds)).await {
        Ok(result) => {
            payload["executed"] = json!(true);
            payload["exit_code"] = json!(result.returncode);
            payload["truncated"] = json!({
                "stdout": result.stdout_truncated,
                "stderr": result.stderr_truncated,
            });
            payload["result"] = json!({
                "command": result.command,
                "cwd": result.cwd,
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            });
            Json(payload).into_response()
        }
        Err(err) => {
            payload["error"] = json!({"kind": err.kind.as_str(), "message": err.message});
            (StatusCode::INTERNAL_SERVER_ERROR, Json(payload)).into_response()
        }
    }
}

async fn contract(Query(query): Query<ContractQuery>) -> Response {
    let requested = query.mode.unwrap_or_default();
    let mode = normalize_mode(&requested);
    Json(json!({
        "mode": mode.as_str(),
        "requested": requested,
        "default_mode": crate::mode::DEFAULT_MODE.as_str(),
        "contract": mode_contract(mode),
        "modes": ALL_MODES
            .iter()
            .map(|mode| (mode.as_str().to_string(), mode_contract(*mode)))
            .collect::<serde_json::Map<String, Value>>(),
    }))
    .into_response()
}

#[cfg(test)]
mod tests {
    use super::*;

    async fn body_of(response: Response) -> Value {
        let bytes = axum::body::to_bytes(response.into_body(), usize::MAX)
            .await
            .expect("body");
        serde_json::from_slice(&bytes).expect("json body")
    }

    fn state() -> (tempfile::TempDir, Arc<AgentState>) {
        let dir = tempfile::tempdir().expect("tempdir");
        let root = dir.path().join("agent_workspace");
        let workspace = Workspace::new(&root).expect("workspace");
        std::fs::create_dir_all(root.join("notes")).expect("notes");
        std::fs::write(root.join("notes/a.txt"), "alpha\n").expect("file");
        (dir, Arc::new(AgentState::new(workspace)))
    }

    #[tokio::test]
    async fn preflight_decides_a_call_without_touching_anything() {
        let (_dir, state) = state();
        let request = json!({
            "mode": "trusted",
            "calls": [
                {"tool": "write_file", "args": {"path": "notes/a.txt"},
                 "policy": {"risk": "write", "sandbox": "workspace"}},
                {"tool": "run_command", "args": {"command": "rm -rf /"},
                 "policy": {"risk": "exec", "shell": true}}
            ]
        });
        let response = preflight(State(state), Json(request)).await;
        let body = body_of(response).await;
        assert_eq!(body["mode"], "trusted");
        let calls = body["calls"].as_array().expect("calls");
        assert_eq!(calls[0]["auto_approve"], json!(true));
        assert_eq!(calls[0]["block_reason"], Value::Null);
        // The file exists in the workspace, so this is a mutation.
        assert_eq!(calls[0]["classification"]["change_class"], "mutation");
        assert_eq!(calls[0]["stage_proposal"], json!(false), "trusted applies");
        assert_eq!(
            calls[1]["circuit_breaker"],
            "circuit breaker: refusing destructive shell command"
        );
        assert_eq!(
            calls[1]["block_reason"],
            "BLOCKED: circuit breaker: refusing destructive shell command"
        );
    }

    #[tokio::test]
    async fn preflight_takes_its_policies_as_data() {
        let (_dir, state) = state();
        let request = json!({
            "mode": "bypass",
            "policies": {"tools": {"secret_tool": {"risk": "destructive", "destructive": true}}},
            "calls": [{"tool": "secret_tool"}, {"tool": "unknown_tool"}]
        });
        let body = body_of(preflight(State(state), Json(request)).await).await;
        let calls = body["calls"].as_array().expect("calls");
        assert_eq!(
            calls[0]["block_reason"],
            "BLOCKED: destructive action is always blocked"
        );
        // Unknown tools fall back to the table's default, which is gated —
        // and bypass then allows it. Policy is data, so this is the caller's.
        assert_eq!(calls[1]["auto_approve"], json!(true));
    }

    #[tokio::test]
    async fn preflight_answers_a_plan_when_it_is_given_one() {
        let (_dir, state) = state();
        let request = json!({
            "mode": "strict",
            "calls": [],
            "plan": {
                "steps": [{"action": "read_file"}, {"action": "run_command"}, {"action": ""}],
                "governed_tools": [],
                "requires_approval": false
            },
            "policies": {"tools": {"read_file": {"risk": "read", "auto_approve": true}}}
        });
        let body = body_of(preflight(State(state), Json(request)).await).await;
        assert_eq!(body["plan"]["non_auto_steps"], json!(["run_command"]));
        assert_eq!(body["plan"]["requires_approval"], json!(true));
    }

    #[tokio::test]
    async fn a_malformed_request_is_a_400_not_a_panic() {
        let (_dir, state) = state();
        let response = preflight(State(state.clone()), Json(json!({"calls": "nope"}))).await;
        assert_eq!(response.status(), StatusCode::BAD_REQUEST);
        let response = exec_command(State(state), Json(json!({"timeout": "soon"}))).await;
        assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    }

    #[tokio::test]
    async fn exec_refuses_to_run_under_strict_but_still_validates() {
        let (_dir, state) = state();
        let request = json!({"mode": "strict", "command": "cat notes/a.txt"});
        let body = body_of(exec_command(State(state), Json(request)).await).await;
        assert_eq!(body["permitted"], json!(false));
        assert_eq!(body["executed"], json!(false));
        assert_eq!(body["validation"]["ok"], json!(true));
        assert_eq!(body["validation"]["executable"], "cat");
        assert!(body["block_reason"]
            .as_str()
            .expect("reason")
            .contains("requires explicit approval"));
        assert!(body.get("result").is_none(), "nothing ran");
    }

    #[tokio::test]
    async fn exec_runs_a_read_only_command_when_the_mode_permits_it() {
        let (_dir, state) = state();
        let request = json!({"mode": "bypass", "command": "cat notes/a.txt"});
        let body = body_of(exec_command(State(state), Json(request)).await).await;
        assert_eq!(body["permitted"], json!(true));
        assert_eq!(body["executed"], json!(true));
        assert_eq!(body["exit_code"], json!(0));
        assert_eq!(body["result"]["stdout"], "alpha\n");
        assert_eq!(body["result"]["cwd"], ".");
        assert_eq!(body["truncated"]["stdout"], json!(false));
    }

    #[tokio::test]
    async fn exec_reports_a_refusal_without_running_anything() {
        let (_dir, state) = state();
        for (command, message) in [
            (
                "cat ../outside",
                "Path traversal in command arguments is not allowed: ../outside",
            ),
            ("rm -rf notes", "Command is not allowed: rm"),
            ("cat a | b", "Shell operators are not allowed."),
        ] {
            let request = json!({"mode": "bypass", "command": command});
            let body = body_of(exec_command(State(state.clone()), Json(request)).await).await;
            assert_eq!(body["validation"]["ok"], json!(false), "{command}");
            assert_eq!(body["validation"]["error"]["message"], message);
            assert_eq!(body["executed"], json!(false));
        }
    }

    #[tokio::test]
    async fn git_is_validated_and_never_executed() {
        let (_dir, state) = state();
        let request = json!({"mode": "bypass", "command": "git status"});
        let body = body_of(exec_command(State(state), Json(request)).await).await;
        assert_eq!(body["executed"], json!(false));
        assert_eq!(body["validation"]["ok"], json!(false));
        assert!(body["validation"]["error"]["message"]
            .as_str()
            .expect("message")
            .contains("read-only git_status"));
    }

    #[tokio::test]
    async fn the_contract_route_answers_every_mode_and_defaults_to_strict() {
        let body = body_of(contract(Query(ContractQuery { mode: None })).await).await;
        assert_eq!(body["mode"], "strict");
        assert_eq!(body["default_mode"], "strict");
        assert_eq!(body["contract"]["proposal_first"], json!(true));
        for mode in ALL_MODES {
            assert_eq!(body["modes"][mode.as_str()]["mode"], mode.as_str());
        }
        let body = body_of(
            contract(Query(ContractQuery {
                mode: Some("yolo".into()),
            }))
            .await,
        )
        .await;
        assert_eq!(body["mode"], "bypass");
        assert_eq!(body["requested"], "yolo");
        // Unknown input never escalates.
        let body = body_of(
            contract(Query(ContractQuery {
                mode: Some("nonsense".into()),
            }))
            .await,
        )
        .await;
        assert_eq!(body["mode"], "strict");
    }

    #[test]
    fn the_router_mounts_exactly_the_three_kernel_routes() {
        let dir = tempfile::tempdir().expect("tempdir");
        let workspace = Workspace::new(dir.path().join("ws")).expect("workspace");
        // Building it is the assertion: axum panics on a duplicate or malformed
        // route at construction time.
        let _router: Router = router(workspace);
    }

    #[test]
    fn the_exec_fallback_policy_is_the_conservative_shape() {
        let policy = run_command_fallback_policy();
        assert_eq!(policy.risk(), "exec");
        assert_eq!(policy.sandbox(), "workspace");
        assert!(!policy.auto_approve);
        assert!(!policy.is_destructive());
        assert!(!effective_auto_approve(
            PermissionMode::Trusted,
            "run_command",
            &policy,
            None
        ));
        assert!(effective_auto_approve(
            PermissionMode::Bypass,
            "run_command",
            &policy,
            None
        ));
    }
}
