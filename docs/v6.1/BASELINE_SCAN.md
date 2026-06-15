# v6.1.0 Product Hardening — Baseline Scan

**Date**: 2026-06-15
**Branch**: feat/v6.1.0-product-hardening
**Current Version**: 6.0.0 (pyproject.toml)
**Target**: 6.1.0

## Purpose
Establish immutable baseline before autonomous v6.1 product hardening loop. Captures architecture, dependency boundaries, and known debt per AGENTS.md.

## Package Structure
- Name: ltcai (Lattice AI)
- Runtime: Python 3.11+
- Key directories:
  - docs/v6.1/ (new)
  - tests/unit/
  - .ltcai-brain/ (knowledge base)
- No src/ layout (flat or installed editable expected)

## Architecture Snapshot (from AGENTS.md + existing docs)
- Primary goals remain: Local LLM first, Knowledge Graph workflows, AgentRuntime, ToolRegistry, Personal/Org Workspace, Security-first.
- Preferred refactoring order:
  1. AgentRuntime extraction (highest priority)
  2. ToolRegistry separation
  3. Config centralization
  4. Server decomposition
  5. Knowledge Graph stabilization
- Current state: v6.0.0 released, focus now on hardening boundaries and test coverage.

## Dependency Guard Targets
- `lattice_brain` (or equivalent brain core) must NOT import `latticeai` / `ltcai` top-level to prevent circular deps and maintain extraction readiness.
- AST-based import guard test required for CI.

## Open Items from AGENTS.md
- No global mutable state
- Preserve legacy KG compatibility
- Documentation sync mandatory before commits affecting version/release

## Next Autonomous Steps (self-selected)
1. BASELINE_SCAN.md (this file)
2. Add AST guard test for lattice_brain isolation
3. Execute targeted test
4. Commit & push
5. Select next micro-patch (likely AgentRuntime boundary or test expansion)

## Verification Criteria for v6.1
- All new tests green
- No import violations
- Docs updated in same commit
- Build + lint + typecheck pass

This baseline is frozen. Any deviation requires explicit correction by pts_openclaw.
