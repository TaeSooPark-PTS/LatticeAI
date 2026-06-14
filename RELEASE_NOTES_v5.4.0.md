# Lattice AI v5.4.0 — Brain Automation Scheduler

**Date:** 2026-06-15  
**Branch:** feat/brain-automation-scheduler  
**PR:** #4 (remote checks: Python 3.11/3.12 pass, Integration smoke pass, Visual Smoke pass, Build package pass, Vercel pass)

Lattice AI v5.4.0 introduces the first Brain automation scheduler layer under strict consent-first rules. Users can create Daily Memory Digest, Weekly Project Review, and Follow-up Radar as reviewable, disabled workflow drafts from the Automate page. No scheduler or Brain-event trigger fires until the user explicitly enables the recipe.

## Highlights

- **Consent-first automation recipes**: `latticeai/services/brain_automation.py` defines the three recipes with local-only consent metadata. Drafts are installed with `enabled: false`.
- **Automate page UX**: Recipe cards in `frontend/src/pages/Act.tsx` with "Create reviewable draft" action. Duplicate prevention via `metadata.recipe_id`, button disabled during install, immediate success feedback, and state update instead of re-creation.
- **TriggerService hardening** (lattice_brain/runtime):
  - Explicit `enabled: false` on trigger nodes treated as disarmed (legacy workflows without the field preserved).
  - Cooldown + last_attempt_at dedup guards for interval and brain_event triggers to prevent duplicate executions.
  - `LATTICE_TZ` environment support exposed via describe().
  - Per-trigger `consecutive_failures` and "degraded" status surfacing.
- **Runtime graph cleanup**: `lattice_brain/runtime` responsibility/dependency graph 정리, entrypoint mapping documented in module headers and app_factory comments. Clear separation: AgentRuntime (lattice_brain facade) vs single-agent state in latticeai/core/agent.
- **E2E scenarios**: A-direction draft install → dedup → consent-first → trigger fire with provenance → LATTICE_TZ → degraded → review flow exercised in brain_automation tests.
- **Version sync**: All Python, npm, VSIX, Tauri, runtime constants, and static metadata bumped to 5.4.0 via scripts/bump_version.py. package.json, pyproject.toml, tauri.conf, Cargo.toml, inits, etc.
- **Docs sync**: README, RELEASE.md, docs/CHANGELOG.md, FEATURE_STATUS.md, SECURITY.md, vscode-extension/README.md, RELEASE_NOTES.md, and root RELEASE_NOTES_v5.4.0.md updated for current 5.4.0 target. Historical references preserved.

## Expected Artifacts (exact 5.4.0 names only)

- `dist/ltcai-5.4.0-py3-none-any.whl`
- `dist/ltcai-5.4.0.tar.gz`
- `dist/ltcai-5.4.0.vsix`
- `ltcai-5.4.0.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_5.4.0_aarch64.dmg`

## Tests & Gates

- Python 3.11 / 3.12 CI: pass
- Integration smoke: pass
- Visual Smoke (playwright): pass
- Build package: pass
- Vercel: pass
- `npm run release:validate` (with --require-vsix --require-tgz --require-dmg) executed as final gate.
- Unit + targeted brain_automation tests green.
- Typecheck / lint / docs:check-links planned as part of release:artifacts flow.

## Collaboration Notes

Backend/product engineering coordination on scheduler boundaries, dedup semantics, runtime seams, and exact artifact hygiene. Feature implementation and release prep followed project AGENTS.md order (AgentRuntime-adjacent work, config centralization awareness, no destructive changes).

## Preserved

- Local-first defaults, explicit consent for all external and automation actions.
- Legacy trigger behavior for workflows without `enabled` field.
- All prior Brain Core, Storage, graph, Admin, and portability capabilities.
- Tracked release-note history from v4.5.0 onward.
- Package publish remains owner-run only; no CI auto-publish.

Full release guide: see [RELEASE.md](RELEASE.md).  
Changelog details: [docs/CHANGELOG.md](docs/CHANGELOG.md).  
Feature status: [FEATURE_STATUS.md](FEATURE_STATUS.md).