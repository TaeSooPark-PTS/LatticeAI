#!/usr/bin/env python3
"""Agent-loop evaluation gate (v9.6.0).

Runs the deterministic scenario suite in ``latticeai.core.agent_eval``
against the real SingleAgentRuntime state machine (scripted model, fake
ports) and fails the release when any scenario regresses.

Usage: .venv/bin/python scripts/agent_eval.py [--verbose]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from latticeai.core.agent_eval import run_agent_eval  # noqa: E402


def main() -> int:
    verbose = "--verbose" in sys.argv
    report = run_agent_eval()
    headline = {
        "scenarios": report["scenarios"],
        "passed": report["passed"],
        "success_rate": report["success_rate"],
        "parse_errors": report["parse_errors"],
        "parse_recovered": report["parse_recovered"],
        "recovery_rate": report["recovery_rate"],
    }
    if verbose:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    failures = [r for r in report["results"] if not r["ok"]]
    if failures:
        print(f"agent-loop-eval: FAIL {headline}")
        for result in failures:
            print(f"  ✗ {result['name']}: {'; '.join(result['failures'])}")
        return 1
    print(f"agent-loop-eval: OK {headline}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
