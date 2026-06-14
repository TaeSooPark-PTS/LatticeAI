# v4.7.2 Intuitive Brain UX Report

## Scope

v4.7.2 focuses on the product question: can a normal user understand why Lattice
AI exists and operate the Brain without learning database, graph, or admin
jargon? The release does not redesign Brain Core, storage, APIs, model runtime,
backup/restore, portability, or the separated Admin Console.

## Product Changes

- First-run login no longer auto-registers a new local account when the saved
  Brain email differs or when the saved email has a wrong password.
- Recommended model setup now has a single primary action for users who do not
  know which model to choose.
- Install/download messaging explains long-running model downloads honestly
  without fabricated ETA.
- Brain home now exposes direct Memory, Topic, Relationship, and Graph actions.
- Brain Chat shows recent memories, older memories, major topics, and
  saved-to-memory feedback after chat.

## Collaboration Summary

pts_claudecode identified the highest-risk product gaps: empty-Brain creation
from login typo, graph-first mental model leakage, lack of direct topic/time
visibility, and model download trust problems. v4.7.2 addresses the parts that
fit a scoped release: safer login, direct Brain views, topic/memory overview,
and honest model setup copy. pts_grok did not return a channel review before
completion.

## Evidence

Fresh v4.7.2 screenshots and walkthrough media are indexed in
[output/release/v4.7.2/SCREENSHOT_INDEX.md](../output/release/v4.7.2/SCREENSHOT_INDEX.md).

## Validation Targets

- `npm run typecheck:frontend`
- `npm run lint`
- `npm run test:visual`
- `npm run test:unit`
- `npm run test:integration`
- `npm run release:artifacts`
- `npm run release:validate`

## Expected Artifacts

- `dist/ltcai-4.7.2-py3-none-any.whl`
- `dist/ltcai-4.7.2.tar.gz`
- `dist/ltcai-4.7.2.vsix`
- `ltcai-4.7.2.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_4.7.2_aarch64.dmg`

## Remaining Technical Debt

- Admin Console still needs deeper role-based authorization workflows and
  configurable log retention UI.
- Brain topic grouping is now visible, but backend topic clustering and a true
  time-axis memory view should be promoted into first-class APIs next.
- AgentRuntime extraction should continue so user Brain state and admin
  observability state remain architecturally separate beyond the frontend layer.

