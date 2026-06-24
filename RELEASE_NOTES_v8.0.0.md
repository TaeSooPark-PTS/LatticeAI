# Lattice AI v8.0.0 Release Notes

Runtime Architecture Contract release.

## Highlights

- Added `lattice-architecture-contract/v1` to make AgentRuntime, ToolRegistry,
  central Config, server decomposition, and Knowledge Graph hardening
  machine-checkable for the 8.0 line.
- Added `tool-registry-contract/v1` to the ToolRegistry manifest so dispatch,
  policy, and permission ownership are visible from one registry contract.
- Made Knowledge Graph logical `replace` imports transactional, preserving the
  existing graph if a malformed import fails mid-run.
- Locked KG v2 read-equivalence for document listing, node lookup,
  relationship search, and traversal.
- Preserved colliding legacy edge labels during logical import/backfill while
  keeping native write-door synonym dedupe canonical.
- Updated product readiness, package metadata, Tauri metadata, static asset
  metadata, and current-release documentation to 8.0.0.

## Expected Artifacts

- `dist/ltcai-8.0.0-py3-none-any.whl`
- `dist/ltcai-8.0.0.tar.gz`
- `dist/ltcai-8.0.0.vsix`
- `ltcai-8.0.0.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_8.0.0_aarch64.dmg`

Package publishing remains owner-run. Do not use wildcard artifact uploads.
