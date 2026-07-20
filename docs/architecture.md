# Lattice AI Architecture

> **Status: historical** — subsystem note preserved for background. The
> canonical current architecture document is
> [`ARCHITECTURE.md`](../ARCHITECTURE.md). This file preserves the v3.6.0
> Knowledge Graph First architecture detail; its version and subsystem claims
> are intentionally frozen at that point in time.

> v3.6.0 — **Knowledge Graph First.** Every data source converges into the graph
> through one unified ingestion pipeline (`latticeai/services/ingestion.py`), with
> formalized entities/relationships (`docs/kg-schema.md`), browser/web inputs,
> per-node provenance, and local export/import/backup
> (`latticeai/services/kg_portability.py`). The agent ecosystem, long-term memory,
> and skills/hooks/tool/MCP registries are all operable from `/app`. Enterprise
> controls remain future work.

Lattice AI is a local-first **Digital Brain Platform**. The architecture is
organized around one durable center and the user's asset: the **Knowledge
Graph**. Models, tools, agents, RAG, workflows, and UI modes are replaceable
layers that operate as views over graph context. Models are replaceable;
knowledge is durable.

## Architecture Goals

- Keep user knowledge local-first by default.
- Treat multimodal input as the normal path, not an add-on.
- Preserve evidence, decisions, files, artifacts, and work history.
- Keep models replaceable and policy-governed.
- Explain risk and source facts instead of hiding capability.
- Keep basic and advanced modes feature-equivalent.
- Keep admin-only capabilities explicit and auditable.

## Workspace View

```mermaid
flowchart TD
    User["User files, screenshots, chats, notes, code, work logs"]
    Ingestion["Multimodal ingestion"]
    Extract["Entity, relation, evidence extraction"]
    Graph["Knowledge Graph"]
    Context["Graph context builder"]
    Models["Local or cloud model workflow"]
    Agents["Multi-agent workflow"]
    Outputs["Coding actions, analysis, documents, team workflows"]
    Admin["Admin policy and audit"]

    User --> Ingestion
    Ingestion --> Extract
    Extract --> Graph
    Graph --> Context
    Context --> Models
    Models --> Agents
    Agents --> Outputs
    Admin --> Models
    Admin --> Graph
```

## Durable Core

The Knowledge Graph stores durable personal and organization workspace context:

- files and document evidence
- images and screenshots
- conversations and notes
- user decisions
- work history
- generated artifacts
- agent and workflow events

The model is not the product core. It is a replaceable participant in the
workspace pipeline.

## Multimodal Ingestion

Lattice AI assumes users will provide source material directly. The expected
input set includes:

- PDF
- Word
- Excel
- PowerPoint
- images
- screenshots
- chat history
- notes
- web content
- code
- work logs

The architecture must not ask users to convert these to plain text before AI can
work on them.

## Local Model Management Policy

Local recommended models must be multimodal. The v2.2 local model workflow policy is:

- macOS Apple Silicon: MLX-VLM first, with MLX-LM retained as a Gemma 4 text
  fallback only for standard Gemma 4 metadata. Gemma 4 12B `gemma4_unified`
  requires an MLX-VLM runtime that includes `mlx_vlm.models.gemma4_unified`.
- Windows: llama.cpp multimodal path, with LM Studio as a user-friendly option
- Linux: llama.cpp or vLLM multimodal path depending on GPU support
- Ollama: kept as an option, not the default priority

The removed path is the old text-only MLX-LM recommendation lane for ordinary
model selection. MLX-LM remains available as a targeted recovery path for
standard Gemma 4 metadata, but it is not used for `gemma4_unified`.
Low-spec machines use smaller or quantized multimodal models.

## Model Source Disclosure

Model catalog entries carry source disclosure fields:

1. `source_country`
2. `source_company`
3. `execution_method`
4. `internet_requirement`
5. `model_name`

These are first-class model facts, not advanced-only metadata.

## Recommendation Flow

```text
hardware scan
  -> CPU/GPU/RAM/disk/OS analysis
  -> multimodal model shortlist
  -> same-family old generation removal
  -> source disclosure
  -> recommendation reason
  -> download/install/load/verify
```

The current default recommendation family is Gemma 4. Qwen3-VL and Llama 4
remain current multimodal alternatives.

## Modes

Basic mode and advanced mode have the same feature access.

- Basic mode uses plain language and source facts.
- Advanced mode adds execution, memory, quantization, and load/unload detail.
- Admin mode adds actual authority: user management, permissions, audit logs,
  organization policy, security policy, sensitive-data monitoring, model approval
  policy, and Private VPC.

## Main Modules

| Module | Responsibility |
| --- | --- |
| `latticeai/services/model_catalog.py` | Multimodal model catalog, source metadata, aliases |
| `latticeai/services/model_recommendation.py` | Hardware-aware multimodal recommendation |
| `latticeai/services/model_runtime.py` | Download, load, server, and model workflow orchestration |
| `llm_router.py` | MLX-VLM/MLX-LM and OpenAI-compatible model routing |
| `knowledge_graph.py` | Graph storage, extraction, local folder knowledge graph context |
| `latticeai/core/context_builder.py` | Graph context for generation |
| `latticeai/core/workspace_os.py` | Workspace state, timeline, snapshots, durable context |
| `latticeai/core/multi_agent.py` | Planner/executor/reviewer/researcher orchestration |
| `latticeai/core/workflow_engine.py` | Workflow definitions and run history |
| `latticeai/core/plugins.py` | Plugin manifest, registry, permission boundary |
| `latticeai/core/security.py` | Local security primitives |

## Compatibility

v2.2.1 preserves the additive workspace and API compatibility posture from
v2.x. Existing graph/workspace data is migrated non-destructively. The release
does remove current recommendation entries for old or text-only model paths, but
it does not destructively mutate existing user graph data.
