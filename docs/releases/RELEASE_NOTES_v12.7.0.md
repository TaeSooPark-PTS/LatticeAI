# Lattice AI 12.7.0 — Fast Index

> **Status: historical** — point-in-time release note.

2026-11-16

12.6.0 Hybrid Graph introduced 3-way BM25F + Vector + Knowledge Graph RRF hybrid search. **12.7.0 Fast Index** eliminates query-time re-tokenization and graph-link re-parsing with a **Pre-Tokenized BM25F Sidecar**, **SQLite `bm25_node_stats` persistence**, and an **inverted posting-list & GraphRAG adjacency cache**, while polishing the repository around a **product-first `README.md`** and a clean **`v12.0.0+` release-history floor**.

The door serves **422 operations / 41 families**, and the Python worker stays at **20 routes**. Writes still go through a single `GraphWriter`.

## What landed

- **Pre-Tokenized BM25F Sidecar & SQLite `bm25_node_stats` Persistence.**
  - `rust/lattice-core/src/bm25_stats.rs`, `db/tables.rs`, `graph_write/{mod,primitives,documents,curation,provenance}.rs`: Registered `bm25_node_stats` in `TableOwnership` and enabled `persist_bm25_sidecar` in `GraphWriter::open` so `upsert_node_with_vector` and `set_node_sensitivity` persist pre-tokenized field lengths (`dl_title`, `dl_tags`, `dl_body`, `dl_meta`) and term frequencies (`tf_json`), cleaning up automatically on deletes, curation, and replace-imports. Read-only and frozen golden databases without `bm25_node_stats` safely skip sidecar hydration with zero schema mutation.
  - `rust/lattice-retrieval/src/bm25/{sidecar,sidecar_cache,scoring,graph_rag,fusion,tokenizer,mod,tests}.rs`, `service_hybrid.rs`: Added `Bm25SidecarIndex` for O(1) `field_lengths`, `term_freqs`, `doc_freq`, `avgdl`, and inverted `postings` (`term -> Vec<PostingEntry>`) candidate evaluation, incremental `(node_id, updated_at, content_hash)` sync, and cached GraphRAG adjacency (`title_to_doc_idxs`, `wikilink_to_doc_idxs`, `tag_to_doc_idxs`, `edge_adj`) via `expand_knowledge_graph_with_sidecar` and `score_and_expand_with_sidecar`.
- **Product-First `README.md` & `v12.0.0+` Release-History Polish.**
  - Redesigned `README.md` around end-user product pillars, Desktop/CLI/Extension quick start, an 8-panel Product Tour screenshot grid, Documentation & Architecture Hub, Honest Boundaries, and `v12.0.0+` release history.
  - Pruned pre-v12 release notes (`v9.*`–`v11.*`) and pre-v12 review/plan snapshots from `docs/releases/`, `docs/reviews/`, `RELEASE.md`, `RELEASE_NOTES.md`, `docs/CHANGELOG.md`, `FEATURE_STATUS.md`, `SECURITY.md`, `vscode-extension/README.md`, and GitHub Releases, and updated `scripts/check_current_release_docs.mjs` and `tests/unit/test_server_app_decomposition.py` to enforce the `v12.0.0+` floor.

## Artifacts

- `dist/ltcai-12.7.0-py3-none-any.whl`
- `dist/ltcai-12.7.0.tar.gz`
- `ltcai-12.7.0.tgz`
- `dist/ltcai-12.7.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_12.7.0_aarch64.dmg`
