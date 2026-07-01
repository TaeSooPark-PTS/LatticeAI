# Lattice AI v8.4.0 Release Notes

## Action-Aware Brain Chat

8.4.0 makes the main Brain composer act on explicit file requests. When a user
asks Lattice AI to create, write, save, or edit a file, `/chat` now routes that
request into the governed workspace file tool instead of treating it as ordinary prose
generation. The result is a real workspace artifact, not only a code block.

## What Changed

- Brain Chat detects explicit file-action intent and routes explicit file-name
  requests to the governed workspace `write_file` tool.
- Literal file writes with user-provided content can now succeed even before a
  model is loaded; model generation is used only when file content must be
  synthesized.
- File target and content parsing is narrower, so prose such as "create a text
  file report.txt" no longer gets swallowed as the target path or literal
  content.
- Normal Q&A remains on direct chat generation, keeping the default conversation
  path fast and low-friction.
- Agent responses returned through chat include `created_files` metadata so the
  UI and clients can tell that an artifact was actually produced.
- The common Gemma 4 26B shorthand alias now resolves to the canonical MLX A4B
  model id.
- Local MLX model loading dependency exports were restored after the runtime
  split, and the local launcher now uses the active venv's `python -m uvicorn`.

## Validation

- `npm run check:python`
- `npm run test:unit`
- `npm run docs:check-links`

Expected local release artifacts use exact 8.4.0 filenames:

- `dist/ltcai-8.4.0-py3-none-any.whl`
- `dist/ltcai-8.4.0.tar.gz`
- `dist/ltcai-8.4.0.vsix`
- `ltcai-8.4.0.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_8.4.0_aarch64.dmg`
