"""Filegen benchmark harness tests (scripts/bench_models.py --filegen).

The weekly multi-model report must work with a stubbed model (CI has no
local model) and must be fail-open: no installed models → skip report and
exit code 0, never a crash and never a CI failure.
"""

from __future__ import annotations

import asyncio
import importlib.util
import re
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "bench_models.py"
spec = importlib.util.spec_from_file_location("bench_models", MODULE_PATH)
bench_models = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(bench_models)

_FULL_HTML = (
    "<!DOCTYPE html>\n<html>\n<head><meta charset=\"utf-8\">"
    "<title>Stub</title></head>\n<body><h1>Stub</h1></body>\n</html>"
)

_DIRTY_BY_EXT = {
    # Model-realistic dirty-but-recoverable outputs per file type: the real
    # extraction pass must strip the wrappers before validation.
    "html": f"Sure! Here is the page:\n```html\n{_FULL_HTML}\n```\nEnjoy!",
    "css": "```css\nbody { font-family: sans-serif; }\n.card { padding: 1rem; }\n```",
    "js": "Here you go:\n```js\nconst items = [];\nfunction add(x) { items.push(x); }\n```",
    "py": "```python\nimport sys\n\nprint(len(sys.argv))\n```",
    "json": 'Here is the data:\n{"books": [{"title": "A", "year": 2001}]}\nHope this helps!',
    "md": "# Notes\n\n## One\n\n- a\n- b\n",
}


def _ext_from_context(context: str) -> str:
    match = re.search(r"`[\w./-]+\.(\w+)`", context)
    assert match, f"target path not found in generation context: {context[:200]}"
    return match.group(1)


async def _dirty_stub(context: str) -> str:
    return _DIRTY_BY_EXT[_ext_from_context(context)]


async def _garbage_stub(context: str) -> str:
    return "blah blah nonsense output with no usable structure"


def test_stubbed_model_dirty_outputs_all_recovered_without_repair():
    result = asyncio.run(bench_models.bench_filegen_model("stub-model", _dirty_stub))
    assert result["model"] == "stub-model"
    assert result["total"] == len(bench_models.FILEGEN_TARGETS)
    # Every dirty output was recoverable by extraction alone: valid, no
    # deterministic-repair fallback, first attempt after extraction.
    assert result["success_rate"] == 1.0
    assert result["clean_rate"] == 1.0
    for row in result["targets"]:
        assert row["valid"], row
        assert not row["repaired"], row
        assert row["attempts"] == 1, row


def test_stubbed_model_garbage_outputs_report_repair_honestly():
    result = asyncio.run(bench_models.bench_filegen_model("weak-stub", _garbage_stub))
    rows = {row["type"]: row for row in result["targets"]}
    # Every attempt fails validation, then deterministic repair delivers a
    # structurally valid file for the scaffoldable types.
    #
    # Three attempts, not two: this stub returns a byte-identical reply every
    # time, which is the case `generate_file_content` spends its single extra
    # escalation call on. The count is asserted exactly so the escalation
    # stays bounded — a model stuck in a loop must cost one extra call, not
    # an unbounded number of them.
    for ext in ("html", "json"):
        assert rows[ext]["valid"], rows[ext]
        assert rows[ext]["repaired"], rows[ext]
        assert rows[ext]["attempts"] == 3, rows[ext]
    # The aggregate must equal the per-row truth (no rigged success rate).
    valid_count = sum(1 for row in result["targets"] if row["valid"])
    assert result["success_rate"] == round(valid_count / result["total"], 4)
    # Nothing that needed repair may count toward the clean rate.
    clean_count = sum(1 for row in result["targets"] if row["valid"] and not row["repaired"])
    assert result["clean_rate"] == round(clean_count / result["total"], 4)


def test_report_formatting_marks_repaired_and_failed_rows():
    result = asyncio.run(bench_models.bench_filegen_model("weak-stub", _garbage_stub))
    result["family"] = "stub"
    text = bench_models.format_filegen_report(
        {"mode": "filegen", "skipped": False, "models": [result]}
    )
    assert "weak-stub" in text
    assert "ok(repaired)" in text
    assert "success=" in text


def test_no_models_is_fail_open_skip_report():
    report = bench_models.run_filegen_benchmark(models=[])
    assert report["skipped"] is True
    assert report["models"] == []
    text = bench_models.format_filegen_report(report)
    assert "SKIPPED" in text
    assert "fail-open" in text


def test_cli_filegen_mode_exits_zero_even_when_skipped(monkeypatch, capsys):
    monkeypatch.setattr(
        bench_models, "run_filegen_benchmark",
        lambda models=None: {
            "mode": "filegen", "skipped": True, "reason": "no models", "models": [],
        },
    )
    exit_code = bench_models.main(["--filegen"])
    assert exit_code == 0
    assert "SKIPPED" in capsys.readouterr().out


def test_cli_filegen_mode_exits_zero_on_model_load_failures(monkeypatch, capsys):
    # A discovered model that fails to load is reported per-model and the
    # script still exits 0 (weekly report, never a gate).
    monkeypatch.setattr(
        bench_models, "run_filegen_benchmark",
        lambda models=None: {
            "mode": "filegen", "skipped": False,
            "models": [{
                "model": "mlx-community/qwen-stub", "family": "qwen",
                "skipped": True, "reason": "load failed: no metal device",
            }],
        },
    )
    exit_code = bench_models.main(["--filegen"])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "SKIPPED" in out
    assert "load failed" in out
