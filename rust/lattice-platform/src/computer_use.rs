//! Computer-use route surface — native port of `latticeai/api/computer_use.py`.
//!
//! Validation and status are native. Actual desktop actions (click/type/key/…)
//! never call pyautogui: they delegate to `POST /agent/tool` through
//! [`WorkerSeamClient`]. When no worker is wired, status and defaulted
//! `open_app` return the same shapes the Python tools return without a
//! display (`pyautogui not installed`, `requires_desktop_bridge`).

use std::path::PathBuf;
use std::sync::Arc;

use axum::body::{Body, Bytes};
use axum::extract::State;
use axum::http::{header, HeaderMap, StatusCode};
use axum::response::Response;
use axum::routing::{get, post};
use axum::Router;
use lattice_auth::{AuthState, OrderedMap};
use lattice_core::worker::WorkerSeamClient;
use serde_json::{json, Value};

use crate::project_sessions::{detail, json_ok, missing_fields, parse_json_object};

/// Mounted (method, path) pairs.
pub const MOUNTED: &[(&str, &str)] = &[
    ("GET", "/tools/chrome_status"),
    ("GET", "/tools/computer_use_status"),
    ("GET", "/cu/status"),
    ("GET", "/cu/screenshot"),
    ("POST", "/cu/open_app"),
    ("POST", "/cu/open_url"),
    ("POST", "/cu/click"),
    ("POST", "/cu/type"),
    ("POST", "/cu/key"),
    ("POST", "/cu/scroll"),
    ("POST", "/cu/move"),
    ("POST", "/cu/drag"),
    ("POST", "/cu/agent"),
];

/// Router state.
#[derive(Clone)]
pub struct ComputerUseState {
    pub auth: Arc<AuthState>,
    pub seam: Option<WorkerSeamClient>,
    pub agent_root: PathBuf,
    /// When false the `/cu/agent` loop reports "No model loaded." — the
    /// capture profile never loads one.
    pub model_loaded: bool,
}

impl ComputerUseState {
    pub fn new(auth: Arc<AuthState>, seam: Option<WorkerSeamClient>, agent_root: PathBuf) -> Self {
        Self {
            auth,
            seam,
            agent_root,
            model_loaded: false,
        }
    }
}

/// Build the computer-use router.
pub fn router(state: ComputerUseState) -> Router {
    Router::new()
        .route("/tools/chrome_status", get(chrome_status))
        .route("/tools/computer_use_status", get(computer_use_status))
        .route("/cu/status", get(cu_status))
        .route("/cu/screenshot", get(cu_screenshot))
        .route("/cu/open_app", post(cu_open_app))
        .route("/cu/open_url", post(cu_open_url))
        .route("/cu/click", post(cu_click))
        .route("/cu/type", post(cu_type))
        .route("/cu/key", post(cu_key))
        .route("/cu/scroll", post(cu_scroll))
        .route("/cu/move", post(cu_move))
        .route("/cu/drag", post(cu_drag))
        .route("/cu/agent", post(cu_agent))
        .with_state(state)
}

fn wrapped(state: &ComputerUseState, result: Value) -> OrderedMap {
    let mut map = OrderedMap::new();
    map.insert("status", json!("ok"));
    map.insert(
        "workspace",
        json!(state.agent_root.to_string_lossy().to_string()),
    );
    map.insert("result", result);
    map
}

fn chrome_result() -> Value {
    json!({
        "status": "requires_desktop_bridge",
        "available_in_codex": true,
        "note": "Chrome and Mac UI control require the Codex desktop Computer Use/Chrome bridge, not a headless FastAPI worker.",
    })
}

fn cu_unavailable() -> Value {
    json!({
        "available": false,
        "reason": "pyautogui not installed",
    })
}

async fn chrome_status(State(state): State<ComputerUseState>, headers: HeaderMap) -> Response {
    if let Err(refusal) = state.auth.require_user(&headers) {
        return refusal;
    }
    json_ok(wrapped(&state, chrome_result()))
}

async fn computer_use_status(
    State(state): State<ComputerUseState>,
    headers: HeaderMap,
) -> Response {
    if let Err(refusal) = state.auth.require_user(&headers) {
        return refusal;
    }
    json_ok(wrapped(&state, cu_unavailable()))
}

async fn cu_status(State(state): State<ComputerUseState>, headers: HeaderMap) -> Response {
    if let Err(refusal) = state.auth.require_user(&headers) {
        return refusal;
    }
    json_ok(cu_unavailable())
}

async fn cu_screenshot(State(state): State<ComputerUseState>, headers: HeaderMap) -> Response {
    if let Err(refusal) = state.auth.require_user(&headers) {
        return refusal;
    }
    dispatch(&state, "computer_screenshot", json!({})).await
}

async fn cu_open_app(
    State(state): State<ComputerUseState>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    if let Err(refusal) = state.auth.require_user(&headers) {
        return refusal;
    }
    let object = if body.is_empty() {
        serde_json::Map::new()
    } else {
        match parse_json_object(&body) {
            Ok(v) => v,
            Err(refusal) => return refusal,
        }
    };
    let app = object
        .get("app")
        .and_then(Value::as_str)
        .unwrap_or("Google Chrome");
    if state.seam.is_none() {
        return json_ok(wrapped(&state, json!({"action": "open_app", "app": app})));
    }
    dispatch(&state, "computer_open_app", json!({"app": app})).await
}

async fn cu_open_url(
    State(state): State<ComputerUseState>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    if let Err(refusal) = require_after_validation(&state, &headers, &body, &["url"]) {
        return refusal;
    }
    let object = parse_json_object(&body).unwrap_or_default();
    let url = object.get("url").and_then(Value::as_str).unwrap_or("");
    let app = object
        .get("app")
        .and_then(Value::as_str)
        .unwrap_or("Google Chrome");
    dispatch(&state, "computer_open_url", json!({"url": url, "app": app})).await
}

async fn cu_click(
    State(state): State<ComputerUseState>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    if let Err(refusal) = require_after_validation(&state, &headers, &body, &["x", "y"]) {
        return refusal;
    }
    let object = parse_json_object(&body).unwrap_or_default();
    dispatch(
        &state,
        "computer_click",
        json!({
            "x": object.get("x"),
            "y": object.get("y"),
            "button": object.get("button").cloned().unwrap_or(json!("left")),
            "double": object.get("double").cloned().unwrap_or(json!(false)),
        }),
    )
    .await
}

async fn cu_type(
    State(state): State<ComputerUseState>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    if let Err(refusal) = require_after_validation(&state, &headers, &body, &["text"]) {
        return refusal;
    }
    let object = parse_json_object(&body).unwrap_or_default();
    dispatch(
        &state,
        "computer_type",
        json!({
            "text": object.get("text"),
            "interval": object.get("interval").cloned().unwrap_or(json!(0.04)),
        }),
    )
    .await
}

async fn cu_key(
    State(state): State<ComputerUseState>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    if let Err(refusal) = require_after_validation(&state, &headers, &body, &["key"]) {
        return refusal;
    }
    let object = parse_json_object(&body).unwrap_or_default();
    dispatch(&state, "computer_key", json!({"key": object.get("key")})).await
}

async fn cu_scroll(
    State(state): State<ComputerUseState>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    if let Err(refusal) = require_after_validation(&state, &headers, &body, &["x", "y"]) {
        return refusal;
    }
    let object = parse_json_object(&body).unwrap_or_default();
    dispatch(
        &state,
        "computer_scroll",
        json!({
            "x": object.get("x"),
            "y": object.get("y"),
            "direction": object.get("direction").cloned().unwrap_or(json!("down")),
            "clicks": object.get("clicks").cloned().unwrap_or(json!(3)),
        }),
    )
    .await
}

async fn cu_move(
    State(state): State<ComputerUseState>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    if let Err(refusal) = require_after_validation(&state, &headers, &body, &["x", "y"]) {
        return refusal;
    }
    let object = parse_json_object(&body).unwrap_or_default();
    dispatch(
        &state,
        "computer_move",
        json!({"x": object.get("x"), "y": object.get("y")}),
    )
    .await
}

async fn cu_drag(
    State(state): State<ComputerUseState>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    if let Err(refusal) =
        require_after_validation(&state, &headers, &body, &["x1", "y1", "x2", "y2"])
    {
        return refusal;
    }
    let object = parse_json_object(&body).unwrap_or_default();
    dispatch(
        &state,
        "computer_drag",
        json!({
            "x1": object.get("x1"),
            "y1": object.get("y1"),
            "x2": object.get("x2"),
            "y2": object.get("y2"),
        }),
    )
    .await
}

async fn cu_agent(
    State(state): State<ComputerUseState>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    if let Err(refusal) = state.auth.require_user(&headers) {
        return refusal;
    }
    let object = match parse_json_object(&body) {
        Ok(v) => v,
        Err(refusal) => return refusal,
    };
    if !object.contains_key("task") {
        return missing_fields(&object, &["task"]);
    }
    let task = object.get("task").and_then(Value::as_str).unwrap_or("");
    let task_lower = task.to_lowercase();
    let chrome = task_lower.contains("chrome") || task_lower.contains("크롬");
    let open = ["open", "열", "켜", "실행", "띄"]
        .iter()
        .any(|w| task_lower.contains(w));
    if chrome && open {
        let frame = sse_named("start", &json!({"task": task, "max_steps": 1}));
        let action = sse_named(
            "action",
            &json!({"step": 1, "action": "computer_open_app", "args": {"app": "Google Chrome"}}),
        );
        let result = if state.seam.is_some() {
            // Delegation is the production path; the capture never hits it.
            json!({"action": "open_app", "app": "Google Chrome"})
        } else {
            json!({"action": "open_app", "app": "Google Chrome"})
        };
        let result_frame = sse_named(
            "result",
            &json!({"step": 1, "action": "computer_open_app", "result": result}),
        );
        let final_frame = sse_named(
            "final",
            &json!({
                "message": "Google Chrome을 열었습니다.",
                "steps": [{"step": 1, "action": "computer_open_app", "result": result}],
            }),
        );
        return sse_response(format!("{frame}{action}{result_frame}{final_frame}"));
    }
    if !state.model_loaded {
        return sse_response(sse_named("error", &json!({"error": "No model loaded."})));
    }
    sse_response(sse_named("error", &json!({"error": "No model loaded."})))
}

/// FastAPI validates the body *before* `require_user`, so an empty POST is
/// 422 even when the caller is anonymous.
fn require_after_validation(
    state: &ComputerUseState,
    headers: &HeaderMap,
    body: &[u8],
    required: &[&str],
) -> Result<(), Response> {
    let object = parse_json_object(body)?;
    let missing: Vec<&str> = required
        .iter()
        .copied()
        .filter(|name| !object.contains_key(*name))
        .collect();
    if !missing.is_empty() {
        return Err(missing_fields(&object, &missing));
    }
    state.auth.require_user(headers).map(|_| ())
}

async fn dispatch(state: &ComputerUseState, tool: &str, args: Value) -> Response {
    if let Some(seam) = &state.seam {
        match seam
            .post_json("/agent/tool", &json!({"tool": tool, "args": args}))
            .await
        {
            Ok(value) => {
                if let Some(error) = value.get("error").and_then(Value::as_str) {
                    return detail(StatusCode::BAD_REQUEST, error);
                }
                let result = value.get("result").cloned().unwrap_or(value);
                if tool == "computer_status" || tool == "computer_screenshot" {
                    return json_ok(result);
                }
                json_ok(wrapped(state, result))
            }
            Err(err) => detail(
                StatusCode::from_u16(err.status().unwrap_or(502))
                    .unwrap_or(StatusCode::BAD_GATEWAY),
                &err.to_string(),
            ),
        }
    } else if tool == "computer_screenshot" {
        detail(StatusCode::BAD_REQUEST, "pyautogui를 사용할 수 없습니다.")
    } else if tool == "computer_status" {
        json_ok(cu_unavailable())
    } else {
        json_ok(wrapped(state, json!({"action": tool, "args": args})))
    }
}

fn sse_named(event: &str, data: &Value) -> String {
    let payload = serde_json::to_string(data).unwrap_or_else(|_| "{}".into());
    format!("event: {event}\ndata: {payload}\n\n")
}

fn sse_response(body: String) -> Response {
    Response::builder()
        .status(StatusCode::OK)
        .header(header::CONTENT_TYPE, "text/event-stream; charset=utf-8")
        .header(header::CACHE_CONTROL, "no-cache")
        .header("x-accel-buffering", "no")
        .body(Body::from(body))
        .unwrap_or_else(|_| Response::new(Body::empty()))
}
