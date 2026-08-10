# Knowledge Graph Performance Baseline

> **Status: mixed provenance — read the date on each table.** The
> `scripts/profile_kg.py` tables were last captured on the v9.6.0 working tree
> and are carried forward as the regression reference; regenerate them before
> treating them as current. The **vector index backend** table is a fresh
> 11.1.0 measurement (2026-08-10) from `scripts/bench_vector_index.py`, and
> says so inline.

Synthetic performance and memory baseline for `KnowledgeGraphStore`
(`lattice_brain/graph/`). Measured with `scripts/profile_kg.py`.

**This is a synthetic baseline, not a real-workload benchmark.** Use it for
release-over-release regression comparison, not as a marketing number.

## Methodology

- `scripts/profile_kg.py` builds a throwaway store the same way the unit tests
  do (`KnowledgeGraphStore(tmp/graph.sqlite, tmp/blobs)`) and ingests N
  synthetic text sources through the real `ingest_source()` pipeline
  (chunking, rule-based concept/triple extraction, provenance edges).
- Corpus: fixed-vocabulary English + Korean text, seeded RNG (`--seed`,
  default 42), ~60% short (~40 words) / 30% medium (~160) / 10% long (~520)
  documents with recurring entities so concept nodes are shared and edges form.
- Fully offline: no LLM router is registered (extraction takes the rules
  path) and the embedder is the built-in deterministic hash model. No network,
  no model downloads.
- Timing: `time.perf_counter()` per call; p50/p95/mean over all calls in a
  phase. Memory: `tracemalloc` peak, reset per phase.
- Caveat: tracemalloc runs for the whole profile and roughly doubles Python
  allocation cost, so absolute timings are conservative upper bounds.
- Caveat: the synthetic vocabulary is small, so FTS selectivity and concept
  density differ from real user data; every tenth query is a guaranteed miss
  to avoid measuring only warm hits.

Run it:

```bash
.venv/bin/python scripts/profile_kg.py                 # 5000 sources, 50 queries
.venv/bin/python scripts/profile_kg.py --nodes 500     # quick run (~10 s)
.venv/bin/python scripts/profile_kg.py --json          # machine-readable
```

## Measured baseline (last captured on the v9.6.0 working tree)

Apple Silicon (darwin), Python 3.x from `.venv`, SQLite with FTS5 trigram
available, tracemalloc enabled.

### 500 sources (quick profile, ~9 s total)

Graph produced: 2,019 nodes / 6,546 edges, 21.2 MB SQLite file.

| phase                | calls | p50 ms | p95 ms | wall s | peak MB |
|----------------------|------:|-------:|-------:|-------:|--------:|
| ingest_source        |   500 |   6.9  |  17.7  |   4.1  |   0.6   |
| search               |    50 |   1.2  |   2.5  |   0.07 |   0.6   |
| context_for_query    |    50 |   1.0  |   2.4  |   0.06 |   0.5   |
| neighbors            |    50 |   0.35 |   0.45 |   0.02 |   0.6   |
| traverse (depth 2)   |    50 |   4.8  |   5.2  |   0.24 |   1.1   |
| stats                |     5 |   1.5  |   1.7  |   0.01 |   0.4   |
| rebuild_vector_index |     1 |  1319  |  1319  |   1.3  |   5.0   |
| vector_search        |    10 |  287   |  294   |   2.9  |  14.4   |

Ingestion throughput: **~122 items/sec** (412 KB corpus).

### 5000 sources (default profile, ~2.5 min total)

Graph produced: 18,426 nodes / 62,534 edges, 198.8 MB SQLite file
(4.26 MB corpus). Measured while other processes shared the CPU, so treat as
an upper bound.

| phase                | calls | p50 ms | p95 ms | wall s | peak MB |
|----------------------|------:|-------:|-------:|-------:|--------:|
| ingest_source        |  5000 |  20.1  |  54.6  | 119.9  |   1.4   |
| search               |    50 |   4.3  |  19.8  |   0.40 |   1.2   |
| context_for_query    |    50 |   4.1  |  18.5  |   0.40 |   1.2   |
| neighbors            |    50 |   0.39 |   0.50 |   0.03 |   1.2   |
| traverse (depth 2)   |    50 |  12.2  |  15.8  |   0.64 |   1.7   |
| stats                |     5 |  13.1  |  13.5  |   0.07 |   1.1   |
| rebuild_vector_index |     1 | 14624  | 14624  |  14.6  |  45.6   |
| vector_search        |    10 |  1732  |  1774  |  17.4  |  83.5   |

Ingestion throughput: **~42 items/sec** at this scale.

Scaling signal (500 → 5000 sources, 10x): per-item ingest p50 grew ~3x
(6.9 ms → 20.1 ms) — ingestion cost is superlinear in DB size (FTS triggers,
dedup lookups, edge upserts against growing tables). `vector_search` p50 grew
~6x (287 ms → 1732 ms), consistent with its O(index size) brute-force scan.
Keyword search p50 grew ~3.6x but stays under 5 ms.

Regenerate with `.venv/bin/python scripts/profile_kg.py` after graph-layer
changes and compare against these tables.

## Vector index backends (11.1.0)

`scripts/bench_vector_index.py` measures the three selectable backends
(`LATTICEAI_VECTOR_INDEX`) against each other. It answers two questions
together, because either one alone is misleading: **how fast** each backend
answers, and **how much of the exact answer it returns** (recall@10 against
the brute-force scan, which is ground truth by construction).

Methodology, and what it does not cover:

- The corpus is synthetic — deterministic pseudo-text from a fixed vocabulary
  (English + Korean) embedded with the built-in hash embedder. Real embeddings
  cluster differently, so the recall figures are an upper bound for a
  well-separated corpus, not a promise about your notes.
- Rows are written straight into `vector_embeddings`. This measures the
  **search** path only; ingestion throughput stays in the tables above.
- Latency and memory are measured in separate passes. tracemalloc roughly
  triples Python allocation cost, so timing under it would measure the
  profiler — the `peak MB` column comes from one extra traced query.
- `peak MB` counts **Python** allocations only. hnswlib keeps its graph in C++
  memory, which tracemalloc cannot see, so that column understates HNSW by the
  size of the graph itself. Read it as "what the query costs the interpreter",
  not as the process's resident size.
- `first ms` is the *first* query after the corpus changed. For `hnsw` that is
  where the graph gets built and the `.hnsw` sidecar written; for the other two
  it is an ordinary query.
- `hybrid p50` is `hybrid_search` (lexical + vector fusion) on that backend —
  the number a user's question actually pays.
- The candidate cap is **lifted** (`--max-candidates 0`). This matters more
  than it sounds: with the product default of 10 000, the exact scan on a
  larger index silently becomes "the newest 10 000 rows", and recall@10 would
  then be measured against a baseline that never looked at most of the corpus.
  The first 50k run made exactly that mistake and scored HNSW at 0.18 — not
  because it missed anything, but because the two backends had searched
  different candidate sets. The `trunc` column now reports whether any query
  hit a cap.

Run it:

```bash
.venv/bin/python scripts/bench_vector_index.py                  # 10k vectors
.venv/bin/python scripts/bench_vector_index.py --nodes 1000     # quick
.venv/bin/python scripts/bench_vector_index.py --json           # machine-readable
```

### Measured, 2026-08-10 — 10 000 vectors, 30 queries, top_k 10

macOS 27 (arm64, Apple Silicon), Python 3.14.5, SQLite 3.53.1,
hnswlib 0.8.0, uncapped candidates, corpus build 2.19 s.

| backend | p50 ms | p95 ms | first ms | peak MB | recall@10 | hybrid p50 ms |
|-----------|-------:|-------:|---------:|--------:|----------:|--------------:|
| brute     | 293.25 | 299.31 |   293.91 |   39.72 |    1.000  |        299.17 |
| quantized | 640.58 | 650.15 |   636.87 |   38.38 |    0.987  |        653.33 |
| hnsw      |   7.01 |   7.50 |   799.95 |    0.05 |    0.953  |         10.07 |

What the table says, plainly:

- **The default is exact and slow.** 293 ms per query at 10k, and hybrid
  inherits nearly all of it (299 ms) — the vector channel *is* the cost.
- **HNSW meets the 11.1.0 target and prices it.** Hybrid p50 **10.1 ms** at 10k
  (target: < 50 ms), a 30x improvement, in exchange for **4.7% of the exact
  top-10 going missing**. That is the trade, stated as a number rather than as
  the word "approximate".
- **The 800 ms first query is the graph build**, paid once per index generation
  and then persisted to the `.hnsw` sidecar (and held in memory for the rest of
  the process). Any write to `vector_embeddings` invalidates the fingerprint
  and buys that cost again, which is why `brute` stays the default for a
  continuously-ingesting brain.
- **Quantized is currently the wrong choice on every axis.** ~2.2x the latency
  for 0.987 recall, and its RAM advantage does not materialise (38.4 vs
  39.7 MB): the exact scan already feeds the index in bounded batches, so
  resident vectors were never the dominant term — the fetched SQLite rows are.
  It ships as an honest, exhaustive backend and as the representation a held
  cross-query index would need. It is not a recommendation.

### Measured, 2026-08-10 — 50 000 vectors, 15 queries, top_k 10

Same machine and settings; corpus build 11.19 s.

| backend | p50 ms | p95 ms | first ms | peak MB | recall@10 | hybrid p50 ms |
|-----------|--------:|--------:|---------:|--------:|----------:|--------------:|
| brute     | 1515.31 | 1522.94 |  1520.89 |  194.92 |    1.000  |       1514.75 |
| quantized | 3254.76 | 3275.69 |  3253.54 |  195.05 |    0.967  |       3441.13 |
| hnsw      |   35.58 |   38.19 |  6114.60 |    0.04 |    0.987  |         43.90 |

- **The exact scan is linear and unusable at this size**: 1.5 s per query, and
  ~195 MB of Python allocation to score one question.
- **HNSW still clears the 50 ms budget at 5x the target corpus** — hybrid p50
  43.9 ms — and its recall here is 0.987, higher than the 10k run's 0.953
  (15 queries is a small sample; treat the two as "around 0.95–0.99", not as a
  trend).
- **The remaining HNSW cost is not the search.** 7 ms at 10k → 36 ms at 50k is
  suspiciously linear for a graph index, and it is: every query first runs the
  freshness check (`SELECT COUNT(*), MAX(indexed_at) FROM vector_embeddings
  WHERE embedding_model=? AND embedding_dim=?`), which has no covering index
  and walks the table. A named follow-up, not a mystery: an index on
  `(embedding_model, embedding_dim)` would remove it. The budget is met either
  way, so it was not worth a schema change in this release.
- **The 6.1 s first query is the 50k graph build.** It is paid once per index
  generation, and the sidecar means a restart does not pay it again.

### A measurement mistake worth keeping

The first 50k run reported HNSW recall of **0.18** and was wrong. The default
candidate cap (10 000) was still in force, so the "exact" baseline had scored
only the newest 10 000 of 50 000 rows while HNSW searched all of them — the
disagreement was the baseline's blind spot, not the ANN's error. The bench now
lifts the cap by default and prints a `trunc` column. Recorded here because
the failure mode is generic: *any* recall number measured against a truncated
baseline is measuring the truncation.

### Not measured

- **100k+ vectors.** Nothing is claimed beyond the 50k run above.
- **sqlite-vec ANN.** The optional `ann` extra was not installed, so
  `vector_search_backend` reported `bruteforce-cosine` throughout. Its numbers
  are unknown, not zero.
- **A real embedding provider.** Everything here uses the deterministic hash
  embedder. With a model- or network-backed embedder, query-embedding cost
  moves into the foreground and these ratios change.
- **Resident process memory.** See the tracemalloc caveat above: the HNSW graph
  lives outside Python's allocator and is not in the `peak MB` column.

## Observations

- Keyword `search()` / `context_for_query()` stay low-millisecond thanks to
  the FTS5 trigram index; they are not the scaling bottleneck.
- `traverse(depth=2)` cost grows with edge fan-out (synthetic corpus creates
  dense shared-concept hubs); p95 is ~5 ms at 500 sources and ~16 ms at 5000.
- `vector_search()` on the default backend is a brute-force scan over every
  stored embedding (O(index size) per query). It is the dominant cost at scale
  — which is what the 11.1.0 backend table above measures, and what
  `LATTICEAI_VECTOR_INDEX=hnsw` addresses. `profile_kg.py` still caps vector
  queries at 10 for this reason.
- `rebuild_vector_index(full=True)` embeds documents and chunks with the hash
  embedder; with a real embedding provider expect this phase to be slower by
  the provider's per-call latency times the item count.

## Regression workflow

1. Before a graph-layer change: `.venv/bin/python scripts/profile_kg.py --nodes 500 --json > /tmp/kg-before.json`
2. After the change: same command to `/tmp/kg-after.json`.
3. Compare phase p50/p95 and `db_size_mb`; investigate anything that moved
   more than ~20% beyond run-to-run noise.
