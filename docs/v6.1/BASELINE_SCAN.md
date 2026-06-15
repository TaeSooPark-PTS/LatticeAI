# v6.1.0 Product Hardening — Baseline Scan

**Date**: 2026-06-15
**Branch**: feat/v6.1.0-product-hardening
**Current Version**: 6.0.0 (pyproject.toml:7, package.json:3)
**Target**: 6.1.0

## Purpose
Establish immutable baseline before autonomous v6.1 product hardening loop. Captures architecture, dependency boundaries, and known debt per AGENTS.md:1-50.

## Package Structure
- Name: ltcai (Lattice AI)
- Runtime: Python 3.11+ (pyproject.toml:10)
- Key directories:
  - docs/v6.1/ (new, INSTRUCTIONS.md:1)
  - tests/unit/ (test_import_guard.py:1)
  - lattice_brain/ (target for isolation, test_import_guard.py:32)
- No src/ layout (flat or installed editable expected)

## Architecture Snapshot (from AGENTS.md + existing docs)
- Primary goals remain: Local LLM first, Knowledge Graph workflows, AgentRuntime, ToolRegistry, Personal/Org Workspace, Security-first. (AGENTS.md:3-9)
- Preferred refactoring order:
  1. AgentRuntime extraction (highest priority) (AGENTS.md:22)
  2. ToolRegistry separation (AGENTS.md:23)
  3. Config centralization (AGENTS.md:24)
  4. Server decomposition (AGENTS.md:25)
  5. Knowledge Graph stabilization (AGENTS.md:26)
- Current state: v6.0.0 released, focus now on hardening boundaries and test coverage. (BASELINE_SCAN.md:5)

## Dependency Guard Targets
- `lattice_brain` (or equivalent brain core) must NOT import `latticeai` / `ltcai` top-level to prevent circular deps and maintain extraction readiness. (test_import_guard.py:3,31)
- AST-based import guard test required for CI. (test_import_guard.py:13-27)

## Open Items from AGENTS.md
- No global mutable state (AGENTS.md:35)
- Preserve legacy KG compatibility (AGENTS.md:36)
- Documentation sync mandatory before commits affecting version/release (AGENTS.md:37-50)

## Next Autonomous Steps (self-selected)
1. BASELINE_SCAN.md (this file) (BASELINE_SCAN.md:40)
2. Add AST guard test for lattice_brain isolation (test_import_guard.py:30)
3. Execute targeted test
4. Commit & push
5. Select next micro-patch (likely AgentRuntime boundary or test expansion)

## Verification Criteria for v6.1
- All new tests green
- No import violations
- Docs updated in same commit
- Build + lint + typecheck pass

This baseline is frozen. Any deviation requires explicit correction by pts_openclaw.
