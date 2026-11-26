from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "run_real_model_rag_eval.py"
)


def _load_eval_module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "run_real_model_rag_eval", SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sample_dataset() -> dict[str, Any]:
    return {
        "dataset_version": "rag-eval-unit",
        "documents": [
            {
                "doc_id": "doc:atlas:current",
                "workspace_id": "ws-1",
                "title": "Atlas Operations Spec",
                "text": "Atlas runs on SQLite WAL inside Rust Core. The owner is Lee Jiwon and service port is 4825.",
            }
        ],
        "queries": [
            {
                "query": "Who owns Atlas and what port does it use?",
                "workspace_id": "ws-1",
                "supporting_span_doc_ids": ["doc:atlas:current"],
                "expected_key_claims": ["Lee Jiwon", "4825"],
                "expect_abstain": False,
            }
        ],
    }


def test_grounded_answer_with_valid_citation_scores_100(monkeypatch: Any) -> None:
    mod = _load_eval_module()
    monkeypatch.setattr(
        mod,
        "_post_chat",
        lambda *_args, **_kwargs: (
            "Atlas is owned by Lee Jiwon and uses service port 4825 [doc:atlas:current].",
            12.0,
        ),
    )
    out = mod.evaluate_target(
        _sample_dataset(),
        model_id="test-model",
        model_version="1",
        provider="local_openai_compat",
        execution_location="local",
        endpoint="http://127.0.0.1:9999/v1",
        api_key=None,
        sample_limit=5,
    )
    assert out["status"] == "executed"
    assert out["claim_support_rate"] == 1.0
    assert out["unsupported_claims"] == 0
    assert out["fabricated_citations"] == 0
    assert out["grounding_gate_passed"] is True


def test_fabricated_citation_penalizes_claim_support_rate(monkeypatch: Any) -> None:
    mod = _load_eval_module()
    monkeypatch.setattr(
        mod,
        "_post_chat",
        lambda *_args, **_kwargs: (
            "Atlas is owned by Lee Jiwon and uses port 4825 [doc:fabricated:999].",
            15.0,
        ),
    )
    out = mod.evaluate_target(
        _sample_dataset(),
        model_id="test-model",
        model_version="1",
        provider="local_openai_compat",
        execution_location="local",
        endpoint="http://127.0.0.1:9999/v1",
        api_key=None,
        sample_limit=5,
    )
    assert out["status"] == "executed"
    assert out["fabricated_citations"] == 1
    assert out["claim_support_rate"] < 1.0
    assert out["grounding_gate_passed"] is False


def test_hallucinated_extra_claim_penalizes_claim_support_rate(
    monkeypatch: Any,
) -> None:
    mod = _load_eval_module()
    monkeypatch.setattr(
        mod,
        "_post_chat",
        lambda *_args, **_kwargs: (
            "Atlas is owned by Lee Jiwon on port 4825 [doc:atlas:current]. "
            "It also deploys on Kubernetes cluster 9999 in Antarctica with quantum encryption.",
            15.0,
        ),
    )
    out = mod.evaluate_target(
        _sample_dataset(),
        model_id="test-model",
        model_version="1",
        provider="local_openai_compat",
        execution_location="local",
        endpoint="http://127.0.0.1:9999/v1",
        api_key=None,
        sample_limit=5,
    )
    assert out["status"] == "executed"
    assert out["unsupported_claims"] >= 1
    assert out["claim_support_rate"] < 1.0
    assert out["grounding_gate_passed"] is False


def test_unconfigured_endpoint_reports_skipped() -> None:
    mod = _load_eval_module()
    out = mod.evaluate_target(
        _sample_dataset(),
        model_id="test-model",
        model_version="1",
        provider="local_openai_compat",
        execution_location="local",
        endpoint=None,
        api_key=None,
        sample_limit=5,
    )
    assert out["status"] == "skipped"


def _temporal_dataset() -> dict[str, Any]:
    return {
        "dataset_version": "rag-eval-temporal-unit",
        "documents": [
            {
                "doc_id": "doc:atlas:current",
                "workspace_id": "ws-1",
                "title": "Atlas 2026 Operations Spec",
                "text": "Atlas owner is Lee Jiwon and port is 4825.",
                "valid_from": "2026-01-15T00:00:00Z",
                "valid_to": None,
                "superseded_by": None,
            },
            {
                "doc_id": "doc:atlas:2024",
                "workspace_id": "ws-1",
                "title": "Atlas 2024 Legacy Spec",
                "text": "Atlas owner was Kim Minsoo on legacy port 94825.",
                "valid_from": "2024-01-01T00:00:00Z",
                "valid_to": "2026-01-15T00:00:00Z",
                "superseded_by": "doc:atlas:current",
            },
            {
                "doc_id": "doc:atlas:restricted",
                "workspace_id": "ws-2",
                "title": "Atlas Restricted Budget",
                "text": "Atlas owner budget is confidential.",
                "valid_from": "2026-01-01T00:00:00Z",
                "valid_to": None,
                "superseded_by": None,
            },
        ],
        "queries": [
            {
                "query": "Who is the current Atlas owner?",
                "category": "temporal_ordering",
                "workspace_id": "ws-1",
                "as_of": None,
                "supporting_span_doc_ids": ["doc:atlas:current"],
                "forbidden_or_trap_doc_ids": ["doc:atlas:2024"],
                "expected_key_claims": ["Lee Jiwon"],
                "expect_abstain": False,
            },
            {
                "query": "Who was the Atlas owner in 2024?",
                "category": "temporal_ordering",
                "workspace_id": "ws-1",
                "as_of": "2024-06-01T00:00:00Z",
                "supporting_span_doc_ids": ["doc:atlas:2024"],
                "forbidden_or_trap_doc_ids": [],
                "expected_key_claims": ["Kim Minsoo"],
                "expect_abstain": False,
            },
        ],
    }


def test_retrieval_metrics_report_mrr_ndcg_and_recency() -> None:
    mod = _load_eval_module()
    out = mod.evaluate_retrieval(_temporal_dataset())
    assert out["queries_evaluated"] == 2
    assert out["recall_at_5"] == 1.0
    assert out["mrr"] == 1.0
    assert out["ndcg_at_10"] == 1.0
    assert out["temporal_cases"] == 1
    assert out["recency_accuracy"] == 1.0


def test_retrieval_never_crosses_workspace_or_leaks_superseded() -> None:
    mod = _load_eval_module()
    dataset = _temporal_dataset()
    ranked = mod.rank_documents(dataset, dataset["queries"][0])
    assert "doc:atlas:restricted" not in ranked
    assert "doc:atlas:2024" not in ranked
    assert mod.recency_correct(["old", "new"], ["new"], ["old"]) is False
    assert mod.recency_correct(["new", "old"], ["new"], ["old"]) is True
    assert mod.reciprocal_rank(["x", "y", "z"], ["z"]) == 1.0 / 3


def test_generation_reports_faithfulness_and_recency(monkeypatch: Any) -> None:
    mod = _load_eval_module()
    monkeypatch.setattr(
        mod,
        "_post_chat",
        lambda *_args, **_kwargs: (
            "Atlas owner is Kim Minsoo [doc:atlas:2024].",
            10.0,
        ),
    )
    out = mod.evaluate_target(
        _temporal_dataset(),
        model_id="test-model",
        model_version="1",
        provider="local_openai_compat",
        execution_location="local",
        endpoint="http://127.0.0.1:9999/v1",
        api_key=None,
        sample_limit=5,
    )
    assert out["status"] == "executed"
    assert out["temporal_cases"] == 2
    assert out["recency_accuracy"] == 0.5
    assert 0.0 <= out["faithfulness"] <= 1.0
