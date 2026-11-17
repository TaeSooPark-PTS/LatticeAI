//! Genuine Field-Weighted Okapi BM25F + CJK Bigram/Subword Tokenization +
//! 1-Hop/2-Hop Knowledge Graph Structural Expansion + 3-Way Hybrid RRF (GraphRAG).

mod fusion;
mod graph_rag;
pub mod reasoning_paths;
pub(crate) mod scoring;
mod sidecar;
mod sidecar_cache;
mod tokenizer;

#[cfg(test)]
mod tests;

pub use fusion::{
    hybrid_bm25_vector_graph_search, score_and_expand_with_sidecar, GraphRagOptions,
    QueryVectorOverride,
};
pub use graph_rag::{
    expand_knowledge_graph, expand_knowledge_graph_with_sidecar,
    expand_knowledge_graph_with_snapshot, GraphRagConfig, GraphStructuralHit,
};
pub use reasoning_paths::{BridgeConcept, NeighborhoodConflict, ReasoningPath};
pub use scoring::{
    bm25f_score_nodes, bm25f_search, bm25f_search_with_options, load_scoped_corpus,
    load_scoped_corpus_records, load_scoped_corpus_with_options, Bm25fParams, Bm25fScoredNode,
    CorpusLoadOptions, KnowledgeLifecycleStatus, ScopedNodeRecord,
};
pub use sidecar::{Bm25SidecarIndex, PostingEntry, SidecarCacheStats};
pub use sidecar_cache::{
    cached_sidecar_stats, with_bm25_sidecar, with_bm25_sidecar_full_snapshot,
    with_bm25_sidecar_snapshot, SidecarCachePool, DEFAULT_MAX_CACHE_BYTES, MAX_CACHED_SIDECARS,
};
pub use tokenizer::{
    extract_query_terms, extract_wikilinks_and_tags, tokenize_bm25_cjk, QueryTerm,
};

/// Environment gate enabling BM25F + 3-way GraphRAG RRF inside `hybrid_search` / `service_hybrid_search`.
pub const BM25F_ENV: &str = "LATTICEAI_BM25F";

/// Whether `LATTICEAI_BM25F` or `LATTICEAI_FUSION_STRATEGY=bm25_graph_rrf|graphrag` is active.
pub fn bm25f_graphrag_enabled() -> bool {
    let bm25_flag = std::env::var(BM25F_ENV)
        .unwrap_or_default()
        .trim()
        .to_ascii_lowercase();
    if matches!(bm25_flag.as_str(), "1" | "true" | "yes" | "on") {
        return true;
    }
    let strategy = std::env::var("LATTICEAI_FUSION_STRATEGY")
        .unwrap_or_default()
        .trim()
        .to_ascii_lowercase();
    matches!(
        strategy.as_str(),
        "bm25_graph_rrf" | "graphrag" | "bm25f" | "hybrid_3way"
    )
}
