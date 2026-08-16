# FROZEN — the agent safety-kernel goldens

Every file under `golden/` is **frozen**. There is no generator any more:
`scripts/generate_agent_parity_fixtures.py` and the Python safety kernel it
drove are gone as of v11.8.0, and `latticeai.tools.commands.run_command` — the
oracle behind `commands.json` and `execution.json` — left the worker back in
WP-P1. These files are the record of what Python answered; they are read by
`rust/lattice-agent/tests/parity.rs` and `tests/execution.rs`, edited by nobody.

If a golden and the Rust kernel disagree, **the kernel changed**. Fix the
kernel, or — if the change is deliberate — delete the rows the change makes
obsolete and say why here. Do not rewrite a row's expected values: a golden you
are allowed to edit proves nothing.

## What each file is

| file | rows | what it pins |
|---|---|---|
| `manifest.json` | — | the grid's inputs: arg variants, the workspace tree, plans, the Python constant tables, `existing_paths`, `which_paths`, `pinned_env` |
| `policies.json` | — | `TOOL_GOVERNANCE` verbatim, plus the per-call overrides |
| `calls.json` | 171 | `is_circuit_breaker` + `classify_tool_call` per `(tool, variant)` |
| `decisions__strict.json` | 171 + 84 + 8 | `effective_auto_approve` / `block_reason_for_tool` / `should_stage_proposal` under `strict`, plus the change-class and plan grids |
| `commands.json` | 59 | `validate` — the shell allow-list, its flag blocks, and the spawn environment |
| `execution.json` | 15 | `run_command`'s captured output, truncation and timeouts |
| `shlex.json` | 35 | `pyshlex::split` against Python's `shlex` |
| `paths.json` | 12 | `Workspace::resolve` — containment, traversal, symlinks |
| `normalize.json` | 28 | the permission-mode alias table |
| `contract.json` | — | `mode_contract` for all three modes, byte for byte |

## What was removed in v11.8.0, and why

**`decisions__trusted.json` and `decisions__bypass.json` — deleted.**
702 rows each. The grid is the cross product of 54 tools and 13 argument
variants, so a verdict repeats once per row in its class: `trusted` had 7
distinct `(auto_approve, block_reason, stage_proposal)` outcomes over its 702
rows and `bypass` had 4. 1,404 rows were asserting 11 facts, and every one of
them was also being re-derived from the same three functions the `strict` grid
exercises.

The gating those two grids proved now has named unit tests in the crate:

* `lattice_agent::permission::tests::trusted_reproduces_every_verdict_class_of_the_retired_grid`
* `lattice_agent::permission::tests::bypass_reproduces_every_verdict_class_of_the_retired_grid`
* `lattice_agent::mode::tests::{trusted_auto_runs_workspace_writes_but_not_exec_or_control,
  trusted_accepts_a_workspace_write_on_either_axis,
  bypass_still_refuses_destructive_and_system_writes,
  proposals_are_staged_only_under_strict, plan_approval_is_skipped_only_by_bypass}`

Each carries one case per verdict class, built from the same policy shapes and
the same argument variants the grid used. `parity.rs` reads its mode list from
`GOLDEN_MODES` and asserts that a retired grid has not quietly reappeared.

`strict` is the mode kept whole: it is the one that gates everything, so it is
where a refusal that stopped firing shows up first.

**`calls.json` and `decisions__strict.json` — trimmed, 702 rows → 171.**
One row per distinct
`(tool, circuit_breaker, classification, policy, auto_approve, block_reason,
stage_proposal)` class — 171 classes across all 54 tools, using 5 of the 13
argument variants (`none`, `root_path`, `rm_rf_root`, `existing_path`,
`blocked_prefix`; the other eight produced no verdict the kept five do not).
The two files carry the same 171 `(tool, variant)` pairs, because `parity.rs`
builds its policy index from `calls.json` and looks decisions up by that pair.

**Rows were deleted, never rewritten.** The surviving 171 in each file are
byte-identical to the ones the generator emitted — the only other change is the
trailing comma on the last kept row. `git show <tag>:` the previous revision to
check that for yourself.
