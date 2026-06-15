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

Next: wait for frontend/UX integration points or larger module boundary.