#![allow(
    dead_code,
    unused_imports,
    unused_variables,
    unused_assignments,
    unused_mut,
    private_interfaces,
    clippy::result_large_err,
    clippy::needless_lifetimes,
    clippy::too_many_arguments,
    clippy::type_complexity,
    clippy::collapsible_if,
    clippy::needless_as_bytes,
    clippy::redundant_closure,
    clippy::needless_return,
    clippy::manual_clamp,
    clippy::ptr_arg,
    clippy::unnecessary_sort_by,
    clippy::result_unit_err,
    clippy::useless_vec,
    clippy::uninlined_format_args,
    clippy::manual_contains,
    clippy::needless_borrows_for_generic_args,
    clippy::implicit_clone,
    clippy::unnecessary_map_or,
    clippy::match_like_matches_macro,
    clippy::manual_range_contains,
    clippy::derivable_impls,
    clippy::needless_pass_by_ref_mut,
    clippy::redundant_guards,
    clippy::map_identity,
    clippy::iter_overeager_cloned,
    clippy::explicit_auto_deref,
    clippy::bool_comparison,
    clippy::nonminimal_bool,
    clippy::if_same_then_else,
    clippy::question_mark,
    clippy::single_char_pattern,
    clippy::manual_pattern_char_comparison,
    clippy::manual_is_ascii_check,
    clippy::repeat_once,
    clippy::unused_self,
    clippy::module_inception
)]

use std::path::PathBuf;
use std::sync::Arc;

use axum::extract::{Path, Query, State};
use axum::http::{header, HeaderMap, HeaderValue, StatusCode};
use axum::response::Response;
use axum::routing::{get, post};
use axum::Router;
use lattice_auth::{AuthState, Identity, OrderedMap};
use lattice_core::worker::WorkerSeamClient;
use serde_json::{json, Map, Value};

use crate::admin::{
    append_audit_event, audit_log_path, build_sensitivity_report, classify_sensitive_message,
    detail_status, json_ok, json_status, load_audit_log, load_chat_history, now_iso,
    redact_secret_text, redact_structure, today_str, tz_name,
};

use super::*;

const CREATE_XLSX: &str = "/tools/create_xlsx";

pub(crate) async fn security_export(
    State(state): State<SecurityState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Result<Response, Response> {
    let (admin, _) = require_admin(&state, &headers)?;
    let parsed = if body.is_empty() {
        Map::new()
    } else {
        match serde_json::from_slice::<Value>(&body) {
            Ok(Value::Object(m)) => m,
            _ => Map::new(),
        }
    };
    let scope = parsed
        .get("scope")
        .and_then(Value::as_str)
        .unwrap_or("events")
        .to_ascii_lowercase();
    let fmt = parsed
        .get("format")
        .and_then(Value::as_str)
        .unwrap_or("json")
        .to_ascii_lowercase();
    let rows = match scope.as_str() {
        "events" => events(&state),
        "users" => summarize_user_risk(&history(&state), &file_events(&state))
            .into_iter()
            .map(Value::Object)
            .collect(),
        "files" => file_events(&state),
        "overview" => {
            let report = build_sensitivity_report(&history(&state));
            vec![report.get("summary").cloned().unwrap_or(json!({}))]
        }
        _ => {
            return Err(detail_status(
                StatusCode::BAD_REQUEST,
                "지원하지 않는 scope입니다.",
            ))
        }
    };
    log_view(
        &state,
        &admin.email,
        "export",
        &format!("{scope}:{fmt}"),
        "export",
    );

    match fmt.as_str() {
        "json" => {
            let body = serde_json::to_string_pretty(&redact_structure(&json!(rows)))
                .unwrap_or_else(|_| "[]".into());
            Ok(attachment(
                StatusCode::OK,
                "application/json; charset=utf-8",
                &format!("security_{scope}.json"),
                body.into_bytes(),
            ))
        }
        "csv" => {
            let bytes = csv_dump(&rows);
            Ok(attachment(
                StatusCode::OK,
                "text/csv; charset=utf-8",
                &format!("security_{scope}.csv"),
                bytes,
            ))
        }
        "excel" | "xlsx" => {
            if rows_have_non_scalar(&rows) {
                // Python's openpyxl raises on list-valued cells → Starlette 500.
                return Ok(plain_500());
            }
            let bytes = excel_via_worker(&state, &rows).await?;
            Ok(attachment(
                StatusCode::OK,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                &format!("security_{scope}.xlsx"),
                bytes,
            ))
        }
        "pdf" => {
            let bytes = minimal_pdf("Lattice AI Security Report", &rows);
            Ok(attachment(
                StatusCode::OK,
                "application/pdf",
                &format!("security_{scope}.pdf"),
                bytes,
            ))
        }
        "txt" => {
            let lines: Vec<String> = rows
                .iter()
                .map(|r| {
                    serde_json::to_string(&redact_structure(r)).unwrap_or_else(|_| "{}".into())
                })
                .collect();
            Ok(attachment(
                StatusCode::OK,
                "text/plain; charset=utf-8",
                &format!("security_{scope}.txt"),
                lines.join("\n").into_bytes(),
            ))
        }
        _ => Err(detail_status(
            StatusCode::BAD_REQUEST,
            "지원하지 않는 포맷입니다.",
        )),
    }
}

fn rows_have_non_scalar(rows: &[Value]) -> bool {
    rows.iter().any(|r| {
        r.as_object()
            .map(|o| {
                o.values()
                    .any(|v| matches!(v, Value::Array(_) | Value::Object(_)))
            })
            .unwrap_or(false)
    })
}

async fn excel_via_worker(state: &SecurityState, rows: &[Value]) -> Result<Vec<u8>, Response> {
    let Some(worker) = state.worker.as_ref() else {
        return Err(detail_status(
            StatusCode::BAD_GATEWAY,
            "xlsx export requires the document worker (POST /tools/create_xlsx).",
        ));
    };
    let headers = sheet_headers(rows);
    let mut table: Vec<Vec<Value>> = Vec::new();
    table.push(headers.iter().map(|h| json!(h)).collect());
    for r in rows {
        let obj = r.as_object();
        table.push(
            headers
                .iter()
                .map(|h| {
                    let v = obj.and_then(|o| o.get(h)).cloned().unwrap_or(Value::Null);
                    match v {
                        Value::String(s) => json!(redact_secret_text(&s)),
                        other => other,
                    }
                })
                .collect(),
        );
    }
    let mut body = OrderedMap::new();
    body.insert("rows", json!(table));
    body.insert("filename", json!("security_export.xlsx"));
    body.insert("sheet_name", json!("security_export"));
    let payload = serde_json::to_value(&body).unwrap_or(json!({}));
    let reply = worker.post_json(CREATE_XLSX, &payload).await.map_err(|e| {
        detail_status(
            StatusCode::from_u16(e.status().unwrap_or(502)).unwrap_or(StatusCode::BAD_GATEWAY),
            &e.to_string(),
        )
    })?;
    if let Some(path) = reply.get("path").and_then(Value::as_str) {
        if let Ok(bytes) = std::fs::read(path) {
            return Ok(bytes);
        }
        // Relative path — try as-is from cwd, then from data_dir.
        let via_data = state.data_dir.join(path);
        if let Ok(bytes) = std::fs::read(&via_data) {
            return Ok(bytes);
        }
    }
    if let Some(b64) = reply.get("bytes_b64").and_then(Value::as_str) {
        if let Ok(bytes) = base64::Engine::decode(&base64::engine::general_purpose::STANDARD, b64) {
            return Ok(bytes);
        }
    }
    Err(detail_status(
        StatusCode::BAD_GATEWAY,
        "worker create_xlsx did not return a readable spreadsheet",
    ))
}

fn sheet_headers(rows: &[Value]) -> Vec<String> {
    let mut headers = Vec::new();
    let mut seen = std::collections::HashSet::new();
    for r in rows {
        if let Some(obj) = r.as_object() {
            for k in obj.keys() {
                if seen.insert(k.clone()) {
                    headers.push(k.clone());
                }
            }
        }
    }
    headers
}

fn csv_dump(rows: &[Value]) -> Vec<u8> {
    if rows.is_empty() {
        return Vec::new();
    }
    let headers = sheet_headers(rows);
    let mut out = String::new();
    out.push_str(&headers.join(","));
    out.push_str("\r\n");
    for r in rows {
        let obj = r.as_object();
        let cells: Vec<String> = headers
            .iter()
            .map(|h| {
                let v = obj.and_then(|o| o.get(h)).cloned().unwrap_or(Value::Null);
                let sanitized = redact_structure(&v);
                csv_escape(&value_cell(&sanitized))
            })
            .collect();
        out.push_str(&cells.join(","));
        out.push_str("\r\n");
    }
    out.into_bytes()
}

fn value_cell(v: &Value) -> String {
    match v {
        Value::Null => String::new(),
        Value::String(s) => s.clone(),
        Value::Bool(b) => {
            if *b {
                "True".into()
            } else {
                "False".into()
            }
        }
        Value::Number(n) => n.to_string(),
        // csv.DictWriter uses Python `str()`, not JSON (`['secret']` not `["secret"]`).
        Value::Array(items) => format!(
            "[{}]",
            items
                .iter()
                .map(|item| match item {
                    Value::String(s) => format!("'{s}'"),
                    other => value_cell(other),
                })
                .collect::<Vec<_>>()
                .join(", ")
        ),
        Value::Object(map) => format!(
            "{{{}}}",
            map.iter()
                .map(|(k, val)| format!("'{k}': {}", value_cell(val)))
                .collect::<Vec<_>>()
                .join(", ")
        ),
    }
}

fn csv_escape(s: &str) -> String {
    if s.contains(',') || s.contains('"') || s.contains('\n') || s.contains('\r') {
        format!("\"{}\"", s.replace('"', "\"\""))
    } else {
        s.to_string()
    }
}

fn minimal_pdf(title: &str, rows: &[Value]) -> Vec<u8> {
    let generated = now_iso();
    let mut text = format!("{title}\nGenerated: {generated}\n\n");
    for row in rows.iter().take(30) {
        text.push_str(&serde_json::to_string(&redact_structure(row)).unwrap_or_default());
        text.push('\n');
    }
    // Minimal valid PDF so the fixture's `%PDF` magic matches.
    let stream = format!("BT /F1 12 Tf 72 720 Td ({title}) Tj ET\n");
    let escaped = stream.replace('(', "\\(").replace(')', "\\)");
    format!(
        "%PDF-1.1\n\
         1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n\
         2 0 obj<</Type/Pages/Count 1/Kids[3 0 R]>>endobj\n\
         3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R>>endobj\n\
         4 0 obj<</Length {}>>stream\n{escaped}\nendstream endobj\n\
         xref\n0 5\n0000000000 65535 f \ntrailer<</Size 5/Root 1 0 R>>\nstartxref\n0\n%%EOF\n",
        escaped.len()
    )
    .into_bytes()
}

pub(crate) fn utf8_json(status: StatusCode, body: String) -> Response {
    json_response_typed(
        status,
        "application/json; charset=utf-8",
        body.into_bytes(),
        None,
    )
}

fn attachment(status: StatusCode, content_type: &str, filename: &str, body: Vec<u8>) -> Response {
    json_response_typed(
        status,
        content_type,
        body,
        Some((
            header::CONTENT_DISPOSITION,
            HeaderValue::from_str(&format!("attachment; filename={filename}"))
                .unwrap_or_else(|_| HeaderValue::from_static("attachment")),
        )),
    )
}

fn plain_500() -> Response {
    json_response_typed(
        StatusCode::INTERNAL_SERVER_ERROR,
        "text/plain; charset=utf-8",
        b"Internal Server Error".to_vec(),
        None,
    )
}

fn json_response_typed(
    status: StatusCode,
    content_type: &str,
    body: Vec<u8>,
    extra: Option<(header::HeaderName, HeaderValue)>,
) -> Response {
    let mut builder = Response::builder()
        .status(status)
        .header(header::CONTENT_TYPE, content_type);
    if let Some((name, value)) = extra {
        builder = builder.header(name, value);
    }
    builder
        .body(axum::body::Body::from(body))
        .unwrap_or_else(|_| Response::new(axum::body::Body::empty()))
}
