# Lattice AI v3.6.0 — Completion Record (Knowledge Graph First)

**Status: ✅ RELEASED — 2026-06-10.** All planned scopes implemented, tested,
committed, pushed, CI green, tagged, and published. No external publish/deploy.

- **Tag:** `v3.6.0` → commit `3c85675`
- **GitHub Release:** https://github.com/TaeSooPark-PTS/LatticeAI/releases/tag/v3.6.0
  (published, not draft; assets: `ltcai-3.6.0-py3-none-any.whl`,
  `ltcai-3.6.0.tar.gz`, `ltcai-3.6.0.tgz`, `ltcai-3.6.0.vsix`)
- **CI:** main CI ✓, Visual Smoke ✓, release.yml (tag) ✓
- **Tests:** unit 455 passing · lint 64/64 · check:python 153 (3.11/3.12/3.14) ·
  release artifacts built + validated

## Commits (v3.5.0 → v3.6.0)

| Commit | Scope |
|---|---|
| `baa2bf6` | chore(audit) — v3.5.0 carry-over (0 blocking) |
| `5a6a7d4` | feat(kg) — entities/relationships schema |
| `135e81a` | feat(kg) — unified ingestion pipeline + provenance |
| `b548885` | feat(browser) — browser/web ingestion + MV3 extension |
| `39a7a0c` | feat(kg) — export/import/backup/restore |
| `21cfb97` | fix(runtime) — hook coverage for ingestion paths |
| `7009e39` | fix(ui) — Knowledge Graph as primary surface |
| `fa89a84` | docs(philosophy) — Digital Brain Platform rewrite |
| `aa011a5` | release: v3.6.0 (version bump) |
| `3c85675` | fix(ci) — 3.11-compatible f-string (PEP 701 quote reuse) |

## Carry-over audit result

Zero blocking items. Settled postures preserved: Vercel landing-only, OIDC
RSA-only, legacy `/account` `/admin` out of scope. The one honest v3.5.0 gap (KG
ingestion not firing tool hooks) is **closed**. Full detail:
`docs/CARRYOVER_AUDIT_v3.6.0.md`.

## Key facts for future work

- New seams: `latticeai/services/ingestion.py` (single write-side entrypoint),
  `latticeai/services/kg_portability.py`, `latticeai/api/browser.py`,
  `latticeai/api/portability.py`, provenance in `knowledge_graph.py`.
- **Gotcha:** PEP 701 f-string quote reuse (`f'{x or ''}'`) compiles on 3.12+ but
  is a SyntaxError on 3.11 — always run `python3.11 scripts/check_python.py`
  before pushing (CI tests on 3.11 + 3.12).
- Version canonical: `WORKSPACE_OS_VERSION`; mirrors enforced by
  `test_version_consistency.py`.
- Local tests need `.venv/bin/python` (system `python3` lacks fastapi).
