# Lattice AI v4.2.0 RC — Storage Migration Report

Status: implemented release candidate

v4.2.0 adds a pluggable storage layer without changing the default local-first
runtime. Existing users continue on SQLite. Postgres is an explicit opt-in
scale target, not a required dependency and not an automatic fallback.

## Existing Data

- Existing v4.1.0 data remains in `~/.ltcai/knowledge_graph.sqlite`.
- Blob payloads remain in `~/.ltcai/knowledge_graph_blobs/`.
- Durable conversations continue to share the same SQLite DB family.
- No startup migration rewrites user data for v4.2.0.

## SQLite Engine

`SQLiteEngine` owns SQLite connection setup:

- WAL mode
- foreign keys enabled
- local path creation
- backup and restore helpers
- sqlite-vec loading attempt when installed
- honest capability report when sqlite-vec is unavailable

The current graph store continues to use SQLite-specific SQL and is therefore
wired only to `SQLiteEngine` for the active FastAPI runtime.

## Postgres Engine

`PostgresEngine` is opt-in and fail-closed:

- Requires `LATTICEAI_POSTGRES_DSN` when explicitly selected.
- Requires optional `psycopg` support.
- Creates `lattice_brain` schema by default.
- Runs `CREATE EXTENSION IF NOT EXISTS vector`.
- Creates a pgvector-backed `brain_vectors` table.

If Postgres is selected but unavailable, Lattice AI reports the error. It does
not hide the failure by falling back to SQLite.

## Docker Setup

`DockerPostgresWizard` writes a local Docker Compose file for
`pgvector/pgvector:pg16`. It never runs Docker unless the caller explicitly
passes consent.

API behavior:

- `consent=false`: writes the compose file and returns `consent_required`.
- `dry_run=true`: returns the exact Docker command without starting anything.
- `consent=true` and `dry_run=false`: runs `docker compose up -d postgres`.

## SQLite to Postgres Migration

`SQLiteToPostgresMigrator` plans and copies all user tables from a SQLite brain
database into Postgres:

- Introspects non-internal SQLite tables.
- Preserves every row.
- Uses table `id` as the idempotence key when present.
- Uses preserved `__source_rowid` when no `id` column exists.
- Upserts rows on repeated runs.
- Leaves the source SQLite database untouched.

The API defaults to dry-run migration planning. Actual copy requires an
explicit DSN and `dry_run=false`.

## Encrypted Archives

`.latticebrain` archive support was added for local encrypted backup/restore:

- Database and blobs are zipped locally.
- Payload is encrypted with AES-256-GCM.
- Keys derive from the user passphrase via PBKDF2-HMAC-SHA256.
- Restore rejects bad passphrases or tampered payloads.

## Compatibility Result

No v4.1.0 capability is removed. SQLite remains the default and does not depend
on Docker, Postgres, pgvector, or network access.

