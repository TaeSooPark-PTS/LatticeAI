[![English](https://img.shields.io/badge/English-Active-2563eb?style=flat-square)](./RELEASE_NOTES_v12.1.0.md) [![한국어](https://img.shields.io/badge/한국어-Korean-475569?style=flat-square)](../ko/releases/RELEASE_NOTES_v12.1.0.md)

# Lattice AI v12.1.0 — Fast Path (2026-08-29)

> **Status: historical** — point-in-time release note.

A release that overlaps the previously single-file sequential pipeline of scan → parse → embed → index → search. The write engine remains a single `GraphWriter`; the bottlenecks were the seam in front of it and the lock inside it.

| | |
| --- | --- |
| Theme | **Fast Path** — straight from disk to recall |
| Door | Native 422 operations / 41 families (unchanged) |
| Worker | 20 routes (unchanged) |

## Added

- Folder ingest and `vault-watch` overlap files with `INGEST_INFLIGHT=4`. Stamp-skipped files do not enter the worker pool. Writes still serialize through the single SQLite writer.
- Note ingest sends document vectors and chunk vectors in a **single** `POST /worker/embed` request, overlapping extraction and embedding via `tokio::join!`.
- `write_vectors_with` persists those seam-produced vectors directly without re-running the hash model over the same text.
- Default `brute` search tries `hnsw+rescore` first when the store has 512+ rows and a worker sidecar is bound, falling back to an exact `brute` scan on failure. Golden tests (small fixtures, no worker) remain byte-identical.

## Changed

- `rebuild_vector_index` now computes embeddings **outside** the write transaction, matching the pattern 12.0.0 introduced for drain. Golden tests remain identical under `FrozenClock`.
- Fixed the weekly `pip-audit` job failing on `cat` when a clean run wrote no Markdown report file.
- Fixed the weekly live Postgres job failing due to a removed test file: the Python graph writer left in 11.6.0, the suite now reports that fact via skip, and `ltcai[postgres]` remains an empty extra alias.

## Fixed

- Dependency Audit (schedule) — clean `pip-audit` runs turning red due to a missing report file.
- Postgres Integration (schedule) — exit 4 caused by `tests/integration/test_postgres_migration_live.py` no longer existing in the tree.

## Known Issues

- Small-model *content* quality remains fail-closed.
- `api_key` cloud paths are mock-verified only.
- The macOS DMG is ad-hoc signed.
- The default vector search env remains `brute`. Auto `hnsw+rescore` activates only at 512+ rows with a bound worker and falls back to exact scan on failure. Explicit `LATTICEAI_VECTOR_INDEX=hnsw` behaves as before.
- `watch` does not auto-delete files.
- Real-embedding default rollout is not closed yet; the hash model remains the fallback when no downloaded embedder is present.

## Follow-up

- When a real embedder is bound at ingest time, the single-shot `/worker/embed` call is the default path; the hash fallback stays intact.
- Flipping `hnsw` to the env default requires re-measuring `recall@10` on a continuously ingested Brain.
- Folder-path extraction still runs once per file; it is overlapped rather than deferred to a backlog.
