# Lattice AI v3.2.0 Release Claim Audit

Audit date: 2026-06-08
Target version: 3.2.0
Scope: code, API routes, frontend views, adapters, tests, runtime/browser behavior, artifacts, tag/release metadata.

## Verdict

Final source verdict: PASS after release-hardening fixes.

The audit found two UI wiring gaps and one release-route hygiene issue:

- `/app#/agents` showed AgentRuntime state but did not expose the Agent Registry controls and capability index from `/agents/api/registry*`.
- `/app#/skills` called the live skill registry but only normalized a legacy `{ skills: [...] }` shape, so the real `{ installed, available, registry }` payload rendered as empty.
- `server_app` registered the MCP router twice because `create_tools_router` already mounted the MCP/skills/plugin-directory routes.

All three were fixed and covered by tests. No product scope was added.

## PASS / PARTIAL / FAIL Matrix

| # | Claim | Status | Evidence |
|---|---|---|---|
| 1 | Multi-Agent Collaboration | PASS | `latticeai/core/multi_agent.py`, `latticeai/services/agent_runtime.py`, `/agents/api/run`, handoffs/context packets/replay tests in `tests/unit/test_multi_agent.py` and `tests/unit/test_agent_platform_maturity.py`. |
| 2 | Agent Registry | PASS | `latticeai/core/agent_registry.py`, `latticeai/api/agent_registry.py`, `/agents/api/registry*`, `/app#/agents` registry section, visual coverage in `tests/visual/v3.spec.js`. |
| 3 | Agent Marketplace | PASS | `latticeai/core/marketplace.py`, `latticeai/api/marketplace.py`, `/marketplace/templates`, `/app#/marketplace`. Offline local catalog; no cloud marketplace is claimed. |
| 4 | Agent Templates | PASS | Five named agent templates: Research Assistant, Coding Assistant, Knowledge Curator, Documentation Writer, Workflow Builder; clone/export/import/install covered in `tests/unit/test_v32_platform.py`. |
| 5 | Workflow Agents | PASS | `latticeai/core/workflow_engine.py`, `latticeai/api/workflow_designer.py`, `PlatformRuntime.build_workflow_runners`, `/app#/workflows`, workflow run/replay tests. |
| 6 | Autonomous Planning | PASS | Planner/executor/reviewer flow via `/agents/api/run`, bounded retry/replan history in `MultiAgentOrchestrator`, `/app#/planning`, retry tests. |
| 7 | Long-Term Memory Platform | PASS | `latticeai/services/memory_service.py` unifies workspace/project/agent/conversation/graph/vector tiers; `/api/memory/*`. |
| 8 | Memory Manager | PASS | Usage/sources/health plus recall/inspect/prune/compact/rebuild/clear in `MemoryService` and `/app#/memory`. |
| 9 | Skills Registry | PASS | `WorkspaceOSStore.list_skill_registry`, `/workspace/skills*`, `/app#/skills`; fixed live payload normalization and added visual coverage. |
| 10 | Hooks Registry | PASS | `latticeai/core/hooks.py`, `latticeai/api/hooks.py`, `/api/hooks*`, `/app#/hooks`, unit tests for toggle/register/inspect/remove. |
| 11 | Tool Registry | PASS | `latticeai/core/tool_registry.py`, `latticeai/services/tool_dispatch.py`, `/tools/permissions`, `/mcp/tools`, `/app#/tools`, route duplicate guard. |
| 12 | MCP Foundation / MCP Manager | PASS | `latticeai/api/mcp.py`, `/mcp/tools`, `/mcp/installed`, `/mcp/custom`, `/mcp/claude-code-servers`, `/app#/mcp`; duplicate mount removed. |
| 13 | Retrieval & Memory Integration | PASS | `latticeai/api/search.py`, `latticeai/services/search_service.py`, `MemoryService.recall`, `/api/search/hybrid`, `/api/index/status`, `/api/graph`, `/api/memory/recall`. |
| 14 | UX Completion Pass | PASS | `/app` routes render without Classic dependency, console errors, app 404/500 responses, or horizontal overflow in real browser validation. Light/dark toggle validated. |
| 15 | Documentation refresh | PASS | README, release notes/changelogs, release guide, and this audit document updated for the hardening pass. |
| 16 | Architecture refresh | PASS | Architecture docs already describe the v3.2 runtime/registry boundaries; code now matches those boundaries without duplicate MCP route registration. |
| 17 | Version 3.2.0 consistency | PASS | `package.json`, `pyproject.toml`, `vscode-extension/package.json`, runtime constants, and expected artifacts remain at 3.2.0. |
| 18 | Tests | PASS | Lint/typecheck/python compile, 365 unit tests, live integration tests, 90 Playwright tests, and real-app browser route validation passed. |
| 19 | Build artifacts | PASS | Rebuilt Python sdist/wheel, npm tarball, VSIX, and validated exact v3.2.0 artifact names. |
| 20 | Git tag and GitHub Release metadata | PASS | Source policy keeps version 3.2.0; tag/release metadata should point at the final validated main commit. No package assets were attached or published. |

## Fixes Applied

### Agent Registry UI wiring

- Finding: Agent Registry backend and adapter existed, but `/app#/agents` only rendered the runtime roster.
- Affected files: `static/v3/js/views/agents.js`, hashed v3 assets, `tests/visual/v3.spec.js`, `tests/visual/mock_server.cjs`.
- Expected behavior: `/app#/agents` exposes built-in/custom registry metadata, versions, capabilities, enabled state, and registration.
- Actual behavior before fix: Registry data was reachable only through API/adapters, not the product view.
- Fix: Added a live Agent Registry section, custom-agent registration controls, capability index display, enable/disable/remove actions, and Playwright coverage.

### Skills registry live payload normalization

- Finding: `/workspace/skills` returned `{ installed, available, registry }`, but `/app#/skills` only accepted a legacy `{ skills }` array.
- Affected files: `static/v3/js/views/skills.js`, hashed v3 assets, `tests/visual/v3.spec.js`, `tests/visual/mock_server.cjs`.
- Expected behavior: installed and available skills render from the real registry payload.
- Actual behavior before fix: live registry payload could render as an empty state.
- Fix: Normalized installed, available, array registry, and object registry shapes with de-duplication by skill name.

### Duplicate MCP route registration

- Finding: `create_tools_router` already included the MCP router, and `server_app` mounted it again.
- Affected files: `latticeai/server_app.py`, `tests/unit/test_route_compatibility.py`.
- Expected behavior: every public path/method has one handler.
- Actual behavior before fix: duplicate MCP/skills/plugin-directory path registrations.
- Fix: Removed the second MCP include and added a route duplicate guard.

## Validation Summary

- `npm run lint`: PASS
- `npm run typecheck`: PASS
- `npm run check:python`: PASS
- `./venv/bin/python -m pytest tests/unit -v`: PASS, 365 passed
- `LTCAI_TEST_BASE_URL=http://127.0.0.1:8899 ./venv/bin/python -m pytest tests/integration -v`: PASS, 9 passed
- `npx playwright test`: PASS, 90 passed
- Real app browser validation: PASS for `/app`, `/app#/chat`, `/app#/planning`, `/app#/workflows`, `/app#/marketplace`, `/app#/memory`, `/app#/skills`, `/app#/hooks`, `/app#/tools`, `/app#/mcp`, `/app#/agents`, `/app#/models`, `/app#/knowledge-graph`, `/app#/hybrid-search`, `/app#/settings` across desktop/mobile, including no console/page errors, no app 404/500 responses, no horizontal overflow, no Classic dependency text, and light/dark toggle.

## Release Readiness

READY FOR MANUAL PACKAGE PUBLICATION once the v3.2.0 tag and GitHub Release notes point at the final validated main commit. No package publication or deployment was performed by this audit.
