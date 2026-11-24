//! v13.8.0 Digital Brain Extension Routes (P0.2, P0.3, P1.1, P1.2, P1.4, P1.7, P3.1).
//!
//! Mounted alongside `brain_api::router` in `lattice-host` without mutating the
//! frozen `memory_brain.json` OpenAPI fragment. Every route enforces workspace
//! authentication and scoping through [`BrainState`].

use std::collections::{BTreeMap, BTreeSet, HashMap};

use axum::extract::{Path, Request, State};
use axum::response::Response;
use axum::routing::{get, post};
use axum::Router;
use lattice_core::embedding_registry::list_embedding_models;
use lattice_core::entity_resolution_ext::{
    evaluate_merge_candidates, execute_audited_entity_merge, rollback_entity_merge,
};
use lattice_core::integrity::{check_graph_integrity, execute_integrity_repair, RepairOptions};
use lattice_core::learning_loop::{
    list_corrections, record_knowledge_correction, revert_knowledge_correction,
    update_correction_status, CorrectionStatus, CorrectionTargetKind, RecordCorrectionRequest,
};
use lattice_core::temporal::query_temporal_timeline;
use serde_json::{json, Value};

use crate::memory_api::shared::{detail_response, ok_json, BrainState, Query};

pub fn router(state: BrainState) -> Router {
    Router::new()
        .route("/api/brain/integrity", get(brain_integrity_check))
        .route("/api/brain/integrity/repair", post(brain_integrity_repair))
        .route("/api/brain/entities/candidates", get(brain_entity_candidates))
        .route("/api/brain/entities/merge", post(brain_entity_merge))
        .route("/api/brain/entities/rollback", post(brain_entity_rollback))
        .route("/api/brain/entities/{entity_id}/timeline", get(brain_entity_timeline))
        .route("/api/brain/relationships/evolution", get(brain_relationship_evolution))
        .route("/api/brain/clusters", get(brain_topic_clusters))
        .route("/api/brain/gaps", get(brain_knowledge_gaps))
        .route("/api/brain/corrections", get(brain_list_corrections).post(brain_create_correction))
        .route("/api/brain/corrections/{correction_id}/status", post(brain_update_correction_status))
        .route("/api/brain/corrections/{correction_id}/revert", post(brain_revert_correction))
        .route("/api/brain/embeddings/registry", get(brain_embedding_registry))
        .with_state(state)
}

fn auth_scope(state: &BrainState, req: &Request, write: bool) -> Result<(String, String), Response> {
    let identity = state.require_user(req.headers())?;
    let ws = if write {
        state.gate_write(req.headers(), req.uri().query(), &identity.email)?
    } else {
        state.gate_read(req.headers(), req.uri().query(), &identity.email)?
    };
    Ok((identity.email, ws.unwrap_or_else(|| "personal".to_string())))
}

async fn parse_json_body(req: Request) -> Result<Value, Response> {
    let bytes = axum::body::to_bytes(req.into_body(), 1024 * 1024)
        .await
        .map_err(|e| detail_response(400, &e.to_string()))?;
    if bytes.is_empty() {
        return Ok(json!({}));
    }
    serde_json::from_slice(&bytes)
        .map_err(|e| detail_response(400, &format!("invalid JSON: {e}")))
}

async fn brain_integrity_check(State(state): State<BrainState>, req: Request) -> Response {
    if let Err(r) = auth_scope(&state, &req, false) {
        return r;
    }
    let res = state.store().with_read_conn(check_graph_integrity);
    match res {
        Ok(report) => ok_json(&serde_json::to_value(report).unwrap_or(Value::Null)),
        Err(err) => detail_response(500, &err.to_string()),
    }
}

async fn brain_integrity_repair(State(state): State<BrainState>, req: Request) -> Response {
    if let Err(r) = auth_scope(&state, &req, true) {
        return r;
    }
    let body = match parse_json_body(req).await {
        Ok(v) => v,
        Err(r) => return r,
    };
    let dry_run = body.get("dry_run").and_then(Value::as_bool).unwrap_or(true);
    let approved_actions: BTreeSet<String> = body
        .get("approved_actions")
        .and_then(Value::as_array)
        .map(|arr| {
            arr.iter()
                .filter_map(Value::as_str)
                .map(str::to_string)
                .collect()
        })
        .unwrap_or_default();
    let backup_path = body
        .get("backup_path")
        .and_then(Value::as_str)
        .map(str::to_string);
    let opts = RepairOptions {
        dry_run,
        approved_actions,
        backup_path,
    };
    let path = state.store().path().to_path_buf();
    let res = tokio::task::spawn_blocking(move || {
        let mut conn = rusqlite::Connection::open(&path)?;
        execute_integrity_repair(&mut conn, &opts)
    })
    .await;
    match res {
        Ok(Ok(out)) => ok_json(&serde_json::to_value(out).unwrap_or(Value::Null)),
        Ok(Err(err)) => detail_response(400, &err.to_string()),
        Err(err) => detail_response(500, &err.to_string()),
    }
}

async fn brain_entity_candidates(State(state): State<BrainState>, req: Request) -> Response {
    let (_email, ws) = match auth_scope(&state, &req, false) {
        Ok(v) => v,
        Err(r) => return r,
    };
    let ws_c = ws.clone();
    let res = state
        .store()
        .with_read_conn(move |conn| evaluate_merge_candidates(conn, &ws_c));
    match res {
        Ok(cands) => ok_json(&json!({"workspace_id": ws, "candidates": cands})),
        Err(err) => detail_response(400, &err.to_string()),
    }
}

async fn brain_entity_merge(State(state): State<BrainState>, req: Request) -> Response {
    let (email, ws) = match auth_scope(&state, &req, true) {
        Ok(v) => v,
        Err(r) => return r,
    };
    let body = match parse_json_body(req).await {
        Ok(v) => v,
        Err(r) => return r,
    };
    let canonical_id = body
        .get("canonical_id")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let duplicate_id = body
        .get("duplicate_id")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let rationale = body
        .get("rationale")
        .and_then(Value::as_str)
        .unwrap_or("user approved merge")
        .to_string();
    let signals = body.get("signals").cloned().unwrap_or_else(|| json!({}));
    let path = state.store().path().to_path_buf();
    let res = tokio::task::spawn_blocking(move || {
        let mut conn = rusqlite::Connection::open(&path)?;
        execute_audited_entity_merge(&mut conn, &ws, &canonical_id, &duplicate_id, &email, &rationale, signals)
    })
    .await;
    match res {
        Ok(Ok(rec)) => ok_json(&serde_json::to_value(rec).unwrap_or(Value::Null)),
        Ok(Err(err)) => detail_response(400, &err.to_string()),
        Err(err) => detail_response(500, &err.to_string()),
    }
}

async fn brain_entity_rollback(State(state): State<BrainState>, req: Request) -> Response {
    if let Err(r) = auth_scope(&state, &req, true) {
        return r;
    }
    let body = match parse_json_body(req).await {
        Ok(v) => v,
        Err(r) => return r,
    };
    let merge_id = body
        .get("merge_id")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let add_block = body
        .get("add_do_not_merge")
        .and_then(Value::as_bool)
        .unwrap_or(true);
    let path = state.store().path().to_path_buf();
    let res = tokio::task::spawn_blocking(move || {
        let mut conn = rusqlite::Connection::open(&path)?;
        rollback_entity_merge(&mut conn, &merge_id, add_block)
    })
    .await;
    match res {
        Ok(Ok(rec)) => ok_json(&serde_json::to_value(rec).unwrap_or(Value::Null)),
        Ok(Err(err)) => detail_response(400, &err.to_string()),
        Err(err) => detail_response(500, &err.to_string()),
    }
}

async fn brain_entity_timeline(
    State(state): State<BrainState>,
    Path(entity_id): Path<String>,
    req: Request,
) -> Response {
    let (_email, ws) = match auth_scope(&state, &req, false) {
        Ok(v) => v,
        Err(r) => return r,
    };
    let q = Query::from_uri(req.uri());
    let as_of = q.get("as_of").map(str::to_string);
    let res = state
        .store()
        .with_read_conn(move |conn| {
            let subject: String = conn
                .query_row(
                    "SELECT COALESCE(NULLIF(TRIM(title), ''), id) FROM nodes WHERE id = ?1",
                    rusqlite::params![entity_id],
                    |r| r.get(0),
                )
                .unwrap_or(entity_id);
            query_temporal_timeline(conn, Some(&ws), &subject, as_of.as_deref())
        });
    match res {
        Ok(summary) => ok_json(&serde_json::to_value(summary).unwrap_or(Value::Null)),
        Err(err) => detail_response(500, &err.to_string()),
    }
}

async fn brain_relationship_evolution(State(state): State<BrainState>, req: Request) -> Response {
    let (_email, ws) = match auth_scope(&state, &req, false) {
        Ok(v) => v,
        Err(r) => return r,
    };
    let ws_c = ws.clone();
    let res = state
        .store()
        .with_read_conn(move |conn| {
            let mut stmt = conn.prepare(
                "SELECT e.id, e.source, e.target, e.type, e.weight, e.confidence,
                        e.evidence, e.created_by, e.created_at, e.valid_from, e.valid_to, e.superseded_by,
                        (SELECT COUNT(*) FROM edge_occurrences o WHERE o.edge_id = e.id) AS occ_count
                 FROM edges_v2 e
                 JOIN nodes_v2 a ON a.id = e.source
                 WHERE COALESCE(NULLIF(TRIM(a.workspace_id), ''), 'personal') = ?1
                 ORDER BY e.created_at DESC LIMIT 200",
            )?;
            let rows: Vec<Value> = stmt
                .query_map(rusqlite::params![ws_c], |r| {
                    let ev_raw: String = r.get::<_, Option<String>>(6)?.unwrap_or_else(|| "[]".into());
                    let evidence: Value = serde_json::from_str(&ev_raw).unwrap_or_else(|_| json!([]));
                    let valid_to: Option<String> = r.get(10)?;
                    let created_by: String = r.get::<_, Option<String>>(7)?.unwrap_or_else(|| "explicit".into());
                    let status = if valid_to.as_deref().is_some_and(|s| !s.is_empty()) {
                        "terminated"
                    } else if created_by == "inferred" {
                        "inferred"
                    } else {
                        "explicit"
                    };
                    Ok(json!({
                        "id": r.get::<_, String>(0)?,
                        "source": r.get::<_, String>(1)?,
                        "target": r.get::<_, String>(2)?,
                        "type": r.get::<_, String>(3)?,
                        "weight": r.get::<_, f64>(4).unwrap_or(1.0),
                        "confidence": r.get::<_, f64>(5).unwrap_or(1.0),
                        "supporting_sources": evidence,
                        "created_by": created_by,
                        "status": status,
                        "created_at": r.get::<_, String>(8)?,
                        "valid_from": r.get::<_, Option<String>>(9)?,
                        "valid_to": valid_to,
                        "superseded_by": r.get::<_, Option<String>>(11)?,
                        "occurrence_count": r.get::<_, i64>(12).unwrap_or(1),
                    }))
                })?
                .filter_map(Result::ok)
                .collect();
            Ok(rows)
        });
    match res {
        Ok(edges) => ok_json(&json!({"workspace_id": ws, "relationships": edges})),
        Err(err) => detail_response(500, &err.to_string()),
    }
}

/// P3.1 Topic Clustering via deterministic label propagation over workspace graph edges.
async fn brain_topic_clusters(State(state): State<BrainState>, req: Request) -> Response {
    let (_email, ws) = match auth_scope(&state, &req, false) {
        Ok(v) => v,
        Err(r) => return r,
    };
    let ws_c = ws.clone();
    let res = state
        .store()
        .with_read_conn(move |conn| {
            let mut stmt = conn.prepare(
                "SELECT n.id, COALESCE(n.title, n.id), n.type
                 FROM nodes n LEFT JOIN nodes_v2 v ON v.id = n.id
                 WHERE COALESCE(NULLIF(TRIM(v.workspace_id), ''), 'personal') = ?1
                   AND COALESCE(v.superseded_by, '') = ''
                 ORDER BY n.id ASC LIMIT 500",
            )?;
            let nodes: Vec<(String, String, String)> = stmt
                .query_map(rusqlite::params![ws_c], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))?
                .filter_map(Result::ok)
                .collect();
            let mut label: HashMap<String, String> =
                nodes.iter().map(|(id, _, _)| (id.clone(), id.clone())).collect();
            let mut adj: HashMap<String, Vec<String>> = HashMap::new();
            let mut estmt = conn.prepare("SELECT from_node, to_node FROM edges")?;
            for (u, v) in estmt
                .query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?)))?
                .filter_map(Result::ok)
            {
                if label.contains_key(&u) && label.contains_key(&v) {
                    adj.entry(u.clone()).or_default().push(v.clone());
                    adj.entry(v).or_default().push(u);
                }
            }
            for _ in 0..4 {
                for (id, _, _) in &nodes {
                    if let Some(nbrs) = adj.get(id) {
                        let mut freq: BTreeMap<String, usize> = BTreeMap::new();
                        for n in nbrs {
                            if let Some(l) = label.get(n) {
                                *freq.entry(l.clone()).or_insert(0) += 1;
                            }
                        }
                        if let Some((best, _)) = freq.into_iter().max_by_key(|(k, c)| (*c, std::cmp::Reverse(k.clone()))) {
                            label.insert(id.clone(), best);
                        }
                    }
                }
            }
            let mut grouped: BTreeMap<String, Vec<Value>> = BTreeMap::new();
            for (id, title, ntype) in nodes {
                let cid = label.get(&id).cloned().unwrap_or_else(|| id.clone());
                grouped.entry(cid).or_default().push(json!({"id": id, "title": title, "type": ntype}));
            }
            let clusters: Vec<Value> = grouped
                .into_iter()
                .filter(|(_, members)| members.len() >= 2)
                .map(|(cid, members)| {
                    json!({
                        "cluster_id": cid,
                        "size": members.len(),
                        "members": members,
                    })
                })
                .collect();
            Ok(clusters)
        });
    match res {
        Ok(clusters) => ok_json(&json!({"workspace_id": ws, "clusters": clusters})),
        Err(err) => detail_response(500, &err.to_string()),
    }
}

/// P3.1 Knowledge Gaps Detector (isolated entities, missing provenance, low-evidence claims).
async fn brain_knowledge_gaps(State(state): State<BrainState>, req: Request) -> Response {
    let (_email, ws) = match auth_scope(&state, &req, false) {
        Ok(v) => v,
        Err(r) => return r,
    };
    let ws_c = ws.clone();
    let res = state
        .store()
        .with_read_conn(move |conn| {
            let mut stmt = conn.prepare(
                "SELECT n.id, COALESCE(n.title, n.id), n.type,
                        (SELECT COUNT(*) FROM edges e WHERE e.from_node = n.id OR e.to_node = n.id) AS deg,
                        COALESCE(json_extract(n.metadata_json, '$.source_uri'), '') AS src
                 FROM nodes n LEFT JOIN nodes_v2 v ON v.id = n.id
                 WHERE COALESCE(NULLIF(TRIM(v.workspace_id), ''), 'personal') = ?1
                   AND COALESCE(v.superseded_by, '') = ''
                 ORDER BY deg ASC LIMIT 100",
            )?;
            let mut isolated = Vec::new();
            let mut missing_provenance = Vec::new();
            for row in stmt
                .query_map(rusqlite::params![ws_c], |r| {
                    Ok((
                        r.get::<_, String>(0)?,
                        r.get::<_, String>(1)?,
                        r.get::<_, String>(2)?,
                        r.get::<_, i64>(3).unwrap_or(0),
                        r.get::<_, String>(4).unwrap_or_default(),
                    ))
                })?
                .filter_map(Result::ok)
            {
                let (id, title, ntype, deg, src) = row;
                if deg == 0 {
                    isolated.push(json!({"id": id, "title": title, "type": ntype}));
                }
                if src.trim().is_empty() && matches!(ntype.as_str(), "Document" | "Fact" | "Decision") {
                    missing_provenance.push(json!({"id": id, "title": title, "type": ntype}));
                }
            }
            Ok(json!({
                "workspace_id": ws_c,
                "isolated_entities_count": isolated.len(),
                "missing_provenance_count": missing_provenance.len(),
                "isolated_entities": isolated,
                "missing_provenance": missing_provenance,
            }))
        });
    match res {
        Ok(payload) => ok_json(&payload),
        Err(err) => detail_response(500, &err.to_string()),
    }
}

async fn brain_list_corrections(State(state): State<BrainState>, req: Request) -> Response {
    let (_email, ws) = match auth_scope(&state, &req, false) {
        Ok(v) => v,
        Err(r) => return r,
    };
    let q = Query::from_uri(req.uri());
    let status = q.get("status").and_then(|s| CorrectionStatus::parse(s).ok());
    let ws_c = ws.clone();
    let res = state
        .store()
        .with_read_conn(move |conn| list_corrections(conn, &ws_c, status));
    match res {
        Ok(items) => ok_json(&json!({"workspace_id": ws, "external_training_upload_allowed": false, "corrections": items})),
        Err(err) => detail_response(500, &err.to_string()),
    }
}

async fn brain_create_correction(State(state): State<BrainState>, req: Request) -> Response {
    let (email, ws) = match auth_scope(&state, &req, true) {
        Ok(v) => v,
        Err(r) => return r,
    };
    let body = match parse_json_body(req).await {
        Ok(v) => v,
        Err(r) => return r,
    };
    let target_kind = match CorrectionTargetKind::parse(
        body.get("target_kind").and_then(Value::as_str).unwrap_or("fact"),
    ) {
        Ok(k) => k,
        Err(e) => return detail_response(400, &e.to_string()),
    };
    let status = match CorrectionStatus::parse(
        body.get("status").and_then(Value::as_str).unwrap_or("pending"),
    ) {
        Ok(s) => s,
        Err(e) => return detail_response(400, &e.to_string()),
    };
    let req_payload = RecordCorrectionRequest {
        workspace_id: ws,
        target_kind,
        target_id: body.get("target_id").and_then(Value::as_str).unwrap_or("").to_string(),
        status,
        actor: email,
        reason: body.get("reason").and_then(Value::as_str).unwrap_or("").to_string(),
        before_snapshot: body.get("before_snapshot").cloned().unwrap_or_else(|| json!({})),
        after_snapshot: body.get("after_snapshot").cloned().unwrap_or_else(|| json!({})),
    };
    let path = state.store().path().to_path_buf();
    let res = tokio::task::spawn_blocking(move || {
        let mut conn = rusqlite::Connection::open(&path)?;
        record_knowledge_correction(&mut conn, &req_payload)
    })
    .await;
    match res {
        Ok(Ok(rec)) => ok_json(&serde_json::to_value(rec).unwrap_or(Value::Null)),
        Ok(Err(err)) => detail_response(400, &err.to_string()),
        Err(err) => detail_response(500, &err.to_string()),
    }
}

async fn brain_update_correction_status(
    State(state): State<BrainState>,
    Path(correction_id): Path<String>,
    req: Request,
) -> Response {
    if let Err(r) = auth_scope(&state, &req, true) {
        return r;
    }
    let body = match parse_json_body(req).await {
        Ok(v) => v,
        Err(r) => return r,
    };
    let status = match CorrectionStatus::parse(
        body.get("status").and_then(Value::as_str).unwrap_or("confirmed"),
    ) {
        Ok(s) => s,
        Err(e) => return detail_response(400, &e.to_string()),
    };
    let path = state.store().path().to_path_buf();
    let res = tokio::task::spawn_blocking(move || {
        let mut conn = rusqlite::Connection::open(&path)?;
        update_correction_status(&mut conn, &correction_id, status)
    })
    .await;
    match res {
        Ok(Ok(rec)) => ok_json(&serde_json::to_value(rec).unwrap_or(Value::Null)),
        Ok(Err(err)) => detail_response(400, &err.to_string()),
        Err(err) => detail_response(500, &err.to_string()),
    }
}

async fn brain_revert_correction(
    State(state): State<BrainState>,
    Path(correction_id): Path<String>,
    req: Request,
) -> Response {
    if let Err(r) = auth_scope(&state, &req, true) {
        return r;
    }
    let path = state.store().path().to_path_buf();
    let res = tokio::task::spawn_blocking(move || {
        let mut conn = rusqlite::Connection::open(&path)?;
        revert_knowledge_correction(&mut conn, &correction_id)
    })
    .await;
    match res {
        Ok(Ok(rec)) => ok_json(&serde_json::to_value(rec).unwrap_or(Value::Null)),
        Ok(Err(err)) => detail_response(400, &err.to_string()),
        Err(err) => detail_response(500, &err.to_string()),
    }
}

async fn brain_embedding_registry(State(state): State<BrainState>, req: Request) -> Response {
    if let Err(r) = auth_scope(&state, &req, false) {
        return r;
    }
    let path = state.store().path().to_path_buf();
    let res = tokio::task::spawn_blocking(move || {
        let conn = rusqlite::Connection::open(&path)?;
        list_embedding_models(&conn)
    })
    .await;
    match res {
        Ok(Ok(models)) => ok_json(&json!({"models": models})),
        Ok(Err(err)) => detail_response(500, &err.to_string()),
        Err(err) => detail_response(500, &err.to_string()),
    }
}
