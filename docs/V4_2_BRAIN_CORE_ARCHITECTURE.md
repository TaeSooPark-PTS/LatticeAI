# Lattice AI v4.2.0 — Brain Core Architecture

Status: released validation complete

v4.2.0 extracts the Digital Brain backend boundary into the importable
`lattice_brain` package while preserving the existing FastAPI contracts and the
v4.1.0 user data layout. FastAPI, CLI, tests, and future tools can now import
Brain Core directly instead of reaching through root compatibility modules.

## Package Boundary

- `lattice_brain` is the independent Brain Core package namespace.
- `latticeai.brain` remains as a compatibility namespace for existing callers.
- Root modules such as `knowledge_graph.py` and `kg_schema.py` remain
  compatibility shims.
- FastAPI now constructs the graph and durable conversation store through
  `lattice_brain.BrainCore`.

## Brain Core Surfaces

The package exposes the implemented v4 brain modules:

- Knowledge system: `KnowledgeGraphStore`
- Memory system: `BrainMemory`
- Context assembler: `ContextAssembler`
- Durable conversations: `ConversationStore`
- Device identity and signed exchange helpers
- Brain Network compatibility exports
- Encrypted `.latticebrain` archives
- Storage abstraction and migration tools

The frontend still talks only to FastAPI localhost APIs. No frontend code calls
Python directly.

## Storage Layer

`lattice_brain.storage` introduces:

- `StorageEngine` ABC
- `SQLiteEngine` default engine
- `PostgresEngine` opt-in engine with pgvector extension setup
- `DockerPostgresWizard` explicit-consent local Docker setup
- `SQLiteToPostgresMigrator` idempotent migration planner/runner, including
  rowid-less FTS5 shadow table support through declared primary keys

SQLite remains the default. Postgres is never required. If
`LATTICEAI_STORAGE_ENGINE=postgres` is selected without a DSN or optional
dependency support, startup fails honestly instead of silently falling back to
SQLite.

## Vector Search

SQLite vector search remains real and local:

- Existing vector rows stay in `vector_embeddings`.
- The active fallback is deterministic local hash embeddings with brute-force
  cosine scoring.
- `sqlite-vec` is detected and loaded when available.
- Capability reports distinguish `sqlite-vec` from `bruteforce-cosine`; the
  fallback is reported honestly and is still a real search path.

Postgres scale mode initializes a pgvector-backed `brain_vectors` table when
the `vector` extension is available.

## Archive Model

v4.2.0 adds encrypted `.latticebrain` archives:

- AES-256-GCM payload encryption
- PBKDF2-HMAC-SHA256 key derivation
- Encrypted SQLite database and blob payload
- Restore replaces the target DB/WAL/SHM safely and restores blobs
- Wrong passphrase or tampered data fails closed

The existing JSON export/import and ZIP backup/restore paths remain compatible.

## FastAPI APIs

New localhost APIs:

- `GET /api/brain/storage`
- `POST /api/brain/storage/postgres/docker`
- `POST /api/brain/storage/migrate-postgres`
- `POST /api/knowledge-graph/archive`
- `POST /api/knowledge-graph/archive/restore`

All mutating operations require admin authorization. Docker starts only when the
request explicitly carries consent.

## Compatibility

- Existing v4.1.0 SQLite data remains in `knowledge_graph.sqlite`.
- Existing `knowledge_graph.py`, `kg_schema.py`, and `latticeai.brain.*`
  imports continue to work.
- Existing FastAPI routes remain available.
- Existing release artifacts still build.
- No data-loss migration is performed automatically.
