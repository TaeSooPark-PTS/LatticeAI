# Lattice AI v9.6.0 — Trusted Agent Loop

> **Status: historical** — point-in-time release note.

Released: 2026-07-20

9.6.0 answers the four findings of the agent-runtime audit in one release:
the reasoning loop was not observable, there was no evaluation harness, tool
safety control was scattered, and every write — even a harmless new file —
carried the same approval friction as a destructive change. All four are
fixed with a git-like mental model: **creating is cheap, changing is
reviewed**.

## What it feels like

- Ask the agent to build something new → it just runs. No approval wall for
  additive work.
- Ask the agent to *change* something that already exists → nothing is
  touched. Instead the Brain home shows a **변경 제안 (change proposal)**
  with a unified diff and a small/large tier. Approve applies exactly what
  you reviewed; reject discards it.
- Every agent response now carries a `loop` summary: how many model calls,
  how many formatting slips were recovered, which repairs the model needed,
  what every tool call did. Weak local models are no longer a black box.

## Agent loop observability

- New `LoopTrace` (`latticeai/core/agent_trace.py`) records typed events
  across PLAN → EXECUTE → VERIFY → ROLLBACK: llm calls, parse errors with
  recovered/unrecovered status, named format repairs, corrections, tool
  outcomes (`ok` / `error` / `blocked_approval` / `blocked_destructive` /
  `proposed`), retries, approval and verdict decisions.
- The agent API returns the trace summary as `loop` in both the
  waiting-approval and final payloads.

## Weak-model robustness, now measured

- `extract_action_details` repairs think-blocks, fences, prose slicing,
  trailing commas — and now Python-literal dicts (single quotes,
  True/False/None) via `ast.literal_eval`. Every repair is reported by name.
- After a second formatting slip the corrective hint escalates with the
  exact list of valid tools, so small models stop inventing action names.
- New **agent evaluation harness** (`scripts/agent_eval.py`, CI gate): 8
  deterministic scripted scenarios driven through the real state machine —
  happy path, weak-model format gauntlet, prose-slip recovery, correction
  escalation, destructive block, loop detection, critic retry, and
  unrecoverable garbage — with 100% scenario pass required to ship.

## Proposal-first change governance

- New central governor (`latticeai/core/tool_governor.py`) classifies every
  governed call as read / additive / mutation / destructive in one place.
- Agent edits and deletions of existing workspace files are staged as
  review proposals (`latticeai/services/change_proposals.py`, review-queue
  source `change_proposal`): unified diff, exact staged content, small/large
  tier, full audit trail. `GET /api/proposals`,
  `POST /api/proposals/{id}/approve|reject`.
- Approve applies **exactly the reviewed content** (never recomputed);
  reject discards; nothing changes on disk while a proposal is pending.
- Additive creates (new files) run with minimal friction — plan approval no
  longer hard-blocks governor-managed tools.
- Frontend: the Brain home "변경 제안 / Change proposals" panel shows diff
  previews with one-click approve/reject; proposals also surface in the Act
  review center.

## Structure & process housekeeping

- Ruff per-file lint ignores trimmed from 9 entries to 3 — all dead ignores
  removed, the one remaining legacy monolith scoped to a single rule.
- AGENTS.md now carries a machine-checked current-release marker and the
  agent-loop invariants, enforced by the docs gate in CI.
- The review queue gained the `change_proposal` source end to end (backend,
  OpenAPI, frontend types).

## Verification

- New tests: `test_agent_trace.py` (11), `test_agent_eval.py` (4),
  `test_change_proposals.py` (15), `PendingProposalsPanel.test.tsx` (2).
- Full sweep: **1127 unit**, **13 integration**, **19 frontend vitest**,
  **18 playwright visual** tests passing; lint (Ruff clean with trimmed
  ignores), typecheck, docs, brain-quality, agent-loop-eval, and
  product-readiness gates green; live-boot smoke on the new
  `/api/proposals` endpoints.

## Compatibility

Additive API surface: three new `/api/proposals` endpoints, a new `loop`
field on agent responses, a new review-queue source. Behavior change by
design: in agent mode, overwrites/edits of existing files no longer apply
directly — they become proposals; new-file creation no longer requires plan
approval. Chat's explicit "만들어줘" direct file path is unchanged.
