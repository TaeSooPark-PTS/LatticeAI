# Security Policy

Current release: **9.8.0 — Honest Knowledge Pipeline**.

## Supported Versions

The public Git tree keeps release history from 8.0.0 through 9.8.0. Security
support follows that same product era.

| Version | Support |
| --- | --- |
| 9.8.x (latest) | Supported |
| 9.7.x | Supported |
| 9.6.x | Supported |
| 9.5.x | Supported |
| 9.4.x | Supported |
| 9.3.x | Supported |
| 9.2.x | Supported |
| 9.1.x | Supported |
| 9.0.x | Supported |
| 8.9.x | Supported |
| 8.8.x | Supported |
| 8.7.x | Supported |
| 8.6.x | Supported |
| 8.5.x | Supported |
| 8.4.x | Supported |
| 8.3.x | Supported |
| 8.2.x | Supported |
| 8.1.x | Supported |
| 8.0.x | Supported |
| Older than 8.0.0 | Not supported in the current Git history |

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

Lattice AI v9.6.0 is a local-first Digital Brain. It keeps user knowledge,
conversation context, Knowledge Graph data, and archives local by default while
making external paths explicit.

### Default Secure Settings

| Setting | Default | Notes |
| --- | --- | --- |
| Bind address | `127.0.0.1` | Local-only unless explicitly changed |
| Auth | Off on loopback local mode | Forced on for public or non-loopback binding |
| CORS | Localhost only | Network CORS requires explicit opt-in |
| Session TTL | 24 hours sliding | Inactive sessions expire |
| API key storage | OS keyring where available | No intentional plaintext secret storage |
| Installer/process execution | Confirmation-token gated | Redacted command plans and local process audit events |
| Brain storage | SQLite | PostgreSQL is optional scale/migration tooling |
| Docker Postgres setup | Disabled | Requires explicit consent |
| Production CSP | Strict local app policy | External script/frame/object blocked by default |
| Chat auto file read | Disabled | Arbitrary path reads require explicit approval |
| Telegram inbound access | Deny by default | Requires an explicit chat-ID allowlist and dedicated server session token |
| Legacy-global graph reads | Disabled | Must be explicitly requested for compatibility |
| Invitation authorization | Signed and expiring | Static authorization cookies and built-in invitation codes are rejected |

### Authentication And Sessions

- Passwords use scrypt hashing.
- Session cookies are `HttpOnly` and `SameSite=Lax`; non-loopback/public
  deployments also set `Secure`.
- Session tokens are stored locally only as SHA-256 hashes; deleted or disabled
  accounts invalidate existing sessions on the next request.
- The local data directory is mode `0700` and atomic JSON/session files are mode
  `0600` on POSIX filesystems.
- Enterprise SSO paths are explicit configuration paths.
- Invitation authorization is HMAC-signed, time-limited, and server-bound. A
  literal `authorized=true` cookie does not grant access, and public mode will
  not start an invitation gate with a built-in default code.

### File And Archive Safety

- Uploaded files are signature-checked where supported.
- Uploaded blobs are stored under the local data directory.
- `.latticebrain` archives are encrypted and integrity-checked.
- Wrong passphrases, tampering, unsupported archive versions, and archive path
  traversal fail closed.

### Agent And Tool Safety

- AgentRuntime preview/readiness does not execute tools and non-auto-approved
  plans require explicit human approval.
- ToolRegistry owns dispatch, permissions, diagnostics, direct HTTP/MCP policy
  gates, and MCP install state.
- Shared agent/plugin registries and graph curation are administrator-managed;
  registry reads redact secret-shaped configuration values.
- MCP graph calls and realtime presence are bound to the authenticated identity
  and active/allowed workspace; MCP environment values are never returned.
- Knowledge Graph scope lookup fails closed on projection/query errors. Unknown
  scoped nodes are private, and legacy-global reads require an explicit
  `include_legacy_global` compatibility choice.
- MCP and plugin execution cannot invoke local filesystem/document tools; those
  tools require the dedicated, scope-bound approval-token endpoints. Plugins
  may invoke only tools explicitly declared in their installed manifest.
- Realtime SSE connections revalidate session and workspace membership before
  delivery, including events queued before a membership revocation.
- Command/file tools enforce sandbox and uniqueness checks where applicable.
- The command tool uses a fixed executable allowlist and sanitized environment;
  interpreter commands, executable paths, traversal, symlink escapes, ripgrep
  preprocessors, and mutating/exec `find` flags are refused.
- Secret-like values are centrally redacted before logs, audit payloads,
  security exports, frontend previews, and hook packets.
- Computer screenshot/status, knowledge/Obsidian, and chat network-status tools
  require their declared policy, capability, consent, and scope checks.
- Permission requests are written atomically with private permissions. External
  notifications contain only a token hint and an optional configured review UI
  link, never the full approval token.

### External Communication

There is no default telemetry. Prompts, files, graph content, and archives do
not leave the machine solely because a token exists.

External communication requires explicit configuration and user/admin action:

- cloud model calls;
- model downloads;
- Telegram bridge, only for explicitly allowed chat IDs;
- Brain Network peer exchange;
- Docker/Postgres scale setup;
- update checks;
- marketplace or remote registry refresh.

Web-page capture additionally resolves and pins public IPs, rejects private and
reserved address classes, rechecks every redirect, ignores proxy environment
variables, and streams at most 4 MiB of textual content.

### Security-Sensitive Environment Variables

| Variable | Requirement | Purpose |
| --- | --- | --- |
| `LATTICEAI_TELEGRAM_ALLOWED_CHAT_IDS` | Required when Telegram is enabled | Comma-separated inbound Telegram chat-ID allowlist; missing/empty means deny all |
| `LATTICEAI_SERVER_SESSION_TOKEN` | Required when Telegram is enabled | Dedicated bearer used by the bridge for local Lattice AI API calls |
| `LATTICEAI_PERMISSION_UI_URL` | Optional | Base URL for human permission-review links; approval tokens are never appended or sent |
| `LATTICEAI_INVITE_GATE_ENABLED` | Optional, default off | Enables invitation onboarding without reopening unrestricted registration |
| `LATTICEAI_INVITE_CODE` | Recommended when the invitation gate is enabled | Operator-generated random invitation code; when omitted, a private per-install value is generated and persisted—there is no shared default |

## Public Deployment Guidance

If exposing Lattice AI beyond localhost:

1. Set `LATTICEAI_MODE=public`.
2. Keep registration closed. If invitation onboarding is required, enable the
   invitation gate and set a random private `LATTICEAI_INVITE_CODE`, or retain
   the generated per-install secret on persistent storage; no shared default
   exists. A valid signed invite authorizes only that registration request;
   unsigned direct `/register` calls remain closed. SSO just-in-time account
   creation requires the same invite claim, bound server-side to the one-time
   OIDC state, nonce, and PKCE transaction; existing active SSO accounts may
   still sign in without a new invitation.
3. Put the app behind HTTPS.
4. Mount persistent storage.
5. Review CORS, auth, rate limits, and graph exposure settings before use.
6. Keep Telegram disabled, or set both its chat-ID allowlist and dedicated
   server session token before enabling it.

## Known Limitations

- Local file security depends on the user's OS account, disk encryption, and
  backup policy.
- Cloud model prompts follow the selected provider's policy after the user
  chooses that provider.
- A trusted local admin can inspect local files and process memory outside
  Lattice AI.
- MLX/model file integrity depends on provider metadata and local download
  controls.
