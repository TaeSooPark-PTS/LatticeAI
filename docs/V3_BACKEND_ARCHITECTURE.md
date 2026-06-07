# Lattice AI v3 Backend Architecture

Lattice AI v3 keeps the product local-first and knowledge-centric by combining
three retrieval layers inside the backend:

```text
User Data
  -> Chunking
  -> Local Embeddings
  -> SQLite Vector Index

User Data
  -> Entity Extraction
  -> Relationship Extraction
  -> SQLite Knowledge Graph

Search
  -> Hybrid Retrieval
     -> Keyword
     -> Vector
     -> Graph
  -> Unified Results
```

## Storage Model

The authoritative knowledge store remains `knowledge_graph.py` on SQLite.
Existing legacy graph rows are preserved, with the normalized v2 projection kept
as a derived read surface.

- `nodes`: entities, documents, folders, conversations, tasks, decisions, and
  chunk nodes.
- `edges`: directed relationships with type, weight, metadata, and timestamps.
- `chunks`: extracted text chunks linked back to the source node.
- `knowledge_sources` and `local_file_index`: approved local folder indexing
  state, source metadata, and incremental scan bookkeeping.
- `vector_embeddings`: derived local vector index rows for non-chunk nodes and
  full chunk text.
- `vector_index_operations`: rebuild/incremental indexing history and status.

Vector rows are derived data. A full rebuild may delete and recreate vector
rows, but it does not mutate source documents, nodes, relationships, chunks, or
local file records.

## Embeddings

The default embedding model is `lattice-local-hash-v1`, a deterministic local
feature-hashing embedder in `latticeai/core/local_embeddings.py`.

It provides:

- no cloud dependency,
- no model download requirement,
- deterministic output for tests and migrations,
- normalized vectors for cosine similarity by dot product,
- a stable interface for replacing the implementation with a local model
  runtime later.

It is a fallback vector signal, not a production semantic embedding model.
Future provider support may include Ollama, MLX, OpenAI-compatible providers,
and other local embedding providers behind the same vector-index interface.

## Search Model

`latticeai/services/search_service.py` composes the retrieval layers.

- Keyword search uses the existing graph text search over title, summary, and
  metadata.
- Vector search embeds the query locally and ranks SQLite vector rows by
  similarity.
- Graph search performs direct graph matching, relationship search, and bounded
  neighbor expansion.
- Hybrid search fuses the three result streams with configurable weights and
  returns one UI-ready result shape with per-source scores.

Default hybrid weights:

```json
{
  "keyword": 0.35,
  "vector": 0.40,
  "graph": 0.25
}
```

## API Contracts

The v3 backend exposes frontend-ready routes under `/api` while preserving all
existing `/knowledge-graph/...` compatibility routes.

```text
POST /api/search/hybrid
GET  /api/search/hybrid?q=...
POST /api/search/keyword
GET  /api/search/keyword?q=...
POST /api/search/vector
GET  /api/search/vector?q=...
GET  /api/graph
GET  /api/graph/node?node_id=...
POST /api/graph/node
GET  /api/graph/relationship
POST /api/graph/relationship
GET  /api/index/status
POST /api/index/rebuild
```

Results include stable fields for UI consumption:

- `id`
- `node_id`
- `item_type`
- `type`
- `title`
- `summary`
- `score`
- `rank`
- `sources`
- `source_scores`
- `metadata`
- `updated_at`
- optional `graph_context`

## Migration Impact

The migration is additive and non-destructive:

- new SQLite tables are created with `CREATE TABLE IF NOT EXISTS`,
- existing graph and local file data is preserved,
- normalized v2 graph projection remains a derived compatibility layer,
- vector rows can be rebuilt from existing graph/chunk rows,
- `GET /api/index/status` reports missing or stale vector items,
- `POST /api/index/rebuild` repairs the derived index.

## Backend Ownership

This architecture deliberately avoids frontend shell redesign. The backend owns
storage, indexing, retrieval, ranking, and API contracts; the UI can consume the
new `/api` routes without requiring visual system changes.
