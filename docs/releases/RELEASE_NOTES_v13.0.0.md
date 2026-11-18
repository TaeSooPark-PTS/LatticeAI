# Lattice AI 13.0.0 — Sovereign Synthesis

> **Status: historical** — point-in-time release note.

2026-11-18

**13.0.0 Sovereign Synthesis** is a major architectural and intelligence upgrade delivering enforced cross-domain decoupling across `lattice-core`, `lattice-auth`, `lattice-agent`, `lattice-platform`, and `lattice-chat`, structured 2-hop Knowledge Graph reasoning paths & contradiction synthesis (`reasoning_paths.rs`, `MultiHopSynthesisView.tsx`), single-transaction review store mutations & one-click reversible folder reorganization undo, durable conversation document session persistence across restarts, modularized frontend API slices (`client.ts`, `streaming.ts`, `transfers.ts`), and unified Rust server i18n catalog verification.

## What landed

- **Enforced Domain-Boundary Decoupling & Outbound Compute Seam (`rust/`):**
  - Promoted canonical `OnceLock`-compiled secret redaction into `lattice_core::redact` (`redact_secret_text`, `redact_secrets`, `redact_structure`, `is_secret_key`), eliminating duplicate regex engines and sideways `knowledge -> workspaceos::workspace::redact` imports across `lattice-platform` and `lattice-chat`.
  - Extracted `lattice_agent::seam::worker` out of `lattice_agent::surface` so `kernel/` and `tools/` depend downward on the compute port and never import inbound HTTP transport (`surface/`), and migrated `lattice-chat` onto canonical `lattice_agent::{kernel,parse,content,tools,seam}` domain groups.
  - Extended `lattice_platform::http_reply` (`json_from_ordered`, `message_error`, `now_iso_offset`, `workspace_from_headers`) so `modelops/setup` carries zero imports from `adminops`, enforced permanently by `rust/lattice-platform/tests/domain_boundaries.rs`.
- **Multi-Hop Knowledge Graph Reasoning Paths & Contradiction Synthesis (`lattice-retrieval` & `Brain.tsx`):**
  - Added `rust/lattice-retrieval/src/bm25/reasoning_paths.rs` and upgraded `graph_rag.rs` + `fusion.rs` (`hybrid_bm25_vector_graph_search`) to track 1-hop and 2-hop reasoning chains (`seed_id` → `bridge_id` → `target_id`), discover bridge concepts (`bridge_concepts`), surface neighborhood conflicts/supersessions (`CONTRADICTS`, `CONFLICTS_WITH`, `SUPERSEDES`), and gate noisy vector-only seed promotion when lexical BM25F seeds exist.
  - Added interactive **2-Hop Multi-Hop Synthesis** mode (`MultiHopSynthesisView.tsx`, `ko`/`en` i18n, 100% Vitest coverage) to Brain Hybrid Search (`Brain.tsx`).
- **Single-Transaction Review Store, Reversible Reorganization Undo & Durable Chat Sessions:**
  - Collapsed `review_items` writes and `timeline` appends into a single atomic `WorkspaceOsStore` transaction (`update_state_with_review_event`, `try_update_state_with_review_event`) in `workspace/store.rs` and `governance/review_queue`.
  - Added automatic reverse `folder_reorganization` undo staging (`is_undo: true`, `undo_proposal_id`) in `governance/change_proposals.rs` (`tests/reversible_reorganization.rs`) paired with a **1-Click Undo** action and rollback badge in `ReviewCard.tsx`.
  - Added bounded atomic disk persistence (`chat_document_sessions.json`) to `lattice_chat::documents::DocumentSessions::with_data_dir` so multi-turn document follow-ups survive server and desktop restarts.
- **Unified Rust Server i18n, Frontend Client Split & Desktop Modularization:**
  - Unified `lattice_auth::messages` onto canonical `lattice_core::messages` (closing `WP-I3`) and extended `scripts/check_server_i18n.mjs` to guard all 146 sorted Rust `(id, ko, en)` catalog entries alongside all 6 Python worker routers.
  - Modularized `frontend/src/api/client.ts` (995 → ~605 lines) into `streaming.ts` and `transfers.ts` and split `src-tauri/src/backend.rs` (`backend_tests.rs`) well below 800 lines while maintaining **100% Vitest statement/branch/function/line coverage**.

## Artifacts

- `dist/ltcai-13.0.0-py3-none-any.whl`
- `dist/ltcai-13.0.0.tar.gz`
- `ltcai-13.0.0.tgz`
- `dist/ltcai-13.0.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_13.0.0_aarch64.dmg`
