# v6.1.0 Product Hardening Baseline Scan

Date: 2026-06-16
Branch: `feat/v6.1.0-product-hardening`
Current committed release line: `6.0.0`
Target release line: `6.1.0`

This scan is evidence for the v6.1.0 hardening work before broad implementation.
It is intentionally descriptive. Version metadata should stay at `6.0.0` until
the final release sync step.

## Version References

Current release metadata still points at `6.0.0` and must be synchronized late in
the release flow:

- `pyproject.toml:7` - Python package version.
- `package.json:3` - root npm package version.
- `package-lock.json:3` and `package-lock.json:9` - lockfile package versions.
- `latticeai/__init__.py:3` - runtime package `__version__`.
- `lattice_brain/__init__.py:29` - Brain Core package `__version__`.
- `lattice_brain/runtime/multi_agent.py:22` - multi-agent runtime version
  constant.
- `src-tauri/tauri.conf.json:4` - desktop app version.
- `src-tauri/Cargo.toml:3` - Tauri crate version.
- `src-tauri/Cargo.lock:147`, `:559`, `:1657`, `:2501` - locked dependency and
  app version references.
- `vscode-extension/package.json:5` - VS Code extension version.
- `vscode-extension/package-lock.json:3` and `:9` - extension lockfile package
  versions.

Current-release docs and evidence paths still point at `6.0.0`:

- `README.md:72`, `:79`, `:86`, `:93`, `:100`, `:107`, `:123`, `:126` -
  screenshot/GIF/index paths under `output/release/v6.0.0/`.
- `README.md:203`, `:221`, `:223-229` - current development target and exact
  artifact names.
- `RELEASE.md:10-27` - release title, notes, screenshot path, and exact artifact
  names.
- `SECURITY.md:53`, `:61` - current release security positioning.
- `vscode-extension/README.md:15` - extension release summary.
- `docs/CHANGELOG.md:6`, `:33`, `:35` - historical v6.0.0 entry. These should
  remain historical while a new v6.1.0 entry is added.
- `docs/v6/*` - v6.0.0 historical planning, architecture, UX, scorecard, and
  instruction records. These are historical and should not be rewritten as
  current-release docs.

## Brain Core Boundary

Requirement: `lattice_brain` must not import `latticeai` or `ltcai`.

Guard status:

- `tests/unit/test_import_guard.py` parses all `lattice_brain/**/*.py` files with
  AST and only checks real `import` / `from ... import` nodes.
- `node scripts/run_python.mjs -m pytest tests/unit/test_import_guard.py -q`
  passes with 2 tests.

String references that are not real imports and are therefore allowed baseline
metadata/doc/comment references:

- `lattice_brain/workflow.py:12` - docstring reference to
  `latticeai.core.agent.AgentRuntime`.
- `lattice_brain/__init__.py:8` - package docstring states the no-import
  boundary.
- `lattice_brain/runtime/__init__.py:46-51` - documentation of current binding
  path through `latticeai`.
- `lattice_brain/runtime/agent_runtime.py:11-12` - docstring references current
  FastAPI/platform integration classes.
- `lattice_brain/runtime/hooks.py:206`, `:272`, `:299`, `:308` - hook metadata
  binding labels.
- `lattice_brain/portability.py:34`, `:36` - archive format identifiers
  `latticeai.kg.export` and `latticeai.kg.backup`.
- `lattice_brain/graph/curator.py:233` - product-name stopword normalization.

## Local-First Trust Gates

The trust boundary already has several explicit guard locations:

- `PRIVACY.md:29` states that token presence alone must not make data leave the
  machine.
- `PRIVACY.md:35-40` lists external surfaces requiring user action: cloud
  models, model downloads, Telegram, Brain Network, and PostgreSQL/Docker.
- `SECURITY.md:77-80` documents SQLite default storage, Docker/Postgres opt-in,
  and disabled automatic chat file reads.
- `SECURITY.md:107-112` documents secret/token redaction.
- `SECURITY.md:122-123` states Telegram, Brain Network, Docker setup, model
  downloads, cloud calls, marketplace refreshes, and update checks are explicit
  opt-in paths.
- `latticeai/services/model_runtime.py:78-95` implements model download gating
  with `_download_allowed()` and `_download_block()`.
- `latticeai/services/model_runtime.py:1386-1431` gates non-streaming model
  prepare/download paths.
- `latticeai/services/model_runtime.py:1518-1630` gates streaming model
  prepare/download paths.
- `tests/unit/test_config.py:33-47` verifies external tokens do not enable
  startup egress.
- `tests/unit/test_v42_brain_storage.py:88-97` verifies Docker/Postgres does not
  start without consent.
- `tests/integration/test_v42_postgres_migration_live.py:26` marks live
  Docker/Postgres validation as requiring explicit consent.
- `tests/integration/test_v42_postgres_migration_live.py:113-114` starts the
  Docker wizard only with `consent=True`.
- `tests/unit/test_v43_cli_privacy.py:9-33` covers Telegram token and tunnel env
  presence not starting external services.
- `tests/unit/test_v51_trust_gates.py:139` covers disabled automatic local file
  reads.

## Root Legacy Modules

Root modules remain for compatibility. v6.1 should reduce any non-shim root
logic when safe and ensure new code imports package modules directly.

- `server.py:1-8` is a thin lazy compatibility entrypoint for
  `latticeai.server_app`.
- `knowledge_graph.py:1-4` is a compatibility shim for `lattice_brain` store
  surfaces.
- `kg_schema.py:1-3` is a compatibility shim for `lattice_brain.schema`.
- `mcp_registry.py:1-10` is a deprecation shim for
  `latticeai.core.mcp_registry`.
- `telegram_bot.py:1-80` is not a shim; it contains root-level Telegram runtime
  implementation and environment loading. It is a high-value follow-up cleanup
  candidate.
- `ltcai_cli.py:1-80` is not a shim; it contains root-level CLI implementation
  and startup/environment helpers. It is a high-value follow-up cleanup
  candidate.

## First Patch Candidates

1. Keep the Brain Core AST import guard in CI and expand it only if new Brain
   Core directories are introduced.
2. Harden backend trust-gate test coverage around model download consent and
   token-only startup egress.
3. Move root `telegram_bot.py` or `ltcai_cli.py` logic behind package modules
   while preserving root compatibility shims.
4. Continue UX hardening so first-run can open the Brain without requiring model
   download.
