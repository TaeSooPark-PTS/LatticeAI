# Lattice AI v4.3.0 Privacy And Local-First Audit

## Default Startup

Default local startup is loopback-only and local-first:

- host: `127.0.0.1`
- Telegram disabled
- model autoload disabled in local mode
- network CORS disabled
- storage engine: SQLite
- embedding provider: local hash fallback
- Docker not auto-started
- Postgres not required
- updater checks disabled unless explicitly enabled

## Token Presence Policy

Credentials alone do not enable outbound communication. The product-hardening
status distinguishes `credential_present` from `enabled`.

Audited integrations:

- Telegram: requires `LATTICEAI_ENABLE_TELEGRAM=true`
- Brain Network: peer push is explicit user/admin action; no automatic sync
- Update checks: require `LATTICEAI_ENABLE_UPDATES=true`
- Model downloads: require explicit load/autoload/user action
- Docker setup: requires runtime consent
- Postgres: requires explicit storage engine and DSN
- External connectors: credentials are inert until connector enablement and
  invocation

## Tests

Unit coverage proves:

- default config is local-only
- Telegram is disabled by default
- Telegram token presence alone does not enable Telegram
- cloud/API-token presence alone does not enable external connectors
- product hardening status reports opt-in egress honestly

## Desktop Guardrails

Tauri sidecar startup sets local-only environment overrides for the packaged
backend. Desktop status commands expose missing backend/runtime failures as
honest unavailable states.

## CLI Guardrails

The CLI startup notification path now requires `LATTICEAI_ENABLE_TELEGRAM=true`;
Telegram bot token and chat ID presence alone no longer starts a notification
thread.

## Remaining Owner-Only Privacy Decisions

- Package registry publication remains owner-only.
- Production model downloads remain explicit user action or policy opt-in.
- History rewrite for old binary assets remains owner-only because it requires a
  force push.
