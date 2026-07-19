# Lattice AI Trust Model

Current release: **9.3.0 — Proactive Brain Intelligence**.

Lattice AI is local-first, explicit about external communication, and honest
when a capability is unavailable.

## Local By Default

By default, Lattice AI binds the API to `127.0.0.1`, stores Brain data under the
local data directory, and does not send prompts, documents, graph content, or
archives to Lattice-owned servers.

Local data can include:

- profile and session metadata;
- conversations, memories, decisions, and workflow history;
- Knowledge Graph nodes, edges, provenance, and indexes;
- uploaded document blobs and extracted text;
- audit and admin operation logs;
- backups and encrypted `.latticebrain` archives.

## Explicit External Paths

External communication requires configuration plus a user/admin action:

- cloud model calls after keys are configured and a cloud model is selected;
- model downloads from registries after install consent;
- Telegram bridge after the integration is enabled, the chat is allowlisted,
  and a dedicated server session bearer is configured;
- Brain Network peer actions after pairing;
- Docker/Postgres setup after opt-in scale configuration;
- update checks only when enabled;
- marketplace or remote registry refreshes only when invoked.

Token presence alone must not start external communication.

Authenticated history, Knowledge Graph reads, and Tool API calls must also stay
inside the caller's user/workspace scope. Direct HTTP/MCP tool routes run
ToolRegistry policy before hooks or handlers execute.

## Consent And Honesty Gates

Lattice AI should fail closed or show an unavailable state for:

- no model loaded;
- local model not installed or not loaded;
- missing cloud key;
- deterministic/model-free preview;
- dry-run versus real execution;
- no graph/context evidence available;
- unavailable external integration;
- unknown or unreadable Knowledge Graph workspace scope;
- missing Telegram chat allowlist or dedicated server session token;
- wrong archive passphrase;
- archive tampering, unsupported archive versions, or path traversal.

## Product Boundary

Normal Brain use is conversation, memory, topics, relationships, model state,
source capture, and Knowledge Graph exploration. Admin Console is for users,
roles, audit logs, security events, retention, and operations. Admin visibility
does not mean secrets should appear in clear text.

## Known Limits

- Local files are only as protected as the user's machine, OS account, backups,
  and disk encryption.
- Cloud model prompts follow the selected provider's policy once the user
  explicitly chooses that provider.
- A local admin can inspect local files and process memory outside Lattice AI.
- Marketplace and model registries are third-party services when explicitly
  contacted.
