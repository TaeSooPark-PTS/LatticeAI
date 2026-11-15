//! CJK bigram, Korean particle stripping, identifier subword, and markdown
//! wikilink/tag/heading extraction — re-exported from `lattice_core::bm25_stats`
//! so `GraphWriter` persistence and `Bm25SidecarIndex` share one tokenizer.

pub use lattice_core::bm25_stats::{
    extract_query_terms, extract_wikilinks_and_tags, tokenize_bm25_cjk, QueryTerm,
};
