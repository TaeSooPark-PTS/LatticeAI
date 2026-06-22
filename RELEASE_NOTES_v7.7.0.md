# Lattice AI v7.7.0 Release Notes

v7.7.0 is the productization-completeness line. Where v7.6.0 closed the
*architecture* with `architecture_readiness()`, v7.7.0 closes the *product*: it
turns "would anyone call this a finished product?" from an opinion into a
repeatable, evidence-probed measurement.

## Highlights

- **Product readiness scorecard.** `latticeai.services.product_readiness`
  evaluates seven product gates — first-run, answer-proof, local-first trust,
  packaging, architecture closure, trust docs, and quality gates — and reports
  `complete` only when each gate's evidence actually resolves on disk. It folds
  the 7.6 `architecture_readiness()` result in, so the product can never claim
  completeness while the structure underneath is incomplete.
- **Re-run it any time.** `node scripts/run_python.mjs scripts/product_readiness.py`
  prints the scorecard and exits non-zero when any gate is incomplete, making
  "is it done yet?" a CI-pluggable gate rather than a judgment call.
- **Honest by construction.** Building the gate surfaced two real evidence gaps
  (a missing 7.7 release note and an over-strict UI anchor); both were fixed by
  pointing evidence at what actually ships, not the reverse.

## Validation Scope

- Focused unit tests (`tests/unit/test_v77_product_readiness.py`) assert the
  scorecard is machine-checkable, release-complete, and that its score reflects
  gate counts.
- Existing 7.6 architecture, AgentRuntime, ToolRegistry, Config, and KG gates
  continue to pass and are composed into the product score.
- Release artifacts are exact-version only:
  `dist/ltcai-7.7.0-py3-none-any.whl`, `dist/ltcai-7.7.0.tar.gz`,
  `dist/ltcai-7.7.0.vsix`, `ltcai-7.7.0.tgz`, and
  `src-tauri/target/release/bundle/dmg/Lattice AI_7.7.0_aarch64.dmg`.

Publishing remains owner-run only. Do not publish packages from automation.
