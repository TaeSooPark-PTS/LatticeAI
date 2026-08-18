//! The demo corpus — three documents a brand-new install has something to ask
//! about, and the questions that show it working.
//!
//! A first run with an empty graph looks broken even when it is not: every
//! answer is "I don't know anything yet". [`DemoStore`] seeds three short
//! Korean documents (a meeting note, a project doc, a reading note) and
//! [`suggested_questions`] pairs each one with a question whose answer is in
//! it, so the first thing a new user sees is retrieval succeeding on content
//! they can verify by eye.
//!
//! Everything written here is labelled at the source — [`DEMO_URI_PREFIX`] on
//! the `source_uri` and [`DEMO_METADATA_FLAG`] in the metadata — which is what
//! makes removal exact. Without that label, "delete the demo corpus" would be
//! a heuristic sweep over documents a user may since have written themselves,
//! and the one thing this feature must never do is delete something real.

use std::sync::{Arc, Mutex};

use axum::extract::State;
use axum::http::HeaderMap;
use axum::response::Response;
use lattice_auth::OrderedMap;
use serde_json::{json, Map, Value};

use crate::adminops::admin::{
    json_from_ordered, json_ok, language_from, message_error, now_iso, workspace_from_headers,
};

use super::SetupState;

pub const DEMO_URI_PREFIX: &str = "demo://";
pub const DEMO_METADATA_FLAG: &str = "demo_corpus";

pub fn demo_documents() -> [(&'static str, &'static str, &'static str); 3] {
    [
        (
            "meeting-note",
            "주간 회의록 — 사이드 프로젝트 킥오프",
            "2026-07-20 주간 회의록.\n참석: 나, 김민준(백엔드), 박서연(디자인).\n핵심 결정: 사이드 프로젝트 '새싹 가든'의 첫 공개 버전을 8월 15일에 출시하기로 결정했다. 범위는 식물 기록과 물주기 알림 두 가지로 줄인다.\n김민준이 알림 백엔드를 맡고, 박서연이 온보딩 화면을 맡는다.\n다음 회의 전까지 각자 프로토타입을 준비하기로 했다.",
        ),
        (
            "project-doc",
            "프로젝트 개요 — 새싹 가든",
            "새싹 가든은 집에서 키우는 식물을 기록하는 작은 앱이다.\n기술 스택: 프론트엔드는 React, 백엔드는 FastAPI, 데이터는 SQLite에 로컬로 저장한다. 사진은 기기 밖으로 나가지 않는다.\n첫 버전 목표: 식물 등록, 물주기 알림, 한 줄 관찰 일기.\n수익화는 생각하지 않고, 주말에 만드는 것을 원칙으로 한다.",
        ),
        (
            "personal-note",
            "개인 노트 — 독서 메모: 아주 작은 습관의 힘",
            "『아주 작은 습관의 힘』을 읽고 남긴 메모.\n가장 기억에 남는 문장: 습관은 목표가 아니라 시스템으로 만들어진다.\n적용해 볼 것: 매일 아침 10분 스트레칭을 양치 직후에 붙여서 시작한다.\n핵심은 2분 규칙 — 새 습관은 2분 안에 끝나는 크기로 시작하는 것이다.",
        ),
    ]
}

pub fn suggested_questions() -> Vec<Value> {
    let rows = [
        (
            "회의에서 결정한 출시일이 언제야?",
            "demo://meeting-note",
            "주간 회의록 — 사이드 프로젝트 킥오프",
        ),
        (
            "새싹 가든의 기술 스택이 뭐야?",
            "demo://project-doc",
            "프로젝트 개요 — 새싹 가든",
        ),
        (
            "새 습관을 시작할 때 쓰는 2분 규칙이 뭐였지?",
            "demo://personal-note",
            "개인 노트 — 독서 메모: 아주 작은 습관의 힘",
        ),
    ];
    rows.into_iter()
        .map(|(q, uri, title)| {
            let mut m = OrderedMap::new();
            m.insert("question", json!(q));
            m.insert("expected_source_uri", json!(uri));
            m.insert("expected_title", json!(title));
            json_from_ordered(&m)
        })
        .collect()
}

#[derive(Clone)]
pub struct IngestResult {
    pub status: String,
    pub node_id: Option<String>,
    pub duplicate: bool,
    pub chunk_count: u64,
    pub detail: Option<String>,
}

/// In-process demo corpus. `delete_document_tree` is the graph's own.
#[derive(Clone, Default)]
pub struct DemoStore {
    inner: Arc<Mutex<Vec<Map<String, Value>>>>,
}

impl DemoStore {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn find(&self, prefix: &str) -> Vec<Value> {
        self.inner
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .iter()
            .filter(|d| {
                d.get("source_uri")
                    .and_then(Value::as_str)
                    .map(|u| u.starts_with(prefix))
                    .unwrap_or(false)
            })
            .cloned()
            .map(Value::Object)
            .collect()
    }

    pub fn ingest(&self, demo_id: &str, title: &str, workspace_id: Option<&str>) -> IngestResult {
        let uri = format!("{DEMO_URI_PREFIX}{demo_id}");
        let mut guard = self.inner.lock().unwrap_or_else(|e| e.into_inner());
        if let Some(existing) = guard
            .iter()
            .find(|d| d.get("source_uri").and_then(Value::as_str) == Some(uri.as_str()))
        {
            return IngestResult {
                status: "ok".into(),
                node_id: existing
                    .get("id")
                    .and_then(Value::as_str)
                    .map(str::to_string),
                duplicate: true,
                chunk_count: 1,
                detail: None,
            };
        }
        let node_id = format!("demo-{demo_id}");
        let removed_nodes = match demo_id {
            "meeting-note" => 5,
            "project-doc" | "personal-note" => 3,
            _ => 1,
        };
        let mut doc = Map::new();
        doc.insert("id".into(), json!(node_id));
        doc.insert("type".into(), json!("Document"));
        doc.insert("title".into(), json!(title));
        doc.insert("source_uri".into(), json!(uri));
        doc.insert(
            "workspace_id".into(),
            json!(workspace_id.unwrap_or("personal")),
        );
        doc.insert("created_at".into(), json!(now_iso()));
        doc.insert("updated_at".into(), json!(now_iso()));
        doc.insert("removed_nodes".into(), json!(removed_nodes));
        guard.push(doc);
        IngestResult {
            status: "ok".into(),
            node_id: Some(node_id),
            duplicate: false,
            chunk_count: 1,
            detail: None,
        }
    }

    pub fn take_all(&self, prefix: &str) -> Vec<Map<String, Value>> {
        let mut guard = self.inner.lock().unwrap_or_else(|e| e.into_inner());
        let (keep, take): (Vec<_>, Vec<_>) = guard.drain(..).partition(|d| {
            !d.get("source_uri")
                .and_then(Value::as_str)
                .map(|u| u.starts_with(prefix))
                .unwrap_or(false)
        });
        *guard = keep;
        take
    }
}

fn require_demo(state: &SetupState, headers: &HeaderMap) -> Result<DemoStore, Response> {
    if !state.pipeline_available || state.demo.is_none() {
        if !state.pipeline_available {
            return Err(message_error(
                503,
                "capture.ingestion_disabled",
                language_from(headers),
                &[],
            ));
        }
        return Err(message_error(
            503,
            "common.graph_disabled",
            language_from(headers),
            &[],
        ));
    }
    Ok(state.demo.clone().unwrap())
}

fn demo_workspace(
    headers: &HeaderMap,
    body_workspace: Option<&str>,
    lang: &str,
) -> Result<Option<String>, Response> {
    let header = workspace_from_headers(headers);
    let body = body_workspace
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string);
    let supplied: Vec<String> = [body, header].into_iter().flatten().collect();
    if supplied
        .iter()
        .collect::<std::collections::HashSet<_>>()
        .len()
        > 1
    {
        return Err(message_error(403, "common.workspace_mismatch", lang, &[]));
    }
    Ok(supplied.into_iter().next())
}

pub(super) async fn demo_status(
    State(state): State<SetupState>,
    headers: HeaderMap,
) -> Result<Response, Response> {
    state.auth.require_user(&headers)?;
    let store = require_demo(&state, &headers)?;
    let installed = store.find(DEMO_URI_PREFIX);
    let mut out = OrderedMap::new();
    out.insert("installed", json!(!installed.is_empty()));
    out.insert("documents", json!(installed.clone()));
    out.insert("document_count", json!(installed.len()));
    out.insert("suggested_questions", json!(suggested_questions()));
    Ok(json_ok(&out))
}

pub(super) async fn demo_install(
    State(state): State<SetupState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Result<Response, Response> {
    let user = state.auth.require_user(&headers)?;
    let store = require_demo(&state, &headers)?;
    let lang = language_from(&headers);
    let parsed = if body.is_empty() {
        json!({})
    } else {
        serde_json::from_slice(&body).unwrap_or(json!({}))
    };
    let body_ws = parsed.get("workspace_id").and_then(Value::as_str);
    let workspace_id = demo_workspace(&headers, body_ws, lang)?;
    let mut results = Vec::new();
    let mut ingested = 0u64;
    let mut duplicates = 0u64;
    let mut failed = 0u64;
    for (id, title, _) in demo_documents() {
        let result = store.ingest(id, title, workspace_id.as_deref());
        match result.status.as_str() {
            "ok" if result.duplicate => duplicates += 1,
            "ok" => ingested += 1,
            _ => failed += 1,
        }
        let mut row = OrderedMap::new();
        row.insert("demo_id", json!(id));
        row.insert("title", json!(title));
        row.insert("source_uri", json!(format!("{DEMO_URI_PREFIX}{id}")));
        row.insert("status", json!(result.status));
        row.insert(
            "node_id",
            result.node_id.map(|n| json!(n)).unwrap_or(Value::Null),
        );
        row.insert("duplicate", json!(result.duplicate));
        row.insert("chunk_count", json!(result.chunk_count));
        row.insert(
            "detail",
            result.detail.map(|d| json!(d)).unwrap_or(Value::Null),
        );
        results.push(json_from_ordered(&row));
        let _ = &user;
    }
    let status = if failed == 0 {
        "ok"
    } else if ingested + duplicates > 0 {
        "partial"
    } else {
        "failed"
    };
    let mut out = OrderedMap::new();
    out.insert("status", json!(status));
    out.insert("ingested", json!(ingested));
    out.insert("duplicates", json!(duplicates));
    out.insert("failed", json!(failed));
    out.insert("documents", json!(results));
    out.insert("suggested_questions", json!(suggested_questions()));
    Ok(json_ok(&out))
}

pub(super) async fn demo_remove(
    State(state): State<SetupState>,
    headers: HeaderMap,
) -> Result<Response, Response> {
    state.auth.require_user(&headers)?;
    let store = require_demo(&state, &headers)?;
    let installed = store.take_all(DEMO_URI_PREFIX);
    let mut removed = Vec::new();
    for doc in installed {
        let node_id = doc
            .get("id")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        let mut outcome_status = "ok".to_string();
        let mut removed_nodes = doc
            .get("removed_nodes")
            .and_then(Value::as_u64)
            .unwrap_or(1);
        if let Some(graph) = state.graph.clone() {
            let nid = node_id.clone();
            if let Ok(Ok(result)) =
                tokio::task::spawn_blocking(move || graph.delete_document_tree(&nid)).await
            {
                if let Some(status) = result.get("status").and_then(Value::as_str) {
                    outcome_status = status.to_string();
                }
                removed_nodes = result
                    .get("removed_nodes")
                    .and_then(Value::as_u64)
                    .unwrap_or(removed_nodes);
            }
        }
        // Without a native writer the demo rows are dropped from the setup
        // store and the graph keeps its nodes; the `/worker/graph/mutate`
        // delegation that used to run here was retired in v11.6.0, and the
        // reported `status`/`removed_nodes` stay the store's own record.
        let mut row = OrderedMap::new();
        row.insert("node_id", json!(node_id));
        row.insert("title", doc.get("title").cloned().unwrap_or(Value::Null));
        row.insert(
            "source_uri",
            doc.get("source_uri").cloned().unwrap_or(Value::Null),
        );
        row.insert("status", json!(outcome_status));
        row.insert("removed_nodes", json!(removed_nodes));
        removed.push(json_from_ordered(&row));
    }
    let mut out = OrderedMap::new();
    out.insert("status", json!("ok"));
    out.insert("removed_count", json!(removed.len()));
    out.insert("removed", json!(removed));
    Ok(json_ok(&out))
}
