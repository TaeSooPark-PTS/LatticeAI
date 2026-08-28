# Lattice AI Trust Model

Current release: **12.1.0 — Fast Path**.

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

- cloud model calls after credentials resolve (`cloud_provider.json` / env
  key / locally OAuth-authenticated `agy` or `grok`) and the escalation
  policy — or an explicit `/cloud` prefix — uses them. Every such turn
  writes a shape-only egress audit (provider / model / reason, never
  content) and stages extracted knowledge as a Review Center proposal;
- model downloads from registries after install consent;
- package installs from `/setup/install` (brew / pip / uv) — only for an item
  the request names that the **server-derived** allowlist also contains, with
  the command taken from the server's own plan rather than from the request;
  the default path remains manual;
- Telegram bridge — removed in 11.6.0 with the platform code that became
  the AI worker; nothing replaces it;
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
- Telegram inbound (the bridge was removed in 11.6.0; nothing replaces it);
- wrong archive passphrase;
- archive tampering, unsupported archive versions, or path traversal;
- a hash-fallback embedder standing in for a real one — it is labelled
  `fallback`, never presented as semantic recall;
- an opted-in approximate vector index that cannot answer — the search falls
  back to the exact scan and carries the reason rather than returning a
  shorter list;
- a model that cannot produce a tool call — the run is guided through numbered
  choices under the same gates, and an unverifiable result is still
  `NEEDS_REVIEW`;
- deleting knowledge — a vanished file is reported and only a confirmed prune
  removes its subtree.

## Product Boundary

Normal Brain use is conversation, memory, topics, relationships, model state,
source capture, and Knowledge Graph exploration. Admin Console is for users,
roles, audit logs, security events, retention, and operations. Admin visibility
does not mean secrets should appear in clear text.

## Known Limits

- Local files are only as protected as the user's machine, OS account, backups,
  and disk encryption.
- Cloud model prompts follow the selected provider's policy once the user
  explicitly chooses that provider (or the `auto`/`always` escalation
  policy does, behind `cloud_allowed`). The `api_key` path is
  mock-verified only in this release; live OAuth used `cli_oauth` at
  zero billing. Extracted cloud knowledge is proposal-first.
- A local admin can inspect local files and process memory outside Lattice AI.
- Marketplace and model registries are third-party services when explicitly
  contacted.
