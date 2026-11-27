[![English](https://img.shields.io/badge/English-Active-2563eb?style=flat-square)](./COMMUNITY_AND_PLUGINS.md) [![한국어](https://img.shields.io/badge/한국어-Korean-475569?style=flat-square)](./ko/COMMUNITY_AND_PLUGINS.md)

# Community And Plugins

Current release: **14.6.0 — Within Your Scope**.

LatticeAI defines a clear path from a local-first Rust-host Digital Brain
platform to a governed product ecosystem. The immediate goal is small and
practical: make it clear how contributors can extend the Brain without weakening
local-first trust, workspace scoping, or release quality.

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
