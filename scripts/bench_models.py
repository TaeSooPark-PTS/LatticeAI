#!/usr/bin/env python3
"""Model robustness benchmark harness for the Lattice agent loop.

What it measures
================
The product claim is that the Brain stays durable "across any AI model" — a
weaker model may emit sloppier output, but the agent loop repairs it and still
completes the task. This harness turns that claim into a **matrix** of three
numbers per model tier:

* ``success_rate`` — fraction of model outputs the loop could turn into a valid
  action (i.e. the task can proceed).
* ``repair_rate``  — of the successful parses, the fraction that ONLY succeeded
  because the loop's tolerant parser had to repair the output (fences, prose,
  trailing commas, ``<think>`` blocks, Python-dict literals). High repair_rate
  = "this tier leans hard on the loop's robustness".
* ``latency_ms``   — see the honesty note below.

It measures **real code**: every output is fed through
``latticeai.core.agent.extract_action_details`` — the exact parser/repair
function the production loop uses (``agent.py`` calls it in plan/execute/verify).
An ``agent-loop`` reference row additionally runs the real
``latticeai.core.agent_eval.run_agent_eval`` state machine over its 20
scripted scenarios.

Two modes
=========
1. **scripted** (default, always runnable, no model/network): a curated corpus
   of model-realistic outputs per quality tier (frontier / mid-local /
   weak-local). Proves the harness works and exposes the loop's repair boundary
   deterministically.
2. **live** (opt-in, ``--live-endpoint``): sends a fixed set of agent prompts to
   an OpenAI-compatible local endpoint (e.g. LM Studio / llama.cpp / vLLM) and
   runs the *real* completions through the same parser, measuring true
   end-to-end generation latency. Falls back to scripted with an honest message
   if the endpoint is unreachable.

Honesty note on latency
========================
In **scripted** mode ``latency_ms`` is the *parse+repair* cost only
(microseconds); it is NOT model inference time and must not be read as such.
Real generation latency is only meaningful in **live** mode.

Usage
=====
    .venv/bin/python scripts/bench_models.py                    # scripted matrix
    .venv/bin/python scripts/bench_models.py --json out.json    # + machine output
    .venv/bin/python scripts/bench_models.py \
        --live-endpoint http://127.0.0.1:1234/v1 --model my-local-model
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from latticeai.core.agent import extract_action_details  # noqa: E402
from latticeai.core.agent_eval import run_agent_eval  # noqa: E402


# ── Scripted corpora ─────────────────────────────────────────────────────
# Each entry is one model output for a canonical agent turn. The tiers encode
# how the SAME intent degrades as model quality drops. All "should_parse=True"
# entries are recoverable by the real loop; "should_parse=False" entries are
# genuinely broken (past the repair boundary) and SHOULD fail — that is what
# makes success_rate < 1.0 meaningful rather than a rigged demo.

_CLEAN = [
    ('{"action": "plan", "goal": "ingest", "steps": [{"action": "write_file"}]}', True),
    ('{"action": "write_file", "args": {"path": "note.txt", "content": "hi"}}', True),
    ('{"action": "final", "message": "done"}', True),
    ('{"action": "verdict", "verdict": "PASS", "next_state": "DONE", "reason": "ok"}', True),
    ('{"action": "knowledge_graph_search", "args": {"query": "roadmap"}}', True),
    ('{"action": "read_file", "args": {"path": "README.md"}}', True),
]

_MID = [
    # markdown-fenced JSON (very common)
    ('```json\n{"action": "plan", "goal": "ingest", "steps": []}\n```', True),
    ('```\n{"action": "write_file", "args": {"path": "a.txt", "content": "x"}}\n```', True),
    # prose preamble then the object
    ('Sure! Here is the next step:\n{"action": "final", "message": "done"}', True),
    # trailing comma before closing brace
    ('{"action": "read_file", "args": {"path": "README.md",}}', True),
    # trailing comma in array
    ('{"action": "plan", "goal": "g", "steps": [{"action": "read_file"},]}', True),
    ('```json\n{"action": "verdict", "verdict": "PASS", "next_state": "DONE", "reason": "ok"}\n```', True),
]

_WEAK = [
    # <think> reasoning block that itself contains braces, then the action
    ('<think>I should write the file {maybe}</think>\n{"action": "write_file", "args": {"path": "n.txt", "content": "c"}}', True),
    ('<reasoning>ok</reasoning> {"action": "final", "message": "done"}', True),
    # Python dict literal (single quotes, True) — ast.literal_eval path
    ("{'action': 'read_file', 'args': {'path': 'x.txt'}}", True),
    ("{'action': 'verdict', 'verdict': 'PASS', 'next_state': 'DONE', 'reason': 'ok'}", True),
    # fenced + trailing comma + prose all at once
    ('Here you go:\n```json\n{"action": "plan", "goal": "g", "steps": [],}\n```', True),
    # genuinely broken: pure prose, no JSON object at all -> must fail
    ("I think we are done here, nothing else to do.", False),
    # genuinely broken: object but missing the required "action" field
    ('{"message": "done", "status": "ok"}', False),
    # genuinely broken: truncated / unbalanced braces past repair
    ('{"action": "write_file", "args": {"path": "n.txt"', False),
]

_PROFILES: Dict[str, List[Tuple[str, bool]]] = {
    "frontier (clean JSON)": _CLEAN,
    "mid-local (fenced/prose/commas)": _MID,
    "weak-local (think/py-literal/broken)": _WEAK,
}


def _bench_corpus(corpus: List[Tuple[str, bool]]) -> Dict[str, Any]:
    total = len(corpus)
    parsed = 0
    repaired = 0
    latencies: List[float] = []
    mismatches: List[str] = []
    repair_kinds: Dict[str, int] = {}
    for raw, should_parse in corpus:
        t0 = time.perf_counter()
        try:
            _action, repairs = extract_action_details(raw)
            ok = True
        except (ValueError, Exception):  # noqa: BLE001 - parser raises ValueError
            ok = False
            repairs = []
        latencies.append((time.perf_counter() - t0) * 1000.0)
        if ok:
            parsed += 1
            if repairs:
                repaired += 1
                for kind in repairs:
                    repair_kinds[kind] = repair_kinds.get(kind, 0) + 1
        if ok != should_parse:
            mismatches.append(raw[:60])
    return {
        "total": total,
        "parsed": parsed,
        "repaired": repaired,
        "success_rate": round(parsed / total, 4) if total else 0.0,
        "repair_rate": round(repaired / parsed, 4) if parsed else 0.0,
        "latency_ms_mean": round(statistics.fmean(latencies), 4) if latencies else 0.0,
        "repair_kinds": repair_kinds,
        # A healthy harness has zero mismatches: every "should_parse" call
        # matched reality. Non-empty means the corpus/parser disagree.
        "harness_mismatches": mismatches,
    }


# ── Live (OpenAI-compatible) path ────────────────────────────────────────
_LIVE_PROMPTS = [
    "Return ONLY one JSON object for the next agent step: read the file README.md.",
    "Return ONLY one JSON object: plan to ingest a document, then finish.",
    "Return ONLY one JSON object: write 'hello' to notes.txt.",
    "Return ONLY one JSON object: a PASS verdict moving the loop to DONE.",
    "Return ONLY one JSON object: search the knowledge graph for 'roadmap'.",
]

_SYSTEM = (
    "You are the executor of an agent loop. Every reply MUST be exactly one "
    'JSON object with an "action" field and nothing else.'
)


def _openai_chat(base_url: str, model: str, prompt: str, *, timeout: float) -> Tuple[str, float]:
    url = base_url.rstrip("/") + "/chat/completions"
    body = json.dumps(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
            "max_tokens": 256,
        }
    ).encode("utf-8")
    req = urllib.request.Request(  # noqa: S310 - operator-supplied local endpoint
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as res:  # noqa: S310
        payload = json.loads(res.read().decode("utf-8", errors="replace"))
    latency = (time.perf_counter() - t0) * 1000.0
    text = payload["choices"][0]["message"]["content"]
    return text, latency


def _bench_live(base_url: str, model: str, *, timeout: float) -> Dict[str, Any]:
    total = 0
    parsed = 0
    repaired = 0
    latencies: List[float] = []
    for prompt in _LIVE_PROMPTS:
        total += 1
        try:
            text, latency = _openai_chat(base_url, model, prompt, timeout=timeout)
        except (urllib.error.URLError, OSError, KeyError, ValueError) as exc:
            return {"error": f"live endpoint unreachable/invalid: {exc}"}
        latencies.append(latency)
        try:
            _action, repairs = extract_action_details(text)
            parsed += 1
            if repairs:
                repaired += 1
        except Exception:  # noqa: BLE001
            pass
    return {
        "total": total,
        "parsed": parsed,
        "repaired": repaired,
        "success_rate": round(parsed / total, 4) if total else 0.0,
        "repair_rate": round(repaired / parsed, 4) if parsed else 0.0,
        "latency_ms_mean": round(statistics.fmean(latencies), 2) if latencies else 0.0,
    }


# ── Reporting ────────────────────────────────────────────────────────────
def _fmt_row(name: str, r: Dict[str, Any]) -> str:
    return (
        f"  {name:<40} "
        f"success={r['success_rate']:<7} "
        f"repair={r['repair_rate']:<7} "
        f"n={r.get('total', '-'):<4} "
        f"latency_ms={r['latency_ms_mean']}"
    )


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Model robustness benchmark harness")
    parser.add_argument("--json", dest="json_out", help="write full report JSON to this path")
    parser.add_argument("--live-endpoint", help="OpenAI-compatible base URL, e.g. http://127.0.0.1:1234/v1")
    parser.add_argument("--model", help="model id for the live endpoint")
    parser.add_argument("--timeout", type=float, default=30.0, help="live request timeout seconds")
    args = parser.parse_args(argv)

    report: Dict[str, Any] = {"mode": "scripted", "profiles": {}}

    # Scripted matrix (always).
    for name, corpus in _PROFILES.items():
        report["profiles"][name] = _bench_corpus(corpus)

    # Real agent-loop reference row.
    loop = run_agent_eval()
    report["agent_loop_reference"] = {
        "scenarios": loop["scenarios"],
        "passed": loop["passed"],
        "success_rate": loop["success_rate"],
        "recovery_rate": loop["recovery_rate"],
        "parse_errors": loop["parse_errors"],
        "parse_recovered": loop["parse_recovered"],
    }

    # Optional live row.
    if args.live_endpoint:
        if not args.model:
            print("--live-endpoint requires --model", file=sys.stderr)
            return 2
        live = _bench_live(args.live_endpoint, args.model, timeout=args.timeout)
        report["mode"] = "scripted+live"
        report["live"] = {"endpoint": args.live_endpoint, "model": args.model, **live}

    # Human-readable matrix.
    print("Model robustness benchmark (real parser: extract_action_details)")
    print("=" * 72)
    print("Scripted tiers (latency_ms = parse+repair only, NOT model inference):")
    mismatch_total = 0
    for name, r in report["profiles"].items():
        print(_fmt_row(name, r))
        if r["repair_kinds"]:
            print(f"      repairs used: {r['repair_kinds']}")
        mismatch_total += len(r["harness_mismatches"])
    ref = report["agent_loop_reference"]
    print("-" * 72)
    print(
        f"  agent-loop (real state machine)          "
        f"success={ref['success_rate']:<7} "
        f"recovery={ref['recovery_rate']:<7} "
        f"scenarios={ref['scenarios']}"
    )
    if "live" in report:
        lv = report["live"]
        print("-" * 72)
        if "error" in lv:
            print(f"  live [{lv['model']}] SKIPPED: {lv['error']}")
        else:
            print(_fmt_row(f"live [{lv['model']}] (real inference)", lv))
    print("=" * 72)
    print(f"harness self-check: {mismatch_total} corpus/parser mismatches (0 = healthy)")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"wrote {args.json_out}")

    # Non-zero exit only if the harness itself is inconsistent (a real bug),
    # never because a weak tier scored low — low scores are the finding.
    return 1 if mismatch_total else 0


if __name__ == "__main__":
    raise SystemExit(main())
