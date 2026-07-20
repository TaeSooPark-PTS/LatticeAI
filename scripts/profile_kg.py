#!/usr/bin/env python3
"""Synthetic Knowledge Graph performance / memory profiler.

Builds a synthetic knowledge graph against a throwaway SQLite path using the
real ``KnowledgeGraphStore`` API (the same construction pattern the unit tests
use: ``KnowledgeGraphStore(db_path, blob_dir)``), then measures wall-time
percentiles (p50/p95) and tracemalloc peak memory for the hot read/write paths:

* ``ingest_source()``  — ingestion throughput (items/sec)
* ``search()``         — FTS5/LIKE keyword search
* ``context_for_query()`` — RAG context assembly
* ``neighbors()`` / ``traverse()`` — 1-hop and depth-2 graph walks
* ``stats()``          — aggregate counters
* ``rebuild_vector_index()`` / ``vector_search()`` — offline hash-embedder
  vector path (skipped gracefully if the vector path is unavailable)

Everything runs offline: no model downloads, no network, no LLM router — the
concept/triple extractors fall back to their rule-based paths and the default
embedder is the deterministic local hash model.

Honesty note: this is a *synthetic* baseline. Corpus text is generated from a
fixed vocabulary, so extraction density and FTS selectivity differ from real
user data. Timings include tracemalloc instrumentation overhead (allocation
tracking roughly doubles Python allocation cost), so treat absolute numbers as
upper bounds and use them for release-over-release comparison, not marketing.

Usage::

    python scripts/profile_kg.py                      # 5000 sources, 50 queries (~2-2.5 min)
    python scripts/profile_kg.py --nodes 500          # quick run (~10 s)
    python scripts/profile_kg.py --json               # machine-readable output
    python scripts/profile_kg.py --db-path /tmp/kg    # keep the DB around
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lattice_brain.graph.store import KnowledgeGraphStore  # noqa: E402


# ── Synthetic corpus ─────────────────────────────────────────────────────────
# Recurring entities so concept nodes are shared across documents and edges
# actually form; a Korean slice exercises the FTS5 trigram path.
ENTITIES = [
    "Lattice", "GraphStore", "Kubernetes", "Postgres", "FastAPI", "SQLite",
    "Telegram", "MLX", "Embedding", "Pipeline", "Workspace", "Provenance",
    "Scheduler", "Retrieval", "Ingestion", "Router", "Registry", "Backup",
]
FILLER = (
    "the service processes requests and stores results in the database "
    "while the worker retries failed jobs and reports metrics to the "
    "dashboard for the on-call engineer to review during the incident "
).split()
KOREAN = ["프로젝트", "일정", "회의", "결정", "지식그래프", "검색", "성능", "메모리"]


def _make_text(rng: random.Random, size: str) -> str:
    lengths = {"small": 40, "medium": 160, "large": 520}  # words
    words: List[str] = []
    target = lengths[size]
    while len(words) < target:
        words.extend(rng.sample(FILLER, k=min(8, target - len(words))))
        if rng.random() < 0.6:
            words.append(rng.choice(ENTITIES))
        if rng.random() < 0.25:
            words.append(rng.choice(KOREAN))
        if rng.random() < 0.1:
            words.append(f"{rng.choice(ENTITIES)} improves {rng.choice(ENTITIES)}.")
    return " ".join(words)


def _pick_size(rng: random.Random) -> str:
    r = rng.random()
    if r < 0.6:
        return "small"
    if r < 0.9:
        return "medium"
    return "large"


# ── Measurement helpers ──────────────────────────────────────────────────────
def _percentiles(samples_s: List[float]) -> Dict[str, float]:
    if not samples_s:
        return {"p50_ms": 0.0, "p95_ms": 0.0, "mean_ms": 0.0}
    ordered = sorted(samples_s)

    def q(p: float) -> float:
        idx = min(len(ordered) - 1, max(0, round(p * (len(ordered) - 1))))
        return ordered[idx]

    return {
        "p50_ms": round(q(0.50) * 1000, 3),
        "p95_ms": round(q(0.95) * 1000, 3),
        "mean_ms": round(statistics.fmean(ordered) * 1000, 3),
    }


def _timed_phase(fn: Callable[[], List[float]]) -> Dict[str, Any]:
    """Run one phase; return sample percentiles + phase peak memory."""
    tracemalloc.reset_peak()
    wall_start = time.perf_counter()
    samples = fn()
    wall = time.perf_counter() - wall_start
    _, peak = tracemalloc.get_traced_memory()
    result = _percentiles(samples)
    result.update(
        {
            "calls": len(samples),
            "wall_s": round(wall, 3),
            "peak_mem_mb": round(peak / (1024 * 1024), 2),
        }
    )
    return result


# ── Profiler ─────────────────────────────────────────────────────────────────
def run_profile(
    nodes: int,
    queries: int,
    db_path: Optional[Path],
    seed: int = 42,
) -> Dict[str, Any]:
    rng = random.Random(seed)
    tmp_ctx = None
    if db_path is None:
        tmp_ctx = tempfile.TemporaryDirectory(prefix="ltcai-profile-kg-")
        base = Path(tmp_ctx.name)
    else:
        base = Path(db_path)
        base.mkdir(parents=True, exist_ok=True)

    report: Dict[str, Any] = {
        "schema_version": "kg-profile/v1",
        "params": {"nodes": nodes, "queries": queries, "seed": seed,
                   "db_path": str(base), "ephemeral": tmp_ctx is not None},
        "phases": {},
        "notes": [],
    }

    tracemalloc.start()
    try:
        store = KnowledgeGraphStore(base / "graph.sqlite", base / "blobs")
        report["fts_enabled"] = bool(getattr(store, "_fts_enabled", False))

        # Phase 1: ingestion ------------------------------------------------
        doc_ids: List[str] = []
        char_total = 0

        def _ingest() -> List[float]:
            nonlocal char_total
            samples: List[float] = []
            for i in range(nodes):
                size = _pick_size(rng)
                text = _make_text(rng, size)
                char_total += len(text)
                t0 = time.perf_counter()
                result = store.ingest_source(
                    source_type="note",
                    title=f"Synthetic note {i} {rng.choice(ENTITIES)}",
                    text=text,
                    source_uri=f"synthetic://note/{i}",
                    owner="profiler@example.com",
                )
                samples.append(time.perf_counter() - t0)
                doc_ids.append(result["node_id"])
            return samples

        ingest = _timed_phase(_ingest)
        ingest["items_per_sec"] = round(nodes / ingest["wall_s"], 1) if ingest["wall_s"] else 0.0
        ingest["corpus_chars"] = char_total
        report["phases"]["ingest_source"] = ingest

        # Query terms: mix entity names, Korean tokens, and rare misses.
        terms = [rng.choice(ENTITIES + KOREAN) for _ in range(max(1, queries))]
        for i in range(0, len(terms), 10):
            terms[i] = f"missing-term-{i}"  # cache-unfriendly misses

        # Phase 2: search ----------------------------------------------------
        def _search() -> List[float]:
            samples = []
            for term in terms:
                t0 = time.perf_counter()
                store.search(term, limit=30)
                samples.append(time.perf_counter() - t0)
            return samples

        report["phases"]["search"] = _timed_phase(_search)

        # Phase 3: context_for_query ----------------------------------------
        def _context() -> List[float]:
            samples = []
            for term in terms:
                t0 = time.perf_counter()
                store.context_for_query(term, limit=6)
                samples.append(time.perf_counter() - t0)
            return samples

        report["phases"]["context_for_query"] = _timed_phase(_context)

        # Phase 4: neighbors / traverse -------------------------------------
        sample_ids = rng.sample(doc_ids, k=min(len(doc_ids), max(1, queries)))

        def _neighbors() -> List[float]:
            samples = []
            for node_id in sample_ids:
                t0 = time.perf_counter()
                store.neighbors(node_id)
                samples.append(time.perf_counter() - t0)
            return samples

        report["phases"]["neighbors"] = _timed_phase(_neighbors)

        def _traverse() -> List[float]:
            samples = []
            for node_id in sample_ids:
                t0 = time.perf_counter()
                store.traverse(node_id, depth=2, limit=100)
                samples.append(time.perf_counter() - t0)
            return samples

        report["phases"]["traverse_depth2"] = _timed_phase(_traverse)

        # Phase 5: stats -----------------------------------------------------
        def _stats() -> List[float]:
            samples = []
            for _ in range(5):
                t0 = time.perf_counter()
                store.stats()
                samples.append(time.perf_counter() - t0)
            return samples

        report["phases"]["stats"] = _timed_phase(_stats)
        graph_stats = store.stats()
        node_counts = graph_stats.get("nodes") or {}
        edge_counts = graph_stats.get("edges") or {}
        report["graph"] = {
            "nodes_total": sum(node_counts.values()) if isinstance(node_counts, dict) else node_counts,
            "edges_total": sum(edge_counts.values()) if isinstance(edge_counts, dict) else edge_counts,
            "by_node_type": node_counts if isinstance(node_counts, dict) else None,
        }

        # Phase 6: vector index (offline hash embedder) ----------------------
        try:
            def _vector_build() -> List[float]:
                t0 = time.perf_counter()
                store.rebuild_vector_index(full=True, include_nodes=True, include_chunks=True)
                return [time.perf_counter() - t0]

            report["phases"]["rebuild_vector_index"] = _timed_phase(_vector_build)

            # vector_search is a brute-force scan over all stored embeddings
            # (O(index size) per query); cap the query count so the default
            # profile stays inside its time budget while still sampling p50/p95.
            vector_terms = terms[: min(len(terms), 10)]

            def _vector_search() -> List[float]:
                samples = []
                for term in vector_terms:
                    t0 = time.perf_counter()
                    store.vector_search(term, limit=30)
                    samples.append(time.perf_counter() - t0)
                return samples

            report["phases"]["vector_search"] = _timed_phase(_vector_search)
            report["notes"].append(
                "Vector phase uses the built-in deterministic hash embedder "
                "(offline); real embedding providers will be slower to index "
                "but produce semantically meaningful rankings."
            )
        except Exception as exc:  # pragma: no cover - environment dependent
            report["phases"]["vector"] = {"skipped": True, "reason": str(exc)[:200]}
            report["notes"].append(
                "Vector phase skipped: the vector path was unavailable in this "
                "environment (no embedding provider required for the rest of "
                "the profile)."
            )

        current, overall_peak = tracemalloc.get_traced_memory()
        report["memory"] = {
            "tracemalloc_current_mb": round(current / (1024 * 1024), 2),
            "phase_peaks_are_reset_per_phase": True,
        }
        report["db_size_mb"] = round((base / "graph.sqlite").stat().st_size / (1024 * 1024), 2)
        report["notes"].append(
            "Synthetic baseline: fixed-vocabulary corpus, rule-based extraction, "
            "tracemalloc enabled for the whole run (adds allocation-tracking "
            "overhead to every timing)."
        )
    finally:
        tracemalloc.stop()
        if tmp_ctx is not None:
            tmp_ctx.cleanup()

    return report


def _print_report(report: Dict[str, Any]) -> None:
    p = report["params"]
    print("Knowledge Graph synthetic profile")
    print(f"  sources={p['nodes']}  queries={p['queries']}  seed={p['seed']}")
    print(f"  db={p['db_path']}  ephemeral={p['ephemeral']}  fts_enabled={report.get('fts_enabled')}")
    graph = report.get("graph") or {}
    print(f"  graph: nodes={graph.get('nodes_total')}  edges={graph.get('edges_total')}  db_size={report.get('db_size_mb')} MB")
    print()
    header = f"  {'phase':<22}{'calls':>6}{'p50 ms':>10}{'p95 ms':>10}{'mean ms':>10}{'wall s':>9}{'peak MB':>9}"
    print(header)
    print("  " + "-" * (len(header) - 2))
    for name, phase in report["phases"].items():
        if phase.get("skipped"):
            print(f"  {name:<22}  skipped: {phase.get('reason', '')}")
            continue
        print(
            f"  {name:<22}{phase['calls']:>6}{phase['p50_ms']:>10}{phase['p95_ms']:>10}"
            f"{phase['mean_ms']:>10}{phase['wall_s']:>9}{phase['peak_mem_mb']:>9}"
        )
        if "items_per_sec" in phase:
            print(f"  {'':<22}  throughput: {phase['items_per_sec']} items/sec  corpus: {phase['corpus_chars']} chars")
    print()
    for note in report.get("notes", []):
        print(f"  note: {note}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Profile KnowledgeGraphStore on a synthetic corpus (offline).")
    parser.add_argument("--nodes", type=int, default=5000, help="synthetic sources to ingest (default: 5000)")
    parser.add_argument("--queries", type=int, default=50, help="queries per read phase (default: 50)")
    parser.add_argument("--db-path", type=Path, default=None, help="directory for the profile DB (default: temp dir, auto-removed)")
    parser.add_argument("--json", action="store_true", help="emit the full report as JSON")
    parser.add_argument("--seed", type=int, default=42, help="corpus RNG seed (default: 42)")
    args = parser.parse_args(argv)

    if args.nodes < 1 or args.nodes > 200_000:
        parser.error("--nodes must be between 1 and 200000")
    if args.queries < 1 or args.queries > 10_000:
        parser.error("--queries must be between 1 and 10000")

    report = run_profile(args.nodes, args.queries, args.db_path, seed=args.seed)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
