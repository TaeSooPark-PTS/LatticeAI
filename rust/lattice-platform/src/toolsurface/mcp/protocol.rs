//! Streamable-HTTP MCP JSON-RPC at `POST /mcp`.
//!
//! This route is mounted on the product router and listed in
//! [`super::MOUNTED`] as `POST /mcp` — one JSON-RPC envelope, not
//! per-method schemas. Session auth is the same `require_user` every other
//! product route uses (loopback no-auth installs resolve to the local
//! owner, which is what a localhost client such as Claude Desktop sees).

use axum::extract::State;
use axum::http::{HeaderMap, StatusCode};
use axum::response::{IntoResponse, Response};
use serde_json::{json, Value};

use crate::workspaceos::workspace::skills;

use super::dispatch::{
    dispatch, is_native_tool, mcp_text_content, parse_skill_name, skill_tool_name, DispatchError,
    NATIVE_TOOLS,
};
use super::http::{json_text, require_user};
use super::McpState;

const JSONRPC: &str = "2.0";
const PARSE_ERROR: i64 = -32700;
const INVALID_REQUEST: i64 = -32600;
const METHOD_NOT_FOUND: i64 = -32601;
const INVALID_PARAMS: i64 = -32602;
const GOVERNANCE_DENIED: i64 = -32001;

const SUPPORTED_VERSIONS: &[&str] = &["2024-11-05", "2025-03-26", "2025-06-18"];
const DEFAULT_VERSION: &str = "2025-03-26";

pub(crate) async fn mcp_jsonrpc(
    State(state): State<McpState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    if let Err(resp) = require_user(&state.auth, &headers) {
        return resp;
    }
    if body.is_empty() {
        return jsonrpc_http(rpc_error(Value::Null, PARSE_ERROR, "Parse error"));
    }
    let parsed = match serde_json::from_slice::<Value>(&body) {
        Ok(value) => value,
        Err(_) => return jsonrpc_http(rpc_error(Value::Null, PARSE_ERROR, "Parse error")),
    };
    if let Some(batch) = parsed.as_array() {
        return handle_batch(&state, &headers, batch);
    }
    handle_one(&state, &headers, &parsed)
}

fn handle_batch(state: &McpState, headers: &HeaderMap, batch: &[Value]) -> Response {
    if batch.is_empty() {
        return jsonrpc_http(rpc_error(Value::Null, INVALID_REQUEST, "Invalid Request"));
    }
    let mut answers = Vec::new();
    for item in batch {
        if is_notification(item) {
            continue;
        }
        match handle_one_value(state, headers, item) {
            OneShot::Response(value) => answers.push(value),
            OneShot::Accepted => {}
        }
    }
    if answers.is_empty() {
        return StatusCode::ACCEPTED.into_response();
    }
    json_text(
        StatusCode::OK,
        &serde_json::to_string(&answers).unwrap_or_else(|_| "[]".into()),
    )
}

fn handle_one(state: &McpState, headers: &HeaderMap, value: &Value) -> Response {
    match handle_one_value(state, headers, value) {
        OneShot::Accepted => StatusCode::ACCEPTED.into_response(),
        OneShot::Response(body) => jsonrpc_http(body),
    }
}

enum OneShot {
    Accepted,
    Response(Value),
}

fn handle_one_value(state: &McpState, headers: &HeaderMap, value: &Value) -> OneShot {
    let Some(obj) = value.as_object() else {
        return OneShot::Response(rpc_error(Value::Null, INVALID_REQUEST, "Invalid Request"));
    };
    if obj.get("jsonrpc").and_then(Value::as_str) != Some(JSONRPC) {
        return OneShot::Response(rpc_error(id_of(value), INVALID_REQUEST, "Invalid Request"));
    }
    let Some(method) = obj.get("method").and_then(Value::as_str) else {
        return OneShot::Response(rpc_error(id_of(value), INVALID_REQUEST, "Invalid Request"));
    };
    let notification = is_notification(value);
    if notification {
        return OneShot::Accepted;
    }
    let id = id_of(value);
    let params = obj.get("params").cloned().unwrap_or(json!({}));
    let result = match method {
        "initialize" => initialize(&params),
        "ping" => Ok(json!({})),
        "tools/list" => Ok(tools_list(state)),
        "tools/call" => tools_call(state, headers, &params),
        other if other.starts_with("notifications/") => Ok(json!({})),
        _ => {
            return OneShot::Response(rpc_error(id, METHOD_NOT_FOUND, "Method not found"));
        }
    };
    match result {
        Ok(value) => OneShot::Response(rpc_result(id, value)),
        Err((code, message)) => OneShot::Response(rpc_error(id, code, &message)),
    }
}

fn is_notification(value: &Value) -> bool {
    let Some(obj) = value.as_object() else {
        return false;
    };
    if obj.get("jsonrpc").and_then(Value::as_str) != Some(JSONRPC) {
        return false;
    }
    if obj.get("method").and_then(Value::as_str).is_none() {
        return false;
    }
    matches!(obj.get("id"), None | Some(Value::Null))
}

fn initialize(params: &Value) -> Result<Value, (i64, String)> {
    let requested = params
        .get("protocolVersion")
        .and_then(Value::as_str)
        .unwrap_or(DEFAULT_VERSION);
    let protocol_version = if SUPPORTED_VERSIONS.contains(&requested) {
        requested
    } else {
        DEFAULT_VERSION
    };
    Ok(json!({
        "protocolVersion": protocol_version,
        "serverInfo": {
            "name": "lattice-ai",
            "version": env!("CARGO_PKG_VERSION"),
        },
        "capabilities": {
            "tools": {}
        }
    }))
}

fn tools_list(state: &McpState) -> Value {
    let mut tools = Vec::new();
    for tool in NATIVE_TOOLS {
        tools.push(json!({
            "name": tool.name,
            "description": tool.description,
            "inputSchema": tool.input_schema_value(),
        }));
    }
    for skill in skills::scan_installed_skills(&state.skills_dir) {
        let input_schema = skill
            .input_schema
            .unwrap_or_else(|| json!({"type": "object", "properties": {}}));
        tools.push(json!({
            "name": skill_tool_name(&skill.name),
            "description": skill.description,
            "inputSchema": input_schema,
        }));
    }
    json!({"tools": tools})
}

fn tools_call(
    state: &McpState,
    headers: &HeaderMap,
    params: &Value,
) -> Result<Value, (i64, String)> {
    let Some(name) = params.get("name").and_then(Value::as_str) else {
        return Err((INVALID_PARAMS, "Missing tool name".into()));
    };
    if !is_native_tool(name) && parse_skill_name(name).is_none() {
        return Err((INVALID_PARAMS, format!("Unknown tool: {name}")));
    }
    let args = params
        .get("arguments")
        .cloned()
        .unwrap_or_else(|| json!({}));
    let identity = require_user(&state.auth, headers).map_err(|_| {
        (
            GOVERNANCE_DENIED,
            "Authentication is required to call tools".into(),
        )
    })?;
    match dispatch(
        state.tools.as_ref(),
        &state.skills_dir,
        &identity,
        headers,
        name,
        &args,
    ) {
        Ok(result) => Ok(json!({
            "content": [{"type": "text", "text": mcp_text_content(&result)}],
            "isError": false,
            "structuredContent": result,
        })),
        Err(DispatchError::Unknown(name)) => Err((INVALID_PARAMS, format!("Unknown tool: {name}"))),
        Err(DispatchError::Governance(message)) => Err((GOVERNANCE_DENIED, message)),
        Err(DispatchError::Missing(field)) => {
            Err((INVALID_PARAMS, format!("Missing argument: {field}")))
        }
        Err(DispatchError::Message(message)) => Err((INVALID_PARAMS, message)),
        Err(DispatchError::Unavailable(message)) => Err((INVALID_PARAMS, message.to_string())),
    }
}

fn id_of(value: &Value) -> Value {
    value.get("id").cloned().unwrap_or(Value::Null)
}

fn rpc_result(id: Value, result: Value) -> Value {
    json!({"jsonrpc": JSONRPC, "id": id, "result": result})
}

fn rpc_error(id: Value, code: i64, message: &str) -> Value {
    json!({
        "jsonrpc": JSONRPC,
        "id": id,
        "error": {"code": code, "message": message}
    })
}

fn jsonrpc_http(body: Value) -> Response {
    json_text(
        StatusCode::OK,
        &serde_json::to_string(&body).unwrap_or_else(|_| "{}".into()),
    )
}
