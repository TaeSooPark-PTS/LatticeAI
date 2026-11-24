[![English](https://img.shields.io/badge/English-Active-2563eb?style=flat-square)](./OPERATIONS.md) [![한국어](https://img.shields.io/badge/한국어-Korean-475569?style=flat-square)](./ko/OPERATIONS.md)

# Lattice AI — Operations Guide (v13.7.1)

> **Status: canonical** — kept in sync with the current release. Storage layout
> below reflects the SQLite live Brain store and workspace scoping, not the
> earlier Markdown-per-node graph.

## 1. Data File Locations

| File / Directory | Purpose |
|------------------|---------|
| `~/.ltcai/users.json` | User accounts (scrypt password hashes) |
| `~/.ltcai/knowledge_graph.sqlite` | Live Brain store — Knowledge Graph nodes/edges/chunks, conversation history, and provenance (`workspace_id`-scoped) |
| `~/.ltcai/knowledge_graph_blobs/` | Uploaded originals / blob storage |
| `~/.ltcai/audit.json` | Audit log (agent executions, user changes, etc.) |
| `~/.ltcai/chat_history.json` | Legacy conversation history — imported once idempotently into SQLite on first boot, then unused |
| `~/ltcai-agent/` | Default base path for agent workspaces — isolated per user/tenant/workspace on the server via `IsolatedRootSpec` when authenticated |
| `~/.ltcai/agent_runs/` | Pending-approval agent runs (survive restarts; approval tokens stored hashed only) |
| `~/.ltcai/project_sessions/` | Project sessions (v9.9.6) — files created by a project, remaining tasks, and last verification outcome |

## 2. Backup and Restore

### 2.1 Full Backup
```bash
tar -czf ltcai-backup-$(date +%Y%m%d).tar.gz ~/.ltcai ~/ltcai-agent
```

### 2.2 Restore
```bash
tar -xzf ltcai-backup-YYYYMMDD.tar.gz -C ~
```

In-app backups (`.latticebrain` / `VACUUM INTO` snapshots) provide an encrypted,
integrity-verified portability path (**RFC 7914 scrypt or X25519 + HKDF-SHA256
+ SHA256-CTR/HMAC-SHA256**, supporting `export`, `inspect`, `verify`, `restore`
dry-run/`confirm`, and `import`), and **since v12.0.0 take effect immediately —
no restart is required after restore.** The store tracks a generation epoch: a
restore increments the epoch, pooled connections opened under the previous
generation are detected as stale on their next checkout and closed, and the
next read immediately sees the restored bytes.

Transient WAL/SHM sidecar files created alongside the live SQLite DB
(`*.db-wal`, `*.db-shm`) and `/scratchpad/` verification outputs are excluded
via `.gitignore` so they are never committed to the repository.

Deleted-file pruning and single-node deletion share the same unified path:
`POST /api/ingestion/folder/prune` returns a dry-run when called without
`confirm`, and removes the subtree through `delete_document_tree` once
confirmed. Single-node `GraphWriter::delete_node` likewise uses
`delete_node_subgraph` to clean up bidirectional edges (including `PART_OF`),
chunks, vectors, orphan `Source` nodes, BM25 statistics, and provenance.
Neither path ever deletes original files on disk.

### 2.3 Reset Conversation History Only
```bash
rm ~/.ltcai/history.json
```

### 2.4 Audit Log Retention (90-Day Example)
```bash
# Add to crontab -e
0 2 * * * find ~/.ltcai -name "audit*.json" -mtime +90 -delete
```

## 3. Log Management

Server logs are emitted to stdout/stderr. Under systemd, journald captures them
automatically.

### 3.1 Viewing Logs
```bash
# Under systemd
journalctl -u ltcai -f

# When running directly, redirect to a file
LTCAI > /var/log/ltcai.log 2>&1
```

### 3.2 Log Rotation
Example `logrotate` config (`/etc/logrotate.d/ltcai`):
```
/var/log/ltcai.log {
    daily
    rotate 30
    compress
    missingok
    notifempty
}
```

## 4. Audit Log Event Types

| `event_type` | Trigger |
|--------------|---------|
| `login` | Login success or failure |
| `user_update` | Administrator updated a user |
| `user_delete` | Administrator deleted a user |
| `history_delete` | Conversation history deleted |
| `agent_exec` | Agent executed a medium/high-risk tool |
| `agent_blocked` | Agent attempted to write to a system path and was blocked |
| `model_load` | Model loaded |
| `model_switch` | Model switched |

### 4.1 Inspecting Audit Logs
```bash
python3 -c "
import json, os
events = json.load(open(os.path.expanduser('~/.ltcai/audit.json')))
for e in events[-20:]:
    print(e['timestamp'], e['event_type'], e.get('user_email', ''))
"
```

## 5. User Management

### 5.1 Creating the First User (Admin)
On first launch, register via `/register` or the web UI. The first registered
user automatically receives the `admin` role.

### 5.2 Listing Users (Admin Only)
```
GET /admin/users
Cookie: session_id=<admin-session>
```

### 5.3 Password Policy
- Minimum 8 characters
- Stored as an scrypt hash
- Plaintext is never persisted

## 6. Upgrade & Migration

### 6.1 Upgrading the npm Package
```bash
npm install -g ltcai@latest
```

### 6.2 Upgrading the PyPI Package
```bash
pip install --upgrade ltcai
```

### 6.3 Data Migration Notes
- `users.json` schema updates: the server automatically backfills missing
  fields with safe defaults.
- Conversation history: stored in the live SQLite store. Legacy
  `chat_history.json` is imported idempotently once on first startup and is not
  used afterward.
- `knowledge_graph.sqlite` Knowledge Graph: SQLite schema migrations run at
  startup and are guarded by read-compatibility, rollback, and equivalence
  tests.

## 7. Public Server Checklist

Verify every item before exposing an instance to the public internet:

- [ ] Set `LATTICEAI_SECRET_KEY` (random string of 32+ characters)
- [ ] Verify `LATTICEAI_MODE=public` and `Secure` cookie behavior over HTTPS
- [ ] If using the invite gate, set a random invite code or persist the
      generated per-install secret on durable storage
- [ ] Set `LATTICEAI_ENABLE_GRAPH=false` if Graph is not needed
- [ ] Configure an HTTPS-terminating reverse proxy (nginx / Caddy)
- [ ] Configure the `CSRF_TRUSTED_ORIGINS` allowlist
- [ ] Review `ALLOWED_COMMANDS` — remove unnecessary interpreters such as
      `python3` or `node`
- [ ] Restrict port 4825 at the firewall so only the reverse proxy can reach it

> **Note**: The Telegram bridge and external SSO OIDC login/callback flows were
> removed during the Rust host transition, so `LATTICEAI_TELEGRAM_*` variables
> are no longer read. Configuration surfaces remain, and password login is
> native (`lattice-auth`).

## 8. User Data Isolation & Embedding / Model Operations

- Conversation history is filtered by `user_email`.
- **Per-user `AGENT_ROOT` isolation**: The agent working directory is no longer
  a single shared directory. The Rust host and agent runtime
  (`IsolatedRootSpec`, `resolve_isolated_agent_root`,
  `LoopRoutesState::isolated`) derive an isolated `AGENT_ROOT` server-side per
  `(user_id, tenant_id, workspace_id)`, and shared workspaces fail closed with
  `workspace_access_denied` unless membership authorization succeeds.
- The Knowledge Graph is scoped by `workspace_id`. Nodes with unknown scope are
  private by default (fail-closed), and legacy-global reads are allowed only
  with an explicit compatibility opt-in. Knowledge nodes track
  `KnowledgeLifecycleState` (`active`, `superseded`, `deprecated`, `disputed`,
  `pending_verification`), and default search excludes `superseded` and
  `deprecated` nodes.
- **Embedding contract & ingest status**: Python
  (`latticeai.core.embedding_providers`) and Rust (`lattice_core::embeddings`)
  share the exact same `EmbeddingIndexContract` (`provider`, `model_id`,
  `model_revision`, `dimension`, `normalization`, `passage_mode`, `query_mode`,
  `index_version`, `grade`, `semantic`). When contracts diverge or an index
  rebuild is needed, status is reported as `IngestStatus::ReindexRequired`
  (`complete` / `partial` / `pending` / `failed` / `reindex_required`).
- **Hugging Face model catalog & cache verification**:
  - `.venv/bin/python scripts/verify_hf_model_registry.py` statically verifies
    Hub metadata and file sizes without downloading weights.
  - In production or offline environments, verify your pre-downloaded local
    cache and recorded `EmbeddingIndexContract.model_revision` until commit
    `revision` pinning and hash verification are enforced across all
    `hf_hub_download` / `from_pretrained` paths.
  - The PostgreSQL / pgvector migration path is `Planned`
    (pre-check / dry-run); SQLite remains the official live store.

## 9. Starting and Stopping the Server

```bash
# Start
LTCAI

# Start with environment variables
LATTICEAI_SECRET_KEY=xxx LATTICEAI_ENABLE_GRAPH=true LTCAI

# Bind an external interface such as a container (requires LATTICEAI_REQUIRE_AUTH=true; refuses to start without it)
LATTICEAI_REQUIRE_AUTH=true lattice-host --bind 0.0.0.0 --port 4825

# Stop
Ctrl+C  or  kill <pid>
```

`lattice-host` binds only to loopback (`127.0.0.1:4825`) by default, and
non-loopback addresses (such as `0.0.0.0`) are permitted only when
`LATTICEAI_REQUIRE_AUTH=true` is set. On shutdown, in-flight HTTP requests and
the worker process are drained cleanly before exit.

## 10. Release Evidence Capture Order (After Layout / UI Changes)

`assertNotOverwritingTaggedRelease()` in `scripts/capture_release_evidence.mjs`
exits immediately with code `1` if a git tag (`vX.Y.Z`) matching `package.json`
already exists. Always follow this order when recapturing screenshots after a
layout or UI change:

1. **Bump the version first** — `node scripts/run_python.mjs scripts/bump_version.py`
   (or bump `package.json` / `pyproject.toml` / extension versions together)
   **before** capturing.
2. `npm run build:assets` — capture renders the built `static/app` bundle.
3. `npm run release:evidence` — writes strictly into `output/release/v<new-version>/`.
   Runs capture, old-version pruning, and the **pixel delta check** in one pass.
4. Binding check — `asset-manifest.sha256` and `mock-server.sha256` in
   `SCREENSHOT_INDEX.md` must match `static/app/asset-manifest.json` and the
   full mock API surface (`tests/visual/mock_server.cjs` +
   `tests/visual/mock_server/*.cjs`) for `npm run lint` to pass. Starting in
   v11.3.0 the fingerprint covers every route module rather than only the
   entry file, so pre-v11.3.0 evidence reports a mismatch until recaptured
   (computed in one place: `scripts/lib/mock_server_fingerprint.mjs`).

To catch cases where a layout change was claimed while pixels stayed identical,
since v10.7.0 `scripts/check_screenshot_pixel_delta.py` compares all 12
captures against the previous release (`npm run check:screenshot-delta` to run
standalone). Static views require at least 1.5% pixel difference and animated
views require at least 8%. Copy-only edits fail this gate by design — if it
fails, inspect the layout rather than re-running capture. If the previous
version directory was pruned, check out the baseline tag to restore it before
running the check.

Overwriting an already-tagged `output/release/vX.Y.Z/` directory with WIP UI
permanently falsifies that release's evidence. When you only need a preview,
write outside the published path with
`LTCAI_RELEASE_EVIDENCE_DIR=/tmp/lattice-preview npm run release:evidence`. If
tagged evidence was polluted, restore the directory from the tagged git tree
and recapture into a new version directory.
