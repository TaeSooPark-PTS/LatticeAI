# Runtime Hook Coverage — v3.5.0

Every place Lattice AI executes a real tool or agent action, and whether it runs
through the unified lifecycle. The single tool path is
`dispatch_tool(hooks, name, args, run_fn)` in `latticeai/core/hooks.py`
(`pre_tool → execute → post_tool`); the HTTP helper `_tool_response`
(`latticeai/api/tools.py`) wraps it; uploads use the parallel
`pre_upload/post_upload/pre_index/post_index` lifecycle
(`latticeai/services/upload_service.py`); agent runs use `pre_run/post_run`.

**Method.** Routers/services were enumerated by a 6-way parallel audit and then
each genuine execution path was verified by reading the call site. A path is a
*bypass* only if a real tool/agent action skips its lifecycle. Read-only metadata
endpoints (status, list-permissions, config) execute no tool and are not bypasses.

**Result.** All discovered tool/agent execution paths are covered. The four
remaining "uncovered" rows are deliberate, documented design decisions (service
maintenance ops + an action already inside the upload lifecycle), not gaps.

## Tool / agent execution paths

| Entrypoint | Execution | Lifecycle path | pre fired | post fired | Test |
|---|---|---|---|---|---|
| `POST /tools/list_dir`, `workspace_tree`, `write_file`, `search_files`, `todo_*`, `inspect_html`, `preview_url`, `create_*`, `read_document`, `knowledge_*`, `obsidian_*`, `network_status` | tool fn | `_tool_response`→`dispatch_tool` | yes (`pre_tool`) | yes (`post_tool`) | `test_hooks_dispatch`, `test_runtime_coverage` |
| `POST /tools/read_file` | `read_file` (kwargs) | `_tool_response` (kwargs-aware) ✅v3.5.0 | yes | yes | `test_runtime_coverage` |
| `POST /tools/edit_file` | `edit_file` (kwargs) | `_tool_response` ✅v3.5.0 | yes | yes | `test_runtime_coverage` |
| `POST /tools/grep` | `grep` (kwargs) | `_tool_response` ✅v3.5.0 | yes | yes | `test_runtime_coverage` |
| `POST /tools/clear_history` | `clear_history` | `_dispatch`→`dispatch_tool` ✅v3.5.0 | yes | yes | `test_runtime_coverage` |
| `POST /tools/git_*`, `run_command`, `build_project`, `deploy_project` | tool fn | `_tool_response` | yes | yes | `test_route_compatibility` |
| `POST /local/*` (list/read/write) | `local_*` | `tool_response` | yes | yes | `test_route_compatibility` |
| `GET/POST /cu/*` (open_app/url/click/type/key/scroll/move/drag) | `computer_*` | `tool_response` | yes | yes | `test_runtime_coverage` |
| `GET /cu/status`, `/cu/screenshot` | `computer_status/screenshot` | `_dispatch` ✅v3.5.0 | yes | yes | `test_runtime_coverage` |
| `POST /cu/agent` (agent loop) | `execute_tool(name,args)` per step + Chrome shortcut | `_dispatch`→`dispatch_tool` ✅v3.5.0 | yes | yes | `test_runtime_coverage` |
| `POST /agent/eval` | `execute_tool` per eval case | `dispatch_tool` ✅v3.5.0 | yes | yes | (covered via dispatch_tool) |
| Single-agent runtime tool calls | `execute_tool` via `AgentDeps` | `core/agent.py`→`dispatch_tool` | yes | yes | `test_hooks_dispatch` |
| Agent run (start→finish) | orchestrator run | `agent_runtime` `pre_run`/`post_run` | yes (`pre_run`) | yes (`post_run`) | `test_hooks_dispatch` |
| Workflow tool node | `dispatch_tool` | `platform_runtime` | yes | yes | `test_hooks_dispatch` |
| Workflow run (start→end) | engine run | `WorkflowEngine` `pre_workflow`/`post_workflow` | yes | yes | `test_hooks_dispatch` |
| `POST /upload/document` | `process_uploaded_document` | upload lifecycle | `pre_upload` | `post_upload` | existing upload tests |
| Document indexing (upload + folder watch) | embed/graph build | `pre_index`/`post_index` | yes | yes | existing |

## Intentionally outside the tool lifecycle (documented, not gaps)

| Entrypoint | Why not `pre_tool`/`post_tool` |
|---|---|
| `read_document` inside `process_uploaded_document` (`upload_service.py`) | Already inside the upload lifecycle (`pre_upload`→`post_upload`); wrapping it again would double-dispatch the same user action. |
| `POST /api/memory/{prune,compact,rebuild,clear}` | Knowledge/memory **service** maintenance operations, not registry tools; they have their own audit events. Not part of the agent tool vocabulary. |
| `clear_history` inside `core/agent.py` executor | Runs inside an agent run already bracketed by `pre_run`/`post_run`; not re-wrapped to avoid nested dispatch. |
| Read-only status/config endpoints (`/tools/permissions`, `/obsidian/status`, model/catalog reads) | Execute no tool — nothing to gate. |

## Summary

- Genuine tool/agent execution paths discovered: **all enumerated routers + services**.
- Bypasses found and closed in v3.5.0: **read_file, edit_file, grep, clear_history, computer-use agent loop (+ /cu/status, /cu/screenshot), skill-eval**.
- Bypasses remaining: **none** (the four rows above are deliberate, documented design decisions).
- Coverage of discovered tool/agent execution paths: **100%**.
