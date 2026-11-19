[![English](https://img.shields.io/badge/English-Active-2563eb?style=flat-square)](./SECURITY_AUDIT.md) [![한국어](https://img.shields.io/badge/한국어-Korean-475569?style=flat-square)](./ko/SECURITY_AUDIT.md)

# Security Audit — Static Scan & Supply-Chain Snapshot

> Status: audit snapshot 2026-11-16

This is a point-in-time static-analysis and dependency-audit snapshot over the
current Rust-host + Python AI worker tree (`latticeai/`). It records exactly
which scanners ran, the raw findings observed, remediation completed since the
previous `v9.8.0` snapshot, and honest remaining items. It is not a guarantee of
absence of vulnerabilities; it is evidence of what the listed tools saw on the
listed date.

Related: [CI & Release Gates](CI_AND_RELEASE_GATES.md) documents how these scans
are wired into continuous integration (`.github/workflows/dependency-audit.yml`
and `.github/workflows/ci.yml`).

## Tooling & commands

Scanners ran from the project virtualenv (`.venv`) and root npm workspace:

| Tool | Version | Command |
|------|---------|---------|
| `pip-audit` | 2.10.1 | `.venv/bin/pip-audit -r requirements.txt --strict` |
| `bandit` | 1.9.4 | `.venv/bin/bandit -q -r latticeai -ll -f json` |
| `npm audit` | npm | `npm audit --audit-level=high` |

## Dependency vulnerabilities (`pip-audit` / `npm audit`)

- `pip-audit -r requirements.txt --strict` → `No known vulnerabilities found` (exit 0).
- `npm audit --audit-level=high` → `found 0 vulnerabilities` (exit 0).

No dependency pins required security overrides.

## Static code analysis (`bandit`)

Scanned **16,745 LOC** across `latticeai/` (`lattice_brain/` moved to Rust
crates in `v11.6.0`). With the `-ll` display filter (MEDIUM severity and above),
bandit reported **11 findings**: **0 HIGH** and **11 MEDIUM** (down from 80
findings across 54,795 LOC in the `v9.8.0` monolith snapshot).

| Count | Severity | Rule | What it flags |
|------:|----------|------|---------------|
| 4 | MEDIUM | `B615` `huggingface_unsafe_download` | `from_pretrained()` called without literal `revision=` kwarg |
| 4 | MEDIUM | `B604` `any_other_function_with_shell_equals_true` | `ToolPolicy(..., shell=True)` dataclass declarations |
| 2 | MEDIUM | `B310` `blacklist` (`urllib.request.urlopen`) | `urlopen` without explicit URL scheme check |
| 1 | MEDIUM | `B608` `hardcoded_sql_expressions` | SQL `IN (%s)` placeholder formatting |

### Per-finding real-risk assessment

| Rule | Location(s) | Verdict | Rationale |
|------|-------------|---------|-----------|
| **`B615`** HF download | `latticeai/core/embedding_providers/text.py:471,472,474,475` (plus unflagged wrapper `_call_hf_hub_download` in `latticeai/services/model_runtime/download.py:294,303`) | **Real, supply-chain relevant** | `AutoTokenizer.from_pretrained` / `AutoModel.from_pretrained` (and `_call_hf_hub_download`) do not currently pass a pinned commit `revision=`. Local snapshot loading uses `local_files_only=True` when cached, and `EmbeddingIndexContract` now tracks `model_revision`, but explicit Hub commit/hash pinning on download remains a tracked hardening item. |
| **`B604`** `shell=True` | `latticeai/core/tool_registry.py:129,136,161,168` | **False positive** | `ToolPolicy(..., shell=True)` declarative metadata specifying whether a tool policy represents a shell capability — not a `subprocess(..., shell=True)` invocation. |
| **`B310`** `urlopen` | `latticeai/services/model_engines.py:123,240` | **Low real risk — harden** | Targets operator-configured local inference engines (LM Studio / Ollama). Asserting an `http`/`https` URL scheme before calling `urlopen` closes any `file:` / custom-scheme edge case. |
| **`B608`** SQL `IN (%s)` | `latticeai/core/vector_index/sidecar.py:109` | **Low real risk (safe parameterized query)** | Formats only `"?"` placeholders matching `len(chunk)` (`IN (?,?,...)`) while passing `(model_id, int(dim), *chunk)` as bound SQLite parameters — no user data is interpolated. |

## Security & supply-chain remediation completed (since v9.8.0 snapshot)

1. **Legacy Python surface retired**: `lattice_brain/` and
   `latticeai/integrations/telegram_bot.py` (`B306` `tempfile.mktemp()`) were
   removed with the Rust host/worker split; static findings fell from 80 (1 HIGH)
   to 11 (0 HIGH).
2. **`.latticebrain` Archive Cryptography & Path Safety**: Completed in Rust
   (`rust/lattice-platform/src/knowledge/portability/{archive, crypto}.rs`):
   RFC 7914 `scrypt` key derivation, X25519 + HKDF-SHA256 recipient sealing,
   encrypt-then-MAC (`SHA256-CTR` + `HMAC-SHA256`) with constant-time MAC check
   (`ct_eq`), ZIP path-traversal rejection (`validate_zip_entries`), dry-run &
   verified restore/import gates.
3. **Per-User `AGENT_ROOT` Isolation**: Server-side workspace path derivation
   (`IsolatedRootSpec`, `resolve_isolated_agent_root`,
   `LoopRoutesState::isolated`) isolates agent execution per `(user_id,
   tenant_id, workspace_id)` and fail-closes unauthorized shared workspaces.
4. **Repository Hygiene & Token Cleanup**: Removed all 77 tracked verification
   files under `scratchpad/` (including `n7_token.txt` and `verify*` dumps) from
   git tracking and disk; added `/scratchpad/`, `*.db-wal`, and `*.db-shm` to
   `.gitignore`.
5. **CI/CD Supply-Chain & Release Asset Gates**: Pinned 100% of GitHub Actions
   `uses:` entries to immutable commit SHAs, promoted retrieval sidecar/contract
   tests + `cargo test -p lattice-retrieval --tests` to PR-blocking checks in
   `.github/workflows/ci.yml`, and added Apple Silicon DMG build/sign/verify +
   SHA-256 release asset uploads in `.github/workflows/release.yml` and
   `publish.yml`.

## Remaining hardening backlog

1. Wire explicit commit `revision=` (and SHA-256 checksum verification where
   published) into `latticeai/services/model_runtime/download.py`
   (`_call_hf_hub_download`) and `latticeai/core/embedding_providers/text.py`
   (`from_pretrained`), eliminating the 4 `B615` findings.
2. Enforce `http`/`https` scheme validation before `urllib.request.urlopen` in
   `latticeai/services/model_engines.py:123,240` (`B310`).
3. Rotate any legacy external tokens if any file previously stored in
   `scratchpad/` was ever valid outside local test verification.

## Limitations (honest)

- Static analysis and advisory database checks only here (`bandit`, `pip-audit`,
  `npm audit`); no DAST or Rust `cargo-audit` run is recorded in this snapshot.
- `pip-audit` and `npm audit` reflect upstream advisory databases as of
  2026-11-16; ongoing checks run weekly via `.github/workflows/dependency-audit.yml`.
- CycloneDX SBOMs (`sbom-python.json`, `sbom-npm.json`) can be regenerated
  locally via `.venv/bin/python scripts/generate_sbom.py`.
