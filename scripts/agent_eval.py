#!/usr/bin/env python3
"""Agent-loop evaluation gate — native since v11.6.0.

The deterministic scenario suite used to live in
``latticeai.core.agent_eval`` and drive ``SingleAgentRuntime``. That loop is
``lattice-agent`` now. CI still invokes this path; we verify the native suite
is present rather than importing the deleted Python runtime.

Last Python generating tree: commit fc65e60.
Goldens: ``rust/fixtures/agent_loop/golden/`` (FROZEN).
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
REQUIRED = (
    REPO / "rust" / "lattice-agent" / "tests" / "agent_loop.rs",
    REPO / "rust" / "fixtures" / "agent_loop" / "golden",
)


def main() -> int:
    missing = [str(path.relative_to(REPO)) for path in REQUIRED if not path.exists()]
    if missing:
        print("agent-loop-eval: FAIL missing native artifacts:", missing)
        return 1
    print("agent-loop-eval: native lattice-agent suite present (Python loop retired)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
