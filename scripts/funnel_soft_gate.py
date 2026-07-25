#!/usr/bin/env python3
"""Funnel soft gate (review Wave 3.3) — funnel metrics as an advisory verdict.

Reads the same ``funnel_metrics.json`` production writes and grades it through
``FunnelMetricsService.snapshot()`` — no rate logic is duplicated here. Two
funnel-health rates carry WARN thresholds:

* ``code_only_rate``    — WARN above 0.05 (goal: >95% of recognized file
  requests deliver a real artifact, not a code/prose-only answer);
* ``needs_review_rate`` — WARN above 0.30 (verifier fail-closed is healthy,
  but a third of runs ending NEEDS_REVIEW means verification is starving).

Rates whose denominator is still zero come back ``None`` from the snapshot;
they are reported as "no data" and never warn — a fresh install stays green.
``real_file_rate``, ``approval_resume_rate`` and TTFV are shown as
informational context only.

Exit codes
==========
* default         — advisory: ALWAYS ``0``; warnings are printed, not enforced;
* ``--strict``    — CI opt-in: ``1`` when any WARN fired ("no data" never
  fails, even under --strict);
* bad flags       — argparse's usual ``2`` (the only real script error).

Usage
=====
    .venv/bin/python scripts/funnel_soft_gate.py                 # advisory report
    .venv/bin/python scripts/funnel_soft_gate.py --strict        # CI gate opt-in
    .venv/bin/python scripts/funnel_soft_gate.py --json          # machine output
    .venv/bin/python scripts/funnel_soft_gate.py --path /tmp/m.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from latticeai.services.funnel_metrics import FunnelMetricsService  # noqa: E402

# Advisory thresholds (overridable per run via flags). WARN fires strictly
# ABOVE the threshold, so a rate sitting exactly on the goal boundary passes.
CODE_ONLY_WARN_THRESHOLD = 0.05
NEEDS_REVIEW_WARN_THRESHOLD = 0.30


def default_metrics_path() -> Path:
    """The exact file production writes: ``Config.from_env`` data-dir pattern
    (``LATTICEAI_DATA_DIR`` env or ``~/.ltcai``) + the ``funnel_metrics.json``
    filename from ``latticeai/runtime/persistence_runtime.py``."""
    data_dir = Path(os.environ.get("LATTICEAI_DATA_DIR") or (Path.home() / ".ltcai"))
    return data_dir / "funnel_metrics.json"


def evaluate_snapshot(
    snapshot: Dict[str, Any],
    *,
    code_only_warn: float = CODE_ONLY_WARN_THRESHOLD,
    needs_review_warn: float = NEEDS_REVIEW_WARN_THRESHOLD,
) -> Dict[str, Any]:
    """Grade a ``FunnelMetricsService.snapshot()`` payload into verdicts."""
    rates = snapshot.get("rates") or {}
    checks: List[Dict[str, Any]] = []

    def check(name: str, threshold: float, goal: str) -> None:
        rate: Optional[float] = rates.get(name)
        if rate is None:
            verdict, detail = "no_data", "no denominator recorded yet"
        elif rate > threshold:
            verdict, detail = "warn", f"{rate} > {threshold} — {goal}"
        else:
            verdict, detail = "ok", f"{rate} <= {threshold}"
        checks.append({
            "name": name, "rate": rate, "threshold": threshold,
            "verdict": verdict, "detail": detail,
        })

    check("code_only_rate", code_only_warn,
          "goal: >95% of file requests deliver real files")
    check("needs_review_rate", needs_review_warn,
          "goal: <30% of agent runs end NEEDS_REVIEW")

    warnings = [c["name"] for c in checks if c["verdict"] == "warn"]
    if warnings:
        status = "warn"
    elif all(c["verdict"] == "no_data" for c in checks):
        status = "no_data"
    else:
        status = "ok"
    return {
        "status": status,
        "warnings": warnings,
        "checks": checks,
        # Informational context — never gated.
        "info": {
            "real_file_rate": rates.get("real_file_rate"),
            "approval_resume_rate": rates.get("approval_resume_rate"),
            "ttfv_seconds": snapshot.get("ttfv_seconds"),
            "counters": snapshot.get("counters") or {},
        },
    }


def format_report(payload: Dict[str, Any]) -> str:
    lines = [
        f"Funnel soft gate (advisory) — {payload['path']}",
        "=" * 72,
    ]
    for check in payload["checks"]:
        rate = "no data" if check["rate"] is None else str(check["rate"])
        verdict = {"warn": "WARN", "ok": "ok", "no_data": "-"}[check["verdict"]]
        lines.append(
            f"  {check['name']:<20} {rate:<8} {verdict:<5} {check['detail']}"
        )
    info = payload["info"]
    lines.append("-" * 72)
    lines.append(
        f"  info: real_file_rate={info['real_file_rate']} "
        f"approval_resume_rate={info['approval_resume_rate']} "
        f"ttfv_seconds={info['ttfv_seconds']}"
    )
    lines.append("=" * 72)
    if payload["status"] == "warn":
        mode = (
            "STRICT — exiting 1" if payload["strict"]
            else "advisory — exit 0; opt into enforcement with --strict"
        )
        lines.append(f"verdict: WARN ({', '.join(payload['warnings'])}) [{mode}]")
    elif payload["status"] == "no_data":
        lines.append("verdict: no data yet — nothing to grade (exit 0)")
    else:
        lines.append("verdict: OK — funnel rates within goals")
    return "\n".join(lines)


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Advisory funnel-metrics gate (warn on funnel-health regressions)"
    )
    parser.add_argument(
        "--path",
        help="metrics JSON path (default: the production data-dir funnel_metrics.json)",
    )
    parser.add_argument(
        "--strict", action="store_true",
        help="exit 1 when any WARN fired (CI opt-in; 'no data' still exits 0)",
    )
    parser.add_argument(
        "--json", dest="json_out", action="store_true",
        help="print the machine-readable verdict JSON to stdout",
    )
    parser.add_argument(
        "--code-only-warn", type=float, default=CODE_ONLY_WARN_THRESHOLD,
        help=f"code_only_rate WARN threshold (default {CODE_ONLY_WARN_THRESHOLD})",
    )
    parser.add_argument(
        "--needs-review-warn", type=float, default=NEEDS_REVIEW_WARN_THRESHOLD,
        help=f"needs_review_rate WARN threshold (default {NEEDS_REVIEW_WARN_THRESHOLD})",
    )
    args = parser.parse_args(argv)

    path = Path(args.path) if args.path else default_metrics_path()
    # A missing or corrupt file yields the service's zeroed state → all rates
    # None → "no data" (never a crash, never a failure).
    snapshot = FunnelMetricsService(path).snapshot()
    result = evaluate_snapshot(
        snapshot,
        code_only_warn=args.code_only_warn,
        needs_review_warn=args.needs_review_warn,
    )
    exit_code = 1 if (args.strict and result["status"] == "warn") else 0
    payload = {
        "mode": "funnel-soft-gate",
        "path": str(path),
        "strict": bool(args.strict),
        "exit_code": exit_code,
        **result,
    }
    if args.json_out:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(format_report(payload))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
