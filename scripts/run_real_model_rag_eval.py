#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DATASET_PATH = Path(__file__).resolve().parent.parent / "datasets" / "rag_eval_v1.json"

_CITATION_RE = re.compile(r"\[([A-Za-z0-9_:\-./]+)\]")
_TOKEN_RE = re.compile(r"[A-Za-z0-9_가-힣]+")
_NUMBER_RE = re.compile(r"\b\d+(?:[.,]\d+)?[A-Za-z%]*\b")
_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "that",
        "this",
        "with",
        "from",
        "are",
        "was",
        "were",
        "have",
        "has",
        "had",
        "not",
        "but",
        "you",
        "your",
        "their",
        "its",
        "our",
        "can",
        "will",
        "been",
        "being",
        "into",
        "about",
        "over",
        "under",
        "than",
        "then",
        "when",
        "where",
        "which",
        "while",
        "who",
        "what",
        "also",
        "only",
        "uses",
        "used",
        "using",
        "based",
        "according",
        "evidence",
        "provided",
        "passage",
        "passages",
        "source",
        "sources",
    }
)


def _content_tokens(text: str) -> set[str]:
    return {
        tok.lower()
        for tok in _TOKEN_RE.findall(text)
        if len(tok) >= 2 and tok.lower() not in _STOPWORDS
    }


def _numeric_tokens(text: str) -> set[str]:
    return {m.lower() for m in _NUMBER_RE.findall(text)}


def _split_claims(answer: str) -> list[str]:
    raw_parts = re.split(r"(?<=[.!?])\s+|\n+", answer.strip())
    claims: list[str] = []
    for part in raw_parts:
        cleaned = part.strip().lstrip("-*• ").strip()
        if not cleaned or cleaned.upper() == "ABSTAIN":
            continue
        if len(_content_tokens(_CITATION_RE.sub("", cleaned))) >= 2:
            claims.append(cleaned)
    return claims


def score_grounded_answer(
    answer: str,
    passages: list[dict[str, Any]],
    expected_claims: list[str],
    *,
    expect_abstain: bool = False,
) -> dict[str, Any]:
    valid_doc_ids = {str(p["doc_id"]) for p in passages}
    evidence_text = "\n".join(
        f"{p.get('title', '')} {p.get('text', '')} {p.get('doc_id', '')}"
        for p in passages
    )
    evidence_tokens = _content_tokens(evidence_text)
    evidence_numbers = _numeric_tokens(evidence_text)

    cited_ids = _CITATION_RE.findall(answer)
    valid_citations = sum(1 for cid in cited_ids if cid in valid_doc_ids)
    fabricated_citations = sum(1 for cid in cited_ids if cid not in valid_doc_ids)

    did_abstain = "ABSTAIN" in answer.upper()
    generated_claims = _split_claims(answer)
    supported_generated = 0
    unsupported_claims = 0

    if expect_abstain:
        if not did_abstain:
            unsupported_claims = max(len(generated_claims), 1)
    elif not did_abstain:
        for sentence in generated_claims:
            sent_citations = _CITATION_RE.findall(sentence)
            if any(cid not in valid_doc_ids for cid in sent_citations):
                unsupported_claims += 1
                continue
            body = _CITATION_RE.sub("", sentence)
            tokens = _content_tokens(body)
            nums = _numeric_tokens(body)
            if nums and not nums.issubset(evidence_numbers):
                unsupported_claims += 1
                continue
            overlap = len(tokens & evidence_tokens) / max(len(tokens), 1)
            if overlap >= 0.5:
                supported_generated += 1
            else:
                unsupported_claims += 1

    expected_hits = 0
    expected_total = 0 if expect_abstain else len(expected_claims)
    if not expect_abstain and not did_abstain:
        lower_answer = answer.lower()
        for claim in expected_claims:
            if claim.lower() in lower_answer:
                expected_hits += 1

    return {
        "did_abstain": did_abstain,
        "expected_hits": expected_hits,
        "expected_total": expected_total,
        "supported_generated_claims": supported_generated,
        "unsupported_claims": unsupported_claims,
        "valid_citations": valid_citations,
        "fabricated_citations": fabricated_citations,
    }


def load_retrieval_report(path: str) -> dict[str, Any]:
    if not path:
        return {"status": "not_run", "reason": "no --retrieval-report given"}
    report = json.loads(Path(path).read_text(encoding="utf-8"))
    return {
        "status": "executed",
        "retriever": "product_chat_path_hash_embedder",
        **report,
    }


def _post_chat(
    endpoint: str,
    api_key: str | None,
    model_id: str,
    prompt: str,
    timeout: float = 30.0,
) -> tuple[str, float]:
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
        "execution_mode": "real_model_cloud"
        if execution_location == "cloud"
        else "real_model_local",
        "dataset_version": dataset["dataset_version"],
        "prompt_version": "grounded-abstain-v2",
        "is_real_model": True,
    }
    if not endpoint:
        return {**meta, "status": "skipped", "reason": "endpoint not configured"}

    docs_by_id = {d["doc_id"]: d for d in dataset["documents"]}
    queries = dataset["queries"][:sample_limit]
    abstain_correct = 0
    claim_hits = 0
    claim_total = 0
    unsupported_claims = 0
    valid_citations = 0
    fabricated_citations = 0
    supported_generated = 0
    temporal_total = 0
    temporal_correct = 0
    latencies: list[float] = []

    for q in queries:
        passages = [
            docs_by_id[did]
            for did in q["supporting_span_doc_ids"]
            if did in docs_by_id
            and docs_by_id[did]["workspace_id"] == q["workspace_id"]
        ]
        ctx = (
            "\n".join(f"[{p['doc_id']}] {p['title']}: {p['text']}" for p in passages)
            or "(no evidence)"
        )
        prompt = f"Evidence:\n{ctx}\n\nQuestion: {q['query']}"
        try:
            answer, lat = _post_chat(endpoint, api_key, model_id, prompt)
        except (urllib.error.URLError, TimeoutError, OSError, KeyError) as exc:
            return {
                **meta,
                "status": "unavailable",
                "reason": f"inference failed: {exc}",
            }
        latencies.append(lat)
        scored = score_grounded_answer(
            answer,
            passages,
            q.get("expected_key_claims", []),
            expect_abstain=bool(q["expect_abstain"]),
        )
        if scored["did_abstain"] == bool(q["expect_abstain"]):
            abstain_correct += 1
        claim_hits += scored["expected_hits"]
        claim_total += scored["expected_total"]
        unsupported_claims += scored["unsupported_claims"]
        valid_citations += scored["valid_citations"]
        fabricated_citations += scored["fabricated_citations"]
        supported_generated += scored["supported_generated_claims"]
        if q.get("category") == "temporal_ordering" or q.get(
            "forbidden_or_trap_doc_ids"
        ):
            if not q["expect_abstain"]:
                temporal_total += 1
                if (
                    not scored["did_abstain"]
                    and scored["expected_hits"] == scored["expected_total"]
                ):
                    temporal_correct += 1

    penalized_denom = max(claim_total + unsupported_claims + fabricated_citations, 1)
    claim_support_rate = claim_hits / penalized_denom
    expected_claim_recall = claim_hits / max(claim_total, 1)
    total_citations = valid_citations + fabricated_citations
    citation_precision = (
        valid_citations / total_citations if total_citations > 0 else 1.0
    )

    return {
        **meta,
        "status": "executed",
        "queries_evaluated": len(queries),
        "claim_support_rate": claim_support_rate,
        "expected_claim_recall": expected_claim_recall,
        "unsupported_claims": unsupported_claims,
        "valid_citations": valid_citations,
        "fabricated_citations": fabricated_citations,
        "citation_precision": citation_precision,
        "faithfulness": supported_generated
        / max(supported_generated + unsupported_claims, 1),
        "temporal_cases": temporal_total,
        "recency_accuracy": (temporal_correct / temporal_total)
        if temporal_total
        else 1.0,
        "grounding_gate_passed": unsupported_claims == 0 and fabricated_citations == 0,
        "abstention_accuracy": abstain_correct / max(len(queries), 1),
        "avg_latency_ms": sum(latencies) / max(len(latencies), 1),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run authentic local/cloud real-model RAG evaluation."
    )
    parser.add_argument("--dataset", default=str(DATASET_PATH))
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument(
        "--local-endpoint", default=os.environ.get("LATTICE_EVAL_LOCAL_ENDPOINT", "")
    )
    parser.add_argument(
        "--local-model",
        default=os.environ.get(
            "LATTICE_EVAL_LOCAL_MODEL", "mlx-community/Qwen3.8-27B-4bit"
        ),
    )
    parser.add_argument(
        "--cloud-endpoint", default=os.environ.get("LATTICE_EVAL_CLOUD_ENDPOINT", "")
    )
    parser.add_argument(
        "--cloud-model",
        default=os.environ.get("LATTICE_EVAL_CLOUD_MODEL", "gpt-4o-mini"),
    )
    parser.add_argument(
        "--cloud-api-key", default=os.environ.get("LATTICE_EVAL_CLOUD_API_KEY", "")
    )
    parser.add_argument(
        "--retrieval-report",
        default="",
        help="JSON written by the lattice-retrieval chat-path evaluation",
    )
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
    print(
        json.dumps(
            {
                "dataset_version": dataset["dataset_version"],
                "retrieval": load_retrieval_report(args.retrieval_report),
                "results": results,
            },
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
