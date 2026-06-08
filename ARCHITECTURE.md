# Lattice AI Architecture

Lattice AI v3.3.1 is a feature-complete (non-enterprise), local-first AI
workspace platform. The durable core is the Knowledge Graph; retrieval, memory,
and the agent ecosystem operate on graph and workspace context. The entire
platform is operable from `/app`.

See [docs/architecture.md](docs/architecture.md) for the full architecture.

## v3.3 Platform Shape

```text
files / images / documents / chats / work history
  -> multimodal ingestion
  -> entity, relation, and evidence extraction
  -> Knowledge Graph  +  Vector Index  ->  Hybrid Search
  -> Long-Term Memory (workspace / project / agent / conversation / graph / vector)
  -> Agent Runtime (Planner -> Researcher -> Executor -> Reviewer)
       via Agent Registry, Tool Registry, Hooks, MCP, Skills, Marketplace templates
  -> Workflow Agents + Autonomous Planning (goal -> plan -> execute -> review -> replan)
  -> coding actions, analysis, documents, team workflows
```

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
