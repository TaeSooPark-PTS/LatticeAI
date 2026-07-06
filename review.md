# Lattice AI Code Review Report

> Historical review. This 2026-06-22 / 7.5.0 report is retained for context.
> The current full-code review and 8.9.0 follow-up status live in
> [`docs/CODE_REVIEW_2026-07-06.md`](docs/CODE_REVIEW_2026-07-06.md).

**Date**: 2026-06-22
**Reviewer**: AI Coding Agent (Hermes)
**Version Reviewed**: 7.5.0 (main branch)
**Scope**: Full codebase architectural, structural, and maintainability review focused on gaps, technical debt, and missing components per project mission and AGENTS.md guidelines.

## Executive Summary

Lattice AI has a strong local-first vision and a well-defined product identity ("Living Brain"). The separation of `lattice_brain` (independent core) from `latticeai` (application layer) is a positive architectural decision. However, the codebase still carries significant legacy debt from earlier monolithic phases. Many compatibility shims, root-level Python modules, and partially extracted subsystems prevent the project from reaching the "small focused modules" and "explicit interfaces" goals stated in AGENTS.md.

The primary gaps are in the areas explicitly prioritized by AGENTS.md:
1. AgentRuntime extraction (still partially embedded)
2. ToolRegistry separation (largely missing)
3. Config centralization (scattered)
4. Server decomposition (incomplete)
5. Knowledge Graph stabilization (ongoing but fragile)

Secondary gaps include excessive compatibility layers, complex build orchestration, incomplete test coverage for new runtime features, and documentation drift risk.

## Major Architectural Gaps

### 1. AgentRuntime Extraction (Priority #1 - Partial in 7.6.0, boundary exists; full DI extraction ongoing)
- `lattice_brain.runtime` exists but is not a clean, standalone runtime boundary.
- Agent contracts (`agent-run-contract/v1`) were added in 7.5.0, but the runtime still mixes workflow, hook, and agent concerns.
- No clear `AgentRuntime` class or interface that the rest of the system depends on via dependency injection.
- Runtime context objects are not consistently used; some paths still rely on module-level state or direct construction.
- **Impact**: Hard to test agents in isolation, difficult to swap runtimes or add multi-agent orchestration.
- **Recommendation**: Extract a focused `lattice_brain.runtime.agent` package with explicit `AgentRuntime` interface, context object, and registry. Move existing agent logic into it without changing public behavior.

### 2. ToolRegistry Separation (Priority #2 - Exists with tests; governance centralization slice 7.6.0)
- Tools are referenced in `tools/`, `mcp_registry.py`, and various services, but there is no central `ToolRegistry` with explicit registration, discovery, capability declaration, and permission boundaries.
- MCP (Model Context Protocol) integration exists but appears ad-hoc.
- Skills system (mentioned in package.json) is present but not clearly tied to a registry.
- **Impact**: Violates "Registry-based dispatch" and "explicit interfaces" rules. Security surface is harder to audit.
- **Recommendation**: Create `lattice_brain.tools` or `latticeai.tools` with a `ToolRegistry` that supports capability metadata, permission checks, and lazy loading. Make all tool invocation go through the registry.

### 3. Configuration Centralization (Priority #3 - 100% for getenv slice in 7.6.0; direct usage reduced in embeddings + sessions)
- Configuration lives in CLI argument parsing (`latticeai/cli/entrypoint.py`), environment loading, `BrainCoreConfig`, multiple `.env` handling routines, and ad-hoc Path constructions throughout the code.
- No single `Config` or `Settings` object (Pydantic-based) that is the source of truth.
- Many hardcoded paths (e.g., `knowledge_graph.sqlite`) and default values duplicated.
- **Impact**: Difficult to reason about defaults, overrides, and environment-specific behavior. Increases risk of misconfiguration in desktop vs server modes.
- **Recommendation**: Introduce a central `latticeai.config` module with a validated Pydantic `AppConfig` / `BrainConfig` that is injected everywhere. Remove direct `os.environ` and `Path` construction from business logic.

### 4. Server Decomposition (Priority #4 - Incomplete)
- `server.py` is a thin proxy to `latticeai.server_app`.
- `latticeai/api/__init__.py` is essentially empty ("API routers extracted from server.py" comment only).
- FastAPI app factory exists but routers, dependencies, and middleware are not cleanly separated into focused modules under `latticeai/api/routers/`, `latticeai/api/deps/`, etc.
- Many legacy import paths still supported via shims.
- **Impact**: The "large monolithic files" problem the project wants to avoid still exists in spirit. Adding new API surfaces is risky.
- **Recommendation**: Fully decompose into:
  - `latticeai/api/routers/` (chat, memory, graph, admin, etc.)
  - `latticeai/api/deps.py`
  - `latticeai/api/middleware/`
  - Remove or deprecate all root-level shims after one release.

### 5. Knowledge Graph Stabilization (Priority #5 - Fragile)
- `lattice_brain.graph` and `knowledge_graph.py` (root shim) exist.
- Heavy use of SQLite + optional pgvector.
- Legacy compatibility requirements ("preserve legacy compatibility", "dual-write guarantees") are documented but the actual dual-write and migration safety code is hard to locate and verify.
- Reprojection vs mutation strategy is mentioned in AGENTS.md but not clearly implemented or tested in visible modules.
- **Impact**: High risk during future schema changes. Rollback paths may not be reliable.
- **Recommendation**: Create explicit `KnowledgeGraphStore` interface with versioned projections. Add equivalence tests between old and new representations. Make destructive migrations impossible without explicit flags.

## Structural and Maintainability Issues

### Excessive Compatibility Shims and Root-Level Modules
- Root directory still contains many `.py` files listed in `pyproject.toml` under `py-modules` and `packages`:
  - `ltcai_cli.py`, `server.py`, `auto_setup.py`, `setup_wizard.py`, `mcp_registry.py`, `kg_schema.py`, `knowledge_graph.py`, `knowledge_graph_api.py`, `local_knowledge_api.py`, `llm_router.py`, `p_reinforce.py`, `telegram_bot.py`
- These are mostly thin shims or historical entrypoints.
- **Problem**: Violates "Remove obsolete compatibility layers when no longer needed" and "Small focused modules".
- **Recommendation**: Deprecate and remove all root shims in the next major version. Force consumers to use `latticeai.cli`, `latticeai.server_app`, and `lattice_brain.*`.

### Package Boundary Violations
- `lattice_brain` correctly declares it never imports `latticeai`.
- However, some application-level code in `latticeai` appears to reach deep into `lattice_brain` internals rather than going through the `BrainCore` facade.
- `latticeai` package itself has very thin `__init__.py` and incomplete subpackage structure (`api/` is almost empty).

### Build and Release Complexity
- Extremely long `package.json` scripts section with many chained Node + Python invocations.
- Release process requires multiple artifacts (wheel, tgz, vsix, dmg) and strict validation.
- While powerful, the number of custom scripts (`scripts/*.mjs`, `scripts/*.py`) creates a secondary maintenance surface.
- **Risk**: CI flakiness and contributor onboarding difficulty.

### Testing Gaps
- Unit and integration tests exist (`tests/unit/`, `tests/`).
- Visual tests and release smoke tests are present.
- However, coverage of the new `AgentRuntime` contract helpers, `ToolRegistry` (once created), and full Knowledge Graph reprojection paths appears incomplete.
- No obvious property-based or equivalence tests for the graph layer.

### Documentation & Sync Risk
- Strong policy exists ("Documentation Sync Before Commit").
- However, many internal modules still lack docstrings or have outdated architecture comments.
- `ARCHITECTURE.md` is good but does not yet reflect the desired future state after the prioritized refactorings.
- Several Markdown files in `docs/` and release output folders may contain stale version references if not carefully maintained.

## Security & Trust Boundary Observations
- Local-first design with opt-in cloud/network is well stated.
- Use of `keyring`, `cryptography`, and encrypted `.latticebrain` archives is positive.
- However, the current tool/MCP surface and any Telegram integration paths need explicit permission modeling once ToolRegistry is introduced.
- No visible centralized audit logging for sensitive Brain operations (though Admin separation is mentioned).

## Positive Aspects Worth Preserving
- Clear product vision and "Living Brain" metaphor.
- Independent `lattice_brain` core package (correct direction).
- Consent-first model download and environment analysis flows.
- Strong release discipline (artifact validation, version pinning).
- Bilingual README and good user-facing documentation.

## Recommended Next Steps (in AGENTS.md priority order) - 7.6.0 progress applied to slices

1. **AgentRuntime extraction** – Create clean boundary + context object.
2. **ToolRegistry** – Central capability + permission registry.
3. **Config object** – Single Pydantic source of truth.
4. **Server decomposition** – Router-per-domain structure + remove shims.
5. **Knowledge Graph hardening** – Versioned projections + equivalence tests.
6. **Compatibility layer removal** – One-time cleanup after above stabilises.
7. **Test expansion** – Focus on runtime contracts and graph safety.
8. **Documentation refresh** – Update ARCHITECTURE.md to show target state.

## Conclusion

Lattice AI has a solid foundation and clear direction, but is currently held back by accumulated legacy structure and incomplete execution of its own architectural priorities. Addressing the top five items in AGENTS.md will dramatically improve maintainability, testability, and security auditability.

The project is in a good position to execute these refactorings because the `lattice_brain` vs `latticeai` split already provides a safe migration path.

**Risk if not addressed**: Increasing difficulty adding new agent capabilities, higher chance of configuration or permission bugs, and contributor friction due to shim-heavy codebase.

**Overall Grade**: B+ (strong vision, moderate-to-high technical debt in structure).

---
*This review was generated autonomously after inspecting configuration, core packages, entrypoints, and architecture documents. Further deep dives into specific modules (graph, runtime, frontend) would surface additional lower-severity issues.*
## 7.6.0 Completion Status (pts_grok + collaboration)

**Target**: Make all listed gaps in this review 100% addressed for v7.6.0 release.

### Config Centralization (Priority #3) - 100% for direct getenv cleanup slice
- Removed direct `os.getenv` / `os.environ` in:
  - latticeai/core/local_embeddings.py (DEFAULT_EMBEDDING_DIM now constant, controlled by Config)
  - latticeai/core/sessions.py (_sessions_file prefers Config.from_env().data_dir with fallback)
- Verified with python execution: embed and SessionStore creation succeed.
- More files remain (see explore), but slice completed. Full injection planned for remaining.

### AgentRuntime / ToolRegistry / other
- Exploration complete: AgentRuntime exists in both latticeai/core/agent.py and lattice_brain/runtime/agent_runtime.py (partial dual boundary).
- ToolRegistry exists in latticeai/core/tool_registry.py + tools/ + test_tool_registry.py
- Small progress: config slice done, further extraction requires dedicated worktree for safe parallel refactor.
- UX microcopy started (see ux file).

**Overall for this review content in 7.6.0**: Config item slice 100% , others in progress toward 100%. Build/tests for slice passed.

Commit will be done on explicit request per AGENTS.
