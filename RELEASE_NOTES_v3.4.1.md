# Lattice AI v3.4.1 — Runtime Completion

**Release type:** runtime completion. Makes the v3.4.0 runtime systems
*verifiably* complete and **corrects the v3.4.0 overclaims** the implementation
audit found. Every claim here is verified by a **live end-to-end run** against a
booted server — not unit tests, mocks, or endpoint existence. Evidence:
[`docs/assets/v3.4.1/e2e_runtime_log.txt`](docs/assets/v3.4.1/e2e_runtime_log.txt)
(**7/7 PASS** + restore-on-restart PASS).

Local-first and Enterprise-disabled are unchanged.

---

## What v3.4.0 overclaimed, and what v3.4.1 actually delivers

### 1. Hooks — full lifecycle coverage (was: HTTP-tool-path only)

v3.4.0 said hooks "fire from tools and workflows." In reality tool hooks fired
only from the HTTP `/tools/*` path; the agent tool path, the multi-agent
executor, and the platform workflow path all bypassed hooks, and 4 of 7 built-ins
were advisory no-ops. v3.4.1:

- A single shared **`dispatch_tool`** (`core/hooks.py`) drives
  `pre_tool → execute → post_tool` for **all three** tool paths: HTTP
  (`api/tools._tool_response`), the single-agent runtime (`core/agent.py` via
  `AgentDeps.hooks`), and the workflow tool node
  (`platform_runtime._tool_node_runner`).
- **Workflow hooks fire from both paths** — the designer endpoint and the
  platform path (`run_workflow_by_id` now passes `hooks` to `WorkflowEngine`).
- The lifecycle is now the explicit set **`pre_/post_` × `run · tool · workflow ·
  upload · index`** (plus `agent`). The upload pipeline fires
  `pre_upload`/`post_upload` + `pre_index`/`post_index`; the local-folder index
  and **folder-watch reindex** fire `pre_index`/`post_index`.
- **All 7 built-in hooks have real runners** (`core/builtin_hooks.py`) — none is
  a silent no-op. A hook with no bound runner and no command is flagged
  **`advisory`** in the registry and UI. Legacy `workflow`/`pipeline` kinds are
  accepted and mapped forward.

*Live:* an HTTP tool call fired pre_tool (real `sensitivity=none` /
`policy[list_dir]: risk=low` output) + post_tool; an agent run auto-fired
pre_run + post_run; an upload fired all four upload+index kinds.

### 2. Local Agent — real probes (was: hardcoded `true`)

v3.4.0 hardcoded `online`, `handshake.ok`, `health.status`, and
`filesystem_access` to `true`. v3.4.1 **probes** them:

- a real **filesystem** write → read → delete in the data dir;
- a live **graph** reachability call (`knowledge_graph.stats()`);
- a derived **`mode`** ∈ {offline, starting, online, degraded, error};
- plus `pid`, `version`, handshake **`latency_ms`**, `last_seen`, and an `error`
  string when a probe fails.

*Live:* `mode=online`, real `pid`, `handshake.latency_ms≈0.7ms`,
`graph_reachable=true`, `error=null`. A failing subsystem yields `degraded`/
`error` honestly.

### 3. Connect Folder — proven end-to-end (was: never run)

*Live:* a real temp folder → permission approval (self-approval; the click is the
consent) → index → the **Files table** shows the folder's documents → **retrieval
+ hybrid search** return them (24 fused matches over the connected content).

### 4. Folder Watch — proven end-to-end + restore (was: isolation-only)

`watchdog` is a declared dependency and is installed. *Live:* creating a file in
a watched folder fired a debounced reindex → `post_index` (`folder.reindex`)
hook; the watcher reports the active source; and after a **server restart** the
watch was **restored** automatically.

---

## Validation (final code state)

- `npm run lint` — 64/64 v3 modules · `npm run typecheck` — `tsc` clean ·
  `npm run build` — assets + Python build OK.
- `pytest tests/unit` — **390 passed** (19 in `test_hooks_dispatch.py`).
- `pytest tests/integration` — **9 passed** against a live server.
- Playwright `tests/visual/v3.spec.js` — **13 passed**.
- **Live E2E** (`docs/assets/v3.4.1/e2e_runtime_log.txt`) — 7/7 + restore PASS.

## Known limitations (honest)

- Live model output (VLM inference, agent-generated *text*) still needs a loaded
  local model; the deterministic agent runner is LLM-free by design.
- The multi-agent executor only invokes tools via workflow/plugin nodes (it does
  not call `execute_tool` directly), so tool hooks fire on the agent path through
  `core/agent.py` (the single-agent runtime) and through workflow tool nodes —
  both now wired. There is no separate code path left unhooked.
- The built-in `tool-permission-gate` records the governance decision into the
  run log and blocks only when policy denies; primary enforcement stays in the
  tool dispatcher (documented, not duplicated).

## Registry publishing

Build artifacts (npm tarball, PyPI sdist/wheel, VSIX) are produced. **Publishing
to npm, PyPI, the VS Code Marketplace, and Open VSX is intentionally NOT done** —
the project owner publishes manually.
