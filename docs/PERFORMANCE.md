# Knowledge Graph Performance Baseline

> **Status: reference** — a synthetic regression baseline, not a
> current-release measurement. The numbers below were last captured on the
> v9.6.0 working tree (see provenance line) and are carried forward as the
> comparison reference. The 9.7.0-9.8.0 line added graph-layer paths
> (`hybrid_search`, vector freshness, extraction-quality scoring), so
> regenerate with `scripts/profile_kg.py` on the current tree before treating
> these as 9.8.0 numbers.

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

## Observations

- Keyword `search()` / `context_for_query()` stay low-millisecond thanks to
  the FTS5 trigram index; they are not the scaling bottleneck.
- `traverse(depth=2)` cost grows with edge fan-out (synthetic corpus creates
  dense shared-concept hubs); p95 is ~5 ms at 500 sources and ~16 ms at 5000.
- `vector_search()` is a brute-force scan over every stored embedding
  (O(index size) per query). It is the dominant cost at scale and the first
  candidate for an ANN/pruning optimization if vector recall becomes a hot
  path. The profiler caps vector queries at 10 for this reason.
- `rebuild_vector_index(full=True)` embeds documents and chunks with the hash
  embedder; with a real embedding provider expect this phase to be slower by
  the provider's per-call latency times the item count.

## Regression workflow

1. Before a graph-layer change: `.venv/bin/python scripts/profile_kg.py --nodes 500 --json > /tmp/kg-before.json`
2. After the change: same command to `/tmp/kg-after.json`.
3. Compare phase p50/p95 and `db_size_mb`; investigate anything that moved
   more than ~20% beyond run-to-run noise.
