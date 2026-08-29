# Lattice AI v9.9.0 — Fail-Closed Trust

> **Status: historical** — point-in-time release note.

Release date: 2026-07-21

9.9.0 closes the gap between what the product promises about trustworthy
autonomy and what the code actually guarantees. It fixes two data-and-trust
defects an external review rated P0, extends change governance to every
mutating tool, makes onboarding honest about hardware it cannot verify, and
adds the supply-chain, benchmark, and documentation-integrity groundwork the
review asked for — with an explicit, honest account of what could not be
verified locally.

## Highlights

### Change proposals can no longer overwrite your edits (P0)

- A proposal now records the target's original content hash and existence at
  creation time (`base_sha256`, `base_exists`). At approval, the current disk
  state is re-hashed and compared: if the file was modified, deleted, or
  created in the meantime, the apply is rejected as a **409 conflict** with a
  rebase hint instead of silently overwriting the newer content.
- The write itself is atomic (temp file + `os.replace`), and approval is
  serialized so duplicate or concurrent approvals apply **exactly once** — a
  replayed approve returns 409, not a second write.
- Legacy proposals without a base snapshot keep their prior apply-as-reviewed
  behavior; identical re-creation of the same content still applies cleanly.

### A confused verifier never reports success (P0)

- When the agent's critic output cannot be parsed, the loop no longer
  fabricates a `PASS`/`DONE`. It performs exactly one strict repair retry;
  if that still fails it terminates in the new `NEEDS_REVIEW` state with an
  honest message telling the user to check the result.
- `DONE` now requires **both** a validly parsed `PASS` verdict **and**
  deterministic execution evidence in the transcript. A PASS over an
  evidence-free run, or a `next_state: DONE` without a PASS, resolves to
  `NEEDS_REVIEW` — the loose "or next_state == DONE" success path is gone.
- The trace records verifier availability, verdict validity, and evidence so
  the outcome is observable.

### Every mutating tool is governed (P1)

- `MUTATING_TOOL_INVENTORY` in `core/tool_governor.py` is a single source of
  truth classifying every side-effecting tool as `new_artifact`,
  `existing_content_update`, `delete`, `external_side_effect`, or
  `internal_state`.
- A CI gate (`tests/unit/test_tool_governance_coverage.py`) fails closed if a
  new registry tool ships without a classification. Tools that would rewrite
  existing content but cannot be staged as a reviewable proposal
  (`create_docx/xlsx/pptx/pdf`, `local_write` overwriting an existing file)
  are now blocked (409) at the dispatch boundary rather than applied silently.
  New-file (additive) creation is unaffected.

### Honest onboarding (P1)

- Device analysis is modeled explicitly as `loading | ready | unavailable`.
  A failed or partial hardware probe no longer fabricates a
  `supported: true`, download-free "Qwen3 8B" recommendation; the screen
  shows the failure cause and makes **retry** and **continue without a model**
  the primary actions. A supported model card renders only when a genuinely
  supported model exists.

### Leaner bundle, audited supply chain, honest verification (P2)

- Initial JS bundle reduced ~22% (180.3 → 141.6 KiB gzip) by lazy-loading the
  onboarding flow, Brain home, and command palette, with a CI bundle-budget
  gate at 150 KiB.
- New `dependency-audit.yml` (pip-audit + npm audit + CycloneDX SBOM) and
  `postgres-integration.yml` (scheduled pgvector service) workflows; all
  GitHub Actions pinned to immutable commit SHAs.
- `docs/SECURITY_AUDIT.md` records an executed pip-audit (0 vulns) and bandit
  scan with per-finding triage; `docs/BENCHMARKS.md` + `scripts/bench_models.py`
  provide a model success/repair/latency harness; `docs/USABILITY_AUDIT.md`
  is a heuristic evaluation of the five key journeys.
- Documentation is classified `canonical | reference | historical` with a
  status/link gate (`scripts/check_doc_status.mjs`); `ARCHITECTURE.md` was
  verified against the real module layout and corrected.
- Eval results now separate `correct_completion` / `safe_termination` /
  `needs_review` / `failed`, so "the loop ended safely" is no longer counted
  as "the request was correctly completed."

## Compatibility

- All API changes are additive except the new fail-closed blocks, which turn
  a previously silent overwrite into an explicit 409 — the safe direction.
- The new `NEEDS_REVIEW` agent state is terminal; consumers reading
  `terminal_states` pick it up automatically.
- `context_for_query()` default output is unchanged; `PLUGIN_SDK_VERSION` is
  unchanged.

## Verification

- 1287 unit tests green; 39 frontend tests green; integration 3 passed / 11
  skipped (live PostgreSQL path runs in the scheduled workflow).
- `scripts/agent_eval.py`: 20/20 scenarios, success rate 1.0, with the new
  fail-closed verifier scenarios.
- Ruff, frontend lint, tsc, i18n parity/literal, bundle budget, OpenAPI
  drift, current-release docs, and doc-status/link gates all pass.
- pip-audit and `npm audit --audit-level=high`: 0 vulnerabilities.

## Honest limitations

- External penetration testing and real end-user interviews were out of scope
  for autonomous execution; they are substituted by a static security scan
  (`docs/SECURITY_AUDIT.md`) and a heuristic usability audit
  (`docs/USABILITY_AUDIT.md`), which do not replace the real activities.
- Live PostgreSQL and per-model long-run benchmarks could not run in this
  environment (no Docker daemon, no loaded local model); they are provided as
  scheduled/dispatch CI paths and a harness whose scripted mode is proven.

## Artifacts

- `dist/ltcai-9.9.0-py3-none-any.whl`
- `dist/ltcai-9.9.0.tar.gz`
- `ltcai-9.9.0.tgz`
- `dist/ltcai-9.9.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_9.9.0_aarch64.dmg`
