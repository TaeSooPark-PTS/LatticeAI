#!/usr/bin/env python3

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
DATASET = REPO / "datasets" / "rag_eval_v1.json"
DIR_ENV = "LATTICE_NEURAL_EVAL_DIR"
BATCH = 16
LATENCY_SAMPLES = 20
INPUTS_TEST = "the_chat_retriever_embedding_inputs_are_written"
MEASURE_TEST = "the_chat_retriever_is_measured_with_real_model_vectors"


def product_module(name: str) -> Any:
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    return importlib.import_module(name)


def timed(call: Any, *args: Any) -> tuple[Any, float]:
    started = time.perf_counter()
    result = call(*args)
    return result, round((time.perf_counter() - started) * 1000, 1)


def percentiles(samples: list[float]) -> dict[str, float]:
    ordered = sorted(samples)
    return {
        "p50_ms": round(statistics.median(ordered), 1),
        "p95_ms": ordered[int(round(0.95 * (len(ordered) - 1)))],
        "max_ms": ordered[-1],
    }


def embed_all(provider: Any, texts: dict[str, str], kind: str) -> dict[str, Any]:
    keys = list(texts)
    vectors: dict[str, Any] = {}
    for start in range(0, len(keys), BATCH):
        chunk = keys[start : start + BATCH]
        rows = provider.embed_batch_for([texts[key] for key in chunk], kind)
        vectors.update(dict(zip(chunk, rows, strict=True)))
    return vectors


def run_harness(workdir: Path, test_name: str) -> None:
    subprocess.run(
        [
            "cargo",
            "test",
            "--release",
            "-q",
            "-p",
            "lattice-retrieval",
            "--test",
            "neural_rag_eval",
            "--",
            "--ignored",
            "--exact",
            test_name,
        ],
        cwd=REPO / "rust",
        env={**os.environ, DIR_ENV: str(workdir)},
        check=True,
    )


def evaluate(workdir: Path) -> dict[str, Any]:
    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    embeddings_module = product_module("latticeai.core.embedding_providers")
    rerankers_module = product_module("latticeai.core.rerankers")

    embedder = embeddings_module.resolve_embedder("auto", probe=False)
    if embedder.active != "huggingface":
        raise SystemExit(
            f"auto resolved {embedder.active!r}, not a Hugging Face model: {embedder.detail}"
        )
    provider = embedder.provider
    reranker = rerankers_module.resolve_reranker().reranker
    if reranker is None:
        raise SystemExit(rerankers_module.resolve_reranker().detail)

    run_harness(workdir, INPUTS_TEST)
    inputs = json.loads((workdir / "embedding_inputs.json").read_text(encoding="utf-8"))
    documents: dict[str, str] = inputs["documents"]
    queries: dict[str, str] = inputs["queries"]

    _, embed_cold_ms = timed(provider.embed_batch_for, ["warm up"], "query")
    embed_warm = [
        timed(provider.embed_batch_for, [text], "query")[1]
        for text in list(queries.values())[:LATENCY_SAMPLES]
    ]
    document_vectors, documents_ms = timed(embed_all, provider, documents, "passage")
    query_vectors, queries_ms = timed(embed_all, provider, queries, "query")
    (workdir / "embeddings.json").write_text(
        json.dumps(
            {
                "model_id": provider.model_id,
                "documents": document_vectors,
                "queries": query_vectors,
            }
        ),
        encoding="utf-8",
    )
    (workdir / "rerank_scores.json").unlink(missing_ok=True)
    run_harness(workdir, MEASURE_TEST)

    by_mode = json.loads((workdir / "candidates.json").read_text(encoding="utf-8"))
    _, rerank_cold_ms = timed(reranker.score, "warm up", ["warm up"])
    scored: dict[tuple[str, tuple[str, ...]], list[float]] = {}
    rerank_ms: list[float] = []
    candidate_counts: list[int] = []
    scores: dict[str, dict[str, dict[str, float]]] = {}
    for mode, records in by_mode.items():
        scores[mode] = {}
        for record in records:
            texts = tuple(item["text"] for item in record["candidates"])
            request = (record["query"], texts)
            if request not in scored:
                values, elapsed = timed(reranker.score, record["query"], list(texts))
                scored[request] = values
                rerank_ms.append(elapsed)
                candidate_counts.append(len(texts))
            scores[mode][record["key"]] = {
                item["id"]: value
                for item, value in zip(
                    record["candidates"], scored[request], strict=True
                )
            }
    (workdir / "rerank_scores.json").write_text(json.dumps(scores), encoding="utf-8")
    run_harness(workdir, MEASURE_TEST)

    return {
        "dataset_version": dataset["dataset_version"],
        "documents": len(documents),
        "queries": len(queries),
        "machine": f"{platform.system()} {platform.machine()}",
        "runtime": {
            name: importlib.metadata.version(name)
            for name in ("sentence-transformers", "transformers", "torch")
        },
        "embedding_model": provider.model_id,
        "reranker_model": reranker.model_id,
        "latency": {
            "embed_first_call_ms": embed_cold_ms,
            "embed_query": percentiles(embed_warm),
            "embed_all_documents_ms": documents_ms,
            "embed_all_queries_ms": queries_ms,
            "rerank_first_call_ms": rerank_cold_ms,
            "rerank_query": percentiles(rerank_ms),
            "rerank_requests": len(rerank_ms),
            "rerank_candidates_per_request": round(
                statistics.mean(candidate_counts), 1
            ),
        },
        "retrieval": json.loads((workdir / "report.json").read_text(encoding="utf-8")),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Measure the chat retriever with cached EmbeddingGemma and cross-encoder weights."
    )
    parser.add_argument("--out", type=Path, help="write the JSON report here")
    parser.add_argument("--workdir", type=Path, help="keep intermediate files here")
    args = parser.parse_args()
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    with tempfile.TemporaryDirectory() as scratch:
        workdir = args.workdir or Path(scratch)
        workdir.mkdir(parents=True, exist_ok=True)
        report = evaluate(workdir.resolve())
    rendered = json.dumps(report, indent=2, ensure_ascii=False)
    if args.out:
        args.out.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
