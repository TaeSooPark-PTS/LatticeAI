# Lattice AI v4.3.0 Portability Architecture

## Scope

v4.3.0 hardens the v4.2 Brain Core/storage architecture without redesigning it.
The primary portable brain format is the encrypted `.latticebrain` archive.
FastAPI remains the only boundary consumed by the frontend and desktop shell.

## `.latticebrain` Archive Format

The archive is a JSON envelope with:

- `format = latticebrain.encrypted`
- `format_version = 2`
- PBKDF2-HMAC-SHA256 key derivation metadata
- AES-256-GCM cipher metadata
- encrypted ZIP payload
- payload SHA-256
- non-secret manifest summary for inspection

The encrypted payload contains:

- `knowledge_graph.sqlite`
- `blobs/` from the Knowledge Graph blob store
- portable JSON state under `data/`
- signed graph export bundles under `workspace_exports/`
- `manifest.json` with entry hashes, section flags, storage metadata,
  public device identity metadata, provenance, and version.

Private key material such as `device_identity.key` is deliberately excluded.

## Operations

- Export: `POST /api/knowledge-graph/archive`
- Inspect: `POST /api/knowledge-graph/archive/inspect`
- Verify: `POST /api/knowledge-graph/archive/verify`
- Import: `POST /api/knowledge-graph/archive/import`
- Restore: `POST /api/knowledge-graph/archive/restore`
- Backup health: `GET /api/knowledge-graph/backup-health`

Restore/import fail closed unless the request is a dry run or includes
`confirm: true`.

## Compatibility

- v1 `.latticebrain` payloads that contain only DB/blob data remain restorable.
- SQLite remains the default source and target.
- Postgres scale-mode brains export through safe logical/archive semantics; the
  migration tooling still requires explicit DSN and does not silently fall back.
- Existing Knowledge Graph JSON exports and ZIP backups remain supported.

## Integrity Policy

Archives fail closed on:

- bad passphrase
- corrupt envelope
- corrupt ZIP payload
- payload SHA mismatch
- missing brain database
- manifest hash mismatch
- unsupported future archive version
- unsafe ZIP member paths

## User Safety

Destructive restore operations require admin permission and explicit
confirmation. Dry-run restore returns the target paths and payload sections
without mutating user data.
