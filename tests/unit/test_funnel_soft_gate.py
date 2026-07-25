"""Funnel soft gate tests (scripts/funnel_soft_gate.py).

The gate must stay advisory by default (always exit 0), enforce only under
--strict, never warn on rates that have no denominator yet, and grade through
FunnelMetricsService.snapshot() so its math can never drift from production.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "funnel_soft_gate.py"
spec = importlib.util.spec_from_file_location("funnel_soft_gate", MODULE_PATH)
funnel_soft_gate = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(funnel_soft_gate)


def _write_metrics(tmp_path, **counters):
    path = tmp_path / "funnel_metrics.json"
    path.write_text(json.dumps(counters), encoding="utf-8")
    return path


_HEALTHY = dict(
    file_requests=100, real_file_delivered=98, code_only_responses=2,
    agent_runs=10, needs_review_runs=1,
)
# code_only_rate = 2/10 = 0.2 — well above the 0.05 goal boundary.
_BAD_CODE_ONLY = dict(
    file_requests=10, real_file_delivered=8, code_only_responses=2,
    agent_runs=10, needs_review_runs=1,
)


# ── healthy metrics ─────────────────────────────────────────────────────

def test_healthy_metrics_pass_with_no_warnings(tmp_path, capsys):
    path = _write_metrics(tmp_path, **_HEALTHY)
    assert funnel_soft_gate.main(["--path", str(path)]) == 0
    out = capsys.readouterr().out
    assert "verdict: OK" in out
    assert "WARN" not in out
    # Healthy stays green even under strict enforcement.
    assert funnel_soft_gate.main(["--path", str(path), "--strict"]) == 0


def test_threshold_boundary_is_not_a_warning(tmp_path, capsys):
    # Exactly 0.05 sits ON the goal boundary — WARN fires only strictly above.
    path = _write_metrics(
        tmp_path, file_requests=100, real_file_delivered=95, code_only_responses=5,
    )
    assert funnel_soft_gate.main(["--path", str(path), "--strict"]) == 0
    assert "verdict: OK" in capsys.readouterr().out


# ── bad code_only rate ──────────────────────────────────────────────────

def test_bad_code_only_rate_warns_but_exits_zero_by_default(tmp_path, capsys):
    path = _write_metrics(tmp_path, **_BAD_CODE_ONLY)
    assert funnel_soft_gate.main(["--path", str(path)]) == 0, "advisory mode never fails"
    out = capsys.readouterr().out
    assert "code_only_rate" in out
    assert "WARN" in out
    assert "advisory" in out


def test_bad_code_only_rate_fails_under_strict(tmp_path):
    path = _write_metrics(tmp_path, **_BAD_CODE_ONLY)
    assert funnel_soft_gate.main(["--path", str(path), "--strict"]) == 1


def test_custom_threshold_flags_override_defaults(tmp_path):
    path = _write_metrics(tmp_path, **_BAD_CODE_ONLY)
    # 0.2 is fine when the operator explicitly relaxes the goal.
    assert funnel_soft_gate.main(
        ["--path", str(path), "--strict", "--code-only-warn", "0.5"]
    ) == 0


def test_bad_needs_review_rate_warns(tmp_path, capsys):
    path = _write_metrics(
        tmp_path, file_requests=10, real_file_delivered=10,
        agent_runs=10, needs_review_runs=4,  # 0.4 > 0.30
    )
    assert funnel_soft_gate.main(["--path", str(path), "--strict"]) == 1
    out = capsys.readouterr().out
    assert "needs_review_rate" in out
    assert "WARN" in out


# ── no data (missing / empty file) ──────────────────────────────────────

def test_missing_file_is_no_data_and_never_fails(tmp_path, capsys):
    path = tmp_path / "does_not_exist.json"
    assert funnel_soft_gate.main(["--path", str(path)]) == 0
    assert "no data" in capsys.readouterr().out
    # "no data" is not a warning — strict mode still exits 0.
    assert funnel_soft_gate.main(["--path", str(path), "--strict"]) == 0


def test_empty_or_corrupt_file_is_no_data(tmp_path, capsys):
    path = tmp_path / "funnel_metrics.json"
    path.write_text("", encoding="utf-8")
    assert funnel_soft_gate.main(["--path", str(path), "--strict"]) == 0
    assert "no data" in capsys.readouterr().out


# ── machine-readable output ─────────────────────────────────────────────

def test_json_output_is_machine_readable(tmp_path, capsys):
    path = _write_metrics(tmp_path, **_BAD_CODE_ONLY)
    assert funnel_soft_gate.main(["--path", str(path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "funnel-soft-gate"
    assert payload["status"] == "warn"
    assert payload["warnings"] == ["code_only_rate"]
    assert payload["exit_code"] == 0
    by_name = {c["name"]: c for c in payload["checks"]}
    assert by_name["code_only_rate"]["verdict"] == "warn"
    assert by_name["code_only_rate"]["rate"] == 0.2
    assert by_name["needs_review_rate"]["verdict"] == "ok"
    # Informational rates ride along for CI archaeology, never gated.
    assert "real_file_rate" in payload["info"]
    assert "approval_resume_rate" in payload["info"]


def test_json_strict_reports_exit_code_one(tmp_path, capsys):
    path = _write_metrics(tmp_path, **_BAD_CODE_ONLY)
    assert funnel_soft_gate.main(["--path", str(path), "--json", "--strict"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["strict"] is True
    assert payload["exit_code"] == 1
