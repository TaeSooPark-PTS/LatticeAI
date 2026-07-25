"""Weekly real-model agent smoke — fail-open contract (review Wave 3.4).

The smoke script benchmarks the live agent loop across installed local
models. These tests pin its *contract*, not model behavior: it must import
cleanly, stay fail-open when no model matches (exit 0 with an honest
"skipped" report), and emit parseable JSON with --json.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import importlib.util

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "bench_agent_smoke.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("bench_agent_smoke", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_module_imports_cleanly():
    module = _load_module()
    assert callable(module.main)
    assert callable(module.discover_agent_models)
    assert len(module.SMOKE_TASKS) >= 3


def test_no_matching_model_is_fail_open_exit_zero(capsys):
    module = _load_module()
    # --model with an id that can never exist forces the empty-models path
    # deterministically, even on machines with local models installed.
    exit_code = module.main(["--model", "no-such-model/definitely-absent"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "skipped" in captured.out


def test_json_skip_report_is_parseable(capsys):
    module = _load_module()
    exit_code = module.main(["--json", "--model", "no-such-model/definitely-absent"])
    captured = capsys.readouterr()
    assert exit_code == 0
    report = json.loads(captured.out)
    assert report["status"] == "skipped"
    assert report["mode"] == "agent-smoke"
    assert report["models"] == []


def test_run_agent_smoke_reports_skip_shape_without_models():
    module = _load_module()
    report = module.run_agent_smoke(models=[], tasks=module.SMOKE_TASKS[:1], max_steps=2)
    assert report["status"] == "skipped"
    assert "no models available" in report["reason"]
