# Lattice AI Architecture

Lattice AI v2.2.1 is a local-first AI workspace for knowledge graphs, AI
pipelines, local model management, and multi-agent workflows. The durable core
is the Knowledge Graph; model workflows operate on graph and workspace context.

See [docs/architecture.md](docs/architecture.md) for the full architecture.

## v2.2.1 Shape

```text
files / images / documents / chats / work history
  -> multimodal ingestion
  -> entity, relation, and evidence extraction
  -> Knowledge Graph
  -> graph context
  -> local or cloud model workflow
  -> AI pipeline output
  -> coding actions, analysis, documents, team workflows
```

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
