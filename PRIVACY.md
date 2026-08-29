# Privacy

Current release: **12.2.0 — Small Voice**.

Lattice AI is local-first. It does not send your prompts, files, graph, or Brain
archive to Lattice-owned servers by default. Some user-chosen features can
contact third parties, and those paths are listed below.

Lattice AI는 기본적으로 로컬 우선입니다. 프롬프트, 파일, 그래프, Brain archive를
기본값으로 Lattice 소유 서버에 보내지 않습니다. 사용자가 선택한 일부 기능은
외부 서비스와 통신할 수 있으며, 아래에 명시되어 있습니다.

## What Is Stored Locally

Default local Brain data lives under `~/.ltcai` unless `LATTICEAI_DATA_DIR` is
configured. Additional user-owned mirrors or legacy vault data may live under
`~/.ltcai-brain` or a configured path.

Stored local data can include:

- local profile and session metadata;
- conversations, memories, decisions, and workflow history;
- Knowledge Graph nodes, edges, provenance, and search indexes;
- uploaded document blobs and extracted text;
- audit logs and admin operation events;
- backups and encrypted `.latticebrain` archives;
- model/runtime status metadata.

## What Can Leave The Machine

Nothing leaves the machine solely because a token exists. External communication
requires explicit configuration and user/admin action.

External paths include:

- **Cloud models**: prompts/context are sent to the selected provider only when
  a cloud path is configured and the escalation policy (or an explicit
  `/cloud` prefix) uses it. Default is local. Every cloud turn writes a
  shape-only egress audit (provider / model / reason, never content).
  Knowledge extracted from a cloud answer is staged as a Review Center
  `kg_cloud_expansion` proposal — it is not written to the graph until a
  person approves it (`auto_commit` default off). Two credential modes:
  `api_key` (OpenAI-compatible, mock-verified only in this release) and
  `cli_oauth` (locally OAuth-authenticated `agy` / `grok`).
- **Model downloads**: model identifiers and download requests go to model
  registries such as Hugging Face or Ollama registries after user consent.
- **Telegram**: removed in 11.6.0. The bridge lived in the platform code that
  became the AI worker, so no message leaves the machine over Telegram any more
  and `LATTICEAI_TELEGRAM_*` is no longer read. Nothing replaces it.
- **Brain Network**: peer exchange requires explicit pairing and network
  action.
- **PostgreSQL/Docker**: scale-mode setup uses configured local/network
  services after opt-in.
- **Update checks**: disabled unless explicitly enabled.
- **Marketplace/registry refreshes**: contact remote registries only when the
  user/admin invokes the path.
- **Setup package installs (12.0.0)**: `/setup/install` may run `brew`, `pip`
  or `uv`, which contact the corresponding package registries. It does so only
  for an item the request explicitly names *and* the server-derived allowlist
  contains — the command comes from the plan the server produced, never from
  the request — and the default path is still manual. Nothing is installed
  because a page was opened.

## .latticebrain Archives

`.latticebrain` archives are portable encrypted Brain bundles. They may contain
graph data, conversations, provenance, blob references or blobs, metadata, and
integrity hashes. Without the passphrase, archive metadata may still reveal
non-content facts such as archive version, creation time, size, and manifest
structure. Wrong passphrases, tampering, unsupported versions, and path
traversal fail closed.

## Screenshots And Logs

Release screenshots are generated from local mock/demo data. Secret-like values
are centrally redacted before logs, audit events, security exports, frontend
previews, and hook packets. Admins can see operational logs, but hard secrets
should not appear in clear text. Permission notifications contain a short token
hint and optional `LATTICEAI_PERMISSION_UI_URL` review link, never the approval
token itself.

## User Control

You can back up, export, inspect, verify, and move your Brain. Package
publishing and public deployments are owner-run; local use remains the default.

Deleting is yours too, and it is never automatic. A file that disappears from
an indexed folder is *reported*, not removed: its memory stays until you run
the folder-prune flow, which shows what it would remove before it removes
anything and only acts with an explicit confirmation. No cleanup path touches
files on your disk. Since 12.0.0 a restore takes effect immediately in the
running process, so the Brain you restored is the Brain the next question
reads — no restart, and no window in which an old copy answers.
