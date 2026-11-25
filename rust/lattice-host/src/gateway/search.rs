use std::path::Path;
use std::sync::Arc;

use axum::extract::{Request, State};
use axum::http::{Method, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::Json;
use lattice_core::{open_read_only, LocalEmbeddingModel};
use lattice_retrieval::hybrid::{hybrid_search, HybridOptions};
use lattice_retrieval::keyword::search as keyword_search;
use lattice_retrieval::routes::{brain_not_found, naive_local_now};
use lattice_retrieval::vector::vector_search;
use serde_json::Value;

use super::params::{ParamError, SearchParams, MAX_BODY_BYTES};
use super::GatewayState;

pub const DEFAULT_TOP_K: i64 = 20;
pub const DEFAULT_LIMIT: i64 = 30;
pub const MAX_LIMIT: i64 = 100;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Engine {
    Hybrid,
    Keyword,
    Vector,
}

impl Engine {
    pub fn path(self) -> &'static str {
        match self {
            Engine::Hybrid => "hybrid",
            Engine::Keyword => "keyword",
            Engine::Vector => "vector",
        }
    }
}

#[derive(Debug, Clone)]
enum Plan {
    Hybrid {
        query: String,
        options: Box<HybridOptions>,
    },
    Keyword {
        query: String,
        limit: i64,
    },
    Vector {
        query: String,
        limit: i64,
        min_score: f64,
    },
}

impl Plan {
    fn build(engine: Engine, params: &SearchParams) -> Result<Self, ParamError> {
        let query = params.required_text(&["query", "q"])?;
        let limit = params.optional_int("limit", 1, MAX_LIMIT)?;
        let min_score = params.optional_float("min_score", 0.0, 1.0)?;
        Ok(match engine {
            Engine::Hybrid => {
                let top_k = params.optional_int("top_k", 1, MAX_LIMIT)?;
                let now = params.optional_instant("now")?;
                let floor = params.optional_float("min_vector_score", 0.0, 1.0)?;
                Plan::Hybrid {
                    query,
                    options: Box::new(HybridOptions {
                        top_k: top_k.or(limit).unwrap_or(DEFAULT_TOP_K),
                        alpha: params.optional_float("alpha", 0.0, 1.0)?,
                        min_vector_score: floor.or(min_score).unwrap_or(0.0),
                        allowed_workspaces: None,
                        include_legacy_global: false,
                        now_secs: now.unwrap_or_else(naive_local_now),
                        ..HybridOptions::default()
                    }),
                }
            }
            Engine::Keyword => Plan::Keyword {
                query,
                limit: limit.unwrap_or(DEFAULT_LIMIT),
            },
            Engine::Vector => Plan::Vector {
                query,
                limit: limit.unwrap_or(DEFAULT_LIMIT),
                min_score: min_score.unwrap_or(0.0),
            },
        })
    }

    fn run(self, db: &Path) -> Result<Value, String> {
        let conn = open_read_only(db).map_err(|err| err.to_string())?;
        let model = LocalEmbeddingModel::from_env();
        match self {
            Plan::Hybrid { query, options } => hybrid_search(&conn, &model, &query, &options),
            Plan::Keyword { query, limit } => keyword_search(&conn, &query, limit, None, false),
            Plan::Vector {
                query,
                limit,
                min_score,
            } => vector_search(&conn, &model, &query, limit, min_score),
        }
        .map_err(|err| err.to_string())
    }
}

pub async fn hybrid(state: State<Arc<GatewayState>>, request: Request) -> Response {
    handle(state, request, Engine::Hybrid).await
}

pub async fn keyword(state: State<Arc<GatewayState>>, request: Request) -> Response {
    handle(state, request, Engine::Keyword).await
}

pub async fn vector(state: State<Arc<GatewayState>>, request: Request) -> Response {
    handle(state, request, Engine::Vector).await
}

async fn handle(
    State(state): State<Arc<GatewayState>>,
    request: Request,
    engine: Engine,
) -> Response {
    let (parts, body) = request.into_parts();
    let mut params = match SearchParams::from_uri(&parts.uri) {
        Ok(params) => params,
        Err(err) => return err.into_response(),
    };
    if parts.method == Method::POST {
        let bytes = match axum::body::to_bytes(body, MAX_BODY_BYTES).await {
            Ok(bytes) => bytes,
            Err(err) => {
                return ParamError::new("body", format!("could not read the request body: {err}"))
                    .into_response()
            }
        };
        if let Err(err) = params.merge_json(&bytes) {
            return err.into_response();
        }
    }
    let plan = match Plan::build(engine, &params) {
        Ok(plan) => plan,
        Err(err) => return err.into_response(),
    };

    let db = state.db_path();
    if !db.is_file() {
        return brain_not_found(&db);
    }
    match tokio::task::spawn_blocking(move || plan.run(&db)).await {
        Ok(Ok(value)) => Json(value).into_response(),
        Ok(Err(detail)) => search_failed(engine, detail),
        Err(err) => search_failed(engine, format!("the search task did not finish: {err}")),
    }
}

fn search_failed(engine: Engine, detail: String) -> Response {
    (
        StatusCode::INTERNAL_SERVER_ERROR,
        Json(serde_json::json!({
            "error": "search_failed",
            "engine": engine.path(),
            "detail": detail,
        })),
    )
        .into_response()
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::http::Uri;

    fn params(query: &str) -> SearchParams {
        let uri: Uri = format!("/rust/search/hybrid{query}").parse().expect("uri");
        SearchParams::from_uri(&uri).expect("query string")
    }

    fn hybrid_options(query: &str) -> Box<HybridOptions> {
        match Plan::build(Engine::Hybrid, &params(query)).expect("plan") {
            Plan::Hybrid { options, .. } => options,
            other => panic!("expected a hybrid plan, got {other:?}"),
        }
    }

    #[test]
    fn engine_paths_match_the_mount_points() {
        assert_eq!(Engine::Hybrid.path(), "hybrid");
        assert_eq!(Engine::Keyword.path(), "keyword");
        assert_eq!(Engine::Vector.path(), "vector");
    }

    #[test]
    fn the_hybrid_defaults_are_pythons_defaults() {
        let options = hybrid_options("?q=hello");
        assert_eq!(options.top_k, DEFAULT_TOP_K);
        assert_eq!(options.alpha, None);
        assert_eq!(options.min_vector_score, 0.0);
        assert!(!options.include_legacy_global);
    }

    #[test]
    fn the_owner_path_applies_no_workspace_scoping() {
        assert!(
            hybrid_options("?q=hello").allowed_workspaces.is_none(),
            "loopback owner requests must see the whole brain, as in Python"
        );
    }

    #[test]
    fn an_absent_now_falls_back_to_the_system_clock() {
        let options = hybrid_options("?q=hello");
        assert!(
            options.now_secs > 1_700_000_000.0,
            "now_secs came from the clock, not from a zero default"
        );
        assert_eq!(hybrid_options("?q=hi&now=42").now_secs, 42.0);
    }

    #[test]
    fn top_k_falls_back_to_limit_so_both_spellings_work() {
        assert_eq!(hybrid_options("?q=hi&limit=7").top_k, 7);
        assert_eq!(hybrid_options("?q=hi&top_k=5&limit=7").top_k, 5);
    }

    #[test]
    fn the_single_lanes_default_to_thirty() {
        match Plan::build(Engine::Keyword, &params("?q=hi")).expect("plan") {
            Plan::Keyword { limit, .. } => assert_eq!(limit, DEFAULT_LIMIT),
            other => panic!("{other:?}"),
        }
        match Plan::build(Engine::Vector, &params("?q=hi&min_score=0.5")).expect("plan") {
            Plan::Vector {
                limit, min_score, ..
            } => {
                assert_eq!(limit, DEFAULT_LIMIT);
                assert_eq!(min_score, 0.5);
            }
            other => panic!("{other:?}"),
        }
    }

    #[test]
    fn both_spellings_of_the_vector_floor_are_accepted() {
        assert_eq!(hybrid_options("?q=hi&min_score=0.4").min_vector_score, 0.4);
        assert_eq!(
            hybrid_options("?q=hi&min_vector_score=0.95").min_vector_score,
            0.95
        );
        assert_eq!(
            hybrid_options("?q=hi&min_vector_score=0.95&min_score=0.1").min_vector_score,
            0.95,
            "the engine's own name wins when both are sent"
        );
    }

    #[test]
    fn every_bad_field_is_named_in_the_rejection() {
        for (query, field) in [
            ("", "query"),
            ("?q=hi&top_k=0", "top_k"),
            ("?q=hi&alpha=9", "alpha"),
            ("?q=hi&min_score=-1", "min_score"),
            ("?q=hi&min_vector_score=2", "min_vector_score"),
            ("?q=hi&now=soon", "now"),
        ] {
            let err = Plan::build(Engine::Hybrid, &params(query)).expect_err("must reject");
            assert_eq!(err.field, field, "for {query}");
        }
        for engine in [Engine::Keyword, Engine::Vector] {
            let err = Plan::build(engine, &params("?q=hi&limit=101")).expect_err("must reject");
            assert_eq!(err.field, "limit");
        }
    }

    #[test]
    fn a_missing_store_is_a_404_that_names_the_path() {
        let response = brain_not_found(Path::new("/nope/knowledge_graph.sqlite"));
        assert_eq!(response.status(), StatusCode::NOT_FOUND);
    }

    #[test]
    fn an_engine_failure_is_a_500_that_names_the_lane() {
        let response = search_failed(Engine::Vector, "boom".into());
        assert_eq!(response.status(), StatusCode::INTERNAL_SERVER_ERROR);
    }

    #[test]
    fn running_against_a_missing_file_is_an_error_not_a_new_database() {
        let dir = tempfile::tempdir().expect("tempdir");
        let plan = Plan::Keyword {
            query: "hi".into(),
            limit: 5,
        };
        let err = plan
            .run(&dir.path().join("knowledge_graph.sqlite"))
            .expect_err("no store");
        assert!(!err.is_empty());
    }
}
