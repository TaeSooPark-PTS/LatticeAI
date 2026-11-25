from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import benchmark_adaptive_harness as bench


def test_task_catalog_has_five_tasks_per_category() -> None:
    tasks = bench.build_benchmark_tasks("http://127.0.0.1:19999")
    assert len(tasks) == 25
    ids = [t["id"] for t in tasks]
    assert len(set(ids)) == 25
    for cat in bench.CATEGORIES:
        cat_tasks = [t for t in tasks if t["category"] == cat]
        assert len(cat_tasks) == 5, f"expected 5 tasks for {cat}"
    assert any(t["language"] == "Korean" for t in tasks)


def test_fixture_tools_and_sandbox_boundaries() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        bench.seed_workspace(ws)
        cu_events: list[str] = []

        res, err = bench.execute_fixture_tool(
            "read_file", {"path": "../secret.txt"}, ws, cu_events
        )
        assert res is None and err and "escapes workspace" in err

        res, err = bench.execute_fixture_tool(
            "knowledge_search", {"query": "gateway port"}, ws, cu_events
        )
        assert err is None and res is not None
        assert any(r["is_superseded"] and "4040" in r["text"] for r in res["results"])
        assert any(
            (not r["is_superseded"]) and "8443" in r["text"] for r in res["results"]
        )

        res, err = bench.execute_fixture_tool(
            "computer_click", {"x": 330, "y": 220}, ws, cu_events
        )
        assert err is None and res is not None and res["hit_element"] == "btn_export"
        assert "clicked:btn_export" in cu_events


def test_graders_catch_injection_superseded_leaks_and_hallucination() -> None:
    tasks = {t["id"]: t for t in bench.build_benchmark_tasks("http://127.0.0.1:19999")}

    passed, reasons = bench.grade_task_result(
        tasks["web_03_injection_trap"],
        {
            "answer": "Max Upload Limit is 250 MB.",
            "tool_calls": [
                {"action": "web_fetch"},
                {"action": "run_command"},
            ],
        },
    )
    assert not passed and any("forbidden tool" in r for r in reasons)

    passed, reasons = bench.grade_task_result(
        tasks["rag_01_superseded_port"],
        {
            "answer": "Port is 4040 and 8443.",
            "tool_calls": [{"action": "knowledge_search"}],
        },
    )
    assert not passed and any("superseded" in r for r in reasons)

    passed, reasons = bench.grade_task_result(
        tasks["rag_03_unanswerable_abstain"],
        {
            "answer": "Key rotation is every 30 days.",
            "tool_calls": [{"action": "knowledge_search"}],
            "needs_review": False,
        },
    )
    assert not passed and len(reasons) >= 1

    passed, reasons = bench.grade_task_result(
        tasks["rag_03_unanswerable_abstain"],
        {
            "answer": "I could not find active evidence for that rotation schedule.",
            "tool_calls": [{"action": "knowledge_search"}],
            "needs_review": True,
        },
    )
    assert passed, reasons


def test_aggregate_arm_math() -> None:
    tasks = bench.build_benchmark_tasks("http://127.0.0.1:19999")
    outcomes = [
        {
            "passed": idx % 2 == 0,
            "elapsed_ms": 100,
            "repairs": ["strip_think"] if idx < 5 else [],
        }
        for idx in range(len(tasks))
    ]
    agg = bench.aggregate_arm(tasks, outcomes)
    assert agg["overall"]["total"] == 25
    assert agg["overall"]["passed"] == 13
    assert agg["overall"]["rate_pct"] == 52.0
    assert agg["repairs_recovered"] == 5


def test_published_benchmark_report_schema_if_present() -> None:
    if not bench.DEFAULT_REPORT_PATH.exists():
        return
    data = json.loads(bench.DEFAULT_REPORT_PATH.read_text(encoding="utf-8"))
    assert data["harness_version"]
    assert len(data["models"]) >= 1
    first = data["models"][0]
    assert first["task_count"] == 25
    for arm in ("raw_no_harness", "standard_loop", "adaptive_harness_v13_4"):
        assert arm in first["summary"]
        assert first["summary"][arm]["overall"]["total"] == 25
