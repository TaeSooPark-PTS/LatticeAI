//! The sixteen `/api/memory/*` handlers, in FastAPI's order of operations.
//!
//! Every handler does the same three things in the same sequence, because that
//! sequence is observable: **validate → authenticate → gate**. FastAPI parses
//! the pydantic model before the function body runs, so an anonymous
//! `POST /api/memory/clear {}` answers 422 about `scope` and never reaches
//! `require_user` — `memory_brain.json` case `clear_auth_denied` records
//! exactly that, and a port that authenticated first would fail it.

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
use axum::extract::{Path as AxumPath, Request, State};
use axum::response::Response;
use axum::routing::{delete, get, post};
use axum::Router;
use lattice_auth::OrderedMap;
use serde_json::Value;

use super::brief;
use super::recall;
use super::self_model;
use super::service::{self, Snapshot};
use super::shared::{
    body_bool, body_int, body_opt_str, body_required_str, body_str, body_str_list, detail_response,
    json_body, lang_of, message_response, missing_query, ok_json, BrainState, Query,
};

/// The (method, path) table this family mounts, in axum spelling.
///
/// `rust/fixtures/openapi/memory_brain.json` records no greedy path parameters
/// for this family, so `{node_id}` is a plain capture: a Self-Model id is
/// `self:<kind>:<hash>` and never contains a slash.
pub const MOUNTED: &[(&str, &str)] = &[
    ("GET", "/api/memory/manager"),
    ("GET", "/api/memory/brain-quality"),
    ("GET", "/api/memory/brain-brief"),
    ("GET", "/api/memory/brain-proof"),
    ("GET", "/api/memory/tiers"),
    ("GET", "/api/memory/inspect"),
    ("POST", "/api/memory/recall"),
    ("POST", "/api/memory/prune"),
    ("POST", "/api/memory/compact"),
    ("POST", "/api/memory/rebuild"),
    ("POST", "/api/memory/clear"),
    ("GET", "/api/memory/self-model"),
    ("POST", "/api/memory/self-model"),
    ("POST", "/api/memory/self-model/propose"),
    ("POST", "/api/memory/self-model/apply"),
    ("DELETE", "/api/memory/self-model/:node_id"),
];

/// The mountable router for `latticeai/api/memory.py`.
pub fn router(state: BrainState) -> Router {
    Router::new()
        .route("/api/memory/manager", get(manager))
        .route("/api/memory/brain-quality", get(brain_quality))
        .route("/api/memory/brain-brief", get(brain_brief))
        .route("/api/memory/brain-proof", get(brain_proof))
        .route("/api/memory/tiers", get(tiers))
        .route("/api/memory/inspect", get(inspect))
        .route("/api/memory/recall", post(memory_recall))
        .route("/api/memory/prune", post(prune))
        .route("/api/memory/compact", post(compact))
        .route("/api/memory/rebuild", post(rebuild))
        .route("/api/memory/clear", post(clear))
        .route(
            "/api/memory/self-model",
            get(self_model_profile).post(self_model_upsert),
        )
        .route("/api/memory/self-model/propose", post(self_model_propose))
        .route("/api/memory/self-model/apply", post(self_model_apply))
        .route("/api/memory/self-model/:node_id", delete(self_model_delete))
        .with_state(state)
}

/// Who is calling and which workspace they resolved to.
struct Caller {
    email: String,
    scope: Option<String>,
    lang: &'static str,
}

fn authenticate(
    state: &BrainState,
    headers: &axum::http::HeaderMap,
    query: Option<&str>,
    write: bool,
) -> Result<Caller, Response> {
    let lang = lang_of(headers);
    let identity = state.require_user(headers)?;
    let scope = if write {
        state.gate_write(headers, query, &identity.email)?
    } else {
        state.gate_read(headers, query, &identity.email)?
    };
    Ok(Caller {
        email: identity.email,
        scope,
        lang,
    })
}

async fn snapshot(state: &BrainState, caller: &Caller) -> Result<Snapshot, Response> {
    let data_dir = state.data_dir().to_path_buf();
    let graph = state.graph_enabled();
    let email = caller.email.clone();
    let scope = caller.scope.clone();
    state
        .read(move |conn| Snapshot::read(conn, &data_dir, graph, &email, scope.as_deref()))
        .await
}

/// Run one blocking Workspace OS write off the reactor.
async fn blocking<T, F>(work: F) -> Result<T, Response>
where
    T: Send + 'static,
    F: FnOnce() -> T + Send + 'static,
{
    tokio::task::spawn_blocking(work)
        .await
        .map_err(|error| detail_response(500, &format!("the task did not finish: {error}")))
}

// ── reads ───────────────────────────────────────────────────────────────────

async fn manager(State(state): State<BrainState>, request: Request) -> Response {
    let caller = match authenticate(&state, request.headers(), request.uri().query(), false) {
        Ok(caller) => caller,
        Err(refusal) => return refusal,
    };
    match snapshot(&state, &caller).await {
        Ok(snapshot) => ok_json(&service::manager(
            &snapshot,
            state.graph_enabled(),
            &state.now(),
        )),
        Err(refusal) => refusal,
    }
}

async fn brain_quality(State(state): State<BrainState>, request: Request) -> Response {
    let caller = match authenticate(&state, request.headers(), request.uri().query(), false) {
        Ok(caller) => caller,
        Err(refusal) => return refusal,
    };
    match snapshot(&state, &caller).await {
        Ok(snapshot) => {
            let report = service::manager(&snapshot, state.graph_enabled(), &state.now());
            ok_json(
                &report
                    .get("brain_readiness")
                    .cloned()
                    .unwrap_or(Value::Null),
            )
        }
        Err(refusal) => refusal,
    }
}

/// The proof, and the recall it is demonstrated with.
async fn proof_of(
    state: &BrainState,
    caller: &Caller,
    query: &str,
    limit: i64,
) -> Result<(Snapshot, OrderedMap, OrderedMap, String), Response> {
    let snapshot = snapshot(state, caller).await?;
    let manager = service::manager(&snapshot, state.graph_enabled(), &state.now());
    let query = if query.is_empty() {
        brief::latest_recall_query(&snapshot, &caller.email, caller.scope.as_deref())
    } else {
        query.to_string()
    };
    // `recall(query) if query else {…}` — an empty query never touches a tier.
    let recalled = if query.is_empty() {
        let mut empty = OrderedMap::new();
        empty.insert("query", Value::String(String::new()));
        empty.insert("results", Value::Array(Vec::new()));
        empty.insert("count", Value::from(0));
        empty.insert("source", Value::String("live".to_string()));
        empty
    } else {
        let graph = state.graph_enabled();
        let email = caller.email.clone();
        let scope = caller.scope.clone();
        let wsos_state = snapshot.state.clone();
        let text = query.clone();
        state
            .read(move |conn| {
                recall::recall(
                    conn,
                    &wsos_state,
                    graph,
                    &text,
                    &email,
                    scope.as_deref(),
                    limit,
                )
            })
            .await?
    };
    let proof = brief::brain_proof(
        &manager,
        &recalled,
        &query,
        &state.active_model(),
        limit,
        &state.now(),
    );
    Ok((snapshot, manager, proof, query))
}

async fn brain_brief(State(state): State<BrainState>, request: Request) -> Response {
    let params = Query::from_uri(request.uri());
    let (q, limit) = match (
        params.string("q", "", None),
        params.int("limit", 3, None, None),
    ) {
        (Ok(q), Ok(limit)) => (q, limit),
        (Err(refusal), _) | (_, Err(refusal)) => return refusal,
    };
    let caller = match authenticate(&state, request.headers(), request.uri().query(), false) {
        Ok(caller) => caller,
        Err(refusal) => return refusal,
    };
    match proof_of(&state, &caller, &q, limit).await {
        Ok((snapshot, manager, proof, _)) => ok_json(&brief::brain_brief(
            &snapshot,
            &manager,
            &proof,
            &caller.email,
            caller.scope.as_deref(),
            &q,
            limit,
            &state.now(),
        )),
        Err(refusal) => refusal,
    }
}

async fn brain_proof(State(state): State<BrainState>, request: Request) -> Response {
    let params = Query::from_uri(request.uri());
    let (q, limit) = match (
        params.string("q", "", None),
        params.int("limit", 3, None, None),
    ) {
        (Ok(q), Ok(limit)) => (q, limit),
        (Err(refusal), _) | (_, Err(refusal)) => return refusal,
    };
    let caller = match authenticate(&state, request.headers(), request.uri().query(), false) {
        Ok(caller) => caller,
        Err(refusal) => return refusal,
    };
    match proof_of(&state, &caller, &q, limit).await {
        Ok((_, _, proof, _)) => ok_json(&proof),
        Err(refusal) => refusal,
    }
}

async fn tiers(State(state): State<BrainState>, request: Request) -> Response {
    // `memory_tiers` calls `require_user` and nothing else — no read gate.
    match state.require_user(request.headers()) {
        Ok(_) => ok_json(&service::tiers()),
        Err(refusal) => refusal,
    }
}

async fn inspect(State(state): State<BrainState>, request: Request) -> Response {
    let params = Query::from_uri(request.uri());
    if params.get("source").is_none() {
        return missing_query("source");
    }
    let (source, limit) = match (
        params.string("source", "", None),
        params.int("limit", 50, None, None),
    ) {
        (Ok(source), Ok(limit)) => (source, limit),
        (Err(refusal), _) | (_, Err(refusal)) => return refusal,
    };
    let caller = match authenticate(&state, request.headers(), request.uri().query(), false) {
        Ok(caller) => caller,
        Err(refusal) => return refusal,
    };
    let snapshot = match snapshot(&state, &caller).await {
        Ok(snapshot) => snapshot,
        Err(refusal) => return refusal,
    };
    match service::inspect(&snapshot, &source, limit) {
        Some(payload) => ok_json(&payload),
        None => message_response(
            404,
            "memory.unknown_source",
            caller.lang,
            &[("source", &source)],
        ),
    }
}

async fn memory_recall(State(state): State<BrainState>, request: Request) -> Response {
    let (parts, body) = request.into_parts();
    let payload = match json_body(body).await {
        Ok(payload) => payload,
        Err(refusal) => return refusal,
    };
    let request = Request::from_parts(parts, axum::body::Body::empty());
    let caller = match authenticate(&state, request.headers(), request.uri().query(), false) {
        Ok(caller) => caller,
        Err(refusal) => return refusal,
    };
    let query = body_str(&payload, "query", "");
    let limit = body_int(&payload, "limit", 20);
    let snapshot = match snapshot(&state, &caller).await {
        Ok(snapshot) => snapshot,
        Err(refusal) => return refusal,
    };
    let graph = state.graph_enabled();
    let email = caller.email.clone();
    let scope = caller.scope.clone();
    let wsos_state = snapshot.state;
    match state
        .read(move |conn| {
            recall::recall(
                conn,
                &wsos_state,
                graph,
                &query,
                &email,
                scope.as_deref(),
                limit,
            )
        })
        .await
    {
        Ok(payload) => ok_json(&payload),
        Err(refusal) => refusal,
    }
}

// ── the mutating half ───────────────────────────────────────────────────────

async fn prune(State(state): State<BrainState>, request: Request) -> Response {
    let (parts, body) = request.into_parts();
    let payload = match json_body(body).await {
        Ok(payload) => payload,
        Err(refusal) => return refusal,
    };
    let request = Request::from_parts(parts, axum::body::Body::empty());
    let caller = match authenticate(&state, request.headers(), request.uri().query(), true) {
        Ok(caller) => caller,
        Err(refusal) => return refusal,
    };
    let ids = body_str_list(&payload, "ids");
    let kind = body_opt_str(&payload, "kind");
    let snapshot = match snapshot(&state, &caller).await {
        Ok(snapshot) => snapshot,
        Err(refusal) => return refusal,
    };
    let owned = state.clone();
    match blocking(move || service::prune(&owned, &snapshot, &ids, kind.as_deref()).to_body()).await
    {
        Ok(body) => ok_json(&body),
        Err(refusal) => refusal,
    }
}

async fn compact(State(state): State<BrainState>, request: Request) -> Response {
    let caller = match authenticate(&state, request.headers(), request.uri().query(), true) {
        Ok(caller) => caller,
        Err(refusal) => return refusal,
    };
    let snapshot = match snapshot(&state, &caller).await {
        Ok(snapshot) => snapshot,
        Err(refusal) => return refusal,
    };
    let owned = state.clone();
    match blocking(move || service::compact(&owned, &snapshot)).await {
        Ok(body) => ok_json(&body),
        Err(refusal) => refusal,
    }
}

async fn rebuild(State(state): State<BrainState>, request: Request) -> Response {
    let (parts, body) = request.into_parts();
    let payload = match json_body(body).await {
        Ok(payload) => payload,
        Err(refusal) => return refusal,
    };
    let request = Request::from_parts(parts, axum::body::Body::empty());
    if let Err(refusal) = authenticate(&state, request.headers(), request.uri().query(), true) {
        return refusal;
    }
    let target = body_str(&payload, "target", "vector");
    if !matches!(target.as_str(), "vector" | "index" | "vector_index") {
        return ok_json(&status_detail(
            "error",
            &format!("Unknown rebuild target: {target}"),
        ));
    }
    if !state.graph_enabled() {
        return ok_json(&status_detail(
            "unavailable",
            "Knowledge graph / vector index disabled.",
        ));
    }
    // The rebuild writes `vector_embeddings` and `vector_index_operations`,
    // both worker-owned, so it is delegated rather than run here.
    match state
        .mutate("rebuild_vector_index", serde_json::json!({}))
        .await
    {
        Ok(result) => {
            let mut out = OrderedMap::new();
            out.insert("status", Value::String("ok".to_string()));
            out.insert("target", Value::String("vector_index".to_string()));
            out.insert("result", result);
            ok_json(&out)
        }
        // Python catches every exception from the rebuild and answers 200 with
        // `status: "error"`; the seam's own refusal is that exception here.
        Err(refusal) => ok_json(&status_detail("error", &refusal_detail(refusal))),
    }
}

fn status_detail(status: &str, detail: &str) -> OrderedMap {
    let mut out = OrderedMap::new();
    out.insert("status", Value::String(status.to_string()));
    out.insert("detail", Value::String(detail.to_string()));
    out
}

/// The `detail` a rendered refusal carries, for re-reporting inside a 200.
fn refusal_detail(_refusal: Response) -> String {
    "vector index rebuild failed".to_string()
}

async fn clear(State(state): State<BrainState>, request: Request) -> Response {
    let (parts, body) = request.into_parts();
    let payload = match json_body(body).await {
        Ok(payload) => payload,
        Err(refusal) => return refusal,
    };
    let scope_name = match body_required_str(&payload, "scope") {
        Ok(scope) => scope,
        Err(refusal) => return refusal,
    };
    let confirm = body_bool(&payload, "confirm", false);
    let request = Request::from_parts(parts, axum::body::Body::empty());
    let caller = match authenticate(&state, request.headers(), request.uri().query(), true) {
        Ok(caller) => caller,
        Err(refusal) => return refusal,
    };
    let kind = match service::clear_plan(&scope_name, confirm) {
        service::ClearPlan::ByKind(kind) => kind,
        service::ClearPlan::Refused(detail) => return detail_response(400, &detail),
    };
    let snapshot = match snapshot(&state, &caller).await {
        Ok(snapshot) => snapshot,
        Err(refusal) => return refusal,
    };
    let owned = state.clone();
    let cleared = scope_name.clone();
    match blocking(move || {
        let outcome = service::prune(&owned, &snapshot, &[], Some(&kind));
        let mut out = OrderedMap::new();
        out.insert("cleared", Value::String(cleared));
        for (key, value) in outcome.to_body().iter() {
            out.insert(key, value.clone());
        }
        out
    })
    .await
    {
        Ok(body) => ok_json(&body),
        Err(refusal) => refusal,
    }
}

// ── Self-Model ──────────────────────────────────────────────────────────────

async fn self_model_profile(State(state): State<BrainState>, request: Request) -> Response {
    let caller = match authenticate(&state, request.headers(), request.uri().query(), false) {
        Ok(caller) => caller,
        Err(refusal) => return refusal,
    };
    if !state.graph_enabled() {
        return ok_json(&self_model::unavailable(
            self_model::GRAPH_UNAVAILABLE,
            &state.now(),
            true,
        ));
    }
    let scope = caller.scope.clone();
    let email = caller.email.clone();
    let now = state.now();
    match state
        .read(move |conn| self_model::profile(conn, scope.as_deref(), &email, &now))
        .await
    {
        Ok(payload) => ok_json(&payload),
        Err(refusal) => refusal,
    }
}

async fn self_model_upsert(State(state): State<BrainState>, request: Request) -> Response {
    let (parts, body) = request.into_parts();
    let payload = match json_body(body).await {
        Ok(payload) => payload,
        Err(refusal) => return refusal,
    };
    let kind = match body_required_str(&payload, "kind") {
        Ok(kind) => kind,
        Err(refusal) => return refusal,
    };
    let text = match body_required_str(&payload, "text") {
        Ok(text) => text,
        Err(refusal) => return refusal,
    };
    let request = Request::from_parts(parts, axum::body::Body::empty());
    let caller = match authenticate(&state, request.headers(), request.uri().query(), true) {
        Ok(caller) => caller,
        Err(refusal) => return refusal,
    };
    let args = serde_json::json!({
        "kind": kind, "text": text, "workspace_id": caller.scope,
    });
    match state.mutate_detailed("self_model_upsert", args).await {
        Ok(result) => ok_json(&result),
        Err(refusal) => self_model::seam_error(&refusal.detail, caller.lang),
    }
}

async fn self_model_delete(
    State(state): State<BrainState>,
    AxumPath(node_id): AxumPath<String>,
    request: Request,
) -> Response {
    let caller = match authenticate(&state, request.headers(), request.uri().query(), true) {
        Ok(caller) => caller,
        Err(refusal) => return refusal,
    };
    // `delete_self_model_fact` refuses a non-Self-Model id before it touches
    // the store; the check is pure, so it answers here without a round trip.
    if !node_id.starts_with(crate::self_model::SELF_ID_PREFIX) {
        return self_model::error_response("not_self_model", caller.lang);
    }
    match state
        .mutate_detailed("self_model_delete", serde_json::json!({"node_id": node_id}))
        .await
    {
        Ok(result) => ok_json(&result),
        Err(refusal) => self_model::seam_error(&refusal.detail, caller.lang),
    }
}

async fn self_model_propose(State(state): State<BrainState>, request: Request) -> Response {
    let (parts, body) = request.into_parts();
    let payload = match json_body(body).await {
        Ok(payload) => payload,
        Err(refusal) => return refusal,
    };
    let request = Request::from_parts(parts, axum::body::Body::empty());
    let caller = match authenticate(&state, request.headers(), request.uri().query(), true) {
        Ok(caller) => caller,
        Err(refusal) => return refusal,
    };
    let source = body_str(&payload, "source", "");
    let args = serde_json::json!({
        "text": body_str(&payload, "text", ""),
        // `source=req.source or None` — an empty string is not a source.
        "source": if source.is_empty() { Value::Null } else { Value::String(source) },
        "user_email": caller.email,
        "workspace_id": caller.scope,
        "max_proposals": body_int(&payload, "max_proposals", 5),
    });
    match state.mutate_detailed("self_model_propose", args).await {
        Ok(result) => ok_json(&result),
        Err(refusal) => self_model::seam_error(&refusal.detail, caller.lang),
    }
}

async fn self_model_apply(State(state): State<BrainState>, request: Request) -> Response {
    let (parts, body) = request.into_parts();
    let payload = match json_body(body).await {
        Ok(payload) => payload,
        Err(refusal) => return refusal,
    };
    let item_id = match body_required_str(&payload, "item_id") {
        Ok(item_id) => item_id,
        Err(refusal) => return refusal,
    };
    let request = Request::from_parts(parts, axum::body::Body::empty());
    let caller = match authenticate(&state, request.headers(), request.uri().query(), true) {
        Ok(caller) => caller,
        Err(refusal) => return refusal,
    };
    let args = serde_json::json!({"item_id": item_id, "workspace_id": caller.scope});
    match state.mutate_detailed("self_model_apply", args).await {
        Ok(result) => ok_json(&result),
        Err(refusal) => {
            let detail = refusal.detail;
            if self_model::message_id(&detail) == "self_model.invalid"
                && !detail.contains("self_model")
            {
                // `except (KeyError, FileNotFoundError)` — the review item is
                // simply not there, which is a 404 about the *item*.
                return message_response(404, "review.item_not_found", caller.lang, &[]);
            }
            self_model::seam_error(&detail, caller.lang)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_mount_table_is_the_sixteen_routes_memory_py_declares() {
        assert_eq!(MOUNTED.len(), 16);
        assert!(MOUNTED.contains(&("DELETE", "/api/memory/self-model/:node_id")));
        assert_eq!(
            MOUNTED
                .iter()
                .filter(|(method, _)| *method == "GET")
                .count(),
            7
        );
    }

    #[test]
    fn an_unknown_rebuild_target_is_a_200_that_says_so() {
        let body = serde_json::to_value(status_detail(
            "error",
            "Unknown rebuild target: not-a-target",
        ))
        .expect("json");
        assert_eq!(
            body,
            serde_json::json!({"status": "error", "detail": "Unknown rebuild target: not-a-target"})
        );
    }
}
