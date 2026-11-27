[![English](https://img.shields.io/badge/English-Active-2563eb?style=flat-square)](./RELEASE_NOTES_v14.3.0.md) [![한국어](https://img.shields.io/badge/한국어-Korean-475569?style=flat-square)](../ko/releases/RELEASE_NOTES_v14.3.0.md)

# Lattice AI v14.3.0 — Temporal Synthesis

Release date: **2026-11-27**

`v14.3.0` resolves four interactive desktop UX defects (including the post-send chat collapse blocker), significantly lifts chat-path RAG retrieval and temporal accuracy with CI ratchet floors, and closes six multi-user privacy, integrity, and scaling findings.

## Highlights

### 1. Frontend UX Fixes

- **Desktop chat collapse after first answer:** moved `BrainMemoryAutomation` and save-feedback blocks into the scrollable `.brain-stream` container so messages and the composer stay inside the viewport across `1024×640`, `1280×720`, and `1440×900` (locked with Playwright visual tests).
- **`Cmd/Ctrl+K` "Today's briefing" cross-route opening:** routed the briefing trigger through a pending flag in `appStore` so choosing "Today's briefing" from any route navigates to Brain, opens the containing drawer, expands the panel, and scrolls it into view.
- **2D Memory Graph "Show superseded" toggle:** moved superseded filtering into `buildExplorerModel` so both 3D and 2D (no-WebGL fallback) views and node counts honour the toggle.
- **Command palette keyboard scrolling:** added `scrollIntoView({ block: "nearest" })` on the active option as `activeIndex` changes so arrow-key selection stays visible.

### 2. Chat Retrieval & Temporal RAG Accuracy

- **IDF/BM25F relevance ranking in chat:** unioned rule concepts with fallback query tokens and switched chat keyword ranking to IDF/BM25F relevance scoring instead of binary hit counts with newest-wins ties.
- **True `as_of` point-in-time semantics:** added `TimeIntent.as_of`, year-only date parsing, valid-time filtering, `Relevance` sorting for `as_of` queries, and `as_of`-anchored temporal state evaluation, wired through `ContextRequest.as_of`.
- **Stricter Self-RAG abstention:** aligned coverage tokenization with grounding tokenization and enforced coverage checks so entity-only overlap on ungrounded queries abstains rather than falsely reporting sufficient evidence.
- **CI chat-path RAG ratchet (`rust/lattice-retrieval/src/rag_eval_chat.rs`):** gates all 248 answerable queries and 32 workspace-boundary queries in `datasets/rag_eval_v1.json` under both constant and `valid_from` timestamp modes.

#### RAG Evaluation Metrics (`datasets/rag_eval_v1.json`, hash embedder, n=248)

| Metric | v14.2.0 Baseline (Chat Path) | v14.3.0 CI Floor (Constant / ValidFrom) | Status |
| --- | :---: | :---: | :---: |
| Overall Recall@5 | 0.9194 | >= 0.9800 / >= 0.9800 | Pass |
| Overall MRR | 0.6122 | >= 0.8500 / >= 0.8300 | Pass |
| Overall nDCG@10 | 0.6826 | >= 0.8700 / >= 0.8600 | Pass |
| `temporal_ordering` Recall@5 | 0.6000 | >= 0.9900 / >= 0.9900 | Pass |
| `temporal_ordering` MRR | 0.2670 | >= 0.7900 / >= 0.7900 | Pass |
| Workspace Boundary Leaks (n=32) | 0 | 0 | Pass |
| Superseded Exclusion Rate | 1.0000 | 1.0000 | Pass |

### 3. Multi-User Privacy, Integrity & Performance Hardening

- **Viewer-filtered share bundle exports:** `build_subgraph_bundle` now derives the viewer from caller identity, removes foreign conversation-derived nodes (including neighbors and chunks), uses `nodes_v2.workspace_id`, and requires `workspace_id` when authentication is enabled.
- **Hardened share bundle imports (`rust/lattice-core/src/graph_write/share_import.rs`):** replaces raw graph merge on accept with collision-safe ID handling, forced accepting workspace/owner attribution, node-type allow-listing (excluding conversation types), metadata sanitization, and `knowledge_sources` exclusion.
- **Scoped temporal prefilters:** `TemporalPlan::resolve`, `window_node_ids`, and `version_anchor` now accept `Scope` and enforce `workspace_membership_sql` + `owner_visible_sql` on `nodes_v2`, closing cross-tenant anchor and count leaks.
- **Scope-before-`LIMIT` graph, brain, and search reads:** pushed `workspace_membership_sql` into SQL `WHERE` before `LIMIT` in `graph_view` and `graph_slice`, and passed `Scope` into `keyword::search` in `/api/graph/search`.
- **Fail-closed Chronicle viewer filter:** `retain_viewer` in `chronicle_api/store.rs` now returns an empty list instead of unfiltered rows on SQL errors.
- **Indexed supersede-on-ingest identity lookups:** added additive expression indexes `idx_nodes_source_uri` and `idx_nodes_filename` and literal JSON path predicates in `identity.rs`.

## Distribution Artifacts

- `dist/ltcai-14.3.0-py3-none-any.whl`
- `dist/ltcai-14.3.0.tar.gz`
- `ltcai-14.3.0.tgz`
- `dist/ltcai-14.3.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_14.3.0_aarch64.dmg`
