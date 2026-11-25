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

#[derive(Debug, Clone)]
pub struct LoopConfig {
    pub worker_origin: String,
    pub runs_dir: std::path::PathBuf,
    pub client: Option<reqwest::Client>,
    pub proposals: Option<Arc<dyn crate::kernel::proposals::ProposalStore>>,
    pub hooks: Option<Arc<dyn crate::tools::HookSink>>,
    pub external: Option<Arc<dyn crate::tools::ToolCatalog>>,
}

impl LoopConfig {
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

    pub fn with_proposals(
        mut self,
        store: Arc<dyn crate::kernel::proposals::ProposalStore>,
    ) -> Self {
        self.proposals = Some(store);
        self
    }

    pub fn with_hooks(mut self, sink: Arc<dyn crate::tools::HookSink>) -> Self {
        self.hooks = Some(sink);
        self
    }

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

pub type ScopeAuthorizer =
    Arc<dyn Fn(Option<&str>, Option<&str>) -> Result<bool, String> + Send + Sync>;

pub struct LoopState {
    workspace: Workspace,
    config: LoopConfig,
    store: AgentRunStore,
    isolate_per_user: bool,
    authorizer: Option<ScopeAuthorizer>,
}

impl std::fmt::Debug for LoopState {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("LoopState")
            .field("workspace", &self.workspace)
            .field("isolate_per_user", &self.isolate_per_user)
            .field("has_authorizer", &self.authorizer.is_some())
            .finish()
    }
}

impl LoopState {
    pub fn new(workspace: Workspace, config: LoopConfig) -> Self {
        let store = AgentRunStore::new(config.runs_dir.clone());
        store.sweep_expired(None);
        Self {
            workspace,
            config,
            store,
            isolate_per_user: false,
            authorizer: None,
        }
    }

    pub fn isolated(base_workspace: Workspace, config: LoopConfig) -> Self {
        let mut state = Self::new(base_workspace, config);
        state.isolate_per_user = true;
        state
    }

    pub fn with_authorizer(mut self, authorizer: ScopeAuthorizer) -> Self {
        self.authorizer = Some(authorizer);
        self
    }

    pub fn store(&self) -> &AgentRunStore {
        &self.store
    }

    fn resolve_workspace_for(&self, body: &RunBody) -> Result<Workspace, (StatusCode, Value)> {
        if !self.isolate_per_user {
            return Ok(self.workspace.clone());
        }
        let shared = crate::tools::sandbox::is_shared_workspace_id(body.workspace_id.as_deref());
        let shared_authorized = if shared {
            match &self.authorizer {
                Some(auth) => auth(body.user_email.as_deref(), body.workspace_id.as_deref())
                    .map_err(|msg| {
                        (
                            StatusCode::FORBIDDEN,
                            json!({"error": "workspace_access_denied", "detail": msg}),
                        )
                    })?,
                None => false,
            }
        } else {
            false
        };
        if shared && !shared_authorized {
            return Err((
                StatusCode::FORBIDDEN,
                json!({
                    "error": "workspace_access_denied",
                    "detail": "shared workspace access denied",
                }),
            ));
        }
        let spec = crate::tools::sandbox::IsolatedRootSpec {
            base_root: self.workspace.root().to_path_buf(),
            user_id: body.user_email.clone(),
            tenant_id: body.tenant_id.clone(),
            workspace_id: body.workspace_id.clone(),
            shared_authorized,
            require_user_isolation: true,
        };
        Workspace::for_isolated_scope(&spec).map_err(|err| {
            (
                StatusCode::FORBIDDEN,
                json!({
                    "error": "workspace_isolation_error",
                    "detail": err.message,
                }),
            )
        })
    }

    fn deps_for(&self, body: &RunBody) -> crate::kernel::agentloop::LoopDeps {
        self.deps_with_workspace(body, self.workspace.clone())
    }

    fn deps_with_workspace(
        &self,
        body: &RunBody,
        workspace: Workspace,
    ) -> crate::kernel::agentloop::LoopDeps {
        let mut deps = body.to_deps_with_ports(
            self.config.worker(),
            workspace,
            self.config.hooks.clone(),
            self.config.external.clone(),
        );
        if let Some(store) = &self.config.proposals {
            deps.proposals = Arc::clone(store);
        }
        deps
    }
}

pub fn loop_router(workspace: Workspace, config: LoopConfig) -> Router {
    Router::new()
        .route("/rust/agent/run", post(run))
        .route("/rust/agent/resume", post(resume))
        .route("/rust/agent/approvals", get(approvals))
        .with_state(Arc::new(LoopState::new(workspace, config)))
}

pub fn loop_router_isolated(
    base_workspace: Workspace,
    config: LoopConfig,
    authorizer: Option<ScopeAuthorizer>,
) -> Router {
    let mut state = LoopState::isolated(base_workspace, config);
    if let Some(auth) = authorizer {
        state = state.with_authorizer(auth);
    }
    Router::new()
        .route("/rust/agent/run", post(run))
        .route("/rust/agent/resume", post(resume))
        .route("/rust/agent/approvals", get(approvals))
        .with_state(Arc::new(state))
}

fn refuse(status: StatusCode, detail: Value) -> Response {
    (status, Json(json!({"detail": detail}))).into_response()
}

enum Outcome {
    Finished(AgentRunContext, Vec<Value>, Workspace),
    Paused(AgentRunContext, Value),
    Unreachable(String),
    Denied(StatusCode, Value),
}

async fn drive(
    state: &Arc<LoopState>,
    body: &RunBody,
    request: &RunRequest,
    observer: Option<crate::kernel::agentloop::StepObserver>,
) -> Outcome {
    let workspace = match state.resolve_workspace_for(body) {
        Ok(ws) => ws,
        Err((status, detail)) => return Outcome::Denied(status, detail),
    };
    let deps = state.deps_with_workspace(body, workspace.clone());
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
    if body.human_in_loop || requirements["requires_approval"] == json!(true) {
        return Outcome::Paused(ctx, requirements);
    }
    runtime.approve(&mut ctx, request, false);
    match runtime.run_to_completion(&mut ctx, request).await {
        Ok(()) => Outcome::Finished(ctx, runtime.audit, workspace),
        Err(error) => Outcome::Unreachable(error.0),
    }
}

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
            Outcome::Finished(ctx, audit, workspace) => Json(finish_payload(
                &ctx,
                &body,
                &workspace,
                &state.deps_for(&body).file_create_actions,
                &audit,
            ))
            .into_response(),
            Outcome::Paused(mut ctx, requirements) => pause(&state, &body, &mut ctx, &requirements),
            Outcome::Unreachable(detail) => refuse(StatusCode::BAD_GATEWAY, json!(detail)),
            Outcome::Denied(status, detail) => refuse(status, detail),
        };
    }

    let (sender, receiver) = mpsc::channel::<String>(256);
    let steps = sender.clone();
    let observer: crate::kernel::agentloop::StepObserver = Box::new(move |event: Value| {
        let _ = steps.try_send(format!(
            "event: agent_step\ndata: {}\n\n",
            serde_json::to_string(&event).unwrap_or_default()
        ));
    });
    tokio::spawn(async move {
        let payload = match drive(&state, &body, &request, Some(observer)).await {
            Outcome::Finished(ctx, audit, workspace) => {
                let deps = state.deps_for(&body);
                finish_payload(&ctx, &body, &workspace, &deps.file_create_actions, &audit)
            }
            Outcome::Paused(mut ctx, requirements) => {
                let response = pause(&state, &body, &mut ctx, &requirements);
                body_json(response).await
            }
            Outcome::Unreachable(detail) => json!({"error": detail}),
            Outcome::Denied(_status, detail) => json!({"error": detail}),
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
    state.store.delete(&run_id);

    if !body.is_approved() {
        return Json(json!({
            "status": "cancelled",
            "run_id": run_id,
            "response": "사용자가 계획을 취소했습니다.",
        }))
        .into_response();
    }

    let workspace = match state.resolve_workspace_for(&stored_request) {
        Ok(ws) => ws,
        Err((status, detail)) => return refuse(status, detail),
    };
    let request = stored_request.to_request();
    let deps = state.deps_with_workspace(&stored_request, workspace.clone());
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
    runtime.approve(&mut ctx, &request, true);
    match runtime.run_to_completion(&mut ctx, &request).await {
        Ok(()) => Json(finish_payload(
            &ctx,
            &stored_request,
            &workspace,
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
    if state.isolate_per_user
        && query
            .user
            .as_deref()
            .map(str::trim)
            .unwrap_or("")
            .is_empty()
    {
        return Json(json!({"pending": []})).into_response();
    }
    Json(json!({"pending": state.store.pending_summaries(query.user.as_deref())})).into_response()
}
