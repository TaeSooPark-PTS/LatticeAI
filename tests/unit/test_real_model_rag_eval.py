"""Unit tests for the real-model RAG evaluation grounding gate (v13.9.0 / PR #69 Fix #5)."""

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
