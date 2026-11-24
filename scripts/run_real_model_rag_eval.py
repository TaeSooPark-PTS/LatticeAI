#!/usr/bin/env python3
"""Authentic Real-Model vs. Fixture RAG Evaluation Runner (P0.1).

Strictly separates deterministic fixture runs from real local (omlx/Ollama/llama.cpp)
and cloud LLM inference, records full provenance metadata, and explicitly marks
unconfigured targets as `skipped` or `unavailable`.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DATASET_PATH = Path(__file__).resolve().parent.parent / "datasets" / "rag_eval_v1.json"


def _post_chat(endpoint: str, api_key: str | None, model_id: str, prompt: str, timeout: float = 30.0) -> tuple[str, float]:
    url = endpoint.rstrip("/") + "/chat/completions"
    payload = json.dumps(
        {
            "model": model_id,
            "temperature": 0.0,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Answer ONLY from the provided evidence passages. Cite doc_id in brackets. "
                        "If evidence is insufficient or workspace-restricted, reply with ABSTAIN."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
        }
    ).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    latency_ms = (time.perf_counter() - t0) * 1000.0
    content = body["choices"][0]["message"]["content"]
    return str(content), latency_ms


def evaluate_target(
    dataset: dict[str, Any],
    *,
    model_id: str,
    model_version: str,
    provider: str,
    execution_location: str,
    endpoint: str | None,
    api_key: str | None,
    sample_limit: int,
) -> dict[str, Any]:
    meta = {
        "model_id": model_id,
        "model_version": model_version,
        "provider": provider,
        "execution_location": execution_location,
        "execution_mode": "real_model_cloud" if execution_location == "cloud" else "real_model_local",
        "dataset_version": dataset["dataset_version"],
        "prompt_version": "grounded-abstain-v1",
        "is_real_model": True,
    }
    if not endpoint:
        return {**meta, "status": "skipped", "reason": "endpoint not configured"}

    docs_by_id = {d["doc_id"]: d for d in dataset["documents"]}
    queries = dataset["queries"][:sample_limit]
    answered = 0
    abstain_correct = 0
    claim_hits = 0
    claim_total = 0
    latencies: list[float] = []

    for q in queries:
        passages = [
            docs_by_id[did]
            for did in q["supporting_span_doc_ids"]
            if did in docs_by_id and docs_by_id[did]["workspace_id"] == q["workspace_id"]
        ]
        ctx = "\n".join(f"[{p['doc_id']}] {p['title']}: {p['text']}" for p in passages) or "(no evidence)"
        prompt = f"Evidence:\n{ctx}\n\nQuestion: {q['query']}"
        try:
            answer, lat = _post_chat(endpoint, api_key, model_id, prompt)
        except (urllib.error.URLError, TimeoutError, OSError, KeyError) as exc:
            return {**meta, "status": "unavailable", "reason": f"inference failed: {exc}"}
        latencies.append(lat)
        did_abstain = "ABSTAIN" in answer.upper()
        if did_abstain == bool(q["expect_abstain"]):
            abstain_correct += 1
        if not q["expect_abstain"]:
            answered += 1
            for claim in q["expected_key_claims"]:
                claim_total += 1
                if claim.lower() in answer.lower():
                    claim_hits += 1

    return {
        **meta,
        "status": "executed",
        "queries_evaluated": len(queries),
        "claim_support_rate": claim_hits / max(claim_total, 1),
        "abstention_accuracy": abstain_correct / max(len(queries), 1),
        "avg_latency_ms": sum(latencies) / max(len(latencies), 1),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run authentic local/cloud real-model RAG evaluation.")
    parser.add_argument("--dataset", default=str(DATASET_PATH))
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--local-endpoint", default=os.environ.get("LATTICE_EVAL_LOCAL_ENDPOINT", ""))
    parser.add_argument("--local-model", default=os.environ.get("LATTICE_EVAL_LOCAL_MODEL", "mlx-community/Qwen3.8-27B-4bit"))
    parser.add_argument("--cloud-endpoint", default=os.environ.get("LATTICE_EVAL_CLOUD_ENDPOINT", ""))
    parser.add_argument("--cloud-model", default=os.environ.get("LATTICE_EVAL_CLOUD_MODEL", "gpt-4o-mini"))
    parser.add_argument("--cloud-api-key", default=os.environ.get("LATTICE_EVAL_CLOUD_API_KEY", ""))
    args = parser.parse_args()

    dataset = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    results = [
        evaluate_target(
            dataset,
            model_id=args.local_model,
            model_version="live",
            provider="local_openai_compat",
            execution_location="local",
            endpoint=args.local_endpoint or None,
            api_key=None,
            sample_limit=args.limit,
        ),
        evaluate_target(
            dataset,
            model_id=args.cloud_model,
            model_version="live",
            provider="cloud_openai_compat",
            execution_location="cloud",
            endpoint=args.cloud_endpoint or None,
            api_key=args.cloud_api_key or None,
            sample_limit=args.limit,
        ),
    ]
    print(json.dumps({"dataset_version": dataset["dataset_version"], "results": results}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
