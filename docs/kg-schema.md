[![English](https://img.shields.io/badge/English-Active-2563eb?style=flat-square)](./kg-schema.md) [![한국어](https://img.shields.io/badge/한국어-Korean-475569?style=flat-square)](./ko/kg-schema.md)

# Knowledge Graph Schema

Current release: **14.3.0 — Temporal Synthesis**.

Spec reference: `lattice_ai_full_spec.pptx` slides 20, 21, and 22  
Implementation: `lattice_brain/graph/schema.py`

---

## One-Line Summary

> **Nodes are every *noun* in the workspace. Edges are every *verb*.**  
> Every node can carry an embedding vector, and every edge carries
> `(direction, type, weight, confidence, evidence)`.

---

## Node

```
Node {
  id          string          // ULID, durable, globally unique ("node:01HX…")
  type        NodeType        // one of the 10 core types below (plus additive extensions)
  label       string          // human-readable short name (<=240 chars)
  attrs       object          // structured type-specific metadata
  embedding   float[1024]?    // semantic vector (optional, recommended)
  owner_id    string?         // owning user
  visibility  Visibility      // private | internal | shared | public
  created_at  ISO8601 UTC (offset-free YYYY-MM-DDTHH:MM:SS, unified across Rust & Python)
  updated_at  ISO8601 UTC (offset-free YYYY-MM-DDTHH:MM:SS, unified across Rust & Python)
}
```

### Core Node Type Catalog

| Type | Meaning | Representative `attrs` |
|------|---------|------------------------|
| `CONVERSATION` | Full conversation session | `started_at`, `model_id`, `mode` |
| `MESSAGE` | Single utterance | `role`, `tokens`, `parent_id` |
| `FILE` | Uploaded or linked file | `mime`, `sizeBytes`, `pageCount`, `lang` |
| `CHUNK` | Split segment of a file | `parent_file`, `offset`, `length` |
| `CODE_SYMBOL` | Function, class, or module | `path`, `lang`, `signature` |
| `CONCEPT` | Extracted concept or tag | `aliases[]`, `extractor` |
| `PERSON` | User or collaborator | `email`, `role` |
| `MODEL` | Local or remote LLM | `provider`, `runtime`, `quantization` |
| `TOOL` | MCP server or external tool | `scope`, `version`, `capabilities[]` |
| `PROJECT` | Topic-scoped workspace | `description`, `members[]` |

---

## Edge

```
Edge {
  id           string          // ULID ("edge:01HX…")
  source       string          // source node id
  target       string          // target node id (directed)
  type         EdgeType        // one of the canonical edge types below
  weight       float [0..1]    // relationship strength
  confidence   float [0..1]    // extraction/inference confidence
  evidence     string[]        // supporting message/chunk IDs
  created_by   string          // extractor:llm-gemma-4-12b | rule:regex | user
  created_at   ISO8601 UTC
}
```

### Core Edge Type Catalog

| Type | Suggested `source → target` | Meaning |
|------|-----------------------------|---------|
| `CONTAINS` | `FILE → CHUNK` | File contains chunk |
| `MENTIONS` | `MESSAGE` / `FILE` / `CHUNK` → `CONCEPT` / `PERSON` / `MODEL` / `TOOL` | Mentions or references in text |
| `REFERENCES` | `FILE` / `MESSAGE` / `CHUNK` → same | Explicit reference or link |
| `REPLIES_TO` | `MESSAGE → MESSAGE` | Reply relationship |
| `AUTHORED_BY` | `FILE` / `MESSAGE` / `CONVERSATION` → `PERSON` | Author attribution |
| `USES` | `PROJECT` / `CONVERSATION` → `TOOL` / `MODEL` | Tool or model usage |
| `DERIVED_FROM` | `CHUNK` / `FILE` → same | Summary or transformation lineage |
| `SIMILAR_TO` | ANY ↔ ANY | Cosine-similarity link (self allowed) |
| `DEPENDS_ON` | `CODE_SYMBOL → CODE_SYMBOL` | Code dependency |
| `TAGGED_AS` | ANY → `CONCEPT` | Tagging |
| `VERSION_OF` | `FILE → FILE` | Version history |
| `GRANTS_ACCESS` | `PERSON → FILE` / `CONVERSATION` / `PROJECT` | Access grant |

**Endpoint rules are advisory (schema documentation).** There is no runtime
endpoint-pair validator — `validate_endpoints` was never implemented, and write
paths do not reject type pairs. Write normalization enforces the *type
vocabulary*: `_upsert_edge` normalizes every edge type to a canonical
`EdgeType` value so free-form string edge types are no longer created.

---

## Current Knowledge-Graph-First Entities & Relations

8.4.0 promoted "all data sources converge into the Knowledge Graph" to a
first-class schema contract. The types below are **additive** — they do not
break existing enum/legacy mappings, `from_legacy` normalizes them losslessly,
and unknown types still fall back to `CONCEPT` / `MENTIONS`. The schema stays
**extensible**: adding a new domain entity requires only one enum member plus
aliases in `_LEGACY_NODE_MAP` / `_LEGACY_EDGE_MAP`.

### Additional Node Types

| Type | Meaning | Representative `attrs` / Provenance |
|------|---------|-------------------------------------|
| `SOURCE` | Provenance node for an ingested source (file, URL, browser tab, git, etc.) | `source_type`, `source_uri`, `content_hash`, `captured_at` |
| `REPOSITORY` | Git repository | `remote`, `branch`, `head` |
| `MEETING` | Meeting or call | `started_at`, `attendees[]` |
| `ORGANIZATION` | Organization, company, or team | `domain`, `members[]` |
| `WORKFLOW` | Workflow definition or run | `workflow_id`, `status` |
| `AGENT` | Agent (role / execution actor) | `role`, `model_id` |
| `SECTION` | Document section (span covered by one heading) | `heading_path`, `depth`, `document` — actively emitted since v12.0.0 |

### Additional Edge Types

| Type | Suggested `source → target` | Meaning |
|------|-----------------------------|---------|
| `INDEXED_FROM` | ANY → `SOURCE` | Which source this node was indexed from (provenance) |
| `MODIFIED_BY` | ANY → `PERSON` | Last modifier |
| `BELONGS_TO_PROJECT` | ANY → `PROJECT` | Project membership |
| `PART_OF` | ANY → ANY | Component / containment hierarchy |
| `HAS_CHUNK` | `SECTION` → `CHUNK` | Text chunk belonging to a section |
| `CONTRADICTS` | ANY ↔ ANY | Two statements conflict with one another |
| `DISCUSSED_IN` | `CONCEPT` / `DECISION` → `MEETING` / `CHAT` | Where an item was discussed |
| `DECIDED_BY` | `DECISION` → `PERSON` | Decision maker |
| `GENERATED_BY` | ANY → `AGENT` / `MODEL` / `WORKFLOW` | Generating actor |
| `USED_BY_AGENT` | ANY → `AGENT` | Consumed by an agent |

### Section Tree (v12.0.0)

`Section` nodes and `PART_OF` / `HAS_CHUNK` edges existed in the taxonomy
earlier, but **nothing emitted them**. Since v12.0.0, the typed chunker's
`heading_path` (heading trail joined by `" > "`, e.g.
`Architecture > Storage`) no longer stays trapped in chunk metadata strings —
it materializes as a tree in the graph:

```
Document ──◄PART_OF── Section(Architecture) ──◄PART_OF── Section(Architecture > Storage)
                                                               │
                                                           HAS_CHUNK
                                                               ▼
                                                             Chunk
```

| Element | Rule |
|---------|------|
| `Section` node | `label` is the section heading (final segment); full trail lives in `attrs`. The ID is a deterministic hash of `(document, heading_path)` so re-ingestion is idempotent. |
| `Section —PART_OF→ Section` / `Document` | Spine of the table of contents. Top-level sections attach directly to the document. |
| `Section —HAS_CHUNK→ Chunk` | Links each text chunk to the heading that governs it. |
| Headingless documents | **Create no sections.** Inventing an "Untitled Section" per file would put names into the graph that the author never wrote. |
| Write path | Written only after the document lands, exclusively via `GraphWriter::upsert_nodes` / `upsert_edges` — no schema widening. |

Both "which section did this fact come from?" and "what else lives in that
section?" are now answerable directly from the graph. Measured on the release
corpus: **549 of 555 triples** carry section provenance.

### Contradictions and Evidence Classification (v12.0.0)

`CONTRADICTS` previously existed only in the vocabulary. Since v12.0.0,
extraction **actually produces** `PART_OF` and `CONTRADICTS`, edges are written
with explicit direction and type, and `evidence` records whether a relationship
came from a verb phrase or co-occurrence. Contradictions are never resolved
silently — they surface as Review Center proposals, and `valid_from` /
`valid_to` are stamped only after human approval.

### Unified Ingestion Shape

Every source enters the graph in the same canonical shape:

```
SOURCE ──INDEXED_FROM◄── Document/File ──CONTAINS──► Chunk[]
   ▲                          │
   │                          └──(mentions/contains)──► Concept / Task / Decision …
provenance(source_type, source_uri, content_hash, captured_at, modified_at,
           owner, workspace_id, permissions, pipeline, embedded, linked)
```

- **Content nodes** (Document/File/web nodes) are idempotent on `content_hash` —
  re-ingesting identical content updates/links instead of creating duplicates.
- Every content node has an `INDEXED_FROM` edge to a `SOURCE` node so
  **provenance is always explainable**.
- Provenance is embedded in `metadata.provenance` and also recorded in the
  auditable `ingestion_provenance` table (`KnowledgeGraphStore.get_provenance(node_id)`).
- Local folder ingestion projects the same `workspace_id` across Computer,
  Drive, Folder, File, Chunk, Concept, and semantic nodes and partitions new
  IDs per workspace. Existing unscoped local folders reproject into the
  personal Brain while preserving existing node IDs and never silently
  reassigning a folder to a different workspace.

Implementation: since v11.6.0 the single entry point is Rust's native ingest
door (`lattice-ingest` → `lattice_core::graph_write`), which normalizes files,
local folders, URLs, browser tabs, and text into this shape.
`lattice_brain/ingestion/` now holds only the shared vocabulary (routing
constants, DTOs, content hashing, and extraction quality scoring); v11.8.0
removed the last leftover `IngestionPipeline` capability-probe class together
with its sole route.

---

## Example (Matches Spec Slide 22)

```json
{
  "node": {
    "id":         "node:01HX7K…",
    "type":       "FILE",
    "label":      "LatticeAI_ProductSpec.pdf",
    "embedding":  [0.014, -0.231, "…", 0.082],
    "attrs":      { "mime": "application/pdf",
                    "pageCount": 24, "lang": "ko" },
    "owner_id":   "user_seoljun",
    "visibility": "private",
    "created_at": "2026-05-20T05:30:00Z"
  },
  "edge": {
    "id":         "edge:01HX7M…",
    "source":     "node:01HX7K…",        // FILE
    "target":     "node:01HX5A…",        // CONCEPT  'MCP'
    "type":       "MENTIONS",
    "weight":     0.82,
    "confidence": 0.91,
    "evidence":   ["chunk:01HX7K…#p3", "chunk:01HX7K…#p11"],
    "created_by": "extractor:llm-gemma-4-12b"
  }
}
```

---

## Migration (Legacy → v2)

Legacy `nodes` / `edges` tables created by `lattice_brain/graph/store.py`
(formerly `knowledge_graph.py`) used free-form string types (including Korean
verb strings). `lattice_brain/graph/schema.py` carries `_LEGACY_EDGE_MAP` and
`_LEGACY_NODE_MAP` to normalize them into canonical enums (see the
[Korean mirror](./ko/kg-schema.md) or `lattice_brain/graph/schema.py` for the
exact legacy Korean verb literals):

| Legacy Edge Family (Korean / Free-Form Verbs) | → v2 `EdgeType` |
|-----------------------------------------------|-----------------|
| Mentions / describes / relates-to verbs | `MENTIONS` |
| Contains verbs | `CONTAINS` |
| Resolves / connects / triggers verbs | `REFERENCES` |
| Depends-on verbs | `DEPENDS_ON` |
| Compares verbs | `SIMILAR_TO` |
| Uses / supports verbs | `USES` |
| Extends verbs | `DERIVED_FROM` |
| Creates / authored verbs | `AUTHORED_BY` |
| Replaces / supersedes verbs | `VERSION_OF` |

| Legacy Node Type (Free-Form String) | → v2 `NodeType` |
|-------------------------------------|-----------------|
| `Code` | `CODE_SYMBOL` |
| `Concept`, `Feature`, `Error`, `Tag` | `CONCEPT` |
| `Person`, `User` | `PERSON` |
| `File`, `Document`, `Page`, `Sheet`, `Slide` | `FILE` / `CHUNK` |
| `Message`, `AIResponse` | `MESSAGE` |
| `Model` | `MODEL` |
| `Tool`, `MCP` | `TOOL` |
| `Project`, `Workspace` | `PROJECT` |

### Execution

Migration runs **automatically on server startup** without a separate CLI: when
`knowledge_graph.KnowledgeGraphStore` opens, it creates/heals the v2 schema
(`lattice_brain.graph.schema.KGStoreV2.init_schema` — additive columns healed
in-place via `ALTER`, `edges_v2` key changes rebuilt via create → copy → swap)
and backfills legacy data into v2. Existing `nodes` / `edges` tables are not
dropped. In format v4, `nodes_v2` / `edges_v2` are the write master while
legacy tables are updated inside the same transaction as a compatibility
projection for older import/API consumers. Databases with pre-existing graph
data write a one-time `backups/*.pre-v2-write-master.*.sqlite` snapshot before
switching and record `PRAGMA user_version=4` and
`kg_meta.db_format_version=4`. Newer unknown DB formats fail closed on open.

---

## Temporal State and Parent Chunks (v14.2.0)

- `chunk_parents(id, source_node, parent_index, text, metadata_json, created_at)`
  is an additive table, created on first parent write; `source_node` cascades on
  delete. Child chunks carry `parent_id` in chunk metadata.
- `SUPERSEDES` edges point from the new version to the old one. Retrieval reads
  `status` (`active` / `superseded`), `valid_from`, `valid_until`, and
  `superseded_by` from node metadata, lifecycle, and inbound edges.
- Watch-folder documents record `document_version` and purge the replaced
  revision's chunks. Note, browser-tab, and upload re-ingest keep older versions
  and mark them superseded.

---

## Embeddings

- Dimension: set by the active embedder (768 for EmbeddingGemma 2, 384 for the
  hash fallback); `LATTICEAI_VECTOR_DIM` overrides it
- Storage: SQLite `BLOB` column packed via `struct.pack('<{n}f', …)`
- Search: defaults to **exact cosine scan (`brute`)**. Since v12.0.0, enabling
  `LATTICEAI_VECTOR_INDEX=hnsw` queries the worker sidecar
  (`POST /worker/vector/query`) for `k * 8` (up to 200) candidate IDs, and the
  native host **rescores those rows with the exact same cosine** (`hnsw+rescore`:
  approximate recall, exact ordering). If the sidecar cannot answer, retrieval
  falls back to exact scan while carrying the reason. The `.hnsw` sidecar uses
  incremental append so a single write never invalidates the whole index.
- Embedder: since v12.0.0, **auto-detected** — adopts a downloaded real
  embedding model when present, or runs the hash/subword fallback explicitly
  labelled `grade='fallback'`. Fallback vectors are never presented as semantic
  vectors. Vector identity is filtered by `(model, dim)` so different embedding
  spaces are never silently mixed.
- Keyword search: since v4, an FTS5 trigram index (`node_fts`) replaces `LIKE`
  scans while preserving Korean substring recall. On SQLite builds without
  FTS5/trigram, the `LIKE` path continues to work and is reported honestly via
  `index_status().storage.fts_enabled`.

---

## Usage (Python)

`KGStoreV2` is **schema, initialization, and statistics only** — older
documented native APIs (`Node`/`Edge` dataclasses, `upsert_node`,
`upsert_edge`, `neighbors`, `search_similar`) were removed and do not exist.
All data reads and writes go through `knowledge_graph.KnowledgeGraphStore`:

```python
from lattice_brain.graph.schema import KGStoreV2, NodeType, EdgeType
from knowledge_graph import KnowledgeGraphStore

store = KnowledgeGraphStore(db_path, blob_dir)

# Write: every ingest path goes through _upsert_node/_upsert_edge,
# normalizing edge types to canonical EdgeType values (free strings blocked).
store.ingest_message("user", "Share project schedule", user_email="me@example.com")

# Read: search (FTS5/LIKE), vector_search, graph, traverse
matches = store.search("project")["matches"]

# v2 statistics (normalized type distribution)
print(KGStoreV2(store.db_path).stats())
```

### v4 Columns (T3b/T3c)

- `nodes_v2.workspace_id` — `NULL` means legacy-global (data predating
  workspace scoping). Since 9.1.0, normal workspace reads exclude it unless
  `include_legacy_global=True` is explicitly passed on a compatibility path.
- `nodes_v2.visibility` — scoped writes use `workspace`/`private`; unscoped
  writes use `legacy` (existing shared data is never silently rewritten to
  `private`).
- `nodes_v2.superseded_by` — revision chain (`mark_superseded`).
- `edge_occurrences` — every observed occurrence of a relation
  (`observed_at`, `weight`, `source`).

If workspace projection lookup fails or a node's scope cannot be verified, the
node is treated as private (returning an empty result or propagating the
error). Fail-open paths that treat projection failures as legacy-global and
expose data across workspaces are strictly forbidden.
