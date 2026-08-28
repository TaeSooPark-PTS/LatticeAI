# Security Policy

> **Status: canonical** — current security model, kept in sync with the current
> release.

Current release: **12.1.0 — Fast Path**.

## Supported Versions

The public Git tree keeps release history from 11.0.0 through 12.1.0. Security
support follows that same product era: **only the 11.x and 12.x line receives
fixes.**

11.6.0 rebuilt the product server in Rust and reduced the Python package to a
pure-compute AI worker; 11.7.0 closed the holes that door disclosed, 11.8.0
narrowed the worker further — from 28 routes to **19** — 11.9.0 kept that
19-route worker while wiring the hybrid cloud lane and the local MCP server
that sit on the host, and 12.0.0 regrouped the two largest Rust crates without
moving the boundary, adding exactly one worker route (`POST
/worker/vector/query`, **20** in total). A fix for this line is a fix to a
different program than the one 10.x and 9.x shipped, so backporting it would
be a claim this project cannot honour. Those release notes stay in the tree as
history; the support line does not.

| Version | Support |
| --- | --- |
| 12.1.x (latest) | Supported |
| 11.9.x | Supported |
| 11.8.x | Supported |
| 11.7.x | Supported |
| 11.6.x | Supported |
| 11.5.x | Supported |
| 11.4.x | Supported |
| 11.3.x | Supported |
| 11.2.x | Supported |
| 11.1.x | Supported |
| 11.0.x | Supported |
| Older than 11.0.0 | Not supported — see the note above |

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

Lattice AI is a local-first Digital Brain. It keeps user knowledge,
conversation context, Knowledge Graph data, and archives local by default while
making external paths explicit.

Since 11.6.0 the product server is `lattice-host` (Rust) and the Python package
is an AI worker reached only over loopback. **11.8.0 narrowed that worker from
28 routes to 19**: nine routes with no caller anywhere in the tree were deleted
end to end — route, implementation, allowlist entry and gateway table — and
negative tests now assert that the gateway no longer forwards them. A route
that nothing calls is still an attack surface, so the smallest honest worker is
the one that answers only what the product actually asks for. 11.9.0 did not
grow that surface; `/worker/sysinfo` gained additive `capabilities` and
`python_version` fields on the same route. **12.0.0 adds one route and states
why**: `POST /worker/vector/query` returns candidate ids for the native exact
rescore when `LATTICEAI_VECTOR_INDEX=hnsw` is opted into — it reads vectors and
returns ids, it writes nothing, and the default `brute` backend never calls it.
The allowlist is **20**, generated from the worker's own profile and pinned
from both sides.

The 12.0.0 crate regrouping does not move any trust boundary. `lattice-agent`
is six groups and `lattice-platform` is seven domains, but the same kernel
still answers "may this run", the same single writer still owns the graph, and
the same gateway still refuses anything off the allowlist. The value of the
regrouping to this document is that the security-relevant code now has a stated
address: refusals live in `lattice-agent`'s `kernel/`, untrusted model text is
handled only in `parse/`, and every byte a run is about to write passes
`content/sanitize`.

### Default Secure Settings

| Setting | Default | Notes |
| --- | --- | --- |
| Bind address | `127.0.0.1` | Local-only unless explicitly changed |
| Auth | Off on loopback local mode | Forced on for public or non-loopback binding |
| CORS | Localhost only | Network CORS requires explicit opt-in |
| Session TTL | 24 hours sliding | Inactive sessions expire |
| API key storage | OS keyring where available | No intentional plaintext secret storage |
| Installer/process execution | Confirmation-token gated | Redacted command plans and local process audit events |
| `/setup/install` package execution | Manual unless the request names the item | Runs brew/pip/uv only for an item on the **server-derived** allowlist; anything else is refused by name |
| Folder prune (deleted-file cleanup) | Dry-run | `POST /api/ingestion/folder/prune` removes a document subtree only with explicit `confirm`; the disk is never touched |
| Brain storage | SQLite | PostgreSQL is optional scale/migration tooling |
| Docker Postgres setup | Disabled | Requires explicit consent |
| Production CSP | Strict local app policy | External script/frame/object blocked by default |
| Chat auto file read | Disabled | Arbitrary path reads require explicit approval |
| Telegram inbound access | Removed in 11.6.0 | The bridge left with the platform code that became the AI worker; nothing replaces it |
| Legacy-global graph reads | Disabled | Must be explicitly requested for compatibility |
| Invitation authorization | Signed and expiring | Static authorization cookies and built-in invitation codes are rejected |

### Authentication And Sessions

- Passwords use scrypt hashing.
- Session cookies are `HttpOnly` and `SameSite=Lax`; non-loopback/public
  deployments also set `Secure`.
- Session tokens are stored locally only as SHA-256 hashes; deleted or disabled
  accounts invalidate existing sessions on the next request.
- **The worker reads the session file rather than caching it forever (11.8.0).**
  `lattice-auth` is the only writer of `sessions.json`; the worker only reads.
  It used to load that file once at boot, so a login that happened afterwards
  did not exist as far as the worker was concerned — silently under
  `trusted_local_owner`, and as a flat 401 under `LATTICEAI_REQUIRE_AUTH=true`
  for a token sitting in the file. A lookup that misses now re-reads before
  giving up, guarded so a token-guessing burst cannot become a disk-read burst:
  the re-read is skipped when the file's `mtime_ns`/`size` are unchanged, and
  otherwise throttled to one parse per second, both under the lock that guards
  the map. This makes valid sessions visible, never invalid ones — an expired or
  unknown token still fails after the re-read.
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
- **A restore takes effect immediately, in-process (12.0.0).** The store
  carries a generation epoch; a restore bumps it, every pooled connection
  opened under the previous generation is stale at its next checkout and is
  closed, and the following read sees the restored bytes. Through 11.9.0 the
  documented mitigation was "restart after restore", which meant a window in
  which a reader could be served pre-restore content — that window is gone.
- **Deleting knowledge is a separate, confirmed action.** Incremental ingest
  counts vanished files and leaves their nodes alone. `POST
  /api/ingestion/folder/prune` reports the plan without `confirm` and, with
  it, removes the document subtree through `delete_document_tree` — which
  clears edges in both directions rather than leaving them dangling. No prune
  path deletes anything on disk.

### Agent And Tool Safety

- AgentRuntime preview/readiness does not execute tools and non-auto-approved
  plans require explicit human approval.
- Mutating tools are enumerated in a governed inventory
  (`MUTATING_TOOL_INVENTORY`); each is either proposal-governed or explicitly
  exempt, and that coverage is release-checked so a new mutating tool cannot
  silently bypass governance. Edits and deletions of existing files run through
  change proposals that record a base content hash and re-verify it before
  applying atomically, so a conflicting concurrent edit fails closed instead of
  being overwritten.
- The agent-eval verifier fails closed: unverifiable or failing outcomes resolve
  to a review state rather than being reported as success.
- **The `guided` profile removes the JSON, not a gate (12.0.0).** A run that
  cannot hold a tool-call contract is walked through numbered choices and
  one argument per turn, but the harness assembles the action and hands it to
  the same `perform_action` tail every profile uses — same gate chain, same
  loop guard, same pre-write snapshot, same `sanitize_write_content`.
  Verification is a closed PASS/FAIL judged by the same evidence and coverage
  gates, and an unverifiable outcome is still `NEEDS_REVIEW`.
- **The loop refuses to execute its own prompt.** A `write_file` whose content
  is the crate's own worked-example constant verbatim is recorded as
  `COPIED_EXAMPLE` and written nowhere; echoed instruction lines are stripped
  from guided answers. Weak models continue the nearest complete shape, and in
  an agent turn that shape is our prompt — so this is a fail-closed refusal
  against a constant the crate owns, which cannot reject a genuine answer.
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
  link, never the full approval token. **11.9.0 unifies those local approval
  tokens** (`LocalApprovals`): a folder ingest that the trusted owner approved
  is redeemable at `/permissions/approve` rather than carrying a second,
  endpoint-private token.

### What 11.8.0 Deleted, And Why It Is Not A Weakening

11.8.0 removed several Python helpers whose names read as security controls:
`hash_password` / `verify_password`, `check_ip_rate_limit`,
`configure_trusted_proxies`, `client_ip`, and `bytes_match_extension`. None of
them was enforcing anything. Password verification, rate limiting, trusted-proxy
resolution and client-IP derivation all became `lattice-auth`'s and the
gateway's in 11.6.0, when the front door moved to Rust; these copies had no
caller left in the worker. Deleting a second, unreachable implementation removes
the chance that a future reader hardens the wrong one.

The guards that *are* load-bearing were kept and are unchanged: the CSRF origin
guard on the proxied worker writes, auth before any decode or compute, the
signature check on uploads, the mode-invariant circuit breakers in the Rust
kernel, `sanitize_write_content` on every native write path, and the committed
worker allowlist — 19 entries then, 20 since 12.0.0 added the vector-query
seam, with tests asserting the nine deleted routes are answered `404` rather
than forwarded.

### External Communication

There is no default telemetry. Prompts, files, graph content, and archives do
not leave the machine solely because a token exists.

External communication requires explicit configuration and user/admin action:

- cloud model calls;
- model downloads;
- Brain Network peer exchange;
- Docker/Postgres scale setup;
- update checks;
- marketplace or remote registry refresh.

**Cloud turns are audited and proposed, not silently written (11.9.0).** Every
cloud turn writes a `cloud_egress` record *before* the provider is called,
naming provider, model, and the escalation reason — shape, never content.
Knowledge extracted from a cloud answer is staged as a Review Center
`kg_cloud_expansion` proposal; the graph changes only when a person approves
it (or when `auto_commit` is explicitly enabled, default off).

**Two credential modes, one of them live-verified without billing.**
`api_key` is an OpenAI-compatible adapter. This release contract-tested it
against a mock server and never live-called it — there was no billing budget
for that path. `cli_oauth` uses a locally OAuth-authenticated CLI
(Antigravity `agy` → gemini-3.7-flash, `grok` → grok-4.6), spawned in a
neutral temp cwd with a 120s timeout; that path was live-verified with zero
API billing. Resolution is `cloud_provider.json` → env key → `agy` → `grok`
→ none. A per-request `network_mode: "local_only"` always wins over the
escalation policy (`auto` default / `manual` / `always`).

The MCP surface at `POST /mcp` is a real streamable-HTTP JSON-RPC server on
the host, and **since 12.0.0 it is inside the OpenAPI product contract** as a
single JSON-RPC envelope operation rather than beside it. Governance refusals
are JSON-RPC errors. It is not a second, ungoverned door, and the agent loop
does not get a quieter one either: when a run's own catalog dispatches an
`mcp.<tool>` name it did not govern, that call goes through the same
governance check `POST /mcp` runs. A name the run *does* govern takes the
kernel's stricter native path instead.

Web-page capture additionally resolves and pins public IPs, rejects private and
reserved address classes, rechecks every redirect, ignores proxy environment
variables, and streams at most 4 MiB of textual content.

### Security-Sensitive Environment Variables

| Variable | Requirement | Purpose |
| --- | --- | --- |
| `LATTICEAI_PERMISSION_UI_URL` | Optional | Base URL for human permission-review links; approval tokens are never appended or sent |
| `LATTICEAI_REQUIRE_AUTH` | Optional, default off on loopback | Forces authentication even in local mode; forced on for public or non-loopback binding |
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

## Known Limitations

- Local file security depends on the user's OS account, disk encryption, and
  backup policy.
- Cloud model prompts follow the selected provider's policy after the user
  chooses that provider.
- A trusted local admin can inspect local files and process memory outside
  Lattice AI.
- MLX/model file integrity depends on provider metadata and local download
  controls.
