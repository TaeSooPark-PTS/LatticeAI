#!/usr/bin/env python3
"""Vector-index backend benchmark: latency *and* the recall it costs.

A speed number for an approximate index is only half a measurement. This
script reports both halves for every backend — p50/p95 query latency and
recall@k against the exact brute-force scan, which is the ground truth by
construction — so "hnsw is 20x faster" always arrives next to "and returns
0.97 of the exact top-10".

Deliberately **not** part of CI: it builds a 10 000-vector corpus, it depends
on an optional compiled extra for one of the three backends, and its numbers
are machine-specific. It is a local instrument, run by hand, whose output is
transcribed into docs/PERFORMANCE.md with the machine it came from.

Honesty notes:

* The corpus is synthetic — deterministic pseudo-text from a fixed vocabulary
  embedded with the built-in hash embedder. Real embeddings cluster very
  differently, so absolute recall here is an upper bound for a well-separated
  corpus, not a promise about your notes.
* Rows are written straight into ``vector_embeddings`` rather than through the
  ingest pipeline. This measures the *search* path; it is not an ingestion
  benchmark, and PERFORMANCE.md keeps those numbers separate.
* A backend whose optional dependency is missing is reported as "not
  measured", never as a blank or a zero.
* The candidate cap is lifted by default (``--max-candidates 0``). With the
  product default of 10 000 the exact scan silently becomes "the newest 10 000
  rows" on a larger index, and recall@k would then be measured against a
  baseline that never looked at most of the corpus — which reads as "the ANN
  backend is inaccurate" when the truth is that the two searched different
  candidate sets. The ``trunc`` column reports whether any query still hit a
  cap.
* Latency and memory are measured in separate passes: tracemalloc roughly
  triples Python allocation cost, so timing under it would measure the
  profiler. The ``peak MB`` column comes from one extra traced query.

Usage::

    .venv/bin/python scripts/bench_vector_index.py                 # 10k vectors
    .venv/bin/python scripts/bench_vector_index.py --nodes 1000     # quick
    .venv/bin/python scripts/bench_vector_index.py --json           # machine-readable
"""

from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lattice_brain.graph.retrieval_vector import (  # noqa: E402
    VECTOR_MAX_CANDIDATES_ENV,
)
from lattice_brain.graph.store import KnowledgeGraphStore  # noqa: E402
from lattice_brain.graph.vector_index import (  # noqa: E402
    VECTOR_INDEX_ENV,
    hnswlib_available,
    sidecar_paths,
)
from lattice_brain.utils import utc_now_iso  # noqa: E402

VOCABULARY = [
    "retrieval", "embedding", "vector", "index", "graph", "ingestion",
    "provenance", "workspace", "chunk", "similarity", "cosine", "quantize",
    "sqlite", "postgres", "backup", "restore", "agent", "proposal", "review",
    "latency", "throughput", "recall", "precision", "fusion", "lexical",
    "지식", "그래프", "검색", "임베딩", "인덱스", "문서", "요약", "회의",
]
BACKENDS = ("brute", "quantized", "hnsw")


def _document(rng: random.Random, index: int) -> str:
    words = [rng.choice(VOCABULARY) for _ in range(rng.randint(12, 60))]
    return f"note {index} " + " ".join(words)


def build_corpus(store: KnowledgeGraphStore, count: int, seed: int) -> float:
    """Write ``count`` synthetic nodes + embeddings. Returns wall seconds."""
    rng = random.Random(seed)  # noqa: S311 — synthetic corpus, not a secret
    model = store._embedding_model
    started = time.perf_counter()
    now = utc_now_iso()
    with store._connect() as conn:
        for index in range(count):
            node_id = f"note:{index:06d}"
            text = _document(rng, index)
            conn.execute(
                "INSERT OR REPLACE INTO nodes(id, type, title, summary, "
                "metadata_json, raw_json, created_at, updated_at) "
                "VALUES (?, 'Document', ?, ?, '{}', '{}', ?, ?)",
                (node_id, f"Note {index}", text[:300], now, now),
            )
            conn.execute(
                "INSERT OR REPLACE INTO vector_embeddings(item_id, item_type, "
                "source_node, text_hash, embedding, embedding_dim, "
                "embedding_model, metadata_json, indexed_at) "
                "VALUES (?, 'node', ?, ?, ?, ?, ?, '{}', ?)",
                (
                    node_id,
                    node_id,
                    f"hash-{index}",
                    model.encode(model.embed(text)),
                    int(model.dim),
                    model.model_id,
                    now,
                ),
            )
    return time.perf_counter() - started


def _queries(rng: random.Random, count: int) -> List[str]:
    return [
        " ".join(rng.choice(VOCABULARY) for _ in range(rng.randint(2, 5)))
        for _ in range(count)
    ]


def _percentiles(samples: List[float]) -> Dict[str, float]:
    ordered = sorted(samples)
    return {
        "p50_ms": round(statistics.median(ordered) * 1000, 2),
        "p95_ms": round(ordered[max(0, int(len(ordered) * 0.95) - 1)] * 1000, 2),
        "mean_ms": round(statistics.fmean(ordered) * 1000, 2),
    }


def measure(
    store: KnowledgeGraphStore,
    backend: str,
    queries: List[str],
    *,
    top_k: int,
    baseline: Optional[Dict[str, List[str]]],
) -> Dict[str, Any]:
    """Run every query against ``backend``; return latency + recall + hits."""
    os.environ[VECTOR_INDEX_ENV] = backend
    selection = store._vector_index_selection()
    if not selection.honored:
        return {"backend": backend, "measured": False, "detail": selection.detail}

    # Warm the sidecar/graph so the reported percentiles are steady-state.
    warm_started = time.perf_counter()
    store.vector_search(queries[0], limit=top_k)
    warm_ms = round((time.perf_counter() - warm_started) * 1000, 2)

    # Latency first, *untraced*: tracemalloc roughly triples Python allocation
    # cost, and a timing table where one column was measured under a profiler
    # would compare backends against the profiler rather than each other.
    hits: Dict[str, List[str]] = {}
    samples: List[float] = []
    truncated = False
    for query in queries:
        started = time.perf_counter()
        payload = store.vector_search(query, limit=top_k)
        samples.append(time.perf_counter() - started)
        hits[query] = [match["id"] for match in payload["matches"]]
        truncated = truncated or bool(payload["recall"]["truncated"])

    # Memory in its own pass — one query is enough to see the peak.
    tracemalloc.start()
    store.vector_search(queries[0], limit=top_k)
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()

    report: Dict[str, Any] = {
        "backend": backend,
        "measured": True,
        "engine": selection.backend,
        "approx": selection.approx,
        "first_query_ms": warm_ms,
        "candidates_truncated": truncated,
        "peak_mb": round(peak / (1024 * 1024), 2),
        **_percentiles(samples),
    }
    if baseline is None:
        report[f"recall_at_{top_k}"] = 1.0  # this run *is* the ground truth
    else:
        scores = [
            len(set(hits[query]) & set(baseline[query])) / max(1, len(baseline[query]))
            for query in queries
        ]
        report[f"recall_at_{top_k}"] = round(statistics.fmean(scores), 4)
    # The product-level number: hybrid_search on the same backend, because
    # that is what a user's question actually runs through.
    hybrid: List[float] = []
    for query in queries:
        started = time.perf_counter()
        store.hybrid_search(query, top_k=10)
        hybrid.append(time.perf_counter() - started)
    hybrid_stats = _percentiles(hybrid)
    report["hybrid_p50_ms"] = hybrid_stats["p50_ms"]
    report["hybrid_p95_ms"] = hybrid_stats["p95_ms"]
    report["_hits"] = hits
    return report


def render(report: Dict[str, Any]) -> str:
    lines = [
        f"vectors: {report['nodes']}  queries: {report['queries']}  "
        f"top_k: {report['top_k']}  max_candidates: {report['max_candidates']}"
        f"  corpus build: {report['build_seconds']}s",
        "",
        f"{'backend':<12}{'p50 ms':>10}{'p95 ms':>10}{'first ms':>10}"
        f"{'peak MB':>10}{'recall@k':>10}{'trunc':>8}{'hybrid p50':>12}",
        "-" * 82,
    ]
    recall_key = "recall_at_%d" % report["top_k"]
    for row in report["backends"]:
        name = row["backend"]
        if not row["measured"]:
            lines.append(f"{name:<12}  not measured — {row['detail']}")
            continue
        lines.append(
            f"{name:<12}{row['p50_ms']:>10}{row['p95_ms']:>10}"
            f"{row['first_query_ms']:>10}{row['peak_mb']:>10}"
            f"{row[recall_key]:>10}"
            f"{('yes' if row['candidates_truncated'] else 'no'):>8}"
            f"{row['hybrid_p50_ms']:>12}"
        )
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nodes", type=int, default=10_000)
    parser.add_argument("--queries", type=int, default=50)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--max-candidates",
        type=int,
        default=0,
        help="candidate cap for every backend (0 = uncapped, comparable recall)",
    )
    parser.add_argument("--db-path", default=None, help="keep the database around")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    os.environ[VECTOR_MAX_CANDIDATES_ENV] = str(args.max_candidates)
    workdir = Path(args.db_path) if args.db_path else Path(tempfile.mkdtemp())
    workdir.mkdir(parents=True, exist_ok=True)
    store = KnowledgeGraphStore(workdir / "bench.sqlite", workdir / "blobs")
    build_seconds = build_corpus(store, args.nodes, args.seed)
    queries = _queries(
        random.Random(args.seed + 1),  # noqa: S311 — synthetic queries
        args.queries,
    )

    rows: List[Dict[str, Any]] = []
    baseline: Optional[Dict[str, List[str]]] = None
    for backend in BACKENDS:
        if backend == "hnsw" and not hnswlib_available():
            rows.append(
                {
                    "backend": backend,
                    "measured": False,
                    "detail": "hnswlib is not installed (pip install 'ltcai[hnsw]')",
                }
            )
            continue
        row = measure(store, backend, queries, top_k=args.top_k, baseline=baseline)
        if baseline is None and row["measured"]:
            baseline = row["_hits"]
        row.pop("_hits", None)
        rows.append(row)

    os.environ.pop(VECTOR_INDEX_ENV, None)
    report = {
        "nodes": args.nodes,
        "queries": args.queries,
        "top_k": args.top_k,
        "max_candidates": args.max_candidates,
        "seed": args.seed,
        "build_seconds": round(build_seconds, 2),
        "db_path": str(store.db_path),
        "sidecar": str(sidecar_paths(store.db_path)[0]),
        "backends": rows,
    }
    print(json.dumps(report, indent=2) if args.json else render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
