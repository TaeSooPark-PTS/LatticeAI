# Backend Hardening v6.1

## Root Legacy Scan (2026-06-16)

Scanned: ltcai_cli.py (309 LOC), telegram_bot.py (1015 LOC)

### Findings
- ltcai_cli.py: entrypoint with argparse main(), banner, tunnel, telegram notify, env helpers (_load_env_file, _apply_extra_path, _has_module, _local_ips, _print_banner, _start_tunnel, _send_telegram). All helpers called from main flow or each other. No isolated pure helper safe to extract without import cycles or behavior change.
- telegram_bot.py: large stateful bot (_bot_pending_plans, session auth, chat registry, file handlers, command dispatch). Duplicated env loader vs ltcai_cli. High coupling to server endpoints.

### Decision
No safe small separation candidate found that preserves:
- shim compatibility
- zero behavior change
- no new package structure risk

Blocker recorded. Recommend dedicated `latticeai/cli/` package in later hardening phase after AgentRuntime stabilization.

## v6.1 Extraction Performed (2026-06-16)

Pure helpers safely extracted despite initial conservative scan:
- Created `latticeai/cli/` package with `__init__.py`
- Moved to `latticeai/cli/runtime.py`:
  - `_load_env_file(path: Path) -> None`
  - `_apply_extra_path() -> None`
  - `_has_module(name: str) -> bool`
- `ltcai_cli.py` now imports from `latticeai.cli.runtime` (root entrypoint preserved, no behavior change)
- Added unit tests in `tests/unit/test_cli_runtime.py` (CLI helper 전용)
- `tests/unit/test_import_guard.py`는 BrainCore import guard 전용

This resolves the recorded blocker for the three pure helpers. Remaining helpers (`_local_ips`, banner, tunnel, telegram) stay in root entrypoint for now.

Next: continue with `telegram_bot.py` analysis after cli package stabilization.

## telegram_bot.py Legacy Breakdown (2026-06-16, pts_grok)

Scanned: telegram_bot.py (1015 LOC, root legacy)

### Function-level extraction candidates

**Env / Config (lines 17-55):**
- load_env_file, env_value: duplicated with ltcai_cli; globals heavy (TOKEN, API_URL, BASE_URL*, DATA_DIR, CHAT_IDS_FILE, INVITE_CODE, SERVER_PORT, PUBLIC_WEB_URL)
- Decision: unsafe (global mutation + duplicate logic)

**Session / Auth (lines 63-94):**
- _get_server_session, _server_client: read DATA_DIR/sessions.json + users.json, admin role filter, 7-day expiry. Stateful cookie injection.
- Unsafe: depends on DATA_DIR global, json side effects, admin logic.

**Chat registry (lines 98-121):**
- load_chat_ids, save_chat_ids, register_chat_id: JSON file IO on CHAT_IDS_FILE, set ops.
- Unsafe: global path + logger + file mutation.

**Telegram send helpers (lines 125-184):**
- send_message, send_photo, send_document, send_chat_action, answer_callback, edit_message: direct httpx to API_URL, chunking, file upload.
- Unsafe: API_URL global, error logging, side-effect network.

**Network / URL (lines 188-214):**
- get_lan_ip: socket + hostname lookup (pure-ish but OS dependent)
- get_web_url, get_graph_url: compose with PUBLIC_WEB_URL / SERVER_PORT / INVITE_CODE globals
- Decision: get_lan_ip borderline pure but not worth extracting alone; URL helpers unsafe due to globals.

**Broadcast / Polling / Download (lines 218-280+):**
- broadcast_web_chat, get_updates, download_telegram_file, download_as_base64: all depend on TOKEN/API_URL or chat registry.
- Unsafe: high coupling.

**Command / AI / Handler functions (lines 283-1007):**
- show_menu, show_status, process_ai_request, handle_command, handle_callback_query, run_bot, ... (30+ handlers)
- All stateful: use _bot_pending_plans, pending plan approvals, agent workspace, multiple URL globals, async task dispatch.
- Unsafe: massive shared mutable state + external service coupling. No isolated pure helper.

### Extraction decision
No safe pure helper identified for immediate package move.
Reasons per candidate:
- All top-level helpers either mutate/read module globals or perform I/O/network with bot-specific config.
- Moving would require significant DI refactor or new context object (post v6.1 scope).
- Preserves zero behavior change and shim compatibility.

Recorded in BACKEND_HARDENING.md. Recommend `latticeai/telegram/` package in a
future phase after Knowledge Graph + AgentRuntime stabilization.

Import smoke test:
- `node scripts/run_python.mjs - <<'PY' ... import telegram_bot ... PY` -> `import ok`
