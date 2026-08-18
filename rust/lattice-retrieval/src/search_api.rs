//! `latticeai/api/search.py`, natively — and the small HTTP kit the other
//! One Door retrieval families share (WP-R6).
//!
//! Thirteen of the module's seventeen routes come here. The four that do not
//! are the worker's: `POST /api/index/rebuild` and `GET /api/embeddings/
//! {status,providers}` produce or describe *embeddings*, which is the AI
//! worker's box (plan §최종 Python 워커 표면), and `GET /api/index/status`
//! moved next to the queue read in [`lattice_jobs::index_api`] because the SPA
//! asks for the two together.
//!
//! ## Why this module also holds the kit
//!
//! `lib.rs` is the integrator's file, so this package cannot add a third
//! module for the pieces `knowledge_graph_api` needs too. They live here
//! instead, at the top, and the knowledge-graph module reaches them as
//! `crate::search_api::…`. They are:
//!
//! * [`RetrievalApiState`] — the store, the auth state, the workspace-scope
//!   resolver and the worker seam, built once by the host;
//! * [`Query`] / [`Model`] — FastAPI's *report*, not its parser: a missing
//!   query parameter and a missing body field answer 422 with pydantic's
//!   `{"detail":[{"type","loc","msg","input"}]}` entries, because
//!   `frontend/src/api/base.ts` reads that shape;
//! * [`graph_disabled`] / [`value_error`] / [`ok`] — the three refusals and the
//!   one success these families answer with.
//!
//! ## Scoping
//!
//! Python resolves `allowed_workspaces` in one place per router
//! (`search.py:_guarded`, `knowledge_graph.py:_scoped`) and the rule is
//! identical in both: `None` — no scoping at all — unless authentication is on
//! *and* the caller is named, in which case it is the caller's membership set
//! (`runtime/build_phases/web.py:159`). [`RetrievalApiState::scope_for`]
//! is that one place here. With no [`AllowedScopes`] wired the answer is always
//! `None`, which is the documented standalone contract: membership belongs to
//! `lattice-platform`, not to a retrieval crate.

use std::collections::BTreeSet;
use std::sync::Arc;

use axum::http::HeaderMap;
use axum::response::Response;
use lattice_auth::{AuthState, Identity};
use lattice_core::db::{RuntimeConfig, Store};
use lattice_core::graph_write::GraphWriter;
use lattice_core::worker::WorkerSeamClient;

use crate::service::Scope;

// ── the route table ─────────────────────────────────────────────────────────

/// Every `(method, path)` this module mounts, pinned against
/// `rust/fixtures/openapi/knowledge_search.json` by `tests/search_api_contract.rs`.
pub const MOUNTED: &[(&str, &str)] = &[
    ("GET", "/api/graph"),
    ("GET", "/api/graph/node"),
    ("POST", "/api/graph/node"),
    ("GET", "/api/graph/relationship"),
    ("POST", "/api/graph/relationship"),
    ("POST", "/api/search/graph"),
    ("GET", "/api/search/hybrid"),
    ("POST", "/api/search/hybrid"),
    ("GET", "/api/search/image-query"),
    ("GET", "/api/search/keyword"),
    ("POST", "/api/search/keyword"),
    ("GET", "/api/search/vector"),
    ("POST", "/api/search/vector"),
];

/// The literal `runtime/build_phases/foundation.py:468` answers with.
///
/// It is a route-body string rather than a `MESSAGES` id in Python too, so it
/// travels with the routes rather than through `lattice_core::messages` (whose
/// table this work package does not own — see the wiring note).
pub const GRAPH_DISABLED_DETAIL: &str =
    "지식 그래프가 비활성화되어 있습니다. LATTICEAI_ENABLE_GRAPH=true 설정 후 다시 시도해 주세요.";

/// `search_service.IMAGE_FUSION_UNAVAILABLE`.
pub const IMAGE_FUSION_UNAVAILABLE: &str = "no shared-space vision model is configured, so a typed question cannot be scored against image vectors; pictures are still found through their OCR text and captions";
/// `search_service.IMAGE_FUSION_DISABLED`.
pub const IMAGE_FUSION_DISABLED: &str = "automatic image fusion is off for this install (LATTICEAI_TEXT_IMAGE_FUSION); the caller may still supply an image vector";
/// The env var `IMAGE_QUERY_FUSION_GATE` reads.
pub const TEXT_IMAGE_FUSION_ENV: &str = "LATTICEAI_TEXT_IMAGE_FUSION";
/// `image_vectors.DEFAULT_IMAGE_FUSION_WEIGHT`.
pub const DEFAULT_IMAGE_FUSION_WEIGHT: f64 = 0.5;
/// The gate's `detail`, as `search_service.py:36` declares it.
pub const IMAGE_FUSION_GATE_DETAIL: &str = "Typed questions can also be scored against the image index when a shared-space vision model is configured.";

// ── state ───────────────────────────────────────────────────────────────────

/// Which workspaces a caller may read.
///
/// `PLATFORM.allowed_scopes(user)` in Python. Implemented by
/// `lattice-platform` (WP-R1); a host that has not wired one gets the
/// unscoped answer, exactly as a Python install with `workspace_service=None`
/// does.
pub trait AllowedScopes: Send + Sync {
    /// The workspace ids this user is a member of.
    fn allowed_scopes(&self, user: &str) -> BTreeSet<String>;
}

/// Everything the retrieval route families need, built once by the host.
#[derive(Clone)]
pub struct RetrievalApiState {
    auth: Arc<AuthState>,
    store: Option<Arc<Store>>,
    config: RuntimeConfig,
    graph: Option<GraphWriter>,
    seam: Option<WorkerSeamClient>,
    scopes: Option<Arc<dyn AllowedScopes>>,
}

impl std::fmt::Debug for RetrievalApiState {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("RetrievalApiState")
            .field("graph_enabled", &self.store.is_some())
            .field("seam", &self.seam.as_ref().map(WorkerSeamClient::origin))
            .field("scoped", &self.scopes.is_some())
            .finish()
    }
}

impl RetrievalApiState {
    /// A state with the graph switched on.
    ///
    /// `store == None` is `LATTICEAI_ENABLE_GRAPH=false`: every route that
    /// touches the Brain answers 404 with [`GRAPH_DISABLED_DETAIL`], which is
    /// what `_require_graph()` does.
    pub fn new(auth: Arc<AuthState>, store: Option<Arc<Store>>, config: RuntimeConfig) -> Self {
        Self {
            auth,
            store,
            config,
            graph: None,
            seam: None,
            scopes: None,
        }
    }

    /// Attach the native write engine (W3b).
    pub fn with_graph(mut self, graph: GraphWriter) -> Self {
        self.graph = Some(graph);
        self
    }

    /// Attach the worker seam the graph *writes* are delegated through.
    pub fn with_seam(mut self, seam: WorkerSeamClient) -> Self {
        crate::vector_hnsw::bind_worker_origin(seam.origin());
        self.seam = Some(seam);
        self
    }

    /// The native writer, when wired.
    pub fn graph(&self) -> Option<&GraphWriter> {
        self.graph.as_ref()
    }

    /// The worker compute seam, when the host bound one.
    pub fn seam(&self) -> Option<&WorkerSeamClient> {
        self.seam.as_ref()
    }

    /// Attach the membership resolver. Without it every read is unscoped.
    pub fn with_scopes(mut self, scopes: Arc<dyn AllowedScopes>) -> Self {
        self.scopes = Some(scopes);
        self
    }

    /// The shared auth state.
    pub fn auth(&self) -> &Arc<AuthState> {
        &self.auth
    }

    /// Where this process's durable state lives.
    pub fn config(&self) -> &RuntimeConfig {
        &self.config
    }

    /// The graph store, or the 404 `_require_graph()` raises.
    pub fn require_graph(&self) -> Result<&Arc<Store>, Response> {
        self.store.as_ref().ok_or_else(graph_disabled)
    }

    /// The worker seam, or the 503 a missing one has to answer with.
    pub fn require_seam(&self, lang: &str) -> Result<&WorkerSeamClient, Response> {
        self.seam
            .as_ref()
            .ok_or_else(|| http_error(503, "capture.ingestion_disabled", lang))
    }

    /// `_allowed_workspaces_for(user)` — the one scope rule, in one place.
    pub fn scope_for(&self, identity: &Identity) -> Scope {
        Scope {
            allowed_workspaces: self.allowed_workspaces(identity),
            include_legacy_global: false,
        }
    }

    /// The raw membership set, for callers that need it without a [`Scope`].
    pub fn allowed_workspaces(&self, identity: &Identity) -> Option<BTreeSet<String>> {
        if !self.auth.effective_require_auth() || identity.email.is_empty() {
            return None;
        }
        self.scopes
            .as_ref()
            .map(|resolver| resolver.allowed_scopes(&identity.email))
    }

    /// `require_user(request)`, with the caller's scope resolved in one step.
    pub fn guard(&self, headers: &HeaderMap) -> Result<(Identity, Scope), Response> {
        let identity = self.auth.require_user(headers)?;
        let scope = self.scope_for(&identity);
        Ok((identity, scope))
    }

    /// `(require_admin or require_user)(request)` — the curate/promotions gate.
    ///
    /// Python's `require_admin` is `None` in the standalone router, which is
    /// why the expression is an `or` rather than a plain call. Here the admin
    /// guard always exists, and on a loopback owner install it passes for the
    /// same reason `require_user` does.
    pub fn guard_admin(&self, headers: &HeaderMap) -> Result<Identity, Response> {
        self.auth.require_admin(headers)
    }
}

pub(crate) mod handlers;
pub(crate) mod kit;

pub use handlers::router;
pub use kit::{
    detail, engine_error, graph_disabled, http_error, http_error_with, language, ok, optional,
    required, FieldSpec, Kind, Model, Query,
};

#[cfg(test)]
use handlers::{
    image_report, pinned_weights, HYBRID_REQUEST, RELATIONSHIP_REQUEST, SEARCH_REQUEST,
};

#[cfg(test)]
mod tests {
    use super::*;
    use axum::http::StatusCode;
    use axum::response::Response;
    use lattice_core::CoreError;
    use serde_json::json;

    fn body_of(response: Response) -> String {
        let (_, body) = response.into_parts();
        tokio::runtime::Builder::new_current_thread()
            .build()
            .unwrap()
            .block_on(async move {
                String::from_utf8(axum::body::to_bytes(body, 1 << 20).await.unwrap().to_vec())
                    .unwrap()
            })
    }

    #[test]
    fn the_route_table_has_no_duplicates() {
        let mut seen: Vec<(&str, &str)> = MOUNTED.to_vec();
        seen.sort_unstable();
        let before = seen.len();
        seen.dedup();
        assert_eq!(before, seen.len(), "a (method, path) is mounted twice");
        assert_eq!(MOUNTED.len(), 13);
    }

    #[test]
    fn a_missing_query_parameter_is_fastapis_422() {
        let query = Query::parse(Some("limit=5"));
        let refusal = query.require_str("q").unwrap_err();
        assert_eq!(refusal.status(), StatusCode::UNPROCESSABLE_ENTITY);
        assert_eq!(
            body_of(refusal),
            r#"{"detail":[{"type":"missing","loc":["query","q"],"msg":"Field required","input":null}]}"#
        );
    }

    #[test]
    fn query_values_are_percent_decoded_and_last_wins() {
        let query = Query::parse(Some("q=%ED%9A%8C%EC%9D%98%EB%A1%9D&q=second&limit=+7"));
        assert_eq!(query.raw("q"), Some("second"));
        assert_eq!(query.int_or("limit", 30).unwrap(), 7);
        let korean = Query::parse(Some("q=%ED%9A%8C%EC%9D%98%EB%A1%9D"));
        assert_eq!(korean.raw("q"), Some("회의록"));
        // A stray `%` is kept rather than swallowed, as `unquote` keeps it.
        assert_eq!(Query::parse(Some("q=100%")).raw("q"), Some("100%"));
    }

    #[test]
    fn bool_parsing_follows_pydantics_vocabulary() {
        let query = Query::parse(Some("a=True&b=off&c=maybe"));
        assert!(query.bool_or("a", false).unwrap());
        assert!(!query.bool_or("b", true).unwrap());
        assert!(query.bool_or("missing", true).unwrap());
        let refusal = query.bool_or("c", false).unwrap_err();
        assert_eq!(refusal.status(), StatusCode::UNPROCESSABLE_ENTITY);
        assert!(body_of(refusal).contains("bool_parsing"));
    }

    #[test]
    fn int_and_float_parsing_report_the_raw_input() {
        let query = Query::parse(Some("limit=x&min_score=y"));
        assert!(body_of(query.int_or("limit", 30).unwrap_err()).contains(r#""input":"x""#));
        assert!(body_of(query.float_or("min_score", 0.0).unwrap_err()).contains("float_parsing"));
    }

    #[test]
    fn a_missing_body_field_reports_the_whole_body_as_input() {
        let refusal = Model::parse(br#"{"limit": 5}"#, SEARCH_REQUEST).unwrap_err();
        assert_eq!(
            body_of(refusal),
            r#"{"detail":[{"type":"missing","loc":["body","query"],"msg":"Field required","input":{"limit":5}}]}"#
        );
    }

    #[test]
    fn an_empty_body_is_an_empty_object() {
        let model = Model::parse(b"", RELATIONSHIP_REQUEST).unwrap();
        assert_eq!(model.str("query"), "");
        assert_eq!(model.int("limit", 30), 30);
    }

    #[test]
    fn a_blank_required_string_is_string_too_short() {
        let refusal = Model::parse(br#"{"query": ""}"#, SEARCH_REQUEST).unwrap_err();
        let body = body_of(refusal);
        assert!(body.contains("string_too_short"), "{body}");
        assert!(body.contains(r#""min_length":1"#), "{body}");
    }

    #[test]
    fn body_types_coerce_the_way_pydantic_does() {
        let model = Model::parse(
            br#"{"query":"x","limit":"5","min_score":1,"weights":{"keyword":1.0}}"#,
            &[
                required("query", Kind::Str(1)),
                optional("limit", Kind::Int),
                optional("min_score", Kind::Float),
                optional("weights", Kind::Object),
            ],
        )
        .unwrap();
        assert_eq!(model.int("limit", 0), 5);
        assert_eq!(model.float("min_score", 0.0), 1.0);
        assert!(model.get("weights").unwrap().is_object());
        let refusal = Model::parse(br#"{"query":"x","limit":[1]}"#, SEARCH_REQUEST);
        assert!(body_of(refusal.unwrap_err()).contains("int_type"));
    }

    #[test]
    fn a_non_object_body_refuses_the_way_pydantic_does() {
        let refusal = Model::parse(b"[1,2]", SEARCH_REQUEST).unwrap_err();
        assert!(body_of(refusal).contains("model_attributes_type"));
        let broken = Model::parse(b"{nope", SEARCH_REQUEST).unwrap_err();
        assert!(body_of(broken).contains("json_invalid"));
    }

    #[test]
    fn an_explicit_null_optional_stays_absent() {
        let model = Model::parse(br#"{"query":"x","weights":null}"#, HYBRID_REQUEST).unwrap();
        assert!(model.get("weights").is_none());
        assert_eq!(pinned_weights(model.get("weights")).len(), 3);
    }

    #[test]
    fn pinned_weights_default_when_the_caller_sent_nothing_usable() {
        let defaults = pinned_weights(None);
        assert_eq!(defaults["keyword"], json!(0.35));
        assert_eq!(defaults["vector"], json!(0.40));
        assert_eq!(defaults["graph"], json!(0.25));
        assert_eq!(pinned_weights(Some(&json!({}))).len(), 3);
        let pinned = pinned_weights(Some(&json!({"keyword": 0.5})));
        assert_eq!(pinned.len(), 1);
    }

    #[test]
    fn the_graph_disabled_refusal_is_pythons_literal() {
        let refusal = graph_disabled();
        assert_eq!(refusal.status(), StatusCode::NOT_FOUND);
        let body = body_of(refusal);
        assert!(body.contains("LATTICEAI_ENABLE_GRAPH=true"), "{body}");
    }

    #[test]
    fn an_engine_value_error_is_a_404_carrying_its_message() {
        let refusal = engine_error(CoreError::InvalidRequest(
            "graph node not found: x".to_string(),
        ));
        assert_eq!(refusal.status(), StatusCode::NOT_FOUND);
        assert_eq!(body_of(refusal), r#"{"detail":"graph node not found: x"}"#);
        let failure = engine_error(CoreError::DimensionMismatch { left: 1, right: 2 });
        assert_eq!(failure.status(), StatusCode::INTERNAL_SERVER_ERROR);
    }

    #[test]
    fn the_image_report_names_the_reason_it_could_not_run() {
        let report = image_report();
        assert_eq!(report["requested"], json!(true));
        assert_eq!(report["applied"], json!(false));
        assert_eq!(report["weight"], json!(0.5));
        assert!(report["detail"]
            .as_str()
            .unwrap()
            .contains("LATTICEAI_TEXT_IMAGE_FUSION"));
    }
}
