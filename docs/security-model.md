[![English](https://img.shields.io/badge/English-Active-2563eb?style=flat-square)](./security-model.md) [![한국어](https://img.shields.io/badge/한국어-Korean-475569?style=flat-square)](./ko/security-model.md)

# Lattice AI — Security Model

## Design Principles

Lattice AI is designed as a **personal AI workspace**. Defaults are locked
down as tightly as possible, and network exposure is allowed only via explicit
opt-in.

## Network Binding

| Setting | Binding | Purpose |
|---------|---------|---------|
| Default | `127.0.0.1:4825` | Local-only, unreachable externally |
| `LATTICEAI_HOST=0.0.0.0` | `0.0.0.0:4825` | Allow devices on the same Wi-Fi/LAN |
| Public deployment | Behind nginx/Caddy | HTTPS termination + reverse proxy |

## Authentication

### Passwords

- scrypt hashing (`hashlib.scrypt`, N=2^14, r=8, p=1)
- Stored in `users.json` as `{"hash": "<scrypt hex>"}`
- Plaintext passwords are never retained in memory

### Sessions

- Tokens generated via `secrets.token_urlsafe` are stored in
  `~/.ltcai/sessions.json` as SHA-256 hashes only (legacy plaintext tokens are
  migrated automatically on load)
- TTL: 24 hours + sliding refresh (extended automatically on activity, flushed
  to disk every 15 minutes)
- Cookie flags: `HttpOnly; SameSite=Lax; Path=/`
- `Secure` is also enforced in public/non-loopback mode (requires HTTPS)
- Persisted across server restarts (file-backed)
- Existing sessions belonging to deleted or deactivated accounts are rejected
  immediately on the next request
- On POSIX systems: data directory `0700`, atomic JSON/session files `0600`
- Invite-gate cookies are HMAC-signed with a per-installation secret and carry
  an expiry timestamp; literal `authorized=true` cookies are never trusted and
  there is no shared default invite code

### SSO (Optional)

- Entra ID / Okta OIDC (`OIDC_DISCOVERY_URL`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`)
- Exchanged for an internal session token after callback
- When the invite gate is enabled, new SSO JIT accounts require a signed invite
  authorization bound to a server-side single-use state/nonce/PKCE transaction
- Admin handoff uses a one-time `sessionStorage` read (avoiding URL parameter
  leakage)

## API Key Security

- Stored in the OS keyring (macOS Keychain, Windows Credential Manager, Linux
  Secret Service)
- Plaintext disk persistence happens only when
  `LATTICEAI_ALLOW_PLAINTEXT_API_KEYS=true` is explicitly set
- API key, token, and password patterns are automatically masked before saving
  chat history

## CORS

```python
CORS_ALLOWED_ORIGINS = ["http://localhost:4825", "http://127.0.0.1:4825"]
```

- Default: localhost only
- `LATTICEAI_CORS_ALLOW_NETWORK=true`: allow devices on the same LAN/Wi-Fi
- Public deployment: allow only the reverse-proxy origin

## Rate Limiting

Per-user token bucket:

| Endpoint | Burst | Sustained |
|----------|-------|-----------|
| `/chat` | 30 | 30/min |
| `/agent` | 10 | 6/min |
| `/upload` | 20 | 12/min |

Set `LATTICEAI_RATE_LIMIT=0` to disable (for local development only).

## File Uploads

```python
MAGIC_NUMBERS = {
    ".pdf": b"%PDF",
    ".docx": b"PK\x03\x04",
    ".xlsx": b"PK\x03\x04",
    ".pptx": b"PK\x03\x04",
    ".png": b"\x89PNG",
    ".jpg": b"\xff\xd8\xff",
    ".zip": b"PK\x03\x04",
}
```

- Leading file bytes are validated against the declared extension on upload
- Mismatches return HTTP 400

## Ingestion & Graph Portability Security (v3.6.0)

- **Ingestion lifecycle**: Every ingest passes through the native ingest door
  (`lattice-ingest` → `lattice_core::graph_write`) and tool dispatch so
  `pre_tool`/`post_tool` hooks fire. If `pre_tool` blocks, ingestion honestly
  rejects with `status="blocked"` (enforcing permission gates and sensitive-data
  guards). Python `IngestionPipeline.ingest` established this contract in
  v3.6.0, and v11.6.0 preserved it when moving the door into Rust.
- **Web URL reader** (`/api/browser/read-url`): Allows `http(s)` only and
  rejects loopback, private, link-local, multicast, and reserved addresses
  across all DNS results. Connections pin the validated IP to prevent DNS
  rebinding, re-validate every redirect, and ignore ambient proxy environment
  variables. Enforces a 12-second timeout, a streamed 4 MB cap, and textual
  content types only. Blocked or login-walled pages fail with **422** rather
  than 5xx.
- **Browser tab capture** (`/api/browser/ingest-current-tab`): Payload
  sanitization (strips scripts/styles) plus payload size limit (413). The
  Manifest V3 extension sends **only to `127.0.0.1`** with no cloud endpoint.
- **Portability permissions**: Status reads require a logged-in user; full graph
  export/provenance and **import / backup / restore are admin-only**
  (`require_admin`), since the graph is a machine-wide resource.
- **Workspace context**: MCP graph calls, memory recall, hybrid search,
  garden-note context, and realtime presence bind to the authenticated user and
  their active/allowed workspace. Agent/plugin registry and graph curation
  mutations are admin-only, and MCP environment variable values are never
  returned in API responses. MCP/plugin executions cannot call local
  file/document tools directly and must go through the dedicated approval-token
  path. Document RAG and answer traces are also searched and stored within the
  same workspace scope.
- **Fail-closed KG scope**: If workspace projection lookup fails or a v2 node's
  scope cannot be determined, the node is withheld. Legacy-global data is read
  only on compatibility paths that explicitly pass `include_legacy_global=True`.
- **Restore integrity**: Backup archives are verified against the SHA-256
  digests in `manifest.json` before restore and rejected on any mismatch.

## Agent Tool Sandbox

### `run_command()` Dangerous-Flag Blocking

Uses only a fixed allowlist (`pwd`, `ls`, `find`, `cat`, `head`, `tail`, `wc`,
`rg`) and a sanitized environment. General interpreters (`python`, `node`,
`npm`, `npx`, `sed`), executable paths, absolute paths, `..` traversal,
symlinks escaping the workspace, `rg --pre`, and `find -exec/-delete` are
rejected before execution.

### `edit_file()` Uniqueness Validation

- `old_string` must appear exactly once in the file to succeed
- `replace_all=true` allows replacing all occurrences
- Paths outside the workspace (e.g., `../../../etc/passwd`) are blocked

### `grep()` Binary Directory Exclusion

Automatically skips `node_modules`, `.git`, `venv`, `dist`, and `__pycache__`.

## Audit Logging

- Logs admin session handoff events
- Logs plaintext password migration events: `password_migrated_from_plaintext`
- Records all requests in `server.log`

## Telegram and Permission Notifications

- Telegram processes messages and callback queries only from chats listed in
  `LATTICEAI_TELEGRAM_ALLOWED_CHAT_IDS`, and never registers a chat before
  allowlist validation.
- Local API calls from the bot require a dedicated
  `LATTICEAI_SERVER_SESSION_TOKEN` bearer token; the bot never scans
  `sessions.json`.
- Permission request notifications include only an 8-character hint instead of
  the full approval token. Optional `LATTICEAI_PERMISSION_UI_URL` links to the
  human review page without embedding the token in the URL.
- The permission queue uses atomic writes and POSIX `0600` permissions.

## Telemetry

**None.** All data stays local. No usage telemetry is sent to external servers.

Exception: prompts sent to user-configured cloud APIs (OpenAI, Groq, etc.) are
subject to those providers' policies.

## Public Deployment Checklist

- [ ] `LATTICEAI_MODE=public`
- [ ] Set `LATTICEAI_INVITE_GATE_ENABLED=true` and a private
      `LATTICEAI_INVITE_CODE` only when using invite onboarding (or persist the
      auto-generated per-install secret)
- [ ] HTTPS reverse proxy (nginx/Caddy)
- [ ] `LATTICEAI_ENABLE_GRAPH=false` (if not needed)
- [ ] Persistent `/data` volume mount
- [ ] `LATTICEAI_ALLOW_LOCAL_MODELS=false`
- [ ] Block direct public access to port 4825 via firewall (reverse proxy only)
- [ ] If enabling Telegram, set `LATTICEAI_TELEGRAM_ALLOWED_CHAT_IDS` and
      `LATTICEAI_SERVER_SESSION_TOKEN`

See [public-deploy.md](public-deploy.md) for details.
