# Lattice AI v9.0.0 — Code Review Closure & Runtime Cleanup

Released: 2026-07-08

9.0.0 closes the July 8 code-review follow-up set and packages the remaining
cleanup-risk reduction work after 8.9.0's scoped memory and ToolRegistry
hardening.

## Highlights

- Fixed no-model file generation so it fails cleanly instead of writing empty
  files and reporting success.
- Preserved terminal SSE events, history, and traces when chat/document streams
  fail mid-generation.
- Persisted failed status for agent-run executor exceptions instead of leaving
  runs permanently running.
- Moved runtime audit appends to JSONL while preserving legacy JSON reads.
- Consolidated duplicated JSON/ISO/hash helpers, setup detection helpers, and
  frontend helper functions.
- Converted the legacy `server_app` compatibility namespace to an explicit
  allowlist.
- Decomposed the main `/chat` fast-path routing into focused intent handlers and
  shared response epilogues.

## Validation

- Unit tests, lint, typecheck, package builds, artifact validation, and CI are
  release gates for this line.
- Expected artifacts use exact 9.0.0 filenames only:
  - `dist/ltcai-9.0.0-py3-none-any.whl`
  - `dist/ltcai-9.0.0.tar.gz`
  - `dist/ltcai-9.0.0.vsix`
  - `ltcai-9.0.0.tgz`
  - `src-tauri/target/release/bundle/dmg/Lattice AI_9.0.0_aarch64.dmg`

Package registry publishing remains owner-run and is not triggered by tag push.
