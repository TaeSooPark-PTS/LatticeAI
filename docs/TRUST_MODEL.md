# Lattice AI Trust Model

Lattice AI's trust model is local-first, opt-in for external communication, and
honest when something is unavailable.

## Local By Default

By default, Lattice AI binds the API to `127.0.0.1`, stores Brain data under the
local data directory, and does not send prompts, documents, graph content, or
archives to Lattice-owned servers.

Local data includes:

- local profile and sessions;
- conversations and memory records;
- Knowledge Graph nodes, edges, provenance, and search indexes;
- uploaded document blobs;
- audit and admin operation logs;
- backups and encrypted `.latticebrain` archives.

## Explicit External Paths

Some features can contact third parties, but they require explicit user/admin
action or configuration:

- model downloads from model registries;
- cloud model API calls after keys are configured and a cloud model is chosen;
- Telegram bridge after the integration is enabled;
- Brain Network peer actions after pairing/initiating network flows;
- Docker/Postgres setup after opt-in scale configuration;
- update checks only when update checking is enabled;
- remote marketplace/registry refreshes only through explicit user actions.

Token presence alone must not start external communication.

## Consent And Honesty Gates

Lattice AI should fail closed or report unavailable state for:

- no model loaded;
- local model not installed;
- installed model not loaded;
- missing cloud key;
- deterministic/model-free preview;
- dry-run versus real execution;
- no graph/context evidence available;
- unavailable external integration;
- wrong archive passphrase;
- archive path traversal or tampering.

## Admin Boundary

The normal user product is Brain Chat, memory, topics, relationships, graph
exploration, model state, and Brain ownership. Admin Console is for users,
roles, audit logs, security events, retention, and operations. Admin visibility
does not mean secrets should appear in clear text.

## Known Limitations

- Local files are only as protected as the user's machine, account, backups, and
  disk encryption.
- Cloud model prompts follow the selected provider's policy.
- A local admin can inspect local files and process memory outside Lattice AI.
- Marketplace and model registries are third-party services when explicitly
  contacted.

