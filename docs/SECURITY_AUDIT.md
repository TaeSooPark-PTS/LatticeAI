# Security Audit — Static Scan Snapshot

> Status: audit snapshot 2026-07-21

This is a point-in-time static-analysis snapshot for release **v9.8.0**. It
records exactly which scanners ran, the raw findings, and an honest per-finding
real-risk assessment. It is not a guarantee of absence of vulnerabilities; it is
evidence of what the listed tools saw on the listed date.

Related: [CI & Release Gates](CI_AND_RELEASE_GATES.md) documents how these scans
are wired into continuous integration (`.github/workflows/dependency-audit.yml`).

## Tooling & commands

Scanners were installed into the project virtualenv only (`.venv`), never added
to runtime dependencies:

```
.venv/bin/python -m pip install pip-audit bandit
```

| Tool      | Version | Command |
|-----------|---------|---------|
| pip-audit | 2.10.1  | `.venv/bin/python -m pip_audit -r requirements.txt --strict` |
| pip-audit | 2.10.1  | `.venv/bin/python -m pip_audit` (installed environment) |
| bandit    | 1.9.4   | `.venv/bin/python -m bandit -r latticeai lattice_brain -ll` |
| npm audit | (npm 10)| `npm audit --audit-level=high` |

## Dependency vulnerabilities (pip-audit / npm audit)

**Result: no known vulnerabilities.**

- `pip-audit -r requirements.txt --strict` → `No known vulnerabilities found`.
- `pip-audit` over the installed environment → `No known vulnerabilities found`.
- `npm audit --audit-level=high` → `found 0 vulnerabilities`.

No dependency version changes were required. Because there were no actual
vulnerable pins to fix, no dependency files were modified (per the honest-fix
rule: only fix a real, safe-to-fix issue).

## Static code analysis (bandit)

Scanned **54,795 lines** across `latticeai/` and `lattice_brain/`. With the
`-ll` display filter (MEDIUM severity and above), bandit reported **80 findings**:
1 HIGH and 79 MEDIUM. Breakdown by rule:

| Count | Severity | Rule | What it flags |
|------:|----------|------|---------------|
| 1  | HIGH   | B324 hashlib | Weak MD5 hash "for security" |
| 58 | MEDIUM | B608 hardcoded_sql_expressions | SQL built via string formatting |
| 7  | MEDIUM | B310 blacklist (urlopen) | `urllib.request.urlopen` with unrestricted scheme |
| 5  | MEDIUM | B604 shell=True | A function called with `shell=True` |
| 3  | MEDIUM | B108 hardcoded_tmp_directory | Hardcoded `/tmp` path |
| 3  | MEDIUM | B104 hardcoded_bind_all_interfaces | Bind to `0.0.0.0` |
| 2  | MEDIUM | B306 blacklist (mktemp) | `tempfile.mktemp()` |
| 1  | MEDIUM | B615 huggingface_unsafe_download | `hf_hub_download` without a pinned revision |

### Per-finding real-risk assessment

| Rule | Location(s) | Verdict | Rationale |
|------|-------------|---------|-----------|
| **B324** MD5 | `lattice_brain/quality.py:44` | **False positive** | MD5 derives a deterministic *pseudo-label* for embedding clusters (`emb_cluster_<hash>`); not used for authentication, integrity, or secrets. Safe cosmetic fix would be `hashlib.md5(..., usedforsecurity=False)`; source is out of this task's edit scope, so documented only. |
| **B604** shell=True | `latticeai/core/agent.py:539`, `latticeai/core/tool_registry.py:129/136/168/175` | **False positive** | These are `ToolPolicy(..., shell=True, ...)` dataclass *declarations* describing whether a tool is a shell tool — not `subprocess(..., shell=True)` calls. Bandit matches the keyword, not an actual shell invocation. |
| **B108** /tmp | `lattice_brain/graph/_kg_constants.py:186/215`, `latticeai/core/agent_eval.py:240` | **False positive / benign** | The `_kg_constants` hits are `/tmp` entries in an **exclusion allowlist** of directories to skip during ingestion (a defensive list). The `agent_eval` hit is a test/eval default `agent_root`. No untrusted file is written to a predictable temp path here. |
| **B104** 0.0.0.0 | `latticeai/cli/entrypoint.py:53/237/238` | **By design, low risk** | The bind switches to `0.0.0.0` only when the operator explicitly passes `--tunnel`; default remains `127.0.0.1`. Intentional and gated. |
| **B310** urlopen | `latticeai/services/model_engines.py:120/234`, `latticeai/services/model_runtime.py:242` | **Low real risk** | Calls target operator-configured local inference engines (LM Studio / Ollama). URLs are not attacker-controlled in normal use. Recommendation: assert `http(s)` scheme before `urlopen` to close the `file:`/custom-scheme door. |
| **B608** SQL f-strings | `lattice_brain/storage/postgres.py`, `.../migration.py`, `lattice_brain/graph/*.py`, `lattice_brain/conversations.py` | **Low real risk — review** | Interpolated fragments are overwhelmingly **schema/table identifiers** quoted through `lattice_brain.storage.postgres._quote_ident`, with row *values* passed as bound parameters. Not user-value injection. Standing recommendation: keep every interpolated identifier sourced from internal constants / allowlists, never from request input. |
| **B306** mktemp | `latticeai/integrations/telegram_bot.py:450/568` | **Real, low severity** | `tempfile.mktemp()` has a documented create-time race; prefer `tempfile.mkstemp()` / `NamedTemporaryFile`. Impact is limited (local temp files for outbound Telegram media), but this is a genuine hardening item for a future source-owning change. |
| **B615** HF download | `latticeai/services/model_runtime.py:601` | **Real, supply-chain relevant** | `hf_hub_download(...)` is called without `revision=`, so a mutated upstream model repo could serve a different artifact than expected. Recommendation: pin a commit/`revision` (or verify a hash) when fetching model weights. Ties directly into the P2 supply-chain theme. |

## Actions taken vs deferred

- **Fixed:** nothing — no dependency vulnerability existed to fix safely, and
  the code-level findings live in source files outside this task's edit scope.
- **Deferred (documented) hardening items**, for a future change that owns the
  relevant source:
  1. Pin a `revision` in `model_runtime.py` `hf_hub_download` (B615).
  2. Replace `tempfile.mktemp()` in `integrations/telegram_bot.py` (B306).
  3. Validate URL scheme before `urlopen` in the model engine paths (B310).
  4. Optional cosmetic: `usedforsecurity=False` on the clustering MD5 (B324).

## Limitations (honest)

- Static analysis only. No dynamic/DAST, no fuzzing, no secret-scanning here.
- bandit reasons syntactically; the false positives above were confirmed by
  reading each site, not by suppression.
- pip-audit resolves against the OSV/PyPI advisory database as of the snapshot
  date; new advisories appear continuously — see the scheduled
  `dependency-audit` workflow for the ongoing signal.
- SBOMs (CycloneDX) for both ecosystems can be regenerated with
  `.venv/bin/python scripts/generate_sbom.py`.
