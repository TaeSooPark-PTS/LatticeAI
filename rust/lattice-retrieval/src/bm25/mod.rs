//! Genuine Field-Weighted Okapi BM25F + CJK Bigram/Subword Tokenization +
//! 1-Hop/2-Hop Knowledge Graph Structural Expansion + 3-Way Hybrid RRF (GraphRAG).

mod fusion;
mod graph_rag;
mod scoring;
mod tokenizer;

#[cfg(test)]
mod tests;

pub use fusion::{hybrid_bm25_vector_graph_search, GraphRagOptions};
pub use graph_rag::{expand_knowledge_graph, GraphRagConfig, GraphStructuralHit};
pub use scoring::{
    bm25f_score_nodes, bm25f_search, load_scoped_corpus, Bm25fParams, Bm25fScoredNode,
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
