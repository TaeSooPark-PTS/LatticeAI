# Model Robustness Benchmarks

> Status: reference 2026-07-21

Methodology and results for `scripts/bench_models.py`, a harness that measures
how well the agent loop turns model output into executable actions across model
quality tiers. Companion to [CI & Release Gates](CI_AND_RELEASE_GATES.md).

## What is measured

The product claim is that the Brain stays durable "across any AI model": weaker
models emit sloppier output, but the loop repairs it and still completes the
task. The harness turns that into three numbers per tier:

- **success_rate** — fraction of model outputs that become a valid agent action
  (the task can proceed).
- **repair_rate** — of the successful parses, the fraction that succeeded *only
  because* the loop's tolerant parser had to repair the output. High = "this
  tier leans hard on the loop's robustness".
- **latency_ms** — see the honesty note below.

Every output is fed through the **real** parser the production loop uses:
`latticeai.core.agent.extract_action_details` (called from `agent.py` in the
plan/execute/verify phases). The harness does not reimplement parsing — it
measures the shipping code. Its known repair tolerances are: `think_strip`
(strip `<think>` blocks), `fence` (unwrap ```` ```json ````), `slice` (extract
the `{...}` span from prose), `trailing_comma`, and `python_literal`
(`ast.literal_eval` of a single-quoted dict).

An `agent-loop` reference row additionally runs the real
`latticeai.core.agent_eval.run_agent_eval` state machine over its 20 scripted
scenarios and reports its success and recovery rates.

## Honesty note on latency

In **scripted** mode, `latency_ms` is the **parse+repair cost only**
(microseconds). It is **not** model inference time and must not be read as such.
True generation latency is only meaningful in **live** mode against a real
endpoint.

## Modes

1. **scripted** (default; no model, no network): a curated corpus of
   model-realistic outputs per tier. Deterministic; proves the harness works and
   exposes the loop's repair boundary. Some weak-tier entries are *intentionally
   broken* past the repair boundary so `success_rate < 1.0` is a real finding,
   not a rigged demo. A self-check asserts every corpus entry's expected
   parse/fail outcome matches reality (0 mismatches = healthy).
2. **live** (opt-in): sends a fixed prompt set to an OpenAI-compatible local
   endpoint (LM Studio / llama.cpp / vLLM), runs the real completions through
   the same parser, and reports **true** generation latency. Falls back to a
   clear "SKIPPED" line if the endpoint is unreachable.

## Running

```bash
# Scripted matrix (always works):
.venv/bin/python scripts/bench_models.py

# With machine-readable output:
.venv/bin/python scripts/bench_models.py --json bench.json

# Against a real local model (OpenAI-compatible endpoint):
.venv/bin/python scripts/bench_models.py \
    --live-endpoint http://127.0.0.1:1234/v1 --model my-local-model
```

Exit code is non-zero only if the harness self-check finds an inconsistency (a
real bug); a low score on a weak tier is a finding, not a failure.

## Reference results (scripted, 2026-07-21)

Captured on the maintainer's machine; latency is parse-only.

| Tier | success_rate | repair_rate | n | repairs exercised |
|------|-------------:|------------:|--:|-------------------|
| frontier (clean JSON) | 1.00 | 0.00 | 6 | — |
| mid-local (fenced/prose/commas) | 1.00 | 1.00 | 6 | fence×3, slice×1, trailing_comma×2 |
| weak-local (think/py-literal/broken) | 0.625 | 1.00 | 8 | think_strip×2, python_literal×2, fence×1, trailing_comma×1 |

Agent-loop reference (real state machine): **success_rate 1.00**, **recovery_rate
0.70** over 20 scenarios. Harness self-check: **0 mismatches**.

Reading the matrix: the mid tier is fully recoverable — every output needed a
repair, and all succeeded, which is exactly the "durable across models" claim in
action. The weak tier's 0.625 is the honest boundary: the 3 genuinely broken
outputs (pure prose, missing `action` field, truncated braces) cannot and should
not parse.

## Measurable vs not-measurable (honest scope)

**This harness can measure:**
- Whether a given model's raw output survives the loop's parser (success_rate).
- How much the loop's repair layer is doing the work (repair_rate + which
  tolerances fire).
- Real end-to-end generation latency in `live` mode.
- End-to-end loop completion over the scripted scenario suite.

**This harness does NOT measure:**
- Task *correctness* / answer quality — only that an action is well-formed and
  executable, not that it is the *right* action.
- Multi-turn reasoning quality or tool-selection accuracy beyond the scripted
  scenarios.
- Throughput/latency under concurrency or on other hardware.
- Any cloud model (no keys are used; live mode targets local endpoints only).
- A representative sample of "all models" — scripted tiers are archetypes, not a
  statistical population. For real numbers on a specific model, use `live` mode.
