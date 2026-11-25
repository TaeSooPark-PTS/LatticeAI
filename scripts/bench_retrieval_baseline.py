#!/usr/bin/env python3
"""Deterministic retrieval & HNSW sidecar benchmark baseline gate.

Measures offline recall@10 and p95 query/incremental-refresh latency on a
synthetic normalized embedding corpus so CI catches recall drops or latency
regressions before release.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sqlite3
import struct
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from latticeai.core.vector_index.sidecar import (  # noqa: E402
    query_sidecar,
    reset_sidecar_cache,
)

MODEL_ID = "huggingface:google/embeddinggemma-2:32"
DIM = 32
CORPUS_SIZE = 600
QUERIES = 25
TOP_K = 10
MIN_RECALL_AT_10 = 0.95
MAX_QUERY_P95_MS = 50.0
MAX_INCREMENTAL_REFRESH_MS = 250.0


def _unit_vec(rng: random.Random, dim: int) -> List[float]:
    raw = [rng.gauss(0.0, 1.0) for _ in range(dim)]
    norm = math.sqrt(sum(v * v for v in raw)) or 1.0
    return [v / norm for v in raw]


def _pack_f32(vec: List[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


def _dot(a: List[float], b: List[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def run_benchmark() -> Dict[str, Any]:
    rng = random.Random(42)  # noqa: S311
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "knowledge_graph.sqlite"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            """
            CREATE TABLE vector_embeddings (
                item_id TEXT PRIMARY KEY,
                item_type TEXT,
                source_node TEXT,
                embedding BLOB,
                embedding_dim INTEGER,
                embedding_model TEXT,
                text_hash TEXT,
                metadata_json TEXT,
                indexed_at TEXT
            )
            """
        )
        vectors: Dict[str, List[float]] = {}
        rows = []
        for idx in range(CORPUS_SIZE):
            item_id = f"doc-{idx:04d}"
            vec = _unit_vec(rng, DIM)
            vectors[item_id] = vec
            rows.append(
                (
                    item_id,
                    "node",
                    item_id,
                    _pack_f32(vec),
                    DIM,
                    MODEL_ID,
                    f"hash-{idx}",
                    "{}",
                    f"2026-11-17T00:00:{idx % 60:02d}",
                )
            )
        conn.executemany(
            "INSERT INTO vector_embeddings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()

        reset_sidecar_cache()
        build_t0 = time.perf_counter()
        build_report = query_sidecar(
            workspace=None,
            embedding_model=MODEL_ID,
            embedding_dim=DIM,
            vector=rows[0][3] and vectors["doc-0000"],
            k=TOP_K,
            db_path=db_path,
        )
        build_ms = (time.perf_counter() - build_t0) * 1000.0
        backend = build_report.get("index", "none")

        query_vecs = [_unit_vec(rng, DIM) for _ in range(QUERIES)]
        latencies_ms: List[float] = []
        recalls: List[float] = []

        for qvec in query_vecs:
            exact = sorted(
                vectors.items(),
                key=lambda kv: _dot(qvec, kv[1]),
                reverse=True,
            )[:TOP_K]
            exact_ids = {item_id for item_id, _ in exact}

            if backend == "hnsw":
                t0 = time.perf_counter()
                reply = query_sidecar(
                    workspace=None,
                    embedding_model=MODEL_ID,
                    embedding_dim=DIM,
                    vector=qvec,
                    k=TOP_K * 4,
                    db_path=db_path,
                )
                latencies_ms.append((time.perf_counter() - t0) * 1000.0)
                cand_ids = reply.get("ids", [])
                rescored = sorted(
                    cand_ids,
                    key=lambda cid: _dot(qvec, vectors[cid]),
                    reverse=True,
                )[:TOP_K]
                hit_overlap = len(exact_ids.intersection(rescored)) / float(TOP_K)
                recalls.append(hit_overlap)
            else:
                t0 = time.perf_counter()
                rescored_pairs = sorted(
                    vectors.items(),
                    key=lambda kv: _dot(qvec, kv[1]),
                    reverse=True,
                )[:TOP_K]
                latencies_ms.append((time.perf_counter() - t0) * 1000.0)
                recalls.append(
                    len(exact_ids.intersection(k for k, _ in rescored_pairs))
                    / float(TOP_K)
                )

        # Incremental insert + delete refresh timing when hnswlib is installed
        inc_ms = 0.0
        inc_mode = "skipped_no_hnswlib"
        if backend == "hnsw":
            new_vec = _unit_vec(rng, DIM)
            conn.execute(
                "INSERT INTO vector_embeddings VALUES (?, 'node', ?, ?, ?, ?, 'hash-new', '{}', '2026-11-17T01:00:00')",
                ("doc-new", "doc-new", _pack_f32(new_vec), DIM, MODEL_ID),
            )
            conn.execute("DELETE FROM vector_embeddings WHERE item_id='doc-0000'")
            conn.commit()
            t0 = time.perf_counter()
            inc_reply = query_sidecar(
                workspace=None,
                embedding_model=MODEL_ID,
                embedding_dim=DIM,
                vector=new_vec,
                k=TOP_K,
                db_path=db_path,
            )
            inc_ms = (time.perf_counter() - t0) * 1000.0
            inc_mode = str(inc_reply.get("refreshed") or "none")

        conn.close()
        reset_sidecar_cache()

    latencies_sorted = sorted(latencies_ms)
    p95_idx = min(
        len(latencies_sorted) - 1,
        max(0, int(math.ceil(0.95 * len(latencies_sorted))) - 1),
    )
    p95_ms = latencies_sorted[p95_idx]
    mean_recall = sum(recalls) / float(len(recalls))

    return {
        "backend": backend,
        "corpus_size": CORPUS_SIZE,
        "dimension": DIM,
        "queries": QUERIES,
        "build_ms": round(build_ms, 3),
        "query_p95_ms": round(p95_ms, 3),
        "mean_recall_at_10": round(mean_recall, 4),
        "incremental_refresh_ms": round(inc_ms, 3),
        "incremental_refresh_mode": inc_mode,
        "budgets": {
            "min_recall_at_10": MIN_RECALL_AT_10,
            "max_query_p95_ms": MAX_QUERY_P95_MS,
            "max_incremental_refresh_ms": MAX_INCREMENTAL_REFRESH_MS,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json", dest="json_path", help="Write benchmark report JSON to path"
    )
    parser.add_argument(
        "--check-budgets",
        action="store_true",
        help="Exit non-zero if recall@10 or latency budgets are violated",
    )
    args = parser.parse_args()

    report = run_benchmark()
    if args.json_path:
        Path(args.json_path).write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8"
        )

    print(
        f"retrieval benchmark ({report['backend']}): "
        f"recall@10={report['mean_recall_at_10']:.4f}, "
        f"query_p95={report['query_p95_ms']:.3f}ms, "
        f"incremental={report['incremental_refresh_ms']:.3f}ms ({report['incremental_refresh_mode']})"
    )

    if args.check_budgets:
        failures = []
        if report["mean_recall_at_10"] < MIN_RECALL_AT_10:
            failures.append(
                f"mean_recall_at_10 {report['mean_recall_at_10']} < {MIN_RECALL_AT_10}"
            )
        if report["query_p95_ms"] > MAX_QUERY_P95_MS:
            failures.append(
                f"query_p95_ms {report['query_p95_ms']} > {MAX_QUERY_P95_MS}"
            )
        if report["incremental_refresh_ms"] > MAX_INCREMENTAL_REFRESH_MS:
            failures.append(
                f"incremental_refresh_ms {report['incremental_refresh_ms']} > {MAX_INCREMENTAL_REFRESH_MS}"
            )
        if failures:
            raise SystemExit("Benchmark budget regression: " + "; ".join(failures))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
