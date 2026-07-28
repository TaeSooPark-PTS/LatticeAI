#!/usr/bin/env python3
"""Weekly real-model agent-loop smoke (review Wave 3.4) — FAIL-OPEN.

What it measures
================
``scripts/agent_eval.py`` proves the loop's state machine against *scripted*
model replies. This harness closes the remaining gap: it drives a small set of
canonical agent tasks through the REAL :class:`latticeai.core.agent.
SingleAgentRuntime` (plan → approve → execute → verify), with the generation
port wired to each *locally installed* MLX model — real inference, no HTTP
server. Per model and task it reports:

* ``final_state``   — the loop's honest terminal state (DONE / NEEDS_REVIEW /
  FAILED), plus the ``agent_eval`` result-class bucket;
* ``steps``         — LLM calls the run needed;
* parse repairs     — ``parse_errors`` / ``parse_recovered`` and the LoopTrace
  ``repairs`` histogram (how hard the loop worked to keep this model on rails);
* ``duration_s``    — true end-to-end wall time including inference.

Model access pattern
====================
Identical to ``scripts/bench_models.py --filegen``: installed gemma/qwen/llama
MLX models are discovered through the product's own model catalog + on-disk HF
checks, loaded with the real ``LLMRouter``, and unloaded afterwards. The tool
port is in-memory (canned results, nothing touches disk or network) — this is
a smoke of the LOOP over real model output, not of the tool implementations.

FAIL-OPEN by design
===================
This is a scheduled/weekly report, never a CI gate:

* no models installed (or catalog import fails, e.g. on a CI runner) →
  an honest "no models available — skipped (fail-open)" report, exit 0;
* a model that fails to load → a per-model skip entry, exit 0;
* any run-level crash → a skip report naming the error, exit 0;
* weak results are the finding, never a failure.

Non-zero exits happen only for real script errors (bad flags → argparse's 2).

Usage
=====
    .venv/bin/python scripts/bench_agent_smoke.py               # human table
    .venv/bin/python scripts/bench_agent_smoke.py --json        # JSON to stdout
    .venv/bin/python scripts/bench_agent_smoke.py --tasks 1 --max-steps 6
    .venv/bin/python scripts/bench_agent_smoke.py --model mlx-community/...
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from latticeai.core.agent import (  # noqa: E402
    AgentDeps,
    AgentRunContext,
    AgentState,
    SingleAgentRuntime,
)
from latticeai.core.agent_eval import classify_result  # noqa: E402
from latticeai.core.agent_prompts import (  # noqa: E402
    CRITIC_PROMPT,
    EXECUTOR_PROMPT,
    MEMORY_UPDATER_PROMPT,
    PLANNER_PROMPT,
)
from latticeai.tools import ToolError  # noqa: E402

# ── canonical smoke tasks ────────────────────────────────────────────────
# Small, file-flavored tasks users actually ask for. The tool port is canned,
# so the measurement is "can this model steer the real loop to completion",
# not "is the artifact beautiful".
SMOKE_TASKS: List[Tuple[str, str]] = [
    ("haiku-file", "Write a short haiku about autumn into haiku.txt."),
    ("hello-script", "Create a Python file hello.py that prints hello."),
    (
        "list-then-summarize",
        "List the files in the workspace, then summarize what you see in one sentence.",
    ),
]

_SMOKE_MODEL_FAMILIES = ("gemma", "qwen", "llama")

_AUTO_POLICY = {
    "auto_approve": True, "risk": "low", "shell": False, "network": False,
    "destructive": False, "sandbox": False, "rollback": "none",
}


def discover_agent_models() -> List[Dict[str, str]]:
    """Installed local MLX models — the exact discovery pattern of
    ``scripts/bench_models.py --filegen`` (product catalog + on-disk HF
    checks). Any import/probe failure is fail-open: an empty list, never an
    exception. Messages go to stderr so ``--json`` stdout stays parseable."""
    try:
        from latticeai.models.router import (
            _looks_like_hf_model_dir,
            hf_cache_model_dir,
            hf_model_dir,
        )
        from latticeai.services.model_catalog import ENGINE_MODEL_CATALOG
    except Exception as exc:  # noqa: BLE001 - discovery must never crash the report
        print(
            f"agent-smoke: model catalog unavailable ({exc}); no models discovered",
            file=sys.stderr,
        )
        return []
    found: List[Dict[str, str]] = []
    for entry in ENGINE_MODEL_CATALOG.get("local_mlx", []):
        model_id = str(entry.get("id") or "")
        lowered = model_id.lower()
        family = next((f for f in _SMOKE_MODEL_FAMILIES if f in lowered), None)
        if not family:
            continue
        try:
            downloaded = (
                _looks_like_hf_model_dir(hf_model_dir(model_id))
                or hf_cache_model_dir(model_id) is not None
            )
        except Exception:  # noqa: BLE001
            downloaded = False
        if downloaded:
            found.append({"id": model_id, "family": family})
    return found


# ── HTTP-less single-agent runtime over an in-memory tool port ───────────

class _SmokeReq:
    conversation_id = None
    temperature = 0.2
    workspace_id = None
    source = "agent_smoke"

    def __init__(self, message: str) -> None:
        self.message = message


def _build_smoke_deps(
    generate_as: Callable[..., Awaitable[Any]],
    tool_log: List[Dict[str, Any]],
) -> AgentDeps:
    """Real production prompts + a canned in-memory tool port.

    The port mirrors the fake in ``latticeai.core.agent_eval``: it records
    calls, fails a pathless write like the real dispatcher, and never touches
    disk or network. All tools are auto-approve — governance behaviour is
    agent_eval's job; this smoke measures loop mechanics over real inference.
    """

    async def generate(**kwargs):
        return '{"action": "noop"}'

    def execute_tool(name: str, args: dict) -> dict:
        if name in ("write_file", "generate_file") and not args.get("path"):
            raise ToolError(f"{name} requires args.path")
        tool_log.append({"name": name, "args": args})
        if name == "list_dir":
            return {"ok": True, "entries": ["README.md", "haiku.txt", "hello.py"]}
        if name == "read_file":
            return {"ok": True, "path": args.get("path", ""), "content": "smoke fixture file"}
        return {"ok": True, "path": args.get("path", "")}

    return AgentDeps(
        generate_as=generate_as,
        generate=generate,
        execute_tool=execute_tool,
        policy_for=lambda name, args: dict(_AUTO_POLICY),
        risk_level=lambda p: p["risk"],
        check_role=lambda name, user: None,
        tool_governance={
            "write_file": dict(_AUTO_POLICY),
            "read_file": dict(_AUTO_POLICY),
            "list_dir": dict(_AUTO_POLICY),
            "generate_file": dict(_AUTO_POLICY),
            "knowledge_graph_ingest": dict(_AUTO_POLICY),
            "knowledge_graph_search": dict(_AUTO_POLICY),
        },
        file_create_actions=frozenset({"write_file", "generate_file"}),
        recent_chat_context=lambda **kw: "",
        clear_history=lambda keep: {"ok": True},
        knowledge_save=lambda *a, **kw: None,
        audit=lambda *a, **kw: None,
        planner_prompt=PLANNER_PROMPT,
        executor_prompt=EXECUTOR_PROMPT,
        critic_prompt=CRITIC_PROMPT,
        memory_updater_prompt=MEMORY_UPDATER_PROMPT,
        agent_root=Path(tempfile.gettempdir()) / "agent-smoke",
    )


async def _run_one_task(
    generate_as: Callable[..., Awaitable[Any]],
    task_id: str,
    prompt: str,
    *,
    max_steps: int = 8,
    model_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Drive one task through the real state machine; reduce to a report row."""
    tool_log: List[Dict[str, Any]] = []
    runtime = SingleAgentRuntime(_build_smoke_deps(generate_as, tool_log))
    ctx = AgentRunContext()
    ctx.state = AgentState.PLANNING
    ctx.executing_model = model_id
    ctx.reviewing_model = model_id
    req = _SmokeReq(prompt)

    t0 = time.perf_counter()
    await runtime.plan(ctx, req, "en", "smoke@local", model_id=model_id)
    # The operator launched this run deliberately — the human approval the
    # gate asks for. The gate itself (and denial paths) are covered by
    # agent_eval; a real model's free-form plan must not dead-end the smoke.
    runtime.approve(ctx, "smoke@local", approved_by_human=True)
    if ctx.state == AgentState.EXECUTING:
        await runtime.run_to_completion(
            ctx, req, "en", "smoke@local", max_steps=max_steps, max_retry=2
        )
    duration = round(time.perf_counter() - t0, 2)

    summary = ctx.trace.summary()
    executed = [call["name"] for call in tool_log]
    return {
        "task": task_id,
        "final_state": ctx.state.value,
        "result_class": classify_result(ctx.state.value, ctx.trace.events, summary, executed),
        "steps": summary["llm_calls"],
        "parse_errors": summary["parse_errors"],
        "parse_recovered": summary["parse_recovered"],
        "repairs_total": sum((summary.get("repairs") or {}).values()),
        "repairs": summary.get("repairs") or {},
        "tool_calls": len(tool_log),
        "duration_s": duration,
    }


def _make_router_generate_as(router: Any, model_id: str) -> Callable[..., Awaitable[str]]:
    """Bridge the runtime's generate_as port to the real LLMRouter (the same
    ``generate_as`` call shape bench_models' filegen mode uses)."""

    async def generate_as(_model_id, message, context, max_tokens, temperature):
        return str(
            await router.generate_as(
                _model_id or model_id,
                message=message,
                context=context,
                max_tokens=max_tokens,
                temperature=temperature,
            )
        )

    return generate_as


async def _smoke_run_models(
    models: List[Dict[str, str]],
    tasks: List[Tuple[str, str]],
    max_steps: int,
) -> List[Dict[str, Any]]:
    from latticeai.models.router import LLMRouter

    router = LLMRouter()
    results: List[Dict[str, Any]] = []
    for model in models:
        model_id = model["id"]
        try:
            await router.load_model(model_id)
        except Exception as exc:  # noqa: BLE001 - fail-open per model
            results.append({
                "model": model_id, "family": model["family"],
                "skipped": True, "reason": f"load failed: {exc}",
            })
            continue
        generate_as = _make_router_generate_as(router, model_id)
        rows: List[Dict[str, Any]] = []
        for task_id, prompt in tasks:
            try:
                rows.append(await _run_one_task(
                    generate_as, task_id, prompt,
                    max_steps=max_steps, model_id=model_id,
                ))
            except Exception as exc:  # noqa: BLE001 - one broken task never sinks the report
                rows.append({
                    "task": task_id, "final_state": "HARNESS_ERROR",
                    "result_class": "failed", "error": str(exc),
                })
        results.append({
            "model": model_id,
            "family": model["family"],
            "tasks": rows,
            "tasks_total": len(rows),
            "completed": sum(1 for r in rows if r.get("final_state") == "DONE"),
            "duration_s": round(sum(float(r.get("duration_s") or 0.0) for r in rows), 2),
        })
        try:
            router.unload_model(model_id)
        except Exception:  # noqa: BLE001
            pass
    return results


def run_agent_smoke(
    models: Optional[List[Dict[str, str]]] = None,
    tasks: Optional[List[Tuple[str, str]]] = None,
    max_steps: int = 8,
) -> Dict[str, Any]:
    """Weekly per-model agent-loop report. Fail-open: never raises, never gates."""
    if models is None:
        models = discover_agent_models()
    selected_tasks = list(tasks or SMOKE_TASKS)
    if not models:
        return {
            "mode": "agent-smoke",
            "status": "skipped",
            "reason": (
                "no models available — install a local gemma/qwen/llama model "
                "via the app's model picker, then re-run"
            ),
            "models": [],
        }
    try:
        results = asyncio.run(_smoke_run_models(models, selected_tasks, max_steps))
    except Exception as exc:  # noqa: BLE001 - fail-open at the run level too
        return {
            "mode": "agent-smoke", "status": "skipped",
            "reason": f"smoke run failed: {exc}", "models": [],
        }
    return {"mode": "agent-smoke", "status": "ok", "models": results}


def format_smoke_report(report: Dict[str, Any]) -> str:
    lines = [
        "Weekly agent-loop smoke (real models through SingleAgentRuntime)",
        "=" * 72,
    ]
    if report.get("status") == "skipped":
        lines.append(f"no models available — skipped (fail-open): {report.get('reason')}")
        lines.append("=" * 72)
        return "\n".join(lines)
    for model in report.get("models", []):
        if model.get("skipped"):
            lines.append(f"  {model['model']}: SKIPPED — {model.get('reason')}")
            continue
        lines.append(
            f"  {model['model']} "
            f"(completed {model['completed']}/{model['tasks_total']}, "
            f"{model['duration_s']}s)"
        )
        for row in model.get("tasks", []):
            if row.get("final_state") == "HARNESS_ERROR":
                lines.append(f"    {row['task']:<22} HARNESS_ERROR  {row.get('error')}")
                continue
            lines.append(
                f"    {row['task']:<22} {row['final_state']:<13} "
                f"steps={row['steps']} parse_errors={row['parse_errors']} "
                f"repairs={row['repairs_total']} tools={row['tool_calls']} "
                f"{row['duration_s']}s"
            )
    lines.append("=" * 72)
    lines.append(
        "note: fail-open weekly report — weak results are the finding, never a failure"
    )
    return "\n".join(lines)


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Weekly real-model agent-loop smoke (fail-open, never a gate)"
    )
    parser.add_argument(
        "--json", dest="json_out", action="store_true",
        help="print the machine-readable report JSON to stdout",
    )
    parser.add_argument(
        "--tasks", type=int, default=len(SMOKE_TASKS),
        help=f"how many smoke tasks to run per model (1-{len(SMOKE_TASKS)}, default all)",
    )
    parser.add_argument(
        "--max-steps", type=int, default=8, help="agent-loop step budget per task",
    )
    parser.add_argument(
        "--model", help="restrict the run to one installed model id",
    )
    args = parser.parse_args(argv)

    count = max(1, min(int(args.tasks), len(SMOKE_TASKS)))
    models = discover_agent_models()
    if args.model:
        models = [m for m in models if m["id"] == args.model]
    report = run_agent_smoke(
        models=models, tasks=SMOKE_TASKS[:count], max_steps=max(2, int(args.max_steps)),
    )
    if args.json_out:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(format_smoke_report(report))
    # FAIL-OPEN: missing or weak models never produce a non-zero exit.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
