# Project Principles

Lattice AI is a local-first AI workspace for knowledge graphs, AI pipelines,
local model management, and multi-agent workflows. It should help users build
and use knowledge without forcing them to study AI infrastructure before they
can get work done.

## User Agency

- Explain clearly before asking the user to choose.
- Do not hide capability in the name of protection.
- Do not hide source, risk, or limitation details.
- Make the safe path clear, but leave the final decision to the user.

## Product Shape

- Knowledge Graph first.
- AI pipelines first.
- Multi-agent workflows first.
- Multimodal input first.
- Local-first by default.
- Models are replaceable.
- Files, evidence, decisions, and generated artifacts are durable workspace
  knowledge.

## Mode Policy

- Basic mode and advanced mode have the same features.
- Basic mode uses plain language.
- Advanced mode shows deeper execution details.
- Admin mode is the only mode with extra authority.

## Engineering Policy

- Prefer explicit interfaces and dependency injection.
- Keep model catalogs and model workflow policy centralized.
- Avoid hidden global state and silent fallbacks.
- Preserve graph data and migration safety.
- Add tests for product-policy behavior, not just implementation details.
