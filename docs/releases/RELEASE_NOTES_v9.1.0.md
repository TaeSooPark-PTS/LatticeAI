# Lattice AI v9.1.0 — Code Review Completion & Fail-Closed Runtime

> **Status: historical** — point-in-time release note.

Released: 2026-07-11

9.1.0 completes every actionable finding in
`docs/reviews/CODE_REVIEW_2026-07-11.md`. It closes fail-open security paths,
gives runtime/model/chat state explicit typed ownership, makes frontend failures
honest and tested, and cleans the repository and release surface while
preserving all historical 8.0.0–9.0.0 records.

## Security: deny by default

- Telegram messages and callbacks are accepted only from chat IDs listed in the
  required `LATTICEAI_TELEGRAM_ALLOWED_CHAT_IDS` setting. Unauthorized chats
  are rejected before registration.
- The Telegram-to-server bridge requires
  `LATTICEAI_SERVER_SESSION_TOKEN`; it no longer scans session storage for a
  bearer value.
- Public invitation authorization is signed and expiring. The static
  `authorized=true` trust path and built-in invitation code are removed. SSO
  just-in-time provisioning binds the verified invite claim to its one-time
  state/nonce/PKCE transaction, so a new SSO account cannot bypass the gate.
- Knowledge Graph scope lookup errors and unknown scoped nodes return no data;
  legacy-global reads require an explicit compatibility opt-in.
- Computer screenshot/status, knowledge/Obsidian, and network-status actions use
  explicit capability, consent, workspace, and ToolRegistry policy checks.
- Permission notifications disclose a short token hint only. Operators may set
  `LATTICEAI_PERMISSION_UI_URL` to provide a review-page link without putting
  approval secrets in messages.
- Session cookies become secure on non-loopback deployments, permission queues
  are atomically stored with private permissions, reconnaissance endpoints
  protect sensitive fields, and MCP filesystem paths are masked.

## Typed runtime, model, and chat ownership

- App construction uses typed config, security, Brain, model, and router stages
  instead of ambient `locals()` export.
- Model runtime operations use injected typed state rather than module-global
  dual synchronization; API errors are translated at the HTTP boundary.
- Chat contracts, history, documents, and streaming are separate focused
  modules backed by deeper service ownership. Chat, agent, and Computer Use
  history/run records retain their authenticated user and workspace scope.
- Shallow runtime forwarding layers, repeated timestamp helpers, and duplicated
  run-status constants are consolidated.
- Setup and local-knowledge implementations live inside the `latticeai`
  package; root modules remain narrow compatibility shims.
- Readiness checks reject forbidden architecture patterns in addition to
  checking that expected symbols exist.

## Honest frontend failures and tests

- Critical API failures produce unavailable/error UI instead of being cached as
  an empty, healthy Brain.
- Answer proof and model-continuity actions stop on failed API results, and
  shared action controls call success callbacks only after a successful result.
- Brain behavior is split into focused hooks, translations into namespaces, and
  experience styles into surface-specific files.
- Vitest covers API result shapes, Brain proof parsing, conversation state,
  shared primitives, and i18n. Visual tests protect the core-service error path.

## Repository hygiene

- Review documents are archived under `docs/reviews/` and retained as
  historical evidence.
- Obsolete local VSIX files are removed. Ignored build, audit, and personal
  workspace trees remain outside version control and are excluded from release
  archives; release artifacts remain ignored unless explicitly published.
- Tauri remains the primary desktop shell; Electron is documented as an
  experimental compatibility shell with the canonical port aligned to 4825.
- Current documentation and release examples are synchronized to 9.1.0 without
  rewriting historical release notes.

## Exact release artifacts

Use exact filenames only:

- `dist/ltcai-9.1.0-py3-none-any.whl`
- `dist/ltcai-9.1.0.tar.gz`
- `dist/ltcai-9.1.0.vsix`
- `ltcai-9.1.0.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_9.1.0_aarch64.dmg`

Package publishing remains an explicit owner action. Do not use wildcard
artifact uploads.
