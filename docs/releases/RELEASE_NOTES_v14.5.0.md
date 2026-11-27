[![English](https://img.shields.io/badge/English-Active-2563eb?style=flat-square)](./RELEASE_NOTES_v14.5.0.md) [![한국어](https://img.shields.io/badge/한국어-Korean-475569?style=flat-square)](../ko/releases/RELEASE_NOTES_v14.5.0.md)

# Lattice AI v14.5.0 — Living Automations (2026-11-27)

`v14.5.0` turns Lattice AI's governance, retrieval, and portability surfaces into live everyday workflows: scheduled and event-driven automations now execute and stage drafts in the Review Center, "latest" retrieval respects trust tiers and user citation feedback, and your entire workspace Brain exports as a clean Markdown/Obsidian vault.

## Highlights

### 1. Living Automations (`lattice-platform`, `lattice-host`, `lattice-core`)
- **Real Trigger Engine (`workflow_designer/triggers/`)**: Enabled `interval` and `brain_event` automations are persisted in `WorkspaceOsStore`, armed with next-run slots, and evaluated by a background scheduler loop started alongside `lattice-host`'s jobs scheduler.
- **Post-Ingest `brain_event` Dispatch**: `GraphWriter` notifies an attached `IngestListener` after every non-duplicate `ingest_content` / `ingest_file` write, firing matching workspace `brain_event` automations (such as **Follow-up Radar**) without blocking ingestion.
- **Drafts-Before-Automation Safety**: Unattended runs only draft for review (staging `pending` items in the Review Queue with `source: "trigger"` and full provenance) and skip workflows containing non-draft steps (`requires_manual_run`) with an explicit `skip_reason`.
- **Honest `GET /workflows/api/triggers`**: Returns real workspace-scoped `armed` rows including `kind`, `next_run_at`, `last_run_at`, `last_status`, `blocked`, and `skip_reason`.

### 2. Trust-Aware "Latest" Ranking & Citation Feedback (`lattice-core`, `lattice-retrieval`)
- **Trust Tiers (`temporal/trust.rs`)**: Frontmatter (`graph_write/frontmatter.rs`), user trust metadata, lifecycle verification, confirmed corrections, and ingestion provenance classify nodes into `Verified`, `Normal`, and `Draft` tiers (with legacy-schema-safe column detection on older SQLite databases).
- **Citation Feedback Loop (`correction_signals.rs`, `temporal/signals.rs`)**: Active, workspace-scoped `wrong_source` (`0.25×`) and `useful` (`1.1×`) verdicts from `knowledge_corrections` adjust retrieval scores and demote flagged sources in newest-first ordering.
- **"Not a Good Source" UI (`SourceFeedback.tsx`, `AnswerProof.tsx`)**: One-click source demotion and undo directly from answer proofs and search results.

### 3. Markdown / Obsidian Vault Export (`lattice-platform`, `frontend/`)
- **New `POST /api/portability/vault` Endpoint**: Exports workspace-scoped knowledge as a deterministic ZIP vault with YAML frontmatter, `[[wikilinks]]`, `_history/` placement for superseded notes, `_export.json` manifest counts, secret redaction, path-traversal sanitization, and strict foreign/share-blocked exclusion.
- **Settings UI (`VaultExportCard.tsx`)**: One-click vault download directly from System Settings, with OpenAPI fragments and TypeScript client types synchronized (`431` paths / `474` operations / `181` schemas).

### 4. Import Sources UI, Conversation Library & Act/Review Polish (`frontend/`)
- **Import Sources (`ImportPanel.tsx`, `api/imports.ts`)**: Unified Capture panel for Obsidian, Notion, Git, and Mail imports.
- **Conversation Library (`ConversationLibrary.tsx`, `conversationExport.ts`)**: Full-history search, browse-all drawer, and one-click Markdown export with safe filename sanitization.
- **Act, Review & Search Enhancements**: Workflow run history and replay viewer (`WorkflowRunHistory.tsx`), fixed export-download and run stop-button behaviors, bulk approve/dismiss bar (`BulkReviewBar.tsx`), and date-range + historical search filters (`SearchFilters.tsx`, `SearchResults.tsx`).

## Distribution Artifacts

- `dist/ltcai-14.5.0-py3-none-any.whl`
- `dist/ltcai-14.5.0.tar.gz`
- `ltcai-14.5.0.tgz`
- `dist/ltcai-14.5.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_14.5.0_aarch64.dmg`
