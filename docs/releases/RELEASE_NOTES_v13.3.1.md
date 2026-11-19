# Lattice AI 13.3.1 — Living Synthesis

> **Status: canonical** — current release note.

2026-11-19

**13.3.1 Living Synthesis** completes the remaining v13.3.0 follow-up architecture and UX items: splitting incremental document sync into a lock-free two-phase async pipeline (`plan_incremental_sync` + async `/worker/embed` + `commit_incremental_sync`) so watched-folder edits under semantic embedding models reuse unchanged chunk vectors instead of re-embedding whole files, surfacing claim-level verification telemetry (`claim_verification`) in `AnswerProof.tsx` and entity-resolution/temporal-timeline summaries (`entity_resolution`, `temporal_timeline`) in `BrainIntelligencePanel.tsx` (at **100% Vitest coverage**), and enabling weekly GitHub Dependabot coverage across Python, npm, Cargo, Tauri, and GitHub Actions.

## What landed

- **Two-Phase Async Semantic Incremental Chunk Sync (`rust/lattice-ingest/src/{incremental_sync.rs,local_files_api/watch_deliver.rs,local_files_api/watch_bridge.rs}`):**
  - Split `incremental_sync` into `plan_incremental_sync` (blocking SQLite read identifying cached vs. uncached chunk hashes) and `commit_incremental_sync` (atomic GraphWriter commit & superseded chunk retirement), keeping `sync_document_incremental` as a clean wrapper.
  - Extracted `local_files_api/watch_deliver.rs` (`try_incremental_sync_async`) so watched-file edits under **both** local-hash and semantic `EmbeddingIndexContract`s run `plan_incremental_sync` → async `POST /worker/embed` (via `post_idempotent_compute`) → `commit_incremental_sync` with zero `Handle::block_on` deadlock risk.
  - Added end-to-end integration test `semantic_watched_edit_reembeds_only_changed_chunks_via_two_phase_sync` in `rust/lattice-ingest/tests/vault_watch_bridge.rs` verifying unchanged chunks are reused under a semantic embedder contract.
- **Claim Verification UI in `AnswerProof.tsx` (`frontend/src/features/brain/{AnswerProof.tsx,BrainConversation.tsx,brainData.ts,types.ts}`, `frontend/src/i18n/brain/evidence.ts`):**
  - Extended `parseGrounding` and `MessageGrounding` with `MessageClaimVerification` (`claimSupportRatio`, `citationPrecision`, `citationRecall`, `supersededCitationLeaks`, `numericMismatches`, `polarityMismatches`, `shouldAbstain`, `abstentionReason`).
  - Added bilingual (`ko`/`en`) `ClaimVerificationStrip` inside `AnswerProofCard` showing claim support and citation precision percentages plus warning badges for numeric mismatches, polarity mismatches, superseded citation leaks, and abstention recommendations, with full 100% Vitest coverage in `AnswerProof.test.tsx` and `brainData.test.ts`.
- **Entity Resolution & Temporal Timeline Card in `BrainIntelligencePanel.tsx` (`frontend/src/features/brain/BrainIntelligencePanel.tsx`, `frontend/src/i18n/brain/care.ts`):**
  - Added bilingual (`ko`/`en`) `ConsolidationEpistemicSummary` displaying `entity_resolution` (scanned entities, merge clusters, preview candidates / applied `SAME_AS` edges) and `temporal_timeline` (active, historical, and unreached facts plus active temporal conflict warning badge) right below consolidation results, with 100% Vitest coverage in `BrainIntelligencePanel.test.tsx`.
- **Automated Supply-Chain Monitoring (`.github/dependabot.yml`):**
  - Added weekly Dependabot update schedules across `pip` (`/`), `npm` (`/`, `/vscode-extension`), `cargo` (`/rust`, `/src-tauri`), and `github-actions` (`/`) alongside the weekly `Dependency Audit` workflow.

## Artifacts

- `dist/ltcai-13.3.1-py3-none-any.whl`
- `dist/ltcai-13.3.1.tar.gz`
- `ltcai-13.3.1.tgz`
- `dist/ltcai-13.3.1.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_13.3.1_aarch64.dmg`
