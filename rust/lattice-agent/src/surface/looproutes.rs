//! `/rust/agent/{run,resume,approvals}` — the loop as three routes.
//!
//! Homomorphic with Python's `/agent`, `/agent/resume` and `/agent/approvals`:
//! same request fields, same terminal payload, same pause contract (`run_id` +
//! a short-TTL token), same 403/404/410 answers on resume. A client written
//! against one reads the other.
//!
//! Fail-closed is the property worth stating: a plan that needs approval never
//! executes here. It is persisted, a token is handed out, and only a resume
//! presenting the matching, unexpired token for the same user carries it into
//! EXECUTING.

use std::convert::Infallible;
use std::pin::Pin;
use std::sync::Arc;
use std::task::{Context, Poll};

use axum::body::Body;
use axum::extract::{Query, State};
use axum::http::{header, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use serde::Deserialize;
use serde_json::{json, Value};
use tokio::sync::mpsc;

use crate::kernel::agentloop::{RunRequest, Runtime};
use crate::kernel::plan::normalize_plan;
use crate::kernel::runs::{
    approval_deadline, approval_expired_payload, token_matches, token_urlsafe, AgentRunStore,
    PausedRun,
};
use crate::kernel::state::{AgentRunContext, AgentState};
use crate::kernel::trace::epoch_now;
use crate::surface::bad_request;
use crate::surface::runbody::{finish_payload, pause_payload, ResumeBody, RunBody};
use crate::surface::worker::WorkerClient;
use crate::tools::sandbox::Workspace;

/// What the host hands these routes.
#[derive(Debug, Clone)]
pub struct LoopConfig {
    /// Where the AI worker listens, e.g. `http://127.0.0.1:4825`.
    pub worker_origin: String,
    /// Where paused runs are persisted. See [`crate::kernel::runs`] for why this is not
    /// Python's directory.
    pub runs_dir: std::path::PathBuf,
    /// Shared HTTP client, so the loopback pool is not duplicated.
    pub client: Option<reqwest::Client>,
    /// Where a `strict` mutation is staged for review.
    ///
    /// `None` uses [`crate::kernel::proposals::JsonProposalStore`] over
    /// `LATTICEAI_DATA_DIR`. A host that runs the Review Center in this process
    /// **must** inject that store instead — see [`crate::kernel::proposals`] for why a
    /// second writer on the same file is not a second opinion but a lost item.
    pub proposals: Option<Arc<dyn crate::kernel::proposals::ProposalStore>>,
    /// The `pre_tool` / `post_tool` sink every run's native tools fire through.
    ///
    /// `None` means no `hooks.json` is in reach of this process and no user
    /// hook fires — the standalone contract. A host that mounts `/api/hooks`
    /// **must** inject a sink over the very [`HooksStore`] those routes use,
    /// for the reason the proposal store is injected: the registry keeps the
    /// document and the run log in memory.
    ///
    /// [`HooksStore`]: https://docs.rs/lattice-platform
    pub hooks: Option<Arc<dyn crate::tools::HookSink>>,
    /// The host's MCP tools and installed skills, as one catalog (v12.0.0).
    ///
    /// `None` means this process has no MCP surface and no skill registry in
    /// reach, so a run's menu is its own tools. A host that mounts `/mcp`
    /// **should** inject a catalog over the very dispatch those routes use —
    /// see [`crate::tools::catalog`] — or the model is told about capabilities
    /// it cannot invoke, which is worse than not mentioning them.
    pub external: Option<Arc<dyn crate::tools::ToolCatalog>>,
}

impl LoopConfig {
    /// A config for `worker_origin` with the default store and a fresh client.
    pub fn new(worker_origin: impl Into<String>) -> Self {
        Self {
            worker_origin: worker_origin.into(),
            runs_dir: crate::kernel::runs::default_runs_dir(),
            client: None,
            proposals: None,
            hooks: None,
            external: None,
        }
    }

    /// Stage proposals into `store` — the Review Center's, in the product.
    pub fn with_proposals(
        mut self,
        store: Arc<dyn crate::kernel::proposals::ProposalStore>,
    ) -> Self {
        self.proposals = Some(store);
        self
    }

    /// Fire `pre_tool` / `post_tool` through `sink` — the hooks registry's.
    pub fn with_hooks(mut self, sink: Arc<dyn crate::tools::HookSink>) -> Self {
        self.hooks = Some(sink);
        self
    }

    /// Offer `catalog`'s MCP tools and skills on every run's menu (v12.0.0).
    pub fn with_catalog(mut self, catalog: Arc<dyn crate::tools::ToolCatalog>) -> Self {
        self.external = Some(catalog);
        self
    }

    fn worker(&self) -> WorkerClient {
        match &self.client {
            Some(client) => WorkerClient::with_client(&self.worker_origin, client.clone()),
            None => WorkerClient::new(&self.worker_origin),
        }
    }
}

/// Shared state for the three loop routes.
#[derive(Debug)]
pub struct LoopState {
    workspace: Workspace,
    config: LoopConfig,
    store: AgentRunStore,
}

impl LoopState {
    pub fn new(workspace: Workspace, config: LoopConfig) -> Self {
        let store = AgentRunStore::new(config.runs_dir.clone());
        // Hygiene at construction, exactly where Python sweeps: long-expired
        // records must not accumulate, and a sweep failure must not block boot.
        store.sweep_expired(None);
        Self {
            workspace,
            config,
            store,
        }
    }

    pub fn store(&self) -> &AgentRunStore {
        &self.store
    }

    /// The ports one run needs: the body's, with the host's proposal store and
    /// the host's hook sink.
    ///
    /// Every `Runtime` these routes build comes through here, so neither
    /// injection can be forgotten on one of the four paths.
    fn deps_for(&self, body: &RunBody) -> crate::kernel::agentloop::LoopDeps {
        let mut deps = body.to_deps_with_ports(
            self.config.worker(),
            self.workspace.clone(),
            self.config.hooks.clone(),
            self.config.external.clone(),
        );
        if let Some(store) = &self.config.proposals {
            deps.proposals = Arc::clone(store);
        }
        deps
    }
}

/// Mount the loop routes.
pub fn loop_router(workspace: Workspace, config: LoopConfig) -> Router {
    Router::new()
        .route("/rust/agent/run", post(run))
        .route("/rust/agent/resume", post(resume))
        .route("/rust/agent/approvals", get(approvals))
        .with_state(Arc::new(LoopState::new(workspace, config)))
}

fn refuse(status: StatusCode, detail: Value) -> Response {
    (status, Json(json!({"detail": detail}))).into_response()
}

/// A run either finished, or paused, or could not reach the reasoner.
enum Outcome {
    Finished(AgentRunContext, Vec<Value>),
    Paused(AgentRunContext, Value),
    Unreachable(String),
}

/// Plan, then either pause for approval or drive to a terminal state.
async fn drive(
    state: &Arc<LoopState>,
    body: &RunBody,
    request: &RunRequest,
    observer: Option<crate::kernel::agentloop::StepObserver>,
) -> Outcome {
    let deps = state.deps_for(body);
    let mut runtime = Runtime::new(deps);
    if let Some(observer) = observer {
        runtime = runtime.with_observer(observer);
    }
    let mut ctx = AgentRunContext::new();
    ctx.executing_model = body.executing_model.clone();
    ctx.reviewing_model = body.reviewing_model.clone();
    ctx.project_context = request.project_context.clone();
    ctx.permission_mode = body.permission_mode.clone();
    ctx.state = AgentState::Planning;
    ctx.state_history.push(ctx.state.as_str().to_string());

    if let Err(error) = runtime.plan(&mut ctx, request).await {
        return Outcome::Unreachable(error.0);
    }
    let requirements = runtime.approval_requirements(&ctx, request);
    // The legacy explicit pause and the approval gate share one path, one
    // store and one resume — the wire label is the only difference.
    if body.human_in_loop || requirements["requires_approval"] == json!(true) {
        return Outcome::Paused(ctx, requirements);
    }
    runtime.approve(&mut ctx, request, false);
    match runtime.run_to_completion(&mut ctx, request).await {
        Ok(()) => Outcome::Finished(ctx, runtime.audit),
        Err(error) => Outcome::Unreachable(error.0),
    }
}

/// Persist the pause and build its payload.
fn pause(
    state: &Arc<LoopState>,
    body: &RunBody,
    ctx: &mut AgentRunContext,
    requirements: &Value,
) -> Response {
    let (Ok(run_id), Ok(token)) = (token_urlsafe(16), token_urlsafe(32)) else {
        return (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({"error": "no_randomness",
                        "detail": "cannot mint an approval token on this host"})),
        )
            .into_response();
    };
    let (expires_epoch, expires_at) = approval_deadline();
    ctx.state_history
        .push(AgentState::WaitingApproval.as_str().to_string());
    // Best-effort, exactly as Python: a store failure must not turn a pause
    // into a failure. It only means this pause cannot survive a restart.
    state.store.save(
        &PausedRun {
            run_id: &run_id,
            user: body.user_email.as_deref().unwrap_or(""),
            language_hint: &body.language_hint,
            token: &token,
            expires_epoch,
            expires_at: &expires_at,
            legacy_context: body.human_in_loop,
            request: serde_json::to_value(body).unwrap_or(Value::Null),
        },
        ctx,
    );
    Json(pause_payload(
        ctx,
        body,
        requirements,
        &run_id,
        &token,
        &expires_at,
    ))
    .into_response()
}

async fn run(State(state): State<Arc<LoopState>>, Json(raw): Json<Value>) -> Response {
    let body: RunBody = match serde_json::from_value(raw) {
        Ok(body) => body,
        Err(err) => return bad_request(format!("could not read the run request: {err}")),
    };
    if body.message.trim().is_empty() {
        return bad_request("message is required");
    }
    let request = body.to_request();
    if !body.stream {
        return match drive(&state, &body, &request, None).await {
            Outcome::Finished(ctx, audit) => Json(finish_payload(
                &ctx,
                &body,
                &state.workspace,
                &state.deps_for(&body).file_create_actions,
                &audit,
            ))
            .into_response(),
            Outcome::Paused(mut ctx, requirements) => pause(&state, &body, &mut ctx, &requirements),
            Outcome::Unreachable(detail) => refuse(StatusCode::BAD_GATEWAY, json!(detail)),
        };
    }

    // Live loop visibility: named `agent_step` frames while the run executes,
    // then the same terminal payload the JSON response returns — a client that
    // ignores named events sees the historical stream shape.
    let (sender, receiver) = mpsc::channel::<String>(256);
    let steps = sender.clone();
    let observer: crate::kernel::agentloop::StepObserver = Box::new(move |event: Value| {
        // Telemetry: a full buffer drops a frame rather than stalling the loop.
        let _ = steps.try_send(format!(
            "event: agent_step\ndata: {}\n\n",
            serde_json::to_string(&event).unwrap_or_default()
        ));
    });
    tokio::spawn(async move {
        let payload = match drive(&state, &body, &request, Some(observer)).await {
            Outcome::Finished(ctx, audit) => {
                let deps = state.deps_for(&body);
                finish_payload(
                    &ctx,
                    &body,
                    &state.workspace,
                    &deps.file_create_actions,
                    &audit,
                )
            }
            Outcome::Paused(mut ctx, requirements) => {
                let response = pause(&state, &body, &mut ctx, &requirements);
                body_json(response).await
            }
            Outcome::Unreachable(detail) => json!({"error": detail}),
        };
        let answer = payload
            .get("response")
            .and_then(Value::as_str)
            .unwrap_or("작업을 완료했습니다.")
            .to_string();
        for frame in [
            json!({"chunk": answer, "agent": payload.clone()}),
            json!({"chunk": "", "agent": payload}),
        ] {
            let _ = sender
                .send(format!(
                    "data: {}\n\n",
                    serde_json::to_string(&frame).unwrap_or_default()
                ))
                .await;
        }
        let _ = sender.send("data: [DONE]\n\n".to_string()).await;
    });

    Response::builder()
        .header(header::CONTENT_TYPE, "text/event-stream")
        .header(header::CACHE_CONTROL, "no-cache")
        .header("X-Routed-To", "rust-agent")
        .body(Body::from_stream(FrameStream { receiver }))
        .unwrap_or_else(|err| bad_request(format!("cannot open the stream: {err}")))
}

/// The mpsc receiver as a body stream. `futures-core`'s `Stream` is the only
/// thing `Body::from_stream` needs, so this is the whole adapter.
struct FrameStream {
    receiver: mpsc::Receiver<String>,
}

impl futures_core::Stream for FrameStream {
    type Item = Result<String, Infallible>;

    fn poll_next(mut self: Pin<&mut Self>, context: &mut Context<'_>) -> Poll<Option<Self::Item>> {
        self.receiver.poll_recv(context).map(|frame| frame.map(Ok))
    }
}

async fn body_json(response: Response) -> Value {
    let bytes = axum::body::to_bytes(response.into_body(), usize::MAX)
        .await
        .unwrap_or_default();
    serde_json::from_slice(&bytes).unwrap_or(Value::Null)
}

async fn resume(State(state): State<Arc<LoopState>>, Json(raw): Json<Value>) -> Response {
    let body: ResumeBody = match serde_json::from_value(raw) {
        Ok(body) => body,
        Err(err) => return bad_request(format!("could not read the resume request: {err}")),
    };
    let run_id = body.run_id.clone().unwrap_or_default();
    let Some(record) = state.store.load(&run_id) else {
        return refuse(
            StatusCode::NOT_FOUND,
            json!("Agent run not found. It may have expired — start a new request."),
        );
    };
    let stored_request: RunBody = match serde_json::from_value(record["req"].clone()) {
        Ok(request) => request,
        // An unreconstructable request is not a resumable run.
        Err(_) => {
            return refuse(
                StatusCode::NOT_FOUND,
                json!("Agent run not found. It may have expired — start a new request."),
            )
        }
    };
    let owner = record["user"].as_str().unwrap_or_default();
    if !owner.is_empty() && body.user_email.as_deref().unwrap_or_default() != owner {
        return refuse(
            StatusCode::FORBIDDEN,
            json!("Agent run belongs to another user."),
        );
    }
    if epoch_now() >= record["expires_epoch"].as_f64().unwrap_or(0.0) {
        state.store.delete(&run_id);
        return refuse(
            StatusCode::GONE,
            approval_expired_payload(&stored_request.message),
        );
    }
    if !token_matches(
        record["token_hash"].as_str().unwrap_or_default(),
        body.approval_token.as_deref().unwrap_or_default(),
    ) {
        return refuse(
            StatusCode::FORBIDDEN,
            json!("Invalid approval token for this run."),
        );
    }
    // Token validated — the pending run is consumed either way.
    state.store.delete(&run_id);

    if !body.is_approved() {
        return Json(json!({
            "status": "cancelled",
            "run_id": run_id,
            "response": "사용자가 계획을 취소했습니다.",
        }))
        .into_response();
    }

    let request = stored_request.to_request();
    let deps = state.deps_for(&stored_request);
    let file_create_actions = deps.file_create_actions.clone();
    let mut runtime = Runtime::new(deps);
    let mut ctx = AgentRunContext::restore(&record["ctx"]);
    if let Some(edit) = body.plan_edit() {
        let (plan, fixes) = normalize_plan(edit, &request.message);
        ctx.plan = plan;
        let mut step = json!({
            "state": AgentState::WaitingApproval.as_str(),
            "edited_plan": true,
        });
        if !fixes.is_empty() {
            step["plan_fixes"] = json!(fixes);
        }
        ctx.transcript.push(step);
    }
    if let Some(model) = body.executing_model.clone() {
        ctx.executing_model = Some(model);
    }
    if let Some(model) = body.reviewing_model.clone() {
        ctx.reviewing_model = Some(model);
    }
    // The authenticated owner explicitly approved this plan.
    runtime.approve(&mut ctx, &request, true);
    match runtime.run_to_completion(&mut ctx, &request).await {
        Ok(()) => Json(finish_payload(
            &ctx,
            &stored_request,
            &state.workspace,
            &file_create_actions,
            &runtime.audit,
        ))
        .into_response(),
        Err(error) => refuse(StatusCode::BAD_GATEWAY, json!(error.0)),
    }
}

#[derive(Debug, Default, Deserialize)]
struct ApprovalsQuery {
    #[serde(default)]
    user: Option<String>,
}

async fn approvals(
    State(state): State<Arc<LoopState>>,
    Query(query): Query<ApprovalsQuery>,
) -> Response {
    Json(json!({"pending": state.store.pending_summaries(query.user.as_deref())})).into_response()
}
