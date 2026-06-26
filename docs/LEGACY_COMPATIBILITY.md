# Legacy Compatibility Map

Current release: **8.1.0 - Intuitive Brain Home**.

Lattice AI is moving toward a smaller, modular architecture centered on
`lattice_brain`, `latticeai.services`, `latticeai.api`, and `latticeai.runtime`.
Some root-level modules remain packaged for compatibility with older imports,
CLI entrypoints, or extension workflows. Their presence does not define the
current 8.1.0 architecture.

## Current Policy

- Keep compatibility shims while public imports or package entrypoints still
  depend on them.
- Prefer moving implementation into focused packages before removing a root
  module.
- Add deprecation notes before removal.
- Avoid breaking package users during a minor release.
- Do not silently remove rollback, backup, restore, or migration paths.

## Root Module Map

| Legacy root module | Current home / direction | Why it remains |
| --- | --- | --- |
| `knowledge_graph.py` | `lattice_brain.graph` / `lattice_brain.knowledge` | Compatibility for older graph imports and historical tooling |
| `knowledge_graph_api.py` | `latticeai.api.memory`, `latticeai.api.search`, graph-related API routers | Compatibility for older API import paths |
| `kg_schema.py` | `lattice_brain` storage/schema modules | Compatibility for graph schema references |
| `auto_setup.py` | setup/model recommendation services | Compatibility for zero-config setup probes and historical auto-setup commands |
| `llm_router.py` | `latticeai.models.router` | Compatibility for older local model routing imports |
| `ltcai_cli.py` | package console entrypoint (`ltcai`) | Compatibility for the installed CLI contract |
| `mcp_registry.py` | `latticeai.core.mcp_registry` and service-backed registries | Compatibility for MCP/skills lookup entrypoints |
| `local_knowledge_api.py` | `lattice_brain.ingestion`, workspace capture APIs | Compatibility for local folder/file watcher flows |
| `p_reinforce.py` | gardener/maintenance service direction | Compatibility for existing Brain gardening runtime hooks |
| `telegram_bot.py` | opt-in integration package or disabled-by-default connector | Compatibility only; Telegram must remain opt-in |
| `setup_wizard.py` | setup and model recommendation services | Compatibility for first-run recommendation calls |
| `server.py` | lazy proxy to `latticeai.server_app` / `latticeai.app_factory` | Preserves historical `server.app` imports without import-time construction |

## Packaging Notes

`pyproject.toml` and `package.json` still include several root modules because
older installed packages may import them directly. That is intentional for now.
The long-term target is:

- move implementation into `lattice_brain`, `latticeai.core`, `latticeai.models`,
  `latticeai.services`, or `latticeai.api`;
- leave thin shims with docstrings/deprecation warnings;
- remove a shim only after tests prove no supported entrypoint relies on it.

## Removal Checklist

Before removing or excluding a legacy module:

1. Search imports in the repository and generated package files.
2. Add or update a compatibility test.
3. Confirm the package still imports from a fresh non-repo working directory.
4. Update `README.md`, `ARCHITECTURE.md`, `FEATURE_STATUS.md`, and this file.
5. Run unit tests, package build, and wheel smoke validation.
