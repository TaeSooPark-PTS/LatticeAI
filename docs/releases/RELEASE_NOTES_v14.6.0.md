[![English](https://img.shields.io/badge/English-Active-2563eb?style=flat-square)](./RELEASE_NOTES_v14.6.0.md) [![한국어](https://img.shields.io/badge/한국어-Korean-475569?style=flat-square)](../ko/releases/RELEASE_NOTES_v14.6.0.md)

# Lattice AI v14.6.0 — Within Your Scope (2026-11-28)

`v14.6.0` changes who an automation acts on by default, and fixes several places where the product reported something it had not actually done.

## Highlights

### 1. Owner-Scoped Automations (security and behaviour change)
- `brain_event` automations are owner-scoped by default (`owner_scope: "self"`). They fire only on items the owner created.
- `owner_scope: "workspace"` fires on shared items owned by other members of the same workspace. It never fires on private or conversation items and never crosses workspaces.
- **Action required for multi-user installs:** existing triggers that watched colleagues' uploads must be set to the **Workspace** scope in the automation form. Single-user installs are unaffected.
- New `source_types` and `title_contains` filters are available in the API and in the automation form.

### 2. Fixes
- Dismissed or approved synthesis proposals no longer stay `pending` and block re-proposals (stale `effective_status`).
- Hugging Face revision pinning is applied on download and load paths. `LATTICEAI_HF_REVISIONS` overrides the pins. Silent `TypeError` retries that dropped endpoint, token, or revision were removed. Models already downloaded are not re-pinned, and unpinned repositories still use `main`.
- The hooks catalog reports `executed`, `implementation`, and `not_executed_reason` for each hook, so a hook that is listed but does not run says so.

### 3. Interface
- Command palette opens conversation hits and has a top-bar button.
- Search results are actionable.
- Memory manager: view, add, search, and forget memories.
- "Save to Brain" shows its real status.
- Document library with filter and preview.
- Run history filters include cancelled runs, a "show more" control, and an error state.

### 4. Tests
- HNSW churn recall test.

## Known Gaps
- Private documents inside a workspace are not implemented. They are not part of this release, and no private upload capability is claimed. See [ROADMAP](../ROADMAP.md).

## Build Artifacts
- `dist/ltcai-14.6.0-py3-none-any.whl`
- `dist/ltcai-14.6.0.tar.gz`
- `ltcai-14.6.0.tgz`
- `dist/ltcai-14.6.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_14.6.0_aarch64.dmg`
