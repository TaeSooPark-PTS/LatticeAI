[![English](https://img.shields.io/badge/English-Active-2563eb?style=flat-square)](./SECURITY.md) [![한국어](https://img.shields.io/badge/한국어-Korean-475569?style=flat-square)](./docs/ko/SECURITY.md)

# Security Policy

> **Status: canonical** — current security model, kept in sync with the current
> release.

Current release: **14.1.2 — Living Synthesis**.

## Supported Versions

The public Git tree keeps release history from **13.0.0 through 14.1.2** (the
modern Rust-host and domain-grouped architecture era). Security support follows
that same product era: **the 14.x and 13.x lines receive fixes.**

| Version | Support |
| --- | --- |
| 14.1.x (latest) | Supported |
| 14.0.x | Supported |
| 13.9.x | Supported |
| 13.8.x | Supported |
| 13.7.x | Supported |
| 13.6.x | Supported |
| 13.5.x | Supported |
| 13.4.x | Supported |
| 13.0.x | Supported |
| Older than 13.0.0 | Not supported |

## Reporting Vulnerabilities

Please report security issues privately instead of opening a public issue:

**rnlgnquvk@gmail.com**

Include:

- vulnerability type and impact;
- reproduction steps or proof of concept;
- affected version;
- contact details if you want credit.

The expected first response target is 48 hours.

## Security Model

Lattice AI is a local-first Digital Brain. User knowledge, conversation context,
Knowledge Graph data, and archives stay local by default while every external
path requires explicit opt-in.

The Rust product server (`lattice-host`) owns all trust boundaries across **422
native operations (41 route families)** and supervises the loopback-only Python
AI compute worker (`latticeai.worker_app`), which is restricted to **20**
allow-listed compute routes (`rust/fixtures/worker_allowlist.json`, pinned from
both Python and Rust; all non-native unallowlisted paths answer `404`). Security
responsibilities map to explicit domain addresses:

- `rust/lattice-auth/src/` — versioned `$scrypt$` password hashing, bounded rate
  limiting, sessions, and structured CSRF origin enforcement (`CsrfReason` +
  `messages.rs`).
- `rust/lattice-agent/src/kernel/` — permission gates, mode-invariant circuit
  breakers, fail-closed `NEEDS_REVIEW` verification, and durable workflow
  approvals.
- `rust/lattice-agent/src/parse/` & `content/sanitize/` — untrusted model text
  parsing and pre-write byte sanitization.
- `rust/lattice-agent/src/tools/safe_io.rs` — OS-level TOCTOU-safe workspace I/O
  (Unix `openat` + `O_NOFOLLOW` + `fstat` root pinning; Windows component-by-component
  handle-pinned `CreateFileW` with `FILE_FLAG_OPEN_REPARSE_POINT` and
  `GetFinalPathNameByHandleW`).
- `rust/lattice-core/src/{graph_write/,integrity/,redact.rs}` — single-writer
  SQLite pool (`Store::write_conn`), workspace-scoped integrity checks/repairs,
  and compiled `OnceLock` PII/secret redaction.

### Default Secure Settings

| Setting | Default | Notes |
| --- | --- | --- |
| Bind address | `127.0.0.1` | Local-only unless explicitly changed; non-loopback binds refuse to start unless `LATTICEAI_REQUIRE_AUTH=true` |
| Auth | Off on loopback local mode | Forced on for public or non-loopback binding |
| CORS | Localhost only | Network CORS requires explicit opt-in |
| Session TTL | 24 hours sliding | Inactive sessions expire |
| Password hashing | Versioned `$scrypt$` | `Result`-based `hash_password` (`$scrypt$n=14,r=8,p=1$...`), with legacy `{salt}:{key}` verification compatibility |
| Rate limiting | Bounded in-memory (`10,000` keys) | Expired-first/oldest eviction, mutex poison recovery, unified HTTP `429` + `Retry-After` across `check_ip` and `enforce` |
| CSRF protection | Strict origin guard | Structured `CsrfReason` enum (`csrf.rs`) and centralized English error strings (`messages.rs`: `CSRF_DENIED_DETAIL`, `CSRF_ERROR_CODE`) |
| Desktop Tauri port & CSP | `PortPolicy::PinnedProduction` (`127.0.0.1:4825`) | Production desktop builds strictly require port `4825` and fail fast on conflict rather than drifting off `tauri.conf.json` CSP (no `'unsafe-inline'`) and `capabilities/default.json` remote IPC origins |
| API key storage | OS keyring where available | No intentional plaintext secret storage |
| Installer & `/setup/install` | Confirmation-token & allowlist gated | Redacted command plans, local process audit events, and server-derived `brew`/`pip`/`uv` allowlist only |
| Folder prune | Dry-run | `POST /api/ingestion/folder/prune` removes a graph subtree only on explicit `confirm`; never touches disk |
| Brain storage | SQLite WAL | Single-writer `GraphWriter`; PostgreSQL remains `Planned` |
| Chat auto file read | Disabled | Arbitrary path reads require explicit approval |
| Legacy-global graph reads | Disabled | Must be explicitly requested (`include_legacy_global`) |
| Invitation authorization | Signed & expiring | Static authorization cookies and built-in default invitation codes are rejected |

### Authentication, Sessions & CSRF

- Passwords use versioned `$scrypt$` hashing (`lattice-auth::password`) with
  `Result<String, PasswordError>` error propagation (no panicking `.expect()`)
  and automatic verification compatibility for legacy `{salt}:{key}` hashes.
- Session cookies are `HttpOnly` and `SameSite=Lax`; non-loopback/public
  deployments also set `Secure`.
- Session tokens are stored locally only as SHA-256 hashes; deleted or disabled
  accounts invalidate existing sessions on the next request.
- `lattice-auth` is the sole writer of `sessions.json`; the Python worker only
  reads it, refreshing on `mtime_ns`/`size` changes (throttled to 1 parse/s) so
  post-boot logins are recognized without enabling disk-read floods.
- Local data directories use mode `0700` and atomic JSON/session files use mode
  `0600` on POSIX filesystems.
- CSRF verification in `rust/lattice-auth/src/csrf.rs` uses the typed
  `CsrfReason` enum and centralized constants in `rust/lattice-auth/src/messages.rs`
  (`CSRF_DENIED_DETAIL`, `CSRF_ERROR_CODE`) for byte-consistent HTTP `403`
  responses.
- Invitation authorization is HMAC-signed, time-limited, and server-bound.

### File, Workspace & Archive Safety

- **Per-user `AGENT_ROOT` isolation:** Agent workspace paths are derived
  server-side via `IsolatedRootSpec` and `resolve_isolated_agent_root`,
  separating files by `(user_id, tenant_id, workspace_id)` and failing closed
  (`workspace_access_denied`) when shared-workspace membership is unverified.
- **TOCTOU-safe workspace I/O:** `rust/lattice-agent/src/tools/safe_io.rs`
  confines reads, writes, and file creation to the authorized workspace root
  using handle-relative `openat` + `O_NOFOLLOW` + `fstat` on Unix and
  component-by-component handle-pinned `CreateFileW` + `GetFinalPathNameByHandleW`
  on Windows.
- **Workspace ownership enforcement:** Integrity repairs, entity merges/rollbacks,
  and knowledge corrections validate target workspace ownership and reject
  cross-workspace targets with HTTP `403`; global repairs require explicit
  `require_admin` authorization.
- **`.latticebrain` archive cryptography:** Implemented in Rust
  (`lattice-platform::knowledge::portability::{archive, crypto}`) using RFC 7914
  `scrypt` passphrases or X25519 + HKDF-SHA256 recipient sealing, Encrypt-then-MAC
  (`SHA256-CTR` + `HMAC-SHA256`) with constant-time MAC checks (`ct_eq`), and
  ZIP path-traversal rejection (`validate_zip_entries`). Restores bump the store
  generation epoch so pooled connections recycle immediately in-process.
- **Desktop & extension hardening:** Tauri production builds enforce
  `PortPolicy::PinnedProduction` on `127.0.0.1:4825` (failing fast with clear
  `gateway_error` diagnostics if port `4825` is occupied instead of silently
  scanning `4826+`), serialize gateway lifecycle transitions, and enforce strict
  CSP without `'unsafe-inline'`. `vscode-extension` enforces optional Bearer auth
  and single-use TTL approval tokens; `browser-extension` restricts host
  permissions to port `4825` and keeps approval decisions off-surface.
- **Static security & supply-chain gates:** Python CI enforces Ruff `"BLE"`
  (no broad blind `except Exception`), McCabe `max-complexity <= 20`, production
  `S608` SQL-injection checks, and minimal Bandit per-file ignores (`S603`,
  `S105`, `S108`). All GitHub Actions `uses:` references are pinned to immutable
  commit SHAs, and the multi-stage `Dockerfile` pins every base image by SHA256
  digest.

### Agent & Tool Safety

- `AgentRuntime` preview/readiness never executes tools; non-auto-approved plans
  require explicit human approval.
- Every mutating tool is tracked in `MUTATING_TOOL_INVENTORY`. Existing-file
  edits and deletions stage as `change_proposal` items recording a base content
  SHA and fail closed with `409` on concurrent edits.
- The loop verifier fails closed to `NEEDS_REVIEW` on unverifiable or failing
  outcomes across all profiles (`standard`, `compact`, `guided`).
- `guided` mode removes JSON formatting demands without bypassing any permission
  gate, loop guard, pre-write snapshot, or `sanitize_write_content` pass, and
  the loop refuses verbatim prompt-example copies (`COPIED_EXAMPLE`).
- Command execution uses a fixed executable allowlist and sanitized environment;
  interpreters, traversal, symlink escapes, ripgrep preprocessors, and mutating
  `find` flags are denied.
- MCP (`POST /mcp`) is inside the OpenAPI product contract and shares
  `check_governance` with the agent catalog; MCP and plugins cannot invoke local
  filesystem/document tools without dedicated approval-token endpoints.
- Secret-like values and PII are centrally redacted (`lattice-core::redact`)
  before logs, audit payloads, `LoopTrace`, cloud egress, and posture-gated
  `GET /host/diagnostics`.

### External Communication

There is no default telemetry. External communication requires explicit
configuration and user/admin action (cloud model calls, model downloads, Brain
Network peer exchange, update checks, marketplace refreshes, or consented
`/setup/install` package runs).

- **Audited, proposal-first cloud turns:** Every cloud turn strips
  `sensitive`/`private`/`do_not_share` nodes and secret files in both modes,
  redacts PII/secrets, enforces bounded token budgets, writes a shape-only
  `cloud_egress` audit record before calling the provider, and stages extracted
  knowledge as a `kg_cloud_expansion` Review Center proposal (`auto_commit`
  default `false`). Per-request `network_mode: "local_only"` always wins.
- **Dual cloud credentials:** `api_key` (OpenAI-compatible, zero-billing
  `GET /models` live probe) and `cli_oauth` (locally authenticated `agy`/`grok`
  in a neutral temp dir with 120s timeout).
- **Web-page capture:** Resolves and pins public IPs, rejects private/reserved
  ranges, rechecks every redirect, ignores proxy env vars, and caps streamed
  text at 4 MiB.

### Security-Sensitive Environment Variables

| Variable | Requirement | Purpose |
| --- | --- | --- |
| `LATTICEAI_PERMISSION_UI_URL` | Optional | Base URL for human permission-review links; approval tokens are never appended |
| `LATTICEAI_REQUIRE_AUTH` | Optional, default off on loopback | Forces authentication in local mode; required for public or non-loopback binds |
| `LATTICEAI_INVITE_GATE_ENABLED` | Optional, default off | Enables signed invitation onboarding without reopening unrestricted registration |
| `LATTICEAI_INVITE_CODE` | Recommended with invite gate | Operator-generated random invite code (otherwise a private per-install value is generated; no shared default) |

## Public Deployment Guidance

If exposing Lattice AI beyond localhost:

1. Set `LATTICEAI_MODE=public` and `LATTICEAI_REQUIRE_AUTH=true`.
2. Keep open registration closed; use the signed invitation gate if onboarding
   additional users.
3. Terminate HTTPS at a reverse proxy (nginx / Caddy) and configure
   `CSRF_TRUSTED_ORIGINS`.
4. Mount persistent storage and restrict port `4825` at the firewall.
5. Review `ALLOWED_COMMANDS`, CORS, rate limits, and graph exposure settings
   before launch.

## Known Limitations

- Local file security depends on the user's OS account, disk encryption, and
  backup policy.
- Cloud model prompts follow the selected provider's policy once explicitly
  enabled by the user.
- A trusted local admin can inspect local files and process memory outside
  Lattice AI.
- Hugging Face model downloads: `scripts/verify_hf_model_registry.py` statically
  verifies Hub metadata without fetching weights and `EmbeddingIndexContract`
  records `model_revision`, while `latticeai/services/model_runtime/download.py`
  and `latticeai/core/embedding_providers/text.py` do not yet enforce immutable
  default commit `revision=` pinning across all download paths (tracked as
  Bandit `B615` in [`docs/SECURITY_AUDIT.md`](docs/SECURITY_AUDIT.md)).
