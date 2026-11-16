# Knowledge Graph & Retrieval Performance

> **Status: current Rust retrieval architecture + labeled historical baselines.**
> The Python `scripts/profile_kg.py` (v9.6.0) and `scripts/bench_vector_index.py`
> (v11.1.0) scripts were retired when Knowledge Graph storage and retrieval
> moved to Rust (`lattice-core` and `lattice-retrieval`). Historical tables below
> are preserved strictly as dated cross-era reference baselines and are **not**
> runnable benchmarks today.

## Current Rust Retrieval Architecture (`lattice-retrieval` & `lattice-core`)

### 1. Pre-Tokenized BM25F Sidecar (`Bm25SidecarIndex` & `bm25_node_stats`)

- **Pre-tokenized inverted posting lists** (`rust/lattice-retrieval/src/bm25/sidecar.rs`):
  `Bm25SidecarIndex` maintains inverted posting lists (`term -> Vec<PostingEntry>`),
  O(1) document frequency `df(t)` lookups, pre-aggregated average field lengths
  (`avgdl_title`, `avgdl_tags`, `avgdl_body`, `avgdl_meta`), and cached wikilink,
  hashtag, title, and edge adjacency maps (`MAX_CACHED_EDGES = 50_000`).
- **Persistent field-stat hydration (`bm25_node_stats`)**: When the
  `bm25_node_stats` table is present, the sidecar hydrates pre-tokenized node
  field stats directly; on read-only golden fixtures without `bm25_node_stats`,
  it computes stats transparently in memory without schema mutation.
- **Incremental sync**: Node updates reconcile incrementally by
  `(node_id, updated_at, content_hash)` rather than re-tokenizing the entire
  corpus on every query.
- **Lifecycle-aware scoring**: `Bm25SidecarIndex::score` calls
  `score_with_options(..., include_historical = false)` so `superseded` and
  `deprecated` nodes are excluded from default active retrieval (and down-ranked
  only when `include_historical = true` is explicitly requested).

### 2. Concurrent Sidecar Snapshot Cache (`SidecarCachePool`)

Implemented in `rust/lattice-retrieval/src/bm25/sidecar_cache.rs`:

- **Shared-read concurrency**: Each cache entry stores `RwLock<Arc<Bm25SidecarIndex>>`.
  Concurrent query and GraphRAG readers clone the `Arc<Bm25SidecarIndex>`
  snapshot and score without holding a write lock across traversal or scoring.
- **Bounded capacity & LRU eviction**:
  - `MAX_CACHED_SIDECARS = 64` cache entries
  - `DEFAULT_MAX_CACHE_BYTES = 256 * 1024 * 1024` (256 MiB, overridable via
    `LATTICEAI_SIDECAR_CACHE_MAX_BYTES`)
  - Eviction considers both entry count and estimated byte footprint with
    `SidecarCacheStats` counters (`cache_hits`, `cache_misses`,
    `incremental_updates`, `tokenized_nodes`, `sidecar_table_hits`,
    `subword_computed_nodes`).

### 3. GraphRAG 1-Hop & 2-Hop Structural Expansion (`graph_rag.rs`)

Implemented in `rust/lattice-retrieval/src/bm25/graph_rag.rs`:

- **Snapshot-safe expansion**: `expand_knowledge_graph_with_snapshot` reads
  edge adjacency directly from an immutable `Bm25SidecarIndex` snapshot, while
  fetching uncached node rows through SQL-level workspace scope and
  `KnowledgeLifecycleStatus` filters.
- **Default traversal parameters** (`GraphRagConfig::default()`):
  - `max_hops = 2`
  - `max_seeds = 12`
  - `max_neighbors = 40`
  - `hop1_decay = 0.68`
  - `hop2_decay = 0.38`
  - `include_historical = false`
- **Relation weighting**: `PART_OF` (1.00), `RELATES_TO` (0.95),
  `DEPENDS_ON` (0.92), `USES` / `IMPLEMENTS` (0.90), `MENTIONS` / `REFERENCES`
  (0.85), `DERIVED_FROM` / `SUMMARIZES` (0.82), `HAS_TAG` / `WIKILINK` /
  `SHARED_CONCEPT` (0.80), fallback (0.68).

### 4. Vector Search & `EmbeddingIndexContract` (`vector.rs` & `embeddings.rs`)

- **Auto HNSW sidecar threshold (`HNSW_AUTO_MIN_ROWS = 512`)**: When a Brain
  has at least 512 vectors and a worker sidecar origin is bound, default vector
  search tries the HNSW sidecar (`k * 8` candidates, capped at `200`) and
  re-scores those exact rows with cosine similarity (`hnsw+rescore`), falling back
  to the bounded exact scan (`DEFAULT_VECTOR_MAX_CANDIDATES = 10_000`, ceiling
  `500_000`) on any sidecar miss.
- **Contract-guarded retrieval**: `EmbeddingIndexContract` (shared field-for-field
  between `lattice-core::embeddings` and `latticeai.core.embedding_providers`)
  enforces matching `provider`, `model_id`, `dimension`, `normalization`,
  `passage_mode`, `query_mode`, and `index_version`, reporting
  `IngestStatus::ReindexRequired` instead of mixing incompatible vector spaces.

### Active Performance & Contract Verification

No end-to-end throughput/latency sweep of the v12.7+ Rust sidecar is freshly
captured in this document; do not extrapolate the historical v9.6.0/v11.1.0
Python numbers below to the Rust `SidecarCachePool` path. Verify active BM25F,
GraphRAG, and vector contract behavior with:

```bash
cargo test --manifest-path rust/Cargo.toml -p lattice-retrieval --tests
```

---

## Historical Reference Baselines (Retired Python Harnesses)

> **Dated snapshots (not runnable today).** Preserved for cross-era context
> only: `scripts/profile_kg.py` (v9.6.0) and `scripts/bench_vector_index.py`
> (v11.1.0) measured the retired Python `lattice_brain` store using the
> deterministic hash embedder and synthetic corpora.

### Legacy v9.6.0 Python KG Profile (`500` & `5000` synthetic sources)

Measured on Apple Silicon (darwin), SQLite with FTS5 trigram, `tracemalloc`
enabled (which inflated Python allocation timings by ~2x):

- **500 sources (2,019 nodes / 6,546 edges, 21.2 MB DB)**:
  `ingest_source` p50 `6.9 ms` / p95 `17.7 ms` (~122 items/sec);
  `search` p50 `1.2 ms` / p95 `2.5 ms`;
  `traverse(depth=2)` p50 `4.8 ms` / p95 `5.2 ms`;
  `vector_search` (brute) p50 `287 ms` / p95 `294 ms`.
- **5000 sources (18,426 nodes / 62,534 edges, 198.8 MB DB)**:
  `ingest_source` p50 `20.1 ms` / p95 `54.6 ms` (~42 items/sec);
  `search` p50 `4.3 ms` / p95 `19.8 ms`;
  `traverse(depth=2)` p50 `12.2 ms` / p95 `15.8 ms`;
  `vector_search` (brute) p50 `1732 ms` / p95 `1774 ms`.

### Legacy v11.1.0 Vector Backend Comparison (2026-08-10, 10k & 50k synthetic vectors)

Measured on macOS (arm64), Python 3.14.5, SQLite 3.53.1, `hnswlib` 0.8.0,
uncapped candidates, deterministic hash embedder:

| Corpus | Backend | p50 ms | p95 ms | First ms | Recall@10 | Hybrid p50 ms |
|---|---|---:|---:|---:|---:|---:|
| 10,000 vectors (30 queries) | `brute` | 293.25 | 299.31 | 293.91 | 1.000 | 299.17 |
| 10,000 vectors (30 queries) | `quantized` | 640.58 | 650.15 | 636.87 | 0.987 | 653.33 |
| 10,000 vectors (30 queries) | `hnsw` | 7.01 | 7.50 | 799.95 | 0.953 | 10.07 |
| 50,000 vectors (15 queries) | `brute` | 1515.31 | 1522.94 | 1520.89 | 1.000 | 1514.75 |
| 50,000 vectors (15 queries) | `quantized` | 3254.76 | 3275.69 | 3253.54 | 0.967 | 3441.13 |
| 50,000 vectors (15 queries) | `hnsw` | 35.58 | 38.19 | 6114.60 | 0.987 | 43.90 |

Key takeaways from that 2026-08-10 sweep: exact brute scan scales linearly with
index size, `quantized` offered no RAM advantage over batched exact scans while
doubling latency (~2.2x), and `hnsw` kept hybrid latency well under 50 ms at
50k rows (motivation for Rust `HNSW_AUTO_MIN_ROWS = 512` + `hnsw+rescore`).
