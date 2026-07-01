# Security Policy

Current release: **8.3.0 - Orchestrated Brain Readiness**.

## Supported Versions

The public Git tree keeps release history from 7.0.0 through 8.3.0. Security
support follows that same product era.

| Version | Support |
| --- | --- |
| 8.3.x (latest) | Supported |
| 8.2.x | Supported |
| 8.1.x | Supported |
| 8.0.x | Supported |
| 7.9.x | Supported |
| 7.8.x | Supported |
| 7.7.x | Supported |
| 7.6.x | Security fixes when practical |
| 7.5.x | Security fixes when practical |
| 7.4.x | Security fixes when practical |
| 7.3.x | Security fixes when practical |
| 7.2.x | Security fixes when practical |
| 7.1.x | Security fixes when practical |
| 7.0.x | Security fixes when practical |
| Older than 7.0.0 | Not supported in the current Git history |

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

Lattice AI v8.3.0 is a local-first Digital Brain. It keeps user knowledge,
conversation context, Knowledge Graph data, and archives local by default while
making external paths explicit.

### Default Secure Settings

| Setting | Default | Notes |
| --- | --- | --- |
| Bind address | `127.0.0.1` | Local-only unless explicitly changed |
| Auth | `REQUIRE_AUTH=true` | Sensitive endpoints require a session |
| CORS | Localhost only | Network CORS requires explicit opt-in |
| Session TTL | 24 hours sliding | Inactive sessions expire |
| API key storage | OS keyring where available | No intentional plaintext secret storage |
| Brain storage | SQLite | PostgreSQL is optional scale mode |
| Docker Postgres setup | Disabled | Requires explicit consent |
| Production CSP | Strict local app policy | External script/frame/object blocked by default |
| Chat auto file read | Disabled | Arbitrary path reads require explicit approval |

### Authentication And Sessions

- Passwords use scrypt hashing.
- Session cookies are `HttpOnly` and `SameSite=Lax`.
- Session state is stored locally.
- Enterprise SSO paths are explicit configuration paths.

### File And Archive Safety

- Uploaded files are signature-checked where supported.
- Uploaded blobs are stored under the local data directory.
- `.latticebrain` archives are encrypted and integrity-checked.
- Wrong passphrases, tampering, unsupported archive versions, and archive path
  traversal fail closed.

### Agent And Tool Safety

- AgentRuntime preview/readiness does not execute tools.
- ToolRegistry owns dispatch, permissions, diagnostics, and MCP install state.
- Command/file tools enforce sandbox and uniqueness checks where applicable.
- Secret-like values are centrally redacted before logs, audit payloads,
  security exports, frontend previews, and hook packets.

### External Communication

There is no default telemetry. Prompts, files, graph content, and archives do
not leave the machine solely because a token exists.

External communication requires explicit configuration and user/admin action:

- cloud model calls;
- model downloads;
- Telegram bridge;
- Brain Network peer exchange;
- Docker/Postgres scale setup;
- update checks;
- marketplace or remote registry refresh.

## Public Deployment Guidance

If exposing Lattice AI beyond localhost:

1. Set `LATTICEAI_MODE=public`.
2. Use a private `LATTICEAI_INVITE_CODE`.
3. Put the app behind HTTPS.
4. Mount persistent storage.
5. Review CORS, auth, rate limits, and graph exposure settings before use.

## Known Limitations

- Local file security depends on the user's OS account, disk encryption, and
  backup policy.
- Cloud model prompts follow the selected provider's policy after the user
  chooses that provider.
- A trusted local admin can inspect local files and process memory outside
  Lattice AI.
- MLX/model file integrity depends on provider metadata and local download
  controls.
