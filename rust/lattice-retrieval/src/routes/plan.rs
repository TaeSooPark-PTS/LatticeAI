//! Turning a validated parameter bag into one runnable engine call.
//!
//! Validation and execution are split on purpose: [`Plan::build`] never touches
//! the database and [`Plan::run`] never touches HTTP, so a bad request is
//! answered before a file is opened and the blocking half can be handed to a
//! worker thread whole.
//!
//! Every plan resolves scoping the same way — `allowed_workspaces = None`, the
//! trusted-local-owner branch. That is not a fail-open oversight: it is the
//! branch the Python endpoints take for a loopback caller on the owner's own
//! machine, and these routes are meant to be mounted on a gateway that binds
//! nowhere else. `user_email` and `include_legacy_global` remain available for a
//! caller that wants to *narrow* the history it reads.

use std::path::Path;

use lattice_core::{open_read_only, CoreError, LocalEmbeddingModel};
use serde_json::{Map, Value};

use super::params::{ParamError, RequestParams};
use crate::context::{assemble_context, ContextRequest, RecentRequest};
use crate::docgen_context::{
    retrieve_context_for_generation, DocumentContextRequest, DEFAULT_DOCUMENT_CONTEXT_BUDGET,
};
use crate::graph_reads::{relationship_search, traverse, RelationshipQuery, TraverseOptions};
use crate::history::{
    conversation_messages, group_conversations, history, search_history, HistoryScope,
};
use crate::self_model::DEFAULT_SUMMARY_TOKENS;
use crate::service::{graph_search, GraphSearchOptions, Scope};
use crate::service_hybrid::{service_hybrid_search, ServiceHybridOptions};

/// Ceiling every "how many rows" parameter is checked against.
pub const MAX_LIMIT: i64 = 500;

/// Which native route a request landed on.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Endpoint {
    /// Three-channel service fusion.
    ServiceHybrid,
    /// Keyword hits expanded through the graph.
    GraphSearch,
    /// Edges, filtered and ordered.
    GraphRelationships,
    /// Undirected breadth-first walk from one seed.
    GraphTraverse,
    /// Raw chronological conversation history.
    History,
    /// History grouped into conversations.
    Conversations,
    /// One conversation's messages.
    ConversationMessages,
    /// Substring search over the history, grouped.
    HistorySearch,
    /// The budgeted context assembly.
    ContextAssemble,
    /// The document-generation context: hybrid document search, a hop-labelled
    /// traversal around it, and the budgeted markdown a writer prompt is given.
    ContextDocument,
}

impl Endpoint {
    /// The path this endpoint is mounted on.
    pub fn path(self) -> &'static str {
        match self {
            Endpoint::ServiceHybrid => "/rust/search/service-hybrid",
            Endpoint::GraphSearch => "/rust/graph/search",
            Endpoint::GraphRelationships => "/rust/graph/relationships",
            Endpoint::GraphTraverse => "/rust/graph/traverse",
            Endpoint::History => "/rust/history",
            Endpoint::Conversations => "/rust/history/conversations",
            Endpoint::ConversationMessages => "/rust/history/conversations/{id}",
            Endpoint::HistorySearch => "/rust/history/search",
            Endpoint::ContextAssemble => "/rust/context/assemble",
            Endpoint::ContextDocument => "/rust/context/document",
        }
    }
}

/// A fully validated request, ready to run without touching HTTP again.
#[derive(Debug)]
pub enum Plan {
    ServiceHybrid {
        query: String,
        options: Box<ServiceHybridOptions>,
    },
    GraphSearch {
        query: String,
        options: GraphSearchOptions,
    },
    Relationships(Box<RelationshipQuery>),
    Traverse {
        node_id: String,
        options: TraverseOptions,
    },
    History {
        conversation_id: Option<String>,
        limit: Option<i64>,
        scope: HistoryScope,
    },
    Conversations {
        scope: HistoryScope,
    },
    ConversationMessages {
        conversation_id: String,
        scope: HistoryScope,
    },
    HistorySearch {
        query: String,
        limit: i64,
        scope: HistoryScope,
    },
    ContextAssemble(Box<ContextRequest>),
    ContextDocument(Box<DocumentContextRequest>),
}

/// The history scope a loopback owner gets, optionally narrowed by the caller.
fn history_scope(params: &RequestParams) -> Result<HistoryScope, ParamError> {
    Ok(HistoryScope {
        user_email: params.optional_text("user_email")?,
        // Never taken from the request: the owner reads their own machine.
        allowed_workspaces: None,
        include_legacy_global: params
            .optional_bool("include_legacy_global")?
            .unwrap_or(true),
    })
}

fn recent_request(value: Option<Value>) -> Result<Option<RecentRequest>, ParamError> {
    let bad = |detail: &str| ParamError::new("recent", detail.to_string());
    match value {
        None | Some(Value::Null) | Some(Value::Bool(false)) => Ok(None),
        Some(Value::Bool(true)) => Ok(Some(RecentRequest::default())),
        Some(Value::Object(map)) => {
            let text = |key: &str| -> Result<Option<String>, ParamError> {
                match map.get(key) {
                    None | Some(Value::Null) => Ok(None),
                    Some(Value::String(value)) => Ok(Some(value.clone())),
                    Some(_) => Err(ParamError::new(
                        "recent",
                        format!("recent.{key} must be a string"),
                    )),
                }
            };
            let limit = match map.get("limit") {
                None | Some(Value::Null) => None,
                Some(value) => Some(
                    value
                        .as_i64()
                        .filter(|value| (0..=MAX_LIMIT).contains(value))
                        .ok_or_else(|| bad("recent.limit must be an integer between 0 and 500"))?,
                ),
            };
            let images = match map.get("images") {
                None | Some(Value::Null) => None,
                Some(Value::Bool(flag)) => Some(*flag),
                Some(_) => return Err(bad("recent.images must be a boolean")),
            };
            Ok(Some(RecentRequest {
                limit,
                include_image_missing_replies: images,
                user_email: text("user_email")?,
                conversation_id: text("conversation_id")?,
                workspace_id: text("workspace_id")?,
            }))
        }
        Some(_) => Err(bad("recent must be an object or a boolean")),
    }
}

impl Plan {
    /// Validate `params` for `endpoint`, or say exactly which field is wrong.
    pub fn build(endpoint: Endpoint, params: &RequestParams) -> Result<Self, ParamError> {
        match endpoint {
            Endpoint::ServiceHybrid => Ok(Plan::ServiceHybrid {
                query: params.required_text(&["query", "q"])?,
                options: Box::new(ServiceHybridOptions {
                    limit: params.optional_int("limit", 1, 100)?.unwrap_or(30),
                    keyword_limit: params.optional_int("keyword_limit", 1, 100)?.unwrap_or(30),
                    vector_limit: params.optional_int("vector_limit", 1, 100)?.unwrap_or(30),
                    graph_limit: params.optional_int("graph_limit", 1, 100)?.unwrap_or(30),
                    weights: params.optional_weights("weights")?,
                    scope: Scope::default(),
                    now_secs: params
                        .optional_instant("now")?
                        .unwrap_or_else(super::naive_local_now),
                }),
            }),
            Endpoint::GraphSearch => Ok(Plan::GraphSearch {
                query: params.required_text(&["query", "q"])?,
                options: GraphSearchOptions {
                    limit: params.optional_int("limit", 1, 100)?.unwrap_or(30),
                    // -1 is how "no expansion" is spelled: the engine reads a
                    // zero as Python does, i.e. as "unset, use the default".
                    expand_depth: params.optional_int("expand_depth", -1, 3)?.unwrap_or(1),
                    scope: Scope::default(),
                },
            }),
            Endpoint::GraphRelationships => Ok(Plan::Relationships(Box::new(RelationshipQuery {
                query: params.optional_text("query")?.unwrap_or_default(),
                node_id: params.optional_text("node_id")?.unwrap_or_default(),
                relationship_type: params
                    .optional_text("relationship_type")?
                    .unwrap_or_default(),
                limit: params.optional_int("limit", 1, 200)?.unwrap_or(30),
                allowed_workspaces: None,
                include_legacy_global: false,
            }))),
            Endpoint::GraphTraverse => Ok(Plan::Traverse {
                node_id: params.required_text(&["node_id", "id"])?,
                options: TraverseOptions {
                    // -1 is how "the seed alone" is spelled; see `expand_depth`.
                    depth: params.optional_int("depth", -1, 4)?.unwrap_or(1),
                    limit: params.optional_int("limit", 1, MAX_LIMIT)?.unwrap_or(100),
                    allowed_workspaces: None,
                    include_legacy_global: false,
                },
            }),
            Endpoint::History => Ok(Plan::History {
                conversation_id: params.optional_text("conversation_id")?,
                limit: params.optional_int("limit", 1, 10_000)?,
                scope: history_scope(params)?,
            }),
            Endpoint::Conversations => Ok(Plan::Conversations {
                scope: history_scope(params)?,
            }),
            Endpoint::ConversationMessages => Ok(Plan::ConversationMessages {
                conversation_id: params.required_text(&["conversation_id"])?,
                scope: history_scope(params)?,
            }),
            Endpoint::HistorySearch => Ok(Plan::HistorySearch {
                query: params.required_text(&["q", "query"])?,
                limit: params.optional_int("limit", 1, MAX_LIMIT)?.unwrap_or(30),
                scope: history_scope(params)?,
            }),
            Endpoint::ContextAssemble => Ok(Plan::ContextAssemble(Box::new(ContextRequest {
                query: params.required_text(&["query", "q"])?,
                budget: params.optional_int("budget", 1, 1_000_000)?.unwrap_or(2000),
                memory_limit: params.optional_int("memory_limit", 0, 100)?.unwrap_or(5),
                knowledge_limit: params.optional_int("knowledge_limit", 1, 100)?.unwrap_or(5),
                memories: params.optional_json("memories")?,
                artifacts: params.optional_json("artifacts")?,
                knowledge: params.optional_bool("knowledge")?.unwrap_or(true),
                notes: params.optional_text("notes")?,
                recent: recent_request(params.optional_json("recent")?)?,
                user_email: params.optional_text("user_email")?,
                conversation_id: params.optional_text("conversation_id")?,
                workspace_id: params.optional_text("workspace_id")?,
                now_secs: params
                    .optional_instant("now")?
                    .unwrap_or_else(super::naive_local_now),
            }))),
            Endpoint::ContextDocument => {
                Ok(Plan::ContextDocument(Box::new(DocumentContextRequest {
                    query: params.required_text(&["query", "q"])?,
                    // The engine clamps to 1..=50; this front door refuses
                    // outside its own range rather than answering a different
                    // question than the one that was asked.
                    max_results: params.optional_int("max_results", 1, 50)?.unwrap_or(10),
                    max_hops: params.optional_int("max_hops", 0, 4)?.unwrap_or(2),
                    // Zero is admitted: it is how "spend the profile's own
                    // ceiling and never trim the knowledge" is spelled.
                    budget: params
                        .optional_int("budget", 0, 1_000_000)?
                        .unwrap_or(DEFAULT_DOCUMENT_CONTEXT_BUDGET),
                    include_self_model: params.optional_bool("include_self_model")?.unwrap_or(true),
                    self_model_tokens: params
                        .optional_int("self_model_tokens", 0, 10_000)?
                        .unwrap_or(DEFAULT_SUMMARY_TOKENS),
                    // Loopback trust, as everywhere else in this router.
                    scope: Scope::default(),
                    now_secs: params
                        .optional_instant("now")?
                        .unwrap_or_else(super::naive_local_now),
                })))
            }
        }
    }

    /// Run the plan against the store. Synchronous — call it on a blocking task.
    pub fn run(self, db: &Path) -> Result<Value, CoreError> {
        let conn = open_read_only(db)?;
        match self {
            Plan::ServiceHybrid { query, options } => {
                let model = LocalEmbeddingModel::from_env();
                service_hybrid_search(&conn, &model, &query, &options)
            }
            Plan::GraphSearch { query, options } => graph_search(&conn, &query, &options),
            Plan::Relationships(request) => relationship_search(&conn, &request),
            Plan::Traverse { node_id, options } => traverse(&conn, &node_id, &options),
            Plan::History {
                conversation_id,
                limit,
                scope,
            } => {
                let rows = history(&conn, conversation_id.as_deref(), limit, &scope)?;
                Ok(Value::Array(rows))
            }
            Plan::Conversations { scope } => {
                let rows = history(&conn, None, None, &scope)?;
                Ok(Value::Array(group_conversations(&rows)))
            }
            Plan::ConversationMessages {
                conversation_id,
                scope,
            } => {
                let rows = history(&conn, None, None, &scope)?;
                let messages = conversation_messages(&rows, &conversation_id);
                let mut out = Map::new();
                out.insert("id".into(), Value::String(conversation_id));
                out.insert("messages".into(), Value::Array(messages));
                Ok(Value::Object(out))
            }
            Plan::HistorySearch {
                query,
                limit,
                scope,
            } => {
                let rows = history(&conn, None, None, &scope)?;
                let mut out = Map::new();
                out.insert(
                    "results".into(),
                    Value::Array(search_history(&rows, &query, limit)),
                );
                out.insert("query".into(), Value::String(query));
                Ok(Value::Object(out))
            }
            Plan::ContextAssemble(request) => {
                let model = LocalEmbeddingModel::from_env();
                assemble_context(&conn, &model, &request)
            }
            Plan::ContextDocument(request) => retrieve_context_for_generation(&conn, &request),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::http::Uri;
    use serde_json::json;

    fn params(path: &str, query: &str) -> RequestParams {
        let uri: Uri = format!("{path}{query}").parse().expect("uri");
        RequestParams::from_uri(&uri).expect("query string")
    }

    fn bag(query: &str) -> RequestParams {
        params("/rust/graph/search", query)
    }

    #[test]
    fn every_endpoint_names_its_mount_point() {
        let mounts: Vec<&str> = [
            Endpoint::ServiceHybrid,
            Endpoint::GraphSearch,
            Endpoint::GraphRelationships,
            Endpoint::GraphTraverse,
            Endpoint::History,
            Endpoint::Conversations,
            Endpoint::ConversationMessages,
            Endpoint::HistorySearch,
            Endpoint::ContextAssemble,
            Endpoint::ContextDocument,
        ]
        .iter()
        .map(|endpoint| endpoint.path())
        .collect();
        assert_eq!(mounts.len(), 10);
        assert!(mounts.iter().all(|path| path.starts_with("/rust/")));
        assert_eq!(Endpoint::History.path(), "/rust/history");
        assert_eq!(Endpoint::ServiceHybrid, Endpoint::ServiceHybrid);
    }

    #[test]
    fn defaults_are_the_python_defaults() {
        let Plan::ServiceHybrid { options, query } =
            Plan::build(Endpoint::ServiceHybrid, &bag("?q=hi")).expect("plan")
        else {
            panic!("wrong plan");
        };
        assert_eq!(query, "hi");
        assert_eq!(options.limit, 30);
        assert_eq!(options.keyword_limit, 30);
        assert!(options.weights.is_none());
        assert!(options.scope.allowed_workspaces.is_none());
        assert!(options.now_secs > 1_700_000_000.0, "the clock, not zero");

        let Plan::GraphSearch { options, .. } =
            Plan::build(Endpoint::GraphSearch, &bag("?q=hi")).expect("plan")
        else {
            panic!("wrong plan");
        };
        assert_eq!(options.expand_depth, 1);
        assert_eq!(options.limit, 30);

        let Plan::Traverse { options, node_id } =
            Plan::build(Endpoint::GraphTraverse, &bag("?node_id=a")).expect("plan")
        else {
            panic!("wrong plan");
        };
        assert_eq!(node_id, "a");
        assert_eq!(options.depth, 1);
        assert_eq!(options.limit, 100);
        assert!(options.allowed_workspaces.is_none());

        let Plan::ContextDocument(request) =
            Plan::build(Endpoint::ContextDocument, &bag("?q=hi")).expect("plan")
        else {
            panic!("wrong plan");
        };
        assert_eq!(request.max_results, 10);
        assert_eq!(request.max_hops, 2);
        assert_eq!(request.budget, 2000);
        assert_eq!(request.self_model_tokens, 200);
        assert!(
            request.include_self_model,
            "the profile rides along by default"
        );
        assert!(request.scope.allowed_workspaces.is_none());
        // Zero budget and zero hops are legal answers, not typos.
        let Plan::ContextDocument(edge) = Plan::build(
            Endpoint::ContextDocument,
            &bag("?q=hi&budget=0&max_hops=0&include_self_model=no"),
        )
        .expect("plan") else {
            panic!("wrong plan");
        };
        assert_eq!((edge.budget, edge.max_hops), (0, 0));
        assert!(!edge.include_self_model);
    }

    #[test]
    fn the_history_scope_is_the_owners_unless_narrowed() {
        let Plan::History {
            scope,
            limit,
            conversation_id,
        } = Plan::build(Endpoint::History, &bag("")).expect("plan")
        else {
            panic!("wrong plan");
        };
        assert!(scope.user_email.is_none());
        assert!(scope.allowed_workspaces.is_none());
        assert!(scope.include_legacy_global, "the Python default is true");
        assert!(limit.is_none());
        assert!(conversation_id.is_none());

        let Plan::Conversations { scope } = Plan::build(
            Endpoint::Conversations,
            &bag("?user_email=a@x&include_legacy_global=0"),
        )
        .expect("plan") else {
            panic!("wrong plan");
        };
        assert_eq!(scope.user_email.as_deref(), Some("a@x"));
        assert!(!scope.include_legacy_global);
        assert!(
            scope.allowed_workspaces.is_none(),
            "a workspace filter is never taken from the request"
        );
    }

    #[test]
    fn every_bad_field_is_named_in_the_rejection() {
        for (endpoint, query, field) in [
            (Endpoint::ServiceHybrid, "", "query"),
            (Endpoint::ServiceHybrid, "?q=hi&limit=0", "limit"),
            (
                Endpoint::ServiceHybrid,
                "?q=hi&graph_limit=101",
                "graph_limit",
            ),
            (Endpoint::ServiceHybrid, "?q=hi&now=soon", "now"),
            (
                Endpoint::GraphSearch,
                "?q=hi&expand_depth=4",
                "expand_depth",
            ),
            (Endpoint::GraphRelationships, "?limit=201", "limit"),
            (Endpoint::GraphTraverse, "", "node_id"),
            (Endpoint::GraphTraverse, "?node_id=a&depth=5", "depth"),
            (Endpoint::GraphTraverse, "?node_id=a&limit=501", "limit"),
            (Endpoint::History, "?limit=0", "limit"),
            (
                Endpoint::History,
                "?include_legacy_global=maybe",
                "include_legacy_global",
            ),
            (Endpoint::ConversationMessages, "", "conversation_id"),
            (Endpoint::HistorySearch, "", "q"),
            (Endpoint::ContextAssemble, "?q=hi&budget=0", "budget"),
            (
                Endpoint::ContextAssemble,
                "?q=hi&knowledge=perhaps",
                "knowledge",
            ),
            (Endpoint::ContextAssemble, "?q=hi&memories=oops", "memories"),
            (Endpoint::ContextDocument, "", "query"),
            (
                Endpoint::ContextDocument,
                "?q=hi&max_results=51",
                "max_results",
            ),
            (Endpoint::ContextDocument, "?q=hi&max_hops=5", "max_hops"),
            (Endpoint::ContextDocument, "?q=hi&budget=-1", "budget"),
            (
                Endpoint::ContextDocument,
                "?q=hi&include_self_model=perhaps",
                "include_self_model",
            ),
            (
                Endpoint::ContextDocument,
                "?q=hi&self_model_tokens=10001",
                "self_model_tokens",
            ),
        ] {
            let err = Plan::build(endpoint, &bag(query)).expect_err("must reject");
            assert_eq!(err.field, field, "for {} {query}", endpoint.path());
        }
    }

    #[test]
    fn the_context_seams_arrive_as_data() {
        let mut params = bag("?q=hi");
        params
            .merge_json(
                br#"{"memories": {"results": []}, "artifacts": [{"path": "a.md"}],
                     "notes": "n", "knowledge": false, "budget": 40,
                     "recent": {"limit": 3, "images": false, "user_email": "a@x"}}"#,
            )
            .expect("merge");
        let Plan::ContextAssemble(request) =
            Plan::build(Endpoint::ContextAssemble, &params).expect("plan")
        else {
            panic!("wrong plan");
        };
        assert_eq!(request.budget, 40);
        assert!(!request.knowledge);
        assert_eq!(request.memories, Some(json!({"results": []})));
        assert_eq!(request.notes.as_deref(), Some("n"));
        let recent = request.recent.expect("recent");
        assert_eq!(recent.limit, Some(3));
        assert_eq!(recent.include_image_missing_replies, Some(false));
        assert_eq!(recent.user_email.as_deref(), Some("a@x"));
        assert!(format!("{:?}", Endpoint::History).contains("History"));
    }

    #[test]
    fn recent_accepts_a_bare_boolean_and_refuses_nonsense() {
        assert!(recent_request(None).unwrap().is_none());
        assert!(recent_request(Some(Value::Bool(false))).unwrap().is_none());
        assert!(recent_request(Some(Value::Null)).unwrap().is_none());
        let default = recent_request(Some(Value::Bool(true)))
            .unwrap()
            .expect("some");
        assert_eq!(default.limit, None);
        assert!(recent_request(Some(json!("nope"))).is_err());
        assert!(recent_request(Some(json!({"limit": 501}))).is_err());
        assert!(recent_request(Some(json!({"limit": "3"}))).is_err());
        assert!(recent_request(Some(json!({"images": 1}))).is_err());
        assert!(recent_request(Some(json!({"user_email": 7}))).is_err());
        let empty = recent_request(Some(json!({}))).unwrap().expect("some");
        assert!(empty.conversation_id.is_none());
    }

    #[test]
    fn running_against_a_missing_store_is_an_error_not_a_new_database() {
        let dir = tempfile::tempdir().expect("tempdir");
        let plan = Plan::build(Endpoint::History, &bag("")).expect("plan");
        assert!(format!("{plan:?}").contains("History"));
        let err = plan
            .run(&dir.path().join("knowledge_graph.sqlite"))
            .expect_err("no store");
        assert!(!format!("{err}").is_empty());
    }
}
