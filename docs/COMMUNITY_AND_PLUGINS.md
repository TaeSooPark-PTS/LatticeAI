# Community And Plugins

Current release: **11.0.1 — Both Branches**.

LatticeAI defines the path from a strong local-first framework (8.4.0
action-aware baseline, 8.5.0 registry+DI hardening, 8.6.0 capture/navigation
reliability, 8.7.0 runtime-state hygiene, 8.8.0 Brain Core extraction
readiness, 8.9.0 scoped Tool API hardening, 9.0.0 cleanup closure, 9.1.0
fail-closed review completion, 9.2.0 model-agnostic file generation, 9.3.0
proactive Brain intelligence, 9.4.0 question-driven everyday automation, 9.5.0 Command Center, and 9.6.0 Trusted Agent Loop)
to a product ecosystem. The
immediate goal is small and practical: make it clear how
contributors can extend the Brain without weakening local-first trust,
workspace scoping, or release quality.

## Community Entry Points

- Start with the product docs: `README.md`, `docs/WHY_LATTICE.md`, and
  `docs/ONBOARDING.md`.
- Use `docs/DEVELOPMENT.md` for local setup, validation, and release gates.
- Use `docs/PLUGIN_SDK.md` for plugin manifests, compatibility, permissions,
  lifecycle state, catalog endpoints, and install flows.
- Use `plugins/hello-world/plugin.json` as the minimum working plugin shape.

## Plugin Direction

Plugins should add one bounded capability at a time:

- new ingestion sources that still route through `IngestionPipeline`;
- graph-aware tools that preserve workspace scoping;
- workflow templates that declare permissions explicitly;
- editor or automation helpers that keep secrets out of logs and audit payloads;
- local-first integrations that require explicit opt-in before network access.

## Guardrails

- No plugin should bypass the ToolRegistry, permission model, or audit hooks.
- No plugin should write graph data without provenance.
- No plugin should assume a cloud model, remote registry, or network connection.
- Compatibility requirements belong in `lattice_version`, and examples should
  use current host-compatible values.

## Current Ecosystem Tasks

- Keep the hello-world plugin valid and boring.
- Expand examples only when they exercise real extension seams.
- Add tests before treating a plugin API as stable.
- Prefer documentation that helps a first contributor build one useful plugin
  over broad ecosystem promises.
