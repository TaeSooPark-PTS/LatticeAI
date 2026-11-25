[![English](https://img.shields.io/badge/English-Active-2563eb?style=flat-square)](./RELEASE_NOTES_v14.1.1.md) [![한국어](https://img.shields.io/badge/한국어-Korean-475569?style=flat-square)](../ko/releases/RELEASE_NOTES_v14.1.1.md)

# Lattice AI v14.1.1 — Living Synthesis (2026-11-25)

## Highlights

### 1. Repository-Wide Python Formatting (`ruff format`) & Blame Hygiene
- **Whole-Tree Formatting (`ruff format .`):** Formatted all 219 Python files in a dedicated format-only commit (`55ba57c381a433066fd94434c3a6250c4a1beb59`) so the entire Python codebase follows a single canonical style.
- **Clean `git blame` History (`.git-blame-ignore-revs`):** Added `.git-blame-ignore-revs` at the repository root recording the format-only commit so `git blame` continues to attribute lines to their semantic authors.
- **CI Format Enforcement (`package.json`, `.github/workflows/ci.yml`):** Updated `npm run lint:python` (invoked by `ci.yml`) to run `ruff format --check .` alongside `ruff check .` and `npm run typecheck:python`, preventing formatting drift on future PRs.

### 2. Zero `C901` Complexity Exemptions Across the Entire Repository
- **Router Decomposition (`latticeai/api/models.py`, `latticeai/api/worker_compute.py`):** Decomposed `create_models_router` and `create_worker_compute_router` into focused module-level sub-registrars and helper functions so every Python function stays at or below McCabe `max-complexity = 20` with zero API or OpenAPI drift.
- **Global `C901` Enforcement (`pyproject.toml`):** Removed `"C901"` from `[tool.ruff.lint.per-file-ignores]` for both `"latticeai/api/*.py"` and `"latticeai/app_factory.py"` — zero `C901` exemptions remain anywhere in the repository.
- **File-Length Hygiene (`tests/unit/test_worker_compute_extract.py`):** Split `/worker/extract` unit tests into `tests/unit/test_worker_compute_extract.py` so every tracked file stays under the 1,000-line ceiling (`check:max-file-lines`).

### 3. Server i18n Ratchet Closed to `0` Unclaimed Modules
- **Explicit Module Classification (`scripts/check_server_i18n.mjs`):** Audited the final two unclassified modules in `latticeai/api/` — `worker_compute_render.py` (pure Office/PDF binary builders and Pydantic schemas with no HTTP routes) and `worker_resilience.py` (internal loopback `/worker/*` timeout and idempotency middleware returning machine-readable codes to `lattice-host`) — and registered both explicitly in `NON_ROUTER_HELPERS`.
- **Fail-Closed Ratchet:** Updated `scripts/check_server_i18n.mjs` to exit `1` if any future `.py` module added under `latticeai/api/` is neither listed in `LOCALIZED` nor explicitly registered in `NON_ROUTER_HELPERS` (`0 unclaimed`).

### 4. Container Ownership & Permissions Hygiene
- **Explicit Runtime Binary Ownership (`Dockerfile`):** Updated line 43 (`COPY --from=host-builder --chown=lattice:lattice --chmod=0755 /build/rust/target/release/lattice-host /usr/local/bin/lattice-host`) so binary ownership and executable permissions are explicit and consistent across all runtime stage copies.
