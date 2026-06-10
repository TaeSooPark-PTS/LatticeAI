# Lattice AI Architecture

Lattice AI is a local-first **Digital Brain Platform**. The durable core — and the
user's asset — is the **Knowledge Graph**: every data source converges into it,
and retrieval, memory, and the agent ecosystem operate as views over it. Models,
agents, RAG, and the UI are replaceable implementations; the graph is durable.
The entire platform is operable from `/app` and runs on local SQLite.

See [docs/architecture.md](docs/architecture.md) for the full architecture and
[docs/kg-schema.md](docs/kg-schema.md) for the entity/relationship model.

## Knowledge Graph First shape (v3.6.0)

Every source flows through **one unified ingestion pipeline** into the graph — no
source bypasses it, none becomes a silo:

```text
source (file · folder · PDF · web URL · browser tab · text/markdown/code)
  -> extraction -> normalization -> metadata -> content hash (idempotent)
  -> chunking -> entity detection -> relationship detection -> embedding
  -> Knowledge Graph   (Source -[indexed_from]- content -[contains]- chunks)
       + provenance (where / when / how / embedded / linked) per node
  -> Vector Index -> Hybrid Search
  -> Long-Term Memory (workspace / project / agent / conversation / graph / vector)
  -> Agent Runtime (Planner -> Researcher -> Executor -> Reviewer)
       via Agent Registry, Tool Registry, Hooks, MCP, Skills, Marketplace templates
  -> Workflow Agents + Autonomous Planning (goal -> plan -> execute -> review -> replan)
  -> coding actions, analysis, documents, team workflows
```

- **Unified ingestion** — `latticeai/services/ingestion.py` is the single
  write-side seam; every source is normalized into one `IngestionItem` and
  bracketed by the `pre_tool`/`post_tool` hook lifecycle (`dispatch_tool`).
- **Provenance** — `ingestion_provenance` makes every node explainable.
- **Portability** — `latticeai/services/kg_portability.py` exports/imports the
  graph (versioned JSON) and takes binary backups (DB + blobs); local-only.
- **Browser/web inputs** — `latticeai/api/browser.py` + a Manifest V3 extension
  feed URLs and tabs into the same pipeline, posting only to `127.0.0.1`.

## v3.3 Platform Components

- **Core** — Workspace OS, Knowledge Graph, Vector Index, Hybrid Search,
  Long-Term Memory + Memory Manager.
- **Agents** — Agent Runtime (Planner / Researcher / Executor / Reviewer),
  Agent Registry, Marketplace + Templates, Workflow Agents, Autonomous Planning.
- **Extensibility** — Skills registry, Hooks registry, Tool Registry, MCP
  Manager.
- **Surfaces** — a single token-native `/app` SPA over a FastAPI router layer;
  every surface reports live or unavailable state honestly (no fabricated data).
- **Enterprise** — SSO, SCIM, RBAC, compliance, DLP, private VPC, governance,
  and multi-tenant controls remain future work.

## Current Model Workflow Policy

- Local recommendations are multimodal-only.
- Gemma 4 is the default recommendation family.
- Qwen3-VL and Llama 4 remain current multimodal alternatives.
- MLX-VLM is the Apple Silicon local execution path.
- llama.cpp and vLLM cover cross-platform multimodal execution paths.
- Ollama remains a selectable option, not the default priority.
- Old same-family generations and text-only local fallback models are removed
  from current recommendation catalogs.

## Source Disclosure

Every recommended model must expose:

1. 제작 국가
2. 제작 회사
3. 실행 방식
4. 인터넷 사용 여부
5. 모델명

Basic mode and advanced mode show the same capabilities. Admin mode is the only
mode with additional authority.
