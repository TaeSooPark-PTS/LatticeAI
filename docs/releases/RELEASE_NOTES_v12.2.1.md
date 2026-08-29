# Lattice AI 12.2.1 — True Count

> **Status: historical** — point-in-time release note.

2026-08-29

12.2.0 Small Voice가 작은 모델에게 *도구*를 주었습니다. 12.2.1은 그
뒤에 남아 있던 숫자를 정직하게 셉니다. 벡터 쿼리는 COUNT 다음에
빠진 행만 읽고, 같은 바이트의 touch는 스탬프를 다시 찍고, 워치에서
사라진 파일은 그래프에서 빠지며, 얇은 요약은 읽은 파일의 문장으로
채워지거나 검토로 갑니다. 쓰기는 계속 `GraphWriter` 하나입니다.

문은 **422 operations / 41 families**, 워커는 **20 routes**.

## What landed

- **HNSW sidecar COUNT+delta.** A warm query is `COUNT(*)` against
  `vector_embeddings`. Matching size → search, no blob dump. Growth →
  ids, then blobs for missing ids, then `add_items` append (including
  a graph loaded from disk). Shrink → full rebuild of what remains.
- **SkipByHash restamps.** Same sha256, new size/mtime, updates the
  existing provenance row through `GraphWriter::update_ingestion_metadata`.
  The next scan skips by stamp.
- **Watch prune.** `scan_watches` calls `prune_deleted(..., confirm=true)`
  for vanished files. Disk files are never deleted. Folder ingest still
  reports and leaves nodes.
- **Summary salvage.** `complete_a_summary` fills a thin "I summarized"
  answer from `read_file` / `mcp.read_file` content already on the
  transcript. Missing evidence is `NEEDS_REVIEW`, not a false DONE.
- **Compact CORE includes MCP.** `mcp.grep`, `mcp.list_dir`,
  `mcp.read_file`, `mcp.knowledge_search` survive the nine-row cap
  alongside the native file tools. A request that names `mcp.grep`
  still ranks it first.
- **`api_key` live `/models` probe.** No completion, no billing. A
  rejected or unreachable key fail-closes `GET /api/cloud/status`.
- **Hash search is named hash.** First-run and the model library say
  so, and point at installing a meaning model. There is still no second
  embedder in `lattice-core`.
- **Release notes live in `docs/releases/`.** Root keeps `RELEASE.md`
  and `RELEASE_NOTES.md` as the current index.

## Honest leftovers

- Hash embeddings remain the no-download default. They are labeled
  `fallback`, not semantic.
- The macOS dmg is ad-hoc signed unless a Developer ID is present on
  the build machine. This tree does not ship Apple credentials.
- Marketplace listing can take a few minutes to verify after publish.
- Vector search env still defaults to `brute`. Auto `hnsw+rescore` at
  512+ is unchanged from 12.1.0.
- `GraphWriter::delete_node` still leaves `PART_OF`; prune does not
  use it.

## Artifacts

- `dist/ltcai-12.2.1-py3-none-any.whl`
- `dist/ltcai-12.2.1.tar.gz`
- `ltcai-12.2.1.tgz`
- `dist/ltcai-12.2.1.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_12.2.1_aarch64.dmg`
