# Lattice AI v4.7.2 Release Notes

Release date: 2026-06-14

## Summary

Lattice AI v4.7.2 is the Intuitive Brain UX Release. It keeps the v4 Brain Core,
StorageEngine, FastAPI localhost API, Tauri shell, backup/restore, model
runtime, graph APIs, portability, and separated Admin Console intact while
making the user-facing Brain easier to understand and operate.

## What Changed

- First-run login now blocks saved-user email mismatch and wrong saved-user
  password paths from silently creating a new empty Brain.
- Recommended model setup now has a one-click `추천대로 시작하기` path.
- Model install/download copy now explains that large downloads may take minutes
  and only reports progress from the runtime instead of inventing ETA.
- Brain home now exposes `기억 보기`, `주제 보기`, `관계 보기`, and `그래프로 보기`
  as direct actions.
- Brain Chat now includes a compact overview of recent memories, older memories,
  and major topics.
- Chat completion now shows `기억에 저장됨` feedback with current topic/memory
  counts.
- Release screenshots, GIF, README, changelog, architecture docs, feature
  status, security posture, VS Code extension docs, and recovery notes were
  synchronized to v4.7.2.

## Collaboration Notes

The v4.7.2 work incorporates the channel review from pts_claudecode:

- avoid login typo -> new empty Brain loss-of-trust behavior,
- make model setup more direct for non-technical users,
- make topic and graph exploration visible without forcing users to think in
  implementation terms,
- keep Admin Console separate from the everyday Brain surface.

pts_grok did not provide an additional review response in the channel before
this release work completed.

## Artifacts

- `dist/ltcai-4.7.2-py3-none-any.whl`
- `dist/ltcai-4.7.2.tar.gz`
- `dist/ltcai-4.7.2.vsix`
- `ltcai-4.7.2.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_4.7.2_aarch64.dmg`

External registry publishing remains owner-run. Do not upload `dist/*`; use
only the exact artifact filenames above.

## Evidence

- Screenshot index: `output/release/v4.7.2/SCREENSHOT_INDEX.md`
- Visual smoke: `npm run test:visual`
- Artifact validation: `npm run release:validate`

