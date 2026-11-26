#![allow(clippy::needless_lifetimes, clippy::result_large_err)]

pub(crate) fn build_pattern(pattern: &str) -> fancy_regex::Regex {
    fancy_regex::Regex::new(pattern).expect("ported pattern must compile")
}

pub mod bm25;
pub mod brain_api;
pub mod chronicle_api;
pub mod command_center_api;
pub mod concepts;
pub mod context;
pub mod docgen;
pub mod docgen_context;
pub mod evidence_api;
pub mod expansion;
pub mod garden_api;
pub mod graph_reads;
pub mod grounding;
pub mod history;
pub mod hybrid;
pub mod keyword;
pub mod knowledge_graph_api;
pub mod memory_api;
pub mod native_hnsw;
pub mod neighborhood_synthesis;
pub mod policy;
pub mod query_embed;
pub mod rag_eval;
pub mod rag_eval_dataset;
pub mod rag_eval_grounded;
pub mod rerank;
pub mod routes;
pub mod search_api;
pub mod self_model;
pub mod service;
pub mod service_hybrid;
pub mod shape;
pub mod vector;
pub mod vector_alignment;
pub mod vector_hnsw;

pub use bm25::{
    bm25f_graphrag_enabled, bm25f_score_nodes, bm25f_search, bm25f_search_with_options,
    cached_sidecar_stats, expand_knowledge_graph, expand_knowledge_graph_with_sidecar,
    expand_knowledge_graph_with_snapshot, extract_query_terms, extract_wikilinks_and_tags,
    hybrid_bm25_vector_graph_search, load_scoped_corpus, load_scoped_corpus_records,
    load_scoped_corpus_with_options, tokenize_bm25_cjk, with_bm25_sidecar,
    with_bm25_sidecar_full_snapshot, with_bm25_sidecar_snapshot, Bm25SidecarIndex, Bm25fParams,
    Bm25fScoredNode, CorpusLoadOptions, GraphRagConfig, GraphRagOptions, GraphStructuralHit,
    KnowledgeLifecycleStatus, PostingEntry, QueryTerm, QueryVectorOverride, ScopedNodeRecord,
    SidecarCachePool, SidecarCacheStats, DEFAULT_MAX_CACHE_BYTES, MAX_CACHED_SIDECARS,
};
pub use concepts::topic_candidates;
pub use context::{assemble_context, ContextRequest};
pub use docgen::{context_for_query, multi_hop_context, search_for_document_generation};
pub use docgen_context::{
    format_sources_footnote, retrieve_context_for_generation, DocumentContextRequest,
};
pub use graph_reads::{relationship_search, traverse, RelationshipQuery, TraverseOptions};
pub use history::{history, HistoryScope};
pub use hybrid::{hybrid_search, HybridOptions};
pub use keyword::search;
pub use policy::{class_weights, classify_query, resolve_policy, rewrite_query, Policy};
pub use rag_eval::{run_default_rag_evaluation, RagEvalReport};
pub use routes::router;
pub use self_model::summary_for_prompt;
pub use service::{graph_search, vector_search_aligned_service, GraphSearchOptions};
pub use service_hybrid::{service_hybrid_search, ServiceHybridOptions};
pub use vector::{
    vector_search, vector_search_aligned, StoredEmbeddingSummary, VectorProviderStatus,
    VectorQueryInput, VectorSearchOptions,
};
